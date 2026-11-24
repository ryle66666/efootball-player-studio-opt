"""Reuse installed resources while their private file identities remain stable."""
import json
import os
from pathlib import Path


STATE = 'outputs/efootball/resource-installation-state-v1.json'
FORMAT = 'studio-installed-resources-v1'


def identity(path):
    value = path.stat()
    return [value.st_dev, value.st_ino, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns]


class Installation:
    def __call__(self, path):
        return self.checked(path)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, 'original'), name)

    def __init__(self, root, legacy, revision, startup):
        self.root = Path(root).resolve()
        self.files = self.root.parent
        self.legacy = legacy
        self.startup = startup
        self.revision = revision
        self.observed = {}
        self.reused = {'source': 0, 'active': 0}
        self.reads = 0
        self.binding = None
        self.previous = {}
        self.manifest_rows = {}
        self.marker_time = None
        installed = self.root / 'installed-release.txt'
        manifest_path = self.root / 'release-manifest.json'
        try:
            manifest = json.loads(manifest_path.read_text('utf8'))
            release = installed.read_text('utf8').strip()
            if not release or release != manifest.get('release_id'):
                return
            marker = identity(installed)
            manifest_stat = identity(manifest_path)
            self.binding = {'release_id': release, 'document_revision': revision,
                            'installation': marker, 'manifest': manifest_stat}
            if max(manifest_stat[3:]) <= marker[3]:
                self.marker_time = marker[3]
            self.manifest_rows = {row['path']: row for row in manifest['files']}
            saved = json.loads((self.root / STATE).read_text('utf8'))
            if (isinstance(saved, dict) and saved.get('format') == FORMAT
                    and saved.get('binding') == self.binding
                    and isinstance(saved.get('files'), dict)):
                self.previous = saved['files']
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass

    def path(self, row, source=False):
        target = ((self.root / 'custom-resources/legends-v10' / row['name'])
                  if source else (self.files / row['path'])).resolve()
        if not target.is_relative_to(self.files):
            raise ValueError('Installed resource path is outside the private directory')
        return target

    def remember(self, path, value, stamp=None):
        stamp = identity(path) if stamp is None else stamp
        if identity(path) != stamp:
            return False
        self.legacy._hash_cache[self.legacy._fingerprint(path)] = value
        self.observed[path.relative_to(self.files).as_posix()] = stamp
        return True

    def prime_sources(self):
        for row in self.legacy._SOURCE_CHECKS:
            try:
                path = self.path(row, source=True)
                stamp = identity(path)
                relative = path.relative_to(self.files).as_posix()
                installed_row = self.manifest_rows.get(path.relative_to(self.root).as_posix(), {})
                recorded = self.previous.get(relative) == stamp
                copied = (self.marker_time is not None
                          and max(stamp[3:]) <= self.marker_time
                          and installed_row.get('bytes') == stamp[2])
                if stamp[2] != row['bytes'] or not (recorded or copied):
                    continue
                if not self.remember(path, row['sha256'], stamp):
                    continue
                self.legacy._source_verified[self.legacy._fingerprint(path)] = True
                self.reused['source'] += 1
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def checked(self, path):
        path = Path(path).resolve()
        expected = None
        for row in self.legacy._ACTIVE_CHECKS:
            if self.path(row) == path:
                expected = row
                break
        if expected is None:
            return self.original(path)
        stamp = identity(path)
        relative = path.relative_to(self.files).as_posix()
        if stamp[2] == expected['bytes'] and self.previous.get(relative) == stamp:
            value = expected['sha256']
            if self.remember(path, value, stamp):
                self.reused['active'] += 1
                return value
            stamp = identity(path)
        self.reads += 1
        value = self.original(path)
        self.remember(path, value, stamp)
        return value

    def finish(self):
        if self.binding is None:
            return
        current = {}
        for source, rows in ((True, self.legacy._SOURCE_CHECKS),
                             (False, self.legacy._ACTIVE_CHECKS)):
            for row in rows:
                try:
                    path = self.path(row, source)
                    stamp = identity(path)
                    key = self.legacy._fingerprint(path)
                    relative = path.relative_to(self.files).as_posix()
                    observed = self.observed.get(relative)
                    if observed is not None and observed != stamp:
                        continue
                    if stamp[2] != row['bytes'] or key not in self.legacy._hash_cache:
                        continue
                    current[relative] = stamp
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        destination = self.root / STATE
        temporary = destination.with_suffix('.writing')
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps({'format': FORMAT, 'binding': self.binding,
                                             'files': current}, separators=(',', ':')), 'utf8')
            os.replace(temporary, destination)
        except OSError:
            pass
        print('STUDIO_STARTUP resources-reused source=%d active=%d reads=%d' %
              (self.reused['source'], self.reused['active'], self.reads), flush=True)


def install(root, legacy, revision):
    import diy_startup
    state = Installation(root, legacy, revision, diy_startup)
    previous = getattr(diy_startup, '_studio_resource_reuse', None)
    state.original = previous.original if isinstance(previous, Installation) else diy_startup.digest
    state.prime_sources()
    diy_startup.digest = state
    diy_startup._studio_resource_reuse = state
    return state
