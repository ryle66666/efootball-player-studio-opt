"""Run the studio's APK export against an installed V17 game source."""
import argparse
import gc
import hashlib
from itertools import count
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
import uuid
import zipfile


class ExportMemory:
    def __init__(self):
        import psutil

        self.psutil = psutil
        self.process = psutil.Process()
        self.started = time.monotonic()
        self.idle = self.process.memory_info().rss
        self.stages = {}
        self.peak = {}
        self.children = {}

    def sample(self, state):
        main = self.process.memory_info()
        others = 0
        for child in self.process.children(recursive=True):
            try:
                name = child.name()
                rss = child.memory_info().rss
            except (self.psutil.NoSuchProcess, self.psutil.AccessDenied):
                continue
            others += rss
            self.children[name] = max(self.children.get(name, 0), rss)
        stage = state.get('stage') or state.get('phase')
        point = {'stage': stage, 'message': state.get('message', ''),
                 'seconds': time.monotonic() - self.started, 'studio_rss': main.rss,
                 'studio_private': getattr(main, 'private', None), 'children_rss': others,
                 'combined_rss': main.rss + others}
        if point['combined_rss'] > self.stages.get(stage, {}).get('combined_rss', 0):
            self.stages[stage] = point
        if point['combined_rss'] > self.peak.get('combined_rss', 0):
            self.peak = point

    def report(self):
        return {'idle_studio_rss': self.idle, 'peak': self.peak, 'stages': self.stages,
                'child_peaks': self.children}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('windows', type=Path)
    parser.add_argument('installation', type=Path)
    parser.add_argument('label')
    parser.add_argument('--game', action='store_true')
    parser.add_argument('--project', type=Path, help='a saved studio project with its assets in the installation')
    parser.add_argument('--profile-memory', action='store_true')
    parser.add_argument('--isolated-work', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    local = Path(__file__).resolve().parent / 'local'
    local.mkdir(exist_ok=True)
    if args.isolated_work is None:
        # Texture DLLs remain loaded on Windows until the export process exits.
        with tempfile.TemporaryDirectory(dir=local) as work:
            env = dict(os.environ, PYTHONHASHSEED='0')
            subprocess.run([sys.executable, __file__, *sys.argv[1:], '--isolated-work', work], env=env, check=True)
        return
    data = args.isolated_work.resolve(strict=True)
    if data.parent != local or any(data.iterdir()):
        raise ValueError('Export requires an empty test directory')
    run_export(args, data, local)


def run_export(args, data, local):
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(args.windows.resolve()))
    from app import Store, StudioServer, referenced_assets, normalize_project
    from apk_format import local_signer
    import native_package

    local_signer(local / 'test-signing')
    original = (args.installation / 'data').resolve()
    state_file = original / 'active-game.json'
    source = json.loads(state_file.read_text('utf8'))
    database = original / 'game-databases' / source['database']
    if database.parent != original / 'game-databases':
        raise ValueError('Unexpected database path')
    shutil.copy2(state_file, data)
    shutil.copytree(database, data / 'game-databases' / database.name)
    shutil.copytree(local / 'test-signing', data / 'signing')
    store = Store(data)
    home = data / 'studio-home'
    if args.game:
        resources = database / 'native-resource-profile.json'
        namespace = json.loads(resources.read_text('utf8'))['cache_namespace']
        if not namespace or Path(namespace).name != namespace:
            raise ValueError('Unexpected library cache name')
        cached = args.installation / 'library' / 'versions' / namespace
        shutil.copytree(cached, home / 'library' / 'versions' / namespace)
        shutil.copytree(args.installation / 'tools', home / 'tools')

    class FixtureServer(StudioServer):
        def use_resources(self, resources):
            self.home = home
            super().use_resources(resources)

    sequence = count(16)
    stable_uuid = SimpleNamespace(uuid4=lambda: uuid.UUID(int=next(sequence) << 112))
    with FixtureServer(('127.0.0.1', 0), store) as server, patch.object(native_package, 'uuid', stable_uuid):
        job = server.native_package
        before = set(threading.enumerate())
        if args.game:
            project = store.project
            if args.project:
                value = json.loads(args.project.read_text('utf8'))
                project = normalize_project(value.get('project', value))
                for name in referenced_assets(project):
                    shutil.copy2(original / 'assets' / name, data / 'assets' / name)
            project['package_id'] = str(uuid.UUID('25a0186f-b8d8-4bc4-b42e-ef629ff8755e'))
            project['identity']['name'] = '性能回归测试球员'
            project['editor_ready'] = True
            store.save(project)
            request = {'apk': source['apk'], 'project': project, 'selected_ids': [project['package_id']],
                       'pack_ids': [], 'coach_ids': [], 'write_mode': 'incremental',
                       'installation_mode': 'update'}
        else:
            request = {'operation': 'companion', 'companion': source['companion'],
                       'installation_mode': 'update'}
        memory = ExportMemory() if args.profile_memory else None
        start = time.monotonic()
        job.start(request)
        state = job.status()
        previous = None
        while state['phase'] == 'working' and time.monotonic() - start < 600:
            if state.get('message') != previous:
                previous = state.get('message')
                print(previous, flush=True)
            if memory:
                memory.sample(state)
            time.sleep(.05 if memory else .5)
            state = job.status()
        if state['phase'] != 'done':
            raise RuntimeError(state)
        for thread in set(threading.enumerate()) - before:
            thread.join(timeout=20)
        result = state['result']
        if args.project:
            assert any(row.get('package_id') == project['package_id']
                       for row in result['report']['identities']), [
                           item.get('message') for item in state.get('diagnostics', [])
                       ]
        with Path(result['path']).open('rb') as file:
            digest = hashlib.file_digest(file, 'sha256').hexdigest()
        report = {'sha256': digest, 'bytes': result['size'], 'seconds': time.monotonic()-start}
        if memory:
            report['memory'] = memory.report()
        kind = 'game' if args.game else 'companion'
        with zipfile.ZipFile(result['path']) as apk:
            report['entries'] = [
                [i.filename, i.CRC, i.file_size, i.compress_size, i.date_time]
                for i in apk.infolist()
            ]
        (local / f'{args.label}-{kind}.json').write_text(json.dumps(report, indent=2), 'utf8')
        print(json.dumps({key: value for key, value in report.items() if key not in ('entries', 'memory')}), flush=True)
        if memory:
            print(json.dumps(memory.peak, ensure_ascii=False), flush=True)
    gc.collect()


if __name__ == '__main__':
    main()
