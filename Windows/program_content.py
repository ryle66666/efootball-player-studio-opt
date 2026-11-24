"""Separate installable game code from streamed game-data packages."""
import copy
from contextlib import ExitStack
from dataclasses import replace
import io
import json
import tempfile
import weakref
import zipfile
from pathlib import Path

from content_pack import PREFIX, Reader as PackReader, SourceMember, opened, relative, size

MARKER = 'assets/player-studio/program.json'
BASE = 'assets/player-studio/content-base.json'
CONTENT_FILE = '73efootball-content.73pack'


def data_name(name):
    if not isinstance(name, str) or not name.startswith(PREFIX):
        return None
    try:
        return relative(name[len(PREFIX):])
    except ValueError:
        return None


def read_json(archive, name):
    return json.loads(archive.read(name))


def program(archive):
    if MARKER not in archive.namelist():
        return None
    value = read_json(archive, MARKER)
    if not isinstance(value, dict) or value.get('protocol') != '73-studio-program-v1':
        raise ValueError('游戏程序标记格式无效')
    return value


def sources(store, game_path, provided=None):
    from content_pack import installed_program
    with zipfile.ZipFile(game_path) as archive:
        marker=program(archive)
    if not marker:
        return None
    chosen=(provided or{}).get('content_pack')
    if not marker.get('content_required') and not chosen:
        return None
    if not chosen:
        prior=installed_program(store,marker['package'])
        if prior and prior['program_id']==marker['program_id']:
            chosen=(prior.get('resource_packages')or{}).get('content_pack')
    if not chosen:
        filename=marker.get('content_file',CONTENT_FILE)
        if not isinstance(filename,str)or Path(filename).name!=filename or '/'in filename or '\\'in filename:
            raise ValueError('程序内容包文件名无效')
        chosen=str(Path(game_path).parent/filename)
    return {'content_pack':str(chosen)}


def inventory(archive):
    if BASE not in archive.namelist():
        return {'files': [], 'required': []}
    value = read_json(archive, BASE)
    if not isinstance(value, dict) or value.get('protocol') != '73-program-data-v1':
        raise ValueError('程序数据目录格式无效')
    rows, required = value.get('files'), value.get('required')
    if not isinstance(rows, list) or not isinstance(required, list) or len(rows) > 60000:
        raise ValueError('程序数据目录数量无效')
    names = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'path', 'bytes'}:
            raise ValueError('程序数据目录记录无效')
        name = relative(row['path'])
        if name in names or type(row['bytes']) is not int or not 0 <= row['bytes'] < 2**63:
            raise ValueError('程序数据目录记录重复或大小无效')
        names.add(name)
    if len(required) != len(set(required)) or any(relative(name) not in names for name in required):
        raise ValueError('程序必需内容目录无效')
    return value


def complete_changes(source, changes):
    """Bring inherited logical resources into this export before sizing it."""
    result = dict(changes)
    physical = getattr(source, 'program_archive', None)
    if physical is None:
        physical = getattr(source, 'archives', {}).get('game', source)
    names = set(physical.namelist())
    content_names=set(getattr(getattr(source, '_content', None), 'virtual', {}))
    for name in source.namelist():
        if (name not in names or name in content_names) and name not in result:
            result[name] = source.file_payload(name)
    result.pop('assets/player-studio/resource-set.json', None)
    manifest_name=PREFIX+'release-manifest.json'
    with opened(result[manifest_name]) if manifest_name in result else source.open(manifest_name) as stream:
        release=json.load(stream)
    release.pop('resource_set',None)
    listed={row['path']:row for row in release['files']}
    release['files']=list(listed.values())
    from export_payload import metadata
    for name,value in result.items():
        if name in names or not name.startswith(PREFIX)or name==manifest_name:continue
        relative_name=name[len(PREFIX):]
        row={'path':relative_name,**metadata(value)}
        if relative_name in listed:listed[relative_name].update(row)
        else:release['files'].append(row)
    result[manifest_name]=encode(release)
    return result


def describe(source, changes, required=()):
    files = []
    for name in sorted(set(source.namelist()) | set(changes)):
        path = data_name(name)
        if path is None:
            continue
        count = size(changes[name]) if name in changes else source.getinfo(name).file_size
        files.append({'path': path, 'bytes': count})
    return {'protocol': '73-program-data-v1', 'files': files, 'required': sorted(required)}


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf8')


def split(source, changes):
    """Keep startup metadata in the APK and put all game data in a 73pack."""
    changes = dict(changes)
    names = set(source.namelist()) | set(changes)
    data = {name: data_name(name) for name in names if data_name(name) is not None}
    # These small files are read while the original gateway module is loaded.
    retained = {name for name, path in data.items() if
                path == 'release-manifest.json' or path.startswith(('work/templates/', 'outputs/reference/')) or
                Path(path).suffix.lower() in ('.json', '.csv', '.txt', '.tsv')}
    moved = set(data) - retained
    files = {path: changes[name] if name in changes else SourceMember(source, name)
             for name, path in sorted(data.items())}
    result = {name: value for name, value in changes.items() if name not in moved}
    manifest_name = PREFIX + 'release-manifest.json'
    with opened(changes[manifest_name]) if manifest_name in changes else source.open(manifest_name) as stream:
        release = json.load(stream)
    physical_release = copy.deepcopy(release)
    physical_release['files'] = [row for row in release['files'] if PREFIX + row['path'] not in moved]
    physical_release.pop('resource_set', None)
    result[manifest_name] = encode(physical_release)
    result[BASE] = encode(describe(source, changes, (data[name] for name in moved)))
    marker = json.loads(result[MARKER])
    marker.update(content_required=True, content_file=CONTENT_FILE)
    result[MARKER] = encode(marker)
    removed = moved | {'assets/player-studio/resource-set.json'}
    return {'changes': result, 'removed': removed, 'files': files, 'filename': CONTENT_FILE,
            'logical_changes': changes}


