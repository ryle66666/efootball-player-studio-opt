"""Explicit whole-object reads for existing JSON/SQLite/CPK business parsers.

Reader.read keeps its small metadata default. These calls opt in at the actual
consumer and read in fixed chunks; a None business limit retains the prior ZIP
consumer's declared-size boundary. Large native containers must use extract or
FilePayload, never this convenience function.
"""
import io,json
from pathlib import PurePosixPath

BLOCK=1024*1024
PERSONAL_JSON_LIMIT=256*1024*1024


def read_bytes(archive,name,*,limit):
    entry=getattr(name,'filename',name);entry=str(entry)
    if limit is None and PurePosixPath(entry).suffix.lower()in('.ucas','.utoc','.pak'):
        raise ValueError('原生资源容器必须使用流式提取或 FilePayload：'+entry)
    info=archive.getinfo(name)if hasattr(archive,'getinfo')else None
    declared=getattr(info,'file_size',None)
    if limit is not None and(type(limit)is not int or limit<0):raise ValueError('业务读取上限无效')
    if declared is not None and(declared<0 or limit is not None and declared>limit):raise ValueError('业务资料超过允许大小：'+entry)
    boundary=declared if limit is None else limit
    stream=archive.open(name)if hasattr(archive,'open')else io.BytesIO(archive.read(name))
    with stream,io.BytesIO()as output:
        count=0
        while block:=stream.read(BLOCK):
            count+=len(block)
            if boundary is not None and count>boundary:raise ValueError('业务资料超过声明或允许大小：'+entry)
            output.write(block)
        if declared is not None and count!=declared:raise ValueError('业务资料读取长度不符：'+entry)
        return output.getvalue()


def read_json(archive,name,*,limit):return json.loads(read_bytes(archive,name,limit=limit))
