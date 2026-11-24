"""Apply independent V17 player packages to the currently installed assets."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import tempfile
import uuid
import zipfile

from content_pack import BLOCK, PREFIX, MAX_MANIFEST, relative
from effective_players import ACTIVE, LIVE, from_cpk
from native_iostore import Container, append_container, read_package_header
from player_identity_binding import owned_person_aliases
from runtime_document import MANIFEST
from player_append_tables import ALIASES, APPEARANCE, BOOTS, PLAYER, VARIATION, WEEKLY
from player_append_tables import add_values, apply as merge_tables, merge_values
import player_skill_bindings
import player_movie_overlay as movie_overlay

MEMBER = 'custom-resources/personal-player-studio/player-append-v1.zip'
PROTOCOL = 'studio-player-append-v1'
UPDATE_PROTOCOL = 'studio-player-append-v2'
MOVIE_PROTOCOL = 'studio-player-append-v3'
SNAPSHOTS = (UPDATE_PROTOCOL, MOVIE_PROTOCOL)
CONTAINER = 'custom-resources/legends-v10/pc3700_mobile_and'
TEMPLATE = 'work/templates/CMD_LOGIN_LIVEDATA.json'
RELEASE = 'release-manifest.json'


def capable(marker):
    return isinstance(marker, dict) and marker.get('player_append') == 1 and not marker.get('content_required')


def supports_replacement(marker):
    return capable(marker) and marker.get('player_append_updates') == 2


def supports_movies(marker):
    return supports_replacement(marker) and marker.get(movie_overlay.CAPABILITY) == 1


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf8')


def digest(path):
    with path.open('rb') as stream:
        return {'bytes': path.stat().st_size, 'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()}


def ids_for(definition, key):
    identity = definition['identity']
    uid, card, person = identity['package_id'], identity['card_id'], identity['person_id']
    owned = definition['owned']
    if (str(uuid.UUID(uid)) != uid or owned['_personal_player_uuid'] != uid
            or owned['gameplayer_id']['id'] != card or not 0 < card < 2**63
            or not 8000000 <= key < 9000000 or not 90000000 <= person < 99000000
            or card & 0xfffffff != person or identity.get('bound_original_player_id')
            or not set(identity.get('previous_card_ids', [])) <= {card}
            or len(definition['base_parameters']) != 147):
        raise ValueError('球员身份与可独立追加的编号不匹配')
    return {card, key, *owned_person_aliases(identity)}


def engine_key(apk, marker):
    rule = marker['player_append_engine']
    with apk.open('lib/arm64-v8a/libUE4.so') as source:
        source.seek(rule['offset'])
        key = source.read(32)
    if hashlib.sha256(key).hexdigest() != rule['sha256']:
        raise ValueError('独立球员小包与游戏的资源接口不符')
    return key


def read_descriptor(archive):
    item = archive.getinfo('manifest.json')
    if item.file_size > MAX_MANIFEST:
        raise ValueError('独立球员资料清单过大')
    value = json.loads(archive.read(item))
    if (value['protocol'] not in (PROTOCOL, *SNAPSHOTS) or not 1 <= len(value['players']) <= 1000
            or len(value['chunks']) > 8192 or len(value['paths']) > 8192):
        raise ValueError('独立球员小包格式无效')
    if value['protocol'] in SNAPSHOTS:
        if not {PLAYER, APPEARANCE, VARIATION, BOOTS, ALIASES, WEEKLY} <= value['tables'].keys():
            raise ValueError('球员更新小包缺少完整的球员资料表')
        if {p['art_key'] for p in value['players']} - {art_owner(name) for name in value['paths']}:
            raise ValueError('球员更新小包缺少所选球员的美术资源')
    for row in value['players']:
        rev = row.get('revision')
        if rev is not None and (type(rev) is not int or rev < 1):
            raise ValueError('独立球员小包版本号无效')
        ch = row.get('content_hash')
        if ch is not None and (not isinstance(ch, str) or len(ch) != 64):
            raise ValueError('独立球员小包内容校验格式无效')
    names = {'manifest.json', *(f'chunk/{i}' for i in range(len(value['chunks'])))}
    if value['protocol'] == MOVIE_PROTOCOL:
        routes = [route for player in value['players'] for route in player['routes']]
        movies = value.get('movies')
        if (not isinstance(movies, dict) or movies.keys() != movie_overlay.private_movies(routes)
                or any(not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha)
                       for sha in movies.values())):
            raise ValueError('动态卡面视频清单与球员绑定不一致')
        if set(movie_overlay.required_art(movie_overlay.route_assets(routes))) - value['paths'].keys():
            raise ValueError('独立小包缺少动态卡面的美术资源')
        names.update('movie/' + name for name in movies)
    info = archive.infolist()
    if (set(archive.namelist()) != names or len(info) != len(names)
            or max(i.file_size for i in info) > 64*1024**2
            or sum(i.file_size for i in info) > 512*1024**2):
        raise ValueError('独立球员美术内容超出上限或包含未知成员')
    return value


def validate_target_and_base(patch, marker):
    target_game = patch.get('target_game')
    marker_game = marker.get('game_version')
    if target_game and marker_game and target_game != marker_game:
        raise ValueError(f'这是 {target_game} 的小包，当前游戏是 {marker_game}，请先使用对应版本')
    base = patch.get('base')
    if isinstance(base, dict):
        if base.get('package') and marker.get('package') and base['package'] != marker['package']:
            raise ValueError('小包与当前游戏的安装模式不匹配，请安装对应的基准 APK')
        if base.get('program_id') and marker.get('program_id') and base['program_id'] != marker['program_id']:
            raise ValueError('小包与当前安装的基准 APK 不匹配，请先安装对应的基准 APK')
        marker_engine = (marker.get('player_append_engine') or {}).get('sha256')
        if base.get('engine_sha256') and marker_engine and base['engine_sha256'] != marker_engine:
            raise ValueError('小包与当前游戏的基准 APK 引擎版本不匹配，请先安装对应的基准 APK')


def merge_document(document, patch, *, report=None):
    snapshot = patch['protocol'] in SNAPSHOTS
    current = {row['identity']['package_id']: row for row in document['players']}
    routes = {row['cardId']: row for row in document['native_binding']['routes']}
    profile = document['native_binding']['profile']
    player_ids = set()
    cards = set()
    linked_boosters = set()
    replace_ids = set()
    changed = False
    for addition in patch['players']:
        definition = addition['definition']
        uid = definition['identity']['package_id']
        key = addition['art_key']
        ids = ids_for(definition, key)
        previous = current.get(uid)
        incoming_rev = addition.get('revision')
        incoming_hash = addition.get('content_hash')
        player_name = (definition.get('owned') or {}).get('name') or uid
        if snapshot and previous is not None and incoming_rev is None and uid in document.get('player_revisions', {}):
            raise ValueError(f'旧小包缺少设计版本号，不能覆盖已记录版本的球员（{player_name}）')
        if incoming_rev is not None and (type(incoming_rev) is not int or incoming_rev < 1):
            raise ValueError('球员设计版本号无效')
        if ids & player_ids:
            raise ValueError('游戏中已存在不同设计或重叠编号，需用 APK 更新该球员')

        owned_routes = {row['cardId']: row for row in addition['routes']}
        if (owned_routes.keys() != ids or len(owned_routes) != len(addition['routes'])
                or any(str(row['portraitKey']) != str(key) or str(row['modelFaceKey']) != str(key)
                       for row in owned_routes.values())):
            raise ValueError('独立球员的卡面或脸模绑定编号无效')

        skip_player = False
        if previous is None:
            if ids & routes.keys():
                raise ValueError('游戏已有球员编号正在使用')
            status, reason = 'added', '新增球员'
            changed = True
            if incoming_rev is not None:
                document.setdefault('player_revisions', {})[uid] = {
                    'revision': incoming_rev, 'content_hash': incoming_hash or ''}
        elif incoming_rev is not None:
            revisions = document.setdefault('player_revisions', {})
            old_info = revisions.get(uid)
            if old_info is not None:
                old_rev = old_info['revision']
                old_hash = old_info.get('content_hash', '')
                if incoming_rev < old_rev:
                    raise ValueError(f'旧小包不能覆盖新设计（{player_name}：小包 v{incoming_rev} < 已安装 v{old_rev}）')
                if incoming_rev == old_rev:
                    if incoming_hash is not None and incoming_hash == old_hash:
                        status, reason, skip_player = 'skipped', '已是最新', True
                    else:
                        raise ValueError(f'球员设计版本冲突（{player_name}：同为 v{incoming_rev} 但内容不同），已拒绝覆盖')
                else:
                    if not snapshot:
                        raise ValueError('游戏中已存在不同设计或重叠编号，需用 APK 更新该球员')
                    if incoming_hash is not None and incoming_hash == old_hash:
                        status, reason, skip_player = 'skipped', '内容无变化，已记录较新版本号', True
                        revisions[uid] = {'revision': incoming_rev, 'content_hash': incoming_hash}
                    else:
                        status, reason = 'updated', f'已更新至 v{incoming_rev}'
            else:
                if not snapshot:
                    if previous != definition:
                        raise ValueError('游戏中已存在不同设计或重叠编号，需用 APK 更新该球员')
                    status, reason, skip_player = 'skipped', '已是最新', True
                else:
                    status, reason = 'updated', f'已更新至 v{incoming_rev}'
            if not skip_player:
                previous_card = previous['identity']['card_id']
                previous_route = routes[previous_card]
                previous_key = int(previous_route['portraitKey'])
                if previous_card != definition['identity']['card_id'] or previous_key != key or ids_for(previous, key) != ids:
                    raise ValueError('此球员的游戏卡号或美术编号已经改变，请先更新基准 APK')
                if any(r not in routes or int(routes[r]['portraitKey']) != key or int(routes[r]['modelFaceKey']) != key for r in ids):
                    raise ValueError('已安装球员的美术归属不完整')
                replace_ids.update(ids)
                revisions[uid] = {'revision': incoming_rev, 'content_hash': incoming_hash or ''}
                changed = True
        else:
            if not snapshot and previous != definition:
                raise ValueError('游戏中已存在不同设计或重叠编号，需用 APK 更新该球员')
            if snapshot:
                previous_card = previous['identity']['card_id']
                previous_route = routes[previous_card]
                previous_key = int(previous_route['portraitKey'])
                if previous_card != definition['identity']['card_id'] or previous_key != key or ids_for(previous, key) != ids:
                    raise ValueError('此球员的游戏卡号或美术编号已经改变，请先更新基准 APK')
                if any(r not in routes or int(routes[r]['portraitKey']) != key or int(routes[r]['modelFaceKey']) != key for r in ids):
                    raise ValueError('已安装球员的美术归属不完整')
                replace_ids.update(ids)
                player_changed = previous != definition or any(routes.get(card) != route for card, route in owned_routes.items())
                status, reason = ('updated', '已更新设计') if player_changed else ('skipped', '已是最新')
            else:
                status, reason, skip_player = 'skipped', '已是最新', True

        if any(card not in replace_ids and card in routes and routes[card] != route for card, route in owned_routes.items()):
            raise ValueError('游戏已有球员的美术绑定与小包冲突')

        player_ids.update(ids)
        cards.add(definition['identity']['card_id'])
        linked_boosters.update(definition['base_parameters'][144:147])

        if not skip_player:
            player_diff = previous != definition or any(routes.get(card) != route for card, route in owned_routes.items())
            changed |= player_diff
            current[uid] = definition
            routes.update(owned_routes)
            if addition.get('design') is not None:
                designs = document.setdefault('write_designs', {}).setdefault('players', {})
                design_diff = designs.get(uid) != addition['design']
                changed |= snapshot and design_diff
                if snapshot and design_diff and status == 'skipped':
                    status, reason = 'updated', '已更新设计'
                designs[uid] = addition['design']
            series = addition.get('series')
            if series is not None:
                series_by_card = document.setdefault('card_series_by_id', {})
                card = str(definition['identity']['card_id'])
                series_diff = series_by_card.get(card) != series
                changed |= snapshot and series_diff
                if snapshot and series_diff and status == 'skipped':
                    status, reason = 'updated', '已更新设计'
                series_by_card[card] = series

        if report is not None:
            report.append({
                'package_id': uid,
                'name': player_name,
                'card_id': definition['identity']['card_id'],
                'revision': incoming_rev,
                'status': status,
                'reason': reason,
            })
    booster_ids = set(patch['boosters'].values())
    if any(type(bid) is not int or not 1 <= bid <= 1023 for bid in booster_ids) or not booster_ids <= linked_boosters:
        raise ValueError('小包自定义增能与球员不匹配')
    incoming_skills = dict(patch['skill_bindings'])
    if (len(incoming_skills) != len(patch['skill_bindings']) or incoming_skills.keys() - cards
            or any(type(flag) is not int or flag < 0 or flag & ~0xf8f for flag in incoming_skills.values())):
        raise ValueError('小包的球员技能编号无效')
    replace_skills = cards & replace_ids
    if incoming_skills or replace_skills:
        hook = next((entry for entry in profile.get('native_hooks', []) if entry['name'] == player_skill_bindings.HOOK), None)
        if hook is None and incoming_skills:
            raise ValueError('基准 APK 缺少此小包需要的球员技能接口')
        if hook is not None:
            prefix, marker, rows = player_skill_bindings.unpack(hook['body_hex'])
            if merge_values(rows, incoming_skills, '球员技能绑定', replace_skills):
                hook['body_hex'] = player_skill_bindings.pack(prefix, marker, rows)
                changed |= snapshot
    add_values(document.setdefault('editor_registries', {}).setdefault('boosters', {}), patch['boosters'], '增能永久编号')
    if set(map(int, patch['preview_boosters'])) - booster_ids:
        raise ValueError('小包增能预览编号无效')
    add_values(document.setdefault('preview_boosters', {}), patch['preview_boosters'], '增能预览')
    if patch['text_routes']:
        if not profile.get('text_hooks'):
            raise ValueError('基准 APK 未包含这项增能文字接口')
        old = {row['id']: row for row in profile.get('boosters', [])}
        incoming = {row['id']: row for row in patch['text_routes']}
        if incoming.keys() - booster_ids:
            raise ValueError('小包增能文字路由无效')
        add_values(old, incoming, '增能文字路由')
        profile['boosters'] = list(old.values())
    document['players'] = list(current.values())
    document['native_binding']['routes'] = list(routes.values())
    document.setdefault('write_selection', {})['final_players'] = len(current)
    return changed, player_ids, booster_ids, replace_ids


def art_owner(name):
    if not name.startswith(('PesMobile/Content/Assets/character/RealFace/',
                            'PesMobile/Content/Assets/ui/Data/Thumbnail/Player/')):
        return None
    match = re.fullmatch(r'(?:(?:SK_|MI_|T_)([0-9]+)_.+|([0-9]+)(?:_|f\.|bg\.).+)',
                         name.rsplit('/', 1)[-1])
    return int(match[1] or match[2]) if match else None


def package_id(chunk):
    return struct.unpack_from('<Q', chunk)[0]


def merge_art(archive, patch, current, output, player_ids, replace_ids=frozenset()):
    current_paths = {path.removeprefix('../../../'): index for path, index in current.paths.items()}
    motion_names = (movie_overlay.route_assets(route for row in patch['players'] for route in row['routes'])
                    if patch['protocol'] == MOVIE_PROTOCOL else set())
    owned = {name: index for name, index in current_paths.items() if art_owner(name) in replace_ids} if replace_ids else {}
    replace_indices = set(owned.values())
    replace_packages = {package_id(current.chunk_ids[index]) for index in replace_indices}
    if replace_packages and any(name not in owned and package_id(current.chunk_ids[index]) in replace_packages
                                for name, index in current_paths.items()):
        raise ValueError('本次选中的卡面引用了其它球员的共享资源')
    additions = []
    positions = {}
    ids = set()
    different = False
    for index, value in enumerate(patch['chunks']):
        chunk = bytes.fromhex(value)
        if len(chunk) != 12 or chunk in ids or chunk[11] not in (2, 3, 4):
            raise ValueError('独立球员美术资源编号无效')
        ids.add(chunk)
        raw = archive.read(f'chunk/{index}')
        if chunk in current.by_id:
            prior = current.by_id[chunk]
            changed = current.read_chunk(prior) != raw
            if changed and prior not in replace_indices:
                raise ValueError('已有球员美术编号冲突')
            if prior not in replace_indices:
                continue
            different |= changed
        else:
            different = True
        positions[index] = len(additions)
        additions.append((chunk, raw))
    paths = {}
    incoming_owned = {}
    for name, index in patch['paths'].items():
        relative('custom-resources/'+name)
        owner = art_owner(name)
        if ((owner not in player_ids and movie_overlay.motion_asset(name) not in motion_names)
                or type(index) is not int or not 0 <= index < len(patch['chunks'])):
            raise ValueError('小包的球员美术路径不属于本次追加的球员')
        if owner in replace_ids:
            incoming_owned[name] = bytes.fromhex(patch['chunks'][index])
        if index in positions:
            paths[name] = positions[index]
        elif current_paths.get(name) != current.by_id[bytes.fromhex(patch['chunks'][index])]:
            raise ValueError('已安装资源路径与小包不同')
    if set(paths.values()) != set(positions.values()):
        raise ValueError('小包有未登记的美术资源')
    new_packages = {package_id(chunk) for chunk, _ in additions if chunk[11] == 2}
    packages = [row for row in patch['packages'] if row['id'] in new_packages]
    if len(packages) != len(new_packages) or {row['id'] for row in packages} != new_packages:
        raise ValueError('小包的美术依赖不完整')
    different |= {name: current.chunk_ids[index] for name, index in owned.items()} != incoming_owned
    if replace_packages and not different:
        header = next(i for i, chunk in enumerate(current.chunk_ids) if chunk[11] == 10)
        _, existing_packages = read_package_header(current.read_chunk(header))
        different = {row['id']: row for row in existing_packages if row['id'] in replace_packages} != {
            row['id']: row for row in packages}
    if different:
        append_container(current, output, additions, paths, packages, replace_indices,
                         remove_indices=replace_indices, remove_paths=owned.keys())
    return different


def stage(reader, apk, marker, installed, target, *, report=None):
    """Stage an all-or-nothing update before publishing any game files."""
    if set(reader.entries) != {MEMBER} or reader.manifest['removed'] or not capable(marker):
        raise ValueError('请选择支持独立球员小包的基准 APK')
    if reader.entries[MEMBER][1] > 512*1024**2:
        raise ValueError('独立球员小包过大，请分批导出')
    with tempfile.TemporaryFile(dir=target(RELEASE).parent) as spool, reader.open(MEMBER) as stream:
        shutil.copyfileobj(stream, spool, BLOCK)
        spool.seek(0)
        with zipfile.ZipFile(spool) as archive:
            patch = read_descriptor(archive)
            validate_target_and_base(patch, marker)
            snapshot = patch['protocol'] in SNAPSHOTS
            if snapshot and not supports_replacement(marker):
                raise ValueError('此小包需要支持独立球员更新的 V17 基准 APK')
            dynamic = patch['protocol'] == MOVIE_PROTOCOL
            if dynamic and not supports_movies(marker):
                raise ValueError('此小包需要支持动态卡面小包的 V17 基准 APK')
            doc = json.loads(installed(MANIFEST).read_bytes())
            old_revisions = dict(doc.get('player_revisions', {}))
            player_results = []
            changed, player_ids, boosters, replace_ids = merge_document(doc, patch, report=player_results)
            all_versioned = all(p.get('revision') is not None and p.get('content_hash') is not None for p in patch['players'])
            if all_versioned and player_results and all(row['status'] == 'skipped' for row in player_results) and not changed:
                metadata_only = old_revisions != doc.get('player_revisions', {})
                if report is not None:
                    report.update({'protocol': patch['protocol'], 'target_game': patch.get('target_game'),
                                   'players': player_results, 'metadata_only': metadata_only})
                if metadata_only:
                    return stage_document(doc, installed, target, [])
                return []
            if not changed and not snapshot:
                if report is not None:
                    report.update({'protocol': patch['protocol'], 'target_game': patch.get('target_game'), 'players': player_results})
                return []
            previous_cpk = installed(ACTIVE).read_bytes()
            cpk = merge_tables(previous_cpk, patch['tables'], player_ids, boosters,
                               layout=apk.read(PREFIX+ACTIVE), replace_ids=replace_ids, snapshot=snapshot)
            catalogue = from_cpk(cpk, doc)
            if not player_ids <= set(catalogue['source_card_ids']):
                raise ValueError('独立小包未包含完整球员资料')
            key = engine_key(apk, marker)
            art = Container(installed(CONTAINER+'.utoc'), key)
            output = target(CONTAINER+'.utoc')
            output.parent.mkdir(parents=True, exist_ok=True)
            merged = merge_art(archive, patch, art, output, player_ids, replace_ids)
            movies_changed = False
            if dynamic:
                movies_changed = movie_overlay.stage_movies(
                    archive, patch['movies'], installed(movie_overlay.PAK),
                    doc['native_binding']['routes'], target(movie_overlay.PAK), key)
            if not (changed or merged or movies_changed or cpk != previous_cpk):
                if report is not None:
                    report.update({'protocol': patch['protocol'], 'target_game': patch.get('target_game'), 'players': player_results})
                return []
            if report is not None:
                if merged or movies_changed or cpk != previous_cpk:
                    for row in player_results:
                        if row['status'] == 'skipped' and row.get('revision') is None:
                            row['status'] = 'updated'
                            row['reason'] = '已更新资源或资料'
                report.update({'protocol': patch['protocol'], 'target_game': patch.get('target_game'), 'players': player_results})
    changed_paths = [ACTIVE, LIVE, TEMPLATE, MANIFEST, RELEASE]
    for name in (ACTIVE, LIVE):
        path = target(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(cpk)
    for suffix in ('.utoc', '.ucas') if merged else ():
        name = CONTAINER + suffix
        changed_paths.append(name)
        doc['resource_files'][Path(name).name] = digest(target(name))
    doc['resource_files'][Path(ACTIVE).name] = digest(target(ACTIVE))
    from native_live import compile_template, verify_template, version_info
    template = json.loads(installed(TEMPLATE).read_bytes())
    metadata = None
    if movies_changed:
        changed_paths.append(movie_overlay.PAK)
        metadata = movie_overlay.template_metadata(template, digest(target(movie_overlay.PAK)))
    template = compile_template(template, cpk, version_info(template)['version']+1, metadata)
    doc['live_data'] = verify_template(template, cpk)
    doc['native_player_catalogue'] = catalogue
    from division_squads import booster_catalogue
    doc['native_booster_catalogue'] = booster_catalogue(cpk)
    doc['native_binding']['local_player_append'] = True
    path = target(TEMPLATE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(compact(template))
    return stage_document(doc, installed, target, changed_paths)


def stage_document(doc, installed, target, changed_paths):
    doc.pop('revision', None)
    doc['revision'] = hashlib.sha256(compact(doc)).hexdigest()
    document = target(MANIFEST)
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_bytes(compact(doc))
    changed_paths = list(dict.fromkeys([*changed_paths, MANIFEST, RELEASE]))
    release = json.loads(installed(RELEASE).read_bytes())
    entries = {row['path']: row for row in release['files']}
    for name in changed_paths:
        if name != RELEASE:
            entries.setdefault(name, {'path': name}).update(digest(target(name)))
    release.update(files=list(entries.values()), personal_player_revision=doc['revision'],
                   release_id=release['base_release_id']+'-studio-'+doc['revision'][:16])
    target(RELEASE).write_bytes(compact(release))
    return changed_paths
