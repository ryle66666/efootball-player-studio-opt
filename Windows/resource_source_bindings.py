"""Explicit installed/file sources for one declared Android resource set.

Path lookup stays lightweight. Full signature/content verification belongs to
the asynchronous import/export preparation, and its receipt freezes every APK.
There is deliberately no process-global source substitution or sibling scan.
"""
from pathlib import Path
import copy,json,os,threading,zipfile

_LOCK=threading.RLock()
_VERIFIED={}


def _matches(info,dependency):
    return (info.get('package')==dependency['package']and
            info.get('version_code')==dependency['version_code']and
            sorted(info.get('signers')or[])==sorted(dependency['signers']))


def paths(library,store,game_path,provided=None):
    from program_content import sources
    content=sources(store,game_path,provided)
    if content is not None:return content
    from apk_sources import recorded,_installed
    from apk_installation import identity
    from resource_set import read_manifest,MissingResourcePackage
    document=read_manifest(game_path)
    if document is None:return {}
    if provided is not None and(not isinstance(provided,dict)or len(provided)>256 or
        any(not isinstance(k,str)or not isinstance(v,(str,Path))for k,v in provided.items())):
        raise ValueError('扩展资源来源必须为包名或依赖编号对应的 APK 路径')
    provided=provided or{};record=recorded(library,store);result={}
    exports={}
    try:
        registry=json.loads((store.root/'resource-set-exports.json').read_text(encoding='utf8'))
        exact=next((v for p,v in registry.get('games',{}).items()if
            os.path.normcase(str(Path(p).absolute()))==os.path.normcase(str(Path(game_path).absolute()))),None)
        if exact and exact.get('verified')and exact.get('set_id')==document['set_id']:
            exports=exact.get('resource_packages')or{}
        candidate=registry.get('sets',{}).get(document['set_id'],{})
        if (candidate.get('game')and os.path.normcase(str(Path(candidate['game']).absolute()))==
            os.path.normcase(str(Path(game_path).absolute()))and not exports):exports=candidate.get('resource_packages')or{}
    except (OSError,ValueError,TypeError,AttributeError):pass
    stored={row.get('package'):row for row in record.get('resource_dependencies',[])if isinstance(row,dict)}
    for dependency in document['packages']:
        package=dependency['package'];key=dependency['id']
        if dependency['role']=='game':
            result[key]=result[package]=str(Path(game_path).absolute());continue
        explicit=provided.get(key,provided.get(package));candidates=[]
        if explicit:candidates.append(str(explicit))
        exported=exports.get(key,exports.get(package))
        if isinstance(exported,str):candidates.append(exported)
        previous=stored.get(package)
        if previous and previous.get('path'):candidates.append(previous['path'])
        if dependency['role']=='offline'and record.get('companion'):candidates.append(record['companion'])
        errors=[];chosen=None
        def inspect(candidate):
            if not Path(candidate).is_file():return None
            info=identity(candidate)
            if not _matches(info,dependency):raise ValueError('包名、版本或签名不配套：'+package)
            return str(Path(candidate).absolute())
        for candidate in dict.fromkeys(candidates):
            try:chosen=inspect(candidate)
            except (OSError,ValueError,KeyError,zipfile.BadZipFile)as exc:
                if explicit and candidate==str(explicit):raise ValueError('所选扩展资源不配套：'+str(exc))from exc
                errors.append(str(exc))
            if chosen:break
        if chosen is None:
            try:
                discovered=_installed(library,package)
                for row in discovered.get('rows',[]):
                    if row.get('package')!=package:continue
                    if row.get('split')or row.get('split_source_dirs'):raise ValueError('资源依赖为分包安装：'+package)
                    if not row.get('path'):continue
                    if row.get('signers')and sorted(row['signers'])!=sorted(dependency['signers']):
                        raise ValueError('已安装资源签名不配套：'+package)
                    chosen=inspect(row['path'])
                    if chosen:break
                errors.extend(discovered.get('errors',[]))
            except (OSError,ValueError,KeyError,zipfile.BadZipFile)as exc:errors.append(str(exc))
        if chosen is None:
            raise MissingResourcePackage('缺少配套资源包 '+package+'，需要版本 '+str(dependency['version_code'])+
                '、内容 '+dependency.get('content_sha256','未声明')+'。请安装该包或在导入页补选完整 APK。'+
                (' '+errors[0]if errors else''))
        result[key]=result[package]=chosen
    return result


