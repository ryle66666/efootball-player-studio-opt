"""One strict logical-resource map for export, import and Android bootstrap.

No resource bytes are buffered by verification or file_payload. The small JSON
descriptor is the sole protocol; Java consumes the same fields before boot.
"""
from contextlib import ExitStack
import copy,hashlib,io,json,re,zipfile
from pathlib import Path

PROTOCOL='player-studio-resource-set-v1'
MANIFEST='assets/player-studio/resource-set.json'
VOLUME_MANIFEST='assets/player-studio/resource-volume.json'
DISCOVERY_ACTION='com.playerstudio.RESOURCE_VOLUME'
MAX_MANIFEST=8*1024*1024
BLOCK=1024*1024


class MissingResourcePackage(ValueError):pass


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf8')
def digest(value):return hashlib.sha256(canonical(value)).hexdigest()


def valid_path(value):
    if not isinstance(value,str)or not value or value.startswith('/')or'\\'in value or':'in value or any(p in('','.', '..')for p in value.split('/'))or'\0'in value:
        raise ValueError('资源集路径无效：'+str(value))
    return value


def _keys(value,allowed,required,label):
    if not isinstance(value,dict)or not required<=value.keys()or value.keys()-allowed:
        raise ValueError('资源集'+label+'字段缺失或包含未知字段')


def _hash(value,label):
    if not isinstance(value,str)or not re.fullmatch('[0-9a-f]{64}',value):raise ValueError('资源集'+label+'SHA256无效')


def normalize(document,*,check_id=True):
    value=copy.deepcopy(document)
    _keys(value,{'protocol','loader_min','set_id','game','packages','files'},
          {'protocol','loader_min','game','packages','files'},'清单')
    if value['protocol']!=PROTOCOL or type(value['loader_min'])is not int or value['loader_min']!=1:
        raise ValueError('资源集协议或最低加载器版本尚不支持')
    _keys(value['game'],{'package','family'},{'package','family'},'游戏')
    for field in('package','family'):
        if not isinstance(value['game'][field],str)or not value['game'][field]:raise ValueError('资源集游戏身份无效')
    packages=value['packages'];files=value['files']
    if not isinstance(packages,list)or not 1<=len(packages)<=128 or not isinstance(files,list)or len(files)>60000:
        raise ValueError('资源集包数或文件数无效')
    ids={};names=set()
    for row in packages:
        _keys(row,{'id','role','package','version_code','signers','content_sha256','volume'},
              {'id','role','package','version_code','signers'},'依赖')
        if row['role']not in('game','offline','extension')or not isinstance(row['id'],str)or not re.fullmatch('[A-Za-z0-9_-]{1,64}',row['id']):raise ValueError('资源集依赖标识无效')
        if not isinstance(row['package'],str)or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+',row['package']):raise ValueError('资源集包名无效')
        if row['id']in ids or row['package']in names:raise ValueError('资源集依赖重复')
        if type(row['version_code'])is not int or not 0<=row['version_code']<=2147483647:raise ValueError('资源集版本号无效')
        if not isinstance(row['signers'],list)or not row['signers']or len(row['signers'])!=len(set(row['signers'])):raise ValueError('资源集签名身份缺失')
        for signer in row['signers']:_hash(signer,'签名')
        if row['role']in('extension','offline'):_hash(row.get('content_sha256'),'依赖内容')
        if row['role']=='extension':
            _hash(row.get('content_sha256'),'扩展内容')
            if type(row.get('volume'))is not int or not 1<=row['volume']<=127:raise ValueError('资源集扩展卷编号无效')
        elif 'volume'in row or row['role']=='game'and'content_sha256'in row:raise ValueError('非扩展依赖不应含扩展卷字段或主包自引用摘要')
        ids[row['id']]=row;names.add(row['package'])
    games=[p for p in packages if p['role']=='game']
    if len(games)!=1 or games[0]['package']!=value['game']['package']:raise ValueError('资源集主游戏身份不一致')
    if any(set(p['signers'])!=set(games[0]['signers'])for p in packages):raise ValueError('整套游戏与资源依赖必须签名配套')
    seen=set();groups={};physical={}
    for row in files:
        _keys(row,{'path','package','entry','bytes','sha256','group','priority'},
              {'path','package','entry','bytes','sha256','group','priority'},'文件')
        valid_path(row['path']);valid_path(row['entry']);_hash(row['sha256'],'文件')
        if row['path']in(MANIFEST,VOLUME_MANIFEST)or row['entry']in(MANIFEST,VOLUME_MANIFEST):raise ValueError('资源清单不能自引用')
        if row['package']not in ids or ids[row['package']]['role']=='offline':raise ValueError('资源文件依赖未声明或使用了旧离线ZIP内部路径')
        if type(row['bytes'])is not int or not 0<=row['bytes']<0xffffffff:raise ValueError('资源文件大小超出单APK格式')
        if type(row['priority'])is not int or not 0<=row['priority']<=2147483647:raise ValueError('资源覆盖优先级无效')
        if not isinstance(row['group'],str)or not row['group']:raise ValueError('资源加载组缺失')
        key=(row['path'],row['priority'])
        if key in seen:raise ValueError('资源逻辑路径同优先级重复：'+row['path'])
        seen.add(key)
        physical_key=(row['package'],row['entry']);content=(row['bytes'],row['sha256'])
        if physical.setdefault(physical_key,content)!=content:raise ValueError('同一物理资源映射内容不一致：'+row['entry'])
        group=(row['group'],row['priority']);owner=groups.setdefault(group,row['package'])
        if owner!=row['package']:raise ValueError('完整资源组被拆分到多个APK：'+row['group'])
    value['packages']=sorted(packages,key=lambda p:p['id'])
    value['files']=sorted(files,key=lambda p:(p['path'],p['priority']))
    expected=digest({k:v for k,v in value.items()if k!='set_id'})
    if check_id and value.get('set_id')!=expected:raise ValueError('资源集清单摘要不一致')
    value['set_id']=expected
    if len(canonical(value))>MAX_MANIFEST:raise ValueError('资源集清单过大')
    return value


