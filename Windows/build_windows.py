"""Build the self-contained 64-bit Windows portable edition."""

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

from compile_cache import MODULES, write_source_manifest
from runtime_modules import BUNDLED_GAME_SOURCES
from update_package import PORTABLE_EXE, PORTABLE_REQUIRED, PORTABLE_VERSION_FILE, version_from_source


def build(root, tools, destination, work):
    if sys.platform != 'win32' or sys.version_info[:2] != (3, 12) or sys.maxsize <= 2**32:
        raise ValueError('请在 64 位 Windows 使用 Python 3.12 构建')
    if destination.exists():
        raise ValueError('交付目录已存在，请指定一个新目录')
    for path in PORTABLE_REQUIRED:
        if path.startswith('tools/') and not (tools / path.removeprefix('tools/')).is_file():
            raise FileNotFoundError('缺少发布工具：' + path)
    if not (root / 'resources' / 'library.sqlite3').is_file():
        raise ValueError('请先在仓库根目录运行 prepare_source.py')
    if not (root / 'web' / 'dist' / 'current' / 'index.html').is_file():
        raise ValueError('缺少已构建的网页界面')

    import PyInstaller.__main__

    release = version_from_source((root / 'app.py').read_text('utf8'))
    work.mkdir(parents=True, exist_ok=True)
    write_source_manifest(root)
    with tempfile.TemporaryDirectory(prefix='portable-', dir=work) as temporary:
        stage = Path(temporary)
        shutil.copytree(root / 'resources', stage / 'resources', ignore=shutil.ignore_patterns('library.sqlite3.gz'))
        bundle = stage / 'bundle'
        PyInstaller.__main__.run([
            str(root / 'app.py'), '--onefile', '--windowed', '--noupx', '--noconfirm',
            '--name', Path(PORTABLE_EXE).stem, '--icon', str(root / 'resources' / 'app.ico'),
            '--distpath', str(bundle), '--workpath', str(work / 'pyinstaller'),
            '--specpath', str(work / 'spec'), '--paths', str(root),
            '--collect-submodules', 'wannacri', '--exclude-module', 'tkinter',
            '--add-data', str(stage / 'resources') + ':resources',
            '--add-data', str(root / 'web' / 'dist' / 'current') + ':web/dist/current',
            *(arg for name in BUNDLED_GAME_SOURCES for arg in ('--add-data', str(root / name) + ':.')),
            *(arg for name in MODULES for arg in ('--hidden-import', name)),
        ])
        shutil.copytree(tools, bundle / 'tools', ignore=shutil.ignore_patterns(
            '.net', '__pycache__', 'system-commandline-sentinel-files'))
        (bundle / PORTABLE_VERSION_FILE).write_text(json.dumps({'version': release}), 'utf8')
        shutil.copy2(root.parent / 'PORTABLE.md', bundle / 'README.md')
        destination.parent.mkdir(parents=True, exist_ok=True)
        if bundle.resolve().parent != stage.resolve() or stage.resolve().parent != work.resolve():
            raise ValueError('构建输出路径无效')
        if destination.exists():
            raise ValueError('交付目录已存在，请指定一个新目录')
        shutil.move(bundle, destination)
    return destination


if __name__ == '__main__':
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools', type=Path, default=root / 'tools')
    parser.add_argument('--output', type=Path, default=root.parent / 'release' / 'Portable')
    parser.add_argument('--work-dir', type=Path, default=root.parent / '.work' / 'build-windows')
    args = parser.parse_args()
    print(build(root, args.tools.resolve(), args.output.resolve(), args.work_dir.resolve()))
