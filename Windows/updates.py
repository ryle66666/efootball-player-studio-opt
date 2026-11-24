"""Optional GitHub Release checks and verified Windows update downloads."""

import copy
import json
import logging
from pathlib import Path
import re
import tempfile
import threading
import urllib.error
import urllib.request

from update_package import ASSET_SUFFIX, atomic_json, download_package, remove_work, stage_package


DEFAULT_REPOSITORY = 'ryle66666/efootball-player-studio-opt'
PREVIOUS_REPOSITORIES = {'light23335/efootball-player-studio', 'ryle66666/efootball-player-studio'}
BUSY = {'checking', 'downloading', 'preparing', 'installing'}
NATIVE_TRANSPORT = None


def native_fetch(url, agent):
    if NATIVE_TRANSPORT:
        return NATIVE_TRANSPORT(url, agent)
    from native_https import fetch
    return fetch(url, agent)


def repository(value):
    value = str(value or '').strip().removeprefix('https://github.com/').removesuffix('/').removesuffix('.git')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}', value):
        raise ValueError('请输入 GitHub 用户名/仓库名，或完整仓库地址')
    return value


def version(value):
    match = re.fullmatch(r'(?:V1[67]-)?v?(\d+\.\d+(?:\.\d+){0,2})(?:test)?', str(value), re.IGNORECASE)
    if match:
        parts = tuple(map(int, match[1].split('.')))
        return parts + (0,) * (4 - len(parts))


def release_result(data, current, repo, platform):
    if data.get('draft') or data.get('prerelease'):
        return {'phase': 'current', 'message': '当前没有新的正式版本'}
    tag = data.get('tag_name', '')
    number = version(tag)
    if number is None or number <= version(current):
        return {'phase': 'current', 'message': '当前已是最新正式版本'}
    prefix = f'https://github.com/{repo}/releases/'
    page = data.get('html_url', '')
    if page.casefold() != (prefix + 'tag/' + tag).casefold():
        raise ValueError('Release 地址与配置仓库不一致')
    assets = [
        asset for asset in data.get('assets', [])
        if str(asset.get('browser_download_url', '')).casefold().startswith((prefix + 'download/' + tag + '/').casefold())
        and 'V15' not in str(asset.get('name', '')).upper()
    ]
    update = next((asset for asset in assets if platform == 'pc' and asset.get('name', '').lower().endswith(ASSET_SUFFIX)), None)
    manual = next((asset for asset in assets if
                   (platform == 'pc' and asset.get('name', '').lower().endswith('.zip')
                    and ('免安装' in asset.get('name', '') or 'portable' in asset.get('name', '').lower()))
                   or (platform == 'android' and asset.get('name', '').lower().endswith('.apk'))), None)
    selected = update or manual
    result = {'phase': 'available', 'version': tag, 'message': '发现可选更新 ' + tag,
              'notes': str(data.get('body') or '')[:8000], 'release_url': page, 'repository': repo,
              'download_url': selected['browser_download_url'] if selected else page,
              'asset': selected['name'] if selected else None, 'mandatory': False}
    if update:
        digest = str(update.get('digest') or '')
        size = update.get('size')
        if re.fullmatch(r'sha256:[0-9a-fA-F]{64}', digest):
            result['sha256'] = digest[7:].lower()
        if type(size) is int and size > 0:
            result['download_total'] = size
    return result


