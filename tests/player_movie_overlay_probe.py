"""Verify a multi-card movie PAK against V17's existing live-data catalogue."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Windows'))

from native_live import compile_template, verify_template, version_info
from native_movie import remux_usm
from native_movie_pak_source import Pak
from player_append import ACTIVE, PREFIX, TEMPLATE, capable, engine_key
from player_movie_overlay import MOUNT as MOVIE_ROOT, PAK, SLOT, movie_path
from player_movie_overlay import stage_movies, template_metadata, verify_experimental_slot
from program_bootstrap import MARKER


STOCK_CONTAINER = 'pc9258_mobile_and.pak'
EMPTY_CONTAINER = PAK.rsplit('/', 1)[1]


def probe(apk, cards, work):
    with zipfile.ZipFile(apk) as source:
        marker = json.loads(source.read(MARKER))
        if not capable(marker) or marker.get('game_version') not in (None, 'V17'):
            raise ValueError('请选择已登记的 V17 基准 APK')
        key = engine_key(source, marker)
        prefix = PREFIX + 'livedata/'
        template = json.loads(source.read(PREFIX + TEMPLATE))
        advertised = {row['name'] + '_mobile_and.pak' for row in version_info(template)['dt880']}
        if {STOCK_CONTAINER, EMPTY_CONTAINER} - advertised:
            raise ValueError('当前基准未登记实验使用的实时资源槽位')
        verify_experimental_slot(source, template, work, key)
        slot = work / 'movie-slot-check.pak'
        # Confirm that the same catalogue already carries native card movies.
        stock = work / STOCK_CONTAINER
        stock.write_bytes(source.read(prefix + STOCK_CONTAINER))
        if not any(name.startswith(MOVIE_ROOT + 'PlayerCard/') for name in Pak(stock, key).files):
            raise ValueError('当前 V17 基准缺少可对照的实时卡面视频')

        movies = {}
        for index, paths in enumerate(cards):
            for role, file in zip(('large', 'mini'), paths, strict=True):
                raw = file.read_bytes()
                _, info = remux_usm(raw)
                expected = (182, 256) if role == 'large' else (128, 128)
                if (info['width'], info['height']) != expected:
                    raise ValueError(f'{file.name} 不是该位置所需的 {expected} 视频')
                digest = hashlib.sha256(f'{index}:{role}:'.encode() + raw).hexdigest()
                movies[f'c{digest[:11]}'] = raw
        if len(movies) != 2 * len(cards):
            raise ValueError('测试中的视频资源编号发生冲突')

        overlay = work / 'multi-card-movies.pak'
        wrapped = work / 'movies.zip'
        checksums = {name: hashlib.sha256(raw).hexdigest() for name, raw in movies.items()}
        movie_names = list(movies)
        routes = [{'front_names': [large], 'thumbnail_names': [mini]}
                  for large, mini in zip(movie_names[::2], movie_names[1::2], strict=True)]
        with zipfile.ZipFile(wrapped, 'x') as storage:
            for name, raw in movies.items():
                storage.writestr('movie/' + name, raw)
        with zipfile.ZipFile(wrapped) as archive:
            assert stage_movies(archive, checksums, slot, routes, overlay, key)
            assert not stage_movies(archive, checksums, overlay, routes, work / 'replayed.pak', key)
        decoded = Pak(overlay, key)
        assert set(decoded.files) == {MOVIE_ROOT + movie_path(name) for name in movies}
        for name, raw in movies.items():
            assert decoded.read(MOVIE_ROOT + movie_path(name)) == raw

        cpk = source.read(PREFIX + ACTIVE)
        stamp = {'sha256': hashlib.sha256(overlay.read_bytes()).hexdigest(), 'bytes': overlay.stat().st_size}
        metadata = template_metadata(template, stamp)
        updated = compile_template(template, cpk, version_info(template)['version'] + 1, metadata)
        verify_template(updated, cpk)
        listed = {row['name']: row['size'] for row in updated['livedata_list']}
        entry = next(row for row in version_info(updated)['dt880'] if row['name'] == SLOT)
        assert listed[EMPTY_CONTAINER] == entry['hashFileSize'] == overlay.stat().st_size
        assert entry['hash'] == hashlib.sha256(overlay.read_bytes()).hexdigest()
        print(f'实时资源模板、{len(cards)} 张卡的 {len(movies)} 路视频、PAK 读回校验已通过。Android 播放待验证。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('apk', type=Path)
    parser.add_argument('--card', type=Path, nargs=2, action='append', required=True, metavar=('LARGE', 'MINI'))
    args = parser.parse_args()
    if len(args.card) < 2:
        parser.error('请提供至少两张卡的大卡和小图视频')
    with tempfile.TemporaryDirectory() as directory:
        probe(args.apk, args.card, Path(directory))
