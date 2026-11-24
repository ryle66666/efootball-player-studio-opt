"""Read 73save snapshots through the game's normal save-import lifecycle."""
from contextlib import closing
import json
from pathlib import Path, PurePosixPath
import sqlite3
import struct
import tempfile
import uuid

MAGIC=b'73SAVE01'
MAX_MANIFEST=4*1024*1024
MAX_DATA=4*1024*1024*1024
DIRECTORIES={'embedded-gateway','outputs','efootball','data','databases','saves','save'}


def _json(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('存档清单包含重复字段')
        result[key]=value
    return result


def _path(value):
    if not isinstance(value,str)or'\\'in value:raise ValueError('存档路径无效')
    path=PurePosixPath(value)
    if path.is_absolute()or str(path)!=value or any(p in('','..','.')for p in path.parts):raise ValueError('存档路径无效')
    if not 1<=len(path.parts)<=6 or not set(path.parts[:-1])<=DIRECTORIES:raise ValueError('存档路径不受支持')
    name=path.name
    if name not in('local-club.sqlite3','local-club.sqlite3-wal','club.sqlite3','club.sqlite3-wal','save-aliases.json','club-aliases.json'):
        raise ValueError('存档文件不受支持')
    return path


def prepare(bundle,directory,package,offline_id,validate):
    """Produce the snapshot and identity metadata expected by prepare_import."""
    bundle=Path(bundle);directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    target=directory/('import-'+uuid.uuid4().hex+'.sqlite3')
    try:
        with bundle.open('rb')as stream,tempfile.TemporaryDirectory(prefix='save-import-',dir=directory)as temporary:
            if stream.read(8)!=MAGIC:raise ValueError('存档格式不支持')
            header=stream.read(4)
            if len(header)!=4:raise ValueError('存档清单不完整')
            length=struct.unpack('>I',header)[0]
            if not 1<=length<=MAX_MANIFEST:raise ValueError('存档清单过大')
            raw=stream.read(length)
            if len(raw)!=length:raise ValueError('存档清单不完整')
            manifest=json.loads(raw.decode('utf8'),object_pairs_hook=_json)
            if not isinstance(manifest,dict)or manifest.get('protocol')!='73save-v1'or manifest.get('package')!=package:
                raise ValueError('存档备份的游戏模式不匹配')
            rows=manifest.get('files')
            if not isinstance(rows,list)or not 1<=len(rows)<=128 or manifest.get('removed')!=[]:raise ValueError('存档文件清单无效')
            paths=set();databases=[];total=0
            for row in rows:
                if not isinstance(row,dict):raise ValueError('存档文件说明无效')
                path=_path(row.get('path'));size=row.get('bytes')
                if type(size)is not int or size<0 or size>MAX_DATA or str(path)in paths:raise ValueError('存档文件长度或路径无效')
                paths.add(str(path));total+=size
                if total>MAX_DATA:raise ValueError('存档备份过大')
                destination=Path(temporary).joinpath(*path.parts);destination.parent.mkdir(parents=True,exist_ok=True)
                with destination.open('xb')as output:
                    remaining=size
                    while remaining:
                        block=stream.read(min(1024*1024,remaining))
                        if not block:raise ValueError('存档文件不完整')
                        output.write(block);remaining-=len(block)
                if path.suffix=='.sqlite3':databases.append(destination)
            if stream.read(1):raise ValueError('存档备份包含多余数据')
            if len(databases)!=1:raise ValueError('备份必须包含一个游戏存档数据库')
            with closing(sqlite3.connect(databases[0]))as source:
                if source.execute('pragma quick_check').fetchall()!=[('ok',)]:raise ValueError('存档数据库检查失败')
                row=source.execute("select value from meta where key='user_id'").fetchone()
                if not row:raise ValueError('存档缺少玩家身份')
                source_id=int(row[0])
                if type(offline_id)is not int or offline_id<=0 or source_id<=0 or source_id==offline_id:raise ValueError('存档玩家身份不兼容')
                with closing(sqlite3.connect(target))as output:
                    source.backup(output);output.execute('pragma journal_mode=delete')
            validate(target)
        return target,{'format':'73efootball-save','version':1,'offline_id':offline_id,'source_id':source_id}
    except BaseException:
        target.unlink(missing_ok=True);raise


def install(namespace,document):
    import save_manager
    previous=save_manager.prepare_import
    if getattr(previous,'_studio_73save',False):
        previous._studio_context=(namespace,document);return
    def prepare_import(bundle,directory):
        with Path(bundle).open('rb')as stream:magic=stream.read(8)
        if magic!=MAGIC:return previous(bundle,directory)
        state,current=prepare_import._studio_context
        package=current().get('installation',{}).get('package','com.efootball.revival.local')
        alias=state.get('_alias')
        return prepare(bundle,directory,package,getattr(alias,'offline_id',None),save_manager.validate)
    prepare_import._studio_73save=True
    prepare_import._studio_context=(namespace,document)
    save_manager.prepare_import=prepare_import
