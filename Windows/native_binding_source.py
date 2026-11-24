"""Inspect the APK's literal native installer without executing APK modules."""
import importlib.util,io,json,marshal,sys,types,zipfile
from pathlib import Path
from apk_literals import module_reader,literal_assignments

def literal_installer(root):
    pending=[root];found=[];count=0
    while pending:
        code=pending.pop();count+=1
        if count>4096:raise ValueError('原生美术安装器包装层过多')
        if code.co_name=='_start_legend_portrait_binding':found.append(code)
        for value in code.co_consts:
            if isinstance(value,types.CodeType):pending.append(value)
            elif isinstance(value,bytes) and value and value[0] in (99,227):
                try:child=marshal.loads(value)
                except (ValueError,EOFError,TypeError):continue
                if isinstance(child,types.CodeType):pending.append(child)
    if len(found)!=1:raise ValueError('原版 APK 的美术安装器不唯一或已改变')
    code=found[0];payload=literal_assignments(code,{'PAYLOAD'})['PAYLOAD']
    if not isinstance(payload,dict) or not {'blob_hex','hooks','offsets','back_fixup'}<=payload.keys():
        raise ValueError('原版 APK 的美术接口资料不完整')
    return code,payload


def read_installer(apk, *, content_catalogue=None):
    reader=module_reader(apk);root=reader('legends_v10')
    code,payload=literal_installer(root)
    if 'prepare_binding' in root.co_names and 'diy_runtime' in root.co_names:
        from native_diy_binding import route_layers,compose,PreparedInstaller,verify_protocol
        verify_protocol(reader)
        layers=route_layers(root);routes=[r for layer in layers for r in layer]
        sites=literal_assignments(reader('diy_routes'),{'SITES'})['SITES']
        templates=literal_assignments(reader('diy_route_templates'),{'TEMPLATES'})['TEMPLATES']
        outer=[c for c in reader('diy_binding_outer').co_consts if isinstance(c,types.CodeType) and c.co_name=='_start_legend_portrait_binding']
        if len(outer)!=1:raise ValueError('V16 原生安装器外层不唯一')
        preparer=next(c for c in reader('diy_runtime').co_consts if isinstance(c,types.CodeType) and c.co_name=='prepare_binding')
        allocators=[c for c in preparer.co_consts if isinstance(c,types.CodeType) and c.co_name=='allocate_pages']
        if len(allocators)!=1:raise ValueError('V16 原生分配器准备入口改变')
        # Content packs contribute only declarative routes. Rebuild them with
        # the inspected V17 composer; never copy native code from the ZIP.
        import zipfile
        from content_delta import read_catalogue
        if content_catalogue is None:
            with zipfile.ZipFile(apk) as archive:content_catalogue=read_catalogue(archive)
        extra=content_catalogue.get('routes',[])
        from art_registry import prepare_registry
        effective,installer=prepare_registry(code,outer[0],payload,layers,extra,sites,templates,allocators[0])
        from native_diy_binding import code_fingerprint,V17_COMPOSE
        composer=next(c for c in reader('diy_routes').co_consts if isinstance(c,types.CodeType)and c.co_name=='compose')
        installer.proof['interface_family']='V17'if code_fingerprint(composer)==V17_COMPOSE else'V16'
        return effective,installer
    if content_catalogue and content_catalogue.get('routes'):
        raise ValueError('此原生接口不支持内容增量美术注册，未忽略新增绑定')
    return payload,code

def installer_metadata(installer):return getattr(installer,'proof',None)