class UpdateChecker:
    def __init__(self, store, resources, current, platform='pc', install_supported=False, portable=False):
        self.store = store
        self.resources = Path(resources)
        self.current = current
        self.platform = platform
        self.install_supported = install_supported
        self.portable = portable
        self.lock = threading.RLock()
        self.state = {'phase': 'idle'}
        self.dismissed = set()

    def settings(self, body=None):
        path = self.store.root / 'update-preferences.json'
        defaults = self.resources / 'github-updates.json'
        with self.lock:
            value = json.loads(defaults.read_text('utf8')) if defaults.is_file() else {
                'repository': DEFAULT_REPOSITORY, 'enabled': True}
            if path.is_file():
                value.update(json.loads(path.read_text('utf8')))
            if value.get('repository', '').casefold() in PREVIOUS_REPOSITORIES:
                value['repository'] = DEFAULT_REPOSITORY
                atomic_json(path, value)
            if body is None:
                return value
            if type(body.get('enabled')) is not bool:
                raise ValueError('自动更新提醒开关无效')
            repo = repository(body['repository'])
            if repo.casefold() in PREVIOUS_REPOSITORIES:
                repo = DEFAULT_REPOSITORY
            if repo.casefold() != value['repository'].casefold():
                if self.state['phase'] in BUSY - {'checking'}:
                    raise ValueError('请等待当前更新任务结束')
                self.state = {'phase': 'idle'}
            value = {'repository': repo, 'enabled': body['enabled']}
            atomic_json(path, value)
            return value

    def status(self):
        with self.lock:
            state = {key: copy.deepcopy(value) for key, value in self.state.items() if key != 'work'}
            return dict(state, settings=self.settings(), current=self.current,
                        can_install=bool(self.install_supported and state.get('sha256')),
                        prompt=state['phase'] == 'available'
                            and (state.get('repository'), state.get('version')) not in self.dismissed)

    def dismiss(self):
        with self.lock:
            self.dismissed.add((self.state.get('repository'), self.state.get('version')))
        return self.status()

    def start(self, manual=False):
        with self.lock:
            settings = self.settings()
            if not manual and (not settings.get('enabled') or self.state['phase'] != 'idle'):
                return self.status()
            if self.state['phase'] in BUSY or self.state['phase'] == 'ready':
                return self.status()
            repo = repository(settings['repository'])
            check = {'phase': 'checking', 'message': '正在检查 GitHub 正式版本…'}
            self.state = check
        threading.Thread(target=self._check, args=(repo, check), daemon=True, name='studio-update-check').start()
        return self.status()

    def _check(self, repo, check=None):
        try:
            agent = 'PlayerStudio/' + self.current
            request = urllib.request.Request('https://api.github.com/repos/' + repo + '/releases/latest',
                headers={'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': agent})
            transport = 'python-https'
            try:
                with urllib.request.urlopen(request, timeout=8) as response:
                    raw = response.read(256 * 1024 + 1)
            except urllib.error.HTTPError:
                raise
            except OSError:
                raw = native_fetch(request.full_url, agent)
                transport = 'system-https'
            if len(raw) > 256 * 1024:
                raise ValueError('更新说明超过大小限制')
            result = dict(release_result(json.loads(raw), self.current, repo, self.platform), transport=transport)
        except urllib.error.HTTPError as exc:
            result = ({'phase': 'current', 'message': '仓库还没有正式 Release；当前版本可以继续使用'} if exc.code == 404
                      else {'phase': 'offline', 'message': '暂时无法检查更新；不影响继续编辑', 'detail': f'GitHub HTTP {exc.code}'})
        except Exception as exc:
            logging.exception('GitHub update request failed')
            result = {'phase': 'offline', 'message': '暂时无法检查更新；不影响继续编辑', 'detail': str(exc)[:300]}
        with self.lock:
            if check is None or self.state is check:
                self.state = result

    def link(self):
        with self.lock:
            target = self.state.get('download_url') or self.state.get('release_url')
            prefix = 'https://github.com/' + repository(self.settings()['repository']) + '/releases/'
            if not target or not target.casefold().startswith(prefix.casefold()):
                raise ValueError('请先检查更新')
            return target

    def download(self):
        with self.lock:
            if not self.install_supported or self.state['phase'] != 'available' or not self.state.get('sha256'):
                raise ValueError('当前 Release 没有可在工坊内安装的更新包')
            self.link()
            selected = self.state.copy()
            self.state = {**selected, 'phase': 'downloading', 'message': '正在下载更新…', 'downloaded': 0}
        threading.Thread(target=self._download, args=(selected,), daemon=True, name='studio-update-download').start()
        return self.status()

    def _download(self, selected):
        work = None
        try:
            folder = self.store.root / 'software-updates'
            folder.mkdir(exist_ok=True)
            if folder.is_symlink() or folder.is_junction():
                raise ValueError('更新暂存目录包含链接')
            work = Path(tempfile.mkdtemp(prefix='studio-update-', dir=folder))
            package = work / 'package.zip'

            def progress(size):
                with self.lock:
                    self.state['downloaded'] = size

            download_package(selected['download_url'], package, selected['sha256'], progress, selected.get('download_total'))
            with self.lock:
                self.state.update(phase='preparing', message='正在准备更新…')
            match = re.search(r'\d+(?:\.\d+){1,3}', selected['version'])
            stage_package(package, work / 'stage', match[0], portable=self.portable)
            package.unlink()
            with self.lock:
                self.state = {**selected, 'phase': 'ready', 'message': '下载完成，可以重启更新', 'work': work}
        except Exception as exc:
            logging.exception('Update download failed')
            if work is not None:
                try:
                    remove_work(work, folder)
                except (OSError, ValueError):
                    logging.warning('Could not remove failed update staging directory')
            with self.lock:
                self.dismissed.add((selected.get('repository'), selected.get('version')))
                self.state = {**selected, 'phase': 'available', 'message': '更新准备失败：' + str(exc)[:300]}

    def claim_install(self):
        with self.lock:
            if not self.install_supported or self.state['phase'] != 'ready':
                raise ValueError('请先下载更新')
            self.state.update(phase='installing', message='正在重启工坊…')
            return self.state['work']

    def release_install(self, error):
        with self.lock:
            self.state.update(phase='ready', message=str(error))

    def is_installing(self):
        with self.lock:
            return self.state['phase'] == 'installing'
