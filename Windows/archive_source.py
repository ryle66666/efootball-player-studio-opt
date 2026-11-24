"""Read-only ZIP sources with bounded raw decompression and structural checks."""
from contextlib import contextmanager
import io
from pathlib import Path
import struct
import zlib
from types import SimpleNamespace

BLOCK=1024*1024


class Member:
    def __init__(self,stream,compressed,size,method):
        self.stream=stream;self.compressed=compressed;self.remaining=size;self.method=method
        self.decoder=zlib.decompressobj(-15)if method==8 else None
        self.pending=b'';self.finished=False
    def _decode(self,wanted):
        if self.method==0:
            raw=self.stream.read(min(wanted,self.compressed));self.compressed-=len(raw);return raw
        while True:
            if not self.pending and self.compressed:
                self.pending=self.stream.read(min(65536,self.compressed))
                if not self.pending:raise ValueError('压缩文件提前结束')
                self.compressed-=len(self.pending)
            if not self.pending:return b''
            raw=self.decoder.decompress(self.pending,max(1,wanted));self.pending=self.decoder.unconsumed_tail
            if self.decoder.unused_data:raise ValueError('压缩文件含多余数据')
            if raw:return raw
            if self.decoder.eof:return b''
    def read(self,size=-1):
        wanted=self.remaining if size is None or size<0 else min(size,self.remaining)
        result=bytearray()
        while len(result)<wanted:
            block=self._decode(min(BLOCK,wanted-len(result)))
            if not block:raise ValueError('解压文件长度不足')
            result.extend(block)
        self.remaining-=len(result)
        if not self.remaining and not self.finished:
            extra=self._decode(1)
            if extra or self.compressed or self.pending or(self.decoder and not self.decoder.eof):raise ValueError('解压文件长度或结束标记无效')
            self.finished=True
        return bytes(result)


class Archive:
    def __init__(self,path):
        self.filename=str(Path(path).resolve())
        self.rows={}
        with Path(self.filename).open('rb')as stream:
            size=stream.seek(0,2);stream.seek(max(0,size-65557));tail=stream.read();end=tail.rfind(b'PK\x05\x06')
            if end<0 or len(tail)-end<22:raise ValueError('压缩包目录结束标记无效')
            disk,central_disk,disk_count,count,length,offset,comment=struct.unpack_from('<HHHHIIH',tail,end+4)
            if end+22+comment!=len(tail)or disk or central_disk or disk_count!=count:raise ValueError('压缩包分卷或目录长度不支持')
            if count==65535 or offset==0xffffffff or length==0xffffffff:
                end_offset=size-len(tail)+end
                stream.seek(end_offset-20);locator=stream.read(20)
                if len(locator)!=20 or locator[:4]!=b'PK\x06\x07':raise ValueError('ZIP64目录定位无效')
                _,position,disks=struct.unpack_from('<IQI',locator,4)
                if disks!=1:raise ValueError('压缩包不能分卷')
                stream.seek(position);record=stream.read(56)
                if len(record)!=56 or record[:4]!=b'PK\x06\x06':raise ValueError('ZIP64目录无效')
                disk,central_disk,disk_count,count,length,offset=struct.unpack_from('<IIQQQQ',record,16)
                if disk or central_disk or disk_count!=count:raise ValueError('ZIP64压缩包不能分卷')
            if count>1000000 or offset+length>size-len(tail)+end:raise ValueError('压缩包目录范围无效')
            self.central_offset=offset;stream.seek(offset);central=io.BytesIO(stream.read(length))
            for _ in range(count):
                header=central.read(46)
                if len(header)!=46 or header[:4]!=b'PK\x01\x02':raise ValueError('压缩包目录条目无效')
                flags,method=struct.unpack_from('<HH',header,8)
                compressed,uncompressed=struct.unpack_from('<II',header,20)
                name_length,extra_length,comment_length,disk=struct.unpack_from('<HHHH',header,28)
                external,position=struct.unpack_from('<II',header,38)
                raw_name=central.read(name_length);extra=central.read(extra_length);note=central.read(comment_length)
                if len(raw_name)!=name_length or len(extra)!=extra_length or len(note)!=comment_length or disk:raise ValueError('压缩包目录条目被截断')
                p=0
                while p<len(extra):
                    if p+4>len(extra):raise ValueError('压缩包附加字段无效')
                    kind,n=struct.unpack_from('<HH',extra,p);p+=4;value=extra[p:p+n];p+=n
                    if len(value)!=n:raise ValueError('压缩包附加字段被截断')
                    if kind==1:
                        q=0
                        for key in ('uncompressed','compressed','position'):
                            current=locals()[key]
                            if current!=0xffffffff:continue
                            if q+8>len(value):raise ValueError('ZIP64文件长度字段无效')
                            number=struct.unpack_from('<Q',value,q)[0];q+=8
                            if key=='uncompressed':uncompressed=number
                            elif key=='compressed':compressed=number
                            else:position=number
                name=raw_name.decode('utf8'if flags&0x800 else'cp437')
                row=SimpleNamespace(filename=name,orig_filename=name,raw_name=raw_name,file_size=uncompressed,
                    compress_size=compressed,compress_type=method,flag_bits=flags,external_attr=external,header_offset=position)
                self._add(row)
            if central.tell()!=length:raise ValueError('压缩包目录含多余条目')
    def _add(self,row):
            name=row.filename
            if name in self.rows:raise ValueError('压缩包包含重复文件名')
            if'\0'in row.orig_filename or'\\'in name or name.startswith('/')or':'in name or any(p=='..'for p in name.split('/')):
                raise ValueError('压缩包文件路径无效')
            if(row.external_attr>>16)&0xf000==0xa000:raise ValueError('压缩包不能包含符号链接')
            self.rows[name]=row
    def __enter__(self):return self
    def __exit__(self,*arguments):self.close()
    def close(self):pass
    def namelist(self):return list(self.rows)
    def getinfo(self,name):return self.rows[name]
    @contextmanager
    def open(self,name):
        row=self.rows[name]
        if row.filename.endswith('/')or row.flag_bits&1 or row.compress_type not in(0,8):raise ValueError('压缩包文件编码不支持')
        with Path(self.filename).open('rb')as source:
            source.seek(row.header_offset);header=source.read(30)
            if len(header)!=30 or header[:4]!=b'PK\x03\x04':raise ValueError('压缩包文件头无效')
            flags,method=struct.unpack_from('<HH',header,6);name_length,extra_length=struct.unpack_from('<HH',header,26)
            raw_name=source.read(name_length)
            if raw_name!=row.raw_name or method!=row.compress_type or flags!=row.flag_bits:raise ValueError('压缩包文件索引不一致')
            source.seek(extra_length,1)
            if row.header_offset<0 or source.tell()+row.compress_size>self.central_offset:raise ValueError('压缩包文件区间越界')
            yield Member(source,row.compress_size,row.file_size,method)
    def read(self,name,limit=64*1024*1024):
        if limit is not None and self.rows[name].file_size>limit:raise ValueError('文件超过本次读取大小限制')
        with self.open(name)as stream:return stream.read()
