"""Streaming cumulative data updates for an installed game program."""
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path,PurePosixPath
import re
import shutil
import struct
import uuid

MAGIC=b'73PACK01'
PROTOCOL='73pack-v1'
MAX_MANIFEST=16*1024*1024
MAX_FILES=60000
BLOCK=1024*1024
DATA_PREFIXES=('custom-resources/','livedata/','work/templates/','outputs/reference/')
PREFIX='assets/gateway-release/'
SEED=PREFIX+'outputs/efootball/local-club.sqlite3'


def relative(value):
    if not isinstance(value,str)or not value or len(value)>1024 or'\\'in value or':'in value or'\0'in value:
        raise ValueError('内容文件路径无效')
    path=PurePosixPath(value)
    if path.is_absolute()or any(part in('','.','..')for part in value.split('/')):
        raise ValueError('内容文件路径越界')
    if any(part.rstrip(' .')!=part or part.split('.')[0].upper()in{'CON','PRN','AUX','NUL',*(f'COM{i}'for i in range(1,10)),*(f'LPT{i}'for i in range(1,10))}for part in path.parts):
        raise ValueError('内容文件路径无效')
    if not(value.startswith(DATA_PREFIXES)or value=='release-manifest.json')or value.lower().endswith(('.py','.pyc','.so','.dex','.dll','.exe')):
        raise ValueError('内容包只能包含游戏数据')
    return value


def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('.'+path.name+'-'+uuid.uuid4().hex)
    try:
        with temporary.open('x',encoding='utf8')as stream:
            json.dump(value,stream,ensure_ascii=False,separators=(',',':'));stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


@contextmanager
def opened(value):
    if isinstance(value,(bytes,bytearray,memoryview)):
        with io.BytesIO(value)as stream:yield stream
    elif isinstance(value,(str,Path)):
        with Path(value).open('rb')as stream:yield stream
    else:
        with value.open()as stream:yield stream


def size(value):
    if isinstance(value,(bytes,bytearray,memoryview)):return len(value)
    if isinstance(value,(str,Path)):return Path(value).stat().st_size
    return value.bytes


def equal(left,right):
    with opened(left)as a,opened(right)as b:
        while True:
            first=a.read(BLOCK);second=b.read(BLOCK)
            if first!=second:return False
            if not first:return True


def _unique_object(pairs):
    value={}
    for key,item in pairs:
        if key in value:raise ValueError('内容包清单含重复字段')
        value[key]=item
    return value


class Reader:
    def __init__(self,path):
        self.path=Path(path)
        with self.path.open('rb')as stream:
            if stream.read(8)!=MAGIC:raise ValueError('内容包格式不支持')
            length=stream.read(4)
            if len(length)!=4:raise ValueError('内容包头部不完整')
            length=struct.unpack('>I',length)[0]
            if not 0<length<=MAX_MANIFEST:raise ValueError('内容包清单大小无效')
            raw=stream.read(length)
            if len(raw)!=length:raise ValueError('内容包清单不完整')
            self.manifest=json.loads(raw.decode('utf8'),object_pairs_hook=_unique_object)
            stat=os.fstat(stream.fileno())
            self._identity=(stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns)
        manifest=self.manifest
        if not isinstance(manifest,dict):raise ValueError('内容包清单格式无效')
        if manifest.get('protocol')!=PROTOCOL:raise ValueError('内容包协议不支持')
        try:
            for key in('pack_id','program_id'):uuid.UUID(manifest[key])
        except (KeyError,ValueError,TypeError,AttributeError):raise ValueError('内容包身份无效')from None
        if not isinstance(manifest.get('package'),str)or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+',manifest['package']):
            raise ValueError('内容包目标游戏无效')
        files=manifest.get('files');removed=manifest.get('removed')
        if not isinstance(files,list)or not isinstance(removed,list)or len(files)+len(removed)>MAX_FILES:
            raise ValueError('内容包文件数量无效')
        cursor=12+length;seen=set();self.entries={}
        for row in files:
            if not isinstance(row,dict)or set(row)!={'path','bytes'}:raise ValueError('内容包文件记录无效')
            name=relative(row['path']);count=row['bytes']
            if name in seen or type(count)is not int or not 0<=count<2**63:raise ValueError('内容包文件记录无效')
            seen.add(name);self.entries[name]=(cursor,count);cursor+=count
        for name in removed:
            relative(name)
            if name in seen:raise ValueError('内容包文件重复或同时标记删除')
            seen.add(name)
        if cursor!=self._identity[2]:raise ValueError('内容包文件长度不一致')

    @contextmanager
    def open(self,name):
        offset,count=self.entries[name]
        with self.path.open('rb')as stream:
            stat=os.fstat(stream.fileno())
            if (stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns)!=self._identity:
                raise ValueError('内容包在读取期间发生变化，请重新导入')
            stream.seek(offset)
            yield LimitedReader(stream,count)


