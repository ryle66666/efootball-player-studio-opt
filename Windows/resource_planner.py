"""Plan complete IoStore units using actual ZIP compression/alignment costs."""
import copy,hashlib,struct,zipfile,zlib
from pathlib import Path
from export_payload import FilePayload,payload,metadata
from apk_format import UINT32_LIMIT,SIGNING_RESERVE,_local_header,_zip_extra
import resource_set as rs


def _compressed_size(data,method):
    data=payload(data);size=data.bytes if isinstance(data,FilePayload)else len(data)
    if method==zipfile.ZIP_STORED:return size
    if method!=zipfile.ZIP_DEFLATED:raise ValueError('资源规划不支持来源压缩格式')
    encoder=zlib.compressobj(9,zlib.DEFLATED,-15);total=0
    if isinstance(data,FilePayload):
        with data.open()as stream:
            while block:=stream.read(rs.BLOCK):total+=len(encoder.compress(block))
    else:
        view=memoryview(data)
        for at in range(0,len(view),rs.BLOCK):total+=len(encoder.compress(view[at:at+rs.BLOCK]))
    return total+len(encoder.flush())


def estimate(source,replacements=None,*,remove_entries=(),store_ucas=True,extra_sizes=None,signing_reserve=SIGNING_RESERVE,allow_oversized=False):
    """Exact unsigned layout plus a conservative bounded signing block.

    extra_sizes is only for not-yet-serialized small control records. Final
    publication always rechecks the measured signed file against the limit.
    """
    replacements=dict(replacements or{});remove=set(remove_entries);extras=dict(extra_sizes or{})
    with zipfile.ZipFile(source)as archive:
        infos={p.filename:copy.copy(p)for p in archive.infolist()};comment=archive.comment
        if len(infos)!=len(archive.infolist()):raise ValueError('来源ZIP记录重复')
    if remove&replacements.keys():raise ValueError('容量规划的移除项与替换项重叠')
    for name in remove:infos.pop(name,None)
    for name in replacements.keys()|extras.keys():
        if name not in infos:infos[name]=zipfile.ZipInfo(name,(2026,1,1,0,0,0))
    cursor=central=0
    deflated_sizes={}
    for name,item in infos.items():
        rs.valid_path(name.rstrip('/'));item.extra=_zip_extra(item.extra);item.flag_bits&=~8
        if not hasattr(item,'CRC'):item.CRC=0
        if name in replacements:
            data=payload(replacements[name]);item.file_size=data.bytes if isinstance(data,FilePayload)else len(data)
            if store_ucas and name.lower().endswith('.ucas'):item.compress_type=zipfile.ZIP_STORED
            if item.compress_type==zipfile.ZIP_DEFLATED and isinstance(data,bytes) and data is replacements[name]:
                # The active and download CPK entries share one immutable bytes object.
                # The replacement map keeps it alive for the entire estimate.
                key=id(data)
                if key not in deflated_sizes:
                    deflated_sizes[key]=_compressed_size(data,item.compress_type)
                item.compress_size=deflated_sizes[key]
            else:
                item.compress_size=_compressed_size(data,item.compress_type)
        elif name in extras:item.file_size=item.compress_size=extras[name];item.compress_type=zipfile.ZIP_STORED
        if max(item.file_size,item.compress_size)>=UINT32_LIMIT:
            if allow_oversized:return dict(signed_upper_bound=max(item.file_size,item.compress_size)+signing_reserve,
                entries=len(infos),signing_reserve=signing_reserve,capacity_exceeded=True,oversized_entry=name)
            raise ValueError('单资源文件超过APK的32位ZIP容量：'+name)
        item.header_offset=cursor;cursor+=len(_local_header(item,cursor))+item.compress_size
        central+=46+len(item._encodeFilenameFlags()[0])+len(item.extra)+len(item.comment)
    if len(infos)>=65535:
        if allow_oversized:return dict(signed_upper_bound=max(cursor+central+22+len(comment)+signing_reserve,UINT32_LIMIT),
            entries=len(infos),signing_reserve=signing_reserve,capacity_exceeded=True,entry_limit_exceeded=True)
        raise ValueError('资源记录数超过非ZIP64 APK限制')
    return dict(unsigned_bytes=cursor+central+22+len(comment),signed_upper_bound=cursor+central+22+len(comment)+signing_reserve,
                entries=len(infos),central_bytes=central,signing_reserve=signing_reserve)