def verify(library,store,game_path,provided=None,progress=None,*,verified_game=None):
    from apk_sources import file_stamp
    from resource_set import read_manifest,Reader
    from program_content import sources
    content=sources(store,game_path,provided)
    if content is not None:
        with Reader(game_path,packages=content)as reader:
            path=content['content_pack'];before=file_stamp(path)
            marker=reader._content.program
            if file_stamp(path)!=before:raise ValueError('内容包在核对时发生变化')
            dependency=dict(id='content_pack',role='content',package=marker['package']+'.content',
                path=path,file=before,program_id=marker['program_id'],verification='73pack-program-identity')
            return {'resource_packages':content,'resource_dependencies':[dependency],'resource_set_id':None,
                    'resource_verification':{'verified':True,'program_id':marker['program_id'],'files':len(reader._content.virtual)}}
    document=read_manifest(game_path)
    if document is None:return {'resource_packages':{},'resource_dependencies':[],'resource_set_id':None}
    resolved=paths(library,store,game_path,provided)
    stamps={p['id']:file_stamp(resolved[p['id']])for p in document['packages']}
    key=json.dumps([document['set_id'],stamps],sort_keys=True,separators=(',',':'))
    with _LOCK:
        cached=copy.deepcopy(_VERIFIED.get(key))
        if cached is None:
            records=[]
            receipts={}
            if verified_game:
                from apk_sources import _trusted
                from export_payload import identity as file_identity
                if _trusted(verified_game)and verified_game['file']==file_stamp(game_path):
                    receipts[str(Path(game_path))]={'file':file_identity(Path(game_path).stat()),
                        'signature':{'verified_v2':True,'signer_sha256':verified_game['signers'][0],
                                     'content_digest':verified_game['content_digest']}}
            with Reader(game_path,resolved,verified_signatures=receipts)as reader:
                for dependency in document['packages']:
                    if dependency['role']=='game':continue # main verified by apk_sources
                    path=resolved[dependency['id']]
                    if progress:progress('核验资源依赖签名：'+dependency['package'])
                    signature=reader.signatures[dependency['id']]
                    if not signature.get('verified_v2')or signature.get('signer_sha256')not in dependency['signers']:
                        raise ValueError('资源依赖真实签名校验失败：'+dependency['package'])
                    records.append(dict(dependency,path=path,file=stamps[dependency['id']],
                        verification='apk-v2-content-digest',content_digest=signature['content_digest']))
                if progress:progress('流式核验完整资源集与原离线资源内容，不重读资料库')
                report=reader.verify()
            if any(file_stamp(resolved[p['id']])!=stamps[p['id']]for p in document['packages']):
                raise ValueError('资源依赖在校验期间发生变化，请重新选择来源')
            cached={'resource_packages':resolved,'resource_dependencies':records,
                    'resource_set_id':document['set_id'],'resource_verification':report}
            if len(_VERIFIED)>=8:_VERIFIED.clear()
            _VERIFIED[key]=copy.deepcopy(cached)
    if any(file_stamp(resolved[p['id']])!=stamps[p['id']]for p in document['packages']):
        raise ValueError('资源依赖在校验后发生变化，请重试')
    return cached


def selected_files(paths):
    """Convert explicit file-picker choices into package-keyed source input."""
    from apk_installation import identity
    if not isinstance(paths,list)or len(paths)>128 or any(not isinstance(p,str)for p in paths):
        raise ValueError('扩展资源补选列表无效')
    result={}
    seen=set();content_identity=None
    for path in paths:
        key=os.path.normcase(str(Path(path).absolute()))
        if key in seen:continue
        seen.add(key)
        if Path(path).suffix.lower()=='.73pack':
            from content_pack import Reader
            manifest=Reader(path).manifest
            identity_fields=tuple(manifest[name]for name in('package','program_id','pack_id'))
            if content_identity==identity_fields:continue
            if 'content_pack'in result:raise ValueError('所选内容包版本不同，请选择本次使用的一个内容包')
            content_identity=identity_fields
            result['content_pack']=str(Path(path).absolute());continue
        info=identity(path);package=info['package']
        if package in result:raise ValueError('同一资源依赖重复选择：'+package)
        result[package]=str(Path(path).absolute())
    return result