class LimitedReader:
    def __init__(self,stream,count):self.stream=stream;self.remaining=count
    def read(self,count=-1):
        count=self.remaining if count<0 else min(count,self.remaining)
        raw=self.stream.read(count);self.remaining-=len(raw)
        if len(raw)!=count:raise ValueError('内容包文件提前结束')
        return raw


def write(destination,*,package,program_id,files,removed=()):
    destination=Path(destination)
    if destination.exists():raise ValueError('内容包输出已存在')
    manifest={'protocol':PROTOCOL,'pack_id':str(uuid.uuid4()),'program_id':str(uuid.UUID(program_id)),
              'package':package,'files':[{'path':relative(name),'bytes':size(value)}for name,value in sorted(files.items())],
              'removed':sorted(relative(name)for name in removed)}
    raw=json.dumps(manifest,ensure_ascii=False,separators=(',',':')).encode('utf8')
    if len(raw)>MAX_MANIFEST or len(manifest['files'])+len(manifest['removed'])>MAX_FILES:raise ValueError('内容包清单超限')
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=destination.with_name('.'+destination.name+'-'+uuid.uuid4().hex)
    try:
        with temporary.open('xb')as output:
            output.write(MAGIC+struct.pack('>I',len(raw))+raw)
            for row in manifest['files']:
                copied=0
                with opened(files[row['path']])as stream:
                    while True:
                        block=stream.read(BLOCK)
                        if not block:break
                        output.write(block);copied+=len(block)
                if copied!=row['bytes']:raise ValueError('内容文件在导出期间发生变化')
            output.flush();os.fsync(output.fileno())
        Reader(temporary)
        os.replace(temporary,destination)
    finally:temporary.unlink(missing_ok=True)
    return manifest


def record_path(store,package):
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.]+',package):raise ValueError('游戏包名无效')
    return store.root/'runtime-programs'/(package+'.json')


def installed_program(store,package):
    path=record_path(store,package)
    if not path.is_file():return None
    value=json.loads(path.read_text('utf8'))
    if not isinstance(value,dict)or not isinstance(value.get('apk'),str)or not isinstance(value.get('program_id'),str):
        raise ValueError('已有游戏程序登记格式无效')
    return value


def remember_program(store,package,program_id,apk,resource_packages=None,source=None,*,managed=False,delivery_name=None):
    record={'program_id':str(uuid.UUID(program_id)),'package':package,
            'apk':str(Path(apk).resolve()),'resource_packages':resource_packages or{}}
    if source:
        path=Path(source).resolve();stat=path.stat()
        record['source']={'path':str(path),'bytes':stat.st_size,'mtime_ns':stat.st_mtime_ns}
    if managed:
        record.update(managed=True,delivery_name=delivery_name)
    atomic_json(record_path(store,package),record)


def plan_export(store,package,source,replacements,open_program,*,capacity,limit,program_id,remove_entries=()):
    """Generate an APK within capacity; allow data updates for oversized APKs."""
    if type(limit)is not int or not 0<limit<=0xffffffff:raise ValueError('APK 容量上限无效')
    if not isinstance(capacity,dict):raise ValueError('缺少有效的 APK 容量规划')
    estimate=capacity.get('signed_upper_bound')
    if type(estimate)is not int or estimate<0:raise ValueError('缺少有效的 APK 容量规划')
    try:program_id=str(uuid.UUID(program_id))
    except (ValueError,TypeError,AttributeError):raise ValueError('游戏程序身份无效')from None
    if estimate<limit:
        result={'kind':'program','program_id':program_id}
    else:
        result=plan(store,package,source,replacements,open_program,remove_entries=remove_entries)
        if result['kind']=='program':result={'kind':'program_content','program_id':program_id}
    return dict(result,apk_capacity=dict(capacity),apk_limit=limit,policy='apk-unless-capacity-exceeded')


