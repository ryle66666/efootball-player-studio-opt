"""Expose completed export files through flat names supported by Android."""
import copy
import os
import json
from pathlib import Path
import shutil
from urllib.parse import quote
import uuid


def stamp(path):
    value = path.stat()
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def expose(store, result):
    value = copy.deepcopy(result)
    folder = (store.root / 'exports').resolve()
    folder.mkdir(parents=True, exist_ok=True)
    index_path = store.root / 'export-delivery.json'
    try:index = json.loads(index_path.read_text('utf8'))
    except (OSError,ValueError):index = {}
    if not isinstance(index,dict):index = {}
    rows = value.get('artifacts') or [value]
    for row in rows:
        source = Path(row['path'])
        if source.is_symlink() or source.is_junction() or not source.resolve().is_relative_to(folder):
            raise ValueError('导出文件位置无效，已保留现有文件。')
        before = stamp(source)
        if source.parent.resolve() == folder:
            destination = source
        else:
            prior = index.get(str(source), {})
            prior_name = prior.get('name', '')
            destination = folder / source.name
            if (prior.get('source') == list(before) and Path(prior_name).name == prior_name and
                    prior_name and (folder / prior_name).is_file() and
                    prior.get('destination') == list(stamp(folder / prior_name))):
                destination = folder / prior_name
                row.update(name=destination.name, url='/exports/' + quote(destination.name))
                continue
            if destination.exists():
                if not os.path.samefile(source, destination):
                    destination = folder / (source.stem + '-' + uuid.uuid4().hex[:8] + source.suffix)
            if not destination.exists():
                try:
                    os.link(source, destination)
                except OSError:
                    created = False
                    try:
                        with destination.open('xb') as output:
                            created = True
                            with source.open('rb') as incoming:
                                shutil.copyfileobj(incoming, output, 1024 * 1024)
                            output.flush()
                            os.fsync(output.fileno())
                    except BaseException:
                        if created:
                            destination.unlink(missing_ok=True)
                        raise
                if stamp(source) != before or destination.stat().st_size != before[2]:
                    destination.unlink(missing_ok=True)
                    raise ValueError('导出文件发生变化，已保留原文件，请重新生成保存入口。')
        row.update(name=destination.name, url='/exports/' + quote(destination.name))
        index[str(source)] = {'name':destination.name, 'source':list(before), 'destination':list(stamp(destination))}
    from content_pack import atomic_json
    try:atomic_json(index_path,index)
    except OSError:pass
    if value.get('artifacts'):
        main = next((row for row in rows if row['path'] == value['path']), None)
        if main:
            value.update(name=main['name'], url=main['url'])
    return value
