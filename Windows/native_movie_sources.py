"""Resolve game USM files independently of editor preview images and videos."""
import json
from pathlib import Path, PurePosixPath
import re
import uuid

PREFIX='PesMobile/Content/Movies/PlayerCard/'


def native_path(path):
    value=str(path).replace('\\','/').removeprefix('../../../')
    if value.startswith('/Game/'):value='PesMobile/Content/'+value[6:]
    if not value.startswith(PREFIX)or not re.fullmatch(r'[a-zA-Z0-9_]+\.usm',value[len(PREFIX):]):return None
    return value


class MovieSources:
    def __init__(self,library):
        self.library=library;self.sources={};self.candidates={}
        for path in getattr(library,'card_media',{}).get('movies',[]):
            normalized=native_path(path)
            if normalized:self.candidates.setdefault(PurePosixPath(normalized).stem,normalized)
        folder=Path(library.containers)if getattr(library,'containers',None)else None
        folders=[folder]if folder else[]
        index=Path(library.resources)/'incremental-content.json'if getattr(library,'resources',None)else None
        if index is not None and index.is_file():
            registered=json.loads(index.read_text('utf8'))
            folders.extend(Path(p['path']).parent for p in registered.get('packages',[]))
        paths=list(dict.fromkeys(path for directory in folders for path in sorted(directory.glob('*.pak'),key=lambda p:('_P'in p.stem,p.name))))
        if paths:
            from native_movie_pak_source import Pak
            if folder is None:raise ValueError('未准备原生卡面视频资源目录')
            key=(folder/'resource-key.bin').read_bytes()
            for path in paths:
                pak=Pak(path,key)
                for entry in pak.files:
                    normalized=native_path(entry)
                    if normalized:
                        self.sources[normalized]=(pak,entry)
                        self.candidates[PurePosixPath(normalized).stem]=normalized

    def paths(self):return list(self.candidates.values())

    def read(self,name):
        name=PurePosixPath(str(name).replace('\\','/')).stem
        path=self.candidates.get(name)
        if path is None:raise ValueError('原生卡面视频未登记：'+name)
        if path in self.sources:
            pak,entry=self.sources[path];raw=pak.read(entry)
        else:
            raw_reader=getattr(self.library.face_assets(),'raw',None)
            if callable(raw_reader):raw=raw_reader(path)
            else:
                target=Path(self.library.converted)/path
                if not target.is_file():self.library.run([path],'raw')
                if not target.is_file():raise ValueError('原生卡面视频未读取：'+path)
                raw=target.read_bytes()
        if not raw.startswith(b'CRID'):raise ValueError('游戏卡面视频需要原生 USM：'+path)
        return raw

    def file(self,path):
        normalized=native_path(path)
        if normalized is None:raise ValueError('原生卡面视频路径无效：'+str(path))
        target=Path(self.library.converted)/normalized
        raw=self.read(normalized)
        if target.is_file()and target.read_bytes()==raw:return target
        target.parent.mkdir(parents=True,exist_ok=True)
        temporary=target.with_name('.movie-'+uuid.uuid4().hex)
        try:temporary.write_bytes(raw);temporary.replace(target)
        finally:temporary.unlink(missing_ok=True)
        return target


def sources(library):
    value=getattr(library,'_native_movie_sources',None)
    signature=tuple(getattr(library,'card_media',{}).get('movies',[]))
    if value is None or getattr(library,'_native_movie_signature',None)!=signature:
        value=MovieSources(library);library._native_movie_sources=value;library._native_movie_signature=signature
    return value


def paths(library):return sources(library).paths()


def file(library,path):return sources(library).file(path)


def read(library,name):return sources(library).read(name)