def plan(store,package,source,replacements,open_program,*,remove_entries=(),player_update=False):
    """Compare final program bytes and emit cumulative changes to its data."""
    prior=installed_program(store,package)
    if not prior or not Path(prior['apk']).is_file():return {'kind':'program','program_id':str(uuid.uuid4())}
    with open_program(prior)as base:
        marker='assets/player-studio/program.json'
        if marker not in base.namelist():return {'kind':'program','program_id':str(uuid.uuid4())}
        identity=json.loads(base.read(marker))
        if (identity.get('package')!=package or identity.get('program_id')!=prior['program_id']
                or identity.get('protocol')!='73-studio-program-v1'):
            return {'kind':'program','program_id':str(uuid.uuid4())}
        previous_files=set();parent=None
        if player_update:
            from player_updates import capable
            selected=json.loads(source.read(marker))if marker in source.namelist()else None
            if not capable(identity) or not capable(selected) or selected['program_id']!=identity['program_id']:
                raise ValueError('请选择工坊已登记且支持球员小包的同一份基准 APK')
            imported=getattr(source,'_content',None)
            if imported is not None and imported.reader is not None:
                previous_files=set(imported.virtual)|imported.removed
                parent=imported.reader.manifest['pack_id']
        from program_content import BASE,inventory
        source_names=set(source.namelist());base_names=set(base.namelist())
        base_names.update(PREFIX+row['path']for row in inventory(base)['files'])
        physical_base=getattr(base,'program_archive',base)
        physical_names=set(physical_base.namelist())
        removed_program_entries=set(remove_entries)
        if removed_program_entries.intersection(replacements):raise ValueError('程序移除项同时被替换')
        final_names=(source_names|set(replacements))-removed_program_entries
        def data(name):
            if not name.startswith(PREFIX):return False
            try:relative(name[len(PREFIX):]);return True
            except ValueError:return False
        program_names={name for name in final_names|base_names if not data(name)
                       and not name.startswith('META-INF/')and name not in('assets/player-studio/program.json','assets/player-studio/resource-set.json',BASE,SEED)}
        for name in sorted(program_names):
            if name not in final_names or name not in base_names:return {'kind':'program','program_id':str(uuid.uuid4())}
            if name=='AndroidManifest.xml':
                from program_bootstrap import program_manifest
                previous=base.read(name)
                if name in replacements:
                    with opened(replacements[name])as stream:current=stream.read()
                else:current=source.read(name)
                if program_manifest(current)!=program_manifest(previous):return {'kind':'program','program_id':str(uuid.uuid4())}
                continue
            with base.open(name)as previous:
                if name in replacements:
                    with opened(replacements[name])as current:
                        if not streams_equal(current,previous):return {'kind':'program','program_id':str(uuid.uuid4())}
                else:
                    with source.open(name)as current:
                        if not streams_equal(current,previous):return {'kind':'program','program_id':str(uuid.uuid4())}
        changed={}
        for name in sorted(n for n in final_names if data(n)):
            value=replacements.get(name)
            same=False
            if name in physical_names:
                with (opened(value)if value is not None else source.open(name))as current,physical_base.open(name)as previous:same=streams_equal(current,previous)
            if name in previous_files:same=False
            if value is None:
                if same:continue
                value=SourceMember(source,name)
            if not same:changed[name[len(PREFIX):]]=value
        removed=[name[len(PREFIX):]for name in (base_names|previous_files)-final_names if data(name)]
        result={'kind':'content_pack','program_id':prior['program_id'],'files':changed,'removed':removed}
        if player_update:result.update(base_path=prior['apk'],parent=parent)
        return result


class SourceMember:
    def __init__(self,source,name):self.source=source;self.name=name;self.bytes=source.getinfo(name).file_size
    def open(self):return self.source.open(self.name)


def streams_equal(first,second):
    while True:
        left=first.read(BLOCK);right=second.read(BLOCK)
        if left!=right:return False
        if not left:return True


def publish_registries(store,updates):
    """Replace editor registries only after the content file is available."""
    with store.lock:
        prepared=[];previous=[]
        try:
            for value,data in updates.items():
                path=Path(value)
                if path.resolve().parent!=store.root.resolve():raise ValueError('登记文件路径无效')
                raw=data if isinstance(data,bytes)else data.encode('utf8')
                temporary=path.with_name('.'+path.name+'-'+uuid.uuid4().hex)
                with temporary.open('xb')as output:
                    output.write(raw);output.flush();os.fsync(output.fileno())
                prepared.append((path,temporary));previous.append((path,path.read_bytes()if path.is_file()else None))
            try:
                for path,temporary in prepared:os.replace(temporary,path)
            except BaseException:
                for path,raw in previous:
                    if raw is None:path.unlink(missing_ok=True)
                    else:path.write_bytes(raw)
                raise
        finally:
            for path,temporary in prepared:temporary.unlink(missing_ok=True)
