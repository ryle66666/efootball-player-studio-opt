from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Windows'))
from updates import DEFAULT_REPOSITORY, UpdateChecker, release_result
from update_package import FORMAT, JOURNAL, PORTABLE_REQUIRED, REQUIRED, install_staged, stage_package

REPO_URL = 'https://github.com/' + DEFAULT_REPOSITORY + '/releases/'
TAG = 'v3.0.17'


def make_package(path, extra=()):
    with zipfile.ZipFile(path, 'w') as package:
        package.writestr('studio-update.json', json.dumps({'format': FORMAT, 'version': TAG[1:]}))
        for relative in sorted(REQUIRED):
            package.writestr('Windows/' + relative, 'APP_VERSION = "3.0.17"\n' if relative == 'app.py' else 'test')
        for name, data in extra:
            package.writestr(name, data)


def release(path=None):
    asset = {'name': 'studio-windows-update.zip', 'browser_download_url': REPO_URL + 'download/' + TAG + '/studio.zip'}
    if path is not None:
        content = path.read_bytes()
        asset.update(size=len(content), digest='sha256:' + hashlib.sha256(content).hexdigest())
    return {'tag_name': TAG, 'html_url': REPO_URL + 'tag/' + TAG, 'assets': [asset]}


class OnlineUpdatesTest(unittest.TestCase):
    def test_migrates_old_default_without_changing_auto_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / 'update-preferences.json'
            file.write_text(json.dumps({'repository': 'light23335/efootball-player-studio', 'enabled': False}))
            checker = UpdateChecker(SimpleNamespace(root=root), root, '3.0.16')
            self.assertEqual(checker.settings(), {'repository': DEFAULT_REPOSITORY, 'enabled': False})
            self.assertEqual(json.loads(file.read_text())['repository'], DEFAULT_REPOSITORY)
            checker.settings({'repository': 'my-own/fork', 'enabled': True})
            self.assertEqual(checker.settings()['repository'], 'my-own/fork')

    def test_selects_only_update_asset_for_in_app_install(self):
        data = release()
        digest = 'sha256:' + 'a' * 64
        data['assets'][0]['digest'] = digest
        data['assets'].insert(0, {'name': 'other-portable.zip', 'digest': digest,
                                  'browser_download_url': REPO_URL + 'download/' + TAG + '/portable.zip'})
        result = release_result(data, '3.0.16', DEFAULT_REPOSITORY, 'pc')
        self.assertEqual(result['sha256'], 'a' * 64)
        self.assertEqual(result['asset'], 'studio-windows-update.zip')
        data['assets'].pop()
        self.assertNotIn('sha256', release_result(data, '3.0.16', DEFAULT_REPOSITORY, 'pc'))
        data['prerelease'] = True
        self.assertEqual(release_result(data, '3.0.16', DEFAULT_REPOSITORY, 'pc')['phase'], 'current')

    def test_download_stages_only_when_digest_is_correct(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'release.zip'
            make_package(archive)
            checker = UpdateChecker(SimpleNamespace(root=root), root, '3.0.16', install_supported=True)
            selected = release_result(release(archive), '3.0.16', DEFAULT_REPOSITORY, 'pc')

            @contextmanager
            def response(*args, **kwargs):
                data = io.BytesIO(archive.read_bytes())
                data.geturl = lambda: 'https://release-assets.githubusercontent.com/test.zip'
                yield data

            with patch('update_package.urllib.request.urlopen', response):
                with self.assertLogs(level='ERROR'):
                    checker._download({**selected, 'sha256': '0' * 64})
                self.assertEqual(checker.status()['phase'], 'available')
                self.assertEqual(list((root / 'software-updates').iterdir()), [])
                checker._download(selected)
            work = checker.claim_install()
            self.assertEqual(checker.status()['phase'], 'installing')
            self.assertTrue((work / 'stage' / 'app.py').is_file())

    def test_rejects_non_program_files_and_mismatched_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, name in enumerate(('Windows/data/signing/key', 'Windows/web/../escape', 'Windows/tools/CON.txt',
                                          'Windows/web/Bad:stream', 'Windows/APP.py')):
                with self.subTest(name=name):
                    archive = root / f'bad-{index}.zip'
                    make_package(archive, [(name, 'bad')])
                    with self.assertRaises(ValueError):
                        stage_package(archive, root / 'stage', TAG[1:])
                    self.assertFalse((root / 'stage').exists())
            archive = root / 'good.zip'
            make_package(archive)
            with self.assertRaises(ValueError):
                stage_package(archive, root / 'stage', '3.0.18')

    def test_portable_package_stages_and_preserves_private_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / 'studio-update-12345678'
            work.mkdir()
            home = root / 'Portable'
            private = home / 'data' / 'project.json'
            private.parent.mkdir(parents=True)
            private.write_text('private')
            archive = work / 'release.zip'
            with zipfile.ZipFile(archive, 'w') as package:
                package.writestr('studio-update.json', json.dumps(
                    {'format': FORMAT, 'version': '1.0.1', 'layout': 'portable'}))
                for relative in PORTABLE_REQUIRED:
                    package.writestr('Portable/' + relative,
                                     '{"version": "1.0.1"}' if relative == 'studio-version.json' else 'fixture')
                package.writestr('Portable/README.md', 'first install guide')
            stage_package(archive, work / 'stage', '1.0.1', portable=True)
            install_staged(work, home, portable=True)
            self.assertTrue((home / 'PlayerStudioOpt.exe').is_file())
            self.assertEqual(private.read_text(), 'private')

    def test_replacement_rolls_back_and_keeps_user_data(self):
        from update_package import _replace_file

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / 'studio-update-12345678'
            work.mkdir()
            home = root / 'Windows'
            (home / 'data').mkdir(parents=True)
            (home / 'data' / 'project.json').write_text('saved player')
            (home / 'app.py').write_text('APP_VERSION = "3.0.16"')
            (home / 'updates.py').write_text('old')
            archive = work / 'release.zip'
            make_package(archive)
            stage_package(archive, work / 'stage', TAG[1:])

            def fail_on_last_file(source, target):
                if target.name == 'app.py' and source.is_relative_to(work / 'stage'):
                    raise PermissionError('Locked')
                return _replace_file(source, target)

            with patch('update_package._replace_file', side_effect=fail_on_last_file):
                with self.assertRaises(PermissionError):
                    install_staged(work, home)
            self.assertFalse((home / JOURNAL).exists())
            self.assertIn('3.0.16', (home / 'app.py').read_text())
            self.assertEqual((home / 'updates.py').read_text(), 'old')
            self.assertEqual((home / 'data' / 'project.json').read_text(), 'saved player')
