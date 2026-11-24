"""Import declarative content into the active library without rebuilding an APK."""
from contextlib import closing
import collections
import copy
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time
import uuid
from archive_source import Archive
from content_pack import atomic_json,streams_equal
from content_delta import safe_path,compact,project_levels

INDEX='incremental-content.json'
JOURNAL='incremental-import.json'


def asset_identity(virtual):
    if not virtual.lower().endswith('.uasset'):return None
    name=Path(virtual).stem;lower=virtual.lower()
    if '/character/'in lower and name.startswith('SK_'):
        match=re.match(r'SK_(\d+)',name)
        return name,'model',match.group(1)if match else''
    if '/thumbnail/player/'in lower or '/realface/'in lower:
        kind='portrait'if re.fullmatch(r'\d+_(?:l)?',name)else'card'
        match=re.match(r'(\d+)',name)if kind=='portrait'or re.fullmatch(r'\d+f',name)else re.search(r'(\d{6,})',name)
        return name,kind,match.group(1)if match else''
    return None


def register_asset(db,virtual):
    identity=asset_identity(virtual)
    if identity is None:return 0
    if db.execute('select 1 from assets where path=?',(virtual,)).fetchone():
        return db.execute('update assets set name=?,kind=?,player_key=? where path=? and '
            '(name IS NOT ? OR kind IS NOT ? OR player_key IS NOT ?)',(*identity,virtual,*identity)).rowcount
    db.execute('insert into assets values(?,?,?,?,?)',(uuid.uuid4().hex[:24],virtual,*identity))
    return 1


def repair_asset_rows(db):
    rows=db.execute("select id,path,name,kind,player_key from assets where name GLOB 'SK_[0-9]*'").fetchall()
    count=0
    for asset_id,path,name,kind,player_key in rows:
        identity=asset_identity(path)
        if identity is None or identity[1]!='model'or (name,kind,player_key)==identity:continue
        db.execute('update assets set name=?,kind=?,player_key=? where id=?',(*identity,asset_id));count+=1
    if count and db.execute("select 1 from sqlite_master where type='table' and name='meta'").fetchone():
        row=db.execute("select value from meta where key='summary'").fetchone()
        if row:
            summary=json.loads(row[0]);summary['by_asset_kind']=dict(db.execute('select kind,count(*) from assets group by kind'))
            summary['assets']=sum(summary['by_asset_kind'].values())
            db.execute("update meta set value=? where key='summary'",(json.dumps(summary,ensure_ascii=False),))
    return count


def repair_asset_index(resources):
    database=Path(resources)/'library.sqlite3'
    if not database.is_file():return 0
    with closing(sqlite3.connect(database))as db,db:
        return repair_asset_rows(db)


