"""Validate, stage and apply Releases for the Windows launcher layout."""

import ast
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import filecmp
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile


FORMAT = 'player-studio-windows-update-v1'
JOURNAL = '.studio-update-recovery.json'
LOCK_FILE = '.studio-update.lock'
ASSET_SUFFIX = '-windows-update.zip'
PORTABLE_EXE = '球员工坊-优化版.exe'
PORTABLE_VERSION_FILE = 'studio-version.json'
REQUIRED = {'app.py', 'updates.py', 'update_package.py', 'resources/github-updates.json',
            'web/dist/current/index.html'}
PORTABLE_REQUIRED = {PORTABLE_EXE, PORTABLE_VERSION_FILE, 'tools/asset-decoder/cue4parse.exe',
                     'tools/video/ffmpeg.exe', 'tools/apk-delta/xdelta3.exe'}
PROGRAM_DIRS = {'resources', 'tools', 'web'}
GITHUB_ASSET_HOSTS = {'github.com', 'release-assets.githubusercontent.com',
                      'objects.githubusercontent.com', 'github-releases.githubusercontent.com'}


def version_from_source(source):
    names = {key.id: node.value for node in ast.parse(source).body if isinstance(node, ast.Assign)
             for key in node.targets if isinstance(key, ast.Name) and key.id in ('RELEASE_VERSION', 'APP_VERSION')}
    version_node = names.get('RELEASE_VERSION') or names.get('APP_VERSION')
    if version_node:
        return ast.literal_eval(version_node)
    raise ValueError('程序缺少版本号')


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf8') as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def program_path(value, portable=False):
    parts = value.split('/')
    if any(not part or part in ('.', '..') or part.rstrip(' .') != part
           or re.search(r'[<>:"\\|?*\x00-\x1f]', part)
           or PureWindowsPath(part).is_reserved() for part in parts):
        raise ValueError('更新包路径无效')
    if portable and len(parts) == 1 and parts[0] in (PORTABLE_EXE, PORTABLE_VERSION_FILE):
        return Path(*parts)
    if not portable and len(parts) == 1 and (re.fullmatch(r'[a-z][a-z0-9_]*\.py', parts[0]) or parts[0] == 'requirements.txt'):
        return Path(*parts)
    if (len(parts) > 1 and parts[0] in ({'tools'} if portable else PROGRAM_DIRS)
            and not any(part.startswith('.') or part == '__pycache__' for part in parts[1:])):
        return Path(*parts)
    raise ValueError('更新包包含非程序文件：' + value)


def checked_path(root, relative):
    target = root / relative
    for path in (target, *target.parents):
        if path.is_symlink() or path.is_junction():
            raise ValueError('更新路径包含链接：' + str(path))
        if path == root:
            break
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('更新路径超出安装目录')
    return target


def download_package(url, destination, expected_sha256, progress=None, expected_bytes=None):
    if not re.fullmatch(r'[0-9a-f]{64}', expected_sha256 or ''):
        raise ValueError('更新包缺少有效的 SHA-256')
    destination = Path(destination)
    partial = destination.with_suffix('.partial')
    total = 0
    digest = hashlib.sha256()
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'PlayerStudio-Updater'})
        with urllib.request.urlopen(request, timeout=30) as response, partial.open('xb') as output:
            final = urllib.parse.urlsplit(response.geturl())
            if final.scheme != 'https' or final.hostname not in GITHUB_ASSET_HOSTS:
                raise ValueError('更新包下载地址无效')
            for chunk in iter(lambda: response.read(1024 * 1024), b''):
                total += len(chunk)
                if total > min(expected_bytes or 2 * 1024**3, 2 * 1024**3):
                    raise ValueError('更新包超过发布大小')
                output.write(chunk)
                digest.update(chunk)
                if progress:
                    progress(total)
        if digest.hexdigest() != expected_sha256 or expected_bytes is not None and total != expected_bytes:
            raise ValueError('更新包校验失败')
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)
    return total