def _group_map(names):
    containers={};result={}
    for name in names:
        if not name.startswith('assets/gateway-release/'):continue
        base=name.rsplit('/',1)[-1]
        if '.'not in base:continue
        stem,suffix=base.rsplit('.',1)
        if suffix in('pak','utoc','ucas'):containers.setdefault(stem,{}).setdefault(suffix,[]).append(name)
    for stem,parts in containers.items():
        if 'ucas'not in parts:continue
        if 'utoc'not in parts:raise ValueError('IoStore缺少UTOC，不能安全迁移：'+stem)
        result['iostore:'+stem]=sorted(n for rows in parts.values()for n in rows)
    cpk=[n for n in names if n.startswith('assets/gateway-release/')and n.endswith('/dt870_mobile_and.cpk')]
    if cpk:result['cpk:dt870_mobile_and']=sorted(cpk)
    return result


def _volume_size(files,*,template_bytes,descriptor_bytes=8192):
    # Extension payloads are ZIP_STORED, with the exact same 4-byte alignment
    # and central headers as replace_entries; template_bytes is its measured
    # stripped signed upper bound. The descriptor's actual bytes are checked
    # again by build_volume after serialization.
    cursor=template_bytes;overhead=0
    physical={}
    for row in files:
        key=(row['bytes'],row['sha256']);previous=physical.get(key)
        if previous is None or row['entry']<previous['entry']:physical[key]=row
    for row in physical.values():
        name=row['entry'].encode('utf8');padding=7  # max actual 4-byte alignment field, including header
        used=30+len(name)+padding+row['bytes'];cursor+=used;overhead+=46+len(name)
    return cursor+overhead+descriptor_bytes+30+46+2*len(rs.VOLUME_MANIFEST)


