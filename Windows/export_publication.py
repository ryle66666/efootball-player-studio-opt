"""Recoverable publication of generated APK sets and their small registries."""
from pathlib import Path
import hashlib,json,os,re,uuid

JOURNAL='export-publication.json'
ALLOWED={'native-player-registry.json','native-booster-registry.json','native-live-versions.json',
         'native-asset-registry.json','native-coach-registry.json','resource-set-exports.json'}


def _hash(raw):return hashlib.sha256(raw).hexdigest()
def _write(path,raw):
    with path.open('xb')as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
def _json(path,value):
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:_write(temporary,json.dumps(value,sort_keys=True,separators=(',',':')).encode());os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def _read(store):
    root=store.root.resolve();path=root/JOURNAL
    if not path.exists():return None
    if path.is_symlink()or path.is_junction()or path.stat().st_size>1024*1024:raise ValueError('导出事务日志路径或体积无效')
    value=json.loads(path.read_text('utf8'));nonce=value.get('nonce','')
    if value.get('version')!=1 or not re.fullmatch('[a-f0-9]{32}',nonce)or value.get('state')not in('pending','committed'):raise ValueError('导出事务日志版本无效')
    if not isinstance(value.get('files'),list)or not 1<=len(value['files'])<=len(ALLOWED)or len({r.get('name')for r in value['files']})!=len(value['files']):raise ValueError('导出事务注册表列表无效')
    generated=value.get('generated_files')
    if not isinstance(generated,list)or not 2<=len(generated)<=132 or any(not isinstance(n,str)or Path(n).name!=n or'\\'in n or':'in n for n in generated):raise ValueError('导出事务成品列表无效')
    if value.get('game_name')not in generated or not re.fullmatch('(?:[a-f0-9]{32}|[a-f0-9]{64})',value.get('set_id','')):raise ValueError('导出事务成品身份无效')
    for row in value.get('files',[]):
        if row.get('name')not in ALLOWED:raise ValueError('导出事务包含非注册表文件')
        for suffix,key in (('old','old'),('new','new')):
            if row.get(key)!='.resource-publish-'+nonce+'-'+row['name']+'.'+suffix:raise ValueError('导出事务临时文件身份无效')
        for name in (row['name'],row['old'],row['new']):
            target=root/name
            if target.is_symlink()or target.is_junction()or target.resolve().parent!=root:raise ValueError('导出事务注册表路径越界')
    destination=value.get('destination','')
    if Path(destination).name!=destination or not destination.startswith('APK-资源集-'):raise ValueError('导出事务目标目录无效')
    target=root/'exports'/destination
    if target.is_symlink()or target.is_junction()or target.resolve().parent!=(root/'exports').resolve():raise ValueError('导出事务目标目录越界')
    return value


def begin(store,updates,*,destination,game_name,set_id,generated_files):
    if _read(store) is not None:raise ValueError('存在尚未完成的导出事务，须先恢复')
    root=store.root.resolve();nonce=uuid.uuid4().hex;rows=[];created=[]
    try:
        for path,raw in updates.items():
            path=Path(path);raw=bytes(raw)
            if path.name not in ALLOWED or path.resolve().parent!=root or path.is_symlink()or path.is_junction():raise ValueError('导出事务注册表不在允许范围')
            old=root/('.resource-publish-'+nonce+'-'+path.name+'.old');new=old.with_suffix('.new')
            previous=path.read_bytes()if path.exists()else None
            if previous is not None:_write(old,previous);created.append(old)
            _write(new,raw);created.append(new)
            rows.append(dict(name=path.name,old=old.name,new=new.name,existed=previous is not None,
                             old_sha256=_hash(previous)if previous is not None else None,new_sha256=_hash(raw)))
        value=dict(version=1,nonce=nonce,state='pending',files=rows,destination=Path(destination).name,game_name=game_name,set_id=set_id,generated_files=sorted(generated_files))
        _json(root/JOURNAL,value)
        # Validate every final absolute path before any publication/mutation.
        _read(store);return value
    except BaseException:
        if not(root/JOURNAL).exists():
            for path in created:path.unlink(missing_ok=True)
        raise


def apply(store):
    value=_read(store)
    if value is None or value['state']!='pending':raise ValueError('没有可提交的导出事务')
    for row in value['files']:
        new=store.root/row['new']
        if _hash(new.read_bytes())!=row['new_sha256']:raise ValueError('导出事务待写元数据校验失败')
    for row in value['files']:os.replace(store.root/row['new'],store.root/row['name'])


def _cleanup(store,value):
    for row in value['files']:
        for key in('old','new'):(store.root/row[key]).unlink(missing_ok=True)
    (store.root/JOURNAL).unlink(missing_ok=True)


def finish(store):
    value=_read(store)
    if value is None:return
    if value['state']=='pending':
        for row in value['files']:
            if _hash((store.root/row['name']).read_bytes())!=row['new_sha256']:raise ValueError('导出事务尚未完整应用注册表')
        value['state']='committed';_json(store.root/JOURNAL,value)
    try:_cleanup(store,value)
    except OSError:return False # committed; startup recovery can finish cleanup
    return True


def rollback(store,*,preserve_files=False):
    value=_read(store)
    if value is None:return
    if value['state']=='committed':_cleanup(store,value);return
    # Validate all old files first; rollback uses prepared files and needs no
    # fresh disk allocation even when the original failure was a full disk.
    for row in value['files']:
        old=store.root/row['old'];current=store.root/row['name']
        already_restored=current.is_file()and _hash(current.read_bytes())==row['old_sha256']
        if row['existed']and not already_restored and(not old.is_file()or _hash(old.read_bytes())!=row['old_sha256']):raise ValueError('导出恢复备份校验失败，保留事务等待恢复')
        if current.exists()and _hash(current.read_bytes())not in(row['old_sha256'],row['new_sha256']):raise ValueError('导出注册表被其它操作修改，未覆盖')
    for row in value['files']:
        if row['existed']:
            if(store.root/row['old']).exists():os.replace(store.root/row['old'],store.root/row['name'])
        else:(store.root/row['name']).unlink(missing_ok=True)
    folder=store.root/'exports'/value['destination']
    if folder.exists()and not preserve_files:
        complete=folder/'complete.json'
        if complete.exists():
            receipt=json.loads(complete.read_text('utf8'))
            if receipt.get('validation',{}).get('set_id')!=value['set_id']:raise ValueError('新导出目录的事务身份不同，未清理')
        names=value['generated_files']
        paths=[]
        for name in names:
            path=folder/name
            if Path(name).name!=name or path.is_symlink()or path.is_junction()or path.resolve().parent!=folder.resolve():raise ValueError('新导出事务文件路径无效')
            paths.append(path)
        for path in paths:path.unlink(missing_ok=True)
        if not any(folder.iterdir()):folder.rmdir()
    _cleanup(store,value)


def recover(store):
    if not(store.root/JOURNAL).exists():return
    # Another live exporter owns this OS lock. Never roll back its pending work.
    from native_package import _claim_export,_release_export
    guard=_claim_export(store)
    try:rollback(store,preserve_files=True)
    finally:_release_export(guard)