class Delta:
    def __init__(self,path):
        self.path=Path(path).resolve();self.archive=Archive(self.path)
        names=self.archive.namelist()
        if len(names)>30000 or len({n.casefold()for n in names})!=len(names):raise ValueError('增量包文件数量或名称无效')
        self.manifest=json.loads(self.archive.read('manifest.json',limit=16*1024*1024))
        m=self.manifest
        if m.get('format')!='efootball-workshop-content-delta'or m.get('schema_version')!=2 or m.get('base_version')!='V17'or m.get('runtime_abi')!='v17-content-art/1':raise ValueError('当前支持 V17 内容增量 v2')
        from content_delta_v2 import CAPABILITIES
        if set(m.get('required_capabilities',[]))-CAPABILITIES:raise ValueError('增量包需要尚未支持的内容能力')
        if m.get('compatibility',{}).get('engine_family')!='efootball-v17-arm64':raise ValueError('内容包游戏接口族不同')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,159}',m.get('package_id','')):raise ValueError('增量包编号无效')
        if any(m.get('import_policy',{}).get(k)is not False for k in('delete_originals','modify_gameplay','ownership_grants')):raise ValueError('增量包只能登记内容')
        self.entries={}
        for row in m.get('entries',[]):
            name=safe_path(row['path']);count=row['bytes']
            if name in self.entries or type(count)is not int or not 0<=count<=512*1024*1024:raise ValueError('增量文件清单无效')
            if self.archive.getinfo(name).file_size!=count:raise ValueError('增量文件长度不符：'+name)
            self.entries[name]=row
        if not self.entries or set(names)!=set(self.entries)|{'manifest.json'}or sum(r['bytes']for r in self.entries.values())>2*1024**3:raise ValueError('增量文件清单与归档内容不一致')
    def __enter__(self):return self
    def __exit__(self,*arguments):self.archive.close()
    def data(self,name):
        if name not in self.entries:raise ValueError('增量包引用未登记文件：'+name)
        return self.archive.read(name,limit=None)
    def json(self,name):
        if self.entries.get(name,{}).get('bytes',0)>32*1024*1024:raise ValueError('增量资料 JSON 大小超限')
        return json.loads(self.data(name))
    def blob(self,reference):
        name=safe_path(reference['path']);entry=self.entries.get(name)
        if entry is None or entry['bytes']!=reference.get('bytes'):raise ValueError('增量资源引用长度不一致')
        return self.data(name)
    def summary(self):
        return {'package_id':self.manifest['package_id'],'base_version':'V17','schema_version':2,
                'counts':self.manifest.get('counts',{}),'bytes':self.path.stat().st_size,
                'verified_files':len(self.entries),'verification':'structure-and-length'}


def recover(store):
    journal=store.root/JOURNAL
    if not journal.is_file():return
    state=json.loads(journal.read_text('utf8'));work=Path(state['work']).resolve()
    if work.parent!=(store.root/'tmp').resolve()or not work.name.startswith('incremental-'):raise ValueError('增量恢复目录无效')
    roots=[Path(p).resolve()for p in state['roots']]
    for row in state['files']if state['phase']!='committed'else[]:
        target=Path(row['path']).resolve()
        if not any(target.is_relative_to(root)for root in roots):raise ValueError('增量恢复路径越界')
        backup=work/'previous'/row['backup']
        if row['existed']:
            if not backup.is_file():raise ValueError('增量恢复副本缺失')
            shutil.copyfile(backup,target)
        else:target.unlink(missing_ok=True)
    journal.unlink()
    if work.is_dir():shutil.rmtree(work)


def _commit(store,work,changes,roots):
    recover(store);rows=[]
    for index,(target,value)in enumerate(changes.items()):
        target=Path(target).resolve()
        if target.is_symlink()or not any(target.is_relative_to(root.resolve())for root in roots):raise ValueError('增量发布路径无效')
        incoming=work/'incoming'/str(index);incoming.parent.mkdir(parents=True,exist_ok=True)
        if isinstance(value,Path):shutil.copyfile(value,incoming)
        else:incoming.write_bytes(value)
        backup=work/'previous'/str(index);backup.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():shutil.copyfile(target,backup)
        rows.append({'path':str(target),'backup':str(index),'existed':target.exists()})
    state={'phase':'prepared','work':str(work),'roots':[str(p.resolve())for p in roots],'files':rows}
    atomic_json(store.root/JOURNAL,state)
    try:
        for index,row in enumerate(rows):
            target=Path(row['path']);target.parent.mkdir(parents=True,exist_ok=True);os.replace(work/'incoming'/str(index),target)
        state['phase']='committed';atomic_json(store.root/JOURNAL,state);recover(store)
    except BaseException:recover(store);raise


