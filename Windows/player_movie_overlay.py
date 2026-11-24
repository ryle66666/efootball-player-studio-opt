"""Private player-card videos in a V17 live-data resource slot."""
import hashlib
import re
import struct

from native_iostore import fstring

SLOT = 'pc9257'
PAK = f'livedata/{SLOT}_mobile_and.pak'
CAPABILITY = 'player_movie_overlay'
MOUNT = '../../../PesMobile/Content/Movies/'
MOVIE_PATTERN = r'c[0-9a-f]{11}'
TEXTURE_PATTERN = rf'(?:{MOVIE_PATTERN}|smi_[0-9a-f]{{12}})'
MOTION_PATH = re.compile(
    rf'PesMobile/Content/(?:Movies/PlayerCard|Assets/ui/Data/Thumbnail/Player(?:/Variation2022)?)/'
    rf'({TEXTURE_PATTERN})_v\.(?:uasset|uexp|ubulk|uptnl)'
)


def route_assets(routes):
    return {name for route in routes for field in ('front_names', 'thumbnail_names')
            for name in route.get(field, ()) if isinstance(name, str)
            and re.fullmatch(TEXTURE_PATTERN, name)}


def private_movies(routes):
    return {name for name in route_assets(routes) if re.fullmatch(MOVIE_PATTERN, name)}


def motion_asset(path):
    match = MOTION_PATH.fullmatch(path)
    return match[1] if match else None


def required_art(names):
    root = 'PesMobile/Content/'
    textures = root + 'Assets/ui/Data/Thumbnail/Player/'
    for name in sorted(names):
        file = name + '_v.uasset'
        yield textures + file
        yield textures + 'Variation2022/' + file
        if re.fullmatch(MOVIE_PATTERN, name):
            yield root + 'Movies/PlayerCard/' + file


def movie_path(name):
    return f'PlayerCard/{name}_v.usm'


def verify_experimental_slot(source, template, work, key):
    from content_pack import PREFIX
    from native_live import version_info
    from native_movie_pak_source import Pak

    entry = next((row for row in version_info(template).get('dt880', []) if row['name'] == SLOT), None)
    if entry is None or not 0 < entry['hashFileSize'] <= 4096:
        raise ValueError('此 V17 基准没有空的动态卡面实验槽位')
    with source.open(PREFIX + PAK) as stream:
        raw = stream.read(4097)
    if len(raw) != entry['hashFileSize'] or hashlib.sha256(raw).hexdigest() != entry['hash']:
        raise ValueError('动态卡面实验槽位与基准清单不一致')
    path = work / 'movie-slot-check.pak'
    path.write_bytes(raw)
    if Pak(path, key).files:
        raise ValueError('实时资源槽位已有内容，不能启用动态卡面实验')


def write_movie_pak(path, movies):
    """Write the same legacy PAK layout used by full APK card movies."""
    entries = []
    with path.open('xb') as output:
        for name, value in sorted(movies.items()):
            raw = value() if callable(value) else value
            offset = output.tell()
            entry = (struct.pack('<QQQI', offset, len(raw), len(raw), 0)
                     + hashlib.sha1(raw).digest() + struct.pack('<BI', 0, 0))
            output.write(bytes(8) + entry[8:])
            output.write(raw)
            entries.append((name, entry))
        index = fstring(MOUNT) + struct.pack('<I', len(entries))
        index += b''.join(fstring(name) + entry for name, entry in entries)
        offset = output.tell()
        output.write(index)
        output.write(struct.pack('<IIQQ', 0x5a6f12e1, 3, offset, len(index))
                     + hashlib.sha1(index).digest())


def stage_movies(archive, movies, previous, current_routes, output, key):
    """Publish a complete set of still-referenced private videos for all cards."""
    from native_movie_pak_source import Pak

    old = Pak(previous, key)
    if old.files and (old.version != 3 or any(
            not re.fullmatch(re.escape(MOUNT) + rf'PlayerCard/{MOVIE_PATTERN}_v\.usm', path)
            for path in old.files)):
        raise ValueError('基准的动态卡面实时资源槽位已被占用')
    active = {MOUNT + movie_path(name) for name in private_movies(current_routes)}
    prior = {path: path.removeprefix(MOUNT) for path in old.files if path in active}
    merged = {name: (lambda path=path: old.read(path)) for path, name in prior.items()}
    different = len(prior) != len(old.files)
    for name, expected in movies.items():
        path = movie_path(name)
        if MOUNT + path not in active:
            raise ValueError('动态卡面视频未绑定到当前球员')
        raw = archive.read('movie/' + name)
        if not raw.startswith(b'CRID') or hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('动态卡面视频校验失败')
        if path in merged and merged[path]() != raw:
            raise ValueError('动态卡面视频编号冲突')
        if path not in merged:
            different = True
            merged[path] = lambda name=name: archive.read('movie/' + name)
    if different:
        output.parent.mkdir(parents=True, exist_ok=True)
        write_movie_pak(output, merged)
    return different


def template_metadata(template, replacement):
    """Preserve the checksums of other advertised live-data containers."""
    from native_live import version_info
    fields = (('.pak', 'hash', 'hashFileSize'), ('.ucas', 'ucas', 'ucasFileSize'),
              ('.utoc', 'utoc', 'utocFileSize'))
    metadata = {row['name'] + '_mobile_and' + suffix: {
        'sha256': row[checksum], 'bytes': row[size],
    } for row in version_info(template).get('dt880', []) for suffix, checksum, size in fields}
    name = PAK.rsplit('/', 1)[1]
    if name not in metadata:
        raise ValueError('此基准未登记动态卡面实时资源槽位')
    metadata[name] = replacement
    return metadata
