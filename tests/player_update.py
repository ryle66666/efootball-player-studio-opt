"""Exercise the workshop and game readers against the same compact updates."""
import json
from pathlib import Path
import random
import shutil
import sys
import tempfile
import unittest
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Windows'))

from content_pack import PREFIX, write
from content_runtime import prepare
from player_append import MANIFEST
from player_update_export import write_update
from program_content import MARKER
from resource_set import Reader


class PlayerUpdates(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.root = self.folder / 'release'
        self.name = 'custom-resources/tables.cpk'
        self.alias = 'livedata/tables.cpk'
        self.base = random.Random(4).randbytes(128 * 1024)
        self.program = self.folder / 'base.apk'
        self.identity = {'package': 'fixture.game', 'program_id': str(uuid.UUID(int=1))}
        marker = {**self.identity, 'protocol': '73-studio-program-v1', 'player_updates': 1}
        with zipfile.ZipFile(self.program, 'x') as archive:
            archive.writestr(MARKER, json.dumps(marker))
            archive.writestr(PREFIX + self.name, self.base)
        self.storage = self.folder / '.content-packs'
        self.storage.mkdir()
        (self.storage / 'program.json').write_text(json.dumps(marker), 'utf8')

    def build(self, filename, files, parent=None):
        path = self.folder / filename
        manifest = write_update(path, base_path=self.program, **self.identity,
                                files=files, removed=[], parent=parent)
        return path, manifest['pack_id']

    def install(self, path):
        shutil.copyfile(path, self.storage / 'pending.73pack')
        return prepare(self.root, base_apk=self.program)

    def test_two_updates_share_base_and_reject_an_earlier_update(self):
        first = b'first update\0' + self.base[13:]
        second = b'next update\0\0' + self.base[13:]
        pack1, first_id = self.build('first.73pack', {self.name: first, self.alias: first})
        pack2, _ = self.build('second.73pack', {self.name: second, self.alias: self.base}, first_id)
        self.assertLess(pack1.stat().st_size, len(first) // 4)
        self.assertEqual(self.install(pack1)['status'], 'applied')
        self.assertEqual((self.root / self.name).read_bytes(), first)
        with Reader(self.program, {'content_pack': str(pack1)}) as overlay:
            self.assertEqual(overlay.read(PREFIX + self.alias), first)
            restored = overlay.file_payload(PREFIX + self.name)
        with restored.open() as stream:
            self.assertEqual(stream.read(), first)
        self.assertEqual(self.install(pack2)['status'], 'applied')
        self.assertEqual((self.root / self.alias).read_bytes(), self.base)
        self.assertEqual(prepare(self.root, base_apk=self.program)['status'], 'current')
        self.assertIn('warning', self.install(pack1))
        self.assertEqual((self.root / self.name).read_bytes(), second)

    def test_full_file_content_pack_remains_usable(self):
        path = self.folder / 'legacy.73pack'
        write(path, **self.identity, files={self.name: b'ordinary v1 content'})
        self.assertEqual(self.install(path)['status'], 'applied')
        self.assertEqual((self.root / self.name).read_bytes(), b'ordinary v1 content')

    def test_wrong_base_bytes_preserve_the_last_installed_update(self):
        first = b'first update\0' + self.base[13:]
        pack1, first_id = self.build('first.73pack', {self.name: first})
        pack2, _ = self.build('second.73pack', {self.name: self.base}, first_id)
        self.assertEqual(self.install(pack1)['status'], 'applied')
        altered = self.folder / 'wrong.apk'
        with zipfile.ZipFile(self.program) as base, zipfile.ZipFile(altered, 'x') as archive:
            archive.writestr(MARKER, base.read(MARKER))
            archive.writestr(PREFIX + self.name, b'x' + self.base[1:])
        shutil.copyfile(pack2, self.storage / 'pending.73pack')
        self.assertIn('warning', prepare(self.root, base_apk=altered))
        self.assertEqual((self.root / self.name).read_bytes(), first)

    def test_interrupted_player_publish_restores_current_files(self):
        current = self.root/MANIFEST
        current.parent.mkdir(parents=True)
        current.write_bytes(b'uncommitted replacement')
        work = self.storage/('apply-'+uuid.uuid4().hex)
        backup = work/'previous'/MANIFEST
        backup.parent.mkdir(parents=True)
        backup.write_bytes(b'original')
        (work/'player-publish.json').write_text(json.dumps({
            'program_id': self.identity['program_id'], 'names': [MANIFEST], 'receipt': False, 'existed': [True],
        }), 'utf8')
        self.assertEqual(prepare(self.root, base_apk=self.program)['status'], 'ready')
        self.assertEqual(current.read_bytes(), b'original')
        self.assertFalse(work.exists())


if __name__ == '__main__':
    unittest.main()
