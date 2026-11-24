"""Build a Windows update asset from the current application version."""

import argparse
import json
from pathlib import Path
import uuid
import zipfile

from update_package import ASSET_SUFFIX, FORMAT, PORTABLE_REQUIRED, PORTABLE_VERSION_FILE, program_path, version_from_source
from updates import version


def build(home, output, portable=False):
    release = (json.loads((home / PORTABLE_VERSION_FILE).read_text('utf8'))['version'] if portable
               else version_from_source((home / 'app.py').read_text('utf8')))
    if version(release) is None:
        raise ValueError('APP_VERSION 必须是可比较的版本号，例如 3.0.17')
    files = ([home / path for path in PORTABLE_REQUIRED if not path.startswith('tools/')] if portable
             else [*home.glob('*.py'), home / 'requirements.txt'])
    folders = ('tools',) if portable else ('resources', 'tools', 'web/dist/current')
    for folder in folders:
        files.extend(path for path in (home / folder).rglob('*')
                     if path.is_file() and not any(part.startswith('.') or part == '__pycache__'
                                                   for part in path.relative_to(home).parts)
                     and path.name != 'library.sqlite3.gz')
    output.mkdir(parents=True, exist_ok=True)
    target = output / ('efootball-player-studio-v' + release + ASSET_SUFFIX)
    temporary = output / ('.build-update-' + uuid.uuid4().hex)
    try:
        with zipfile.ZipFile(temporary, 'x', zipfile.ZIP_DEFLATED, compresslevel=3) as package:
            manifest = {'format': FORMAT, 'version': release}
            if portable:
                manifest['layout'] = 'portable'
                package.write(home / 'README.md', 'Portable/README.md')
            package.writestr('studio-update.json', json.dumps(manifest))
            for file in sorted(files):
                relative = program_path(file.relative_to(home).as_posix(), portable)
                if file.is_symlink() or any(parent.is_junction() or parent.is_symlink()
                                            for parent in file.parents if parent.is_relative_to(home)):
                    raise ValueError('构建文件包含链接：' + str(file))
                package.write(file, ('Portable/' if portable else 'Windows/') + relative.as_posix())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path.cwd() / 'release')
    parser.add_argument('--portable', type=Path, help='Bundle a standalone Windows build')
    args = parser.parse_args()
    print(build(args.portable.resolve() if args.portable else Path(__file__).resolve().parent,
                args.output.resolve(), portable=bool(args.portable)))
