"""Measure import peak memory and retained thumbnail frames with fixed inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import tracemalloc
from types import SimpleNamespace
from threading import Event, Lock, Thread
import zipfile

import psutil
from PIL import Image
from collections import OrderedDict


def measure(action):
    process = psutil.Process()
    rss = process.memory_info().rss
    samples = [rss]
    stop = Event()

    def sample():
        while not stop.wait(.003):
            samples[0] = max(samples[0], process.memory_info().rss)

    thread = Thread(target=sample)
    thread.start()
    tracemalloc.start(4)
    try:
        result = action()
        _, peak = tracemalloc.get_traced_memory()
        after = process.memory_info().rss
    finally:
        tracemalloc.stop()
        stop.set()
        thread.join()
    return result, {'python_peak_bytes': peak, 'peak_rss_growth': max(samples[0], after) - rss,
                    'retained_rss_growth': after - rss}


def test(windows, root):
    sys.path.insert(0, str(windows))
    sys.dont_write_bytecode = True
    from app import Store
    from native_library import NativeLibrary
    from project_sharing import export_selected
    from thumbnail_background import background

    store = Store(root / 'data')
    source = root / 'large.bin.obj'
    size = 96 * 1024 * 1024
    with source.open('wb') as file:
        file.seek(size - 1)
        file.write(b'\n')
    with source.open('rb') as file:
        digest = hashlib.file_digest(file, 'sha256').hexdigest()
    result, asset_metrics = measure(lambda: store.import_asset(source, 'model'))
    assert result['id'] == digest + '.obj' and result['size'] == size
    path = store.root / 'assets' / result['id']
    assert path.stat().st_size == size
    assert store.import_asset(source, 'model') == result
    path.write_bytes(b'incomplete')
    assert store.import_asset(source, 'model') == result and path.stat().st_size == size
    assert not list(path.parent.glob('.incoming-*'))
    project = store.project.copy()
    project['model'] = {**project['model'], 'asset': result['id']}
    project['identity'] = {**project['identity'], 'name': '回归测试球员'}
    exports = {
        'project_v1': measure(lambda: store.export_project(project)),
        'project_v2': measure(lambda: export_selected(store, [project['package_id']], project)),
    }
    manifests = []
    for version, (exported, _) in enumerate(exports.values(), 1):
        document = 'project.json' if version == 1 else 'projects.json'
        with zipfile.ZipFile(exported['path']) as archive:
            asset = 'assets/' + result['id']
            assert archive.namelist() == [asset, 'manifest.json', document]
            manifest = json.loads(archive.read('manifest.json'))
            assert manifest['version'] == version
            assert manifest['sha256'][document] == hashlib.sha256(archive.read(document)).hexdigest()
            with archive.open(asset) as payload:
                assert manifest['sha256'][asset] == digest == hashlib.file_digest(payload, 'sha256').hexdigest()
            manifest.pop('created_at', None)
            manifest.pop('package_id', None)
            manifest['sha256'].pop(document)
            manifests.append(manifest)
        receiver = Store(root / f'receiver-{version}')
        imported = receiver.import_project(exported['path'])
        assert imported['model']['asset'] == result['id']
        assert (receiver.root / 'assets' / result['id']).stat().st_size == size

    assets = store.root / 'assets'
    layers = []
    for i in range(4):
        name = f'{i:064x}.png'
        with Image.new('RGBA', (3072, 3072), (i*35, 50, 70, 170)) as image:
            image.save(assets / name)
        layers.append({'asset_id': name, 'kind': 'image', 'slot': ['f01', 'f02', 'f03', 'f04'][i]})
    library = SimpleNamespace(store=store, thumbnail_lock=Lock(), thumbnail_frames=OrderedDict())
    project = {'appearance': {}, 'native_card': {'preset_id': 'fixture', 'layers': layers}}

    def render():
        with background(project, library) as thumbnail:
            return hashlib.sha256(thumbnail.tobytes()).hexdigest()

    expected, thumbnail_metrics = measure(render)
    assert render() == expected
    thumbnail_metrics['decoded_cache_bytes'] = sum(im.width*im.height*4
                                                    for im in library.thumbnail_frames.values())
    native = NativeLibrary(windows / 'resources', windows, store)
    filters, filters_metrics = measure(native.search_filters)
    index = native._identity_index()
    filters_metrics['id_collection_bytes'] = sum(sys.getsizeof(ids)
                                                for groups in index.values() for ids in groups.values())
    if not filters['leagues']:
        # Minimal league metadata for bundled databases that only ship team identities.
        native._leagues = lambda: [{'id': 'fixture', 'name': '测试联赛', 'name_en': 'League',
                                    'team_ids': sorted(index['teams'])[:4]}]
        filters = native.search_filters()
    nation = filters['nations'][0]['id']
    league = filters['leagues'][0]
    queries = [{'nation': nation}, {'team': league['teams'][0]['id']},
               {'league': league['id']}, {'nation': nation, 'league': league['id']},
               {'nation': -1}, {'league': 'missing'}]
    filtered = [native.search(query) for query in queries]
    catalogue_digest = hashlib.sha256(
        json.dumps([filters, filtered], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {'asset_import_96mib': asset_metrics, 'thumbnail_four_3072px': thumbnail_metrics,
            'project_archive_exports_96mib': {name: metrics for name, (_, metrics) in exports.items()},
            'project_manifests': manifests,
            'thumbnail_sha256': expected, 'library_identity_index': filters_metrics,
            'catalogue_sha256': catalogue_digest}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('windows', type=Path)
    parser.add_argument('label')
    args = parser.parse_args()
    local = Path(__file__).resolve().parent / 'local'
    local.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=local) as directory:
        report = test(args.windows.resolve(), Path(directory))
    baseline = local / 'memory-original.json'
    if args.label != 'original' and baseline.is_file():
        previous = json.loads(baseline.read_text('utf8'))
        for name in ('thumbnail_sha256', 'catalogue_sha256', 'project_manifests'):
            assert report[name] == previous[name], name
    (local / f'memory-{args.label}.json').write_text(json.dumps(report, indent=2), 'utf8')
    print(json.dumps(report, indent=2))