def build(game,packages,files):
    return normalize(dict(protocol=PROTOCOL,loader_min=1,game=game,packages=packages,files=files),check_id=False)


def loads(raw):
    if len(raw)>MAX_MANIFEST:raise ValueError('资源集清单过大')
    def pairs(values):
        result={}
        for k,v in values:
            if k in result:raise ValueError('资源集JSON键重复：'+k)
            result[k]=v
        return result
    return normalize(json.loads(raw,object_pairs_hook=pairs))


def read_manifest(apk):
    with zipfile.ZipFile(apk)as archive:
        if MANIFEST not in archive.namelist():return None
        info=archive.getinfo(MANIFEST)
        if info.file_size>MAX_MANIFEST:raise ValueError('资源集清单过大')
        return loads(archive.read(info))


def volume_content(files):
    # Exclude the target package id to keep content naming independent of its
    # own digest-derived Android package name.
    return digest(sorted([{k:v for k,v in row.items()if k!='package'}for row in files],key=lambda r:(r['path'],r['priority'])))


def extension_package(game_package,volume,content_sha256):
    _hash(content_sha256,'扩展内容')
    result=f'{game_package}.rs.v{volume:03d}.h{content_sha256[:16]}'
    if len(result)>220:raise ValueError('游戏包名过长，无法安全生成扩展资源包名')
    return result


def verify_copy_manifest(document,release):
    """The original Gateway copier must never overwrite a prepared resource.

    It ignores our receipt and unconditionally copies release.files whenever
    release_id changes, including a full .installing temporary per main UCAS.
    Only code/templates/seed DB that this loader does not own may remain there.
    """
    managed={row['path']for row in document['files']}
    prefix='assets/gateway-release/'
    for row in release.get('files',[]):
        if prefix+row['path']in managed:raise ValueError('已接管资源仍在原生物理复制清单中，会重复覆盖安装凭据：'+row['path'])
    return dict(verified=True,managed_not_recopied=True)


