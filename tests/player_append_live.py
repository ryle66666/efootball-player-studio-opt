"""Run the shipped standalone exporter and installer against real V17 assets."""
import argparse
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

from independent_player_probe import PLAYER_IDS, WINDOWS, test_save
from content_pack import PREFIX, Reader as PackReader, write
from content_runtime import prepare
from native_cpk import Cpk, unwrap, wrap
from native_iostore import Container, append_container, read_package_header
from personal_source import owned_art_paths, ownership, read as read_personal
from player_append import ACTIVE, CONTAINER, LIVE, MANIFEST, MEMBER, RELEASE, TEMPLATE, compact
from player_append_export import write_append
from player_append_tables import ALIASES, APPEARANCE, BOOSTERS, LAYOUTS, WEEKLY, aliases, rows
import player_skill_bindings
from program_bootstrap import MARKER, changes
from resource_set import Reader
from write_modes import source_container


def without(raw, exclude):
    cpk = Cpk(raw)
    replacements = {}
    for name in (*LAYOUTS, WEEKLY, ALIASES):
        if name == BOOSTERS:
            continue
        original = cpk.read(name)
        value = unwrap(original)
        if name == WEEKLY:
            value = b''.join(struct.pack('<QII', *row) for row in struct.iter_unpack('<QII', value) if row[0] not in exclude)
        elif name == ALIASES:
            kept = {key: val for key, val in aliases(value).items() if key not in
                    {f'{pid}{suffix}' for pid in exclude for suffix in ('', '_', '_l', 'f')}}
            output = io.StringIO(newline='')
            writer = csv.writer(output, lineterminator='\r\n')
            writer.writerow(['before', 'after'])
            writer.writerows(kept.items())
            value = output.getvalue().encode()
        else:
            value = b''.join(row for key, row in rows(name, value).items() if key not in exclude)
        replacements[name] = wrap(value, original)
    return cpk.replace(replacements)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('apk', type=Path)
    parser.add_argument('profile', type=Path)
    parser.add_argument('content_pack', type=Path)
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text('utf8'))
    local = Path(__file__).resolve().parent / 'local'
    old = PackReader(args.content_pack).manifest
    with (tempfile.TemporaryDirectory(prefix='append-runtime-', dir=local) as name,
          Reader(args.apk, packages={'content_pack': str(args.content_pack)}, verify_identities=False) as source):
        work = Path(name).resolve()
        assert work.parent == local.resolve() and work.name.startswith('append-runtime-')
        root = work/'release'
        arm = {'offset': profile['resource_key_offset'], 'sha256': profile['resource_key_sha256']}
        replacements = {}
        changes(source, replacements, WINDOWS/'resources', old['package'], old['program_id'],
                player_updates=True, player_append_engine=arm, player_append_updates=True)
        marker = json.loads(replacements[MARKER])
        from native_package import hook_archive
        from runtime_modules import WORLD_HELPERS, PERSONAL_HELPERS
        helpers = {name:(WINDOWS/(name+'.py')).read_text('utf8') for name in WORLD_HELPERS+PERSONAL_HELPERS[4:]}
        hooked = hook_archive(source.read('assets/chaquopy/app.imy', limit=None),
                              (WINDOWS/'personal_runtime.py').read_text('utf8'),
                              binding_source=(WINDOWS/'personal_binding.py').read_text('utf8'),
                              division_source=(WINDOWS/'division_squads.py').read_text('utf8'),
                              rating_source=(WINDOWS/'native_overall.py').read_text('utf8'),
                              helper_sources=helpers)
        runtime = work/'apk-runtime.zip'
        runtime.write_bytes(hooked)
        with source.open('lib/arm64-v8a/libUE4.so') as stream:
            stream.seek(arm['offset']); key = stream.read(32)
        assert hashlib.sha256(key).hexdigest() == arm['sha256']
        # A minimal ZIP exercises startup marker and engine resource resolution.
        # The real APK above stays read only.
        fixture = work/'program-fixture.zip'
        with zipfile.ZipFile(fixture, 'x') as archive:
            archive.writestr(MARKER, replacements[MARKER])
            archive.writestr('lib/arm64-v8a/libUE4.so', bytes(arm['offset'])+key)
            archive.writestr(PREFIX+ACTIVE, source.read(PREFIX+ACTIVE, limit=None))
        storage = work/'.content-packs'; storage.mkdir()
        (storage/'program.json').write_bytes(replacements[MARKER])
        native = source.read(PREFIX+ACTIVE, limit=None)
        release = json.loads(source.read(PREFIX+RELEASE))
        document = read_personal(source, release)
        owners = ownership(document)
        keep = [p for p in document['players'] if p['identity']['package_id'] not in PLAYER_IDS]
        selected = {uid: owners[uid] for uid in PLAYER_IDS}
        excluded = set.union(*(p['aliases'] for p in selected.values()))
        clean = without(native, excluded)
        art = source_container(source, work, key)
        toc = root/(CONTAINER+'.utoc')
        append_container(art, toc, [], {}, [], remove_indices=owned_art_paths(art, selected.values()))
        binding = copy.deepcopy(document['native_binding'])
        binding['routes'] = [r for r in binding['routes'] if r['cardId'] not in excluded]
        skill = next(row for row in binding['profile']['native_hooks'] if row['name'] == player_skill_bindings.HOOK)
        prefix, offset, skill_rows = player_skill_bindings.unpack(skill['body_hex'])
        skill['body_hex'] = player_skill_bindings.pack(prefix, offset, {key:val for key,val in skill_rows.items() if key not in excluded})
        start = dict(document, players=keep, native_binding=binding)
        for member, data in ((ACTIVE, clean), (LIVE, clean), (MANIFEST, compact(start)),
                             (TEMPLATE, source.read(PREFIX+TEMPLATE)), (RELEASE, compact(release))):
            path = root/member
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        output = []
        legacy_marker = {key: value for key, value in marker.items() if key != 'player_append_updates'}
        for uid in PLAYER_IDS:
            after = without(native, excluded-selected[uid]['aliases'])
            pack = work/(uid+'.73pack')
            write_append(pack, legacy_marker, clean, after, art, document, [uid])
            assert set(PackReader(pack).entries) == {MEMBER}
            output.append(pack)
        sequence = []
        for index, pack in enumerate(output):
            shutil.copyfile(pack, storage/'pending.73pack')
            result = prepare(root, base_apk=fixture)
            assert result['status'] == 'applied' and result['files'] == 7, result
            updated = json.loads((root/MANIFEST).read_bytes())
            assert {p['identity']['package_id'] for p in updated['players']} == {p['identity']['package_id'] for p in keep}|set(PLAYER_IDS[:index+1])
            definition = next(p for p in updated['players'] if p['identity']['package_id'] == PLAYER_IDS[index])
            sequence.append((definition, (root/ACTIVE).read_bytes()))
            print('已独立追加', definition['identity']['package_id'], pack.stat().st_size, flush=True)
        shutil.copyfile(output[0], storage/'pending.73pack')
        before = hashlib.sha256((root/MANIFEST).read_bytes()).hexdigest()
        result = prepare(root, base_apk=fixture)
        assert result['files'] == 0 and before == hashlib.sha256((root/MANIFEST).read_bytes()).hexdigest(), result
        conflicting = work/'conflict.zip'
        with PackReader(output[0]).open(MEMBER) as stream, zipfile.ZipFile(io.BytesIO(stream.read())) as original:
            with zipfile.ZipFile(conflicting, 'x', compression=zipfile.ZIP_DEFLATED) as conflict:
                descriptor = json.loads(original.read('manifest.json'))
                descriptor['players'][0]['definition']['owned']['name'] += ' conflict'
                conflict.writestr('manifest.json', compact(descriptor))
                for name in original.namelist():
                    if name != 'manifest.json':
                        conflict.writestr(name, original.read(name))
        write(storage/'pending.73pack', package=marker['package'], program_id=marker['program_id'], files={MEMBER: conflicting})
        result = prepare(root, base_apk=fixture)
        assert result['status'] == 'current' and '不同设计' in result.get('detail', ''), result
        assert before == hashlib.sha256((root/MANIFEST).read_bytes()).hexdigest()
        final_art = Container(toc, key)
        for index in owned_art_paths(art, selected.values()):
            assert final_art.read_chunk(final_art.by_id[art.chunk_ids[index]]) == art.read_chunk(index)
        assert (root/ACTIVE).read_bytes() == (root/LIVE).read_bytes()
        skill = next(row for row in updated['native_binding']['profile']['native_hooks'] if row['name'] == player_skill_bindings.HOOK)
        assert player_skill_bindings.unpack(skill['body_hex'])[2] == skill_rows
        # The edited design is exported against the same clean baseline that
        # never contained either independently installed player.
        first, second = PLAYER_IDS
        edited = copy.deepcopy(document)
        updated_player = next(p for p in edited['players'] if p['identity']['package_id'] == first)
        updated_player['owned']['_personal_design_revision'] = hashlib.sha256(b'player edit fixture').hexdigest()
        after = Cpk(without(native, selected[second]['aliases']))
        original_appr = after.read(APPEARANCE)
        appearances = rows(APPEARANCE, unwrap(original_appr))
        card = updated_player['identity']['card_id']
        old = appearances[card]
        appearances[card] = old[:-1] + bytes([old[-1] ^ 1])
        revised = after.replace({APPEARANCE: wrap(b''.join(appearances.values()), original_appr)})
        first_art = owned_art_paths(art, [selected[first]])
        art_index = next(i for i in sorted(first_art) if art.chunk_ids[i][11] == 2)
        chunk = art.chunk_ids[art_index]
        old = art.read_chunk(art_index)
        revised_art = old[:-1] + bytes([old[-1] ^ 1])
        header_index = next(i for i, ident in enumerate(art.chunk_ids) if ident[11] == 10)
        _, packages = read_package_header(art.read_chunk(header_index))
        own_package = [p for p in packages if p['id'] == struct.unpack_from('<Q', chunk)[0]]
        revised_container = append_container(art, work/'revised.utoc', [(chunk, revised_art)],
                                             {}, own_package, replace_indices={art_index})
        update = work/'card-update.73pack'
        write_append(update, marker, clean, revised, revised_container, edited, [first], inherited=start)
        shutil.copyfile(update, storage/'pending.73pack')
        # Import using only modules packaged into the game. Importing from
        # the workshop's source directory can hide missing APK dependencies.
        script = """
import sys
sys.path.insert(0, sys.argv[1])
from content_runtime import prepare
result = prepare(sys.argv[2], base_apk=sys.argv[3])
assert result['status'] == 'applied' and result['files'] == 7, result
"""
        subprocess.run([sys.executable, '-I', '-c', script, str(runtime), str(root), str(fixture)], check=True)
        final = json.loads((root/MANIFEST).read_bytes())
        final_players = {p['identity']['package_id']: p for p in final['players']}
        assert final_players[first] == updated_player
        assert final_players[second] == sequence[1][0]
        final_art = Container(toc, key)
        assert final_art.read_chunk(final_art.by_id[chunk]) == revised_art
        for index in owned_art_paths(art, [selected[second]]):
            assert final_art.read_chunk(final_art.by_id[art.chunk_ids[index]]) == art.read_chunk(index)
        table = unwrap(Cpk((root/ACTIVE).read_bytes()).read(APPEARANCE))
        assert rows(APPEARANCE, table)[card] == appearances[card]
        assert (root/ACTIVE).read_bytes() == (root/LIVE).read_bytes()
        repeated = work/'repeated.zip'
        with PackReader(update).open(MEMBER) as payload, repeated.open('xb') as target:
            shutil.copyfileobj(payload, target)
        write(storage/'pending.73pack', package=marker['package'], program_id=marker['program_id'], files={MEMBER: repeated})
        result = prepare(root, base_apk=fixture)
        assert result['status'] == 'current' and result['files'] == 0, result
        assert result['import_report']['status'] == 'skipped', result
        assert not (storage/'pending.73pack').exists()
        assert (storage/'active.73pack').read_bytes() == update.read_bytes()
        seed = source.read(PREFIX+'outputs/efootball/local-club.sqlite3', limit=None)
        test_save(seed, document, sequence)
        from art_registry import seal_personal_order
        from native_binding_source import read_installer, verify_installer
        payload, code = read_installer(args.apk, content_catalogue=updated['content_catalogue'])
        verify_installer(code, seal_personal_order(updated['native_binding'], payload))
        print('真实资源、重复导入、存档进度与最终美术绑定校验通过。')


if __name__ == '__main__':
    main()