def stage_package(archive, destination, expected_version, portable=False):
    destination = Path(destination)
    if destination.exists():
        raise ValueError('更新暂存目录已存在')
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        manifest = [item for item in entries if item.filename == 'studio-update.json']
        if len(manifest) != 1 or manifest[0].file_size > 4096:
            raise ValueError('更新包缺少版本清单')
        info = json.loads(package.read(manifest[0]))
        expected = {'format': FORMAT, 'version': expected_version}
        if portable:
            expected['layout'] = 'portable'
        if info != expected:
            raise ValueError('更新包版本与 Release 不一致')
        seen = set()
        files = []
        unpacked = 0
        for item in entries:
            if item is manifest[0]:
                continue
            prefix = 'Portable/' if portable else 'Windows/'
            if (portable and item.filename == 'Portable/README.md' and not item.is_dir()
                    and not stat.S_ISLNK(item.external_attr >> 16)):
                continue
            if not item.filename.startswith(prefix) or item.is_dir() or stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError('更新包包含无效条目')
            relative = program_path(item.filename.removeprefix(prefix), portable)
            key = relative.as_posix().casefold()
            if key in seen:
                raise ValueError('更新包包含重复文件')
            seen.add(key)
            files.append((item, relative))
            unpacked += item.file_size
        required = {name.casefold() for name in PORTABLE_REQUIRED} if portable else REQUIRED
        if not required <= seen or unpacked > 4 * 1024**3 or len(files) > 50000:
            raise ValueError('更新包缺少程序文件或体积超限')
        destination.mkdir()
        for item, relative in files:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(item) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output)
    staged_version = (json.loads((destination / PORTABLE_VERSION_FILE).read_text('utf8'))['version'] if portable
                      else version_from_source((destination / 'app.py').read_text('utf8')))
    if staged_version != expected_version:
        raise ValueError('更新包中的程序版本与发布版本不一致')
    return len(files)


def _replace_file(source, target):
    temporary = target.with_name(target.name + '.studio-updating')
    if temporary.is_symlink() or temporary.is_junction():
        raise ValueError('更新暂存路径包含链接')
    temporary.unlink(missing_ok=True)
    try:
        with source.open('rb') as stream, temporary.open('xb') as output:
            shutil.copyfileobj(stream, output)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def recover_update(home):
    journal_path = home / JOURNAL
    journal = json.loads(journal_path.read_text('utf8'))
    work = Path(journal['work'])
    if not re.fullmatch(r'studio-update-[a-z0-9_]{8}', work.name):
        raise ValueError('更新恢复记录无效')
    for entry in reversed(journal['files']):
        relative = program_path(entry['path'], journal.get('portable', False))
        target = checked_path(home, relative)
        if entry['existed']:
            backup = checked_path(work / 'backup', relative)
            if not target.is_file() or not filecmp.cmp(backup, target, shallow=False):
                _replace_file(backup, target)
        else:
            target.unlink(missing_ok=True)
    journal_path.unlink()


def install_staged(work, home, portable=False):
    work, home = Path(work), Path(home)
    stage, backup = work / 'stage', work / 'backup'
    journal_path = home / JOURNAL
    if journal_path.exists() or backup.exists():
        raise ValueError('存在尚未恢复的更新')
    files = [program_path(path.relative_to(stage).as_posix(), portable) for path in stage.rglob('*') if path.is_file()]
    if not (PORTABLE_REQUIRED if portable else REQUIRED) <= {path.as_posix() for path in files}:
        raise ValueError('更新暂存目录不完整')
    backup.mkdir()
    journal = {'work': str(work), 'files': [], 'portable': portable}
    atomic_json(journal_path, journal)
    try:
        # Keep the startup and recovery entry points for the end of replacement.
        for relative in sorted(files, key=lambda path: path.name in ('app.py', 'update_package.py', PORTABLE_EXE)):
            target = checked_path(home, relative)
            source = checked_path(stage, relative)
            existed = target.exists()
            if existed:
                if target.is_file() and filecmp.cmp(source, target, shallow=False):
                    continue
                old = backup / relative
                old.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, old)
            journal['files'].append({'path': relative.as_posix(), 'existed': existed})
            atomic_json(journal_path, journal)
            target.parent.mkdir(parents=True, exist_ok=True)
            _replace_file(source, target)
    except Exception:
        recover_update(home)
        raise
    journal_path.unlink()
    return len(files)


