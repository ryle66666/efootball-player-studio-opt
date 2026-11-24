"""Check each independent game/resource installation without mixing editions."""
import hashlib,json,zipfile
from pathlib import Path
from apk_installation import GAME,RESOURCES,COEXIST_GAME,COEXIST_RESOURCES,identity
import resource_set as resource_sets

def inspect(apk):
    with zipfile.ZipFile(apk) as z:
        kind='game' if 'assets/gateway-release/release-manifest.json' in z.namelist() else 'extension'if resource_sets.VOLUME_MANIFEST in z.namelist()else'resource' if 'assets/resource-bootstrap/resources.zip' in z.namelist() else None
        descriptor=json.loads(z.read(resource_sets.VOLUME_MANIFEST))if kind=='extension'else None
    if not kind:raise ValueError('APK 不是支持的游戏或配套资源包')
    result=dict(identity(apk),kind=kind,path=str(apk))
    if kind=='game':
        manifest=resource_sets.read_manifest(apk)
        if manifest:result.update(resource_set_id=manifest['set_id'],resource_dependencies=[p for p in manifest['packages']if p['role']!='game'])
    if descriptor:
        dep=descriptor.get('package')or{}
        if descriptor.get('protocol')!=resource_sets.PROTOCOL or dep.get('role')!='extension'or any(result.get(k)!=dep.get(k)for k in('package','version_code'))or set(result['signers'])!=set(dep.get('signers',[])):
            raise ValueError('扩展资源APK实际身份与卷描述不匹配')
        result.update(content_sha256=dep['content_sha256'],volume=dep['volume'])
    return result


def expected_dependencies(game,resources=()):
    if 'resource_dependencies'in game:return game['resource_dependencies']
    path=game.get('path')
    if path and Path(path).is_file():
        manifest=resource_sets.read_manifest(path)
        if manifest:return[p for p in manifest['packages']if p['role']!='game']
    return[dict(package=expected_resources(game,resources),role='offline',legacy=True)]


def _offline_hash(row):
    if row.get('content_sha256'):return row['content_sha256']
    path=row.get('path')
    if not path:raise ValueError('尚未核对离线资源内容：'+row['package'])
    hash_value=hashlib.sha256()
    with zipfile.ZipFile(path)as archive,archive.open('assets/resource-bootstrap/resources.zip')as stream:
        while block:=stream.read(1024*1024):hash_value.update(block)
    return hash_value.hexdigest()

def expected_resources(game,resources):
    known={GAME:RESOURCES,COEXIST_GAME:COEXIST_RESOURCES}
    if game['package'] in known:return known[game['package']]
    with zipfile.ZipFile(game['path']) as z:dex=[z.read(n) for n in z.namelist() if n.endswith('.dex')]
    matches=[r['package'] for r in resources if any(a.encode() in raw for a in r.get('authorities',[]) for raw in dex)]
    if len(set(matches))!=1:raise ValueError('无法确认这份 DIY 游戏依赖的资源包，请使用配套安装包')
    return matches[0]

def check(rows,errors=(),target=None):
    resources=[r for r in rows if r['kind']in('resource','extension')];results=[]
    games=[r for r in rows if r['kind']=='game' and (not target or r['package']==target)]
    for game in games:
        checks=[]
        try:
            dependencies=expected_dependencies(game,resources);checks=[]
            for dep in dependencies:
                expected=dep['package'];resource=next((r for r in resources if r['package']==expected),None)
                passed=bool(resource and set(resource.get('signers',[]))&set(game.get('signers',[])))
                message='游戏和资源包名依赖、签名配套'if passed else'缺少配套资源 '+expected if not resource else'游戏与资源签名不同，不配套'
                if passed and not dep.get('legacy'):
                    passed=(set(resource['signers'])==set(dep['signers'])==set(game['signers'])and resource.get('version_code')==dep['version_code'])
                    if not passed:message='资源版本或签名不匹配：'+expected
                    else:
                        content=_offline_hash(resource)if dep['role']=='offline'else resource.get('content_sha256')
                        passed=content==dep['content_sha256']
                        if not passed:message='资源内容版本不匹配：'+expected
                if resource and resource.get('dex_identity_valid')is False:passed=False;message='共存资源 DEX 索引损坏，请使用新版工坊重新生成并同签名覆盖安装资源 APK'
                checks.append(dict(package=expected,role=dep['role'],passed=passed,message=message))
            expected=next((p['package']for p in dependencies if p['role']=='offline'),None)
            passed=all(c['passed']for c in checks);message='全部资源依赖身份、内容版本与签名配套'if passed else next(c['message']for c in checks if not c['passed'])
            if game.get('dex_identity_valid')is False:passed=False;message='共存游戏 DEX 索引损坏，请使用新版工坊重新生成游戏 APK'
        except(ValueError,OSError,KeyError,zipfile.BadZipFile)as exc:expected=None;passed=False;message=str(exc)
        results.append({'game':game['package'],'resource':expected,'passed':passed,'message':message,'dependencies':checks})
    if not games:results.append({'game':target or '', 'resource':None,'passed':False,'message':'未发现可检查的游戏 APK'})
    return {'passed':all(r['passed'] for r in results) and not errors,'pairs':results,'errors':list(errors),'coexist_allowed':True}

def validate_install(incoming,installed):
    row=inspect(incoming);rows=[r for r in installed if r['package']!=row['package']]+[row]
    if row.get('dex_identity_valid') is False:raise ValueError('共存 APK 的 DEX 索引损坏，请使用新版工坊重新生成后安装')
    if row['kind']=='game':
        result=check(rows,target=row['package'])
        if not result['passed']:raise ValueError('安装配套检查未通过：'+result['pairs'][0]['message']+'。请先安装对应资源 APK。')
        return result
    for game in [r for r in installed if r['kind']=='game']:
        try:dependent=any(p['package']==row['package']for p in expected_dependencies(game,[row]))
        except ValueError:continue
        if dependent and not set(game['signers'])&set(row['signers']):raise ValueError('资源与已安装的对应游戏签名不同，不配套')
    return {'passed':True,'resource_first':True}
