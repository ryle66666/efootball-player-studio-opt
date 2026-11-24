"""Build and publish a complete, verified game/resource set on one volume.

Only generated files enter the private staging directory. Existing immutable
dependencies are referenced and verified, never overwritten or uninstalled.
"""
from pathlib import Path
import copy,hashlib,json,os,struct,time,uuid,zipfile
from urllib.parse import quote
from cryptography.hazmat.primitives import serialization
from apk_format import local_signer,replace_entries,sign_v2,verify_v2,UINT32_LIMIT,SIGNING_RESERVE
from apk_installation import identity,coexist_entries,GAME,RESOURCES,COEXIST_GAME,COEXIST_RESOURCES
from export_payload import measure,identity as stat_identity
import resource_set as rs
import resource_planner as planner
import resource_package as package

PREFIX='assets/gateway-release/'
OFFLINE_MEMBER='assets/resource-bootstrap/resources.zip'


def _offline_digest(path):
    with zipfile.ZipFile(path)as archive,archive.open(OFFLINE_MEMBER)as stream:return measure(stream)['sha256']


def _offline(store,template,game_package,signer,key,certificate,staging,progress,game):
    """Reuse a verified pair, otherwise apply the existing resource signing flow."""
    template=Path(template);original=identity(template)
    target={GAME:RESOURCES,COEXIST_GAME:COEXIST_RESOURCES}.get(game_package)
    if target is None:
        from install_pairs import expected_resources
        target=expected_resources({'package':game_package,'path':str(game)},[original])
    progress('核对原离线资源内容及配套签名')
    content=_offline_digest(template)
    dependency=dict(id='offline',role='offline',package=target,version_code=original['version_code'],signers=[signer],content_sha256=content)
    cache=store.root/'resource-set-exports.json'
    records=json.loads(cache.read_text('utf8'))if cache.is_file()else{'offline':{}}
    cache_key=rs.digest(dependency)
    candidates=[template]
    prior=records.get('offline',{}).get(cache_key,{})
    if prior.get('path'):candidates.append(Path(prior['path']))
    for candidate in candidates:
        if not candidate.is_file():continue
        actual=identity(candidate)
        if actual['package']!=target or actual['version_code']!=dependency['version_code']or actual['signers']!=[signer]:continue
        # The final Reader verifies the actual full signed contents. A prior
        # cache label never replaces that verification.
        return dependency,candidate,False,records,cache_key
    progress('沿用配套资源生成流程，保留原离线资源内容')
    mode='coexist'if target==COEXIST_RESOURCES else'update'
    output=staging/'73efootball-配套离线资源.apk';unsigned=staging/'offline.unsigned'
    with zipfile.ZipFile(template)as archive:changes,_=coexist_entries(archive,mode)
    built=replace_entries(template,unsigned,changes,prepare_signing=True)
    sign_v2(unsigned,output,key,certificate,prepared=built,consume=True)
    actual=identity(output);verified=verify_v2(output)
    if actual['package']!=target or actual['version_code']!=dependency['version_code']or verified['signer_sha256']!=signer:
        raise ValueError('生成的原离线配套资源身份不一致')
    if output.stat().st_size>=UINT32_LIMIT:raise ValueError('原离线资源 APK 超出单包容量，未发布')
    return dependency,output,True,records,cache_key


def _release_copy_list(changes,manifest):
    """The legacy Gateway copies only main-APK files, after our early prepare."""
    path=PREFIX+'release-manifest.json'
    if path not in changes:raise ValueError('资源集导出缺少最终发布清单')
    release=json.loads(changes[path]);managed={r['path']for r in manifest['files']}
    external={r['path']for r in manifest['files']if r['package']!='game'}
    release['files']=[row for row in release['files']if PREFIX+row['path']not in managed]
    release['resource_set']={'protocol':rs.PROTOCOL,'set_id':manifest['set_id'],'external_files':len(external),'managed_files':len(managed)}
    rs.verify_copy_manifest(manifest,release)
    changes[path]=rs.canonical(release)


