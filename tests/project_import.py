"""Measure .efproject imports and compare restored projects and assets."""
import argparse
import copy
import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import shutil
import sys
import tempfile
from threading import Thread
import time
from unittest.mock import patch
import zipfile

from memory import measure


def fixture_variant(source, destination, *, collection=False):
    """Recreate the USM-only variant using the original model and movie bytes."""
    with zipfile.ZipFile(source) as original:
        manifest = json.loads(original.read('manifest.json'))
        project = json.loads(original.read('project.json'))
        custom = project['native_card']['custom_motion']
        previews = {'assets/' + row.pop('preview')['asset_id'] for row in (custom['large'], custom['mini'])}
        assert len(previews) == 2
        digests = manifest['sha256']
        for name in previews:
            digests.pop(name)
        digests.pop('project.json')
        if collection:
            second = copy.deepcopy(project)
            second['package_id'] = '19b234c3-544e-4572-afa2-96f268219c0f'
            second['identity']['name'] += ' B'
            document = 'projects.json'
            manifest.update(version=2, players=2)
            manifest.pop('package_id', None)
            data = json.dumps([project, second], ensure_ascii=False).encode()
        else:
            document = 'project.json'
            data = json.dumps(project, ensure_ascii=False).encode()
        with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as output:
            for name in sorted(digests):
                with original.open(name) as content, output.open(name, 'w') as target:
                    shutil.copyfileobj(content, target, 256 * 1024)
            digests[document] = hashlib.sha256(data).hexdigest()
            output.writestr(document, data)
            output.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False).encode())


def check_import(source, data, *, legacy=False):
    from app import Store, referenced_assets

    store = Store(data)
    with zipfile.ZipFile(source) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        unpacked = sum(info.file_size for info in archive.infolist())
    start = time.perf_counter()
    result, metrics = measure(lambda: store.import_project(source))
    elapsed = time.perf_counter() - start
    saved = [store.saved_player(path.stem)['project'] for path in sorted((data / 'players').glob('*.json'))]
    assert result == store.project
    referenced = set.union(referenced_assets(result), *(referenced_assets(project) for project in saved))
    actual = {path.name for path in (data / 'assets').iterdir()}
    assert actual == referenced
    for name in actual:
        with (data / 'assets' / name).open('rb') as file:
            assert hashlib.file_digest(file, 'sha256').hexdigest() == name.split('.')[0]
    if legacy:
        for project in saved:
            for role in ('large', 'mini'):
                name = project['native_card']['custom_motion'][role]['preview']['asset_id']
                with (data / 'assets' / name).open('rb') as video:
                    assert video.read(4) == b'\x1aE\xdf\xa3'
    snapshot = json.dumps([result, saved, sorted(actual)], ensure_ascii=False, sort_keys=True).encode()
    return dict(metrics, seconds=elapsed, unpacked_bytes=unpacked, players=len(saved),
                assets=len(actual), version=manifest['version'],
                restored_sha256=hashlib.sha256(snapshot).hexdigest())


