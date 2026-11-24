"""Attach only the reproducibly built, reviewed pre-engine Java gate."""
from pathlib import Path
import hashlib,json

MARKER='assets/player-studio/resource-loader.json'

def variant_changes(source,resources,game_package):
    from apk_installation import COEXIST_GAME,GAME
    if game_package not in (GAME,COEXIST_GAME):raise ValueError('资源集启动器不支持此游戏安装系列')
    names=set(source.namelist());originals={}
    for manifest in sorted(resources.glob('startup-*/variant.json')):
        profile=json.loads(manifest.read_text('utf8'))
        if profile.get('protocol')!='player-studio-resource-variant-v1':continue
        entry=profile['dex_entry']
        if entry not in names:continue
        if entry not in originals:originals[entry]=source.read(entry)
        def bundled(relative):
            path=(resources/relative).resolve()
            if not path.is_relative_to(resources.resolve()):raise ValueError('启动组件资料路径无效')
            return path.read_bytes()
        if not any(originals[entry]==bundled(relative)for relative in profile['source_files']):continue
        mode='coexist'if game_package==COEXIST_GAME else'update'
        raw=bundled(profile['outputs'][mode])
        if not raw.startswith(b'dex\n')or b'org/efootballrevival/runtimeprobe/ResourceSetInstaller'not in raw:
            raise ValueError('随工坊提供的资源启动组件不完整：'+profile['name'])
        marker={'protocol':'player-studio-resource-loader-v1','loader_min':profile['loader_min'],
                'resource_set_protocol':profile['resource_set_protocol'],'dex':entry,
                'startup_variant':profile['name'],'startup_hook':profile['startup_hook'],
                'device_validated':profile['device_validated']}
        return {entry:raw,MARKER:json.dumps(marker,sort_keys=True,separators=(',',':')).encode('utf8')},marker
    return None

def changes(source,resources,game_package):
    resources=Path(resources)
    variant=variant_changes(source,resources,game_package)
    if variant is not None:return variant
    profile=json.loads((resources/'native-resource-loader.json').read_text(encoding='utf8'))
    name=profile['dex_entry'];original=source.read(name);digest=hashlib.sha256(original).hexdigest()
    if digest not in profile['accepted_source_sha256']:
        raise ValueError('此来源的游戏 Java 启动组件尚未适配，工程和来源文件已保留。')
    raw=(resources/profile['dex_file']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=profile['dex_sha256']:raise ValueError('随工坊提供的资源启动器校验失败')
    from apk_installation import COEXIST_GAME,GAME,patch_dex
    if game_package==COEXIST_GAME:raw,_=patch_dex(raw)
    elif game_package!=GAME:raise ValueError('资源集启动器不支持此游戏安装系列')
    expected=profile['coexist_dex_sha256']if game_package==COEXIST_GAME else profile['dex_sha256']
    if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('资源启动器共存身份转换校验失败')
    marker={'protocol':'player-studio-resource-loader-v1','loader_min':profile['loader_min'],
            'resource_set_protocol':profile['resource_set_protocol'],'dex':name,'sha256':expected,
            'startup_hook':profile['startup_hook'],'device_validated':profile['device_validated']}
    return {name:raw,MARKER:json.dumps(marker,sort_keys=True,separators=(',',':')).encode('utf8')},marker