def import_delta(library,store,path,progress=lambda **kw:None):
    started=time.perf_counter();resources=library.resources;work=store.root/'tmp'/('incremental-'+uuid.uuid4().hex)
    work.mkdir(parents=True);committed=False
    try:
        with Delta(path)as delta:
            index_path=resources/INDEX
            index=json.loads(index_path.read_text('utf8'))if index_path.is_file()else{'packages':[],'chunks':{},'containers':[]}
            prior=next((p for p in index['packages']if p['manifest']['package_id']==delta.manifest['package_id']),None)
            if prior:
                if prior['manifest']!=delta.manifest:raise ValueError('相同增量包编号包含不同清单')
                if not prior['receipt'].get('pending_entities'):
                    from incremental_presentation import repair_registered,load
                    with store.lock:
                        repaired=repair_asset_index(resources);presentation=repair_registered(resources,store,force=True)
                    load(library);refresh_presentation(library,store,prepare_covers=True)
                    library._identity_index_cache=None;library._face_assets=None
                    if hasattr(library,'_decoder'):library._decoder=None
                    receipt=copy.deepcopy(prior['receipt']);receipt['counts_result']={'reused':len(receipt['items'])}
                    return {'already_installed':True,'content_delta':receipt,'repaired_assets':repaired,'presentation':presentation,
                            'changed_records':0,'timings':{'total_seconds':time.perf_counter()-started}}
            if library.profile.get('game_version')not in('V17','V17.1'):raise ValueError('此内容包需要 V17 或 V17.1 资料库')
            player_rows=delta.json('data/players.json')['players'];players={str(r['card_id']):copy.deepcopy(r)for r in player_rows}
            if len(players)!=len(player_rows)or len(players)!=delta.manifest['counts']['new_player_cards']:raise ValueError('新增卡片编号或数量无效')
            progression=json.loads((resources/'native-progression.json').read_text('utf8'))
            project_levels(players,delta.json('data/players.json').get('level_info_projection',[]),progression['costs'])
            from content_delta_v2 import art_routes,semantic_payload
            routes=art_routes(delta,players);route_by={str(r['cardId']):r for r in routes}
            catalog=json.loads((resources/'catalog.json').read_text('utf8'));changes={};statuses={};counts=collections.Counter()
            database=work/'library.sqlite3'
            with closing(sqlite3.connect(resources/'library.sqlite3'))as source,closing(sqlite3.connect(database))as db:
                source.backup(db)
                with db:
                    for cid,row in players.items():
                        definition=row['definition'];identity=definition.get('gameplayer_id',{})
                        if str(identity.get('id'))!=cid or identity.get('serial')!=0 or len(definition.get('parameters',[]))!=147 or row.get('default_import_action')!='register_catalogue_only':raise ValueError('增量卡片定义无效：'+cid)
                        current=db.execute('select payload from players where id=?',(cid,)).fetchone()
                        state='reused'if current and semantic_payload(json.loads(current[0]))==semantic_payload(definition)else'preserved_user'if current else'added'
                        statuses['player_card:'+cid]={'name':row.get('name',cid),'status':state,'reasons':['当前卡片已存在不同内容']if state=='preserved_user'else[]};counts[state]+=1
                        if state!='added':continue
                        name=row.get('name')or cid;english=row.get('name_en')or name;origin=row.get('origin',{});position=definition['parameters'][0]
                        if type(position)is not int or not 0<=position<len(catalog['POSITIONS']):raise ValueError('新增卡片位置无效')
                        cap=definition.get('level_info',{}).get('level_limit')
                        from incremental_presentation import card_metadata
                        definition,kind,search=card_metadata(cid,row)
                        db.execute('insert into players values(?,?,?,?,?,?,?,?,?,?,?)',(cid,name,english,search,catalog['POSITIONS'][position],kind,definition.get('_overall'),cap,str(route_by.get(cid,{}).get('modelNativeId',row.get('base_id',int(cid)&0xfffffff))),compact(definition).decode(),'内容增量'))
                    for route in routes:
                        cid=str(route['cardId'])
                        if statuses['player_card:'+cid]['status']=='preserved_user':continue
                        for suffix in('_','_l'):db.execute('insert or ignore into card_aliases values(?,?)',(cid+suffix,route['portraitKey']+'_'))
                    summary=json.loads(db.execute('select value from meta where key="summary"').fetchone()[0]);summary['players']=db.execute('select count(*)from players').fetchone()[0]
                    db.execute('update meta set value=?where key="summary"',(json.dumps(summary,ensure_ascii=False),))
                if db.execute('pragma quick_check').fetchone()[0]!='ok':raise ValueError('增量资料库检查未通过')
            changes[resources/'library.sqlite3']=database
            appearance=resources/'native-appearance.bin'
            if appearance.is_file():
                import struct
                raw=appearance.read_bytes()
                if len(raw)%16:raise ValueError('当前外观索引结构无效')
                current={struct.unpack_from('<Q',raw,at)[0]:raw[at:at+16]for at in range(0,len(raw),16)}
                additions={}
                for container in delta.json('native/cpk-deltas.json')['containers']:
                    for table in container['tables']:
                        if table['path']!='common/etc/appearance/PlayerAppearance.bin':continue
                        if(table.get('stride'),table.get('key_offset'),table.get('key_bytes'))!=(64,0,8):raise ValueError('增量外观表布局不同')
                        for row in table.get('added',[]):
                            key=int(row['key']);record=delta.blob(row['record'])
                            if len(record)!=64 or int.from_bytes(record[:8],'little')!=key:raise ValueError('增量外观记录无效')
                            if key not in current:additions[key]=record
                if additions:
                    from native_appearance import compact_table
                    packed=compact_table(additions)
                    for at in range(0,len(packed),16):current[struct.unpack_from('<Q',packed,at)[0]]=packed[at:at+16]
                    changes[appearance]=b''.join(current[key]for key in sorted(current))
            progress(message='保存新增资料与美术文件，复用已准备的资源库')
            destination=Path(prior['path']).parent if prior else store.root/'content-deltas'/uuid.uuid4().hex
            destination.mkdir(parents=True,exist_ok=True)
            if (destination/'content.zip').resolve()!=delta.path:changes[destination/'content.zip']=delta.path
            native=delta.json('native/iostore-delta.json')
            metadata={str(row['id']):row for row in native['package_store_entries']}
            chunk_updates={}
            original_assets=library.face_assets()if callable(getattr(library,'face_assets',None))else None
            from native_iostore import package_names
            for number,row in enumerate(native['chunks']):
                raw=delta.blob(row['payload']);cid=bytes.fromhex(row['chunk_id_hex'])
                if len(cid)!=12 or cid[11]!=row['type']or int.from_bytes(cid[:8],'little')!=int(row['package_id']):raise ValueError('美术资源编号无效')
                physical=destination/('chunk-'+str(number));changes[physical]=raw
                paths=list(row['virtual_paths'])
                if cid[11]==2:
                    import struct
                    names=package_names(raw);own_index=struct.unpack_from('<I',raw)[0]&0x3fffffff
                    if own_index>=len(names):raise ValueError('美术资源主包名称索引无效')
                    own=names[own_index]
                    if own.startswith('/Game/'):paths.append('../../../PesMobile/Content/'+own[6:]+'.uasset')
                for virtual in paths:
                    name=safe_path(virtual.removeprefix('../../../'))
                    item={'file':str(physical),'package':metadata.get(str(row['package_id'])),'chunk':row['chunk_id_hex']}
                    if original_assets is not None and '../../../'+name in original_assets.files:
                        if original_assets.get(name)[0]!=raw:raise ValueError('当前美术资源已有不同内容：'+name)
                    if name in chunk_updates and changes[Path(chunk_updates[name]['file'])]!=raw:
                        raise ValueError('增量包内的美术路径存在冲突：'+name)
                    if name in index['chunks']:
                        if Path(index['chunks'][name]['file']).read_bytes()!=raw:raise ValueError('美术资源已有不同内容：'+name)
                    else:chunk_updates[name]=item
            index['chunks'].update(chunk_updates)
            with closing(sqlite3.connect(database))as db,db:
                repaired=repair_asset_rows(db)
                for virtual in chunk_updates:
                    repaired+=register_asset(db,virtual)
                summary['assets']=db.execute('select count(*)from assets').fetchone()[0]
                summary['by_asset_kind']=dict(db.execute('select kind,count(*) from assets group by kind'))
                db.execute('update meta set value=?where key="summary"',(json.dumps(summary,ensure_ascii=False),))
            for row in delta.json('native/additional-containers.json')['files']:
                name=row['apk_path']
                if not re.fullmatch(r'assets/gateway-release/livedata/pc[0-9]{4,5}_mobile_and\.(pak|utoc|ucas)',name):raise ValueError('附加资源容器路径无效')
                target=destination/Path(name).name;changes[target]=delta.blob(row['payload'])
                if target.suffix=='.utoc'and str(target)not in index['containers']:index['containers'].append(str(target))
            for row in delta.json('assets/card-art-files.json')['files']:
                name=safe_path(row['source_path'])
                if not re.fullmatch(r'assets/card-designer/(backgrounds|background-videos)/[A-Za-z0-9_.-]+\.(png|webm|mp4|jpg)',name):raise ValueError('卡面素材路径无效')
                changes[destination/'media'/Path(name).relative_to('assets/card-designer')]=delta.blob(row['payload'])
            receipt={**delta.summary(),'items':statuses,'counts_result':dict(counts),'pending_entities':[k for k,v in statuses.items()if v['status']=='preserved_user']}
            index['packages']=[p for p in index['packages']if p['manifest']['package_id']!=delta.manifest['package_id']]
            index['packages'].append({'path':str(destination/'content.zip'),'manifest':delta.manifest,'receipt':receipt})
            from incremental_presentation import stage,load
            presentation=stage(resources,store,index,changes,database)
            changes[index_path]=compact(index)
            with store.lock:_commit(store,work,changes,[resources,store.root/'content-deltas',store.root/'assets'])
            committed=True;library._face_assets=None;library._identity_index_cache=None
            if hasattr(library,'_decoder'):library._decoder=None
            library.content_routes.update({str(r['cardId']):r for r in routes if statuses['player_card:'+str(r['cardId'])]['status']!='preserved_user'})
            load(library);refresh_presentation(library,store,prepare_covers=True)
            return {'content_delta':receipt,'counts':{'players':summary['players'],'assets':len(chunk_updates)},
                    'release_id':library.profile['release_id'],'timings':{'total_seconds':time.perf_counter()-started},
                    'apk_rebuilt':False,'library_reprepared':False,'changed_records':counts['added'],'repaired_assets':repaired,'presentation':presentation}
    finally:
        if work.exists()and not(store.root/JOURNAL).exists()and work.resolve().parent==(store.root/'tmp').resolve():shutil.rmtree(work)


