"""Finalize a locally derived catalogue source without flattening its APK set.

This is an importable source, not a ready-to-install game: the regular game
compiler still refreshes live-data/runtime contracts before actual delivery.
"""
import copy,hashlib,json,uuid,zipfile
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from export_payload import metadata,verify_replacements
from apk_format import replace_entries,sign_v2,verify_v2,UINT32_LIMIT
from apk_installation import identity,coexist_entries
import resource_set as rs
import resource_planner as planner
import resource_package as packaging


class ContentReader(rs.Reader):
    # Retain the base Reader metadata cap. Existing business parsers opt in
    # explicitly through archive_data; container extraction always uses open.
    pass


def matches(archive,name,raw):
    if archive.getinfo(name).file_size!=len(raw):return False
    with archive.open(name)as stream:return hashlib.file_digest(stream,'sha256').hexdigest()==hashlib.sha256(raw).hexdigest()


def existing_metadata(archive,name):
    if name not in archive.namelist():return None
    return metadata(archive.file_payload(name))


def finalize(archive,replacements,output,work,receipt,*,signer=None,resource_template=None,limit=UINT32_LIMIT,manifest_reserve=256*1024):
    """Return the receipt plus source_bundle/resource_packages when signed.

    An old caller without a signer retains the legacy unsigned-single-source
    contract. Production import passes its local signer and original offline
    template, publishing the returned source-set directory as one unit.
    """
    output=Path(output);work=Path(work);replacements=dict(replacements)
    if signer is None:
        if archive.document:raise ValueError('扩展增量来源需要本地签名与配套资源上下文，不能回退为单个未签名APK')
        replace_entries(archive.filename,output,replacements,store_ucas=True)
        with zipfile.ZipFile(output)as check:verify_replacements(check,replacements)
        return receipt
    key,certificate=signer;signer_sha=hashlib.sha256(certificate.public_bytes(serialization.Encoding.DER)).hexdigest()
    original=identity(archive.filename);bundle=output.parent/'source-set'
    if bundle.exists():raise ValueError('增量资源集暂存目录已存在')
    bundle.mkdir();dependencies=[];packages={};new_files=[]
    if archive.document:
        offline=[p for p in archive.document['packages']if p['role']=='offline']
        candidates=[(p,Path(archive.archives[p['id']].filename))for p in offline]
    elif resource_template:
        info=identity(resource_template)
        with zipfile.ZipFile(resource_template)as base,base.open('assets/resource-bootstrap/resources.zip')as stream:
            content_sha=hashlib.file_digest(stream,'sha256').hexdigest()
        candidates=[(dict(id='offline',role='offline',package=info['package'],version_code=info['version_code'],
                          signers=info['signers'],content_sha256=content_sha),Path(resource_template))]
    else:raise ValueError('签名增量来源需要明确的原离线资源APK，不能估猜配套内容')
    for dep,path in candidates:
        actual=identity(path);required=copy.deepcopy(dep);required['signers']=[signer_sha]
        if actual['package']!=dep['package']or actual['version_code']!=dep['version_code']:raise ValueError('增量原离线来源身份不匹配')
        if actual['signers']!=[signer_sha]:
            unsigned=bundle/(dep['id']+'.unsigned');target=bundle/(dep['id']+'.apk')
            with zipfile.ZipFile(path)as base:changes,_=coexist_entries(base,'update')
            prepared=replace_entries(path,unsigned,changes,prepare_signing=True)
            sign_v2(unsigned,target,key,certificate,prepared=prepared,consume=True);verify_v2(target)
            path=target;new_files.append(target.name)
        dependencies.append(required);packages[required['id']]=path
    template=Path(resource_template)if resource_template else candidates[0][1]
    if archive.document:family=archive.document['game']['family']
    else:
        from apk_installation import COEXIST_GAME
        family='coexist'if original['package']==COEXIST_GAME else'standard'
    plan=planner.plan(archive.filename,replacements,reader=archive,limit=limit,game_package=original['package'],
          family=family,signers=[signer_sha],game_version=original['version_code'],offline_dependencies=dependencies,
          template_bytes=packaging.template_size(template),manifest_reserve=manifest_reserve)
    for volume in plan['volumes']:
        dep=volume['dependency'];path=Path(volume['reuse_path'])if volume.get('reuse_path')else bundle/(dep['id']+'.apk')
        if not volume.get('reuse_path'):
            packaging.build_volume(template,volume,path,key,certificate,limit=limit);new_files.append(path.name)
        packages[dep['id']]=path
    changes=plan['main_replacements'];prefix='assets/gateway-release/';release_path=prefix+'release-manifest.json'
    release=json.loads(changes[release_path]if release_path in changes else archive.read(release_path))
    managed={row['path']for row in plan['manifest']['files']}
    release['files']=[r for r in release['files']if prefix+r['path']not in managed]
    rs.verify_copy_manifest(plan['manifest'],release)
    changes[release_path]=rs.canonical(release)
    measured=planner.estimate(archive.filename,changes,remove_entries=plan['remove_entries'])
    if measured['signed_upper_bound']>=limit:raise ValueError('增量来源控制清单更新后主APK超限，未发布资料库')
    unsigned=bundle/(output.name+'.unsigned');game=bundle/output.name
    prepared=replace_entries(archive.filename,unsigned,changes,remove_entries=plan['remove_entries'],store_ucas=True,prepare_signing=True)
    sign_v2(unsigned,game,key,certificate,prepared=prepared,consume=True);verify_v2(game)
    if game.stat().st_size>=limit:raise ValueError('签名后增量主APK超限，未发布资料库')
    with rs.Reader(game,packages)as complete:
        checked=complete.verify();expected=dict(replacements);expected[release_path]=changes[release_path]
        verify_replacements(complete,expected)
        rs.verify_copy_manifest(complete.document,json.loads(complete.read(release_path)))
    new_files.append(game.name)
    result=copy.deepcopy(receipt)
    result.update(resource_packages={k:str(v)for k,v in packages.items()},
                  source_bundle=dict(directory=str(bundle),game=str(game),new_files=new_files),
                  derived_source=dict(kind='signed-local-catalogue-source',installable_game=False,
                    original_package=original['package'],original_signers=original['signers'],signer=signer_sha,
                    resource_set_id=plan['manifest']['set_id'],verified=checked))
    return result