class _Verified(io.RawIOBase):
    def __init__(self,stream,row):self.stream=stream;self.row=row;self.size=0;self.hash=hashlib.sha256();self.done=False
    def readable(self):return True
    def read(self,size=-1):
        if size==0:return b''
        block=self.stream.read(size);self.hash.update(block);self.size+=len(block)
        if self.size>self.row['bytes']:raise ValueError('资源集文件超过声明大小：'+self.row['path'])
        if not block or self.size==self.row['bytes']:
            if self.size!=self.row['bytes']or self.hash.hexdigest()!=self.row['sha256']:raise ValueError('资源集文件校验失败：'+self.row['path'])
            self.done=True
        return block
    def readinto(self,buffer):
        block=self.read(len(buffer));buffer[:len(block)]=block;return len(block)
    def close(self):
        if not self.closed:self.stream.close()
        super().close()


class Reader:
    """ZipFile-like logical reader. All dependencies are explicit, never guessed."""
    def __init__(self,game,packages=None,*,verify_identities=True,metadata_only=False,verified_signatures=None):
        self.filename=str(game);self._stack=ExitStack();self.archives={};self.locations={};self.document=None;self._source_stamps={};self.signatures={}
        try:
            game_archive=self._stack.enter_context(zipfile.ZipFile(game));self._check_zip(game_archive)
            self.archives['game']=game_archive
            self.program_archive=game_archive
            from program_content import DataArchive
            self._content=DataArchive(game_archive,game,packages,metadata_only=metadata_only)
            self._stack.callback(self._content.close)
            if self._content.reader is not None:
                from export_payload import identity as file_identity
                content_path=Path(self._content.reader.path)
                self._source_stamps[content_path]=file_identity(content_path.stat())
            if MANIFEST not in game_archive.namelist():return
            if game_archive.getinfo(MANIFEST).file_size>MAX_MANIFEST:raise ValueError('资源集清单过大')
            self.document=loads(game_archive.read(MANIFEST));provided=dict(packages or{})
            for dep in self.document['packages']:
                actual=game if dep['role']=='game'else provided.get(dep['id'],provided.get(dep['package']))
                if actual is None:raise MissingResourcePackage('缺少资源包 '+dep['package']+'，所需内容 '+dep.get('content_sha256',str(dep['version_code'])))
                archive=game_archive if dep['role']=='game'else self._stack.enter_context(zipfile.ZipFile(actual))
                from export_payload import identity as file_identity
                path=Path(actual);self._source_stamps[path]=file_identity(path.stat())
                self._check_zip(archive);self.archives[dep['id']]=archive
                if verify_identities:
                    from apk_installation import identity
                    from apk_format import verify_v2
                    receipt=(verified_signatures or{}).get(str(path))
                    if receipt and receipt.get('file')==self._source_stamps[path]and receipt.get('signature',{}).get('verified_v2'):
                        signature=receipt['signature']
                    else:signature=verify_v2(actual)
                    self.signatures[dep['id']]=signature
                    actual_info=identity(actual)
                    if actual_info['package']!=dep['package']or actual_info['version_code']!=dep['version_code']or set(actual_info['signers'])!=set(dep['signers']):
                        raise ValueError('资源包名、版本或签名不配套：'+dep['package'])
                    if not signature.get('verified_v2')or signature.get('signer_sha256')not in dep['signers']:
                        raise ValueError('资源包已验证签名与清单不一致：'+dep['package'])
                if dep['role']=='extension':self._check_volume(archive,dep)
            for row in self.document['files']:
                prior=self.locations.get(row['path'])
                if prior is None or prior['priority']<row['priority']:self.locations[row['path']]=row
            # A moved file must truly be absent from the main APK. Otherwise
            # its stale payload would preserve the very overflow being fixed.
            for path,row in self.locations.items():
                if self._dep(row['package'])['role']=='extension'and path in game_archive.namelist():
                    raise ValueError('已迁移资源仍残留于游戏APK：'+path)
            self._unchanged()
        except BaseException:self.close();raise
    def _dep(self,key):return next(p for p in self.document['packages']if p['id']==key)
    @staticmethod
    def _check_zip(archive):
        names=archive.namelist()
        if len(names)!=len(set(names)):raise ValueError('资源APK包含重复ZIP路径')
        for name in names:
            if name.endswith('/'):continue
            valid_path(name)
    def _check_volume(self,archive,dep):
        try:
            if archive.getinfo(VOLUME_MANIFEST).file_size>MAX_MANIFEST:raise ValueError('扩展卷描述过大')
            descriptor=json.loads(archive.read(VOLUME_MANIFEST))
        except KeyError as exc:raise ValueError('扩展资源包缺少卷描述：'+dep['package'])from exc
        expected={k:dep[k]for k in('id','role','package','version_code','signers','content_sha256','volume')}
        if descriptor!=dict(protocol=PROTOCOL,package=expected):raise ValueError('扩展资源包内容版本不匹配：'+dep['package'])
        rows=[r for r in self.document['files']if r['package']==dep['id']]
        if volume_content(rows)!=dep['content_sha256']:raise ValueError('扩展资源映射内容摘要不一致：'+dep['package'])
    def close(self):self._stack.close()
    def _unchanged(self):
        from export_payload import identity as file_identity
        if any(file_identity(path.stat())!=stamp for path,stamp in self._source_stamps.items()):raise ValueError('资源集APK在读取时发生变化')
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def namelist(self):return sorted((set(self.archives['game'].namelist())|self.locations.keys()|self._content.virtual.keys())-self._content.removed)
    def infolist(self):return[self.getinfo(name)for name in self.namelist()]
    def _locate(self,name):
        if isinstance(name,zipfile.ZipInfo):name=name.filename
        if name in self._content.removed:raise KeyError(name)
        if name in self._content.virtual:return self._content,name,None
        valid_path(name.rstrip('/'));row=self.locations.get(name)
        if row:return self.archives[row['package']],row['entry'],row
        return self.archives['game'],name,None
    def getinfo(self,name):
        if isinstance(name,zipfile.ZipInfo):name=name.filename
        archive,entry,row=self._locate(name);info=copy.copy(archive.getinfo(entry));info.filename=name
        if row and info.file_size!=row['bytes']:raise ValueError('资源ZIP大小与清单不一致：'+name)
        return info
    def open(self,name,mode='r',*args,**kwargs):
        if mode!='r':raise ValueError('资源集只读')
        archive,entry,row=self._locate(name);self.getinfo(name);stream=archive.open(entry,'r')
        return _Verified(stream,row)if row else stream
    def read(self,name,*,limit=16*1024*1024):
        info=self.getinfo(name)
        if limit is not None and info.file_size>limit:raise ValueError('资源较大，请使用流式open或显式limit=None：'+name)
        with self.open(name)as stream:return stream.read()
    def file_payload(self,name):
        from export_payload import FilePayload
        archive,entry,row=self._locate(name)
        if archive is self._content:return archive.file_payload(entry)
        result=FilePayload.from_zip(archive,entry)
        if row and(result.bytes!=row['bytes']or result.sha256!=row['sha256']):raise ValueError('资源集文件校验失败：'+name)
        return result
    def verify(self):
        self._unchanged();count=size=0;physical=set()
        if self._content.reader is not None:
            for name in self._content.virtual:
                count+=1
                with self.open(name)as stream:
                    while block:=stream.read(BLOCK):size+=len(block)
        for row in self.document['files']if self.document else[]:
            count+=1;key=(row['package'],row['entry'])
            if key in physical:continue
            physical.add(key)
            archive=self.archives[row['package']]
            if archive.getinfo(row['entry']).file_size!=row['bytes']:raise ValueError('资源ZIP大小与清单不一致：'+row['path'])
            with _Verified(archive.open(row['entry']),row)as stream:
                while block:=stream.read(BLOCK):size+=len(block)
        for dep in self.document['packages']if self.document else[]:
            if dep['role']!='offline':continue
            hash_value=hashlib.sha256()
            try:
                with self.archives[dep['id']].open('assets/resource-bootstrap/resources.zip')as stream:
                    while block:=stream.read(BLOCK):hash_value.update(block);size+=len(block)
            except KeyError as exc:raise ValueError('离线资源包缺少resources.zip：'+dep['package'])from exc
            if hash_value.hexdigest()!=dep['content_sha256']:raise ValueError('原离线资源内容摘要不匹配：'+dep['package'])
            count+=1
        self._unchanged()
        return dict(verified=True,files=count,bytes=size,set_id=self.document['set_id']if self.document else None)
