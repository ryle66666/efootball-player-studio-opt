"""Force-check retained workshop-owned card textures, never stock assets.

This operates on cooked private pixels, independently of local project files,
the unchanged-design filter, and full/incremental selection. Existing compressed
textures and failed quality checks remain byte-identical.
"""
from pathlib import Path,PurePosixPath
import copy,re,struct
from export_payload import FilePayload,extract
from native_iostore import Container,package_id,read_package_header,package_header,append_container

REVISION='owned-card-cooked-compression-v1'
PREFIX='assets/gateway-release/'
FACE='/Game/Assets/character/RealFace/'
PORTRAIT='/Game/Assets/ui/Data/Thumbnail/Player/'


def owners_for_export(players,inherited,identities=(),bindings=()):
    """Reuse permanent-identity validation, with only the final retained cards."""
    from personal_source import ownership
    routes={int(row['cardId']):copy.deepcopy(row)for row in inherited.get('native_binding',{}).get('routes',[])}
    for identity,binding in zip(identities,bindings):
        routes[int(identity['card_id'])]={'cardId':identity['card_id'],'portraitKey':int(binding['asset_key'])}
    document=dict(inherited,players=list(players),native_binding={'routes':list(routes.values())})
    return ownership(document)


def texture_owner(name,owners):
    """Accept dedicated numeric namespaces only, excluding bound stock people."""
    if name.startswith(FACE):
        match=re.fullmatch(r'T_(\d+)_(.+)',name[len(FACE):])
        if not match:return None
        token=int(match[1]);channel=match[2]
    elif name.startswith(PORTRAIT):
        stem=name[len(PORTRAIT):].removeprefix('Variation2022/')
        match=re.fullmatch(r'(\d+)(?:_.+|_|f)',stem)
        if not match:return None
        token=int(match[1]);channel=None
    else:return None
    for uid,owner in owners.items():
        identity=owner['identity'];private={int(owner['asset_key']),int(identity.get('independent_person_id',identity['person_id']))}
        private={key for key in private if 8000000<=key<9000000 or 90000000<=key<99000000}
        # ownership() has separately verified all historical owned card IDs.
        portraits=private|{int(key)for key in owner['aliases']if int(key)>=2**28}
        if token not in(private if channel is not None else portraits):continue
        if channel is None:usage='portrait'
        elif channel in('Face_D','ArmLeg_D'):usage='skin'
        elif channel in('Hair_D','Hair_High_D'):usage='mask'
        elif channel.endswith('_N'):usage='normal'
        elif 'SRO'in channel:usage='sro'
        else:usage='unknown'
        return {'owner':uid,'usage':usage,'alpha_cutoff':.3333 if usage=='mask'else .5}
    return None


def _logical(path):
    prefix='../../../PesMobile/Content/'
    return '/Game/'+path[len(prefix):-7]if path.startswith(prefix)and path.endswith('.uasset')else None


