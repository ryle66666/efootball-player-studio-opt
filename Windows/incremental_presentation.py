"""Derived search and artwork catalogues for locally registered content."""
from contextlib import closing
import collections
import copy
import json
from pathlib import Path
import re
import shutil
import sqlite3
import uuid

REVISION=2
STYLE_KIND={'0000':'Standard','0100':'Legendary','0101':'Legendary','0200':'BigTime','0201':'BigTime',
            '0300':'Trending','0301':'Trending','0302':'Trending','0303':'Trending','0400':'Featured',
            '0401':'Featured','0500':'Epic','0501':'BigTime','0600':'Highlight','0601':'Highlight'}
ORDER={'f01':0,'f02':1,'portrait':2,'f04':3,'f03':4,'f05':5,'f06':6,'f07':7,'f10':8,'f09':9}


def design(identifier):
    value=int(identifier);kind=(value>>40)&255
    return f'{kind>>4:02d}{kind&15:02d}',(value>>28)&4095,bool(value&(1<<48))


def names(value):
    if isinstance(value,str):return [value]if value else[]
    if isinstance(value,dict):return [n for v in value.values()for n in names(v)]
    if isinstance(value,list):return [n for v in value for n in names(v)]
    return []


def card_metadata(cid,row,aliases=()):
    from card_types import editor_card_type
    origin=row.get('origin')or{};payload=copy.deepcopy(row['definition'])
    label=origin.get('variation')or payload.get('_studio_card_label','')
    kind=editor_card_type(STYLE_KIND.get(design(cid)[0],origin.get('card_type','UNKNOWN')),label)
    if label:payload['_studio_card_label']=label
    values=list(dict.fromkeys(names([payload.get('_studio_names'),row.get('name'),row.get('name_en'),list(aliases)])))
    payload['_studio_names']=values
    search=' '.join([cid,*values,label]).casefold()
    return payload,kind,search