def verify_installer(code,binding,*,strict_document=False):
    from personal_binding import prepare
    calls=[]
    def allocation_stub(libc,rows,base,size,*args):
        calls.append(size);return 0x100000,'preflight_no_native_memory'
    namespace={'__name__':'studio_binding_preflight','allocate_portrait_pages':allocation_stub}
    order_report=None
    if hasattr(code,'function'):
        if not strict_document:binding=dict(binding,source=code.proof)
        if (binding.get('source',{}).get('registry')if strict_document else code.proof.get('registry'))and hasattr(code,'baseline_installer'):
            from art_registry import prepare_runtime
            from personal_binding import fingerprint
            if not strict_document:binding.update(original_fingerprint=code.proof['raw_fingerprint'],prepared_fingerprint=fingerprint(code.payload))
            function=prepare_runtime(code.baseline_installer.function(allocation_stub),binding,code.allocator_code)
            order_report=getattr(function,'_art_order_report',None)
        else:function=code.function(allocation_stub)
    else:function=types.FunctionType(code,namespace)
    modified,report=prepare(function,binding)
    def forbidden(*args,**kwargs):raise RuntimeError('桌面预检禁止分配游戏内存')
    # Exercise the same allocation entry used by the worker. Capturing only
    # its payload missed V16's incompatible allocator-size closure.
    near_calls=[]
    def near(libc,rows,base,size):near_calls.append(size);return 0x100000
    result=modified.__globals__['allocate_portrait_pages'](None,[],0,report['page_bytes'],near,forbidden,forbidden,[])
    valid=(calls==[4096]and not near_calls and result==(0x100000,'preflight_no_native_memory'))if report['page_bytes']==4096 else(not calls and near_calls==[report['page_bytes']*2]and result==(0x100000,'personal_private_mmap'))
    if not valid:
        raise ValueError('原生美术分配器协议预检失败')
    result=dict(report,allocator_contract_verified=True,shared_registry_pipeline=bool(getattr(code,'proof',{}).get('registry')))
    if strict_document:result.update(serialized_fingerprints_preserved=True,registry_order=order_report,
        personal_payload_fingerprint_verified='personal_payload_fingerprint'in binding,
        legacy_personal_order_unverifiable='personal_payload_fingerprint'not in binding)
    return result


def _verify_shipped_art_helpers(archive):
    """Match actual startup helper code to this verified compiler before use."""
    def normalized(code):
        return code.replace(co_filename='',co_consts=tuple(normalized(v)if isinstance(v,types.CodeType)else v for v in code.co_consts))
    folder=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent));checked=[]
    with zipfile.ZipFile(io.BytesIO(archive.read('assets/chaquopy/app.imy')))as app:
        for name in('art_registry','native_diy_binding','personal_binding'):
            raw=app.read(name+'.pyc')
            if raw[:4]!=importlib.util.MAGIC_NUMBER:raise ValueError('美术启动模块字节码版本与工坊不同：'+name)
            code=marshal.loads(raw[16:]);source=(folder/(name+'.py')).read_text('utf8')
            if not isinstance(code,types.CodeType)or normalized(code)!=normalized(compile(source,name+'.py','exec',dont_inherit=True)):
                raise ValueError('最终游戏美术启动模块未同步工坊：'+name)
            checked.append(name)
    return checked


def verify_apk_startup(path,resource_packages=None,*,require_current_helpers=True):
    """Read actual final JSON and exercise registry/personal/allocator gates.

    Both legacy-single and resource-set exports resolve through the same Reader.
    No routes, fingerprints or JSON keys are repaired during this verification.
    The only executed installer fragment aborts at literal PAYLOAD capture;
    allocator checks use stubs and never allocate native game memory or threads.
    The opt-out is for read-only legacy diagnosis, never final publication.
    """
    from archive_data import read_json,PERSONAL_JSON_LIMIT
    from resource_set import Reader
    from art_registry import verify_content
    with Reader(path,packages=resource_packages)as archive:
        document=read_json(archive,'assets/gateway-release/custom-resources/personal-player-studio/players.json',limit=PERSONAL_JSON_LIMIT)
        content=verify_content(document);binding=document.get('native_binding')
        if not binding:return {'verified':True,'required':False,'content_registry':content,'serialized_json_readback':True}
        if require_current_helpers and ('personal_payload_fingerprint'not in binding or 'site_order'not in binding.get('profile',{})):
            raise ValueError('新导出游戏缺少个人美术入口顺序或完整生成指纹')
        helpers=_verify_shipped_art_helpers(archive)if require_current_helpers else []
        resource_set=archive.document is not None
    _,installer=read_installer(path,content_catalogue=document.get('content_catalogue'))
    report=verify_installer(installer,binding,strict_document=True)
    return dict(report,verified=True,required=True,content_registry=content,serialized_json_readback=True,
        resource_set=resource_set,actual_startup_helpers_verified=helpers,
        legacy_diagnosis_only=not require_current_helpers,native_memory_or_threads_started=False)