def _installation_status(library,row):
    """Report actual installed identities, without installing or clearing data."""
    if row['kind']=='game':return 'needs_install'
    from apk_sources import _installed
    dep=row['dependency']
    try:installed=_installed(library,dep['package'])
    except Exception:return 'reused'if row['reused']else'needs_install'
    actual=next((r for r in installed.get('rows',[])if r.get('package')==dep['package']),None)
    if actual is None:return 'reused'if row['reused']else'needs_install'
    if set(actual.get('signers')or[])!=set(dep['signers']):return 'signature_mismatch'
    if actual.get('version_code')!=dep['version_code']:return 'version_mismatch'
    path=actual.get('path')
    if not path or not Path(path).is_file():return 'needs_install'
    try:
        if row['kind']=='companion':content=_offline_digest(path)
        else:
            with zipfile.ZipFile(path)as archive:
                info=archive.getinfo(rs.VOLUME_MANIFEST)
                if info.file_size>rs.MAX_MANIFEST:return 'version_mismatch'
                description=json.loads(archive.read(info));content=description.get('package',{}).get('content_sha256')
        return 'installed_match'if content==dep['content_sha256']else'version_mismatch'
    except (OSError,ValueError,KeyError,zipfile.BadZipFile):return 'version_mismatch'


def _destination(store):
    return store.root/'exports'/('APK-资源集-'+time.strftime('%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:8])


def _records(store):
    path=store.root/'resource-set-exports.json'
    return json.loads(path.read_text('utf8'))if path.is_file()else{}


def _game_visibility(raw,manifest):
    """Declare exact immutable dependency packages; never use broad visibility."""
    from android_manifest import Manifest
    document=Manifest(raw);required=sorted({p['package']for p in manifest['packages']if p['role']!='game'})
    existing={a['value']for _,tag,attrs in document.nodes()if tag=='package'for a in attrs if a['name']=='name'}
    missing=sorted(set(required)-existing)
    if missing:document.add_queries(missing)
    # add_queries appends a block. Re-exported sources already have one, so
    # merge their actual child records into a single valid queries element.
    ranges=[];start=None;depth=0
    for index,part in enumerate(document.chunks):
        kind=struct.unpack_from('<H',part)[0]
        if kind not in(0x102,0x103):continue
        tag=document.string(struct.unpack_from('<I',part,20)[0])
        if kind==0x102:
            if tag=='queries'and start is None:start=index;depth=0
            elif start is not None:depth+=1
        elif start is not None:
            if tag=='queries'and depth==0:ranges.append((start,index));start=None
            else:depth-=1
    if len(ranges)>1:
        first,last=ranges[0];children=[p for begin,end in ranges for p in document.chunks[begin+1:end]]
        merged=[document.chunks[first],*children,document.chunks[last]];discard={i for begin,end in ranges for i in range(begin,end+1)}
        document.chunks=[p for i,p in enumerate(document.chunks)if i not in discard]
        document.chunks[first:first]=merged
    document.add_query_intent(rs.DISCOVERY_ACTION)
    # AXML parsers resolve android:name by resource id, not just string text.
    # Preserve the existing map and populate it if a minimal/older source had
    # never used the name attribute before adding dependency queries.
    package._resource_map(document,{document.index('name'):0x01010003})
    result=document.bytes();check=Manifest(result)
    actual={a['value']for _,tag,attrs in check.nodes()if tag=='package'for a in attrs if a['name']=='name'}
    if not set(required)<=actual:raise ValueError('游戏资源依赖可见性声明不完整')
    return result,{'exact_packages':required,'discovery_action':rs.DISCOVERY_ACTION,'added_query_all_packages':False}


def _prepare_single(store,game,replacements,work,*,game_name,installation,measured,progress,limit,
                    remove_entries=(),content=None,logical_changes=None):
    """The old single-APK route needs no local copy of installed offline data."""
    from native_live import verify_apk
    key,certificate=local_signer(store.root/'signing')
    staging=Path(work)/'complete-resource-set';staging.mkdir()
    output=staging/game_name;unsigned=staging/'game.unsigned'
    packages={};content_artifact=None
    if content:
        content_path=staging/content['filename'];os.replace(content['path'],content_path)
        packages['content_pack']=str(content_path)
        content_artifact=dict(kind='content_pack',path=str(content_path),dependency=None,reused=False,status='manual_import')
    progress('生成游戏程序 APK 和内容包'if content else'生成游戏 APK')
    build=replace_entries(game,unsigned,replacements,store_ucas=True,prepare_signing=True,remove_entries=remove_entries)
    performance=sign_v2(unsigned,output,key,certificate,prepared=build,consume=True)
    if output.stat().st_size>=limit:raise ValueError('游戏 APK 签名后超限，未发布成品')
    signature_stamp=stat_identity(output.stat())
    signature=verify_v2(output)
    if stat_identity(output.stat())!=signature_stamp:raise ValueError('游戏 APK 在签名核对期间发生变化')
    live=verify_apk(output,logical_changes or replacements,resource_packages=packages)
    actual=identity(output)
    if actual['package']!=installation['package']or actual['signers']!=[signature['signer_sha256']]:raise ValueError('游戏 APK 的安装身份或签名不一致')
    publication_id=uuid.uuid4().hex
    artifact=dict(kind='game',path=str(output),dependency=dict(id='game',role='game',package=actual['package'],
        version_code=actual['version_code'],signers=actual['signers']),reused=False,status='needs_install')
    artifacts=[artifact]+([content_artifact]if content_artifact else[])
    stamps={str(output):signature_stamp}
    if content_artifact:stamps[content_artifact['path']]=stat_identity(Path(content_artifact['path']).stat())
    return dict(staging=staging,destination=_destination(store),game=output,artifacts=artifacts,packages=packages,
        signature=signature,signing_performance=performance,apk_build=build,live=live,manifest=None,
        publication_id=publication_id,signature_stamps={str(output):signature_stamp},offline_records=_records(store),
        loader={'required':bool(content),'changed':bool(content),'mode':'program-content'if content else'legacy-single-apk'},
        report={'mode':'program-content'if content else'single-apk','required':bool(content),'main_size':measured,'removed_main_entries':sorted(remove_entries),
                'extension_volumes':[],'source_resource_set':None,'limit':limit,'atomic_publication':True,'in_game_tested':False},
        stamps=stamps)


def prepare(store,library,game,replacements,work,*,game_name,installation,resource_packages=None,
            companion=None,profile_resources=None,progress=lambda message:None,limit=UINT32_LIMIT,measured=None,
            remove_entries=(),content=None,logical_changes=None):
    """Prepare one program APK, optionally paired with its data package."""
    if type(limit)is not int or not SIGNING_RESERVE<limit<=UINT32_LIMIT:raise ValueError('资源APK容量上限无效')
    options={'remove_entries':remove_entries}if remove_entries else{}
    measured=planner.estimate(game,replacements,**options)if measured is None else measured
    if not isinstance(measured,dict)or type(measured.get('signed_upper_bound'))is not int or measured['signed_upper_bound']<0:
        raise ValueError('资源APK容量规划无效')
    if measured['signed_upper_bound']>=limit:
        raise ValueError('程序本体仍超过 Android APK 容量上限，无法生成可安装的程序；未生成扩展资源 APK')
    options=dict(game_name=game_name,installation=installation,measured=dict(measured),progress=progress,limit=limit)
    if remove_entries:options['remove_entries']=remove_entries
    if content:options.update(content=content,logical_changes=logical_changes)
    return _prepare_single(store,game,replacements,work,**options)


def publish(store,prepared,report,*,registry_updates=None,defer_commit=False):
    """Publish the set and all small registries as one recoverable transaction."""
    import export_publication as transaction
    staging=prepared['staging'];destination=prepared['destination']
    final_game=destination/prepared['game'].name
    report['output']=str(final_game)
    def final_paths(value):
        if isinstance(value,dict):return {k:final_paths(v)for k,v in value.items()}
        if isinstance(value,list):return [final_paths(v)for v in value]
        if isinstance(value,str):
            try:path=Path(value)
            except (OSError,ValueError):return value
            if path.is_absolute()and path.parent==staging:return str(destination/path.name)
        return value
    final_resource_report=final_paths(prepared['report']);report['resource_set']=final_resource_report
    artifacts=[]
    for original in prepared['artifacts']:
        row=copy.deepcopy(original);old=Path(row['path']);path=destination/old.name if old.parent==staging else old
        row.update(path=str(path),size=old.stat().st_size)
        try:relative=path.resolve().relative_to((store.root/'exports').resolve()).as_posix()
        except ValueError:relative=None
        row['name']=relative;row['url']='/exports/'+quote(relative)if relative else None
        row.setdefault('status','reused'if row['reused']else'needs_install');artifacts.append(row)
    report['artifacts']=artifacts
    report_path=staging/(prepared['game'].stem+'.report.json');report_path.write_bytes(rs.canonical(report))
    records=copy.deepcopy(prepared['offline_records'])
    if prepared['manifest']is not None:
        offline=next(a for a in artifacts if a['kind']=='companion')
        records.setdefault('offline',{})[prepared['offline_key']]={'path':offline['path'],'dependency':prepared['offline_dependency']}
        final_packages={}
        for row in artifacts:
            if row['kind']=='game':continue
            dep=row['dependency'];final_packages[dep['id']]=final_packages[dep['package']]=row['path']
        binding={'game':str(final_game),'set_id':prepared['manifest']['set_id'],'resource_packages':final_packages,'verified':True}
        records.setdefault('sets',{})[prepared['manifest']['set_id']]=binding;records.setdefault('games',{})[str(final_game)]=binding
    else:
        records.setdefault('artifacts',{})[str(final_game)]={'game':str(final_game),'mode':prepared['report']['mode'],
                                                           'publication_id':prepared['publication_id'],'verified':True}
    updates=dict(registry_updates or{});updates[store.root/'resource-set-exports.json']=rs.canonical(records)
    generated={p.name for p in staging.iterdir()if p.suffix in('.apk','.73pack')}|{report_path.name,'complete.json'}
    transaction.begin(store,updates,destination=destination,game_name=prepared['game'].name,
                      set_id=prepared['publication_id'],generated_files=generated)
    def validated():
        for name,stamp in prepared['stamps'].items():
            if stat_identity(Path(name).stat())!=stamp:raise ValueError('成品或复用依赖在发布前发生变化')
        return dict(verified=True,set_id=prepared['publication_id'],resource_set_id=(prepared['manifest']or{}).get('set_id'),
                    mode=prepared['report']['mode'],live_data_verified=prepared['live']['verified'])
    published=False
    try:
        package.atomic_publish(staging,destination,verify=validated,limit=prepared['report']['limit'],
                               verified_apks=prepared.get('signature_stamps'))
        published=True
        transaction.apply(store)
        if not defer_commit:transaction.finish(store)
    except BaseException as exc:
        try:transaction.rollback(store,preserve_files=published)
        except Exception as recovery_error:
            raise RuntimeError('资源集发布失败：'+str(exc)+'；元数据恢复待重试：'+str(recovery_error))from exc
        if not published:raise
        report.setdefault('warnings',[]).append({'code':'registry_deferred','item':'publication',
            'message':'成品已生成，资料库记录暂未更新，下次生成时会重新读取。',
            'stage':'publication','detail':str(exc),'error_type':type(exc).__name__})
    game=next(a for a in artifacts if a['kind']=='game')
    return dict(path=str(final_game),url=game['url'],name=game['name'],size=final_game.stat().st_size,
                artifacts=artifacts,resource_set=final_resource_report,report=report,report_path=str(destination/report_path.name))


def discard_published(store,prepared):
    """Roll back only this newly created set if the source guard rejects it."""
    folder=prepared['destination'];root=(store.root/'exports').resolve()
    if not folder.exists():return
    if folder.is_symlink()or folder.is_junction()or folder.resolve().parent!=root or not folder.name.startswith('APK-资源集-'):
        raise ValueError('拒绝清理未确认归属的资源集路径')
    complete=folder/'complete.json';value=json.loads(complete.read_text('utf8'))
    if value.get('validation',{}).get('set_id')!=prepared['publication_id']:raise ValueError('资源集清理凭据不一致')
    names={p['name']for p in value.get('files',[])}|{'complete.json',prepared['game'].stem+'.report.json'}
    # Every resolved final path is checked before any deletion. Never recurse,
    # and preserve reused dependencies outside this one newly published folder.
    paths=[]
    for name in names:
        if Path(name).name!=name:raise ValueError('资源集清理文件名无效')
        path=folder/name
        if path.is_symlink()or path.is_junction()or path.resolve().parent!=folder.resolve():raise ValueError('资源集清理路径越界')
        paths.append(path)
    for path in paths:path.unlink(missing_ok=True)
    if not any(folder.iterdir()):folder.rmdir()
    cache=store.root/'resource-set-exports.json'
    if cache.is_file():
        data=json.loads(cache.read_text('utf8'));game=str(folder/prepared['game'].name)
        data.get('games',{}).pop(game,None)
        data.get('artifacts',{}).pop(game,None)
        for key,row in list(data.get('sets',{}).items()):
            if row.get('game')==game:data['sets'].pop(key)
        for key,row in list(data.get('offline',{}).items()):
            if Path(row.get('path','')).parent==folder:data['offline'].pop(key)
        temp=cache.with_suffix('.tmp');temp.write_bytes(rs.canonical(data));temp.replace(cache)