def stage(resources,store,index,changes,database):
    """Stage derived files together with the database and its import receipt."""
    from incremental_delta import Delta
    from content_delta import compact,project_levels
    from content_delta_v2 import semantic_payload
    resources=Path(resources);previous=index.get('presentation')or{}
    media={};suites={};accepted={}
    def loaded(path,default):
        value=changes.get(path)
        if value is not None:return json.loads(value.read_bytes()if isinstance(value,Path)else value)
        return json.loads(path.read_text('utf8'))if path.is_file()else default
    with closing(sqlite3.connect(database))as db,db:
        people={}
        for cid,base,name,english,payload in db.execute('select id,base_id,name,name_en,payload from players'):
            key=str(base or(int(cid)&0xfffffff))
            people.setdefault(key,[]).extend([name,english,*names(json.loads(payload).get('_studio_names'))])
        for item in index['packages']:
            path=Path(item['path']);folder=path.parent
            try:
                with Delta(changes.get(path,path))as delta:
                    document=delta.json('data/players.json')
                    players={str(row['card_id']):copy.deepcopy(row)for row in document['players']}
                    progression=loaded(resources/'native-progression.json',{})
                    project_levels(players,document.get('level_info_projection',[]),progression.get('costs',[]))
                    for row in players.values():
                        cid=str(row['card_id']);status=item['receipt']['items'].get('player_card:'+cid,{})
                        if status.get('status')in('preserved_user','skipped_conflict'):continue
                        current=db.execute('select payload from players where id=?',(cid,)).fetchone()
                        if not current:continue
                        current_payload=json.loads(current[0])
                        if semantic_payload(current_payload)!=semantic_payload(row['definition']):continue
                        payload,kind,search=card_metadata(cid,dict(row,definition=current_payload),people.get(str(row.get('base_id')or(int(cid)&0xfffffff)),[]))
                        db.execute('update players set payload=?,card_type=?,search=? where id=?',(compact(payload).decode(),kind,search,cid))
                        accepted[cid]={'card_type':kind,'label':payload.get('_studio_card_label','')}
                    for suite in delta.json('data/card-suites.json').get('full_target_catalogue',[]):
                        kind=int(suite['nativeType'],16);style=f'{kind>>4:02d}{kind&15:02d}'
                        suites[f"{style}/{int(suite['variant'])}"]=suite
                    for row in delta.json('assets/card-art-files.json')['files']:
                        source=row['source_path'];relative=Path(source).relative_to('assets/card-designer')
                        if len(relative.parts)!=2 or relative.parts[0]not in('backgrounds','background-videos')or not re.fullmatch(r'[A-Za-z0-9_.-]+\.(png|jpg|webm|mp4)',relative.name):
                            raise ValueError('卡面素材路径无效')
                        physical=folder/'media'/relative
                        if physical in changes:
                            data=changes[physical];raw=data.read_bytes()if isinstance(data,Path)else data
                        elif physical.is_file():raw=physical.read_bytes()
                        else:raw=delta.blob(row['payload']);changes[physical]=raw
                        old=previous.get('media',{}).get(relative.stem,{})
                        asset=old.get('asset_id','');target=store.root/'assets'/asset
                        if not re.fullmatch(r'[a-f0-9]{64}\.(png|jpg|webm|mp4)',asset)or not target.is_file()or target.read_bytes()!=raw:
                            asset=uuid.uuid4().hex+uuid.uuid4().hex+relative.suffix
                            changes[store.root/'assets'/asset]=raw
                        media[relative.stem]={'name':relative.stem,'path':'content-delta/'+delta.manifest['package_id']+'/'+relative.as_posix(),
                            'asset_id':asset,'url':'/assets/'+asset,'kind':'video'if relative.suffix in('.webm','.mp4')else'image'}
            except (OSError,ValueError,KeyError)as exc:
                raise ValueError('无法更新已安装增量的卡面索引：'+path.name+'：'+str(exc))from exc
        row=db.execute("select value from meta where key='summary'").fetchone()
        if row:
            summary=json.loads(row[0]);summary['by_card_type']=dict(db.execute('select card_type,count(*) from players group by card_type'))
            db.execute("update meta set value=?where key='summary'",(compact(summary).decode(),))
        assets={row[0]:dict(zip(('id','path','name'),row[1:]))for row in db.execute('select name,id,path,name from assets where kind in("portrait","card")')}
    card_media=loaded(resources/'native-card-media.json',{'movies':[]})
    movies={Path(p).stem:p for p in card_media.get('movies',[])if not p.startswith('content-delta/')}
    card_media['movies']=list(movies.values())
    card_media['previews']=[r['path']for r in media.values()if r['kind']=='video']
    changes[resources/'native-card-media.json']=compact(card_media)
    presets=loaded(resources/'native-card-presets.json',{'rows':[],'counts':{}})
    rows={r['id']:r for r in presets['rows']if not r.get('content_delta_design')}
    signature=lambda row:tuple((r['slot'],r['kind'],r['path'])for r in row['layers'])
    seen={signature(row)for row in rows.values()}
    for key,suite in suites.items():
        style,agent=key.split('/');agent=int(agent)
        layers=design_layers(style,agent,False,media,assets,movies)
        if not layers or tuple((r['slot'],r['kind'],r['path'])for r in layers)in seen:continue
        identifier=f'incremental-{style}-{agent}'
        rows[identifier]={'id':identifier,'card_type':STYLE_KIND.get(style,'UNKNOWN'),'native_style':style,
            'native_agent':agent,'limited':False,'title':suite.get('donorVariation')or suite['id'],
            'design_code':f'{style} / {agent}','source_id':str(suite.get('donorPlayerId')or'7511'),
            'sample_date':'','layers':layers,'examples':names(suite.get('donorName')),'labels':names(suite.get('donorVariation')),
            'source_count':1,'content_delta_design':True}
        seen.add(signature(rows[identifier]))
    presets['rows']=list(rows.values());presets['counts']=dict(collections.Counter(r['card_type']for r in rows.values()))
    changes[resources/'native-card-presets.json']=compact(presets)
    index['presentation']={'revision':REVISION,'media':media,'suites':suites,'players':accepted}
    index['presentation_revision']=REVISION
    return {'players':len(accepted),'media':len(media),'presets':sum(bool(r.get('content_delta_design'))for r in rows.values())}


