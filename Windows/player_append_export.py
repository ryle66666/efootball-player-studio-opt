"""Extract only selected player records and owned assets from a normal build."""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile

from content_pack import Reader, atomic_json, write
from native_iostore import read_package_header
from personal_source import ownership, owned_art_paths
from player_append import (MEMBER, PROTOCOL, UPDATE_PROTOCOL, MOVIE_PROTOCOL, art_owner, compact,
                           ids_for, merge_document, read_descriptor, supports_movies, supports_replacement)
from player_append_tables import ALIASES, BOOSTERS, alias_keys, apply, changes
import player_skill_bindings
import player_movie_overlay as movie_overlay

REVISIONS_FILE = 'player-revisions.json'


def player_content_hash(entry, tables, paths, chunk_hashes, movies=None):
    definition = entry['definition']
    ids = ids_for(definition, entry['art_key'])
    boosters = set(definition['base_parameters'][144:147])
    slot_aliases = alias_keys(ids, slots=True)
    player_tables = {}
    for name in sorted(tables):
        rows = tables[name]
        if name == ALIASES:
            picked = sorted(r for r in rows if r[0] in slot_aliases)
        elif name == BOOSTERS:
            picked = sorted(r for r in rows if r[0] in boosters)
        elif name.endswith('/string/all_diff.str'):
            picked = rows
        else:
            picked = sorted(r for r in rows if r[0] in ids)
        if picked:
            player_tables[name] = picked
    motion_names = movie_overlay.route_assets(entry['routes']) if movies is not None else set()
    owned_art = sorted(
        [name, chunk_hashes[pos]]
        for name, pos in paths.items()
        if (art_owner(name) in ids or movie_overlay.motion_asset(name) in motion_names) and pos in chunk_hashes
    )
    payload = {
        'definition': definition,
        'routes': entry['routes'],
        'art_key': entry['art_key'],
        'design': entry.get('design'),
        'series': entry.get('series'),
        'tables': player_tables,
        'art': owned_art,
    }
    if movies:
        refs = movie_overlay.private_movies(entry['routes'])
        if refs:
            payload['movies'] = {name: movies[name] for name in sorted(refs)}
    return hashlib.sha256(compact(payload)).hexdigest()


def allocate_revisions(store, hashes_by_uid):
    path = store.root / REVISIONS_FILE
    lock = getattr(store, 'lock', None)

    def _read():
        if path.is_file():
            try:
                raw = json.loads(path.read_text('utf8'))
                players = raw.get('players')
                if isinstance(players, dict):
                    return dict(players)
            except (OSError, ValueError):
                pass
        return {}

    if lock is not None:
        with lock:
            existing = _read()
    else:
        existing = _read()

    assigned = {}
    updated = dict(existing)
    for uid, ch in hashes_by_uid.items():
        old = existing.get(uid)
        old_rev = old.get('revision') if isinstance(old, dict) and type(old.get('revision')) is int else 0
        old_hash = old.get('content_hash') if isinstance(old, dict) else None
        rev = old_rev if (old_rev >= 1 and old_hash == ch) else (old_rev + 1)
        assigned[uid] = rev
        updated[uid] = {'revision': rev, 'content_hash': ch}

    def commit():
        if lock is not None:
            with lock:
                fresh = _read()
                fresh.update({uid: updated[uid] for uid in assigned})
                atomic_json(path, {'version': 1, 'players': fresh})
        else:
            atomic_json(path, {'version': 1, 'players': updated})

    return assigned, commit


def require_runtime_hooks(before, after):
    """The installed game keeps its executable interfaces for added players."""
    fields = ('address', 'body_hex', 'expected_hex', 'entry_offset', 'extra_entries',
              'absolute_fixups', 'writable_fixups', 'code_links', 'code_exports')
    def executable(hook):
        result = {key: hook[key] for key in fields if key in hook}
        if hook['name'] == player_skill_bindings.HOOK:
            code, _, _ = player_skill_bindings.unpack(result['body_hex'])
            code.extend(bytes(-len(code) % 16))
            result['body_hex'] = code.hex()
        return result
    existing = {hook['name']: executable(hook) for hook in before}
    changed = [hook['name'] for hook in after if existing.get(hook['name']) != executable(hook)]
    if changed:
        raise ValueError('所选球员需要不同的游戏运行接口，请使用完整 APK 导出：' + '、'.join(changed))


