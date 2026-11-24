"""Build portable copy/literal recipes against the signed V17 program APK."""
import hashlib
import json
import mmap
from pathlib import Path
import struct
import tempfile
import zipfile

from content_pack import BLOCK, PREFIX, Reader, opened, write
from export_payload import measure
from native_iostore import MAGIC
from player_updates import MEMBER, PROTOCOL, open_update, transfer


def ranges(length, width):
    return ((offset, min(width, length - offset)) for offset in range(0, length, width))


def iostore_ranges(toc, length):
    if len(toc) < 144 or toc[:16] != MAGIC or toc[16] != 2:
        raise ValueError('球员资源容器目录格式不支持')
    header, count, blocks, width = struct.unpack_from('<4I', toc, 20)
    start = header + count * 22
    if header != 144 or width != 12 or toc[80] & 4 or start + blocks * width > len(toc):
        raise ValueError('球员资源容器目录的区间无效')
    result = set()
    for offset in range(start, start + blocks * width, width):
        at = int.from_bytes(toc[offset:offset + 5], 'little')
        size = int.from_bytes(toc[offset + 5:offset + 8], 'little')
        if toc[80] & 2:
            size += -size % 16
        if at + size > length:
            raise ValueError('球员资源块超出文件范围')
        if size:
            result.add((at, size))
    return sorted(result)


def add_run(runs, copy, offset, length):
    if not length:
        return
    if runs and runs[-1][0] == copy and runs[-1][1] + runs[-1][2] == offset:
        runs[-1][2] += length
    else:
        runs.append([copy, offset, length])


def matched_runs(base, final, base_ranges, final_ranges):
    blocks = {}
    for offset, length in base_ranges:
        blocks.setdefault(hashlib.sha256(base[offset:offset + length]).digest(), offset)
    runs = []
    cursor = 0
    for offset, length in final_ranges:
        if cursor > offset:
            raise ValueError('球员资源目录的数据区间重叠')
        add_run(runs, False, cursor, offset - cursor)
        block = final[offset:offset + length]
        prior = blocks.get(hashlib.sha256(block).digest())
        if prior is not None and base[prior:prior + length] == block:
            add_run(runs, True, prior, length)
        else:
            add_run(runs, False, offset, length)
        cursor = offset + length
    add_run(runs, False, cursor, len(final) - cursor)
    return runs


def shifted_runs(base, final):
    runs = []
    cursor = shift = 0
    window = 512 * 1024
    for offset, length in ranges(len(base), 32 * 1024):
        expected = max(cursor, offset + shift)
        low = max(cursor, expected - window)
        high = min(len(final), expected + window + length)
        if high - low < length:
            continue
        match = final.find(base[offset:offset + length], low, high)
        if match < 0:
            continue
        add_run(runs, False, cursor, match - cursor)
        add_run(runs, True, offset, length)
        cursor = match + length
        shift = match - offset
    add_run(runs, False, cursor, len(final) - cursor)
    return runs


def make_runs(apk, name, files, base, final, base_size, new_size):
    suffix = Path(name).suffix.lower()
    if not base_size or not new_size or suffix not in ('.ucas', '.cpk', '.utoc', '.json'):
        return [[False, 0, new_size]] if new_size else []
    with mmap.mmap(base.fileno(), 0, access=mmap.ACCESS_READ) as old, mmap.mmap(final.fileno(), 0, access=mmap.ACCESS_READ) as new:
        if suffix == '.cpk':
            return matched_runs(old, new, ranges(base_size, 2048), ranges(new_size, 2048))
        if suffix == '.ucas':
            toc = str(Path(name).with_suffix('.utoc').as_posix())
            try:
                old_toc = apk.read(PREFIX + toc)
            except KeyError:
                return [[False, 0, new_size]]
            with opened(files.get(toc, old_toc)) as source:
                new_toc = source.read()
            return matched_runs(old, new, iostore_ranges(old_toc, base_size), iostore_ranges(new_toc, new_size))
        return shifted_runs(old, new)


def _write_file(base_apk, name, files, archive, index, work, seen):
    with tempfile.TemporaryFile(dir=work) as target, opened(files[name]) as source:
        final = measure(source, target)
        row = {'name': name, 'bytes': final['bytes'], 'sha256': final['sha256']}
        alias = seen.get(final['sha256'])
        if alias is not None:
            return {**row, 'alias': alias}
        seen[final['sha256']] = name
        base_info = None
        with tempfile.TemporaryFile(dir=work) as base:
            if PREFIX + name in base_apk.NameToInfo and Path(name).suffix.lower() in ('.ucas', '.cpk', '.utoc', '.json'):
                with base_apk.open(PREFIX + name) as stream:
                    base_info = measure(stream, base)
            target.flush()
            base.flush()
            runs = make_runs(base_apk, name, files, base, target, base_info['bytes'] if base_info else 0, final['bytes'])
            with archive.open(f'payload/{index}', 'w', force_zip64=final['bytes'] >= 2**31) as output:
                for copy, offset, length in runs:
                    if not copy:
                        target.seek(offset)
                        transfer(target, output, length)
        if any(run[0] for run in runs):
            row['base'] = {field: base_info[field] for field in ('bytes', 'sha256')}
        return {**row, 'runs': [[offset, length] if copy else [length] for copy, offset, length in runs]}


def write_update(destination, *, base_path, package, program_id, files, removed, parent):
    destination = Path(destination)
    work = destination.parent
    with tempfile.TemporaryDirectory(dir=work) as directory:
        encoded = Path(directory) / 'updates.zip'
        with zipfile.ZipFile(base_path) as base, zipfile.ZipFile(encoded, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            seen = {}
            rows = [_write_file(base, name, files, archive, index, work, seen) for index, name in enumerate(sorted(files))]
            archive.writestr('manifest.json', json.dumps({'protocol': PROTOCOL, 'parent': parent, 'files': rows}, separators=(',', ':')))
        manifest = write(destination, package=package, program_id=program_id, files={MEMBER: encoded}, removed=removed)
        try:
            with open_update(Reader(destination), work) as update, zipfile.ZipFile(base_path) as base:
                paths = {}
                for index, name in enumerate(update.files):
                    paths[name] = Path(directory) / f'checked-{index}'
                    update.materialize(name, base, paths[name], paths.__getitem__)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        return {**manifest, 'materialized_files': len(rows)}