def refresh_presentation(library,store,*,prepare_covers=False):
    library._native_movie_sources=None
    if hasattr(library,'card_media'):
        from native_presets import NativePresets
        library.card_media=json.loads((library.resources/'native-card-media.json').read_text('utf8'))
        library.presets=NativePresets(library)
    if hasattr(store,'project')and callable(getattr(store,'save',None)):
        from incremental_presentation import repair_project
        updated=repair_project(store.project,library)
        if updated!=store.project:store.save(updated,True)
    covers=getattr(library,'video_covers',None)
    if covers is not None and library.state['phase']=='ready':
        if prepare_covers:
            covers.prepare()
        else:
            covers.schedule()


def overlay_assets(assets,resources):
    path=Path(resources)/INDEX
    if not path.is_file():return assets
    index=json.loads(path.read_text('utf8'))
    from native_iostore import Container
    key=(assets.containers[0].path.parent/'resource-key.bin').read_bytes()if assets.containers else None
    for name in index['containers']:
        container=Container(Path(name),key);assets.containers.append(container)
        for virtual,number in container.paths.items():assets.files[virtual]=(container,number)
    original_get=assets.get;original_face=assets.face
    original_raw=getattr(assets,'raw',None)
    original_paths=getattr(assets,'raw_paths',None)
    def get(path):
        if path.startswith('/Game/'):path='PesMobile/Content/'+path[6:]+'.uasset'
        name=path.removeprefix('../../../');row=index['chunks'].get(name)
        if row:return Path(row['file']).read_bytes(),row['package']
        return original_get(path)
    def face(path):
        name=path.removeprefix('../../../');row=index['chunks'].get(name)
        if row:
            from native_skeletal import face_details
            raw,metadata=get(path);return raw,metadata,dict(face_details(raw),container='incremental-content')
        return original_face(path)
    assets.get=get;assets.face=face
    if original_raw is not None:
        def raw(path):
            name=path.removeprefix('../../../')
            if name.startswith('/Game/'):name='PesMobile/Content/'+name[6:]+(''if name.endswith(('.uasset','.ubulk','.uptnl'))else'.uasset')
            row=index['chunks'].get(name)
            return Path(row['file']).read_bytes()if row else original_raw(path)
        assets.raw=raw
    if original_paths is not None:
        assets.raw_paths=lambda:list(dict.fromkeys([*original_paths(),*index['chunks']]))
    return assets