class ContentStream(io.RawIOBase):
    def __init__(self, context):
        super().__init__()
        self.context = context
        self.stream = context.__enter__()

    def readable(self):
        return True

    def read(self, count=-1):
        return self.stream.read(count)

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)

    def close(self):
        if not self.closed:
            self.context.__exit__(None, None, None)
        super().close()


class DataArchive:
    def __init__(self, archive, game_path, packages, *, metadata_only=False):
        self.archive = archive
        self.program = program(archive)
        self.base = inventory(archive)
        self.reader = None
        self._update = None
        self._update_files = None
        self._stack = ExitStack()
        self._restored = {}
        self.virtual = {PREFIX + row['path']: row['bytes'] for row in self.base['files']}
        self.removed = set()
        selected=(packages or {}).get('content_pack')
        if not self.program or not (self.program.get('content_required') or selected):
            self.virtual = {}
            return
        if metadata_only:
            return
        filename = self.program.get('content_file', CONTENT_FILE)
        if not isinstance(filename, str) or Path(filename).name != filename or '/' in filename or '\\' in filename:
            raise ValueError('程序内容包文件名无效')
        path = selected or str(Path(game_path).parent / filename)
        if not Path(path).is_file():
            raise ValueError('此游戏程序需要对应的 .73pack 内容包，请补选内容包文件')
        self.reader = PackReader(path)
        manifest = self.reader.manifest
        if (manifest['package'], manifest['program_id']) != (self.program['package'], self.program['program_id']):
            raise ValueError('内容包与所选游戏程序不匹配')
        from player_append import MEMBER as INDEPENDENT_PLAYER_MEMBER
        if INDEPENDENT_PLAYER_MEMBER in self.reader.entries:
            raise ValueError('独立球员小包请直接在游戏中导入，导出下一名时只需选择基准 APK')
        covered = set(self.reader.entries) | set(manifest['removed'])
        if not set(self.base['required']) <= covered:
            raise ValueError('内容包缺少此程序必需的数据文件')
        from player_updates import capable, is_update, open_update
        if is_update(self.reader):
            if not capable(self.program):
                raise ValueError('请先选用支持球员小包的基准 APK')
            self._update_files = UpdateWorkspace()
            with ExitStack() as opening:
                self._update = opening.enter_context(open_update(self.reader, self._update_files.name))
                self.virtual = {PREFIX + name: row['bytes'] for name, row in self._update.files.items()}
                # The restored data may outlive this reader through FilePayload.
                # Keep only the archive context in the reader's stack.
                self._stack = opening.pop_all()
        else:
            self.virtual = {PREFIX + name: count for name, (_, count) in self.reader.entries.items()}
        self.removed = {PREFIX + name for name in manifest['removed']}

    def close(self):
        self._stack.close()
        self._update_files = None

    def _restore(self, name):
        if name not in self._restored:
            path = Path(self._update_files.name) / name
            try:
                self._update.materialize(name, self.archive, path, self._restore)
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            self._restored[name] = path
        return self._restored[name]

    def getinfo(self, name):
        if name not in self.virtual:
            raise KeyError(name)
        # Only size metadata is needed by the logical resource reader.
        info=zipfile.ZipInfo(name)
        info.file_size=info.compress_size=self.virtual[name]
        info.compress_type=zipfile.ZIP_STORED;info.CRC=0
        return info

    def open(self, name, mode='r'):
        if mode != 'r':
            raise ValueError('内容包只读')
        if self.reader is None:
            raise ValueError('读取游戏数据时必须提供对应内容包')
        if self._update is not None:
            return self._restore(name[len(PREFIX):]).open('rb')
        return ContentStream(self.reader.open(name[len(PREFIX):]))

    def file_payload(self, name):
        from export_payload import FilePayload, identity, measure
        if self._update is not None:
            path = self._restore(name[len(PREFIX):])
            return replace(FilePayload.from_path(path), lease=UpdateFile(path, self._update_files))
        with self.open(name) as stream:
            measured = measure(stream)
        offset, count = self.reader.entries[name[len(PREFIX):]]
        return FilePayload(self.reader.path, count, measured['sha256'], measured['crc32'],
                           identity(self.reader.path.stat()), offset=offset)


class UpdateFile:
    def __init__(self, path, directory):
        self.path = path
        self.directory = directory

    def open(self):
        return self.path.open('rb')


class UpdateWorkspace:
    def __init__(self):
        directory = tempfile.TemporaryDirectory(prefix='studio-player-update-', delete=False, ignore_cleanup_errors=True)
        self.name = directory.name
        weakref.finalize(self, directory.cleanup)
