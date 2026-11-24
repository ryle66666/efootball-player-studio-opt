"""Local player catalogue and conversion of existing game artwork only."""
from pathlib import Path
import contextlib
from collections import OrderedDict
import hashlib
import json
import os
import re
import sqlite3
import struct
import subprocess
import threading
import uuid
import zipfile
from native_movie import convert_movie
from native_models import FACE_DECODER_REVISION, resolve_face,person_keys
from native_texture import TEXTURE_DECODER_REVISION
from native_presets import NativePresets
from native_symbols import merged_symbols,resolve_club,converted_symbol,informative_symbol
from native_materials import MATERIAL_DECODER_REVISION,MATERIAL_VALIDATION,COLOR_PARAMETERS
from library_video_covers import LibraryVideoCovers
from library_bound_cards import front_name, load as load_bound_cards, rarity as card_rarity

BAKED_FRONT = re.compile(r'030[01]_f01_[0-9]{7,}_')


def baked_front(layers):
    return any(layer['slot']=='f01' and BAKED_FRONT.fullmatch(layer.get('name','')) for layer in layers)

def glb_textures(path, materials, output):
    data=path.read_bytes();chunks=[];offset=12
    while offset<len(data):
        length,kind=struct.unpack_from('<II',data,offset);chunks.append((kind,data[offset+8:offset+8+length]));offset+=8+length
    doc=json.loads(next(b for k,b in chunks if k==0x4E4F534A));binary=bytearray(next((b for k,b in chunks if k==0x004E4942),b''));bound=0
    for material in doc.get('materials',[]):
        item=materials.get(material.get('name'))
        if not item:continue
        png,properties=item
        while len(binary)%4:binary.append(0)
        view=len(doc.setdefault('bufferViews',[]));doc['bufferViews'].append({'buffer':0,'byteOffset':len(binary),'byteLength':len(png)})
        binary.extend(png);image=len(doc.setdefault('images',[]));doc['images'].append({'bufferView':view,'mimeType':'image/png'})
        texture=len(doc.setdefault('textures',[]));doc['textures'].append({'source':image})
        pbr=material.setdefault('pbrMetallicRoughness',{});pbr['baseColorTexture']={'index':texture};pbr['metallicFactor']=0
        if any(k in material.get('name','').lower() for k in ('hair','eyelash')):
            material['alphaMode']='MASK';material['alphaCutoff']=properties.get('BasePropertyOverrides',{}).get('OpacityMaskClipValue',.3333);material['doubleSided']=True
        bound+=1
    doc.setdefault('buffers',[{}])[0]['byteLength']=len(binary)
    raw=json.dumps(doc,separators=(',',':')).encode();raw+=b' '*((-len(raw))%4);binary+=b'\0'*((-len(binary))%4)
    payload=struct.pack('<II',len(raw),0x4E4F534A)+raw+struct.pack('<II',len(binary),0x004E4942)+binary
    output.write_bytes(struct.pack('<4sII',b'glTF',2,len(payload)+12)+payload)
    return {'materials':len(doc.get('materials',[])),'textured_materials':bound}