def recompress_archive(archive,replacements,owners,key,work,*,cache_dir=None,native_folder=None,progress=lambda message:None):
    """Rebuild affected complete IoStore pairs before live hashes are reserved.

    Each changed texture is staged separately; unchanged package data is copied
    by the existing container compactor without editing meshes/materials/tables.
    """
    from content_autofill import Overlay
    from custom_texture_recompression import recompress
    report={'revision':REVISION,'forced':True,'scope':'final_retained_workshop_cards_only','cards':len(owners),
        'textures':[],'containers':[],'changed_textures':0,'original_cards_modified':False,
        'requires_local_projects':False,'quality_gate_retained':True,'lossy_recompression':False}
    if not owners:return {},report
    folder=Path(work)/'owned-card-compression';folder.mkdir()
    updates={};view=Overlay(archive,replacements);known=set(view.namelist())
    members=sorted(name for name in known if re.fullmatch(r'assets/pc\d{4,5}_mobile_and(?:_optional)?\.utoc',name)or
        re.fullmatch(re.escape(PREFIX)+r'(?:livedata|custom-resources/legends-v10)/pc\d{4,5}_mobile_and(?:_optional)?\.utoc',name))
    for number,member in enumerate(members):
        toc=folder/(str(number)+'.utoc');extract(view,member,toc);container=Container(toc,key)
        candidates=[]
        for path,index in container.paths.items():
            name=_logical(path);owner=texture_owner(name,owners)if name else None
            if owner:candidates.append((path,index,name,owner))
        if not candidates:continue
        ucas=member[:-5]+'.ucas'
        if ucas not in known:raise ValueError('自建卡资源缺少配套 UCAS：'+member)
        data=view.file_payload(ucas)
        if not isinstance(data,FilePayload):data=extract(view,ucas,folder/(str(number)+'.source.ucas'))
        container.data=data
        header_index=next(i for i,cid in enumerate(container.chunk_ids)if cid[11]==10)
        original_header=container.read_chunk(header_index);cid,entries=read_package_header(original_header,strict=False)
        canonical=package_header(cid,entries)
        if original_header!=canonical:
            if not original_header.startswith(canonical)or any(original_header[len(canonical):]):
                raise ValueError('自建卡资源容器含未适配包重定向，未修改：'+member)
            read_original=container.read_chunk
            container.read_chunk=lambda index,_read=read_original,_head=canonical,_index=header_index:_head if index==_index else _read(index)
        metadata={entry['id']:entry for entry in entries};additions=[];paths={};packages=[];indices=set();visited=set()
        for ordinal,(path,index,name,owner)in enumerate(candidates,1):
            if index in visited:continue
            visited.add(index);chunk=container.chunk_ids[index];pid=int.from_bytes(chunk[:8],'little')
            if chunk[11]!=2 or pid!=package_id(name)or pid not in metadata:
                raise ValueError('自建卡贴图包身份与索引不一致：'+name)
            progress('检查并压缩自建卡贴图：'+str(ordinal)+' / '+str(len(candidates)))
            declared=container.ranges[index][1]
            if declared>129*1024**2:
                report['textures'].append({'owner':owner['owner'],'container':member,'package':name,
                    'package_id':pid,'changed':False,'status':'preserved','reason':'private_package_size_limit',
                    'source_package_bytes':declared,'result_package_bytes':declared})
                continue
            original=container.read_chunk(index)
            encoded,item=recompress(original,name,usage=owner['usage'],alpha_cutoff=owner['alpha_cutoff'],
                cache_dir=cache_dir,native_folder=native_folder)
            item=dict(item,owner=owner['owner'],container=member);report['textures'].append(item)
            if not item['changed']:continue
            staged=folder/(str(number)+'-'+str(index)+'.uasset');staged.write_bytes(encoded)
            additions.append((chunk,FilePayload.from_path(staged)));paths[path.removeprefix('../../../')]=len(additions)-1
            packages.append(metadata[pid]);indices.add(index);report['changed_textures']+=1
        if not additions:continue
        output=folder/(str(number)+'-compressed.utoc')
        rebuilt=append_container(container,output,additions,paths,packages,replace_indices=indices)
        # Only the exact accepted private texture exports and store header may change.
        for old_index,old_chunk in enumerate(container.chunk_ids):
            if old_index in indices or old_index==header_index:continue
            new_index=rebuilt.by_id[old_chunk]
            if container.metas[old_index*33:(old_index+1)*33]!=rebuilt.metas[new_index*33:(new_index+1)*33]:
                raise ValueError('压缩影响了非目标资源的校验记录')
        updates[member]=FilePayload.from_path(output);updates[ucas]=FilePayload.from_path(output.with_suffix('.ucas'))
        report['containers'].append({'path':member,'changed_packages':len(additions),'source_ucas_bytes':data.bytes,
            'result_ucas_bytes':updates[ucas].bytes,'unrelated_chunk_metadata_preserved':True})
    report['source_package_bytes']=sum(row['source_package_bytes']for row in report['textures'])
    report['result_package_bytes']=sum(row['result_package_bytes']for row in report['textures'])
    return updates,report