def plan(game,replacements,*,reader=None,limit=UINT32_LIMIT,game_package=None,family='standard',
         signers=(),game_version=1,offline_dependencies=(),template_bytes=None,
         manifest_reserve=256*1024,previous_manifest=None):
    """Return main_replacements/remove_entries/volumes and the final manifest.

    ``reader`` is an already resolved ResourceSet.Reader for inherited exports.
    ``template_bytes`` MUST be estimate(template, removal_of_all_large_payloads)
    from the actual resource APK before building, not a guessed APK size.
    """
    if type(limit)is not int or not SIGNING_RESERVE<limit<=UINT32_LIMIT:raise ValueError('资源APK容量上限无效')
    owned=reader is None
    reader=reader or rs.Reader(game,verify_identities=False)
    try:
        previous=previous_manifest or reader.document
        if previous:previous=rs.normalize(previous)
        if game_package is None:
            from apk_installation import identity
            info=identity(game);game_package=info['package'];game_version=info['version_code'];signers=signers or info['signers']
        names=set(reader.namelist())|set(replacements);names.discard(rs.MANIFEST)
        groups=_group_map(names);by_name={n:g for g,rows in groups.items()for n in rows}
        if previous:
            for row in previous['files']:
                if row['path']not in by_name:
                    raise ValueError('继承资源组尚无安全迁移规则：'+row['path'])
        previous_owner={}
        if previous:
            deps={p['id']:p for p in previous['packages']}
            for row in previous['files']:
                dep=deps[row['package']]
                if dep['role']=='extension':previous_owner[row['group']]=dep['volume']
        data_cache={};rows_cache={}
        def data(name):
            if name not in data_cache:data_cache[name]=payload(replacements[name])if name in replacements else reader.file_payload(name)
            return data_cache[name]
        def rows(group):
            if group not in rows_cache:rows_cache[group]=[dict(path=n,entry=n,group=group,priority=0,**metadata(data(n)))for n in groups[group]]
            return rows_cache[group]
        allocations={g:previous_owner[g]for g in groups if g in previous_owner}
        with zipfile.ZipFile(game)as physical_archive:physical=set(physical_archive.namelist())
        def main_plan():
            moved={n for g in allocations for n in groups[g]}
            replacements_main={n:v for n,v in replacements.items()if n not in moved and n!=rs.MANIFEST}
            removals=moved&physical
            measurement=estimate(game,replacements_main,remove_entries=removals,extra_sizes={rs.MANIFEST:manifest_reserve})
            return replacements_main,sorted(removals),measurement
        main_changes,remove,measurement=main_plan()
        candidates=sorted((g for g in groups if g not in allocations),key=lambda g:(-sum((metadata(replacements[n])['bytes']if n in replacements else reader.getinfo(n).file_size)for n in groups[g]),g))
        while measurement['signed_upper_bound']>=limit:
            if type(template_bytes)is not int or template_bytes<=0:raise ValueError('扩展资源规划需要实际资源APK模板的封装大小，不能估猜')
            if not candidates:raise ValueError('游戏启动代码与不可迁移资源仍超过单APK容量；没有可安全拆分的完整容器')
            group=candidates.pop(0);incoming=rows(group)
            if _volume_size(incoming,template_bytes=template_bytes)>=limit:
                raise ValueError('单个完整资源组超过扩展APK容量，不能切断UCAS：'+group)
            volumes=sorted(set(allocations.values()));assigned=None
            for volume in volumes:
                contents=[r for g,v in allocations.items()if v==volume for r in rows(g)]
                if _volume_size(contents+incoming,template_bytes=template_bytes)<limit:assigned=volume;break
            if assigned is None:assigned=next(v for v in range(1,128)if v not in volumes)
            allocations[group]=assigned;main_changes,remove,measurement=main_plan()
        # A previously stable volume can grow after texture edits. Keep its
        # groups in place where possible, but spill complete groups when full.
        for volume in sorted(set(allocations.values())):
            if type(template_bytes)is not int or template_bytes<=0:raise ValueError('继承扩展资源需要实际资源APK模板的封装大小')
            members=sorted(g for g,v in allocations.items()if v==volume);kept=[]
            for group in members:
                incoming=rows(group)
                if _volume_size(incoming,template_bytes=template_bytes)>=limit:raise ValueError('单个完整资源组超过扩展APK容量：'+group)
                if _volume_size(kept+incoming,template_bytes=template_bytes)>=limit:
                    new=next(v for v in range(1,128)if v not in allocations.values());allocations[group]=new
                else:kept+=incoming
        deps=[dict(id='game',role='game',package=game_package,version_code=game_version,signers=sorted(signers))]
        deps.extend(copy.deepcopy(list(offline_dependencies)));files=[];volumes=[]
        for volume in sorted(set(allocations.values())):
            content=[r for g,v in allocations.items()if v==volume for r in rows(g)]
            aliases={}
            for row in content:
                key=(row['bytes'],row['sha256']);aliases[key]=min(aliases.get(key,row['entry']),row['entry'])
            for row in content:row['entry']=aliases[(row['bytes'],row['sha256'])]
            content_sha=rs.volume_content(content);key=f'v{volume:03d}'
            dep=dict(id=key,role='extension',package=rs.extension_package(game_package,volume,content_sha),version_code=1,
                     signers=sorted(signers),content_sha256=content_sha,volume=volume)
            for row in content:row['package']=key
            deps.append(dep);files.extend(content)
            reuse=None
            if previous:
                old=next((p for p in previous['packages']if p['role']=='extension'and p['volume']==volume and p['content_sha256']==content_sha and set(p['signers'])==set(signers)and p['package']==dep['package']),None)
                if old:reuse=reader.archives[old['id']].filename
            volumes.append(dict(dependency=dep,files=content,payloads={r['entry']:data(r['path'])for r in content},
                                signed_upper_bound=_volume_size(content,template_bytes=template_bytes),reuse_path=reuse))
        # The credential binds every supported final resource, including
        # containers/CPK that fit in the main APK. A game-only numeric edit
        # must never reuse a resource receipt for the previous CPK bytes.
        for group in groups:
            if group in allocations:continue
            files.extend([dict(row,package='game')for row in rows(group)])
        manifest=rs.build(dict(package=game_package,family=family),deps,files)
        main_changes,remove,_=main_plan();main_changes[rs.MANIFEST]=rs.canonical(manifest)
        final=estimate(game,main_changes,remove_entries=remove)
        if final['signed_upper_bound']>=limit:raise ValueError('最终资源清单写入后游戏APK超限，请增加清单预留后重新规划')
        return dict(manifest=manifest,main_replacements=main_changes,remove_entries=remove,volumes=volumes,
                    main_size=final,limit=limit,template_bytes=template_bytes,
                    source_resource_set=previous['set_id']if previous else None)
    finally:
        if owned:reader.close()
