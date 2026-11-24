"""Fast self-contained unit tests for storage cleanup and player pack validation."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import threading
import time
from unittest.mock import patch
import unittest
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'Windows'))

from content_pack import PREFIX, Reader as PackReader, write as write_pack
from content_runtime import prepare
from native_cpk import Cpk, utf_xor, wrap
from native_iostore import Container, append_container, package_header, package_id, write_container
import pack_inspector
from player_append import ACTIVE, CONTAINER, LIVE, MANIFEST, MEMBER, RELEASE, TEMPLATE, compact, ids_for
from player_append_export import write_append
from player_append_tables import ALIASES, APPEARANCE, BOOTS, BOOSTERS, PLAYER, VARIATION, WEEKLY
from program_bootstrap import MARKER
import storage_usage
import workspace_tools


def _build_utf_table(columns, rows_data):
    """Build a minimal CRI @UTF table (columns: list of (name, type_code, value_or_key))."""
    # type codes matching native_cpk.FORMATS: 4=I, 6=Q, 10=string
    strings = bytearray(b'<NULL>\0')
    string_offsets = {}

    def intern(text):
        if text not in string_offsets:
            string_offsets[text] = len(strings)
            strings.extend(text.encode('utf8') + b'\0')
        return string_offsets[text]

    col_defs = bytearray()
    row_width = 0
    col_specs = []
    for name, code in columns:
        name_off = intern(name)
        col_defs.extend(struct.pack('>BI', 0x50 | code, name_off))
        width = 8 if code == 6 else 4
        col_specs.append((name, code, width))
        row_width += width

    row_buf = bytearray()
    for row in rows_data:
        for name, code, _ in col_specs:
            val = row[name]
            if code == 10:
                row_buf.extend(struct.pack('>I', intern(val)))
            elif code == 6:
                row_buf.extend(struct.pack('>Q', val))
            else:
                row_buf.extend(struct.pack('>I', val))

    schema_size = len(col_defs)
    rows_offset = 24 + schema_size
    strings_offset = rows_offset + len(row_buf)
    data_offset = strings_offset + len(strings)
    table_size = 24 + schema_size + len(row_buf) + len(strings)
    header = struct.pack(
        '>4sIIIIIIHHI',
        b'@UTF', table_size, 0,
        rows_offset, strings_offset, data_offset,
        0, len(columns), row_width, len(rows_data),
    )
    # Fix header fields: native_cpk reads >IIIIHHI at offset 8 -> ro, so, do, no, ncols, rowlen, nrows
    header = struct.pack(
        '>4sII', b'@UTF', table_size, 0,
    )[:8] + struct.pack(
        '>IIIIHHI', rows_offset, strings_offset, data_offset, 0, len(columns), row_width, len(rows_data)
    )
    return header + col_defs + row_buf + strings


def _build_cpk(files: dict[str, bytes]) -> bytes:
    """Build a minimal valid CPK archive accepted by native_cpk.Cpk."""
    align = 2048
    content_offset = align
    payload = bytearray()
    toc_rows = []
    for path, raw in sorted(files.items()):
        if '/' in path:
            dirname, filename = path.rsplit('/', 1)
        else:
            dirname, filename = '', path
        file_offset = len(payload)
        payload.extend(raw)
        pad = (-len(payload)) % align
        payload.extend(b'\0' * pad)
        toc_rows.append({
            'DirName': dirname,
            'FileName': filename,
            'FileSize': len(raw),
            'ExtractSize': len(raw),
            'FileOffset': file_offset,
        })

    toc_offset = align + len(payload)
    toc_utf = utf_xor(_build_utf_table(
        [('DirName', 10), ('FileName', 10), ('FileSize', 4), ('ExtractSize', 4), ('FileOffset', 6)],
        toc_rows,
    ))
    toc_section = b'TOC \0\0\0\0' + struct.pack('<Q', len(toc_utf)) + toc_utf
    total_end = toc_offset + len(toc_section)

    hdr_utf = utf_xor(_build_utf_table(
        [('TocOffset', 6), ('ContentOffset', 6), ('Align', 4), ('ContentSize', 6)],
        [{
            'TocOffset': toc_offset,
            'ContentOffset': content_offset,
            'Align': align,
            'ContentSize': total_end - content_offset,
        }],
    ))
    hdr_section = b'CPK \0\0\0\0' + struct.pack('<Q', len(hdr_utf)) + hdr_utf
    out = bytearray(total_end)
    out[:len(hdr_section)] = hdr_section
    out[content_offset:content_offset + len(payload)] = payload
    out[toc_offset:toc_offset + len(toc_section)] = toc_section
    return bytes(out)


def _dummy_wesys(raw: bytes) -> bytes:
    import zlib
    from native_cpk import wesys_crypt
    comp = zlib.compress(raw, 1)
    return struct.pack('<BBB5sII', 255, 0x21, 0x83, b'WESYS', len(comp), len(raw)) + wesys_crypt(comp, 1, len(comp), len(raw))


class _DummyStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()


class _DummyJob:
    def __init__(self, phase='idle'):
        self._phase = phase

    def status(self):
        return {'phase': self._phase}


class _DummyServer:
    def __init__(self, root: Path, home: Path):
        self.store = _DummyStore(root)
        self.library = type('Lib', (), {'home': Path(home)})()
        self.native_package = _DummyJob('idle')
        self.game_import = _DummyJob('idle')


class TestStorageUsage(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.home = self.base / 'studio'
        self.data = self.home / 'data'
        self.server = _DummyServer(self.data, self.home)

    def test_managed_base_rollover_and_delivery_cleanup(self):
        from content_pack import installed_program
        from managed_game_base import prune, publish
        from write_modes import latest_export

        store, library = self.server.store, self.server.library
        exports = self.data / 'exports'
        source = self.data / 'test-source.apk'
        source.write_bytes(b'source')
        package = 'fixture.game'

        def delivery(number, verified=True):
            bundle = exports / f'APK-资源集-{number}'
            bundle.mkdir(parents=True)
            apk = bundle / f'export-{number}.apk'
            apk.write_bytes(f'test-apk-{number}'.encode())
            report = bundle / 'export.report.json'
            report.write_text('{}', encoding='utf8')
            (bundle / 'complete.json').write_text(json.dumps({
                'validation': {'verified': verified, 'live_data_verified': verified},
            }), encoding='utf8')
            return {'path': str(apk), 'report_path': str(report),
                    'artifacts': [{'path': str(apk), 'kind': 'game'}]}

        def register(number, target_package=package):
            result = publish(store, delivery(number), target_package, str(uuid.UUID(int=number)),
                             source=source, library=library)
            prune(store, library.home, result)
            return result

        one = register(1)
        first_base, first_alias = Path(one['base_apk']), Path(one['path'])
        self.assertTrue(first_base.samefile(first_alias))
        initial_storage = storage_usage.inspect_storage(store, library.home)
        initial_base = next(item for item in initial_storage['categories'] if item['id'] == 'exports_base')
        self.assertEqual(initial_base['apparent_bytes'] - initial_base['actual_bytes'], first_base.stat().st_size)
        self.assertEqual(list(exports.glob('APK-资源集-*')), [])
        with self.assertRaisesRegex(ValueError, '尚未通过'):
            publish(store, delivery(2, verified=False), package, str(uuid.UUID(int=2)),
                    source=source, library=library)
        self.assertEqual(installed_program(store, package)['apk'], str(first_base))
        # Simulate cutting the delivery APK to a phone. The registered base is still usable.
        first_alias.unlink()
        after_handoff = storage_usage.inspect_storage(store, library.home)
        after_base = next(item for item in after_handoff['categories'] if item['id'] == 'exports_base')
        self.assertEqual(after_base['actual_bytes'], initial_base['actual_bytes'])
        self.assertEqual(after_base['apparent_bytes'], after_base['actual_bytes'])
        with patch('write_modes.source', return_value=({}, {'path': str(first_base)})) as read:
            self.assertEqual(latest_export(store, None)['path'], str(first_base))
            read.assert_called_once_with(str(first_base), None, resource_packages={})

        three = register(3)
        third_alias = Path(three['path'])
        other = register(10, 'fixture.coexist')
        self.assertTrue(third_alias.exists())
        resume = self.data / 'export-resume.json'
        resume.write_text(json.dumps({'request': {'apk': three['base_apk']}}), encoding='utf8')
        four = register(4)
        base, alias = Path(four['base_apk']), Path(four['path'])
        self.assertFalse(first_base.exists())
        self.assertTrue(Path(three['base_apk']).exists())
        self.assertTrue(third_alias.exists())
        self.assertTrue(Path(other['base_apk']).samefile(other['path']))
        self.assertEqual(installed_program(store, 'fixture.coexist')['apk'], other['base_apk'])
        preview = storage_usage.preview_cleanup(store, library.home, 'managed_retired')
        self.assertEqual(preview['targets'], [])
        resume.unlink()
        preview = storage_usage.preview_cleanup(store, library.home, 'managed_retired')
        self.assertEqual(preview['files'], 4)
        self.assertEqual(preview['freed_bytes'], sum((Path(three['base_apk']).with_suffix(suffix)).stat().st_size
                                                   for suffix in ('.apk', '.managed.json', '.report.json')))
        with self.assertRaisesRegex(ValueError, '预览'):
            storage_usage.execute_cleanup(self.server, 'managed_retired')
        # A new reference to the delivery hardlink protects the entire old base.
        resume.write_text(json.dumps({'request': {'apk': str(third_alias)}}), encoding='utf8')
        prune(store, library.home, four)
        self.assertTrue(third_alias.exists())
        self.assertTrue(Path(three['base_apk']).exists())
        with self.assertRaisesRegex(ValueError, '清理目标已变化'):
            storage_usage.execute_cleanup(self.server, 'managed_retired', confirmation=preview['confirmation'])
        resume.unlink()
        preview = storage_usage.preview_cleanup(store, library.home, 'managed_retired')
        result = storage_usage.execute_cleanup(self.server, 'managed_retired', confirmation=preview['confirmation'])
        self.assertEqual(result['deleted_files'], 4)
        self.assertFalse(Path(three['base_apk']).exists())
        self.assertFalse(third_alias.exists())
        self.assertEqual(set(exports.glob('*.apk')), {alias, Path(other['path'])})
        self.assertTrue(base.samefile(alias))
        self.assertEqual(installed_program(store, package)['apk'], str(base))
        result = workspace_tools.clear_exports(self.server)
        self.assertGreaterEqual(result['deleted'], 1)
        self.assertFalse(alias.exists())
        self.assertTrue(base.is_file())
        self.assertTrue(Path(other['base_apk']).is_file())

    def test_hardlink_accounting_and_protected_base_apk(self):
        exports = self.data / 'exports'
        bundle = exports / 'APK-资源集-20261122-000000-aaaaaaaa'
        bundle.mkdir(parents=True)
        apk = bundle / 'base.apk'
        apk.write_bytes(b'A' * 65536)
        (bundle / 'complete.json').write_text('{}', encoding='utf8')
        top_link = exports / 'base.apk'
        os.link(apk, top_link)

        rp_dir = self.data / 'runtime-programs'
        rp_dir.mkdir(parents=True)
        (rp_dir / 'fixture.game.json').write_text(
            json.dumps({'program_id': str(uuid.UUID(int=1)), 'package': 'fixture.game', 'apk': str(apk)}),
            encoding='utf8',
        )

        report = storage_usage.inspect_storage(self.server.store, self.home)
        by_id = {c['id']: c for c in report['categories']}
        self.assertIn('exports_base', by_id)
        base_cat = by_id['exports_base']
        self.assertTrue(base_cat['protected'])
        self.assertFalse(base_cat['can_clean'])
        self.assertEqual(base_cat['apparent_bytes'], 65536 * 2 + 2)
        self.assertEqual(base_cat['actual_bytes'], 65536 + 2)
        self.assertEqual(report['hardlink_saved_bytes'], 65536)

        with self.assertRaises(ValueError):
            storage_usage.preview_cleanup(self.server.store, self.home, 'exports_base')
        with self.assertRaises(ValueError):
            storage_usage.execute_cleanup(self.server, 'exports_base')

        cleared = workspace_tools.clear_exports(self.server)
        self.assertEqual(cleared['deleted'], 0)
        self.assertTrue(apk.is_file() and top_link.is_file())

    def test_keep_apks_retention_and_cleanup(self):
        exports = self.data / 'exports'
        exports.mkdir(parents=True)
        now = 1_700_000_000.0
        for i in range(4):
            bdir = exports / f'APK-资源集-20261122-00000{i}-1111111{i}'
            bdir.mkdir()
            apk = bdir / f'game-{i}.apk'
            apk.write_bytes(bytes([i + 1]) * 16384)
            (bdir / 'complete.json').write_text('{}', encoding='utf8')
            link = exports / f'game-{i}.apk'
            os.link(apk, link)
            mtime = now - (4 - i) * 100
            os.utime(apk, (mtime, mtime))
            os.utime(bdir / 'complete.json', (mtime, mtime))

        storage_usage.save_settings(self.server.store, {'keep_apks': 2})
        report = storage_usage.inspect_storage(self.server.store, self.home)
        hist = next(c for c in report['categories'] if c['id'] == 'exports_history')
        # 4 APKs * 16384 + 4 complete.json * 2
        self.assertEqual(hist['actual_bytes'], 4 * 16384 + 8)
        self.assertEqual(hist['reclaimable_bytes'], 2 * 16384 + 4)

        res = storage_usage.execute_cleanup(self.server, 'exports_history')
        self.assertEqual(res['freed_bytes'], 2 * 16384 + 4)
        self.assertFalse((exports / 'game-0.apk').exists())
        self.assertFalse((exports / 'game-1.apk').exists())
        self.assertTrue((exports / 'game-2.apk').exists())
        self.assertTrue((exports / 'game-3.apk').exists())

    def test_tmp_24h_boundary_and_resume_protection(self):
        tmp_dir = self.data / 'tmp'
        tmp_dir.mkdir(parents=True)
        now = 1_700_000_000.0

        fresh = tmp_dir / 'fresh-job'
        fresh.mkdir()
        (fresh / 'part.bin').write_bytes(b'1' * 1024)
        os.utime(fresh / 'part.bin', (now - 3600, now - 3600))
        os.utime(fresh, (now - 3600, now - 3600))

        stale = tmp_dir / 'stale-job'
        stale.mkdir()
        (stale / 'old.bin').write_bytes(b'2' * 2048)
        os.utime(stale / 'old.bin', (now - 90000, now - 90000))
        os.utime(stale, (now - 90000, now - 90000))

        paused = tmp_dir / 'paused-job'
        paused.mkdir()
        (paused / 'state.bin').write_bytes(b'3' * 4096)
        os.utime(paused / 'state.bin', (now - 90000, now - 90000))
        os.utime(paused, (now - 90000, now - 90000))
        (self.data / 'export-resume.json').write_text(
            json.dumps({'version': 1, 'preserved_work': str(paused), 'request': {}}),
            encoding='utf8',
        )

        report = storage_usage.inspect_storage(self.server.store, self.home, now=now)
        tmp_cat = next(c for c in report['categories'] if c['id'] == 'tmp')
        self.assertEqual(tmp_cat['reclaimable_bytes'], 2048)

        res = storage_usage.execute_cleanup(self.server, 'tmp', now=now)
        self.assertEqual(res['freed_bytes'], 2048)
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(paused.exists())

    def test_cleanup_preserves_nested_projects_and_resume_resources(self):
        exports = self.data / 'exports'
        nested = exports / 'archive'
        nested.mkdir(parents=True)
        project = nested / 'retained.EFPROJECT'
        selected_pack = exports / 'selected.73pack'
        resolved_pack = exports / 'resolved.73pack'
        disposable = exports / 'unused.txt'
        for path in (project, selected_pack, resolved_pack, disposable):
            path.write_bytes(b'fixture')
        (self.data / 'export-resume.json').write_text(json.dumps({'request': {
            'resource_files': [str(selected_pack)],
            'resource_packages': {'content_pack': str(resolved_pack)},
        }}), encoding='utf8')

        preview = storage_usage.preview_cleanup(self.server.store, self.home, 'exports_packs')
        self.assertEqual(preview['targets'], [disposable.name])
        result = storage_usage.execute_cleanup(self.server, 'exports_packs')
        self.assertEqual(result['deleted_files'], 1)
        self.assertFalse(disposable.exists())
        for path in (project, selected_pack, resolved_pack):
            self.assertTrue(path.is_file(), path)

    def test_expired_pack_preview_only_deletes_confirmed_incompatible_packs(self):
        from content_pack import remember_program

        exports = self.data / 'exports'
        exports.mkdir()
        base = exports / 'base.apk'
        base.write_bytes(b'fixture')
        current, old = (str(uuid.UUID(int=n)) for n in (1, 2))
        remember_program(self.server.store, 'fixture.game', current, base)
        for name, package, program in (
            ('current', 'fixture.game', current), ('expired', 'fixture.game', old),
            ('protected', 'fixture.game', old), ('other', 'other.game', old),
        ):
            write_pack(exports / (name + '.73pack'), package=package, program_id=program, files={})
        (exports / 'broken.73pack').write_bytes(b'invalid')
        (self.data / 'export-resume.json').write_text(json.dumps({'request': {
            'resource_files': [str(exports / 'protected.73pack')],
        }}), encoding='utf8')
        store = self.server.store
        category = 'exports_expired_packs'
        report = storage_usage.inspect_storage(store, self.home)
        expired = next(row for row in report['categories'] if row['id'] == category)
        self.assertEqual([row['name'] for row in expired['items']], ['expired.73pack'])
        self.assertEqual(storage_usage.preview_cleanup(store, self.home, 'exports_packs')['targets'], [])
        preview = storage_usage.preview_cleanup(store, self.home, category)
        self.assertEqual(preview['targets'], ['expired.73pack'])
        write_pack(exports / 'later.73pack', package='fixture.game', program_id=old, files={})
        with self.assertRaisesRegex(ValueError, '重新预览'):
            storage_usage.execute_cleanup(self.server, category, confirmation=preview['confirmation'])
        self.assertTrue((exports / 'expired.73pack').is_file())
        preview = storage_usage.preview_cleanup(store, self.home, category)
        cleaned = storage_usage.execute_cleanup(self.server, category, confirmation=preview['confirmation'])
        self.assertEqual(cleaned['deleted_files'], 2)
        for name in ('base.apk', 'current.73pack', 'other.73pack', 'protected.73pack', 'broken.73pack'):
            self.assertTrue((exports / name).is_file(), name)

    def test_export_review_is_opt_in_and_never_deletes(self):
        exports = self.data / 'exports'
        exports.mkdir()
        for name in ('old.apk', 'new.apk'):
            (exports / name).write_bytes(b'fixture')
        store = self.server.store
        self.assertIsNone(storage_usage.review_after_export(store, self.home))
        storage_usage.save_settings(store, {'keep_apks': 1, 'review_after_export': True})
        self.assertEqual(storage_usage.review_after_export(store, self.home)['files'], 1)
        self.assertEqual(len(list(exports.glob('*.apk'))), 2)

    def test_rebind_source_keeps_database_and_releases_old_protection(self):
        import apk_sources
        from content_pack import remember_program

        store = self.server.store
        library = self.server.library
        database = 'a' * 24
        library.resources = self.data / 'game-databases' / database
        exports = self.data / 'exports'
        exports.mkdir()
        old, latest = (exports / name for name in ('old.apk', 'latest.apk'))
        for path in (old, latest):
            path.write_bytes(b'fixture')
        active = self.data / 'active-game.json'
        original = {'database': database, 'apk': str(old), 'source_revision': 'before'}
        active.write_text(json.dumps(original), encoding='utf8')
        remember_program(store, 'fixture.game', str(uuid.UUID(int=1)), latest)

        def verified(path, key, current_library, *, expected):
            self.assertEqual(key, 'apk')
            self.assertIs(current_library, library)
            self.assertEqual(expected is None, path == old)
            return {'package': 'fixture.game', 'file': apk_sources.file_stamp(path)}

        with patch('apk_sources.verify_source', side_effect=verified), patch(
            'resource_source_bindings.verify', side_effect=ValueError('资源不配套'),
        ):
            with self.assertRaisesRegex(ValueError, '资源不配套'):
                apk_sources.rebind_exported_source(library, store, str(old))
        self.assertEqual(json.loads(active.read_text('utf8')), original)
        with patch('apk_sources.verify_source', side_effect=verified), patch(
            'resource_source_bindings.verify', return_value={'resource_dependencies': [], 'resource_set_id': None},
        ):
            result = apk_sources.rebind_exported_source(library, store, str(old))
        saved = json.loads(active.read_text('utf8'))
        self.assertEqual(saved['database'], database)
        self.assertNotEqual(saved['source_revision'], original['source_revision'])
        self.assertEqual(result, {'previous': str(old), 'apk': str(latest)})
        protected = storage_usage.protected_targets(store, self.home)
        self.assertNotIn(old, protected['paths'])
        self.assertIn(latest, protected['paths'])

    def test_browser_cache_preserves_local_storage_and_indexeddb(self):
        browser = self.data / 'browser'
        (browser / 'component_crx_cache').mkdir(parents=True)
        (browser / 'component_crx_cache' / 'crx.bin').write_bytes(b'c' * 5000)
        (browser / 'Default' / 'Cache').mkdir(parents=True)
        (browser / 'Default' / 'Cache' / 'c0').write_bytes(b'd' * 3000)
        (browser / 'Default' / 'Local Storage').mkdir(parents=True)
        ls = browser / 'Default' / 'Local Storage' / 'keep.ldb'
        ls.write_bytes(b'keep-ls')
        (browser / 'Default' / 'IndexedDB').mkdir(parents=True)
        idb = browser / 'Default' / 'IndexedDB' / 'keep.db'
        idb.write_bytes(b'keep-idb')

        res = storage_usage.execute_cleanup(self.server, 'browser_cache')
        self.assertEqual(res['freed_bytes'], 8000)
        self.assertFalse((browser / 'component_crx_cache' / 'crx.bin').exists())
        self.assertFalse((browser / 'Default' / 'Cache' / 'c0').exists())
        self.assertEqual(ls.read_bytes(), b'keep-ls')
        self.assertEqual(idb.read_bytes(), b'keep-idb')


class TestPlayerPacks(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)
        self.store = _DummyStore(self.work / 'studio-data')
        self.root = self.work / 'release'
        self.storage = self.work / '.content-packs'
        self.storage.mkdir(parents=True)
        self.root.mkdir(parents=True)

        self.package = 'fixture.game'
        self.program_id = str(uuid.UUID(int=42))
        self.engine_key = bytes(range(32))
        self.engine_sha = hashlib.sha256(self.engine_key).hexdigest()
        self.marker = {
            'protocol': '73-studio-program-v1',
            'package': self.package,
            'program_id': self.program_id,
            'game_version': 'V17',
            'player_updates': 1,
            'player_append': 1,
            'player_append_updates': 2,
            'player_append_engine': {'offset': 0, 'sha256': self.engine_sha},
        }
        (self.storage / 'program.json').write_text(json.dumps(self.marker), encoding='utf8')

        # Build minimal base CPK with one dummy stock player (person 10001) so tables are non-empty.
        stock_card = (1 << 40) | 10001
        stock_player = bytearray(400)
        struct.pack_into('<Q', stock_player, 8, stock_card)
        stock_player[31] = 80
        stock_appr = bytearray(64)
        struct.pack_into('<Q', stock_appr, 0, stock_card)
        stock_var = bytearray(168)
        struct.pack_into('<Q', stock_var, 0, stock_card)
        stock_boots = bytearray(16)
        struct.pack_into('<Q', stock_boots, 0, stock_card)
        stock_booster = bytearray(40)
        struct.pack_into('<H', stock_booster, 14, 1)

        self.base_cpk = _build_cpk({
            PLAYER: _dummy_wesys(bytes(stock_player)),
            'common/etc/pesdb/PlayerDeleteList.bin': _dummy_wesys(b''),
            APPEARANCE: _dummy_wesys(bytes(stock_appr)),
            VARIATION: _dummy_wesys(bytes(stock_var)),
            BOOTS: _dummy_wesys(bytes(stock_boots)),
            BOOSTERS: _dummy_wesys(bytes(stock_booster)),
            WEEKLY: _dummy_wesys(struct.pack('<QII', stock_card, 1, 2)),
            ALIASES: _dummy_wesys(b'before,after\r\n'),
        })

        # Build minimal IoStore container
        cid = 0x1122334455667788
        hdr_chunk = (struct.pack('<Q', cid) + b'\0\0\0\x0a', package_header(cid, []))
        self.base_utoc = self.work / 'base.utoc'
        write_container(self.base_utoc, [hdr_chunk], {}, cid)
        self.base_container = Container(self.base_utoc, self.engine_key)

        self.template = {
            'version_file': {'version': 2026102500, 'dt870FileSize': len(self.base_cpk), 'dt870Hash': hashlib.sha256(self.base_cpk).hexdigest()},
            'livedata_list': [{'name': 'dt870_mobile_and.cpk', 'size': len(self.base_cpk)}],
        }
        self.base_doc = {
            'players': [],
            'native_binding': {'routes': [], 'profile': {'native_hooks': [], 'boosters': []}},
            'editor_registries': {'boosters': {}},
            'preview_boosters': {},
            'write_designs': {'players': {}},
            'card_series_by_id': {},
            'resource_files': {},
        }
        self.release_manifest = {'base_release_id': '20261024-test', 'release_id': '20261024-test', 'files': []}

        # Write installed files
        for rel, content in (
            (ACTIVE, self.base_cpk),
            (LIVE, self.base_cpk),
            (TEMPLATE, compact(self.template)),
            (MANIFEST, compact(self.base_doc)),
            (RELEASE, compact(self.release_manifest)),
        ):
            dest = self.root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
        cont_dest = self.root / (CONTAINER + '.utoc')
        cont_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.base_utoc, cont_dest)
        shutil.copyfile(self.base_utoc.with_suffix('.ucas'), cont_dest.with_suffix('.ucas'))

        self.apk = self.work / 'base.apk'
        with zipfile.ZipFile(self.apk, 'x') as zf:
            zf.writestr(MARKER, json.dumps(self.marker))
            zf.writestr('lib/arm64-v8a/libUE4.so', self.engine_key)
            zf.writestr(PREFIX + ACTIVE, self.base_cpk)

    def _build_player_state(self, uid: str, person: int, art_key: int, name: str, version_tag: int, *,
                            art_byte: int = 1, movies=()):
        card = (1 << 40) | person
        definition = {
            'identity': {
                'package_id': uid, 'person_id': person, 'card_id': card,
                'previous_card_ids': [card], 'independent_person_id': person,
                'bound_original_player_id': None, 'bound_person_ids': [],
            },
            'owned': {
                '_personal_player_uuid': uid,
                'gameplayer_id': {'id': card},
                'name': name,
                'version_tag': version_tag,
            },
            'base_parameters': [0] * 147,
        }
        routes = [
            {'cardId': k, 'portraitKey': str(art_key), 'modelFaceKey': str(art_key)}
            for k in sorted(ids_for(definition, art_key))
        ]
        if movies:
            for route in routes:
                route.update(front_names=['', movies[0], '', '', '', '', '', '', '', ''],
                             thumbnail_names=['', movies[1], '', '', '', ''])
        doc = copy.deepcopy(self.base_doc)
        doc['players'].append(definition)
        doc['native_binding']['routes'].extend(routes)
        doc['write_designs']['players'][uid] = f'design-{version_tag}'

        # Build after CPK
        cpk_obj = Cpk(self.base_cpk)
        from native_cpk import unwrap
        all_ids = sorted(ids_for(definition, art_key))
        p_rows = bytearray(unwrap(cpk_obj.read(PLAYER)))
        a_rows = bytearray(unwrap(cpk_obj.read(APPEARANCE)))
        v_rows = bytearray(unwrap(cpk_obj.read(VARIATION)))
        b_rows = bytearray(unwrap(cpk_obj.read(BOOTS)))
        w_rows = bytearray(unwrap(cpk_obj.read(WEEKLY)))
        al_rows = bytearray(unwrap(cpk_obj.read(ALIASES)))
        for seq_idx, k in enumerate(all_ids, 2):
            row_p = bytearray(400)
            struct.pack_into('<Q', row_p, 8, k)
            row_p[31] = min(120, 75 + version_tag)
            p_rows.extend(row_p)
            row_a = bytearray(64)
            struct.pack_into('<Q', row_a, 0, k)
            row_a[8] = version_tag & 255
            a_rows.extend(row_a)
            row_v = bytearray(168)
            struct.pack_into('<Q', row_v, 0, k)
            v_rows.extend(row_v)
            row_b = bytearray(16)
            struct.pack_into('<Q', row_b, 0, k)
            b_rows.extend(row_b)
            w_rows.extend(struct.pack('<QII', k, seq_idx, 2))
            al_rows.extend(f'{k},p{k}\r\n'.encode('utf8'))

        after_cpk = Cpk(self.base_cpk).replace({
            PLAYER: _dummy_wesys(bytes(p_rows)),
            APPEARANCE: _dummy_wesys(bytes(a_rows)),
            VARIATION: _dummy_wesys(bytes(v_rows)),
            BOOTS: _dummy_wesys(bytes(b_rows)),
            WEEKLY: _dummy_wesys(bytes(w_rows)),
            ALIASES: _dummy_wesys(bytes(al_rows)),
        })

        # Build after Container
        rel_path = f'PesMobile/Content/Assets/ui/Data/Thumbnail/Player/{art_key}_test.uasset'
        pkg_name = f'/Game/Assets/ui/Data/Thumbnail/Player/{art_key}_test'
        pid = package_id(pkg_name)
        chunk_id = struct.pack('<Q', pid) + b'\0\0\0\x02'
        chunk_raw = bytes([art_byte]) * 64
        chunks = [(chunk_id, chunk_raw)]
        paths = {rel_path: 0}
        packages = [{'id': pid, 'exports': 1, 'bundles': 1, 'imports': []}]
        if movies:
            from player_movie_overlay import required_art
            for path in required_art(movies):
                pid = package_id('/Game/' + path.removeprefix('PesMobile/Content/').removesuffix('.uasset'))
                paths[path] = len(chunks)
                chunks.append((struct.pack('<Q', pid) + b'\0\0\0\x02', path.encode()))
                packages.append({'id': pid, 'exports': 1, 'bundles': 1, 'imports': []})
        out_utoc = self.work / f'art-{uid}-{version_tag}-{uuid.uuid4().hex[:6]}.utoc'
        after_container = append_container(self.base_container, out_utoc, chunks, paths, packages)
        return doc, after_cpk, after_container

    def _export(self, filename, doc, after_cpk, after_container, uid, *, marker=None, use_store=True,
                target_game='V17', movie_files=None):
        dest = self.work / filename
        write_append(
            dest, marker or self.marker, self.base_cpk, after_cpk,
            after_container, doc, [uid],
            inherited=self.base_doc if (marker or self.marker).get('player_append_updates') == 2 else None,
            store=self.store if use_store else None,
            target_game=target_game,
            studio_version='3.0.16', movie_files=movie_files,
        )
        return dest

    def _install(self, pack_path):
        shutil.copyfile(pack_path, self.storage / 'pending.73pack')
        return prepare(self.root, base_apk=self.apk)

    def test_precheck_rejections_target_game_base_and_disk_space(self):
        uid = str(uuid.UUID(int=101))
        doc, cpk, cont = self._build_player_state(uid, 91000001, 8100001, 'Tester A', 1)

        # 1. target_game mismatch
        wrong_game_pack = self._export('wrong-game.73pack', doc, cpk, cont, uid, target_game='V32-r2')
        res = self._install(wrong_game_pack)
        self.assertEqual(res['status'], 'ready')
        self.assertIn('V32-r2', res['detail'])
        self.assertEqual(json.loads((self.root / MANIFEST).read_bytes())['players'], [])
        report = json.loads((self.storage / 'last-import-result.json').read_text('utf8'))
        self.assertEqual(report['status'], 'rejected')
        self.assertIn('V32-r2', report['detail'])

        # 2. base engine mismatch
        mismatched_marker = dict(self.marker, player_append_engine={'offset': 0, 'sha256': '0' * 64})
        wrong_base_pack = self._export('wrong-base.73pack', doc, cpk, cont, uid, marker=mismatched_marker, use_store=False)
        res = self._install(wrong_base_pack)
        self.assertEqual(res['status'], 'ready')
        self.assertIn('基准', res['detail'])
        self.assertEqual(json.loads((self.root / MANIFEST).read_bytes())['players'], [])

        # 3. disk space insufficient
        good_pack = self._export('good.73pack', doc, cpk, cont, uid)
        with patch('content_runtime.shutil.disk_usage', return_value=type('U', (), {'total': 1024**3, 'used': 1024**3 - 128, 'free': 128})()):
            res = self._install(good_pack)
        self.assertEqual(res['status'], 'ready')
        self.assertIn('空间不足', res['detail'])
        self.assertTrue((self.storage / 'pending.73pack').is_file())
        self.assertEqual(json.loads((self.root / MANIFEST).read_bytes())['players'], [])

    def test_six_version_update_cases_receipt_and_inspector(self):
        uid = str(uuid.UUID(int=201))

        # Case 1: New player (revision 1) -> added
        doc1, cpk1, cont1 = self._build_player_state(uid, 92000001, 8200001, 'Star Player', 1, art_byte=11)
        pack_v1 = self._export('star-v1.73pack', doc1, cpk1, cont1, uid)
        insp = pack_inspector.inspect_pack(pack_v1)
        self.assertTrue(insp['valid'])
        self.assertEqual(insp['kind'], 'independent_player')
        self.assertEqual(insp['target_game'], 'V17')
        self.assertEqual(insp['players'][0]['revision'], 1)

        res1 = self._install(pack_v1)
        self.assertEqual(res1['status'], 'applied')
        self.assertEqual(res1['files'], 7)
        self.assertEqual(res1['import_report']['players'][0]['status'], 'added')

        # Case 3: Re-export unchanged -> reuses revision 1 & same content_hash, but new outer pack_id -> skipped (0 writes)
        pack_v1_repeat = self._export('star-v1-repeat.73pack', doc1, cpk1, cont1, uid)
        insp_repeat = pack_inspector.inspect_pack(pack_v1_repeat)
        self.assertEqual(insp_repeat['players'][0]['revision'], 1)
        res_skip = self._install(pack_v1_repeat)
        self.assertEqual(res_skip['status'], 'current')
        self.assertEqual(res_skip['files'], 0)
        self.assertEqual(res_skip['import_report']['status'], 'skipped')
        self.assertEqual(res_skip['import_report']['players'][0]['status'], 'skipped')
        self.assertFalse((self.storage / 'pending.73pack').exists())
        self.assertEqual((self.storage / 'active.73pack').read_bytes(), pack_v1.read_bytes())
        self.assertEqual(self._install(pack_v1)['import_report']['status'], 'skipped')
        self.assertFalse((self.storage / 'pending.73pack').exists())

        # Case 2: Edit design -> allocates revision 2 -> updated, preserves card_id
        doc2, cpk2, cont2 = self._build_player_state(uid, 92000001, 8200001, 'Star Player V2', 2, art_byte=22)
        pack_v2 = self._export('star-v2.73pack', doc2, cpk2, cont2, uid)
        insp2 = pack_inspector.inspect_pack(pack_v2)
        self.assertEqual(insp2['players'][0]['revision'], 2)
        res2 = self._install(pack_v2)
        self.assertEqual(res2['status'], 'applied')
        self.assertEqual(res2['files'], 7)
        self.assertEqual(res2['import_report']['players'][0]['status'], 'updated')
        installed_doc = json.loads((self.root / MANIFEST).read_bytes())
        self.assertEqual(installed_doc['players'][0]['owned']['name'], 'Star Player V2')
        self.assertEqual(installed_doc['player_revisions'][uid]['revision'], 2)

        # Case 5: Downgrade with pack_v1 (revision 1 < installed 2) -> rejected, design stays V2
        res_down = self._install(pack_v1)
        self.assertEqual(res_down['status'], 'current')
        self.assertIn('旧小包不能覆盖新设计', res_down['detail'])
        self.assertEqual(json.loads((self.root / MANIFEST).read_bytes())['players'][0]['owned']['name'], 'Star Player V2')

        # Case 4: Same revision 2 with different content_hash -> rejected (conflict)
        conflict_zip = self.work / 'conflict.zip'
        with PackReader(pack_v2).open(MEMBER) as st, zipfile.ZipFile(io.BytesIO(st.read())) as orig:
            with zipfile.ZipFile(conflict_zip, 'x') as out_zip:
                desc = json.loads(orig.read('manifest.json'))
                desc['players'][0]['content_hash'] = 'f' * 64
                out_zip.writestr('manifest.json', compact(desc))
                for name in orig.namelist():
                    if name != 'manifest.json':
                        out_zip.writestr(name, orig.read(name))
        conflict_pack = self.work / 'conflict.73pack'
        write_pack(conflict_pack, package=self.package, program_id=self.program_id, files={MEMBER: conflict_zip})
        res_conflict = self._install(conflict_pack)
        self.assertEqual(res_conflict['status'], 'current')
        self.assertIn('版本冲突', res_conflict['detail'])

        # Case 6: Legacy v1 pack (no revision) on a new player -> added, repeat -> skipped
        uid_leg = str(uuid.UUID(int=202))
        doc_l, cpk_l, cont_l = self._build_player_state(uid_leg, 92000002, 8200002, 'Legacy Player', 1, art_byte=33)
        legacy_marker = {k: v for k, v in self.marker.items() if k != 'player_append_updates'}
        pack_leg_1 = self._export('legacy-1.73pack', doc_l, cpk_l, cont_l, uid_leg, marker=legacy_marker, use_store=False)
        res_leg_1 = self._install(pack_leg_1)
        self.assertEqual(res_leg_1['status'], 'applied')
        self.assertEqual(res_leg_1['files'], 7)
        pack_leg_2 = self._export('legacy-2.73pack', doc_l, cpk_l, cont_l, uid_leg, marker=legacy_marker, use_store=False)
        res_leg_2 = self._install(pack_leg_2)
        self.assertEqual(res_leg_2['status'], 'current')
        self.assertEqual(res_leg_2['files'], 0)

        # Verify history & corrupt pack inspection
        history = json.loads((self.storage / 'import-history.json').read_text('utf8'))
        self.assertGreaterEqual(len(history), 5)
        corrupt = self.work / 'bad.73pack'
        corrupt.write_bytes(b'not-a-73pack')
        bad_info = pack_inspector.inspect_pack(corrupt)
        self.assertFalse(bad_info['valid'])

    def test_identical_design_with_new_revision_blocks_older_revision(self):
        uid = str(uuid.UUID(int=251))
        original = self._build_player_state(uid, 92000001, 8200001, 'Design A', 1, art_byte=11)
        alternate = self._build_player_state(uid, 92000001, 8200001, 'Design B', 2, art_byte=22)
        v1 = self._export('a-v1.73pack', *original, uid)
        v2 = self._export('b-v2.73pack', *alternate, uid)
        v3 = self._export('a-v3.73pack', *original, uid)
        self.assertEqual(pack_inspector.inspect_pack(v3)['players'][0]['revision'], 3)

        self.assertEqual(self._install(v1)['status'], 'applied')
        result = self._install(v3)
        self.assertEqual(result['import_report']['players'][0]['status'], 'skipped')
        self.assertEqual(result['files'], 0)
        self.assertEqual((self.storage / 'active.73pack').read_bytes(), v1.read_bytes())
        self.assertFalse((self.storage / 'pending.73pack').exists())
        persisted = (self.root / MANIFEST).read_bytes()
        self.assertEqual(json.loads(persisted)['player_revisions'][uid]['revision'], 3)
        self.assertIn('旧小包不能覆盖新设计', self._install(v2)['detail'])
        self.assertEqual((self.root / MANIFEST).read_bytes(), persisted)
        self.assertFalse((self.storage / 'pending.73pack').exists())
        self.assertEqual(list(self.storage.glob('rejected-*.73pack')), [])

    def test_unversioned_snapshot_cannot_overwrite_versioned_player(self):
        uid = str(uuid.UUID(int=252))
        original = self._build_player_state(uid, 92000001, 8200001, 'Design A', 1, art_byte=11)
        alternate = self._build_player_state(uid, 92000001, 8200001, 'Design B', 2, art_byte=22)
        old = self._export('unversioned.73pack', *original, uid, use_store=False)
        new = self._export('versioned.73pack', *alternate, uid)

        self.assertEqual(self._install(old)['status'], 'applied')
        self.assertEqual(self._install(new)['status'], 'applied')
        persisted = (self.root / MANIFEST).read_bytes()
        result = self._install(old)
        self.assertEqual(result['import_report']['status'], 'rejected')
        self.assertIn('缺少设计版本号', result['detail'])
        self.assertEqual((self.root / MANIFEST).read_bytes(), persisted)

    def test_mixed_multiplayer_skip_and_add(self):
        self._mixed_multiplayer_add(edit_existing=False)

    def test_mixed_multiplayer_update_and_add(self):
        self._mixed_multiplayer_add(edit_existing=True)

    def test_dynamic_multiplayer_update_and_add(self):
        self._mixed_multiplayer_add(edit_existing=True, dynamic=True)

    def _mixed_multiplayer_add(self, *, edit_existing, dynamic=False):
        import player_movie_overlay as motion
        movies_a, movies_b, replaced_a = (), (), ()
        movie_files = None
        if dynamic:
            movies_a = ('c11111111111', 'c22222222222')
            replaced_a = ('c33333333333', movies_a[1])
            movies_b = ('c44444444444', movies_a[1])
            movie_files = {motion.movie_path(name): b'CRID' + name.encode()
                           for name in set(movies_a + replaced_a + movies_b)}
            pak = self.root / motion.PAK
            pak.parent.mkdir(parents=True, exist_ok=True)
            motion.write_movie_pak(pak, {})
            self.template['version_file']['dt880'] = [{
                'name': motion.SLOT, 'hash': hashlib.sha256(pak.read_bytes()).hexdigest(),
                'hashFileSize': pak.stat().st_size,
                'ucas': '0' * 64, 'ucasFileSize': 0, 'utoc': '0' * 64, 'utocFileSize': 0,
            }]
            for suffix in ('.pak', '.utoc', '.ucas'):
                self.template['livedata_list'].append({'name': motion.SLOT + '_mobile_and' + suffix, 'size': 0})
            (self.root / TEMPLATE).write_bytes(compact(self.template))
        uid_a = str(uuid.UUID(int=301))
        uid_b = str(uuid.UUID(int=302))
        doc_a, cpk_a, cont_a = self._build_player_state(
            uid_a, 93000001, 8300001, 'Player A', 1, art_byte=41, movies=movies_a)
        if dynamic:
            experimental = {**self.marker, motion.CAPABILITY: 1}
            pack_a = self._export('only-a.73pack', doc_a, cpk_a, cont_a, uid_a,
                                  marker=experimental, movie_files=movie_files)
            self.assertEqual(self._install(pack_a)['import_report']['status'], 'rejected')
            self.marker = experimental
            self.apk.unlink()
            with zipfile.ZipFile(self.apk, 'x') as zf:
                zf.writestr(MARKER, json.dumps(self.marker))
                zf.writestr('lib/arm64-v8a/libUE4.so', self.engine_key)
                zf.writestr(PREFIX + ACTIVE, self.base_cpk)
        else:
            pack_a = self._export('only-a.73pack', doc_a, cpk_a, cont_a, uid_a)
        res_a = self._install(pack_a)
        self.assertEqual(res_a['status'], 'applied')
        self.assertEqual(res_a['files'], 8 if dynamic else 7)

        if edit_existing:
            doc_a, cpk_a, cont_a = self._build_player_state(
                uid_a, 93000001, 8300001, 'Player A Updated', 2, art_byte=43, movies=replaced_a)
        doc_b, cpk_b, cont_b = self._build_player_state(
            uid_b, 93000002, 8300002, 'Player B', 1, art_byte=42, movies=movies_b)
        doc_ab = copy.deepcopy(doc_a)
        doc_ab['players'].extend(doc_b['players'])
        doc_ab['native_binding']['routes'].extend(doc_b['native_binding']['routes'])
        doc_ab['write_designs']['players'].update(doc_b['write_designs']['players'])

        from native_cpk import unwrap
        cpk_a_obj, cpk_b_obj = Cpk(cpk_a), Cpk(cpk_b)
        def merge_bin(table, width):
            ra, rb = unwrap(cpk_a_obj.read(table)), unwrap(cpk_b_obj.read(table))
            base_len = len(unwrap(Cpk(self.base_cpk).read(table)))
            return _dummy_wesys(ra + rb[base_len:])
        base_weekly_len = len(unwrap(Cpk(self.base_cpk).read(WEEKLY)))
        wa, wb = unwrap(cpk_a_obj.read(WEEKLY)), unwrap(cpk_b_obj.read(WEEKLY))
        base_alias_len = len(unwrap(Cpk(self.base_cpk).read(ALIASES)))
        aa, ab = unwrap(cpk_a_obj.read(ALIASES)), unwrap(cpk_b_obj.read(ALIASES))
        cpk_ab = Cpk(self.base_cpk).replace({
            PLAYER: merge_bin(PLAYER, 400),
            APPEARANCE: merge_bin(APPEARANCE, 64),
            VARIATION: merge_bin(VARIATION, 168),
            BOOTS: merge_bin(BOOTS, 16),
            WEEKLY: _dummy_wesys(wa + wb[base_weekly_len:]),
            ALIASES: _dummy_wesys(aa + ab[base_alias_len:]),
        })
        from native_iostore import read_package_header
        hdr_idx = next(i for i, c in enumerate(cont_b.chunk_ids) if c[11] == 10)
        _, b_pkgs = read_package_header(cont_b.read_chunk(hdr_idx))
        b_indices = [i for i, chunk in enumerate(cont_b.chunk_ids)
                     if chunk[11] != 10 and chunk not in cont_a.by_id]
        positions = {index: pos for pos, index in enumerate(b_indices)}
        b_additions = [(cont_b.chunk_ids[i], cont_b.read_chunk(i)) for i in b_indices]
        b_paths = {p.removeprefix('../../../'): positions[i] for p, i in cont_b.paths.items() if i in positions}
        b_pkgs = [row for row in b_pkgs if any(row['id'] == struct.unpack_from('<Q', chunk)[0]
                                                for chunk, _ in b_additions)]
        cont_ab = append_container(cont_a, self.work / f'ab-{uuid.uuid4().hex[:6]}.utoc', b_additions, b_paths, b_pkgs)

        dest = self.work / 'ab.73pack'
        write_append(dest, self.marker, self.base_cpk, cpk_ab, cont_ab, doc_ab, [uid_a, uid_b],
                     inherited=self.base_doc, store=self.store, target_game='V17', studio_version='3.0.16',
                     movie_files=movie_files)
        res_ab = self._install(dest)
        self.assertEqual(res_ab['status'], 'applied')
        self.assertEqual(res_ab['files'], 8 if dynamic else 7)
        statuses = {p['package_id']: p['status'] for p in res_ab['import_report']['players']}
        expected = 'updated' if edit_existing else 'skipped'
        self.assertEqual(statuses, {uid_a: expected, uid_b: 'added'})
        saved = json.loads((self.root / MANIFEST).read_bytes())
        self.assertEqual(saved['players'], doc_ab['players'])
        if dynamic:
            from native_movie_pak_source import Pak

            def check_movies(names):
                installed = Pak(pak, self.engine_key)
                expected = {motion.MOUNT + motion.movie_path(name): movie_files[motion.movie_path(name)]
                            for name in names}
                self.assertEqual({path: installed.read(path) for path in installed.files}, expected)
                metadata = json.loads((self.root / TEMPLATE).read_bytes())['version_file']['dt880'][0]
                self.assertEqual(metadata['hash'], hashlib.sha256(pak.read_bytes()).hexdigest())

            check_movies(replaced_a + movies_b)
            repeated = self.work / 'ab-repeat.73pack'
            write_append(repeated, self.marker, self.base_cpk, cpk_ab, cont_ab, doc_ab, [uid_a, uid_b],
                         inherited=self.base_doc, store=self.store, movie_files=movie_files)
            self.assertEqual(self._install(repeated)['import_report']['status'], 'skipped')
            self.assertFalse((self.storage / 'pending.73pack').exists())
            self.assertEqual((self.storage / 'active.73pack').read_bytes(), dest.read_bytes())
            self.assertEqual(res_ab['import_report']['movie_playback'], 'unverified')
            static = self._build_player_state(uid_a, 93000001, 8300001, 'Player A Static', 3, art_byte=44)
            self.assertEqual(self._install(self._export(
                'a-static.73pack', *static, uid_a, movie_files={},
            ))['import_report']['status'], 'applied')
            check_movies(movies_b)

            updated_b = self._build_player_state(
                uid_b, 93000002, 8300002, 'Player B Updated', 2, art_byte=45, movies=movies_b)
            next_pack = self._export('b-update.73pack', *updated_b, uid_b, movie_files=movie_files)
            invalid = self.work / 'invalid-movies.zip'
            with PackReader(next_pack).open(MEMBER) as stream, zipfile.ZipFile(io.BytesIO(stream.read())) as src:
                with zipfile.ZipFile(invalid, 'x') as target:
                    for name in src.namelist():
                        target.writestr(name, b'CRIDbad' if name == 'movie/' + movies_b[0] else src.read(name))
            bad_pack = self.work / 'invalid-movies.73pack'
            write_pack(bad_pack, package=self.package, program_id=self.program_id, files={MEMBER: invalid})
            current_files = [self.root / name for name in (MANIFEST, TEMPLATE, motion.PAK)]
            expected = [path.read_bytes() for path in current_files]
            rejected = self._install(bad_pack)['import_report']
            self.assertEqual(rejected['status'], 'rejected')
            self.assertIn('视频校验失败', rejected['detail'])
            self.assertEqual([path.read_bytes() for path in current_files], expected)
            self.assertFalse((self.storage / 'pending.73pack').exists())


if __name__ == '__main__':
    unittest.main()
