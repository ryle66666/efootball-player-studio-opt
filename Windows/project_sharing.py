"""Portable multi-player projects with verified assets and collision-safe import."""
from export_errors import export_context, exported, call as export_call
from pathlib import Path
import json,uuid
import os
import re
import time
import zipfile

from export_payload import measure
import player_package as migration


def write_project_archive(destination: Path, document: str, data: bytes, assets: dict[str, Path],
                          manifest: dict, *, limit: int | None = None) -> None:
    temporary = destination.with_name('.incoming-' + uuid.uuid4().hex)
    digests = {document: migration.digest(data)}
    total = len(data)
    try:
        with zipfile.ZipFile(temporary, 'x', zipfile.ZIP_DEFLATED) as archive:
            for name, path in sorted(assets.items()):
                with export_context('工程素材', path.name, '读取图片、模型或动画文件'), path.open('rb') as source:
                    size = os.fstat(source.fileno()).st_size
                    if limit is not None and total + size > limit:
                        raise ValueError('工程包总大小超过 512 MB，请分批导出')
                    entry = zipfile.ZipInfo(name, time.localtime(time.time())[:6])
                    entry.compress_type = zipfile.ZIP_DEFLATED
                    entry.file_size = size
                    with archive.open(entry, 'w') as output:
                        metadata = measure(source, output)
                    if metadata['bytes'] != size:
                        raise ValueError('工程素材在导出期间发生变化')
                    digests[name] = metadata['sha256']
                    total += size
            archive.writestr('manifest.json', migration.compact({**manifest, 'sha256': digests}).encode('utf8'))
            archive.writestr(document, data)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)

@exported('多球员工程','导出所选工程',lambda store,selected,current: "所选球员 "+str(selected))
def export_selected(store,selected,current):
    import app
    if not isinstance(selected,list) or not 1<=len(selected)<=100 or len(set(selected))!=len(selected):raise ValueError('请选择 1–100 名球员')
    projects=[app.normalize_project(current) if uid==current['package_id'] else export_call('球员',uid,'读取待导出工程',store.saved_player,uid)['project'] for uid in selected]
    data=json.dumps(projects,ensure_ascii=False,indent=2).encode()
    if len(data)>512*1024*1024:raise ValueError('工程包总大小超过 512 MB，请分批导出')
    assets={}
    for project in projects:
        with export_context('球员',project,'收集工程资源'):
            for name in app.referenced_assets(project):
                with export_context('工程素材',name,'读取图片、模型或动画文件'):
                    path=store.root/'assets'/name
                    if not path.is_file():raise ValueError('工程引用的资源缺失：'+name)
                    assets['assets/'+name]=path
    manifest={'format':'73efootball-studio-project','version':2,'game_platform':app.GAME_PLATFORM,'players':len(projects)}
    name='球员合集-'+str(len(projects))+'人-'+uuid.uuid4().hex[:8]+'.efproject';target=store.root/'exports'/name
    write_project_archive(target, 'projects.json', data, assets, manifest, limit=512*1024*1024)
    return {'url':'/exports/'+name,'filename':name,'path':str(target),'players':len(projects)}

def read_archive_json(archive: zipfile.ZipFile, name: str, checksum: str | None = None):
    migration.require(archive.getinfo(name).file_size <= migration.MAX_JSON, 'JSON 过大')
    raw = archive.read(name)
    if checksum is not None and migration.digest(raw) != checksum:
        raise ValueError('工程文档校验失败：' + name)
    return migration.read_json(raw)


def stage_project_archive(archive: zipfile.ZipFile, manifest: dict, document: str, staging: Path,
                          *, asset_pattern: re.Pattern[str], max_asset: int) -> tuple[object, dict[str, Path]]:
    """Verify the whole package before passing staged files to the store."""
    checksums = manifest.get('sha256')
    if (not isinstance(checksums, dict) or document not in checksums or 'manifest.json' in checksums
            or set(archive.namelist()) != set(checksums) | {'manifest.json'}):
        raise ValueError('工程包清单不完整')
    if any(not isinstance(checksum, str) or len(checksum) != 64 for checksum in checksums.values()):
        raise ValueError('工程包校验值无效')
    value = read_archive_json(archive, document, checksums[document])
    assets = {}
    for member, checksum in checksums.items():
        if member == document:
            continue
        name = member.removeprefix('assets/')
        if name == member or not asset_pattern.fullmatch(name):
            raise ValueError('工程包含未知文件')
        entry = archive.getinfo(member)
        if entry.file_size > max_asset or name[:64] != checksum:
            raise ValueError('资源内容与永久文件名不一致')
        destination = staging / name
        with archive.open(entry) as source, destination.open('xb') as output:
            metadata = measure(source, output)
        if metadata['sha256'] != checksum or metadata['bytes'] != entry.file_size:
            raise ValueError('工程包校验失败：' + member)
        assets[name] = destination
    return value, assets