class NativeLibrary:
    def __init__(self, resources, home, store):
        self.resources=Path(resources);self.home=Path(home);self.store=store
        self.profile=json.loads((self.resources/'native-resource-profile.json').read_text(encoding='utf8'))
        from incremental_presentation import repair_registered,load as load_presentation
        with store.lock:self.presentation_repairs=repair_registered(self.resources,store)
        load_presentation(self)
        from incremental_delta import repair_asset_index
        with store.lock:self.asset_index_repairs=repair_asset_index(self.resources)
        with store.lock:self.bound_cards=load_bound_cards(self.resources)
        namespace=self.profile.get('cache_namespace')
        if namespace is not None and (not isinstance(namespace,str) or not namespace or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in namespace)):raise ValueError('资源缓存版本标识无效')
        self.root=self.home/'library'/'versions'/namespace if namespace else self.home/'library'
        self.containers=self.root/'containers';self.converted=self.root/'converted'
        self.tool=self.home/'tools/asset-decoder/cue4parse.exe';self.native=self.tool.parent/'native'
        self.ffmpeg=self.home/'tools/video/ffmpeg.exe'
        self.card_media=json.loads((self.resources/'native-card-media.json').read_text(encoding='utf8'))
        self.card_widgets=json.loads((self.resources/'native-card-widgets.json').read_text(encoding='utf8'))
        content=self.resources/'content-catalogue.json'
        self.content_routes={str(r['cardId']):r for r in json.loads(content.read_text('utf8')).get('routes',[])} if content.is_file() else {}
        registered=self.resources/'incremental-content.json'
        if registered.is_file():
            from incremental_delta import Delta
            from content_delta_v2 import art_routes
            for item in json.loads(registered.read_text('utf8'))['packages']:
                with Delta(item['path'])as delta:
                    players={str(r['card_id']):r for r in delta.json('data/players.json')['players']}
                    self.content_routes.update({str(r['cardId']):r for r in art_routes(delta,players)
                        if item['receipt']['items'].get('player_card:'+str(r['cardId']),{}).get('status')!='preserved_user'})
        self.presets=NativePresets(self)
        self.convert_lock=threading.Lock();self.setup_lock=threading.Lock();self.number_lock=threading.Lock()
        self.identity_lock = threading.Lock()
        self.thumbnail_lock=threading.Lock();self.thumbnail_frames=OrderedDict()
        self.video_covers=LibraryVideoCovers(self)
        self._face_assets=None
        try:ready=json.loads((self.root/'ready.json').read_text(encoding='utf8')).get('release_id')==self.profile['release_id'] and self.containers.is_dir()
        except (OSError,ValueError):ready=False
        self.state={'phase':'ready' if ready else 'missing','message':''}
        self.cache_path=store.root/('native-cache-'+namespace+'.json' if namespace else 'native-cache.json')
        try:self.cache=json.loads(self.cache_path.read_text(encoding='utf8'))
        except (OSError,ValueError):self.cache={}

    def db(self):
        c=sqlite3.connect('file:'+str(self.resources/'library.sqlite3').replace('\\','/')+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;return c

    def thumbnail_video_frame(self,path,seconds):
        from thumbnail_background import desktop_frame
        return desktop_frame(self,path,seconds)

    def thumbnail_movie(self,path):
        from native_movie_sources import native_path,paths as movie_paths,file as movie_file
        direct=next((row for row in getattr(self,'content_media',{}).values()if row['path']==path or
            native_path(path)and row['name']==Path(path).stem),None)
        if direct:
            if not (self.store.root/'assets'/direct['asset_id']).is_file():raise ValueError('增量动画素材未读取，请重新导入对应内容增量包')
            return dict(direct)
        if path not in movie_paths(self):raise ValueError('小头像动画不在当前资源库中')
        with self.convert_lock:
            source=movie_file(self,path)
            movie=source.with_suffix('.webm');meta=convert_movie(source,movie,self.ffmpeg)
            asset=self.store.import_asset(movie,'video')
            return {'asset_id':asset['id'],'url':asset['url'],'kind':'video',**meta}

    def summary(self):
        with contextlib.closing(self.db()) as c:s=json.loads(c.execute("SELECT value FROM meta WHERE key='summary'").fetchone()[0])
        return {**s,**self.state,'tools_ready':self.tool.is_file(),'cache_directory':str(self.root),'game_version':self.profile.get('game_version','V14')}

    LEAGUE_LABELS={'English League':'英格兰联赛','Italian League':'意大利联赛','Spanish League':'西班牙联赛',
        'French League':'法国联赛','German League':'德国联赛','Portuguese League':'葡萄牙联赛','Dutch League':'荷兰联赛',
        'English 2nd Division':'英格兰乙级联赛','Italian 2nd Division':'意大利乙级联赛','Spanish 2nd Division':'西班牙乙级联赛'}

    def _identity_index(self):
        """Card IDs by native nationality and club, read once per library file."""
        with self.identity_lock:
            path = self.resources / 'library.sqlite3'
            stat = path.stat()
            key = (path, stat.st_size, stat.st_mtime_ns)
            cached = getattr(self, '_identity_index_cache', None)
            if cached and cached[0] == key:
                return cached[1]
            nations = {}; teams = {}
            with contextlib.closing(self.db()) as c:
                for pid, nation, team in c.execute(
                    "SELECT id,json_extract(payload,'$._nation_id'),"
                    "json_extract(payload,'$.initial_base_team_id') FROM players"
                ):
                    # players.id is the primary key, so each group contains unique IDs.
                    if type(nation) is int:
                        nations.setdefault(nation, []).append(pid)
                    if type(team) is int:
                        teams.setdefault(team, []).append(pid)
            value = {'nations': nations, 'teams': teams}
            self._identity_index_cache = (key, value)
            return value

    # Sponsor names and season prefixes change between data releases; match
    # stable keywords in order (second tiers before first tiers).
    LEAGUE_KEYWORDS=(('Ligue 2','法国乙级联赛'),('Ligue 1','法国甲级联赛'),('Eredivisie','荷兰甲级联赛'),
        ('Liga Portugal','葡萄牙超级联赛'),('Brasileir','巴西甲级联赛'),('Argentine League','阿根廷联赛'),
        ('J2 LEAGUE','日本J2联赛'),('J1 LEAGUE','日本J1联赛'),('Chilean League','智利联赛'),('Swiss League','瑞士超级联赛'),
        ('Süper Lig','土耳其超级联赛'),('Scottish Premiership','苏格兰超级联赛'),('Danish League','丹麦超级联赛'),
        ('Belgian League','比利时甲级联赛'),('Colombian League','哥伦比亚联赛'),('SEALION 6 LEAGUE 1','泰国甲级联赛'),
        ('American League 2','美国联赛（第二级别）'),('American League','美国职业大联盟'),('Korean League','韩国K联赛'),
        ('Liga Super Malaysia','马来西亚超级联赛'),('Moroccan League','摩洛哥联赛'))

    def _league_label(self,name):
        return str(name or '')

    def _leagues(self):
        """Native league membership exported with this library (team-leagues.json)."""
        from native_leagues import for_library
        data=for_library(self)
        rows=[]
        for key,row in (data.get('competitions') or {}).items():
            ids=list(dict.fromkeys(int(v) for v in row.get('team_ids',[]) if type(v) is int))
            if ids:rows.append({'id':str(key),'name':self._league_label(row.get('name'))or str(key),
                                'name_en':row.get('name_en',row.get('name','')),'team_ids':ids})
        return rows

    def search_filters(self):
        """Nationalities and league→team choices that actually contain cards."""
        index=self._identity_index();identities=self.identities()
        names={kind:{int(r['id']):r for r in rows} for kind,rows in identities.items()}
        nations=[{'id':nid,'name':names['nation'].get(nid,{}).get('name') or str(nid),
                  'name_en':names['nation'].get(nid,{}).get('name_en',''),'count':len(ids)}
                 for nid,ids in index['nations'].items()]
        nations.sort(key=lambda r:r['name'])
        leagues=[]
        for league in self._leagues():
            teams=[{'id':tid,'name':names['team'].get(tid,{}).get('name') or str(tid),
                    'name_en':names['team'].get(tid,{}).get('name_en',''),'count':len(index['teams'][tid])}
                   for tid in league['team_ids'] if index['teams'].get(tid)]
            if teams:
                teams.sort(key=lambda r:r['name'])
                leagues.append({'id':league['id'],'name':league['name'],'name_en':league['name_en'],
                                'count':sum(t['count'] for t in teams),'teams':teams})
        return {'nations':nations,'leagues':leagues}

    def _identity_filter(self,body):
        """None when unfiltered; otherwise the exact matching card IDs."""
        def number(key):
            value=body.get(key)
            if value in (None,''):return None
            try:return int(value)
            except (TypeError,ValueError):raise ValueError('筛选条件无效：'+key)
        nation,team,league=number('nation'),number('team'),str(body.get('league') or '')
        if nation is None and team is None and not league:return None
        index=self._identity_index();ids=None
        def meet(values):
            nonlocal ids
            if ids is None:
                ids = set(values)
            else:
                ids.intersection_update(values)
        if nation is not None:meet(index['nations'].get(nation,()))
        if team is not None:meet(index['teams'].get(team,()))
        elif league:
            row=next((r for r in self._leagues() if r['id']==league),None)
            meet((pid for tid in row['team_ids'] for pid in index['teams'].get(tid, ())) if row else ())
        return ids

    def search(self, body):
        q=str(body.get('query','')).strip().casefold()[:120];kind=body.get('kind','');page=max(0,int(body.get('page',0)));limit=24
        where=[];values=[]
        from catalogue_trash import active as trash_items
        recycled=trash_items(self.store)
        if recycled:
            where.append('id '+('IN'if body.get('recycle_bin')else'NOT IN')+' ('+','.join('?'for _ in recycled)+')');values.extend(recycled)
        elif body.get('recycle_bin'):where.append('0')
        if q:
            where.append('search LIKE ?');values.append('%'+q.replace('%','').replace('_','')+'%')
        if body.get('position'):
            where.append('position=?');values.append(body['position'])
        if kind:where.append('card_type=?');values.append(kind)
        identity=self._identity_filter(body)
        if identity is not None:
            where.append('id IN (SELECT value FROM json_each(?))');values.append(json.dumps(sorted(identity)))
        if body.get('for_pack'):
            from pack_catalogue import exclusions
            blocked=exclusions(self.resources)
            if blocked:
                where.append('id NOT IN ('+','.join('?' for _ in blocked)+')');values.extend(blocked)
        clause=' WHERE '+' AND '.join(where) if where else ''
        cover_index=self.video_covers.index()
        with contextlib.closing(self.db()) as c:
            count=c.execute('SELECT count(*) FROM players'+clause,values).fetchone()[0]
            rows=[dict(r) for r in c.execute('SELECT id,name,name_en,position,card_type,overall,level_limit,base_id,source,json_extract(payload,\'$.default_rarity\') AS rarity_enum FROM players'+clause+' ORDER BY CASE WHEN card_type="BigTime" THEN 0 WHEN card_type="Epic" THEN 1 WHEN card_type="Legendary" THEN 2 ELSE 3 END, CAST(id AS INTEGER) DESC LIMIT ? OFFSET ?',values+[limit,page*limit])]
            for row in rows:
                row['portrait']=self._portrait(c,row['id'],row['base_id'])
                tkey=self._library_thumb_key(row,cover_index)
                row['card_thumb_key']=tkey
                row['card_thumb_url']=self.store.card_thumb_url(tkey) if hasattr(self.store,'card_thumb_url') else None
        return {'rows':rows,'total':count,'page':page,'pages':max(1,(count+limit-1)//limit),'recycled':len(recycled)}

    def _armleg_keys(self):
        cached = getattr(self, '_cached_armleg_keys', None)
        if cached is not None:
            return cached
        keys = set()
        if self.containers.is_dir():
            try:
                for path in self.face_assets().files:
                    name = path.rsplit('/', 1)[-1].removesuffix('.uasset')
                    m = re.fullmatch(r'T_(\d+)(?:(?:ep|lg|hi|sp)\d+v\d+)?_ArmLeg_D', name)
                    if m:
                        keys.add(m[1])
            except Exception:
                pass
        self._cached_armleg_keys = keys
        return keys

    def appearance_templates(self, body):
        """Search native players with valid PlayerAppearance records for body/tattoo template replacement."""
        from native_appearance import profile as appearance_profile
        q = str(body.get('query', '')).strip().casefold()[:120]
        limit = max(1, min(30, int(body.get('limit', 16))))
        armleg = self._armleg_keys()
        rows = []
        with contextlib.closing(self.db()) as c:
            if q:
                sql = ('SELECT id,name,name_en,position,card_type,overall,base_id FROM players '
                       'WHERE search LIKE ? OR id=? ORDER BY CASE WHEN card_type="Standard" THEN 0 '
                       'WHEN card_type="Featured" THEN 1 ELSE 2 END, CAST(id AS INTEGER) DESC LIMIT 80')
                candidates = c.execute(sql, ('%' + q.replace('%', '').replace('_', '') + '%', q)).fetchall()
            else:
                sql = ('SELECT id,name,name_en,position,card_type,overall,base_id FROM players '
                       'ORDER BY CASE WHEN card_type="Standard" THEN 0 WHEN card_type="Epic" THEN 1 ELSE 2 END, '
                       'CAST(id AS INTEGER) DESC LIMIT 80')
                candidates = c.execute(sql).fetchall()
        seen_person = set()
        for r in candidates:
            pid = str(r['id'])
            if appearance_profile(self.resources, pid) is None:
                continue
            pkeys = set(person_keys(pid))
            person_sig = str(r['base_id'] or (int(pid) & 0xfffffff))
            if person_sig in seen_person:
                continue
            seen_person.add(person_sig)
            has_tattoo = bool(pkeys & armleg)
            rows.append({
                'id': pid,
                'name': r['name'],
                'name_en': r['name_en'],
                'position': r['position'],
                'card_type': r['card_type'],
                'overall': r['overall'],
                'has_unique_arm': has_tattoo,
            })
            if len(rows) >= limit:
                break
        return {'rows': rows}

    def _card_movie_candidates(self,aliases,names):
        for name in names:
            yield name + '_v'
            yield self._resolve_alias(aliases, name + '_v')
            yield self._resolve_alias(aliases, name) + '_v'

    def _card_route(self, pid):
        return getattr(self, 'bound_cards', {}).get(pid) or self.content_routes.get(pid, {})

    def _card_series(self, pid):
        ident = int(self._card_route(pid).get('suiteDonorId', pid))
        style = (ident >> 40) & 255
        return f'{style >> 4:02d}{style & 15:02d}', (ident >> 28) & 4095, bool(ident & (1 << 48))

    def _front_portrait(self, route, assets):
        name = front_name(route, 'f07')
        if name is None and route.get('portraitKey'):
            name = route['portraitKey'] + 'f'
        return assets.get(name) if name else None

    def _library_card_covers(self, index, style, agent, limited, aliases, direct, route=None):
        originals = index.get('native', {})
        previews = index.get('direct', {})
        direct_slots = {layer['slot']: layer for layer in direct}
        covers = []
        for slot in ('f01', 'f02'):
            override = front_name(route, slot)
            if override is not None:
                record = (originals.get(override + '_v') or originals.get(override) or {}) if override else {}
                filename, path = record.get('file'), record.get('path')
            elif layer := direct_slots.get(slot):
                filename = previews.get(layer['asset_id']) if layer['kind']=='video' else None
                path = layer['path']
            else:
                names = [f'{style}_{agent}_{slot}_lim', f'{style}_{slot}_lim'] if limited else []
                names += [f'{style}_{agent}_{slot}', f'{style}_{slot}']
                record = next((originals[k] for k in self._card_movie_candidates(aliases,names) if k in originals), {})
                filename, path = record.get('file'), record.get('path')
            if filename and re.fullmatch(r'[a-f0-9]{64}\.png', filename):
                covers.append({'slot':slot, 'path':path, 'file':filename})
        return covers

    def _library_thumb_key(self, row, cover_index):
        sig = f"v3|{row.get('id')}|{row.get('name')}|{row.get('position')}|{row.get('card_type')}|{row.get('overall')}|{row.get('portrait')}|{self.converted.name}"
        pid = str(row['id'])
        route = self._card_route(pid)
        style, agent, limited = self._card_series(pid)
        if style in ('0300','0301'):
            sig += '|baked-front-v1'
        if route.get('portraitKey'):
            sig += f"|bound-v1|{row.get('rarity_enum')}|" + json.dumps(route, sort_keys=True, separators=(',', ':'))
        if cover_index:
            from incremental_presentation import design_layers
            direct = design_layers(style,agent,limited,getattr(self,'content_media',{}))
            covers = self._library_card_covers(cover_index,style,agent,limited,self._alias_map,direct,route)
            for cover in covers:
                sig += f"|{cover['slot']}:{cover['file']}"
        digest = hashlib.sha256(sig.encode('utf8')).hexdigest()[:12]
        safe_id = re.sub(r'[^a-z0-9]+', '', str(row.get('id') or '').lower())[:20] or 'card'
        return f'lib_{safe_id}_{digest}'

    def _ensure_asset_index(self, c):
        """Cache portrait/card asset lookup by name and alias map in memory (keyed by sqlite mtime/size)."""
        try:
            st = (self.resources / 'library.sqlite3').stat()
            sig = (st.st_size, st.st_mtime_ns)
        except OSError:
            sig = None
        if sig and getattr(self, '_asset_index_sig', None) == sig:
            return self._asset_by_name, self._portrait_by_name, self._alias_map
        alias_map = {}
        try:
            for before, after in c.execute('SELECT before, after FROM card_aliases'):
                alias_map[before] = after
        except sqlite3.OperationalError:
            pass
        by_name = {}
        portrait_by_name = {}
        # Prefer PesMobile/% paths just like the original ORDER BY CASE WHEN path LIKE 'PesMobile/%' THEN 0 ELSE 1 END
        for r in c.execute("SELECT id, kind, name, path, player_key FROM assets WHERE kind IN ('portrait','card')"):
            row = dict(r)
            name = row['name']
            is_pes = row['path'].startswith('PesMobile/')
            prev = by_name.get(name)
            if prev is None or (is_pes and not prev['path'].startswith('PesMobile/')):
                by_name[name] = row
            if row['kind'] == 'portrait':
                pprev = portrait_by_name.get(name)
                if pprev is None or (is_pes and not pprev['path'].startswith('PesMobile/')):
                    portrait_by_name[name] = row
        self._asset_index_sig = sig
        self._asset_by_name = by_name
        self._portrait_by_name = portrait_by_name
        self._alias_map = alias_map
        return by_name, portrait_by_name, alias_map

    def _portrait(self,c,pid,base):
        assets, portrait_by_name, _ = self._ensure_asset_index(c)
        front = self._front_portrait(self._card_route(pid), assets)
        if front:
            return front['id']
        return self._ordinary_portrait(c,pid,base,portrait_by_name)

    def _ordinary_portrait(self,c,pid,base,portrait_by_name):
        for candidate in (pid+'_l',pid+'_',base+'_l',base+'_'):
            candidate=self._alias(c,candidate)
            hit = portrait_by_name.get(candidate)
            if hit: return hit['id']
        return None

    def _thumbnail(self,c,pid,base):
        # '_' is the native squad/roster image; '_l' belongs to the larger
        # artwork. Resolve the game's aliases before choosing the package.
        _, portrait_by_name, _ = self._ensure_asset_index(c)
        bound_key = self._card_route(str(pid)).get('portraitKey')
        if bound_key and (row := portrait_by_name.get(bound_key + '_')):
            return row['id']
        for key in dict.fromkeys((str(pid),str(base),str(int(pid)&0xfffffff))):
            name=self._alias(c,key+'_')
            hit = portrait_by_name.get(name)
            if hit: return hit['id']
        return None

    def _alias(self,c,name):
        _, _, alias_map = self._ensure_asset_index(c)
        return self._resolve_alias(alias_map, name)

    @staticmethod
    def _resolve_alias(alias_map,name):
        visited=set()
        while name not in visited:
            visited.add(name)
            nxt = alias_map.get(name)
            if not nxt or nxt==name:break
            name=nxt
        return name

    def identities(self):
        with contextlib.closing(self.db()) as c:
            try:rows=[dict(r) for r in c.execute('SELECT * FROM identities ORDER BY name')]
            except sqlite3.OperationalError:rows=[]
        from team_names import display
        return {kind:[display(r) for r in rows if r['kind']==kind] for kind in ('nation','team')}

    def player(self,pid):
        with contextlib.closing(self.db()) as c:
            row=c.execute('SELECT * FROM players WHERE id=?',(str(pid),)).fetchone()
            if not row:raise ValueError('球员编号不在当前数据库中')
            from native_celebration import restore_payload
            r=dict(row);r['payload']=restore_payload(json.loads(r['payload']));r['portrait']=self._portrait(c,r['id'],r['base_id'])
            from ai_playing_styles import restore_payload as restore_ai_styles
            restore_ai_styles(r['payload'])
            if not r['payload'].get('_studio_number_source'):
                from native_numbers import restore as restore_number
                restore_number(r['payload'],int(pid),int(r['payload'].get('initial_base_team_id') or 0),self._shirt_number_assignments())
            r['thumbnail']=self._thumbnail(c,r['id'],r['base_id'])
            from native_appearance import profile
            r['appearance_profile']=profile(self.resources,r['id'])
            if r['payload']['parameters'][47]==0:
                raise ValueError('原数据库此条是体型、能力为零的空占位记录，无法作为真实球员模板')
            alias=self._alias(c,r['id']+'_').split('_')[0]
            # Native legend IDs retain their person ID in the low 28 bits.
            # An exact name join cannot find retired players with no base row.
            person=str(int(r['id'])&0xfffffff)
            model_keys=list(dict.fromkeys([r['id'],r['base_id'],alias,person,*person_keys(r['id']),*person_keys(r['base_id'])]))
            matches=[dict(v) for v in c.execute('SELECT * FROM assets WHERE kind="model" AND player_key IN ('+','.join('?' for _ in model_keys)+') ORDER BY CASE WHEN name LIKE "%_Face" THEN 0 ELSE 1 END,name LIMIT 40',model_keys)]
            r['models']=matches
            r['model_material_revision']=MATERIAL_DECODER_REVISION
            selected,r['model_resolution']=resolve_face(matches,r['id'])
            r['auto_model']=selected['id'] if selected else None
            r['face_variants']=[m for m in matches if m['name'].endswith('_Face')]
            r['card_layers']=[dict(v) for v in c.execute('SELECT * FROM assets WHERE kind="card" AND player_key=? ORDER BY name',(r['id'],))]
            r['identity_names']={}
            for kind,value in [('nation',r['payload'].get('_nation_id')),('team',r['payload'].get('initial_base_team_id') or r['payload'].get('initial_base_national_team_id'))]:
                try:name=c.execute('SELECT name,name_en FROM identities WHERE kind=? AND id=?',(kind,value)).fetchone()
                except sqlite3.OperationalError:name=None
                r['identity_names'][kind]={'id':value,'name':name['name'] if name else '', 'name_en':name['name_en'] if name else ''}
            return r

    def _shirt_number_assignments(self):
        from apk_sources import path_for_resources,packages_for_resources,file_stamp
        from native_numbers import assignments
        source=None;key=None
        try:
            source=path_for_resources(self.resources);packages=packages_for_resources(self.resources)
            key={'main':file_stamp(source),'dependencies':[file_stamp(p)for p in sorted(set(packages.values()))]}
        except (OSError,ValueError):key={'path':source,'unavailable':True}
        with self.number_lock:
            if key!=getattr(self,'number_source_key',None):
                self.number_assignments={};self.number_source_key=key
                if source:
                    try:
                        from native_cpk import Cpk
                        from resource_set import Reader
                        with Reader(source,packages)as archive:
                            names=['assets/gateway-release/'+name+'/dt870_mobile_and.cpk'for name in('livedata','custom-resources/legends-v10')]
                            self.number_assignments=assignments([Cpk(archive.read(name,limit=None))for name in names if name in archive.namelist()])
                    except (OSError,KeyError,ValueError,zipfile.BadZipFile):pass
            return self.number_assignments

    def assets(self, body):
        kind=body.get('kind','portrait');q=str(body.get('query','')).strip()[:120];page=max(0,int(body.get('page',0)));limit=18
        if kind not in ('portrait','thumbnail','card','model'):raise ValueError('未知资源类别')
        where=' WHERE kind=?';params=['portrait' if kind=='thumbnail' else kind]
        if kind=='thumbnail':
            where+=" AND substr(name,-1)='_' AND (path LIKE 'PesMobile/%' OR NOT EXISTS (SELECT 1 FROM assets preferred WHERE preferred.kind=assets.kind AND preferred.name=assets.name AND preferred.path LIKE 'PesMobile/%'))"
        with contextlib.closing(self.db()) as c:
            if q:
                text='%'+q.replace('%','')+'%'
                owners=list(c.execute('SELECT id,base_id FROM players WHERE search LIKE ? LIMIT 80',(text,)))
                keys={str(v) for row in owners for v in row}
                if kind=='thumbnail':
                    for row in owners:
                        keys.update((str(int(row['id'])&0xfffffff),self._alias(c,str(row['id'])+'_').split('_')[0]))
                keys=list(keys)
                where+=' AND (name LIKE ? OR path LIKE ?'
                params+=[text,text]
                if keys:where+=' OR player_key IN ('+','.join('?' for _ in keys)+')';params+=keys
                where+=')'
            count=c.execute('SELECT count(*) FROM assets'+where,params).fetchone()[0]
            rows=[dict(r) for r in c.execute('SELECT * FROM assets'+where+' ORDER BY name LIMIT ? OFFSET ?',params+[limit,page*limit])]
        for row in rows:
            hit=self.cache.get(row['id'])
            if row['kind']=='model' and row['name'].endswith('_Face') and hit and hit.get('decoder_revision')!=FACE_DECODER_REVISION:hit=None
            row['converted']=hit if hit and (self.store.root/'assets'/hit['asset_id']).is_file() else None
        if kind=='thumbnail':
            with contextlib.closing(self.db()) as c:
                for row in rows:
                    owner=c.execute('SELECT name,name_en FROM players WHERE id=? OR base_id=? ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END LIMIT 1',(row['player_key'],row['player_key'],row['player_key'])).fetchone()
                    row['player_name']=owner['name'] if owner else ''
        return {'rows':rows,'total':count,'page':page,'pages':max(1,(count+limit-1)//limit)}

    def card(self,pid):
        if self.state['phase']!='ready':raise ValueError('请先准备原版资源缓存')
        player=self.player(pid)
        pid=str(pid)
        route=self._card_route(pid)
        style,agent,limited=self._card_series(pid)
        layers=[];movies=[];missing=[]
        from incremental_presentation import design_layers
        direct=[row for row in design_layers(style,agent,limited,getattr(self,'content_media',{}))
                if front_name(route,row['slot']) is None]
        direct_slots={row['slot']for row in direct}
        from native_movie_sources import paths as movie_paths,file as movie_file
        native_movies={Path(path).stem:path for path in movie_paths(self)}
        with contextlib.closing(self.db()) as c:
            asset_index,portrait_index,aliases=self._ensure_asset_index(c)
            bound_portrait=self._front_portrait(route,asset_index)
            if bound_portrait:
                direct=[row for row in direct if row['slot']!='f07']
                direct_slots.discard('f07')
            ordinary=self._ordinary_portrait(c,pid,player['base_id'],portrait_index)
            portrait_row=c.execute('SELECT name FROM assets WHERE id=?',(ordinary,)).fetchone() if ordinary else None
            portrait_pid=portrait_row[0].split('_')[0] if portrait_row else str(pid)
            art_ids=list(dict.fromkeys([str(pid),portrait_pid]))
            def common_names(slot):
                names=[f'{style}_{agent}_{slot}_lim',f'{style}_{slot}_lim'] if limited else []
                return names+[f'{style}_{agent}_{slot}',f'{style}_{slot}']
            def texture(names):
                for name in names:
                    resolved=self._alias(c,name)
                    row=c.execute('SELECT * FROM assets WHERE name=? AND kind IN ("portrait","card") ORDER BY CASE WHEN path LIKE "PesMobile/%" THEN 0 ELSE 1 END LIMIT 1',(resolved,)).fetchone()
                    if row:return dict(row)
            def movie(names):
                return next((native_movies[k] for k in self._card_movie_candidates(aliases,names) if k in native_movies),None)
            for slot in ('f01','f02','f03','f04','f05','f06','f07','f09','f10'):
                if slot in direct_slots:continue
                override=front_name(route,slot)
                if slot=='f07' and bound_portrait:continue
                blend='screen' if slot in ('f03','f05','f06','f07','f10') else 'source-over'
                if override is not None:
                    path=native_movies.get(override+'_v') or native_movies.get(override) if override else None
                    if path:movies.append({'slot':slot,'path':path,'kind':'video','blend':blend})
                    elif override and (row:=asset_index.get(override)):layers.append({**row,'slot':slot,'blend':blend})
                    elif override:missing.append({'path':override,'reason':'绑定的卡面图层无法读取'})
                    continue
                names=[f'{style}_{slot}_{key}_' for key in art_ids]+common_names(slot)
                row=texture(names)
                if row:layers.append({**row,'slot':slot,'blend':blend})
            if player['portrait']:
                row=c.execute('SELECT * FROM assets WHERE id=?',(player['portrait'],)).fetchone()
                layers.append({**dict(row),'slot':'portrait','blend':'source-over',**({'native_slot':'f07'} if bound_portrait else {})})
            # The native ST_PlayerCardFrontTexture contains f01..f10. bXX are
            # card-back layers; mXX are MobileIcon layers, never front layers.
            for slot in ('f02','f03','f05','f10'):
                if slot in direct_slots or front_name(route,slot) is not None:continue
                path=movie(common_names(slot))
                if path:movies.append({'slot':slot,'path':path,'kind':'video','blend':'source-over' if slot=='f02' else 'screen'})
        converted=self.convert({'ids':list(dict.fromkeys(row['id'] for row in layers))})['results'] if layers else {}
        output=[]
        for row in direct:
            if (self.store.root/'assets'/row['asset_id']).is_file():output.append(row)
            else:missing.append({'path':row['path'],'reason':'增量卡面素材未读取，请重新导入对应内容增量包'})
        for row in layers:
            hit=converted.get(row['id'],{})
            if hit.get('asset_id'):output.append({**hit,'slot':row['slot'],'blend':row['blend'],**({'native_slot':row['native_slot']} if row.get('native_slot') else {})})
            else:missing.append({'path':row['path'],'reason':hit.get('error','原版图层无法读取')})
        font_paths={name:'PesMobile/Content/Platforms/_Android/Assets/ui/Font/'+path+'.ufont' for name,path in [('sans','eFootballSans-Regular'),('headline','eFootballStencil-Regular'),('chinese','NotoSansSC-SemiBold')]}
        with self.convert_lock:
            for row in movies:movie_file(self,row['path'])
            raw_paths=list(font_paths.values())
            requests=[path for path in raw_paths if not (self.converted/path).is_file()]
            if requests:self.run(requests,'raw')
            for row in movies:
                path=self.converted/row['path']
                try:
                    webm=path.with_suffix('.webm');meta=convert_movie(path,webm,self.ffmpeg)
                    imported=self.store.import_asset(webm,'video')
                    output.append({**row,**meta,'name':path.stem,'asset_id':imported['id'],'url':imported['url']})
                except (ValueError,OSError) as exc:missing.append({'path':row['path'],'reason':str(exc)})
            fonts={}
            for name,ref in font_paths.items():
                path=self.converted/ref
                if not path.is_file():missing.append({'path':ref,'reason':'原版字体未读取'});continue
                raw=path.read_bytes();font=path.with_suffix('.otf' if raw[:4]==b'OTTO' else '.ttf');font.write_bytes(raw)
                asset=self.store.import_asset(font,'font');fonts[name]={'asset_id':asset['id'],'path':ref}
        animated_slots={row['slot'] for row in output if row['kind']=='video'}
        output=[row for row in output if row['kind']=='video' or row['slot'] not in animated_slots]
        order={'f01':0,'f02':1,'portrait':2,'f04':3,'f03':4,'f05':5,'f06':6,'f07':7,'f10':8,'f09':9}
        output.sort(key=lambda row:order[row['slot']])
        suite=getattr(self,'content_suites',{}).get(f'{style}/{agent}',{})
        for name in suite.get('appearance',{}).get('detail',{}).values():
            match=re.search(r'_(f\d\d)(?:_|$)',str(name))
            if match and not any(row['slot']==match[1]for row in output)and not any(str(name)in row['path']for row in missing):
                missing.append({'path':name,'reason':'此卡面套件声明的图层尚未读取'})
        return {'version':1,'composition_revision':3,'source_id':str(pid),'native_style':style,'native_agent':agent,
                'card_type':player['card_type'],'source_label':player['payload'].get('_studio_card_label',''),
                'suppress_native_date':front_name(route,'f09')=='',
                'baked_portrait':not bound_portrait and baked_front(output),
                'layers':output,'fonts':fonts,'layout':self.card_media['layout'],'missing':missing,
                'ui':{key:self.card_widgets[key] for key in ('widgets','states','native_dispatch')},'info':self.card_info(pid),
                'player_preview':{
                    'id':str(pid),
                    'name':player['name'],
                    'position':player['position'],
                    'overall':player['overall'],
                    'card_type':player['card_type'],
                    'team_id':player['payload'].get('initial_base_team_id') or player['payload'].get('initial_base_national_team_id') or 0,
                    'nation_id':player['payload'].get('_nation_id') or 0,
                    'booster_ids':(player['payload'].get('initial_booster_id_list') or [])[:2],
                },
                'video_frames_unchanged':True,'renderer':'native-assets-browser-composite',
                'material_verified_in_game':False}

    def _card_fonts(self):
        cached = getattr(self, '_cached_card_fonts', None)
        if cached and all((self.store.root / 'assets' / v['asset_id']).is_file() for v in cached.values()):
            return dict(cached), []
        font_paths = {name: 'PesMobile/Content/Platforms/_Android/Assets/ui/Font/' + path + '.ufont'
                      for name, path in [('sans', 'eFootballSans-Regular'), ('headline', 'eFootballStencil-Regular'), ('chinese', 'NotoSansSC-SemiBold')]}
        missing = []
        fonts = {}
        with self.convert_lock:
            raw_paths = list(font_paths.values())
            requests = [path for path in raw_paths if not (self.converted / path).is_file()]
            if requests:
                self.run(requests, 'raw')
            for name, ref in font_paths.items():
                path = self.converted / ref
                if not path.is_file():
                    missing.append({'path': ref, 'reason': '原版字体未读取'})
                    continue
                raw = path.read_bytes()
                font = path.with_suffix('.otf' if raw[:4] == b'OTTO' else '.ttf')
                if not font.is_file():
                    font.write_bytes(raw)
                asset = self.store.import_asset(font, 'font')
                fonts[name] = {'asset_id': asset['id'], 'path': ref}
        if len(fonts) == 3:
            self._cached_card_fonts = dict(fonts)
        return fonts, missing

    def _import_cached_image(self, image_path: Path):
        """Memoize store.import_asset(path, 'image') by (path, size, mtime_ns) to avoid repeated SHA-256 hashing."""
        cache = getattr(self, '_image_import_cache', None)
        if cache is None:
            cache = {}
            self._image_import_cache = cache
        try:
            st = image_path.stat()
        except OSError:
            return None
        sig = (str(image_path), st.st_size, st.st_mtime_ns)
        hit = cache.get(sig)
        if hit and (self.store.root / 'assets' / hit['id']).is_file():
            return hit
        asset = self.store.import_asset(image_path, 'image')
        cache[sig] = asset
        return asset

    def _converted_card_texture(self, row):
        path = self.converted / Path(row['path']).with_suffix('.png')
        if '/character/RealFace/' in row['path'] and re.fullmatch(r'[0-9]+(?:_|f)',row['name']) and not path.is_file():
            # The legacy archive indexes these packages under RealFace, but the
            # decoder writes textures under the mounted name inside the package.
            return self.converted / 'PesMobile/Content/Assets/ui/Data/Thumbnail/Player' / (row['name'] + '.png')
        return path

    def _shared_ui_graphic_paths(self):
        graphics = self.card_widgets['graphics']
        paths = {}
        for i in range(6):
            key = f'UiPlayerCardInfo_RarityStar_{i}'
            if key in graphics:
                paths[f'stars{i}'] = graphics[key]
        for key, name in [('logo', 'UiPlayerCardInfo_eFootballLogo'), ('combi', 'UiPlayerCardInfo_Combi'),
                          ('celebration', 'UiPlayerCardInfo_Celebration'), ('boosterEffect', 'UiPlayerCardBoosterEffect')]:
            if name in graphics:
                paths[key] = graphics[name]
        for i in range(6):
            name = 'CmnIconBoosterTiny_' + (str(i) if i else 'Empty')
            if name in graphics:
                paths[f'booster{i}'] = graphics[name]
        return paths

    def card_previews(self, ids):
        """Fast batched static-first card specs for a single library page (1–24 players).
        Merges all missing card textures, portraits, club/nation emblems and shared UI icons
        into ONE single cue4parse invocation to eliminate per-card subprocess startup overhead.
        """
        if self.state['phase'] != 'ready':
            raise ValueError('请先准备原版资源缓存')
        if not isinstance(ids, list) or not 1 <= len(ids) <= 24:
            raise ValueError('每次批量读取 1–24 张原版卡面')
        pids = list(dict.fromkeys(str(i) for i in ids))
        from incremental_presentation import design_layers
        cover_index = self.video_covers.index()
        if not hasattr(self, '_preview_symbols'):
            self._preview_symbols = merged_symbols(self.card_widgets['symbols'], self.face_assets().files) if self.containers.is_dir() else dict(self.card_widgets['symbols'])
        symbols = self._preview_symbols
        ui_paths = self._shared_ui_graphic_paths()

        plans = {}
        tex_rows_by_id = {}  # asset_db_id -> row dict
        symbol_refs = set()  # club/nation/ui paths to decode in the same batch

        # Single lightweight SQLite pass + O(1) in-memory asset name index
        with contextlib.closing(self.db()) as c:
            asset_by_name, portrait_by_name, aliases = self._ensure_asset_index(c)
            placeholders = ','.join('?' for _ in pids)
            player_rows = {str(r['id']): dict(r) for r in c.execute(f'SELECT id,name,position,card_type,overall,base_id,payload FROM players WHERE id IN ({placeholders})', pids)}
            for pid in pids:
                row = player_rows.get(pid)
                if not row:
                    plans[pid] = None
                    continue
                payload = json.loads(row['payload'])
                route = self._card_route(pid)
                portrait_row = None
                for cand in (pid + '_l', pid + '_', str(row['base_id']) + '_l', str(row['base_id']) + '_'):
                    hit = portrait_by_name.get(self._alias(c, cand))
                    if hit:
                        portrait_row = hit
                        break
                style, agent, limited = self._card_series(pid)
                bound_portrait = self._front_portrait(route, asset_by_name)
                direct = design_layers(style, agent, limited, getattr(self, 'content_media', {}))
                covers = self._library_card_covers(cover_index,style,agent,limited,aliases,direct,route)
                direct = [r for r in direct if r['kind']!='video' and front_name(route,r['slot']) is None
                          and not (r['slot']=='f07' and bound_portrait)]
                direct_slots = {r['slot'] for r in direct + covers}
                portrait_pid = portrait_row['name'].split('_')[0] if portrait_row else pid
                art_ids = list(dict.fromkeys([pid, portrait_pid]))

                def common_names(slot):
                    names = [f'{style}_{agent}_{slot}_lim', f'{style}_{slot}_lim'] if limited else []
                    return names + [f'{style}_{agent}_{slot}', f'{style}_{slot}']

                def texture(names):
                    for name in names:
                        hit = asset_by_name.get(self._alias(c, name))
                        if hit:
                            return hit
                    return None

                layers = []
                for slot in ('f01', 'f02', 'f03', 'f04', 'f05', 'f06', 'f07', 'f09', 'f10'):
                    if slot in direct_slots or slot=='f07' and bound_portrait:
                        continue
                    override = front_name(route,slot)
                    if override is None:
                        names = [f'{style}_{slot}_{key}_' for key in art_ids] + common_names(slot)
                        trow = texture(names)
                    else:
                        trow = asset_by_name.get(override) if override else None
                    if trow:
                        layers.append({**trow, 'slot': slot, 'blend': 'screen' if slot in ('f03', 'f05', 'f06', 'f07', 'f10') else 'source-over'})
                        tex_rows_by_id[trow['id']] = trow
                if bound_portrait or portrait_row:
                    prow = dict(bound_portrait or portrait_row)
                    layers.append({**prow, 'slot': 'portrait', 'blend': 'source-over',
                                   **({'native_slot':'f07'} if bound_portrait else {})})
                    tex_rows_by_id[prow['id']] = prow

                # Resolve club & nation emblem paths up front so they decode in the SAME cue4parse batch
                team = int(payload.get('initial_base_team_id') or payload.get('initial_base_national_team_id') or 0)
                nation = int(payload.get('_nation_id') or 0)
                club_ref = resolve_club(symbols, team) if 0 <= team <= 131071 else None
                nation_ref = next((symbols[n] for n in (f'{nation:03d}_l', f'{nation:03d}') if n in symbols), None) if 0 <= nation <= 511 else None
                if club_ref:
                    symbol_refs.add(club_ref)
                if nation_ref:
                    symbol_refs.add(nation_ref)

                plans[pid] = {
                    'row': row, 'payload': payload, 'style': style, 'agent': agent,
                    'direct': direct, 'layers': layers, 'covers': covers,
                    'rarity': card_rarity(payload,route),
                    'suppress_native_date': front_name(route,'f09')=='',
                    'team': team, 'nation': nation, 'club_ref': club_ref, 'nation_ref': nation_ref,
                }

        # Check which shared UI icons, emblems, and card textures actually need cue4parse decoding
        ui_cached = getattr(self, '_cached_ui_graphics', None)
        if not ui_cached or not all((self.store.root / 'assets' / v['asset_id']).is_file() for v in ui_cached.values()):
            symbol_refs.update(ui_paths.values())
            ui_cached = None

        converted = {}
        missing_tex_rows = []
        with self.convert_lock:
            for tid, trow in tex_rows_by_id.items():
                hit = self.cache.get(tid)
                if hit and (self.store.root / 'assets' / hit['asset_id']).is_file():
                    converted[tid] = hit
                else:
                    missing_tex_rows.append(trow)

            needed_paths = [r['path'] for r in missing_tex_rows]
            for ref in symbol_refs:
                if not (self.converted / Path(ref).with_suffix('.png')).is_file():
                    needed_paths.append(ref)
            needed_paths = list(dict.fromkeys(needed_paths))

            # ONE single cue4parse.exe invocation for ALL textures + ALL emblems across the entire batch!
            if needed_paths:
                self.run(needed_paths)

            cache_dirty = False
            for r in missing_tex_rows:
                p = self._converted_card_texture(r)
                if not p.is_file():
                    converted[r['id']] = {'error': '这个游戏资源尚不能转换', 'path': r['path']}
                    continue
                asset = self._import_cached_image(p)
                if asset:
                    hit = {'asset_id': asset['id'], 'url': asset['url'], 'resource_id': r['id'], 'path': r['path'], 'name': r['name'], 'kind': r['kind']}
                    converted[r['id']] = hit
                    self.cache[r['id']] = hit
                    cache_dirty = True
            if cache_dirty:
                self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=2), encoding='utf8')

            # Import shared UI icons once per session
            if ui_cached is None:
                ui_cached = {}
                for key, ref in ui_paths.items():
                    image = self.converted / Path(ref).with_suffix('.png')
                    if image.is_file():
                        asset = self._import_cached_image(image)
                        if asset:
                            ui_cached[key] = {'asset_id': asset['id'], 'kind': 'image', 'path': ref}
                if ui_cached:
                    self._cached_ui_graphics = ui_cached

            # Import club & nation emblems needed by this batch (memoized)
            club_assets = {}
            nation_assets = {}
            for plan in plans.values():
                if not plan:
                    continue
                cref = plan['club_ref']
                if cref and cref not in club_assets:
                    image = converted_symbol(self.converted, cref)
                    if image.is_file() and informative_symbol(image):
                        asset = self._import_cached_image(image)
                        club_assets[cref] = {'asset_id': asset['id'], 'kind': 'image', 'path': cref} if asset else None
                    else:
                        club_assets[cref] = None
                nref = plan['nation_ref']
                if nref and nref not in nation_assets:
                    image = self.converted / Path(nref).with_suffix('.png')
                    if image.is_file():
                        asset = self._import_cached_image(image)
                        nation_assets[nref] = {'asset_id': asset['id'], 'kind': 'image', 'path': nref} if asset else None
                    else:
                        nation_assets[nref] = None

            cover_assets = {
                filename: self._import_cached_image(self.video_covers.root / filename)
                for filename in {cover['file'] for plan in plans.values() if plan for cover in plan['covers']}
            }

        fonts, font_missing = self._card_fonts()
        order = {'f01': 0, 'f02': 1, 'portrait': 2, 'f04': 3, 'f03': 4, 'f05': 5, 'f06': 6, 'f07': 7, 'f10': 8, 'f09': 9}
        ui_payload = {key: self.card_widgets[key] for key in ('widgets', 'states', 'native_dispatch')}
        results = {}
        for pid in pids:
            plan = plans.get(pid)
            if not plan:
                results[pid] = None
                continue
            row = plan['row']
            payload = plan['payload']
            output = []
            missing = list(font_missing)
            for drow in plan['direct']:
                if (self.store.root / 'assets' / drow['asset_id']).is_file():
                    output.append(drow)
            for lrow in plan['layers']:
                hit = converted.get(lrow['id'], {})
                if hit.get('asset_id'):
                    output.append({**hit, 'slot': lrow['slot'], 'blend': lrow['blend'],
                                   **({'native_slot':lrow['native_slot']} if lrow.get('native_slot') else {})})
            for cover in plan['covers']:
                hit = cover_assets[cover['file']]
                if hit:
                    output.append({'asset_id':hit['id'], 'url':hit['url'], 'kind':'image',
                                   'slot':cover['slot'], 'path':cover['path'], 'blend':'source-over'})
            if baked_front(output) and not any(layer.get('native_slot')=='f07' for layer in output):
                output = [layer for layer in output if layer['slot']!='portrait']
            output.sort(key=lambda r: order.get(r.get('slot'), 7))

            info_assets = dict(ui_cached or {})
            if plan['club_ref'] and club_assets.get(plan['club_ref']):
                info_assets['club'] = club_assets[plan['club_ref']]
            if plan['nation_ref'] and nation_assets.get(plan['nation_ref']):
                info_assets['nation'] = nation_assets[plan['nation_ref']]
            info = {
                'source_id': pid, 'team_id': plan['team'], 'nation_id': plan['nation'], 'symbol_revision': 3,
                'club_missing': bool(plan['team'] and 'club' not in info_assets), 'club_preferred': bool(plan['team']),
                'national_team': not bool(payload.get('initial_base_team_id')),
                'rarity': plan['rarity'],
                'assets': info_assets, 'missing': [],
            }
            results[pid] = {
                'version': 1, 'composition_revision': 3, 'source_id': pid,
                'native_style': plan['style'], 'native_agent': plan['agent'],
                'card_type': row['card_type'], 'source_label': payload.get('_studio_card_label', ''),
                'suppress_native_date': plan['suppress_native_date'],
                'layers': output, 'fonts': fonts, 'layout': self.card_media['layout'], 'missing': missing,
                'ui': ui_payload, 'info': info,
                'player_preview': {
                    'id': pid, 'name': row['name'], 'position': row['position'],
                    'overall': row['overall'], 'card_type': row['card_type'],
                    'team_id': plan['team'], 'nation_id': plan['nation'],
                    'booster_ids': (payload.get('initial_booster_id_list') or [])[:2],
                },
                'preview_only': True,
            }
        return {'results': results}

    def card_info(self,pid,team_id=None,nation_id=None):
        player=self.player(pid);payload=player['payload']
        team=payload.get('initial_base_team_id') or payload.get('initial_base_national_team_id') or 0
        nation=payload.get('_nation_id') or 0
        team=int(team_id) if team_id is not None else int(team)
        nation=int(nation_id) if nation_id is not None else int(nation)
        if not 0<=team<=131071 or not 0<=nation<=511:raise ValueError('球队或国家编号超出原表范围')
        graphics=self.card_widgets['graphics'];symbols=self.card_widgets['symbols'];paths={}
        if not hasattr(self,'_preview_symbols'):
            self._preview_symbols=merged_symbols(symbols,self.face_assets().files) if self.containers.is_dir() else dict(symbols)
        symbols=self._preview_symbols
        for i in range(6):
            key=f'UiPlayerCardInfo_RarityStar_{i}'
            if key in graphics:paths[f'stars{i}']=graphics[key]
        for key,name in [('logo','UiPlayerCardInfo_eFootballLogo'),('combi','UiPlayerCardInfo_Combi'),
                         ('celebration','UiPlayerCardInfo_Celebration'),('boosterEffect','UiPlayerCardBoosterEffect')]:
            if name in graphics:paths[key]=graphics[name]
        for i in range(6):
            name='CmnIconBoosterTiny_'+(str(i) if i else 'Empty')
            if name in graphics:paths[f'booster{i}']=graphics[name]
        club=resolve_club(symbols,team)
        if club:paths['club']=club
        for key,names in [('nation',[f'{nation:03d}_l',f'{nation:03d}'])]:
            path=next((symbols[n] for n in names if n in symbols),None)
            if path:paths[key]=path
        imported={};missing=[]
        with self.convert_lock:
            needed=[v for v in paths.values() if not (self.converted/Path(v).with_suffix('.png')).is_file()]
            if needed:self.run(needed)
            for key,ref in paths.items():
                image=converted_symbol(self.converted,ref) if key=='club' else self.converted/Path(ref).with_suffix('.png')
                if not image.is_file():missing.append(ref);continue
                if key=='club' and not informative_symbol(image):missing.append(ref);continue
                asset=self.store.import_asset(image,'image')
                imported[key]={'asset_id':asset['id'],'kind':'image','path':ref}
        return {'source_id':str(pid),'team_id':team,'nation_id':nation,'symbol_revision':3,
                'club_missing':bool(team and 'club' not in imported),'club_preferred':bool(team),
                'national_team':not bool(payload.get('initial_base_team_id')),
                'rarity':card_rarity(payload,self._card_route(str(pid))),
                'assets':imported,'missing':missing}

    def setup(self,body):
        if self.state['phase']=='preparing':return self.summary()
        if self.state['phase']=='ready' and self.containers.is_dir():return self.summary()
        main=Path(body.get('apk') or self.home/'原始APK'/('73efootball-'+self.profile.get('game_version','V16')+'.apk')).absolute()
        companion=Path(body.get('companion') or self.home/'原始APK'/'73efootball-离线资源包-V9+.apk').absolute()
        if not main.is_file() or not companion.is_file():raise ValueError('请选择主 APK 和离线资源包 APK')
        if not self.tool.is_file():raise ValueError('缺少随软件提供的 tools/asset-decoder 工具目录')
        self.state={'phase':'preparing','message':'正在检查两个 APK','files':0,'bytes':0}
        threading.Thread(target=self._setup,args=(main,companion),daemon=True).start();return self.summary()

    def _setup(self,main,companion,resource_packages=None):
        with self.setup_lock:
            staging=self.root/('prepare-'+uuid.uuid4().hex)
            try:
                staging.mkdir(parents=True)
                def copy(z,member,priority=0):
                    item=z.getinfo(member);name=Path(member).name
                    if priority and not name.startswith('global.'):
                        p=Path(name);name=p.stem+'_10_P'+p.suffix
                    dest=staging/name
                    self.state['message']='准备原版资源：'+name
                    with z.open(member) as src,dest.open('wb') as dst:
                        while chunk:=src.read(4*1024*1024):dst.write(chunk);self.state['bytes']+=len(chunk)
                    self.state['files']+=1
                from resource_set import Reader
                from apk_sources import resource_paths
                if resource_packages is None:resource_packages=resource_paths(self,self.store,main)
                with Reader(main,resource_packages) as z:
                    release=json.loads(z.read('assets/gateway-release/release-manifest.json'))['release_id']
                    if release!=self.profile['release_id']:raise ValueError('APK 与当前数据库不是同一版，请先在“APK 数据更新”中导入新版主包')
                    with z.open('lib/arm64-v8a/libUE4.so') as lib:
                        lib.seek(self.profile['resource_key_offset']);key=lib.read(32)
                    if hashlib.sha256(key).hexdigest()!=self.profile['resource_key_sha256']:raise ValueError('资源引擎与 V14 不同，需要适配')
                    with z.open('lib/arm64-v8a/libUE4.so') as lib:
                        if hashlib.file_digest(lib,'sha256').hexdigest()!=self.profile['native_engine_sha256']:
                            raise ValueError('APK 引擎指纹与已核对 V14 不同')
                    (staging/'resource-key.bin').write_bytes(key)
                    with z.open('assets/pc0000_mobile_and.pak') as raw,zipfile.ZipFile(raw) as nested:
                        for n in nested.namelist():
                            if n.endswith(('.pak','.ucas','.utoc')):copy(nested,n,10)
                    for n in z.namelist():
                        if n.startswith(('assets/pc0010_mobile_and.','assets/gateway-release/livedata/','assets/gateway-release/custom-resources/')) and n.endswith(('.pak','.ucas','.utoc')):copy(z,n,10)
                from import_resource_source import companion_archive
                with companion_archive(companion) as nested:
                    for n in nested.namelist():
                        if n.endswith(('.pak','.ucas','.utoc')):copy(nested,n)
                if self.containers.exists():raise ValueError('资源目录已存在，请重新启动检查，不覆盖已有缓存')
                staging.rename(self.containers)
                self.state['message']='准备资料库动态背景的静态封面'
                self.video_covers.prepare()
                (self.root/'ready.json').write_text(json.dumps({'apk':str(main),'companion':str(companion),'release_id':release,
                    'resource_packages':{str(k):str(v)for k,v in resource_packages.items()},'files':self.state['files']}),encoding='utf8')
                self.state={'phase':'ready','message':'原版资源已就绪','files':self.state['files']}
            except Exception as exc:self.state={'phase':'error','message':str(exc)}

    def run(self,paths,mode='auto'):
        from incremental_delta import decode_paths
        paths=decode_paths(self,paths,mode)
        if not paths:return
        if any(not (self.native/name).is_file() for name in ('Detex.dll','zlib-ng2.dll','oodle-data-shared.dll')):
            raise ValueError('随软件提供的离线解码库缺失，请重新解压完整软件包')
        self.converted.mkdir(parents=True,exist_ok=True)
        config=self.converted/('batch-'+uuid.uuid4().hex+'.txt');config.write_text('\n'.join(paths),encoding='utf8')
        env=os.environ.copy();env['TEMP']=str(self.native);env['TMP']=str(self.native)
        key=(self.containers/'resource-key.bin').read_bytes().hex()
        args=[str(self.tool),'-i',str(self.containers),'-g','GAME_eFootball','-k','0x'+key,'-c',str(config),'-o',str(self.converted),'-y','--mesh-format','Gltf2']
        if mode!='auto':args+=['-f',mode]
        result=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=env,timeout=120,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        config.unlink(missing_ok=True)
        (self.root/'last-conversion.log').write_bytes(result.stdout)
        if result.returncode:raise ValueError('原版资源转换失败，请查看 library/last-conversion.log')

    def convert(self,body):
        identifiers=list(dict.fromkeys(body.get('ids') or []))
        if not identifiers or len(identifiers)>24:raise ValueError('每次请选择 1–24 项资源')
        if self.state['phase']!='ready':raise ValueError('请先准备原版资源缓存')
        with self.convert_lock,contextlib.closing(self.db()) as c:
            rows=[];results={}
            for identifier in identifiers:
                row=c.execute('SELECT * FROM assets WHERE id=?',(identifier,)).fetchone()
                if not row:raise ValueError('资源编号不在游戏索引中')
                hit=self.cache.get(identifier)
                current_face=row['kind']=='model' and row['name'].endswith('_Face')
                if hit and (not current_face or (hit.get('decoder_revision')==FACE_DECODER_REVISION
                        and hit.get('texture_decoder_revision')==TEXTURE_DECODER_REVISION
                        and hit.get('material_decoder_revision')==MATERIAL_DECODER_REVISION
                        and hit.get('material_validation')==MATERIAL_VALIDATION)) and (self.store.root/'assets'/hit['asset_id']).is_file():results[identifier]=hit
                else:rows.append(dict(row))
            ordinary=[r['path'] for r in rows if r['kind']!='model' or not r['name'].endswith('_Face')]
            if ordinary:self.run(ordinary)
            for r in rows:
                suffix='.glb' if r['kind']=='model' else '.png'
                p=self.converted/Path(r['path']).with_suffix(suffix) if suffix=='.glb' else self._converted_card_texture(r)
                face_metadata={}
                if r['kind']=='model' and r['name'].endswith('_Face'):
                    p,face_metadata=self.convert_face(r)
                if not p.is_file():
                    results[r['id']]={'error':'这个游戏资源尚不能转换，未使用替代图像','path':r['path']};continue
                metadata={}
                if suffix=='.glb':p,metadata=self.texture_model(p,r)
                asset=self.store.import_asset(p,'model' if suffix=='.glb' else 'image')
                hit={'asset_id':asset['id'],'url':asset['url'],'resource_id':r['id'],'path':r['path'],'name':r['name'],'kind':r['kind'],**metadata,**face_metadata}
                results[r['id']]=hit;self.cache[r['id']]=hit
            self.cache_path.write_text(json.dumps(self.cache,ensure_ascii=False,indent=2),encoding='utf8')
            return {'results':results}

    def face_assets(self):
        if self._face_assets is None:
            from native_art import Assets
            self._face_assets=Assets(self.containers)
            from incremental_delta import overlay_assets
            self._face_assets=overlay_assets(self._face_assets,self.resources)
        return self._face_assets

    def convert_face(self,row):
        from native_skeletal import mesh_glb
        assets=self.face_assets();raw,_,details=assets.face(row['path'])
        class SelectedFace:
            def raw(self,path):return raw
        target=self.converted/Path(row['path']).with_suffix('.glb')
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(mesh_glb(SelectedFace(),row['path']))
        return target,{'decoder_revision':FACE_DECODER_REVISION,'face_source':details}

    def texture_model(self,path,row):
        from native_materials import material_properties,package_path,validate_face_textures
        b=path.read_bytes();length=struct.unpack_from('<I',b,12)[0];doc=json.loads(b[20:20+length]);names=[m['name'] for m in doc.get('materials',[]) if m.get('name')]
        folder=row['path'].rsplit('/',1)[0]
        strict=row.get('kind','model')=='model'and (row.get('name')or Path(row['path']).stem).endswith('_Face')
        if strict:
            used={primitive.get('material',0)for mesh in doc.get('meshes',[])for primitive in mesh.get('primitives',[])}
            if not used or any(type(index)is not int or not 0<=index<len(doc.get('materials',[]))for index in used):
                raise ValueError('原版脸模材质索引无效')
            names=[doc['materials'][index].get('name')for index in sorted(used)]
            if not all(names):raise ValueError('原版脸模材质缺少名称')
        # Original UE material parameters supply the exact texture links.
        sources={m['name']:m.get('extras',{}).get('studio_material_path') for m in doc.get('materials',[]) if m.get('name')}
        candidates={name:package_path(sources[name]) if (sources.get(name)or'').startswith('/Game/') else folder+'/'+name+'.uasset' for name in names}
        if candidates:self.run(list(candidates.values()),'json')
        materials={};requests=[];bindings=[]
        def load_material(ref):
            file=self.converted/Path(ref).with_suffix('.json')
            if ref not in candidates.values():self.run([ref],'json')
            if not file.is_file():raise ValueError('原版材质 JSON 未生成：'+ref)
            return json.loads(file.read_text(encoding='utf-8-sig'))
        for name in names:
            try:props=material_properties(candidates[name],load_material)
            except (ValueError,OSError):
                if strict:raise
                continue
            for param in props.get('TextureParameterValues',[]):
                label=param.get('ParameterInfo',{}).get('Name','').casefold()
                if label not in COLOR_PARAMETERS:continue
                ref=param.get('ParameterValue',{}).get('ObjectPath','')
                if not ref.startswith('/Game/'):continue
                texture=package_path(ref)
                requests.append(texture);bindings.append((name,texture,props));break
            else:
                if strict:raise ValueError('原版脸模材质缺少 BaseColor 贴图引用：'+candidates[name])
        texture_overrides=self._prepare_model_textures(requests)
        for name,ref,props in bindings:
            png=self.converted/Path(ref).with_suffix('.png')
            if png.is_file():materials[name]=(png.read_bytes(),props)
            elif strict:raise ValueError('原版脸模 BaseColor 贴图未生成：'+ref)
        target=path.with_name(path.stem+'-native.glb');temporary=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            metadata=glb_textures(path,materials,temporary)
            if strict:
                metadata['validated_materials']=validate_face_textures(temporary)
                metadata['material_validation']=MATERIAL_VALIDATION
            os.replace(temporary,target)
        finally:temporary.unlink(missing_ok=True)
        return target,{**metadata,'texture_decoder_revision':TEXTURE_DECODER_REVISION,
                       'material_decoder_revision':MATERIAL_DECODER_REVISION,'texture_overrides':texture_overrides}

    def _prepare_model_textures(self,requests):
        # A name-map pixel format can be stale even when CUE4Parse reports a
        # successful conversion. Decode ETC2 from its actual platform header,
        # replacing old cached PNGs before assembling another face GLB.
        from native_iostore import package_names
        from native_texture import cooked_mip,desktop_etc2_png
        texture_overrides=[]
        for ref in dict.fromkeys(requests):
            raw,_=self.face_assets().get(ref)
            declared=[n for n in package_names(raw) if n.startswith('PF_')]
            if not any(n in ('PF_ETC2_RGB','PF_ETC2_RGBA') for n in declared):continue
            header=struct.unpack_from('<16I',raw)
            if len(declared)==1 and raw.find(declared[0].encode()+b'\0',header[13]+header[14])>=0:continue
            mip=cooked_mip(raw,lambda suffix:self.face_assets().get(ref.removesuffix('.uasset')+suffix)[0])
            if mip['format'] not in ('PF_ETC2_RGB','PF_ETC2_RGBA'):
                raise ValueError('原版纹理名称与实际平台格式不符：'+ref)
            png=self.converted/Path(ref).with_suffix('.png');png.parent.mkdir(parents=True,exist_ok=True)
            png.write_bytes(desktop_etc2_png(mip,self.native))
            texture_overrides.append({'path':ref,'declared':declared,'cooked':mip['format']})
        missing=[v for v in requests if not (self.converted/Path(v).with_suffix('.png')).is_file()]
        if missing:self.run(missing)
        return texture_overrides
