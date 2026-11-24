"""Exercise two independent additions against a copy of real V17 game data.

The temporary ZIPs are diagnostic payloads. They are not game import files.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
from types import SimpleNamespace
import zipfile

WINDOWS = Path(__file__).resolve().parents[1] / 'Windows'
sys.path.insert(0, str(WINDOWS))

from content_delta_v2 import record_plan, rows as indexed_rows
from content_delta import PREFIX, TABLES
from effective_players import ACTIVE, from_cpk
from native_cpk import Cpk, unwrap, wrap
from native_iostore import append_container, read_package_header
from personal_runtime import apply_definitions, REGISTRY_KEY
from personal_source import owned_art_paths, ownership, read as read_personal
from resource_set import Reader
from write_modes import source_container


PLAYER_IDS = ('1ad329ca-5d36-41af-8929-6c1098c0d219',
              '54255f0e-b15b-4734-84bf-9dd04f06701b')


def without_player_records(raw, excluded):
    cpk = Cpk(raw)
    changes = {}
    for name, (width, offset) in TABLES.items():
        wrapped = cpk.read(name)
        rows = indexed_rows(unwrap(wrapped), width, offset)
        changes[name] = wrap(b''.join(row for key, row in rows.items() if key not in excluded), wrapped)
    return cpk.replace(changes)


def build_payload(path, cpk, source, player, owner):
    indices = sorted(owned_art_paths(source, [owner]))
    assert indices, player['identity']['package_id']
    chunk_ids = [source.chunk_ids[i] for i in indices]
    positions = {index: position for position, index in enumerate(indices)}
    paths = {name.removeprefix('../../../'): positions[index]
             for name, index in source.paths.items() if index in positions}
    _, store = read_package_header(source.read_chunk(next(i for i, key in enumerate(source.chunk_ids) if key[11] == 10)))
    package_ids = {struct.unpack_from('<Q', chunk)[0] for chunk in chunk_ids}
    packages = [entry for entry in store if entry['id'] in package_ids]
    assert packages
    tables = []
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, (width, offset) in TABLES.items():
            raw = unwrap(cpk.read(name))
            rows = indexed_rows(raw, width, offset)
            added = []
            for key in sorted(owner['aliases'] & rows.keys()):
                member = f'records/{len(tables)}/{key}'
                archive.writestr(member, rows[key])
                added.append({'key': key, 'record': member})
            tables.append({'path': name, 'stride': width, 'key_offset': offset, 'key_bytes': 8, 'added': added})
        for pos, index in enumerate(indices):
            archive.writestr(f'art/{pos}', source.read_chunk(index))
        archive.writestr('manifest.json', json.dumps({'definition': player, 'tables': tables,
            'chunks': [i.hex() for i in chunk_ids], 'paths': paths, 'packages': packages}, separators=(',', ':')))


def merge_payload(path, cpk_data, current, out):
    cpk = Cpk(cpk_data)
    changes = {}
    with zipfile.ZipFile(path) as archive:
        document = json.loads(archive.read('manifest.json'))
        records = SimpleNamespace(blob=archive.read)
        for table in document['tables']:
            name = table['path']
            wrapped = cpk.read(name)
            rows, actions, conflicts = record_plan(unwrap(wrapped), table, records)
            if conflicts:
                raise ValueError(f'球员原生编号冲突 {name}: {conflicts}')
            if any(status == 'added' for _, status in actions.values()):
                rows.update({key: row for key, (row, _) in actions.items()})
                order = sorted(rows) if TABLES[name][0] != 168 else rows
                changes[name] = wrap(b''.join(rows[key] for key in order), wrapped)
        chunks = [(bytes.fromhex(chunk), archive.read(f'art/{i}'))
                  for i, chunk in enumerate(document['chunks'])]
        present = [chunk in current.by_id for chunk, _ in chunks]
        if any(present):
            if not all(present) or any(current.read_chunk(current.by_id[chunk]) != raw for chunk, raw in chunks):
                raise ValueError('同编号球员美术内容不同')
            art = current
        else:
            art = append_container(current, out, chunks, document['paths'], document['packages'])
    return cpk.replace(changes) if changes else cpk_data, art, document['definition']


def test_save(seed, document, sequence):
    retired = [p['identity']['card_id'] for p in document['players'] if p['identity']['package_id'] in PLAYER_IDS]
    existing = [p for p in document['players'] if p['identity']['package_id'] not in PLAYER_IDS]
    level_info = None
    with closing(sqlite3.connect(':memory:')) as club:
        club.deserialize(seed)
        start = dict(document, players=existing, write_selection={'removed': {'player_ids': retired}})
        apply_definitions(club, start)
        imported = list(existing)
        for step, (definition, native) in enumerate(sequence):
            imported.append(definition)
            current = dict(document, players=imported,
                           native_player_catalogue=from_cpk(native, document), write_selection={})
            result = apply_definitions(club, current)
            first = next(row for row in result if row['package_id'] == PLAYER_IDS[0])
            key = (first['card_id'], first['serial'])
            row = json.loads(club.execute('select json from gameplayers where id=? and serial=?', key).fetchone()[0])
            if step == 0:
                assert first['status'] == 'granted'
                row['games'] = 7
                row['level_info']['exp_total'] += 20
                level_info = row['level_info']
                with club:
                    club.execute('update gameplayers set json=? where id=? and serial=?', (json.dumps(row), *key))
            else:
                second = next(row for row in result if row['package_id'] == PLAYER_IDS[1])
                assert second['status'] == 'granted' and row['games'] == 7 and row['level_info'] == level_info
                count = club.execute('select count(*) from gameplayers').fetchone()[0]
                apply_definitions(club, current)
                assert club.execute('select count(*) from gameplayers').fetchone()[0] == count
        registry = json.loads(club.execute('select value from meta where key=?', (REGISTRY_KEY,)).fetchone()[0])
        assert set(PLAYER_IDS) <= registry.keys()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('apk', type=Path)
    parser.add_argument('profile', type=Path)
    parser.add_argument('--content-pack', type=Path)
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text('utf8'))
    local = Path(__file__).resolve().parent / 'local'
    local.mkdir(exist_ok=True)
    packages = {'content_pack': str(args.content_pack)} if args.content_pack else None
    with tempfile.TemporaryDirectory(prefix='independent-player-', dir=local) as temporary, Reader(args.apk, packages=packages, verify_identities=False) as archive:
        work = Path(temporary).resolve()
        assert work.parent == local.resolve() and work.name.startswith('independent-player-')
        with archive.open('lib/arm64-v8a/libUE4.so') as engine:
            engine.seek(profile['resource_key_offset'])
            key = engine.read(32)
        assert hashlib.sha256(key).hexdigest() == profile['resource_key_sha256']
        source = source_container(archive, work, key)
        raw = archive.read(PREFIX+ACTIVE, limit=None)
        doc = read_personal(archive, json.loads(archive.read(PREFIX+'release-manifest.json')))
        owners = ownership(doc)
        players = {p['identity']['package_id']: p for p in doc['players']}
        print('来源中的自建球员数量', len(players), flush=True)
        chosen = [owners[uid] for uid in PLAYER_IDS]
        native = without_player_records(raw, set.union(*(p['aliases'] for p in chosen)))
        removed = owned_art_paths(source, chosen)
        art = append_container(source, work/'base.utoc', [], {}, [], remove_indices=removed)
        payloads = []
        for pos, uid in enumerate(PLAYER_IDS):
            path = work/f'player-{pos}.zip'
            build_payload(path, Cpk(raw), source, players[uid], owners[uid])
            payloads.append(path)
        sequence = []
        for pos, path in enumerate(payloads):
            native, art, definition = merge_payload(path, native, art, work/f'applied-{pos}.utoc')
            sequence.append((definition, native))
            print(definition['identity']['package_id'], path.stat().st_size, flush=True)
        original = Cpk(raw)
        merged = Cpk(native)
        for name, (width, offset) in TABLES.items():
            assert indexed_rows(unwrap(original.read(name)), width, offset) == indexed_rows(unwrap(merged.read(name)), width, offset)
        for index in removed:
            assert art.read_chunk(art.by_id[source.chunk_ids[index]]) == source.read_chunk(index)
        for path in payloads:
            again, same_art, _ = merge_payload(path, native, art, work/'duplicate.utoc')
            assert again is native and same_art is art
        seed = archive.read(PREFIX+'outputs/efootball/local-club.sqlite3', limit=None)
        test_save(seed, doc, sequence)
        print('两份同名球员已独立合并，读取结果与原生表和美术一致，重复导入、进度保留通过。')


if __name__ == '__main__':
    main()
