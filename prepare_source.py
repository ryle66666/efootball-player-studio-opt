"""Restore the bundled default catalogue used by the source build."""

import gzip
import hashlib
from pathlib import Path
import shutil
import tempfile


def prepare(root):
    resources = Path(root) / 'Windows' / 'resources'
    database = resources / 'library.sqlite3'
    digest = (resources / 'library.sqlite3.sha256').read_text('ascii').strip()
    if database.is_file():
        with database.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                raise ValueError('本地默认数据库与源码校验值不同')
        return database
    with tempfile.NamedTemporaryFile(dir=resources, prefix='catalogue-', delete=False) as output:
        pending = Path(output.name)
    try:
        with gzip.open(database.with_suffix('.sqlite3.gz'), 'rb') as source, pending.open('wb') as output:
            shutil.copyfileobj(source, output)
        with pending.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                raise ValueError('默认数据库校验失败')
        pending.replace(database)
    finally:
        pending.unlink(missing_ok=True)
    return database


if __name__ == '__main__':
    print(prepare(Path(__file__).resolve().parent))