def decode_paths(library,paths,mode):
    index_path=library.resources/INDEX
    if not index_path.is_file():return list(paths)
    chunks=json.loads(index_path.read_text('utf8'))['chunks'];remaining=[]
    assets=library.face_assets();references={}
    for path in paths:
        name=path.removeprefix('../../../')
        if name not in chunks:remaining.append(path);continue
        target=library.converted/Path(name);target.parent.mkdir(parents=True,exist_ok=True)
        raw,_=assets.get(name)
        if mode=='raw':target.write_bytes(raw);continue
        if mode=='json':
            from native_materials import material_json
            document=material_json(raw,lambda ref:assets.get(ref)[0],references)
            target.with_suffix('.json').write_text(json.dumps(document,ensure_ascii=False,indent=2),'utf8')
            continue
        from native_texture import cooked_mip,desktop_etc2_png
        mip=cooked_mip(raw,lambda suffix:assets.get(name.removesuffix('.uasset')+suffix)[0])
        if mip['format']in('PF_ETC2_RGB','PF_ETC2_RGBA'):image=desktop_etc2_png(mip,library.native)
        elif mip['format']in('PF_B8G8R8A8','PF_R8G8B8A8'):
            import io
            from PIL import Image
            output=io.BytesIO();Image.frombytes('RGBA',(mip['width'],mip['height']),mip['data'],'raw','BGRA'if mip['format']=='PF_B8G8R8A8'else'RGBA').save(output,format='PNG');image=output.getvalue()
        else:raise ValueError('增量纹理格式尚未支持：'+mip['format'])
        target.with_suffix('.png').write_bytes(image)
    return remaining


def materialize(archive,library,work,progress=lambda **kw:None):
    """Merge registered native records once, during game export."""
    path=library.resources/INDEX
    if not path.is_file():return archive,{},None
    from content_delta import Delta as ExportDelta,read_catalogue
    from content_delta_v2 import build_source
    from content_autofill import Overlay
    index=json.loads(path.read_text('utf8'));replacements={};reports=[]
    current=archive
    for number,item in enumerate(index['packages']):
        existing=next((r for r in read_catalogue(current)['packages']if r['package_id']==item['manifest']['package_id']),None)
        if existing and not existing.get('pending_entities'):continue
        folder=Path(work)/('registered-content-'+str(number));folder.mkdir()
        def finish(source,changes,receipt):
            replacements.update(changes);reports.append(receipt)
            return Overlay(source,changes)
        with ExportDelta(item['path'])as delta:
            current=build_source(delta,archive.filename,None,folder,library.profile,progress,
                source_reader=current,finalizer=finish)
    return current,replacements,{'source':'registered-incremental-content','packages':reports,
                                 'apk_rebuilt':False,'library_reprepared':False}
