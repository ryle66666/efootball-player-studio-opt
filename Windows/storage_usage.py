"""Hardlink-aware disk usage inspection and protected storage cleanup."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import stat
import time
import uuid

from content_pack import Reader, atomic_json, record_path

SETTINGS_FILE = 'storage-settings.json'
DEFAULT_KEEP_APKS = 2
TMP_MAX_AGE_SECONDS = 24 * 3600
PROTECTED_CATEGORIES = frozenset({'exports_base', 'library', 'user_data'})

BROWSER_CACHE_TOP = frozenset({
    'Ad Blocking', 'Autofill', 'BrowserMetrics', 'CertificateRevocation', 'Crashpad',
    'Edge Entity Extraction', 'Edge Shopping', 'Edge Wallet', 'EdgeLanguageDetectionModel',
    'GPUPersistentCache', 'GraphiteDawnCache', 'GrShaderCache', 'OnDeviceHeadSuggestModel',
    'OptimizationHints', 'ProvenanceData', 'ProvenanceDataTensors', 'Safe Browsing',
    'SafetyTips', 'ShaderCache', 'Speech Recognition', 'Subresource Filter',
    'ZxcvbnData', 'component_crx_cache', 'hyphen-data',
})

BROWSER_CACHE_PROFILE = frozenset({
    'Cache', 'Code Cache', 'DawnGraphiteCache', 'DawnWebGPUCache',
    'EdgeCoupons', 'GPUCache',
})
BROWSER_CACHE_PROFILE_SUBPATHS = frozenset({'CacheStorage', 'ScriptCache'})
_REPARSE_ATTR = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)


def _is_link(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, 'is_junction', None)
        if callable(is_junction) and is_junction():
            return True
        st = path.lstat()
        return bool(_REPARSE_ATTR and (getattr(st, 'st_file_attributes', 0) & _REPARSE_ATTR))
    except OSError:
        return True


def inode_key(st: os.stat_result, path: Path) -> tuple:
    if getattr(st, 'st_ino', 0):
        return (st.st_dev, st.st_ino)
    return ('path', os.path.normcase(str(path)))


def load_settings(store) -> dict:
    path = store.root / SETTINGS_FILE
    keep = DEFAULT_KEEP_APKS
    review = False
    if path.is_file() and not _is_link(path):
        try:
            raw = json.loads(path.read_text('utf8'))
            value = raw.get('keep_apks')
            if type(value) is int and 1 <= value <= 20:
                keep = value
            review = raw.get('review_after_export') is True
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    return {'keep_apks': keep, 'review_after_export': review}


def save_settings(store, body: dict) -> dict:
    if not isinstance(body, dict):
        raise ValueError('存储设置格式无效')
    keep = body.get('keep_apks')
    if type(keep) is not int or not 1 <= keep <= 20:
        raise ValueError('完整 APK 保留数量须为 1 至 20 的整数')
    review = body.get('review_after_export', load_settings(store)['review_after_export'])
    if type(review) is not bool:
        raise ValueError('导出后清理提醒设置无效')
    data = {'keep_apks': keep, 'review_after_export': review}
    atomic_json(store.root / SETTINGS_FILE, data)
    return data


def _read_json(path: Path) -> dict:
    if not path.is_file() or _is_link(path):
        return {}
    try:
        value = json.loads(path.read_text('utf8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def scan_files(root: Path) -> list[tuple[Path, os.stat_result, tuple]]:
    """Iteratively scan regular files under root without crossing links or junctions."""
    root = Path(root)
    if not root.is_dir() or _is_link(root):
        return []
    entries: list[tuple[Path, os.stat_result, tuple]] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    path = Path(entry.path)
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if stat.S_ISLNK(st.st_mode):
                        continue
                    if _REPARSE_ATTR and (getattr(st, 'st_file_attributes', 0) & _REPARSE_ATTR):
                        continue
                    if stat.S_ISDIR(st.st_mode):
                        stack.append(path)
                    elif stat.S_ISREG(st.st_mode):
                        if not getattr(st, 'st_ino', 0):
                            st = os.lstat(entry.path)
                        entries.append((path, st, inode_key(st, path)))
        except OSError:
            continue
    return entries


def summarize_entries(entries: list[tuple[Path, os.stat_result, tuple]]):
    inodes: dict[tuple, int] = {}
    apparent = 0
    for _, st, ikey in entries:
        apparent += st.st_size
        if ikey not in inodes:
            inodes[ikey] = st.st_size
    return len(entries), apparent, sum(inodes.values()), inodes


def _safe_resolve(value) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return Path(value).resolve()
    except OSError:
        return None


def pack_base_status(store, package: str, program_id: str) -> dict:
    """Compare a pack with the local registration for the same game package."""
    try:
        directory = store.root / 'runtime-programs'
        record = _read_json(record_path(store, package)) if not _is_link(directory) else {}
        apk = _safe_resolve(record.get('apk'))
        if record.get('package') != package or apk is None or not apk.is_file():
            return {'status': 'unknown', 'label': '当前工坊没有可用的同包名基准'}
        current = uuid.UUID(record['program_id'])
        target = uuid.UUID(program_id)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {'status': 'unknown', 'label': '当前工坊没有可用的同包名基准'}
    if current == target:
        return {'status': 'current', 'label': '匹配当前工坊基准'}
    return {'status': 'expired', 'label': '已过期（相对当前工坊基准）', 'program_id': str(current)}


def protected_targets(store, home: Path) -> dict:
    """Collect all protected file paths, directories, and hardlink inodes."""
    store_root = store.root.resolve()
    home = Path(home).resolve()
    paths: set[Path] = set()
    inodes: set[tuple] = set()
    dirs: set[Path] = set()
    reasons: dict[Path, str] = {}
    inode_reasons: dict[tuple, str] = {}
    active_cache_namespace: str | None = None

    def protect_path(value, reason: str) -> None:
        resolved = _safe_resolve(value)
        if resolved is None:
            return
        paths.add(resolved)
        reasons.setdefault(resolved, reason)
        try:
            if not _is_link(resolved) and resolved.is_file():
                st = resolved.lstat()
                ikey = inode_key(st, resolved)
                inodes.add(ikey)
                inode_reasons.setdefault(ikey, reason)
        except OSError:
            pass

    def protect_dir(value, reason: str) -> None:
        resolved = _safe_resolve(value)
        if resolved is None:
            return
        dirs.add(resolved)
        reasons.setdefault(resolved, reason)

    runtime_dir = store_root / 'runtime-programs'
    if runtime_dir.is_dir() and not _is_link(runtime_dir):
        for item in runtime_dir.glob('*.json'):
            record = _read_json(item)
            protect_path(record.get('apk'), '小包基准 APK')
            pkgs = record.get('resource_packages')
            if isinstance(pkgs, dict):
                for pkg_path in pkgs.values():
                    protect_path(pkg_path, '小包基准配套资源')

    active = _read_json(store_root / 'active-game.json')
    for key in ('apk', 'companion'):
        protect_path(active.get(key), '当前资料库来源')
    for dep in active.get('resource_dependencies') or ():
        if isinstance(dep, dict):
            protect_path(dep.get('path'), '当前资料库配套资源')
    db_name = active.get('database')
    if isinstance(db_name, str) and re.fullmatch(r'[a-f0-9]{24}', db_name):
        db_dir = store_root / 'game-databases' / db_name
        protect_dir(str(db_dir), '当前游戏数据库')
        profile = _read_json(db_dir / 'native-resource-profile.json')
        ns = profile.get('cache_namespace')
        if isinstance(ns, str) and re.fullmatch(r'[A-Za-z0-9_-]+', ns):
            active_cache_namespace = ns
            protect_dir(str(home / 'library' / 'versions' / ns), '当前资料库缓存')

    identities = _read_json(store_root / 'apk-source-identities.json')
    for record in identities.values():
        if not isinstance(record, dict):
            continue
        for key in ('apk', 'companion'):
            protect_path(record.get(key), '已登记游戏来源')
        for dep in record.get('resource_dependencies') or ():
            if isinstance(dep, dict):
                protect_path(dep.get('path'), '已登记配套资源')

    resume = _read_json(store_root / 'export-resume.json')
    req = resume.get('request') if isinstance(resume.get('request'), dict) else {}
    for key in ('apk', 'companion'):
        protect_path(req.get(key), '导出中断恢复')
    resource_files = req.get('resource_files')
    if isinstance(resource_files, list):
        for path in resource_files:
            protect_path(path, '导出中断恢复的配套文件')
    resource_packages = req.get('resource_packages')
    if isinstance(resource_packages, dict):
        for path in resource_packages.values():
            protect_path(path, '导出中断恢复的配套文件')
    for key in ('preserved_work', 'work'):
        protect_dir(resume.get(key) or req.get(key), '导出中断恢复')

    pub = _read_json(store_root / 'export-publication.json')
    dest = pub.get('destination')
    if isinstance(dest, str) and Path(dest).name == dest and dest.startswith('APK-资源集-'):
        protect_dir(str(store_root / 'exports' / dest), '导出事务中')

    # Propagate protection across resource-set bindings and APK-资源集-* bundles.
    registry = _read_json(store_root / 'resource-set-exports.json')
    bindings: dict[Path, dict] = {}
    for section in ('artifacts', 'games', 'sets'):
        block = registry.get(section)
        if not isinstance(block, dict):
            continue
        for k, record in block.items():
            if not isinstance(record, dict):
                continue
            game = _safe_resolve(record.get('game') or (k if section != 'sets' else None))
            pkgs = record.get('resource_packages')
            if game is not None and isinstance(pkgs, dict):
                bindings.setdefault(game, pkgs)

    exports_dir = (store_root / 'exports').resolve()
    bundle_entries: dict[Path, list[tuple[Path, os.stat_result, tuple]]] = {}
    if exports_dir.is_dir() and not _is_link(exports_dir):
        for child in exports_dir.iterdir():
            if child.is_dir() and not _is_link(child) and child.name.startswith('APK-资源集-'):
                bundle_entries[child.resolve()] = scan_files(child)

    def _is_protected_file(p: Path) -> bool:
        if p in paths:
            return True
        try:
            if p.is_file() and not _is_link(p):
                return inode_key(p.lstat(), p) in inodes
        except OSError:
            pass
        return False

    while True:
        before = (len(paths), len(inodes), len(dirs))
        for game, pkgs in bindings.items():
            if _is_protected_file(game):
                reason = reasons.get(game, '受保护资源集')
                protect_path(str(game), reason)
                for dep_path in pkgs.values():
                    protect_path(dep_path, reason)
        for bundle_dir, items in bundle_entries.items():
            matched_reason = reasons.get(bundle_dir)
            if not matched_reason:
                for p, _, ikey in items:
                    if p in paths or ikey in inodes:
                        matched_reason = reasons.get(p) or inode_reasons.get(ikey) or '受保护资源集'
                        break
            if matched_reason:
                protect_dir(str(bundle_dir), matched_reason)
                for p, st, ikey in items:
                    paths.add(p)
                    inodes.add(ikey)
                    reasons.setdefault(p, matched_reason)
                    inode_reasons.setdefault(ikey, matched_reason)
        if (len(paths), len(inodes), len(dirs)) == before:
            break

    return {
        'paths': paths,
        'inodes': inodes,
        'dirs': dirs,
        'reasons': reasons,
        'inode_reasons': inode_reasons,
        'active_cache_namespace': active_cache_namespace,
    }


def _path_is_protected(path: Path, ikey: tuple | None, prot: dict) -> tuple[bool, str]:
    resolved = path.resolve()
    if resolved.suffix.lower() == '.efproject':
        return True, '球员工程文件'
    if resolved in prot['paths']:
        return True, prot['reasons'].get(resolved, '受保护项')
    if ikey is not None and ikey in prot['inodes']:
        return True, prot['inode_reasons'].get(ikey, '受保护硬链接')
    for d in prot['dirs']:
        if resolved == d or d in resolved.parents:
            return True, prot['reasons'].get(d, '受保护目录')
    return False, ''


def _browser_entries(browser_dir: Path) -> tuple[list, list]:
    """Split data/browser into safe-to-clean caches and protected profile data."""
    cache_entries: list[tuple[Path, os.stat_result, tuple]] = []
    protected_entries: list[tuple[Path, os.stat_result, tuple]] = []
    if not browser_dir.is_dir() or _is_link(browser_dir):
        return cache_entries, protected_entries
    for child in browser_dir.iterdir():
        if _is_link(child):
            continue
        if child.is_file():
            try:
                st = child.lstat()
                protected_entries.append((child.resolve(), st, inode_key(st, child.resolve())))
            except OSError:
                pass
            continue
        if not child.is_dir():
            continue
        if child.name in BROWSER_CACHE_TOP:
            cache_entries.extend(scan_files(child.resolve()))
        elif child.name == 'Default' or child.name.startswith('Profile '):
            for sub in child.iterdir():
                if _is_link(sub):
                    continue
                if sub.is_file():
                    try:
                        st = sub.lstat()
                        protected_entries.append((sub.resolve(), st, inode_key(st, sub.resolve())))
                    except OSError:
                        pass
                elif sub.is_dir():
                    if sub.name in BROWSER_CACHE_PROFILE:
                        cache_entries.extend(scan_files(sub.resolve()))
                    elif sub.name == 'Service Worker':
                        for sw in sub.iterdir():
                            if _is_link(sw):
                                continue
                            if sw.is_dir() and sw.name in BROWSER_CACHE_PROFILE_SUBPATHS:
                                cache_entries.extend(scan_files(sw.resolve()))
                            elif sw.is_dir():
                                protected_entries.extend(scan_files(sw.resolve()))
                            elif sw.is_file():
                                try:
                                    st = sw.lstat()
                                    protected_entries.append((sw.resolve(), st, inode_key(st, sw.resolve())))
                                except OSError:
                                    pass
                    else:
                        protected_entries.extend(scan_files(sub.resolve()))
        else:
            protected_entries.extend(scan_files(child.resolve()))
    return cache_entries, protected_entries


def _classify(store, home: Path, *, busy: bool = False, now: float | None = None):
    now = time.time() if now is None else now
    store_root = store.root.resolve()
    home = Path(home).resolve()
    settings = load_settings(store)
    keep_apks = settings['keep_apks']
    prot = protected_targets(store, home)
    from managed_game_base import managed_versions, records, version_alias

    # 1. Analyze exports/ grouped into APK bundle units vs packs/other files.
    exports_dir = store_root / 'exports'
    bundles: dict[Path, list[tuple[Path, os.stat_result, tuple]]] = {}
    root_files: list[tuple[Path, os.stat_result, tuple]] = []
    other_dirs_files: list[tuple[Path, os.stat_result, tuple]] = []
    if exports_dir.is_dir() and not _is_link(exports_dir):
        for child in exports_dir.iterdir():
            if _is_link(child):
                continue
            if child.is_dir():
                if child.name.startswith('APK-资源集-'):
                    bundles[child.resolve()] = scan_files(child.resolve())
                else:
                    other_dirs_files.extend(scan_files(child.resolve()))
            elif child.is_file():
                try:
                    st = child.lstat()
                    res = child.resolve()
                    root_files.append((res, st, inode_key(st, res)))
                except OSError:
                    pass

    bases_dir = store_root / 'game-bases'
    managed_entries = {entry[0]: entry for entry in scan_files(bases_dir)}
    root_entries = {entry[0]: entry for entry in root_files}
    registered = {Path(row['apk']) for row in records(store)}
    managed_base_units = []
    retired_units = []
    managed_aliases = set()
    directories = {path.parent for path in managed_entries if path.parent.parent == bases_dir}
    for directory in sorted(directories):
        for apk, receipt, data in managed_versions(store, directory):
            if apk not in managed_entries or receipt not in managed_entries:
                continue
            alias = version_alias(exports_dir, data) if not _is_link(exports_dir) else None
            members = ([root_entries[alias]] if alias in root_entries else [])
            if members:
                managed_aliases.add(alias)
            for path in (apk, receipt, apk.with_suffix('.report.json')):
                if path in managed_entries:
                    members.append(managed_entries.pop(path))
            reasons = [_path_is_protected(path, ikey, prot) for path, _, ikey in members]
            reason = next((reason for protected, reason in reasons if protected), '')
            if not reason and apk in registered:
                reason = '当前工作底包'
            unit = {'name': apk.relative_to(store_root).as_posix(), 'entries': members,
                    'reason': reason or '已退役且没有引用', 'protected': bool(reason),
                    'reclaimable': not reason, 'mtime': max(st.st_mtime for _, st, _ in members)}
            (managed_base_units if reason else retired_units).append(unit)
    for path, entry in managed_entries.items():
        _, reason = _path_is_protected(path, entry[2], prot)
        managed_base_units.append({'name': path.relative_to(store_root).as_posix(), 'entries': [entry],
                                   'reason': reason or '底包目录中的未识别文件', 'protected': True,
                                   'mtime': entry[1].st_mtime})
    root_files = [entry for entry in root_files if entry[0] not in managed_aliases]

    bundle_by_inode: dict[tuple, Path] = {}
    for bdir, entries in bundles.items():
        for _, _, ikey in entries:
            bundle_by_inode[ikey] = bdir

    units: list[dict] = []
    paired_root_paths: set[Path] = set()
    for bdir, entries in bundles.items():
        unit_entries = list(entries)
        for rp, rst, rikey in root_files:
            if bundle_by_inode.get(rikey) == bdir:
                unit_entries.append((rp, rst, rikey))
                paired_root_paths.add(rp)
        units.append({'name': bdir.name, 'entries': unit_entries, 'bundle_dir': bdir})

    pack_entries: list[tuple[Path, os.stat_result, tuple]] = []
    protected_project_entries: list[tuple[Path, os.stat_result, tuple]] = []
    for entry in other_dirs_files:
        if entry[0].suffix.lower() == '.efproject':
            protected_project_entries.append(entry)
        else:
            pack_entries.append(entry)
    for rp, rst, rikey in root_files:
        if rp in paired_root_paths:
            continue
        if rp.suffix.lower() == '.efproject':
            protected_project_entries.append((rp, rst, rikey))
        elif rp.suffix.lower() == '.apk':
            units.append({'name': rp.name, 'entries': [(rp, rst, rikey)], 'bundle_dir': None})
        else:
            pack_entries.append((rp, rst, rikey))

    base_units: list[dict] = managed_base_units
    history_units: list[dict] = []
    for unit in units:
        is_prot = False
        reason = ''
        if unit['bundle_dir'] is not None:
            is_prot, reason = _path_is_protected(unit['bundle_dir'], None, prot)
        if not is_prot:
            for p, _, ikey in unit['entries']:
                is_prot, reason = _path_is_protected(p, ikey, prot)
                if is_prot:
                    break
        mtime = max((st.st_mtime for _, st, _ in unit['entries']), default=0.0)
        unit.update(protected=is_prot, reason=reason, mtime=mtime)
        if is_prot:
            base_units.append(unit)
        else:
            history_units.append(unit)

    history_units.sort(key=lambda u: u['mtime'], reverse=True)
    for idx, unit in enumerate(history_units):
        if idx < keep_apks:
            unit['reclaimable'] = False
            unit['reason'] = f'策略保留最近 {keep_apks} 个完整 APK'
        else:
            unit['reclaimable'] = True
            unit['reason'] = f'超出最近 {keep_apks} 个保留上限'

    # 2. Library versions
    lib_versions_dir = home / 'library' / 'versions'
    lib_active_entries: list[tuple[Path, os.stat_result, tuple]] = []
    lib_old_items: list[dict] = []
    active_ns = prot['active_cache_namespace']
    if lib_versions_dir.is_dir() and not _is_link(lib_versions_dir):
        for child in lib_versions_dir.iterdir():
            if not child.is_dir() or _is_link(child):
                continue
            entries = scan_files(child.resolve())
            if active_ns is None or child.name == active_ns:
                lib_active_entries.extend(entries)
            else:
                _, app_b, act_b, _ = summarize_entries(entries)
                lib_old_items.append({
                    'name': child.name,
                    'entries': entries,
                    'dir': child.resolve(),
                    'apparent_bytes': app_b,
                    'actual_bytes': act_b,
                    'reclaimable': True,
                    'protected': False,
                    'reason': '旧版资料库缓存',
                    'mtime': max((st.st_mtime for _, st, _ in entries), default=0.0),
                })

    # 3. Browser cache vs protected profile
    browser_cache_entries, browser_prot_entries = _browser_entries(store_root / 'browser')

    # 4. Compiler cache
    compiler_entries = scan_files(store_root / 'compiler-cache')

    # 5. Temp files
    tmp_dir = store_root / 'tmp'
    tmp_items: list[dict] = []
    if tmp_dir.is_dir() and not _is_link(tmp_dir):
        for child in tmp_dir.iterdir():
            if _is_link(child):
                continue
            resolved = child.resolve()
            if child.is_dir():
                entries = scan_files(resolved)
                try:
                    newest = max([child.lstat().st_mtime, *(st.st_mtime for _, st, _ in entries)])
                except OSError:
                    newest = now
            elif child.is_file():
                try:
                    st = child.lstat()
                    entries = [(resolved, st, inode_key(st, resolved))]
                    newest = st.st_mtime
                except OSError:
                    continue
            else:
                continue
            is_prot, reason = _path_is_protected(resolved, None, prot)
            age = now - newest
            if is_prot:
                reclaimable = False
            elif busy:
                reclaimable = False
                reason = '当前存在运行中任务'
            elif age <= TMP_MAX_AGE_SECONDS:
                reclaimable = False
                reason = '24 小时内新建临时项'
            else:
                reclaimable = True
                reason = '超过 24 小时的残留临时项'
            _, app_b, act_b, _ = summarize_entries(entries)
            tmp_items.append({
                'name': child.name,
                'entries': entries,
                'path': resolved,
                'is_dir': child.is_dir(),
                'apparent_bytes': app_b,
                'actual_bytes': act_b,
                'reclaimable': reclaimable,
                'protected': is_prot,
                'reason': reason,
                'mtime': newest,
            })

    # 6. User data & protected directories
    user_entries: list[tuple[Path, os.stat_result, tuple]] = [
        *protected_project_entries,
        *browser_prot_entries,
    ]
    for folder_name in ('players', 'coaches', 'assets', 'signing', 'backups', 'game-databases', 'runtime-programs'):
        user_entries.extend(scan_files(store_root / folder_name))
    if store_root.is_dir() and not _is_link(store_root):
        for child in store_root.iterdir():
            if child.is_file() and not _is_link(child):
                try:
                    st = child.lstat()
                    res = child.resolve()
                    user_entries.append((res, st, inode_key(st, res)))
                except OSError:
                    pass

    # Compute global inode -> all observed paths to ensure accurate reclaimable bytes.
    all_entries = [
        *(e for u in base_units for e in u['entries']),
        *(e for u in retired_units for e in u['entries']),
        *(e for u in history_units for e in u['entries']),
        *pack_entries,
        *lib_active_entries,
        *(e for item in lib_old_items for e in item['entries']),
        *browser_cache_entries,
        *compiler_entries,
        *(e for item in tmp_items for e in item['entries']),
        *user_entries,
    ]
    global_inode_paths: dict[tuple, set[Path]] = {}
    global_inode_sizes: dict[tuple, int] = {}
    global_inode_links: dict[tuple, int] = {}
    for p, st, ikey in all_entries:
        global_inode_paths.setdefault(ikey, set()).add(p)
        global_inode_sizes.setdefault(ikey, st.st_size)
        global_inode_links[ikey] = max(global_inode_links.get(ikey, 0), st.st_nlink)

    def true_freed_bytes(target_entries: list[tuple[Path, os.stat_result, tuple]]) -> int:
        by_inode: dict[tuple, set[Path]] = {}
        for p, _, ikey in target_entries:
            by_inode.setdefault(ikey, set()).add(p)
        freed = 0
        for ikey, del_paths in by_inode.items():
            if ikey in prot['inodes']:
                continue
            if global_inode_paths.get(ikey, set()) <= del_paths and global_inode_links[ikey] <= len(del_paths):
                freed += global_inode_sizes[ikey]
        return freed

    # Build category payloads + deletion plans.
    categories: list[dict] = []
    plans: dict[str, dict] = {}

    def add_unit_list_items(unit_list: list[dict]) -> list[dict]:
        rows = []
        for u in unit_list:
            _, app_b, act_b, _ = summarize_entries(u['entries'])
            links = max((len(global_inode_paths.get(ikey, ())) for _, _, ikey in u['entries']), default=1)
            rows.append({
                'name': u['name'],
                'files': len(u['entries']),
                'apparent_bytes': app_b,
                'actual_bytes': act_b,
                'links': links,
                'protected': u['protected'],
                'reclaimable': bool(u.get('reclaimable', False)),
                'reason': u.get('reason', ''),
                'mtime': u['mtime'],
            })
        rows.sort(key=lambda r: r['actual_bytes'], reverse=True)
        return rows

    # Category: exports_base
    base_entries = [e for u in base_units for e in u['entries']]
    base_cnt, base_app, base_act, _ = summarize_entries(base_entries)
    if base_cnt:
        categories.append({
            'id': 'exports_base',
            'label': '基准 APK 与受保护导出',
            'description': '小包基准或当前资料库引用的完整 APK 及资源集（已对硬链接去重）。',
            'files': base_cnt,
            'apparent_bytes': base_app,
            'actual_bytes': base_act,
            'reclaimable_bytes': 0,
            'protected': True,
            'can_clean': False,
            'items': add_unit_list_items(base_units),
        })

    retired_entries = [entry for unit in retired_units for entry in unit['entries']]
    retired_count, retired_apparent, retired_actual, _ = summarize_entries(retired_entries)
    retired_freed = true_freed_bytes(retired_entries)
    if retired_count:
        categories.append({
            'id': 'managed_retired', 'label': '已退役的工作底包',
            'description': '已经替换、且资料库和恢复任务均未引用的旧底包。',
            'files': retired_count, 'apparent_bytes': retired_apparent, 'actual_bytes': retired_actual,
            'reclaimable_bytes': retired_freed, 'protected': False, 'can_clean': True,
            'items': add_unit_list_items(retired_units),
        })
    plans['managed_retired'] = {
        'label': '已退役的工作底包', 'entries': retired_entries, 'dirs': [],
        'freed_bytes': retired_freed, 'base_root': store_root, 'warning': None,
    }

    # Category: exports_history
    hist_all_entries = [e for u in history_units for e in u['entries']]
    hist_del_units = [u for u in history_units if u['reclaimable']]
    hist_del_entries = [e for u in hist_del_units for e in u['entries']]
    hist_cnt, hist_app, hist_act, _ = summarize_entries(hist_all_entries)
    hist_reclaim = true_freed_bytes(hist_del_entries)
    if hist_cnt:
        categories.append({
            'id': 'exports_history',
            'label': '历史完整 APK 导出',
            'description': f'未受保护的历史完整 APK。策略保留最新 {keep_apks} 个，超出部分可安全清理。',
            'files': hist_cnt,
            'apparent_bytes': hist_app,
            'actual_bytes': hist_act,
            'reclaimable_bytes': hist_reclaim,
            'protected': False,
            'can_clean': True,
            'items': add_unit_list_items(history_units),
        })
    plans['exports_history'] = {
        'label': '历史完整 APK 导出',
        'entries': hist_del_entries,
        'dirs': [u['bundle_dir'] for u in hist_del_units if u['bundle_dir'] is not None],
        'freed_bytes': hist_reclaim,
        'base_root': exports_dir.resolve(),
        'warning': None,
    }

    # Category: exports_packs
    pack_reclaim_entries = []
    expired_entries = []
    pack_items = []
    expired_items = []
    for entry in sorted(pack_entries, key=lambda row: row[1].st_size, reverse=True):
        path, st, ikey = entry
        protected, reason = _path_is_protected(path, ikey, prot)
        if path.suffix.lower() == '.73pack':
            try:
                manifest = Reader(path).manifest
                comparison = pack_base_status(store, manifest['package'], manifest['program_id'])
            except (OSError, ValueError, RecursionError):
                comparison = {'status': 'unknown', 'label': '小包清单无法识别'}
            reason = f"{comparison['label']} · {reason}" if protected else comparison['label']
            reclaimable = comparison['status'] == 'expired' and not protected
            if reclaimable:
                expired_entries.append(entry)
        else:
            reclaimable = not protected
            if reclaimable:
                pack_reclaim_entries.append(entry)
        items = expired_items if path.suffix.lower() == '.73pack' and reclaimable else pack_items
        items.append({
            'name': path.relative_to(exports_dir).as_posix(),
            'reason': reason or '导出文件',
            'reclaimable': reclaimable,
        })
    expired_paths = {entry[0] for entry in expired_entries}
    pack_cnt, pack_app, pack_act, _ = summarize_entries([e for e in pack_entries if e[0] not in expired_paths])
    pack_reclaim = true_freed_bytes(pack_reclaim_entries)
    if pack_cnt:
        categories.append({
            'id': 'exports_packs',
            'label': '球员小包与其它导出文件',
            'description': '小包基准匹配情况、卡图、模型与报告文件。小包由专用入口清理。',
            'files': pack_cnt,
            'apparent_bytes': pack_app,
            'actual_bytes': pack_act,
            'reclaimable_bytes': pack_reclaim,
            'protected': False,
            'can_clean': True,
            'items': pack_items,
        })
    plans['exports_packs'] = {
        'label': '球员小包与其它导出文件',
        'entries': pack_reclaim_entries,
        'dirs': [],
        'freed_bytes': pack_reclaim,
        'base_root': exports_dir.resolve(),
        'warning': None,
    }
    if expired_entries:
        _, expired_app, expired_act, _ = summarize_entries(expired_entries)
        categories.append({
            'id': 'exports_expired_packs',
            'label': '已过期小包',
            'description': '与当前工坊同包名基准不匹配。仍可用于对应的旧基准。',
            'files': len(expired_entries),
            'apparent_bytes': expired_app,
            'actual_bytes': expired_act,
            'reclaimable_bytes': true_freed_bytes(expired_entries),
            'protected': False,
            'can_clean': True,
            'items': expired_items,
        })
    plans['exports_expired_packs'] = {
        'label': '已过期小包',
        'entries': expired_entries,
        'dirs': [],
        'freed_bytes': true_freed_bytes(expired_entries),
        'base_root': exports_dir,
        'warning': '对应旧基准的手机仍可能用到这些小包。',
    }

    # Category: library (protected)
    lib_cnt, lib_app, lib_act, _ = summarize_entries(lib_active_entries)
    if lib_cnt:
        categories.append({
            'id': 'library',
            'label': '当前游戏资料库缓存',
            'description': f'当前生效游戏版本的解码资源目录（{active_ns or "默认全部版本受保护"}）。',
            'files': lib_cnt,
            'apparent_bytes': lib_app,
            'actual_bytes': lib_act,
            'reclaimable_bytes': 0,
            'protected': True,
            'can_clean': False,
            'items': [],
        })

    # Category: library_old
    lib_old_entries = [e for item in lib_old_items for e in item['entries']]
    lib_old_cnt, lib_old_app, lib_old_act, _ = summarize_entries(lib_old_entries)
    lib_old_reclaim = true_freed_bytes(lib_old_entries)
    if lib_old_cnt:
        categories.append({
            'id': 'library_old',
            'label': '旧版资料库缓存',
            'description': '已被新版本替代的历史解码资源目录。',
            'files': lib_old_cnt,
            'apparent_bytes': lib_old_app,
            'actual_bytes': lib_old_act,
            'reclaimable_bytes': lib_old_reclaim,
            'protected': False,
            'can_clean': True,
            'items': [{k: v for k, v in item.items() if k not in ('entries', 'dir')} for item in lib_old_items],
        })
    plans['library_old'] = {
        'label': '旧版资料库缓存',
        'entries': lib_old_entries,
        'dirs': [item['dir'] for item in lib_old_items],
        'freed_bytes': lib_old_reclaim,
        'base_root': lib_versions_dir.resolve(),
        'warning': None,
    }

    # Category: browser_cache
    bc_cnt, bc_app, bc_act, _ = summarize_entries(browser_cache_entries)
    bc_reclaim = true_freed_bytes(browser_cache_entries)
    if bc_cnt:
        categories.append({
            'id': 'browser_cache',
            'label': '内置浏览器组件与缓存',
            'description': 'Edge 窗口缓存与扩展组件下载，不触及 Local Storage、IndexedDB 与界面设置。',
            'files': bc_cnt,
            'apparent_bytes': bc_app,
            'actual_bytes': bc_act,
            'reclaimable_bytes': bc_reclaim,
            'protected': False,
            'can_clean': True,
            'items': [],
        })
    plans['browser_cache'] = {
        'label': '内置浏览器组件与缓存',
        'entries': browser_cache_entries,
        'dirs': [],
        'freed_bytes': bc_reclaim,
        'base_root': (store_root / 'browser').resolve(),
        'warning': '如工坊窗口仍在运行，被 Edge 锁定的部分缓存文件会自动跳过。',
    }

    # Category: compiler_cache
    cc_cnt, cc_app, cc_act, _ = summarize_entries(compiler_entries)
    cc_reclaim = true_freed_bytes(compiler_entries)
    categories.append({
        'id': 'compiler_cache',
        'label': '可重建编译缓存',
        'description': '未修改卡面、脸模和压缩块缓存；清理后下次导出自动重建。',
        'files': cc_cnt,
        'apparent_bytes': cc_app,
        'actual_bytes': cc_act,
        'reclaimable_bytes': cc_reclaim,
        'protected': False,
        'can_clean': True,
        'items': [],
    })
    plans['compiler_cache'] = {
        'label': '可重建编译缓存',
        'entries': compiler_entries,
        'dirs': [],
        'freed_bytes': cc_reclaim,
        'base_root': (store_root / 'compiler-cache').resolve(),
        'warning': None,
    }

    # Category: tmp
    tmp_all_entries = [e for item in tmp_items for e in item['entries']]
    tmp_del_items = [item for item in tmp_items if item['reclaimable']]
    tmp_del_entries = [e for item in tmp_del_items for e in item['entries']]
    tmp_cnt, tmp_app, tmp_act, _ = summarize_entries(tmp_all_entries)
    tmp_reclaim = true_freed_bytes(tmp_del_entries)
    if tmp_cnt:
        categories.append({
            'id': 'tmp',
            'label': '临时文件与任务残留',
            'description': '仅清理超过 24 小时且无活跃任务或中断恢复引用的临时文件。',
            'files': tmp_cnt,
            'apparent_bytes': tmp_app,
            'actual_bytes': tmp_act,
            'reclaimable_bytes': tmp_reclaim,
            'protected': False,
            'can_clean': True,
            'items': [{k: v for k, v in item.items() if k not in ('entries', 'path', 'is_dir')} for item in tmp_items][:50],
        })
    plans['tmp'] = {
        'label': '临时文件与任务残留',
        'entries': tmp_del_entries,
        'dirs': [item['path'] for item in tmp_del_items if item['is_dir']],
        'freed_bytes': tmp_reclaim,
        'base_root': tmp_dir.resolve(),
        'warning': None,
    }

    # Category: user_data (protected)
    ud_cnt, ud_app, ud_act, _ = summarize_entries(user_entries)
    if ud_cnt:
        categories.append({
            'id': 'user_data',
            'label': '工程、素材、签名与浏览器设置',
            'description': '玩家自建球员/教练工程、素材、签名、数据库与浏览器个人存储（永久受保护）。',
            'files': ud_cnt,
            'apparent_bytes': ud_app,
            'actual_bytes': ud_act,
            'reclaimable_bytes': 0,
            'protected': True,
            'can_clean': False,
            'items': [],
        })

    categories.sort(key=lambda c: c['actual_bytes'], reverse=True)
    _, total_app, total_act, _ = summarize_entries(all_entries)
    all_del_entries = [
        *retired_entries,
        *hist_del_entries,
        *pack_reclaim_entries,
        *expired_entries,
        *lib_old_entries,
        *browser_cache_entries,
        *compiler_entries,
        *tmp_del_entries,
    ]
    total_reclaim = true_freed_bytes(all_del_entries)
    active_source = _safe_resolve(_read_json(store_root / 'active-game.json').get('apk'))
    summary = {
        'total_apparent_bytes': total_app,
        'total_actual_bytes': total_act,
        'total_reclaimable_bytes': total_reclaim,
        'hardlink_saved_bytes': max(0, total_app - total_act),
        'keep_apks': keep_apks,
        'review_after_export': settings['review_after_export'],
        'exported_library_source': str(active_source) if active_source and (
            active_source.is_relative_to(exports_dir) or active_source.is_relative_to(bases_dir)) else None,
        'busy': bool(busy),
        'categories': categories,
        'generated_at': now,
    }
    return summary, plans, prot


def inspect_storage(store, home: Path, *, busy: bool = False, now: float | None = None) -> dict:
    summary, _, _ = _classify(store, home, busy=busy, now=now)
    return summary


def preview_cleanup(store, home: Path, category: str, *, busy: bool = False, now: float | None = None) -> dict:
    if category in PROTECTED_CATEGORIES:
        raise ValueError('该分类受保护，不允许清理')
    _, plans, _ = _classify(store, home, busy=busy, now=now)
    plan = plans.get(category)
    if plan is None:
        raise ValueError('未知的存储清理分类：' + str(category))
    base_root = plan['base_root']
    targets = []
    for path, _, _ in plan['entries']:
        try:
            targets.append(path.relative_to(base_root).as_posix())
        except ValueError:
            targets.append(path.name)
    _, app_b, act_b, _ = summarize_entries(plan['entries'])
    return {
        'category': category,
        'label': plan['label'],
        'files': len(plan['entries']),
        'apparent_bytes': app_b,
        'actual_bytes': act_b,
        'freed_bytes': plan['freed_bytes'],
        'targets': sorted(targets),
        'warning': plan['warning'],
        'confirmation': _confirmation(category, plan),
    }


def _confirmation(category: str, plan: dict) -> str:
    entries = sorted((str(path), ikey, st.st_size, st.st_mtime_ns) for path, st, ikey in plan['entries'])
    return hashlib.sha256(json.dumps([category, entries]).encode('utf8')).hexdigest()


def review_after_export(store, home: Path) -> dict | None:
    if not load_settings(store)['review_after_export']:
        return None
    review = preview_cleanup(store, home, 'exports_history')
    logging.info('Post-export APK cleanup review: files=%d expected_freed=%d',
                 review['files'], review['freed_bytes'])
    return {key: review[key] for key in ('files', 'freed_bytes')}


def _prune_delivery_index(store) -> None:
    index_path = store.root / 'export-delivery.json'
    if not index_path.is_file() or _is_link(index_path):
        return
    try:
        index = json.loads(index_path.read_text('utf8'))
    except (OSError, ValueError):
        return
    if not isinstance(index, dict):
        return
    exports_root = (store.root / 'exports').resolve()
    cleaned = {}
    for source_str, row in index.items():
        if not isinstance(row, dict):
            continue
        src = Path(source_str)
        name = row.get('name', '')
        if src.is_file() and isinstance(name, str) and name and (exports_root / name).is_file():
            cleaned[source_str] = row
    if len(cleaned) != len(index):
        try:
            atomic_json(index_path, cleaned)
        except OSError:
            pass


def execute_cleanup(server, category: str, *, now: float | None = None, confirmation: str | None = None) -> dict:
    if server.native_package.status()['phase'] == 'working' or server.game_import.status()['phase'] == 'working':
        raise ValueError('请等待当前导出或导入任务完成')
    if category in PROTECTED_CATEGORIES:
        raise ValueError('该分类受保护，不允许清理')
    if category == 'managed_retired' and not confirmation:
        raise ValueError('请先预览并确认要清理的旧底包')
    store = server.store
    home = server.library.home
    with store.lock:
        _, plans, prot = _classify(store, home, busy=False, now=now)
        plan = plans.get(category)
        if plan is None:
            raise ValueError('未知的存储清理分类：' + str(category))
        if confirmation is not None and confirmation != _confirmation(category, plan):
            raise ValueError('清理目标已变化，请重新预览后确认')
        base_root = plan['base_root']
        entries = plan['entries']
        for path, _, ikey in entries:
            if _is_link(path):
                raise ValueError('待清理文件包含外部链接，已取消操作')
            resolved = path.resolve()
            if not resolved.is_relative_to(base_root):
                raise ValueError('待清理文件超出预期目录，已取消操作')
            is_prot, reason = _path_is_protected(resolved, ikey, prot)
            if is_prot:
                raise ValueError(f'存在受保护文件（{reason}），已拒绝清理')

        logging.info('Storage cleanup starting: category=%s files=%d expected_freed=%d',
                     category, len(entries), plan['freed_bytes'])

        if category == 'compiler_cache':
            from compile_cache import clear
            res = clear(store.root / 'compiler-cache')
            logging.info('Storage cleanup finished: category=%s deleted=%d freed=%d',
                         category, res['files'], res['bytes'])
            return {
                'category': category,
                'deleted_files': res['files'],
                'freed_bytes': res['bytes'],
                'skipped_locked': 0,
                'message': f"已清理编译缓存 {res['files']} 个文件",
            }

        deleted_paths: list[Path] = []
        deleted_inodes: dict[tuple, int] = {}
        skipped_locked = 0
        for path, st, ikey in entries:
            try:
                path.unlink()
                deleted_paths.append(path)
                deleted_inodes[ikey] = st.st_size
            except OSError:
                if category == 'browser_cache':
                    skipped_locked += 1
                else:
                    raise

        # Remove emptied directories only within base_root.
        candidate_dirs = set(plan['dirs'])
        for path in deleted_paths:
            parent = path.parent
            while parent != base_root and parent.is_relative_to(base_root):
                candidate_dirs.add(parent)
                parent = parent.parent
        for folder in sorted(candidate_dirs, key=lambda p: len(p.parts), reverse=True):
            if folder.is_dir() and not _is_link(folder) and folder.resolve().is_relative_to(base_root):
                try:
                    if not any(folder.iterdir()):
                        folder.rmdir()
                except OSError:
                    pass

        if category in ('exports_history', 'exports_packs', 'exports_expired_packs', 'managed_retired'):
            _prune_delivery_index(store)

        # Report freed bytes as min(planned, deleted distinct inodes), conservative if hardlinks remain.
        deleted_set = set(deleted_paths)
        freed_bytes = plan['freed_bytes'] if deleted_set >= {p for p, _, _ in entries} else sum(
            size for ikey, size in deleted_inodes.items() if ikey not in prot['inodes']
        )
        logging.info('Storage cleanup finished: category=%s deleted=%d freed=%d skipped_locked=%d',
                     category, len(deleted_paths), freed_bytes, skipped_locked)
        msg = f'已清理 {len(deleted_paths)} 个文件'
        if skipped_locked:
            msg += f'（跳过 {skipped_locked} 个正被占用的缓存文件）'
        return {
            'category': category,
            'deleted_files': len(deleted_paths),
            'freed_bytes': freed_bytes,
            'skipped_locked': skipped_locked,
            'message': msg,
        }
