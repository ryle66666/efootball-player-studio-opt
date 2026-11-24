"""Read a data-only player update carried by the existing .73pack importer."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid
import zipfile

from content_pack import BLOCK, MAX_FILES, MAX_MANIFEST, PREFIX, relative

MEMBER = 'custom-resources/personal-player-studio/player-update-v1.zip'
PROTOCOL = 'studio-player-update-v1'
CAPABILITY = 'player_updates'
LIMIT = 0xffffffff


def capable(program):
    return isinstance(program, dict) and program.get(CAPABILITY) == 1 and not program.get('content_required')


def is_update(pack):
    return MEMBER in pack.entries


def transfer(source, output, length):
    while length:
        block = source.read(min(length, BLOCK))
        if not block:
            raise ValueError('球员小包的数据不完整')
        output.write(block)
        length -= len(block)


def sha256(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


@contextmanager
def open_update(pack, work):
    if set(pack.entries) != {MEMBER}:
        raise ValueError('球员小包不能混合其它内容文件')
    with tempfile.TemporaryFile(dir=work) as payload, pack.open(MEMBER) as source:
        shutil.copyfileobj(source, payload, BLOCK)
        payload.seek(0)
        with zipfile.ZipFile(payload) as archive:
            yield Update(archive, pack.manifest['removed'])


class Update:
    def __init__(self, archive, removed):
        self.archive = archive
        if archive.getinfo('manifest.json').file_size > MAX_MANIFEST:
            raise ValueError('球员小包清单过大')
        manifest = json.loads(archive.read('manifest.json'))
        if not isinstance(manifest, dict) or manifest.get('protocol') != PROTOCOL:
            raise ValueError('球员小包版本不支持')
        self.parent = manifest.get('parent')
        if self.parent is not None:
            if not isinstance(self.parent, str) or str(uuid.UUID(self.parent)) != self.parent:
                raise ValueError('球员小包的上次更新标识无效')
        rows = manifest.get('files')
        if not isinstance(rows, list) or len(rows) + len(removed) > MAX_FILES:
            raise ValueError('球员小包文件数量无效')
        self.files = {}
        members = {'manifest.json'}
        targets = {name.casefold() for name in removed} | {MEMBER.casefold()}
        total = 0
        for index, row in enumerate(rows):
            name = relative(row['name'])
            size = row['bytes']
            folded = name.casefold()
            if folded in targets or type(size) is not int or not 0 <= size <= LIMIT or not sha256(row['sha256']):
                raise ValueError('球员小包目标文件无效')
            targets.add(folded)
            total += size
            if total > LIMIT:
                raise ValueError('球员小包展开后的文件过大')
            if 'alias' in row:
                prior = self.files.get(row['alias'])
                if not prior or (size, row['sha256']) != (prior['bytes'], prior['sha256']):
                    raise ValueError('球员小包的重复文件记录无效')
            else:
                member = f'payload/{index}'
                members.add(member)
                self._validate_runs(row, archive.getinfo(member).file_size)
                row = {**row, 'payload': member}
            self.files[name] = row
        if len(archive.infolist()) != len(members) or set(archive.namelist()) != members:
            raise ValueError('球员小包含有多余或重复的文件')

    @staticmethod
    def _validate_runs(row, literal_size):
        base = row.get('base')
        if base is not None and (not isinstance(base, dict) or type(base.get('bytes')) is not int
                                 or not 0 <= base['bytes'] <= LIMIT or not sha256(base.get('sha256'))):
            raise ValueError('球员小包的基准校验无效')
        runs = row['runs']
        if not isinstance(runs, list):
            raise ValueError('球员小包的写入清单无效')
        total = literals = 0
        for run in runs:
            if not isinstance(run, list) or len(run) not in (1, 2) or any(type(v) is not int or v < 0 for v in run) or run[-1] == 0:
                raise ValueError('球员小包的数据区间无效')
            total += run[-1]
            if len(run) == 1:
                literals += run[0]
            elif base is None or sum(run) > base['bytes']:
                raise ValueError('球员小包引用超出基准文件')
        if total != row['bytes'] or literals != literal_size:
            raise ValueError('球员小包的目标长度不匹配')

    def materialize(self, name, base_apk, destination, previous):
        row = self.files[name]
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if 'alias' in row:
            with previous(row['alias']).open('rb') as source, destination.open('xb') as output:
                transfer(source, output, row['bytes'])
                output.flush()
                os.fsync(output.fileno())
        else:
            with self.archive.open(row['payload']) as literal, destination.open('xb') as output:
                with self._open_base(base_apk, name, row.get('base'), destination.parent) as original:
                    for run in row['runs']:
                        if len(run) == 1:
                            transfer(literal, output, run[0])
                        else:
                            original.seek(run[0])
                            transfer(original, output, run[1])
                output.flush()
                os.fsync(output.fileno())
        with destination.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != row['sha256']:
                raise ValueError('球员小包重建后的文件校验不一致：' + name)

    @staticmethod
    @contextmanager
    def _open_base(apk, name, expected, work):
        if expected is None:
            yield None
            return
        info = apk.getinfo(PREFIX + name)
        if info.file_size != expected['bytes']:
            raise ValueError('基准 APK 的数据长度不同：' + name)
        with apk.open(info) as stream, tempfile.TemporaryFile(dir=work) as local:
            digest = hashlib.sha256()
            while block := stream.read(BLOCK):
                digest.update(block)
                local.write(block)
            if digest.hexdigest() != expected['sha256']:
                raise ValueError('基准 APK 的数据校验不一致：' + name)
            yield local


def android_apk():
    from java import jclass

    context = jclass('com.chaquo.python.Python').getPlatform().getApplication()
    return Path(str(context.getApplicationInfo().sourceDir))