def design_layers(style,agent,limited,media,assets=None,movies=None):
    layers=[];assets=assets or{};movies=movies or{}
    for slot in('f01','f02','f03','f04','f05','f07','f10'):
        candidates=([f'{style}_{agent}_{slot}_lim',f'{style}_{slot}_lim']if limited else[])+[f'{style}_{agent}_{slot}',f'{style}_{slot}']
        layer=None
        for name in candidates:
            video=media.get(name+'_v')or({'path':movies[name+'_v'],'kind':'video','name':name+'_v'}if name+'_v'in movies else None)
            image=media.get(name)or(dict(assets[name],kind='image')if name in assets else None)
            layer=video or image
            if layer:break
        if layer:layers.append(dict(layer,slot=slot,blend='screen'if slot in('f03','f05','f07','f10')else'source-over'))
    return layers


def load(library):
    path=library.resources/'incremental-content.json'
    value=json.loads(path.read_text('utf8')).get('presentation',{})if path.is_file()else{}
    library.content_media=value.get('media',{});library.content_suites=value.get('suites',{})
    library.content_players=value.get('players',{})


def repair_registered(resources,store,force=False):
    from incremental_delta import INDEX,_commit
    from content_delta import compact
    resources=Path(resources);path=resources/INDEX
    if not path.is_file():return None
    index=json.loads(path.read_text('utf8'))
    if not force and index.get('presentation_revision')==REVISION:return None
    work=store.root/'tmp'/('incremental-'+uuid.uuid4().hex);work.mkdir(parents=True)
    try:
        database=work/'library.sqlite3'
        with closing(sqlite3.connect(resources/'library.sqlite3'))as source,closing(sqlite3.connect(database))as dest:source.backup(dest)
        changes={resources/'library.sqlite3':database}
        report=stage(resources,store,index,changes,database);changes[path]=compact(index)
        _commit(store,work,changes,[resources,store.root/'content-deltas',store.root/'assets'])
        return report
    finally:
        if work.exists()and not(store.root/'incremental-import.json').exists():shutil.rmtree(work)


def repair_project(project,library):
    card=project.get('native_card')or{};cid=str(card.get('source_id',''))
    metadata=getattr(library,'content_players',{}).get(cid)
    appearance=project.get('appearance',{});art=project.get('native_art',{})
    if not metadata or card.get('preset_id')or card.get('custom_motion')or appearance.get('card_background_override')or appearance.get('background_image')or art.get('background')or art.get('full'):return project
    direct=design_layers(card.get('native_style',''),card.get('native_agent',0),False,getattr(library,'content_media',{}))
    if not direct:return project
    updated=copy.deepcopy(project);target=updated['native_card'];old_kind=target.get('card_type')
    present={row['slot']for row in target.get('layers',[])}
    for layer in direct:
        if layer['slot']not in present:target.setdefault('layers',[]).append(layer)
    target['layers'].sort(key=lambda row:ORDER.get(row.get('slot'),100))
    target['card_type']=metadata['card_type'];target['source_label']=metadata.get('label','')
    if updated.get('card_type')==old_kind:updated['card_type']=metadata['card_type']
    if updated.get('card_series')==old_kind:updated['card_series']=metadata['card_type']
    fixed={row['slot']for row in direct}
    target['missing']=[row for row in target.get('missing',[])if not any(slot in row.get('path','')for slot in fixed)]
    target['content_presentation_revision']=REVISION
    return updated
