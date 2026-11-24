"""Keep the working APK independent from its disposable delivery link."""
import json
import logging
import os
from pathlib import Path
import uuid
import zipfile

from content_pack import atomic_json, installed_program, remember_program
from export_delivery import stamp
from workspace_tools import export_file, export_folder


def managed_path(store, package, program_id):
    from content_pack import record_path
    record_path(store, package)
    root = store.root.resolve()
    directory = root / 'game-bases' / package
    if directory.resolve() != directory or directory.is_junction():
        raise ValueError('底包目录是外部链接')
    return directory / (str(uuid.UUID(program_id)) + '.apk')


def records(store):
    registry = store.root / 'runtime-programs'
    if registry.is_symlink() or registry.is_junction():
        return
    for path in registry.glob('*.json'):
        if path.is_symlink() or path.is_junction():
            continue
        try:
            row = installed_program(store, path.stem)
            if row and row.get('managed') is True and row.get('package') == path.stem:
                expected = managed_path(store, path.stem, row['program_id'])
                if Path(row['apk']) == expected and not expected.is_symlink() and expected.is_file():
                    yield {**row, 'mtime_ns': expected.stat().st_mtime_ns}
        except (OSError, ValueError, TypeError, AttributeError):
            continue


def delivery_aliases(store):
    """Recognize only published links whose managed APK still exists."""
    folder = export_folder(store)
    aliases = set()
    for row in records(store):
        name = row.get('delivery_name', '')
        if not isinstance(name, str) or not name or Path(name).name != name:
            continue
        path = folder / name
        try:
            if not path.is_symlink() and not path.is_junction() and path.samefile(row['apk']):
                aliases.add(path)
        except OSError:
            continue
    return aliases


def managed_versions(store, directory):
    """Find owned files using the recorded identity of each artifact."""
    if directory.is_symlink() or directory.is_junction():
        return
    for receipt in directory.glob('*.managed.json'):
        if receipt.is_symlink() or receipt.is_junction():
            continue
        try:
            program_id = receipt.name.removesuffix('.managed.json')
            path = managed_path(store, directory.name, program_id)
            if path.is_symlink() or path.is_junction():
                continue
            data = json.loads(receipt.read_text('utf8'))
            if list(stamp(path)) == data['stamp']:
                yield path, receipt, data
        except (OSError, ValueError, TypeError, KeyError):
            continue


def version_alias(folder, data):
    name = data.get('delivery_name')
    if not isinstance(name, str) or not name or Path(name).name != name or any(c in name for c in '/\\:'):
        return None
    path = folder / name
    try:
        if not path.is_symlink() and not path.is_junction() and list(stamp(path)) == data['stamp']:
            return path
    except OSError:
        pass
    return None


def publish(store, result, package, program_id, *, source, library):
    from export_delivery import expose

    output = Path(result['path'])
    folder = export_folder(store)
    relative = output.relative_to(folder).as_posix()
    if export_file(store, relative) != output or not output.parent.name.startswith('APK-资源集-'):
        raise ValueError('待登记 APK 资源集路径无效')
    completion = output.parent / 'complete.json'
    receipt = json.loads(completion.read_text('utf8'))
    verification = receipt.get('validation', {})
    if completion.is_symlink() or completion.is_junction() or not verification.get('verified') or not verification.get('live_data_verified'):
        raise ValueError('待登记底包尚未通过完整校验')
    packaged = {entry.name for entry in output.parent.iterdir()}
    report = Path(result['report_path'])
    expected = {output.name, report.name, completion.name}
    if packaged != expected or report.parent != output.parent or report.is_symlink() or report.is_junction():
        raise ValueError('待登记资源集包含额外文件')

    original_stamp = stamp(output)
    base = managed_path(store, package, program_id)
    base.parent.mkdir(parents=True, exist_ok=True)
    created = not base.exists()
    if created:
        os.link(output, base)
    owned_receipt = base.with_suffix('.managed.json')
    try:
        if base.is_symlink() or stamp(base) != original_stamp or not base.samefile(output):
            raise ValueError('新底包路径已存在不同的文件')
        result = expose(store, result)
        alias = export_file(store, result['name'])
        if not alias.samefile(base):
            raise ValueError('交付文件未能与底包共用磁盘空间')
        atomic_json(owned_receipt, {'stamp': list(original_stamp), 'delivery_name': alias.name})
        remember_program(store, package, program_id, base, source=source,
                         managed=True, delivery_name=alias.name)
    except BaseException:
        if created:
            base.unlink(missing_ok=True)
            owned_receipt.unlink(missing_ok=True)
        raise

    message = None
    try:
        managed_report = base.with_suffix('.report.json')
        os.replace(report, managed_report)
        result['report_path'] = str(managed_report)
        if not output.samefile(base):
            raise ValueError('交付完成后资源集文件发生变化')
        output.unlink()
        completion.unlink()
        output.parent.rmdir()
    except (OSError, ValueError):
        message = '新底包已登记，旧格式资源集残留可稍后清理。'

    # Keep the active library readable, moving its APK reference only when the
    # existing signer and adapter verification both accept the new base.
    try:
        from apk_sources import recorded, rebind_exported_source
        active = recorded(library, store).get('apk')
        if active and Path(active) != base:
            root = store.root.resolve()
            if Path(active).resolve().is_relative_to(folder) or Path(active).resolve().is_relative_to(root/'game-bases'):
                rebind_exported_source(library, store, active, str(base))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile):
        message = '当前资料库仍使用原 APK，原来源已保留。'

    result['path'] = str(alias)
    result['base_apk'] = str(base)
    for artifact in result.get('artifacts', []):
        if artifact.get('kind') == 'game':
            artifact['path'] = str(alias)
    if message:
        logging.warning('Managed game base %s: %s', base, message)
        result['storage_warning'] = message
    return result


def prune(store, home, result):
    """After successful completion, retire superseded workshop-owned links."""
    from storage_usage import _path_is_protected, inode_key, protected_targets
    base, alias = Path(result['base_apk']), Path(result['path'])
    folder = export_folder(store)
    protection = protected_targets(store, home)

    def referenced(path):
        return _path_is_protected(path, inode_key(path.lstat(), path), protection)[0]

    for path, receipt, data in managed_versions(store, base.parent):
        if path == base or referenced(path):
            continue
        old_alias = version_alias(folder, data)
        report = path.with_suffix('.report.json')
        files = [path, receipt, *([report] if report.is_file() else [])]
        if old_alias and old_alias != alias:
            files.append(old_alias)
        if any(file.is_symlink() or file.is_junction() or referenced(file) for file in files):
            continue
        if list(stamp(path)) == data['stamp']:
            if old_alias and old_alias != alias:
                old_alias.unlink()
            path.unlink()
            for file in (receipt, report):
                file.unlink(missing_ok=True)
