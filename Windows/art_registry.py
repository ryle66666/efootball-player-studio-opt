"""One declarative stock/content registry, shared by export and Android startup."""
import copy,itertools,json,types


def merged_routes(stock,content):
    result={}
    for route in [*stock,*content]:
        row=copy.deepcopy(route);key=int(row['cardId']);row['cardId']=key
        if key in result and result[key]!=row:raise ValueError('美术注册表同一球员存在冲突：'+str(key))
        result[key]=row
    return [result[k]for k in sorted(result)]


def verify_content(document):
    """The final document must register every route from its effective catalogue."""
    expected=merged_routes([],document.get('content_catalogue',{}).get('routes',[]))
    registry=document.get('native_binding',{}).get('source',{}).get('registry',{})
    actual=merged_routes([],registry.get('content_routes',[]))
    if expected!=actual:
        required={r['cardId']:r for r in expected};registered={r['cardId']:r for r in actual}
        wrong=sorted(cid for cid in required.keys()|registered.keys()if required.get(cid)!=registered.get(cid))
        raise ValueError('内容增量美术绑定与生效清单不一致：应注册 '+str(len(expected))+' 条，实际 '+str(len(actual))+' 条；球员编号 '+', '.join(map(str,wrong[:12])))
    return {'verified':True,'expected_routes':len(expected),'registered_routes':len(actual),
            'card_ids':[r['cardId']for r in expected]}


def prepare_registry(raw_code,outer,raw_payload,layers,content,sites,templates,allocator_code):
    from native_diy_binding import compose,PreparedInstaller,site_names
    from personal_binding import fingerprint,code_fingerprint
    order=list(site_names(sites,templates));stock=[r for layer in layers for r in layer]
    original=raw_payload
    for layer in layers:original=compose(original,layer,sites,templates,site_order=order)
    base=PreparedInstaller(raw_code,outer,original,raw_payload,stock,allocator_code)
    routes=merged_routes(stock,content)
    effective=compose(raw_payload,routes,sites,templates,site_order=order) if routes else raw_payload
    installer=PreparedInstaller(raw_code,outer,effective,raw_payload,routes,allocator_code)
    installer.baseline_installer=base
    installer.original_route_layers=copy.deepcopy(layers)
    installer.route_layers=[routes]
    installer.proof['registry']={'version':2,'site_order':order,'stock_routes':stock,'content_routes':content,'sites':sites,'templates':templates,
        'base_prepared_fingerprint':fingerprint(original),'base_prepared_code_fingerprint':code_fingerprint(base.code),
        'raw_code_fingerprint':code_fingerprint(raw_code),'allocator_code_fingerprint':code_fingerprint(allocator_code)}
    return effective,installer


def resolve_registry_payload(raw_payload,binding):
    """Recover a v1 order only by a unique match to its existing full digest.

    Re-serialization can destroy mapping order but cannot change the stored
    prepared fingerprint. No digest is replaced and no arbitrary order wins.
    New v2 manifests require their explicit list; corrupt v2 never falls back.
    """
    from native_diy_binding import compose,site_names
    from personal_binding import fingerprint
    proof=binding.get('source',{});registry=proof.get('registry',{});version=registry.get('version')
    if version not in(1,2):raise ValueError('不支持的美术注册表准备协议')
    if fingerprint(raw_payload)!=proof.get('raw_fingerprint')or fingerprint(raw_payload)!=binding.get('original_fingerprint'):
        raise ValueError('原生美术原始安装器指纹不同')
    sites,templates=registry['sites'],registry['templates'];routes=merged_routes(registry['stock_routes'],registry['content_routes'])
    order=site_names(sites,templates,registry.get('site_order'))
    if version==2 and 'site_order'not in registry:raise ValueError('新版美术注册表缺少显式入口顺序')
    expected=binding.get('prepared_fingerprint')
    def build(names):return compose(raw_payload,routes,sites,templates,site_order=names)if routes else raw_payload
    payload=build(order)
    if fingerprint(payload)==expected:return payload,{'version':version,'site_order':list(order),'legacy_order_recovered':False}
    if version==2 or 'site_order'in registry:raise ValueError('美术注册表生成结果与导出不同')
    # Five sites: bounded to exactly 120 candidates, never a route/template
    # search. All other payload bytes and the expected cryptographic gate stay.
    matches=[]
    for candidate in itertools.permutations(order):
        if candidate==order:continue
        value=build(candidate)
        if fingerprint(value)==expected:matches.append((candidate,value))
        if len(matches)>1:break
    if len(matches)!=1:raise ValueError('旧版美术入口顺序无法通过唯一完整指纹恢复')
    order,payload=matches[0]
    return payload,{'version':version,'site_order':list(order),'legacy_order_recovered':True}