def test_rejection_and_retry(root):
    from app import Store, StudioServer

    first_asset = b'v 1 0 0\n'
    second_asset = b'v 0 1 0\n'
    first_id, second_id = (hashlib.sha256(raw).hexdigest() + '.obj'
                           for raw in (first_asset, second_asset))
    for version in (1, 2):
        data = root / f'reject-{version}'
        store = Store(data)
        original = copy.deepcopy(store.project)
        original['identity']['name'] = '原工程'
        store.save_player(original)
        store.save(original)
        incoming = copy.deepcopy(original)
        incoming['identity']['name'] = '导入工程'
        incoming['model']['asset'] = first_id
        second = copy.deepcopy(incoming)
        second['package_id'] = '741cc6a3-ecf7-4ba1-b12f-50de9af3d479'
        second['model']['asset'] = second_id

        def write_archive(name, *, corrupt=False, missing=False):
            target = root / f'{name}-{version}.efproject'
            rows = [incoming, second] if version == 2 else incoming
            document = 'projects.json' if version == 2 else 'project.json'
            raw = json.dumps(rows, ensure_ascii=False).encode()
            members = {document: raw}
            if not missing:
                members['assets/' + first_id] = first_asset
            members['assets/' + second_id] = second_asset
            manifest = {'format': '73efootball-studio-project', 'version': version,
                        'sha256': {key: hashlib.sha256(value).hexdigest()
                                   for key, value in members.items()}}
            if corrupt:
                members['assets/' + second_id] = b'corrupt: wrong content with the correct filename'
            with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
                for filename, value in members.items():
                    archive.writestr(filename, value)
                archive.writestr('manifest.json', json.dumps(manifest))
            return target

        broken = write_archive('wrong-digest', corrupt=True)
        missing = write_archive('missing-reference', missing=True)
        valid = write_archive('valid')
        before = store.project_path.read_bytes(), store._player_path(original['package_id']).read_bytes()

        def assert_untouched():
            assert before == (store.project_path.read_bytes(), store._player_path(original['package_id']).read_bytes())
            assert len(list((data / 'players').glob('*.json'))) == 1
            assert not list((data / 'assets').iterdir())

        for path in (broken, missing):
            try:
                store.import_project(path)
            except ValueError:
                assert_untouched()
            else:
                raise AssertionError('accepted an invalid package')
        if version == 2:
            with patch('app.MAX_PERSONAL_PLAYERS', 2):
                try:
                    store.import_project(valid)
                except ValueError:
                    assert_untouched()
                else:
                    raise AssertionError('accepted a package above the player limit')

        store_asset = data / 'assets' / first_id
        store_asset.write_bytes(b'truncated')
        with StudioServer(('127.0.0.1', 0), store, token='project-import-test') as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = HTTPConnection('127.0.0.1', server.server_port, timeout=30)
                try:
                    connection.request('POST', '/api/project/import', json.dumps({'path': str(valid)}),
                                       {'X-Studio-Token': server.token, 'Content-Type': 'application/json'})
                    response = connection.getresponse()
                    imported = json.loads(response.read())['project']
                finally:
                    connection.close()
                assert response.status == 200
            finally:
                server.shutdown()
                thread.join(timeout=10)
        assert imported['package_id'] != original['package_id']
        assert store.saved_player(original['package_id'])['project']['identity']['name'] == '原工程'
        assert store.saved_player(imported['package_id'])['project']['identity']['name'] == '导入工程'
        assert store_asset.read_bytes() == first_asset
        assert not list((data / 'assets').glob('.project-import-*'))
        assert (data / 'assets' / second_id).is_file() == (version == 2)
    print('Validated rejection, clean retry, HTTP import and asset repair for both versions.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('windows', type=Path)
    parser.add_argument('label')
    parser.add_argument('individual', type=Path)
    parser.add_argument('collections', nargs='+', type=Path)
    parser.add_argument('--check-errors', action='store_true')
    args = parser.parse_args()
    sys.path.insert(0, str(args.windows.resolve()))
    sys.dont_write_bytecode = True
    local = Path(__file__).resolve().parent / 'local'
    local.mkdir(exist_ok=True)
    report = {}
    with tempfile.TemporaryDirectory(dir=local) as directory:
        root = Path(directory)
        fixtures = [
            ('individual', args.individual, False),
            *((f'collection-{i}', path, False) for i, path in enumerate(args.collections, 1)),
        ]
        for collection in (False, True):
            name = 'legacy-collection' if collection else 'legacy-individual'
            path = root / (name + '.efproject')
            fixture_variant(args.individual, path, collection=collection)
            fixtures.append((name, path, True))
        for name, source, legacy in fixtures:
            report[name] = check_import(source, root / name, legacy=legacy)
            print(name, json.dumps(report[name]), flush=True)
        if args.check_errors:
            test_rejection_and_retry(root)
    baseline = local / 'project-import-before.json'
    if args.label != 'before' and baseline.exists():
        original = json.loads(baseline.read_text('utf8'))
        for name, row in report.items():
            assert row['restored_sha256'] == original[name]['restored_sha256'], name
    (local / f'project-import-{args.label}.json').write_text(json.dumps(report, indent=2), 'utf8')


if __name__ == '__main__':
    main()
