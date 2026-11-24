"""Prepare shared static video covers outside requests for the card list."""

import hashlib
import json
import logging
from pathlib import Path
import re
import subprocess
import threading

from content_pack import atomic_json
from library_bound_cards import front_name
from native_movie import extract_ivf
from native_movie_sources import sources
from thumbnail_background import prepare_library_cover

BACKGROUND = re.compile(r'_f0[12](?:_lim)?_v$')
ASSET_ID = re.compile(r'[a-f0-9]{64}\.(?:webm|mp4)')
REVISION = 'library-video-covers-v1'


class LibraryVideoCovers:
    def __init__(self, library):
        self.library = library
        self.root = library.converted / 'library-video-covers'
        self.path = self.root / 'index.json'
        self.lock = threading.Lock()
        self.worker_lock = threading.Lock()
        self.cached = None

    def index(self):
        try:
            stamp = self.path.stat().st_mtime_ns
        except FileNotFoundError:
            return {}
        if self.cached is None or self.cached[0] != stamp:
            try:
                index = json.loads(self.path.read_text('utf8'))
                native, direct = index['native'], index['direct']
                files = {row['file'] for row in native.values()} | set(direct.values())
                unavailable = {name for name in files if name and not (self.root / name).is_file()}
                if unavailable:
                    index.pop('signature', None)
                    index['native'] = {key: row for key, row in native.items() if row['file'] not in unavailable}
                    index['direct'] = {key: name for key, name in direct.items() if name not in unavailable}
                self.cached = (stamp, index)
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                return {}
        return self.cached[1]

    def signature(self, direct, bound):
        lib = self.library
        files = [*sorted(lib.containers.glob('*.pak')),
                 lib.resources / 'incremental-content.json', lib.ffmpeg]
        stamps = [(f.name, s.st_size, s.st_mtime_ns) for f in files if f.is_file() for s in (f.stat(),)]
        payload = [REVISION, lib.profile['release_id'], lib.card_media.get('movies', []), sorted(direct), sorted(bound), stamps]
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def prepare(self):
        try:
            return self._prepare()
        except (OSError, ValueError):
            logging.exception('Could not prepare library video covers')
            return self.index()

    def _prepare(self):
        lib = self.library
        with self.lock:
            if not lib.ffmpeg.is_file():
                return self.index()
            direct = {row['asset_id'] for row in getattr(lib, 'content_media', {}).values()
                      if row.get('kind') == 'video' and ASSET_ID.fullmatch(row.get('asset_id', ''))}
            routes = [*getattr(lib, 'bound_cards', {}).values(), *getattr(lib, 'content_routes', {}).values()]
            bound = {name for route in routes for slot in ('f01', 'f02') if (name := front_name(route, slot))}
            signature = self.signature(direct, bound)
            existing = self.index()
            if existing.get('signature') == signature:
                return existing
            originals, previews, missing = {}, {}, []
            reader = sources(lib)
            for path in reader.paths():
                name = Path(path).stem
                if not (BACKGROUND.search(name) or name in bound or name.removesuffix('_v') in bound):
                    continue
                originals[name] = {'path': path, 'file': None}
                try:
                    raw = reader.read(path)
                    filename = hashlib.sha256(raw).hexdigest() + '.png'
                    target = self.root / filename
                    if not target.is_file():
                        stream = extract_ivf(raw)
                        demuxer = 'ivf' if stream.startswith(b'DKIF') else 'mpegvideo' if stream.startswith(b'\0\0\x01\xb3') else None
                        if not demuxer or not prepare_library_cover(lib.ffmpeg, stream, target, demuxer=demuxer):
                            raise ValueError('未找到可用的背景帧')
                    originals[name]['file'] = filename
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    missing.append(name)
                    logging.debug('Library cover unavailable for %s: %s', name, error)
            for asset in sorted(direct):
                source = lib.store.root / 'assets' / asset
                filename = Path(asset).stem + '.png'
                try:
                    if source.is_file() and prepare_library_cover(lib.ffmpeg, source, self.root / filename):
                        previews[asset] = filename
                        continue
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    logging.debug('Library cover unavailable for %s: %s', asset, error)
                missing.append(asset)
            result = {'signature': signature, 'native': originals, 'direct': previews, 'missing': missing}
            atomic_json(self.path, result)
            return result

    def schedule(self):
        if not self.worker_lock.acquire(blocking=False):
            return

        def run():
            try:
                self.prepare()
            finally:
                self.worker_lock.release()

        try:
            threading.Thread(target=run, daemon=True).start()
        except RuntimeError:
            self.worker_lock.release()