def migrate_binding_order(binding,raw_payload):
    """Pure optional v1 -> v2 migration; existing digests/routes stay unchanged."""
    _,report=resolve_registry_payload(raw_payload,binding);result=copy.deepcopy(binding)
    registry=result['source']['registry'];registry.update(version=2,site_order=report['site_order'])
    return result,report


def seal_personal_order(binding,prepared_payload):
    """Persist personal-dispatch order and its full generation fingerprint."""
    from native_diy_binding import site_names
    from personal_binding import compose,fingerprint
    result=copy.deepcopy(binding);profile=result['profile']
    profile['site_order']=list(site_names(profile['sites'],profile['templates'],profile.get('site_order')))
    result['personal_payload_fingerprint']=fingerprint(compose(prepared_payload,result['routes'],profile))
    return result


def prepare_runtime(previous,binding,allocator_code=None):
    """Recover the actual original worker; never wrap an already prepared worker."""
    from native_diy_binding import PreparedInstaller
    from personal_binding import capture,fingerprint,code_fingerprint
    proof=binding.get('source',{});registry=proof.get('registry')
    if not registry:return previous
    if registry.get('version')not in(1,2):raise ValueError('不支持的美术注册表准备协议')
    if not isinstance(previous,types.FunctionType) or code_fingerprint(previous.__code__)!=registry['base_prepared_code_fingerprint']:
        raise ValueError('原生美术准备入口与导出核对结果不同')
    if fingerprint(capture(previous))!=registry['base_prepared_fingerprint']:raise ValueError('原生分派表在启动前已改变')
    raw=previous.__globals__.get('_start_legend_portrait_binding')
    if not isinstance(raw,types.FunctionType) or code_fingerprint(raw.__code__)!=registry['raw_code_fingerprint']:
        raise ValueError('原生美术原始回退入口不同')
    raw_payload=capture(raw)
    if fingerprint(raw_payload)!=proof['raw_fingerprint'] or fingerprint(raw_payload)!=binding['original_fingerprint']:
        raise ValueError('原生美术原始安装器指纹不同')
    if allocator_code is None:
        import diy_runtime
        candidates=[c for c in diy_runtime.prepare_binding.__code__.co_consts if isinstance(c,types.CodeType)and c.co_name=='allocate_pages']
    else:candidates=[allocator_code]
    if len(candidates)!=1 or code_fingerprint(candidates[0])!=registry['allocator_code_fingerprint']:
        raise ValueError('原生美术分配器与已核对协议不同')
    routes=merged_routes(registry['stock_routes'],registry['content_routes'])
    payload,order_report=resolve_registry_payload(raw_payload,binding)
    allocator=raw.__globals__.get('allocate_portrait_pages')
    if not callable(allocator):raise ValueError('原生美术原始分配器缺失')
    installer=PreparedInstaller(raw.__code__,previous.__code__,payload,raw_payload,routes,candidates[0])
    if installer.proof['prepared_worker_fingerprint']!=proof['prepared_worker_fingerprint']:raise ValueError('美术工作器准备结果与导出不同')
    function=installer.function(allocator,raw.__globals__);function._art_order_report=order_report
    return function
