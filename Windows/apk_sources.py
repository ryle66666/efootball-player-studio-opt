"""The current library's imported APKs are authoritative export inputs."""
from pathlib import Path
from contextlib import contextmanager
import copy,hashlib,json,os,re,struct,threading,uuid,zipfile

MARKERS={'apk':'assets/gateway-release/release-manifest.json','companion':'assets/resource-bootstrap/resources.zip'}
WORKSHOP_DOCUMENT='assets/gateway-release/custom-resources/personal-player-studio/players.json'
IDENTITY_REVISION='verified-apk-source-v1'
_SOURCE_LOCK=threading.RLock()
_CHECKED={}
_VERIFY_LOCKS={}


def _read(path):
    try:
        value=json.loads(Path(path).read_text(encoding='utf8'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}


def recorded(library,store):
    saved=_read(store.root/'apk-source-identities.json').get(_library_key(library))
    if isinstance(saved,dict)and not _database(library) and(saved.get('apk')or saved.get('companion')):return saved
    active=_read(store.root/'active-game.json')
    if active.get('apk') or active.get('companion'):return active
    release=getattr(library,'profile',{}).get('release_id')
    for folder,name in ((getattr(library,'resources',None),'import-report.json'),(getattr(library,'root',None),'ready.json')):
        if folder is None:continue
        row=_read(Path(folder)/name)
        if (row.get('apk') or row.get('companion')) and (not release or row.get('release_id')==release):return row
    return {}


def path_for_resources(resources,key='apk'):
    """Read the current source only for this exact imported database.

    Historical import reports remain immutable evidence, including their old
    APK hash/path. Legacy catalogue repair may use the active verified path
    without rewriting that report or importing the database again.
    """
    resources=Path(resources)
    if key not in MARKERS:raise ValueError('来源 APK 类型无效')
    if re.fullmatch('[a-f0-9]{24}',resources.name):
        active=_read(resources.parent.parent/'active-game.json')
        if active.get('database')==resources.name and active.get(key):return active[key]
    value=_read(resources/'import-report.json').get(key)
    if value:return value
    raise ValueError('当前资料库缺少'+('主程序'if key=='apk'else'离线资源')+' APK 来源记录')


def packages_for_resources(resources):
    """Read already committed dependency paths only; never scan/hash/reimport."""
    resources=Path(resources);record={}
    if re.fullmatch('[a-f0-9]{24}',resources.name):
        active=_read(resources.parent.parent/'active-game.json')
        if active.get('database')==resources.name:record=active
    if not record:record=_read(resources/'import-report.json')
    result={}
    for dependency in record.get('resource_dependencies',[]):
        if isinstance(dependency,dict)and dependency.get('package')and dependency.get('path'):
            result[dependency['package']]=dependency['path']
            if dependency.get('id'):result[dependency['id']]=dependency['path']
    return result


def _database(library):
    name=Path(getattr(library,'resources','.')).name
    return name if re.fullmatch('[a-f0-9]{24}',name)else None


def _library_key(library):
    profile=getattr(library,'profile',{})
    return hashlib.sha256(json.dumps([_database(library),profile.get('release_id'),
        profile.get('cache_namespace'),profile.get('native_engine_sha256')],sort_keys=True).encode()).hexdigest()[:24]


def file_stamp(path):
    p=Path(path).absolute();s=p.stat()
    return {'path':str(p),'bytes':s.st_size,'mtime_ns':s.st_mtime_ns,'ctime_ns':s.st_ctime_ns,
            'inode':s.st_ino,'device':s.st_dev}


def _family(value):return str(value).split('-studio-',1)[0]


def _trusted(value):
    return (isinstance(value,dict)and value.get('revision')==IDENTITY_REVISION and
        value.get('verification')=='apk-v2-content-digest'and isinstance(value.get('package'),str)and
        re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+',value['package'])and
        isinstance(value.get('signers'),list)and len(value['signers'])==1 and
        all(isinstance(s,str)and re.fullmatch('[a-f0-9]{64}',s)for s in value['signers'])and
        isinstance(value.get('compatibility'),dict)and isinstance(value.get('file'),dict))


def _profile_key(library,key):
    resources=Path(library.resources)
    names=('native-resource-profile.json','native-compatibility.json','native-route-templates.json',
           'native-position-hook.json','native-acquisition-hook.json','native-overall-hook.json','native-thumbnail-hook.json')if key=='apk'else()
    value=[getattr(library,'profile',{}),[(n,hashlib.sha256((resources/n).read_bytes()).hexdigest())for n in names if(resources/n).is_file()]]
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def verify_source(path,key,library,*,expected=None,progress=None):
    try:stamp=file_stamp(path)
    except OSError as exc:raise ValueError('来源 APK 不可读取或 URI 授权已失效：'+str(path))from exc
    lock_key=(key,json.dumps(stamp,sort_keys=True),_profile_key(library,key))
    with _SOURCE_LOCK:
        if len(_VERIFY_LOCKS)>=32:
            for old,slot in list(_VERIFY_LOCKS.items()):
                if not slot.locked():_VERIFY_LOCKS.pop(old,None)
                if len(_VERIFY_LOCKS)<32:break
        lock=_VERIFY_LOCKS.setdefault(lock_key,threading.Lock())
    with lock:return _verify_source(path,key,library,expected=expected,progress=progress)


def _verify_source(path,key,library,*,expected=None,progress=None):
    """Verify an APK's real signature and adapted interface before trusting it.

    A certificate merely found in a signing block is never verification. Only
    a complete v2 public-key/content-digest check is cached, bound to all file
    identity fields and the current interface contracts, not just its name.
    """
    from apk_installation import identity
    from apk_format import verify_v2
    p=Path(path).absolute()
    if key not in MARKERS:raise ValueError('来源 APK 类型无效')
    if not p.is_file():raise ValueError('来源 APK 不可读取或 URI 授权已失效：'+str(path))
    before=file_stamp(p);cache_key=(key,json.dumps(before,sort_keys=True),_profile_key(library,key))
    with _SOURCE_LOCK:cached=copy.deepcopy(_CHECKED.get(cache_key))
    if cached is None:
        if progress:progress('读取'+('主程序'if key=='apk'else'离线资源')+' APK 包名、签名和分包状态')
        info=identity(p)
        package=info.get('package');signers=sorted(set(info.get('signers')or[]))
        if not isinstance(package,str)or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+',package):raise ValueError('来源 APK 包名无效')
        if len(signers)!=1:raise ValueError('来源 APK 缺少可验证的单一签名，不能自动更新来源记录')
        if expected is not None:
            if not _trusted(expected):raise ValueError('缺少可信的原始来源身份，不能自动替换已丢失的 APK')
            if package!=expected['package']:raise ValueError('新路径的 APK 包名与当前资料库来源不一致')
            if signers!=expected['signers']:raise ValueError('新路径的 APK 签名与当前资料库来源不一致')
        with zipfile.ZipFile(p)as archive:
            if MARKERS[key]not in archive.namelist():raise ValueError('来源 APK 类型与导入记录不一致')
            from android_manifest import Manifest
            document=Manifest(archive.read('AndroidManifest.xml'))
            for _,node,attrs in document.nodes():
                values={a['name']:a['value']for a in attrs}
                if node=='uses-split'or node=='manifest'and values.get('split')or node=='application'and values.get('isSplitRequired'):
                    raise ValueError('分包安装不能作为完整来源，请选择完整 APK')
            release=json.loads(archive.read(MARKERS[key]))if key=='apk'else None
        if progress:progress('核验来源 APK 的真实签名与完整内容摘要')
        try:signature=verify_v2(p)
        except Exception as exc:raise ValueError('来源 APK 签名或完整内容校验失败，未更新来源记录')from exc
        if not signature.get('verified_v2')or signature.get('signer_sha256')not in signers:
            raise ValueError('来源 APK 的已验证签名与包信息不一致')
        compatibility={'kind':key}
        cached={'revision':IDENTITY_REVISION,'package':package,'signers':signers,
                'verification':'apk-v2-content-digest','content_digest':signature['content_digest'],'file':before}
        if key=='apk':
            if progress:progress('核对主程序适配根、资源密钥与原生接口；保留现有资料库')
            from engine_compatibility import detect
            adapter=detect(p,library.resources,release['release_id'],getattr(library,'profile',{}))
            profile=library.profile
            family=adapter.get('family');wanted=profile.get('adapter_family',profile.get('family','V14'))
            base=_family(release.get('base_release_id')or release['release_id'])
            if family!=wanted or adapter.get('game_version')!=profile.get('game_version',adapter.get('game_version')):
                raise ValueError('来源 APK 的原生适配系列与当前资料库不一致')
            if base!=_family(profile.get('release_id')):raise ValueError('来源 APK 的基础版本与当前资料库不一致')
            compatibility.update(base_release_id=base,adapter_family=family,game_version=adapter['game_version'],
                interface_contract_sha256=hashlib.sha256((Path(library.resources)/'native-compatibility.json').read_bytes()).hexdigest(),
                resource_key_sha256=profile.get('resource_key_sha256'))
            with zipfile.ZipFile(p)as archive,archive.open('lib/arm64-v8a/libUE4.so')as engine:
                cached['engine_sha256']=hashlib.file_digest(engine,'sha256').hexdigest()
                template_name='assets/gateway-release/work/templates/CMD_LOGIN_LIVEDATA.json'
                if archive.getinfo(template_name).file_size>2*1024*1024:raise ValueError('来源实时更新清单过大')
                raw=archive.read(template_name)
            from native_live import _version,version_info
            version=_version(version_info(json.loads(raw))['version'],'来源的')
            cached['live']={'version':version,'template_sha256':hashlib.sha256(raw).hexdigest()}
            cached['live_version_max']=version
        cached['compatibility']=compatibility
        if file_stamp(p)!=before:raise ValueError('来源 APK 在校验时发生变化，请重试')
        with _SOURCE_LOCK:
            if len(_CHECKED)>=16:_CHECKED.clear()
            _CHECKED[cache_key]=copy.deepcopy(cached)
    if expected is not None:
        if not _trusted(expected):raise ValueError('缺少可信的原始来源身份，不能自动替换已丢失的 APK')
        if cached['package']!=expected['package']:raise ValueError('新路径的 APK 包名与当前资料库来源不一致')
        if cached['signers']!=expected['signers']:raise ValueError('新路径的 APK 签名与当前资料库来源不一致')
        if cached['compatibility']!=expected['compatibility']:raise ValueError('新路径的 APK 适配根与当前资料库不一致')
        if key=='apk':
            from native_live import _version
            cached['live_version_max']=max(cached['live']['version'],_version(expected.get('live_version_max',expected['live']['version']),'已验证资料库的'))
        for field in ('identity_relation','original_apk_sha256'):
            if field in expected:cached[field]=expected[field]
    if file_stamp(p)!=before:raise ValueError('来源 APK 在校验后发生变化，请重试')
    return cached


def _assert_current(library,store):
    active=_read(store.root/'active-game.json');database=_database(library)
    if active.get('database')and active['database']!=database:
        raise ValueError('当前资料库已切换，请重新打开导出页后重试')


def _atomic(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf8')as stream:
            json.dump(value,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


class SourceChanged(ValueError):pass


def assert_export_current(library,store,checked):
    try:
        _assert_current(library,store)
        row=recorded(library,store)
        if row.get('source_revision')!=checked.get('source_revision')or row.get('database')!=checked.get('database'):
            raise ValueError('资料库来源已更新')
        if file_stamp(checked['selected_path'])!=checked['selected_identity']['file']:
            raise ValueError('所选来源 APK 已变化')
        if file_stamp(checked['library_path'])!=checked['library_identity']['file']:
            raise ValueError('资料库来源 APK 已变化')
        for dependency in checked.get('resource_dependencies',[])+checked.get('library_resource_dependencies',[]):
            if file_stamp(dependency['path'])!=dependency['file']:
                raise ValueError('资源依赖已变化：'+dependency['package'])
    except (OSError,ValueError)as exc:
        raise SourceChanged('来源在编译期间已变化，此次导出已停止；请刷新写入列表后重试，现有资料库与工程保留')from exc


@contextmanager
def publication_guard(library,store,checked):
    """An import cannot change the active pointer during final registry publish."""
    from native_live import _file_lock
    with _SOURCE_LOCK,_file_lock(store.root/'active-game-source.lock'):
        assert_export_current(library,store,checked)
        yield


def _commit(library,store,previous,updated):
    from native_live import _file_lock
    with _SOURCE_LOCK,_file_lock(store.root/'active-game-source.lock'):
        _assert_current(library,store)
        current=recorded(library,store)
        if current!=previous:raise SourceChanged('来源记录已被另一任务更新，请重试')
        if _database(library):updated=dict(updated,database=_database(library))
        if current==updated:return current
        updated=dict(updated,source_revision=uuid.uuid4().hex)
        if _database(library):_atomic(store.root/'active-game.json',updated)
        else:
            path=store.root/'apk-source-identities.json';rows=_read(path)
            rows[_library_key(library)]=updated;_atomic(path,rows)
        return updated


def activate_import(store,database,apk,companion,identities,resource_bound=None):
    """Publish the imported database/path/identity pointer as one locked record."""
    from native_live import _file_lock
    if not re.fullmatch('[a-f0-9]{24}',database):raise ValueError('导入数据库编号无效')
    for key,path in (('apk',apk),('companion',companion)):
        if not _trusted(identities.get(key))or file_stamp(path)!=identities[key]['file']:
            raise ValueError('导入来源在准备资料库时发生变化，请重试')
    row={'database':database,'apk':str(Path(apk).absolute()),'companion':str(Path(companion).absolute()),
         'source_identities':copy.deepcopy(identities),'source_revision':uuid.uuid4().hex}
    if resource_bound and(resource_bound.get('resource_set_id')or resource_bound.get('resource_dependencies')):
        for dependency in resource_bound['resource_dependencies']:
            if file_stamp(dependency['path'])!=dependency['file']:raise ValueError('资源依赖在准备期间发生变化，请重试')
        row.update(resource_set_id=resource_bound['resource_set_id'],resource_dependencies=copy.deepcopy(resource_bound['resource_dependencies']))
    with _SOURCE_LOCK,_file_lock(store.root/'active-game-source.lock'):_atomic(store.root/'active-game.json',row)
    return row


def _installed(library,package):
    provider=getattr(library,'installed_source',None)
    if callable(provider):result=provider(package)
    else:
        provider=getattr(library,'installed_sources',None)
        result=provider()if callable(provider)else{'rows':[]}
    if not isinstance(result,dict):raise ValueError('Android 来源查询返回格式无效')
    return result


def resource_paths(library,store,game_path,provided=None):
    from resource_source_bindings import paths
    return paths(library,store,game_path,provided)


def _legacy_anchor(library,store,before,candidate,*,explicit=False,selection_token=None,resource_packages=None,progress=None):
    """An exact old APK or a fresh explicit selection can bind a legacy record.

    Interface equality cannot prove an unknown old signature. Explicit binding
    records that limitation rather than claiming the new signer was the old one.
    """
    if not candidate or not Path(candidate).is_file():return None
    if explicit:
        from write_modes import source,requirements
        _,summary=source(candidate,requirements(library),resource_packages=resource_paths(library,store,candidate,resource_packages))
        if not isinstance(selection_token,str)or selection_token!=summary['token']:
            raise ValueError('本次手动选择来源的校验已过期，请重新选择当前主 APK；无需重新读取资料库')
    report=_read(Path(library.resources)/'import-report.json');digest=report.get('apk_sha256')
    if not explicit and not(isinstance(digest,str)and re.fullmatch('[a-f0-9]{64}',digest)):return None
    info=verify_source(candidate,'apk',library,progress=progress)
    if explicit:info['identity_relation']='explicit_new_anchor_legacy_identity_unknown'
    else:
        if progress:progress('核对导入时原 APK 的整包摘要，为旧记录重新绑定来源')
        with Path(candidate).open('rb')as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
        if file_stamp(candidate)!=info['file']:raise ValueError('所选来源在核对导入摘要时变化，请重试')
        if actual!=digest:return None
        info.update(identity_relation='exact_imported_apk_sha256',original_apk_sha256=digest)
    return info


def _readable_unsigned_legacy(path):
    """A pre-identity local delta source with NO APK signing block.

    This is deliberately narrower than failed signature verification. A present
    v2/v3 block, including one with a corrupt payload digest, cannot enter the
    legacy-anchor fallback. Only an explicit fresh selection can replace it.
    """
    from apk_format import layout
    before=file_stamp(path);metadata=layout(path)
    if file_stamp(path)!=before:raise SourceChanged('旧来源在识别历史未签名增量包时发生变化，请重试')
    return before if metadata['data_end']==metadata['central']else None


def current_sources(library,store,*,keys=('apk',),selected_apk=None,selected_companion=None,
                    explicit_source_selection=False,write_source_token=None,resource_packages=None,progress=None):
    """Refresh only a proven source identity; never select a convenient APK."""
    chosen={'apk':selected_apk,'companion':selected_companion}
    for attempt in range(3):
        _assert_current(library,store);before=recorded(library,store);updated=copy.deepcopy(before)
        if not before:raise ValueError('当前资料库缺少来源记录，请先导入游戏')
        identities=updated.setdefault('source_identities',{});unsigned_guards=[]
        for key in keys:
            path=before.get(key);expected=before.get('source_identities',{}).get(key)
            if expected is not None and not _trusted(expected):raise ValueError('来源身份记录无效，请恢复可信记录或重新导入')
            if path and Path(path).is_file():
                unsigned=(_readable_unsigned_legacy(path)if expected is None and key=='apk'and
                    explicit_source_selection and chosen.get(key)else None)
                if unsigned is None:
                    info=verify_source(path,key,library,expected=expected if _trusted(expected)else None,progress=progress)
                    if expected is None:info['identity_relation']='migrated_existing_readable_source'
                    identities[key]=info;continue
                # Older content-delta imports intentionally retained an
                # unsigned local catalogue source. Its existence is not a
                # verified old identity and must not block an authorized new
                # anchor. The chosen APK/dependencies are still fully checked.
                unsigned_guards.append((path,unsigned))
            if not _trusted(expected):
                anchor=_legacy_anchor(library,store,before,chosen.get(key),explicit=explicit_source_selection,
                    selection_token=write_source_token,resource_packages=resource_packages,progress=progress)if key=='apk'else None
                if anchor is None:
                    raise ValueError('当前资料库'+('主'if key=='apk'else'离线资源')+' APK 已丢失且没有可信身份；请选择当前安装的完整主 APK 重新绑定来源，无需重读资料库，不能自动改用其它 APK')
                updated[key]=anchor['file']['path'];identities[key]=anchor
                alias={'path':str(path or''),'identity_relation':anchor['identity_relation'],
                       'package':anchor['package'],'signers':anchor['signers'],'compatibility':anchor['compatibility']}
                history=updated.setdefault('legacy_source_aliases',{}).setdefault(key,[])
                history.append(alias);del history[:-8]
                if anchor['identity_relation']=='explicit_new_anchor_legacy_identity_unknown':
                    updated['source_binding_notice']={'message':'旧记录未保存签名，已按本次选择重新绑定来源，资料库未重读。',
                        'package':anchor['package'],'signer_short':anchor['signers'][0][:16]}
                continue
            if progress:progress('已安装路径变化，按原包名重新查询 Android 来源；无需重读资料库')
            try:discovered=_installed(library,expected['package'])
            except Exception as exc:raise ValueError('无法重新查询原包名的 Android 来源，请选择同一来源的完整 APK')from exc
            candidates=[]
            for row in discovered.get('rows',[]):
                if row.get('package')!=expected['package']:continue
                if row.get('split')or row.get('split_source_dirs'):raise ValueError('当前来源为分包安装，请选择完整 APK')
                if sorted(row.get('signers')or[])!=expected['signers']:raise ValueError('已安装同包名游戏的签名与当前资料库来源不一致')
                if row.get('path'):candidates.append(row['path'])
            if chosen.get(key):candidates.append(chosen[key])
            errors=[];found=None
            for candidate in dict.fromkeys(str(p)for p in candidates):
                try:found=verify_source(candidate,key,library,expected=expected,progress=progress);break
                except (OSError,ValueError,KeyError,zipfile.BadZipFile)as exc:errors.append(str(exc))
            if found is None:
                detail='；'.join(errors or discovered.get('errors',[]))
                raise ValueError('当前资料库'+('主'if key=='apk'else'离线资源')+' APK 不可读取，未找到同包名、同签名且兼容的来源'+('：'+detail if detail else''))
            history=updated.setdefault('source_path_history',{}).setdefault(key,[])
            previous=copy.deepcopy(expected)
            if previous['file']['path']!=found['file']['path']:
                history[:]=[r for r in history if r.get('file',{}).get('path')!=previous['file']['path']]
                history.append(previous);del history[:-8]
            updated[key]=found['file']['path'];identities[key]=found
        if 'apk'in keys:
            from resource_source_bindings import verify as verify_resource_set
            library_packages=resource_packages
            if selected_apk and os.path.normcase(str(Path(updated['apk']).absolute()))!=os.path.normcase(str(Path(selected_apk).absolute())):
                library_packages=None
            if selected_apk and (library_packages or {}).get('content_pack'):
                # An optional overlay belongs to the selected export even
                # when the library uses the exact same program APK.
                from program_content import program
                with zipfile.ZipFile(updated['apk'])as archive:marker=program(archive)
                if marker and not marker.get('content_required'):
                    library_packages={name:path for name,path in library_packages.items()if name!='content_pack'}
            bound=verify_resource_set(library,store,updated['apk'],library_packages,progress,verified_game=identities.get('apk'))
            if bound['resource_set_id']is not None or bound.get('resource_dependencies'):
                updated.update({k:copy.deepcopy(bound[k])for k in('resource_dependencies','resource_set_id')})
            else:
                updated.pop('resource_dependencies',None);updated.pop('resource_set_id',None)
        for old_path,old_stamp in unsigned_guards:
            if file_stamp(old_path)!=old_stamp:raise SourceChanged('旧未签名来源在重新绑定期间发生变化，请重试，原资料库来源记录保留')
        try:return _commit(library,store,before,updated)
        except SourceChanged:
            if attempt==2:raise


def rebind_exported_source(library, store, current_path, replacement=None):
    """Switch a still-readable exported source while keeping the active library."""
    from content_pack import installed_program
    from resource_source_bindings import verify as verify_resource_set

    _assert_current(library, store)
    before = recorded(library, store)
    if not _database(library) or not before.get('apk'):
        raise ValueError('当前没有可切换来源的活动资料库')
    source = Path(before['apk']).resolve()
    roots = (store.root.resolve() / 'exports', store.root.resolve() / 'game-bases')
    if source != Path(current_path).resolve() or not any(source.is_relative_to(root) for root in roots):
        raise ValueError('当前资料库的导出来源已变化，请刷新存储管理')
    expected = verify_source(source, 'apk', library, expected=before.get('source_identities', {}).get('apk'))
    packages = None
    if not replacement:
        registration = installed_program(store, expected['package'])
        if not registration or registration.get('package') != expected['package']:
            raise ValueError('未登记同包名的最新基准，请手动选择同来源的完整 APK')
        replacement = registration['apk']
        packages = registration.get('resource_packages')
        if packages:
            if not isinstance(packages, dict):
                raise ValueError('最新基准的配套资源登记格式无效')
            # An optional overlay belongs to the export, not the library source.
            from program_content import program
            with zipfile.ZipFile(replacement) as archive:
                marker = program(archive)
            if marker and not marker.get('content_required'):
                packages = {key: value for key, value in packages.items() if key != 'content_pack'}
    target = Path(replacement).resolve()
    if target == source:
        raise ValueError('当前资料库已经使用这个 APK')
    identity = verify_source(target, 'apk', library, expected=expected)
    bound = verify_resource_set(library, store, target, packages, verified_game=identity)
    updated = copy.deepcopy(before)
    updated['apk'] = identity['file']['path']
    updated.setdefault('source_identities', {})['apk'] = identity
    updated.pop('source_binding_notice', None)
    history = updated.setdefault('source_path_history', {}).setdefault('apk', [])
    history[:] = [row for row in history if row.get('file', {}).get('path') != expected['file']['path']]
    history.append(expected)
    del history[:-8]
    for field in ('resource_dependencies', 'resource_set_id'):
        if bound[field]:
            updated[field] = copy.deepcopy(bound[field])
        else:
            updated.pop(field, None)
    if file_stamp(target) != identity['file'] or file_stamp(source) != expected['file']:
        raise SourceChanged('APK 在切换来源时发生变化，请重试')
    committed = _commit(library, store, before, updated)
    return {'previous': str(source), 'apk': committed['apk']}


def refresh_read_sources(library,store):
    """Refresh an installed source before reading cached workspace metadata."""
    before=recorded(library,store)
    stale=tuple(key for key in ('apk','companion') if before.get(key) and not Path(before[key]).is_file())
    if not stale:return before
    return current_sources(library,store,keys=stale)


def export_preflight(library,store,selected,*,key='apk',explicit_source_selection=False,write_source_token=None,resource_packages=None,progress=None):
    """Keep library authority and the independently selected write source apart."""
    old=recorded(library,store)
    if type(explicit_source_selection)is not bool:raise ValueError('手动来源选择标记无效')
    library_selection=selected
    if key=='apk' and not library_selection and (resource_packages or {}).get('content_pack'):
        library_selection=old.get('apk')
    current=current_sources(library,store,keys=(key,),progress=progress,
        explicit_source_selection=explicit_source_selection,write_source_token=write_source_token,resource_packages=resource_packages,
        **({'selected_apk':library_selection}if key=='apk'else{'selected_companion':selected}))
    path=str(selected or current.get(key)or'')
    history=current.get('source_path_history',{}).get(key,[])
    alias=any(_trusted(row)and row['file']['path']==path and
        row['package']==current['source_identities'][key]['package']and
        row['signers']==current['source_identities'][key]['signers']and
        row['compatibility']==current['source_identities'][key]['compatibility']for row in history)
    alias=alias or any(row.get('path')==path and row.get('identity_relation')in
        ('exact_imported_apk_sha256','explicit_new_anchor_legacy_identity_unknown')and
        all(row.get(field)==current['source_identities'][key][field]for field in ('package','signers','compatibility'))
        for row in current.get('legacy_source_aliases',{}).get(key,[]))
    if not Path(path).is_file()and(path==str(old.get(key)or'')or alias):path=current[key]
    if progress:progress('核对导出所选 APK；当前资料库来源单独校验')
    selected_info=verify_source(path,key,library,progress=progress)
    from resource_source_bindings import verify as verify_resource_set
    bound=verify_resource_set(library,store,path,resource_packages,progress,verified_game=selected_info)if key=='apk'else{}
    return {'library_path':current[key],'selected_path':selected_info['file']['path'],
            'library_identity':current['source_identities'][key],'selected_identity':selected_info,
            'database':current.get('database'),'source_revision':current.get('source_revision'),
            'resource_packages':bound.get('resource_packages',{}),'resource_dependencies':bound.get('resource_dependencies',[]),
            'library_resource_dependencies':current.get('resource_dependencies',[]),'resource_set_id':bound.get('resource_set_id'),
            'rebind_notice':current.get('source_binding_notice')if key not in old.get('source_identities',{})else None}


def kind_matches(path,key):
    try:
        with zipfile.ZipFile(path) as archive:return MARKERS[key] in archive.namelist()
    except (OSError,ValueError,zipfile.BadZipFile):return False


def _mode_source(path):
    p=Path(path).absolute();stat=p.stat()
    return {'path':str(p),'size':stat.st_size,'mtime_ns':stat.st_mtime_ns}


def remember_mode(store,path,mode):
    """Remember a successful export's mode for this exact source and signer."""
    from apk_installation import local_certificate
    if mode not in ('coexist','update'):raise ValueError('安装方式无效')
    source=_mode_source(path);key=hashlib.sha256(os.path.normcase(source['path']).encode()).hexdigest()
    target=store.root/'installation-modes.json';value=_read(target)
    certificate=local_certificate(store);previous=value.get(key)
    if isinstance(previous,dict)and previous.get('source')==source and previous.get('signer')==certificate and previous.get('mode')in('coexist','update'):return
    value[key]={'source':source,'mode':mode,'signer':certificate}
    temp=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
    try:temp.write_text(json.dumps(value,ensure_ascii=False),encoding='utf8');temp.replace(target)
    finally:temp.unlink(missing_ok=True)


def installation_guidance(path,store):
    """Inspect the selected library's APK, never an unrelated installed game."""
    from apk_installation import identity,local_certificate,GAME,COEXIST_GAME
    result={'recommended_mode':'coexist','reason':'尚未读取到可识别的主 APK；请先导入游戏库。',
            'source_path':str(path or ''),'package':None,'workshop':False,'signer_matches':False,'basis':'unavailable'}
    certificate=local_certificate(store);source=None;prior=None
    try:
        if not path:return result
        source=_mode_source(path)
        with zipfile.ZipFile(path)as archive:
            if MARKERS['apk']not in archive.namelist():return result
            workshop=WORKSHOP_DOCUMENT in archive.namelist()
        info=identity(path);matches=bool(certificate and certificate in info['signers'])
        key=hashlib.sha256(os.path.normcase(source['path']).encode()).hexdigest()
        prior=_read(store.root/'installation-modes.json').get(key)
        result.update(package=info['package'],workshop=workshop,signer_matches=matches)
        if info['package']==COEXIST_GAME:
            result.update(reason='当前库来自“工坊版”共存包，后续更新请继续使用共存模式。',basis='coexist_package')
            if not matches:result['reason']+='此包与本机签名不同，重新生成后不能直接覆盖原设备签名的安装。'
        elif info['package']!=GAME:
            result.update(reason='当前包名无法确认安装方式，建议先核对导入来源，再使用共存模式。',basis='unknown_package')
        elif workshop and not matches:
            result.update(reason='当前库来自其他签名的工坊包，可能由另一台设备或另一端工坊生成，推荐共存模式。',basis='foreign_signer')
        elif isinstance(prior,dict)and prior.get('source')==source and prior.get('signer')==certificate and certificate and prior.get('mode')in('coexist','update'):
            result.update(recommended_mode=prior['mode'],reason='此来源已成功生成过安装包，继续沿用首次选择的'+('共存模式。'if prior['mode']=='coexist'else'工坊更新模式。'),basis='successful_export')
        elif workshop and matches:
            result.update(recommended_mode='update',reason='当前库来自本机同签名、非共存的工坊包，推荐工坊更新模式。',basis='local_workshop')
        else:
            result.update(reason='当前库来自原版包。若要保留原版游戏，推荐共存模式；未安装原游戏但已有原版资源包，也可手动选择工坊更新模式。',basis='original')
    except (OSError,ValueError,KeyError,IndexError,struct.error,zipfile.BadZipFile):
        result.update(reason='无法完整读取当前来源的包名或签名，未认定可覆盖更新；请核对文件或重新导入。',basis='unreadable')
    finally:
        result['key']=hashlib.sha256(json.dumps([source,certificate,prior,result['recommended_mode']],sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]
    return result


def resolve(library,store):
    row=recorded(library,store);home=Path(getattr(library,'home',store.root.parent));selected={};errors=[];pending=[]
    names={'apk':'73efootball-'+getattr(library,'profile',{}).get('game_version','V17')+'.apk',
           'companion':'73efootball-离线资源包-V9+.apk'}
    for key in MARKERS:
        if row:
            # Keep a missing imported path visible. Never silently substitute
            # an unrelated installed base.apk after URI permission is lost.
            path=str(row.get(key) or '')
            expected=row.get('source_identities',{}).get(key)
            # Boot/status/list calls only consume the system's package/signing
            # metadata. Full APK verification and atomic publication run later
            # in the asynchronous export preflight, never on this UI thread.
            if path and not Path(path).is_file()and _trusted(expected):
                try:
                    discovered=_installed(library,expected['package'])
                    for candidate in discovered.get('rows',[]):
                        target=str(candidate.get('path')or'')
                        if (candidate.get('package')==expected['package']and
                            sorted(candidate.get('signers')or[])==expected['signers']and
                            not candidate.get('split')and not candidate.get('split_source_dirs')and
                            target and Path(target).is_file()and kind_matches(target,key)):
                            pending.append({'kind':key,'recorded_path':path,'candidate_path':target,
                                'package':expected['package'],'verification':'pending_export_preflight'})
                            path=target;break
                except (OSError,ValueError,KeyError,zipfile.BadZipFile):pass
        else:
            candidates=[home/'原始APK'/names[key],home/names[key],home.parent/names[key]]
            path=next((str(p) for p in candidates if p.is_file() and kind_matches(p,key)),'')
        selected[key]=path
        if row and (not path or not Path(path).is_file()):
            errors.append(('主程序' if key=='apk' else '离线资源')+' APK 已移动或读取授权失效，请选择当前安装的完整 APK 重新绑定来源，无需重读资料库。')
        elif path and not kind_matches(path,key):
            errors.append(('主程序' if key=='apk' else '离线资源')+' APK 内容与导入记录不符，请重新选择对应文件。')
    stamps=[file_stamp(p)if p and Path(p).is_file()else None for p in selected.values()]
    key=hashlib.sha256(json.dumps([row.get('database'),row.get('source_revision'),*selected.values(),stamps],ensure_ascii=False).encode()).hexdigest()[:24]
    return dict(selected,source_key=key,recorded=bool(row),errors=errors,relocation_pending=pending,
                installation=installation_guidance(selected['apk'],store))