def build_patch(before, after, container, document, selected, *, inherited=None,
                marker=None, revisions=None, target_game=None, studio_version=None, movie_files=None):
    selected = set(selected)
    owners = ownership(document)
    if not selected or selected - owners.keys():
        raise ValueError('没有完整编译的球员可导出')
    player_ids = set()
    boosters = set()
    players = []
    routes = {route['cardId']: route for route in document['native_binding']['routes']}
    for player in document['players']:
        uid = player['identity']['package_id']
        if uid not in selected:
            continue
        key = owners[uid]['asset_key']
        ids = ids_for(player, key)
        player_ids.update(ids)
        boosters.update(owners[uid]['boosters'])
        players.append({'definition': player, 'routes': [routes[card] for card in sorted(ids)], 'art_key': key,
                        'design': document.get('write_designs', {}).get('players', {}).get(uid),
                        'series': document.get('card_series_by_id', {}).get(str(player['identity']['card_id']))})
    booster_keys = {key: value for key, value in document.get('editor_registries', {}).get('boosters', {}).items()
                    if value in boosters}
    boosters = set(booster_keys.values())
    snapshot = inherited is not None
    tables = changes(before, after, player_ids, boosters, snapshot=snapshot)
    indices = owned_art_paths(container, [owners[uid] for uid in selected])
    movies = None
    if movie_files is not None:
        if not snapshot or not supports_movies(marker):
            raise ValueError('请先使用支持动态卡面小包的基准 APK')
        player_routes = [route for row in players for route in row['routes']]
        names = movie_overlay.route_assets(player_routes)
        indices.update(index for path, index in container.paths.items()
                       if movie_overlay.motion_asset(path.removeprefix('../../../')) in names)
        movies = {}
        for name in sorted(movie_overlay.private_movies(player_routes)):
            raw = movie_files.get(movie_overlay.movie_path(name))
            if not raw or not raw.startswith(b'CRID'):
                raise ValueError('本次所选球员缺少动态卡面视频：' + name)
            movies[name] = hashlib.sha256(raw).hexdigest()
    indices = sorted(indices)
    if not indices:
        raise ValueError('本次所选球员缺少卡面或脸模资源')
    positions = {index: position for position, index in enumerate(indices)}
    paths = {path.removeprefix('../../../'): positions[index]
             for path, index in container.paths.items() if index in positions}
    header = next(i for i, chunk in enumerate(container.chunk_ids) if chunk[11] == 10)
    from struct import unpack_from
    chunks = [container.chunk_ids[index] for index in indices]
    package_ids = {unpack_from('<Q', chunk)[0] for chunk in chunks if chunk[11] == 2}
    _, available = read_package_header(container.read_chunk(header))
    packages = [row for row in available if row['id'] in package_ids]
    hook = next((hook for hook in document['native_binding']['profile'].get('native_hooks', [])
                 if hook['name'] == player_skill_bindings.HOOK), None)
    skill_rows = player_skill_bindings.unpack(hook['body_hex'])[2] if hook else {}
    chunk_hashes = {pos: hashlib.sha256(container.read_chunk(idx)).hexdigest() for pos, idx in enumerate(indices)}
    for entry in players:
        uid = entry['definition']['identity']['package_id']
        entry['content_hash'] = player_content_hash(entry, tables, paths, chunk_hashes, movies)
        if revisions and uid in revisions:
            entry['revision'] = int(revisions[uid])
    manifest = {'protocol': UPDATE_PROTOCOL if snapshot else PROTOCOL, 'players': players, 'tables': tables,
                'boosters': booster_keys, 'paths': paths, 'packages': packages,
                'chunks': [chunk.hex() for chunk in chunks],
                'skill_bindings': [[key, row] for key, row in skill_rows.items() if key in player_ids],
                'preview_boosters': {key: value for key, value in document.get('preview_boosters', {}).items()
                                     if int(key) in boosters},
                'text_routes': [row for row in document['native_binding']['profile'].get('boosters', [])
                                if row['id'] in boosters],
                'created_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
    if movies is not None:
        manifest.update(protocol=MOVIE_PROTOCOL, movies=movies)
    resolved_game = target_game or document.get('game_version')
    if resolved_game:
        manifest['target_game'] = str(resolved_game)
    if studio_version:
        manifest['studio_version'] = str(studio_version)
    if isinstance(marker, dict):
        base_info = {'package': marker.get('package'), 'program_id': marker.get('program_id')}
        eng = marker.get('player_append_engine')
        if isinstance(eng, dict) and eng.get('sha256'):
            base_info['engine_sha256'] = eng['sha256']
        manifest['base'] = base_info
    replace_ids = frozenset()
    if snapshot:
        keys = ('native_binding', 'write_designs', 'card_series_by_id', 'editor_registries',
                'preview_boosters', 'write_selection')
        prior = {key: copy.deepcopy(inherited[key]) for key in keys if key in inherited}
        prior['players'] = inherited['players']
        replace_ids = merge_document(prior, manifest)[3]
    apply(before, tables, player_ids, boosters, replace_ids=replace_ids, snapshot=snapshot)
    return manifest, indices


def write_append(destination, marker, before, after, container, document, selected, *,
                 inherited=None, store=None, target_game=None, studio_version=None, movie_files=None):
    if supports_replacement(marker) and inherited is None:
        raise ValueError('需要基准 APK 中的球员资料才能生成更新小包')
    manifest, indices = build_patch(
        before, after, container, document, selected,
        inherited=inherited if supports_replacement(marker) else None,
        marker=marker, target_game=target_game, studio_version=studio_version, movie_files=movie_files,
    )
    commit_revisions = None
    if store is not None:
        hashes = {p['definition']['identity']['package_id']: p['content_hash'] for p in manifest['players']}
        revisions, commit_revisions = allocate_revisions(store, hashes)
        for entry in manifest['players']:
            entry['revision'] = revisions[entry['definition']['identity']['package_id']]
    with tempfile.TemporaryDirectory(dir=Path(destination).parent) as directory:
        inner = Path(directory) / 'player-append.zip'
        with zipfile.ZipFile(inner, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr('manifest.json', compact(manifest))
            for position, index in enumerate(indices):
                archive.writestr(f'chunk/{position}', container.read_chunk(index))
            for name in manifest.get('movies', {}):
                archive.writestr('movie/' + name, movie_files[movie_overlay.movie_path(name)],
                                 compress_type=zipfile.ZIP_STORED)
        with zipfile.ZipFile(inner) as archive:
            read_descriptor(archive)
        result = write(destination, package=marker['package'], program_id=marker['program_id'], files={MEMBER: inner})
    Reader(destination)
    if commit_revisions is not None:
        commit_revisions()
    return {**result, 'materialized_files': 1, 'players': len(manifest['players'])}
