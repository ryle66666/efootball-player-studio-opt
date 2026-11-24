"""Explicit management of generated files and one-time game settings."""
from pathlib import Path
import json,os,subprocess,uuid

DEFAULTS={'enabled':False,'division':10,'points':0,'matches':0,'wins':0,'draws':0,'losses':0,'style_mode':'shuffle_bag'}

def export_folder(store):
    folder=store.root/'exports'
    if folder.is_symlink() or folder.is_junction() or folder.resolve().parent!=store.root.resolve():raise ValueError('导出目录是外部链接，未执行文件操作')
    return folder.resolve()

def normalize_game_options(body):
    value={**DEFAULTS,**body}
    if type(value['enabled']) is not bool:raise ValueError('进度修改开关无效')
    if value['style_mode'] not in ('shuffle_bag','independent'):raise ValueError('AI 风格抽取方式无效')
    for k,low,high in [('division',1,10),('points',0,99999),('matches',0,9999),('wins',0,9999),('draws',0,9999),('losses',0,9999)]:
        if type(value[k]) is not int or not low<=value[k]<=high:raise ValueError(k+' 的数值超出范围')
    if value['wins']+value['draws']+value['losses']!=value['matches']:raise ValueError('胜、平、负之和须等于已赛场次')
    if value['division']>=4 and (value['matches']>9 or value['points']>27):raise ValueError('组别 10–4 当前轮最多已赛 9 场、积分 0–27；第 10 场由游戏结算升降级')
    if value['division']>=4 and value['points']!=3*value['wins']+value['draws']:raise ValueError('组别 10–4 积分须为 胜场×3＋平场')
    return value

def game_options(store,body=None):
    path=store.root/'game-options.json'
    if body is None:return {**DEFAULTS,**json.loads(path.read_text(encoding='utf8'))} if path.is_file() else dict(DEFAULTS)
    value=normalize_game_options(body)
    previous=game_options(store)
    progress_keys=('enabled','division','points','matches','wins','draws','losses')
    value['revision']=previous['revision'] if previous.get('revision') and all(previous.get(k)==value[k] for k in progress_keys) else str(uuid.uuid4())
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8');temp.replace(path)
    return value

def export_file(store,name):
    folder=export_folder(store);name=str(name)
    parts=name.split('/')
    if not 1<=len(parts)<=2 or any(not p or p in('.','..')or'\\'in p or':'in p for p in parts):raise ValueError('导出文件路径无效')
    candidate=folder.joinpath(*parts)
    if candidate.is_symlink() or candidate.is_junction():raise ValueError('导出文件路径无效')
    parent=candidate.parent
    if len(parts)==2:
        if parent.is_symlink()or parent.is_junction()or parent.resolve().parent!=folder or not(parent/'complete.json').is_file():raise ValueError('整套 APK 尚未完成发布或路径无效')
    path=candidate.resolve()
    if path.parent!=parent.resolve()or not path.is_relative_to(folder)or not path.is_file():raise ValueError('导出文件已清除或不存在')
    return path

def export_list(store):
    import storage_usage
    from managed_game_base import delivery_aliases
    folder=export_folder(store);rows=[]
    paths=[]
    for p in folder.iterdir():
        if p.is_symlink()or p.is_junction():continue
        if p.is_file():paths.append(p)
        elif p.is_dir()and(p/'complete.json').is_file():paths.extend(q for q in p.iterdir()if q.is_file()and not q.is_symlink()and not q.is_junction())
    home=getattr(getattr(store,'library',None),'home',None) or Path(store.root).parent
    prot=storage_usage.protected_targets(store,home)
    aliases=delivery_aliases(store)
    inode_sizes={}
    for p in sorted(paths,key=lambda p:p.stat().st_mtime,reverse=True):
        st=p.stat()
        resolved=p.resolve()
        ikey=storage_usage.inode_key(st,resolved)
        inode_sizes.setdefault(ikey,st.st_size)
        is_prot,_=storage_usage._path_is_protected(resolved,ikey,prot)
        if resolved in aliases and resolved not in prot['paths']:
            is_prot,_=storage_usage._path_is_protected(resolved,None,prot)
        name=p.relative_to(folder).as_posix()
        suffix=p.suffix.lower()
        rows.append({'name':name,'bytes':st.st_size,'apk':suffix=='.apk','pack':suffix=='.73pack',
                     'protected':is_prot,'url':'/exports/'+__import__('urllib.parse',fromlist=['quote']).quote(name)})
    from export_delivery import expose
    for row in rows:
        if '/'not in row['name']or Path(row['name']).suffix.lower()not in('.apk','.73pack'):continue
        try:
            delivery=expose(store,{'path':str(folder/row['name'])})
            row['delivery_name']=delivery['name'];row['delivery_url']=delivery['url']
        except (OSError,ValueError):pass
    from compile_cache import summary
    apparent_bytes=sum(r['bytes'] for r in rows)
    actual_bytes=sum(inode_sizes.values())
    return {'rows':rows,'count':len(rows),'bytes':actual_bytes,'actual_bytes':actual_bytes,
            'apparent_bytes':apparent_bytes,'compiler_cache':summary(store.root/'compiler-cache')}

def clear_exports(server):
    import storage_usage
    from managed_game_base import delivery_aliases
    if server.native_package.status()['phase']=='working' or server.game_import.status()['phase']=='working':raise ValueError('请等待当前导出或导入任务完成')
    with server.store.lock:
        rows=export_list(server.store)['rows']
        folder=export_folder(server.store)
        prot=storage_usage.protected_targets(server.store,server.library.home)
        aliases=delivery_aliases(server.store)
        all_paths=[export_file(server.store,row['name'])for row in rows]
        paths=[];kept_inodes=set()
        for path in all_paths:
            st=path.lstat()
            ikey=storage_usage.inode_key(st,path.resolve())
            is_prot,_=storage_usage._path_is_protected(path.resolve(),None if path in aliases else ikey,prot)
            if is_prot:
                kept_inodes.add(ikey)
            else:
                paths.append((path,st.st_size,ikey))
        freed_inodes={}
        for path,size,ikey in paths:
            path.unlink()
            if ikey not in kept_inodes and ikey not in prot['inodes']:
                freed_inodes.setdefault(ikey,size)
        for parent in {path.parent for path,_,_ in paths if path.parent!=folder}:
            if not any(parent.iterdir()):parent.rmdir()
        storage_usage._prune_delivery_index(server.store)
    return {'deleted':len(paths),'bytes':sum(freed_inodes.values()),
            'protected':len(all_paths)-len(paths),'message':'正在被资料库、小包基准或保留资源集引用的文件已保留'}

def clear_compiler_cache(server):
    if server.native_package.status()['phase']=='working' or server.game_import.status()['phase']=='working':raise ValueError('请等待当前导出或导入任务完成')
    from compile_cache import clear
    with server.store.lock:return clear(server.store.root/'compiler-cache')

def open_export(store,name):
    path=export_file(store,name)
    if os.name!='nt':raise ValueError('当前平台请使用保存或直接安装')
    subprocess.Popen(['explorer.exe','/select,'+str(path)])
    return {'opened':True}