@contextmanager
def update_lock(home):
    import msvcrt

    path = checked_path(home, Path(LOCK_FILE))
    with path.open('a+b') as stream:
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise ValueError('工坊正在更新，请稍候') from exc
        try:
            yield
        finally:
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def find_update_window(title):
    find = ctypes.windll.user32.FindWindowW
    find.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    find.restype = wintypes.HWND
    return find(None, title)


def post_close(hwnd):
    post = ctypes.windll.user32.PostMessageW
    post.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    post.restype = wintypes.BOOL
    return post(hwnd, 0x0010, 0, 0)


def remove_work(path, parent):
    path, parent = Path(path), Path(parent)
    if (path.parent != parent or not re.fullmatch(r'studio-update-[a-z0-9_]{8}', path.name)
            or path.is_symlink() or path.is_junction() or parent.is_symlink() or parent.is_junction()
            or path.resolve().parent != parent.resolve()):
        raise ValueError('更新工作目录无效')
    shutil.rmtree(path)


def wait_for_exit(pid, expected_image=None):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.WaitForSingleObject.restype = ctypes.c_uint32
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x100000 | (0x1000 if expected_image else 0), False, pid)
    if not handle:
        return ctypes.get_last_error() == 87
    try:
        if expected_image:
            query = kernel.QueryFullProcessImageNameW
            query.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
            query.restype = wintypes.BOOL
            size = ctypes.c_uint32(32768)
            image = ctypes.create_unicode_buffer(size.value)
            if not query(handle, 0, image, ctypes.byref(size)) or Path(image.value) != expected_image:
                return True
        return kernel.WaitForSingleObject(handle, 120000) == 0
    finally:
        kernel.CloseHandle(handle)


def start_update_helper(home, work, data):
    binary = Path(sys.executable)
    portable = getattr(sys, 'frozen', False)
    if portable:
        helper = work / 'updater.exe'
        shutil.copy2(binary, helper)
        arguments = [str(helper), '--apply-studio-update', str(os.getpid()), str(os.getppid())]
        restart = [str(binary)]
    else:
        helper = work / 'updater.py'
        shutil.copy2(home / 'update_package.py', helper)
        arguments = [str(binary), str(helper), str(os.getpid()), '0']
        restart = [str(binary), str(home / 'app.py')]
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1')
    subprocess.Popen([*arguments, str(home), str(work), *restart, '--data-dir', str(data)],
                     cwd=home, env=env, creationflags=subprocess.CREATE_NO_WINDOW)


def run_update_helper(pid, parent_pid, home, work, restart):
    portable = Path(restart[0]) == home / PORTABLE_EXE
    try:
        with update_lock(home):
            if not wait_for_exit(pid):
                raise ValueError('工坊尚未退出，请重试更新')
            if parent_pid and not wait_for_exit(parent_pid, home / PORTABLE_EXE):
                raise ValueError('工坊启动器尚未退出，请重试更新')
            if (home / JOURNAL).exists():
                recover_update(home)
            else:
                install_staged(work, home, portable)
            atomic_json(work / 'installed.json', {'format': FORMAT})
            stage = work / 'stage'
            if stage.is_dir() and checked_path(work, Path('stage')).resolve().parent == work.resolve():
                shutil.rmtree(stage, ignore_errors=True)
            for old in work.parent.glob('studio-update-*'):
                if old != work and (old / 'installed.json').is_file():
                    try:
                        remove_work(old, work.parent)
                    except (OSError, ValueError):
                        pass
    except Exception as exc:
        ctypes.windll.user32.MessageBoxW(None, str(exc), '球员工坊更新失败', 0x10)
        if (home / JOURNAL).exists():
            return
    subprocess.Popen(restart, cwd=home, env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1'),
                     creationflags=subprocess.CREATE_NO_WINDOW)


def helper_main(args):
    pid, parent, home, work, *restart = args
    run_update_helper(int(pid), int(parent), Path(home), Path(work), restart)


if __name__ == '__main__':
    helper_main(sys.argv[1:])
