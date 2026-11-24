"""Regression and performance checks for the Windows studio."""
import argparse
import gzip
import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import re
import statistics
import struct
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid
import zipfile
import zlib

import psutil


def formats(windows, local, record):
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(windows))
    import apk_format
    import app
    import content_pack
    from native_cpk import unwrap, wesys_crypt, wrap
    from native_tables import PLAYER, rows
    from overall import calculate
    from tournament_simulation import RATING_KEYS, simulate_match

    ratings = dict(zip(RATING_KEYS, (82, 85, 80, 78, 72)))
    pure = {
        'rating': calculate(app.default_project(), app.CATALOG),
        'match': simulate_match(ratings, dict.fromkeys(RATING_KEYS, 79),
                                root_seed=721, match_id='final'),
    }
    row_bytes = b''.join(struct.pack('<Q8s', i, b'player') for i in range(1, 6))
    assert b''.join(rows(row_bytes, 16).values()) == row_bytes
    original = b'original player table'
    compressed = zlib.compress(original, 1)
    wrapper = struct.pack('<BBB5sII', 255, 0x21, 0x83, b'WESYS',
                          len(compressed), len(original))
    wrapper += wesys_crypt(compressed, 1, len(compressed), len(original))
    player_bin = wrap(row_bytes, wrapper)
    assert unwrap(player_bin) == row_bytes and wrap(row_bytes, player_bin) == player_bin

    with tempfile.TemporaryDirectory(dir=local) as directory:
        root = Path(directory)
        source, unsigned, signed = (root / name for name in ('source.apk', 'raw.apk', 'signed.apk'))
        with zipfile.ZipFile(source, 'w') as apk:
            apk.writestr(zipfile.ZipInfo(PLAYER), wrapper)
            apk.writestr(zipfile.ZipInfo('assets/keep.bin'), b'keep')
        apk_format.replace_entries(source, unsigned, {PLAYER: player_bin})
        key, cert = apk_format.local_signer(local / 'test-signing')
        apk_format.sign_v2(unsigned, signed, key, cert)
        apk_format.verify_v2(signed)
        with zipfile.ZipFile(signed) as apk:
            assert apk.read(PLAYER) == player_bin and apk.read('assets/keep.bin') == b'keep'

        pack, rewritten = root / 'sample.73pack', root / 'rewritten.73pack'
        program_id = str(uuid.UUID(int=9))
        pack_path = 'custom-resources/' + PLAYER
        with patch('content_pack.uuid.uuid4', return_value=uuid.UUID(int=7)):
            content_pack.write(pack, package='fixture.game', program_id=program_id,
                               files={pack_path: player_bin})
            with content_pack.Reader(pack).open(pack_path) as stream:
                decoded = stream.read()
            content_pack.write(rewritten, package='fixture.game', program_id=program_id,
                               files={pack_path: decoded})
        assert pack.read_bytes() == rewritten.read_bytes()
        signed_hash = hashlib.sha256(signed.read_bytes()).hexdigest()
        signature_reference = local / 'signed-baseline.sha256'
        if record:
            signature_reference.write_text(signed_hash, encoding='ascii')
        elif signature_reference.exists():
            assert signed_hash == signature_reference.read_text(encoding='ascii')
        return dict(pure=pure, formats={
            'Player.bin': hashlib.sha256(player_bin).hexdigest(),
            'APK (before signing)': hashlib.sha256(unsigned.read_bytes()).hexdigest(),
            '.73pack': hashlib.sha256(pack.read_bytes()).hexdigest(),
        })


def request(server, path, headers=None):
    connection = HTTPConnection('127.0.0.1', server['port'], timeout=90)
    token = urlsplit(server['url']).query.partition('=')[2]
    try:
        connection.request('GET', path, headers={'X-Studio-Token': token, **(headers or {})})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def check_static(server, data):
    status, headers, home = request(server, '/')
    assert status == 200 and 'HttpOnly' in headers['Set-Cookie']
    status, reply, raw = request(server, '/', {'If-None-Match': headers['ETag']})
    assert status == 304 and raw == b'' and 'Set-Cookie' in reply
    static = re.search(rb'src="(/dist/[^"]+\.js)"', home)[1].decode()
    _, plain, body = request(server, static, {'Accept-Encoding': 'gzip;q=0'})
    status, zipped, compressed = request(server, static, {'Accept-Encoding': 'gzip'})
    assert status == 200 and zipped['Content-Encoding'] == 'gzip'
    assert gzip.decompress(compressed) == body and plain['ETag'] != zipped['ETag']
    assert 'immutable' in zipped['Cache-Control'] and 'Accept-Encoding' in zipped['Vary']
    assert request(server, static, {'If-None-Match': zipped['ETag'],
                                    'Accept-Encoding': 'gzip'})[0] == 304
    _, plain, body = request(server, '/api/bootstrap', {'Accept-Encoding': 'gzip;q=0,*;q=1'})
    status, zipped, compressed = request(server, '/api/bootstrap', {'Accept-Encoding': 'gzip'})
    assert status == 200 and zipped['Content-Encoding'] == 'gzip'
    assert 'Content-Encoding' not in plain and zipped['Cache-Control'] == 'no-store'
    assert gzip.decompress(compressed) == body
    for amount in (262_145, 9 * 1024 * 1024):
        name = 'a' * 64 + '.glb'
        body = b'a' * amount
        (data / 'assets' / name).write_bytes(body)
        status, headers, raw = request(server, '/assets/' + name)
        assert status == 200 and raw == body and headers['Cache-Control'] == 'no-store'


def server_run(windows, local, label, *, browser=None, static=False, profile=False, features=False):
    with tempfile.TemporaryDirectory(dir=local) as directory:
        data = Path(directory)
        env = dict(os.environ, PYTHONPYCACHEPREFIX=str(local / 'pycache' / label), PYTHONUTF8='1')
        args = [sys.executable, *(('-X', 'importtime') if profile else ())]
        args.extend(('app.py', '--no-browser', '--no-idle-exit', '--data-dir', str(data)))
        started = time.perf_counter()
        with (local / f'{label}-{"importtime" if profile else "stderr"}.txt').open('w') as errors:
            proc = subprocess.Popen(args, cwd=windows, env=env, stdout=subprocess.PIPE,
                                    stderr=errors, text=True)
            try:
                line = proc.stdout.readline()
                if not line:
                    raise RuntimeError(f'工坊启动失败，详见 {errors.name}')
                server = json.loads(line)
                assert request(server, '/')[0] == 200
                startup = (time.perf_counter() - started) * 1000
                process = psutil.Process(server['pid'])
                time.sleep(.15)
                idle = process.memory_info().rss
                if profile:
                    return None
                snapshot = {}
                for route in ('/health', '/api/ping', '/api/bootstrap'):
                    status, _, raw = request(server, route)
                    assert status == 200, (route, status)
                    value = json.loads(raw)
                    if route == '/api/bootstrap':
                        value['data_directory'] = '<data>'
                        value['library']['cache_directory'] = '<cache>'
                        value['project']['package_id'] = '<project>'
                    snapshot[route] = value
                if static:
                    check_static(server, data)
                pages = smoke(server, browser, local / label, process, data, features) if browser else {}
                return startup, idle, snapshot, pages
            finally:
                for child in psutil.Process(proc.pid).children(recursive=True):
                    try:
                        child.terminate()
                        child.wait(timeout=10)
                    except psutil.NoSuchProcess:
                        pass
                proc.terminate()
                proc.wait(timeout=15)
                proc.stdout.close()


def smoke(server, browser, output, process, data, features):
    output.mkdir(exist_ok=True)
    triangle = b'v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n'
    name = hashlib.sha256(triangle).hexdigest() + '.obj'
    (data / 'assets' / name).write_bytes(triangle)
    with browser.new_context(viewport={'width': 1450, 'height': 950}) as context:
        page = context.new_page()
        errors, bad, counts = [], [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('response', lambda response: bad.append(response.url) if response.status == 404 else None)
        page.on('request', lambda req: counts.append(req.url))
        page.add_init_script("sessionStorage.setItem('studio-disclaimer','1')")

        def wait_for(expression):
            stop = time.monotonic() + 20
            while not page.evaluate(expression):
                if time.monotonic() >= stop:
                    raise AssertionError((expression, errors, bad,
                                          page.locator('#editor-content').inner_text()[:300]))
                page.wait_for_timeout(50)

        before = time.perf_counter()
        page.goto(server['url'] + '&effects=full', wait_until='load')
        wait_for('Boolean(window.__studio && document.body.dataset.workspacePage === "home")')
        page.wait_for_load_state('networkidle')
        cdp = context.new_cdp_session(page)
        cdp.send('HeapProfiler.collectGarbage')
        entry = page.evaluate("""() => {
            let items=[performance.getEntriesByType('navigation')[0],...performance.getEntriesByType('resource')];
            return {requests:items.length, transfer_bytes:items.reduce((n,i)=>n+i.transferSize,0),
                    load_ms:items[0].loadEventEnd};
        }""")
        entry.update(home_ready_ms=(time.perf_counter()-before)*1000,
                     backend_home_rss=process.memory_info().rss,
                     js_heap_home=cdp.send('Runtime.getHeapUsage')['usedSize'],
                     canvas_3d_home=page.locator('.model-stage canvas').count(),
                     three_home_requests=sum('/vendor/' in url or 'model-viewer' in url for url in counts))
        page.screenshot(path=output / 'home.png', animations='disabled')
        page.evaluate("(name)=>{window.__studio.project.editor_ready=true; window.__studio.project.model.asset=name}", name)
        glb = []
        for route in ('tutorial', 'tools', 'personal', 'stadium', 'tournaments', 'match-feel', 'packs',
                      'motion', 'coaches', 'library', 'profile', 'abilities', 'training',
                      'skills', 'card', 'model', 'native', 'apk'):
            if route == 'match-feel':
                dock_item = page.locator('#section-tabs').get_by_role('button', name='比赛手感', exact=True)
                dock_item.click()
                page.locator('#section-tabs [aria-current="page"][aria-label="比赛手感"]').wait_for()
                page.evaluate("window.__studio.navigate('home')")
                page.locator('#shell-menu-button').click()
                page.locator('#navigation > [data-page="match-feel"]').click()
            else:
                page.evaluate('(route)=>window.__studio.navigate(route)', route)
            wait_for(f'document.body.dataset.workspacePage === "{route}"')
            if route == 'match-feel':
                original = page.locator('[data-feel-mode="original"]')
                assert original.get_attribute('aria-pressed') == 'true'
                page.locator('[data-feel-mode="custom"]').click()
                threshold = page.locator('#match-feel-form [name="spin_threshold"]')
                frame = page.locator('[data-feel-field="trap_cancel_frame"]')
                assert frame.locator('[data-feel-reference="original"]').inner_text() == '8'
                assert frame.locator('[data-feel-reference="workshop"]').inner_text() == '6'
                assert frame.locator('input').input_value() == '8'
                assert float(threshold.input_value()) == 0.70
                frame.locator('input').fill('9')
                threshold.fill('0.85')
                page.locator('#match-feel-form button[type="submit"]').click()
                page.get_by_text('比赛手感已保存', exact=True).wait_for()
                page.evaluate("window.__studio.navigate('home');window.__studio.navigate('match-feel')")
                assert float(threshold.input_value()) == 0.85
                assert frame.locator('input').input_value() == '9'
                page.locator('[data-feel-reset]').click()
                page.get_by_text('比赛手感已保存', exact=True).wait_for()
                assert frame.locator('input').input_value() == '8'
                page.locator('[data-feel-mode="workshop"]').click()
                page.locator('[data-feel-mode="workshop"][aria-pressed="true"]').wait_for()
                original.click()
                page.locator('[data-feel-mode="original"][aria-pressed="true"]').wait_for()
            if route == 'model':
                wait_for('window.__studio.viewer?.subject?.userData.asset')
                wait_for('window.__studio.viewer.framesRendered > 2')
                glb = page.evaluate("""async () => {
                    const result = await window.__studio.viewer.exportGLB();
                    return Array.from(new Uint8Array(result instanceof Blob ? await result.arrayBuffer() : result));
                }""")
                assert glb[:4] == [103, 108, 84, 70]
            page.wait_for_load_state('networkidle')
            if route in ('card', 'model', 'tournaments', 'native'):
                page.screenshot(path=output / (route + '.png'), animations='disabled')
        cdp.send('HeapProfiler.collectGarbage')
        entry.update(js_heap_after_pages=cdp.send('Runtime.getHeapUsage')['usedSize'],
                     backend_after_pages_rss=process.memory_info().rss)
        if features:
            check_preview_lifecycle(page, context, data, server, glb, wait_for, output)
            check_player_content_scope(page)
        assert not errors and not bad, (errors, bad)
        return entry


def check_preview_lifecycle(page, context, data, server, glb, wait_for, output):
    from PIL import Image

    source = data / 'fixture.glb'
    source.write_bytes(bytes(glb))
    model_id = page.evaluate("""async path => {
        const response = await fetch('/api/assets/import', {method: 'POST',
            headers: {'Content-Type':'application/json'}, body:JSON.stringify({path,category:'model'})});
        if (!response.ok) throw Error(await response.text());
        return (await response.json()).id;
    }""", str(source))
    page.evaluate("""id => {
        const studio=window.__studio;
        studio.project.model.asset=id;
        studio.viewer.previewApi=null;
        studio.navigate('model');
    }""", model_id)
    wait_for(f'window.__studio.viewer?.subject?.userData.asset === "{model_id}"')
    page.evaluate("""() => {
        const studio=window.__studio;
        studio.viewer.setOption('wireframe',true);
        studio.navigate('home');
    }""")
    assert page.evaluate('window.__studio.viewer.frame === null')
    wait_for('window.__studio.viewer === null')
    page.evaluate("window.__studio.navigate('model')")
    wait_for('window.__studio.viewer?.options.wireframe === true')
    wait_for(f'window.__studio.viewer?.subject?.userData.asset === "{model_id}"')
    shared = page.evaluate("""async id => {
        const studio=window.__studio, viewer=studio.viewer, url='/assets/'+id;
        viewer.previewApi=async()=>({export_url:url,raw_url:url,reference_url:url,anchors:{}});
        viewer.loadedKey=null;
        await viewer.update(studio.project);
        const tools=viewer.headTools;
        viewer.setHeadTest({raw:true,reference:true,jaw:.5,blink:.3});
        const reference = [];
        tools.reference.traverse(node=>{if(node.isMesh)reference.push(node.material.wireframe);});
        const result = await viewer.exportGLB();
        const header = new Uint8Array(result instanceof Blob ? await result.arrayBuffer() : result, 0, 4);
        return {same:tools.actual===tools.raw, shown:tools.actual.visible,
                reference:tools.reference.visible&&reference.length>0&&reference.every(Boolean),
                snapshot:viewer.snapshot().startsWith('data:image/png;base64,'),
                exportHeader:Array.from(header)};
    }""", model_id)
    assert shared == {'same': True, 'shown': True, 'reference': True, 'snapshot': True,
                      'exportHeader': [103, 108, 84, 70]}

    colors = [(170, 25, 40), (50, 160, 80)]
    images = []
    for color in colors:
        image = data / 'fixture.png'
        with Image.new('RGB', (12, 12), color) as sample:
            sample.save(image)
        name = hashlib.sha256(image.read_bytes()).hexdigest() + '.png'
        image.replace(data / 'assets' / name)
        images.append(name)
    result = page.evaluate("""async ids => {
        const studio=window.__studio, renderer=studio.nativeCard;
        const project=id=>({...studio.project,appearance:{hide_card_ui:true},native_card:{
            layout:{width:600,height:860},fonts:{},layers:[{slot:'f01',kind:'image',asset_id:id}]}});
        const pixels=()=>Array.from(renderer.canvas.getContext('2d').getImageData(0,0,1,1).data);
        await renderer.update(project(ids[0]),studio.simulation);
        const previous=renderer.resources[0].element, first=pixels();
        await renderer.update(project(ids[1]),studio.simulation);
        const result={first,second:pixels(),reclaimed:!previous.hasAttribute('src'),cached:renderer.media.size};
        renderer.clear();
        return {...result,empty:renderer.media.size===0};
    }""", images)
    assert result == {'first': [*colors[0], 255], 'second': [*colors[1], 255],
                      'reclaimed': True, 'cached': 1, 'empty': True}
    page.evaluate("""id => {
        window.__studio.project.appearance.portrait=id;
        window.__studio.navigate('card');
    }""", images[0])
    with page.expect_download() as started:
        page.locator('[data-action="export-card"]').click()
    with Image.open(started.value.path()) as picture:
        assert picture.format == 'PNG' and picture.size == (1200, 1720)
    page.evaluate("window.__studio.navigate('tournaments')")
    reload = page.locator('[data-world-reload]')
    reload.wait_for()
    # The browser starts with empty data. Supply a minimal catalogue after
    # exercising the actual missing-source response and its explicit retry.
    page.route('**/api/world/load', lambda route: route.fulfill(json={
        'document': {'tournaments': []}, 'teams': [], 'leagues': [],
    }))
    reload.click()
    page.locator('[data-world-new]').click()
    page.locator('[data-world-field$=".name"]').first.fill('回归测试赛事')
    page.locator('[data-world-stage="knockout"]').click()
    assert page.locator('.world-stage').count() == 1
    page.locator('[data-world-back]').click()
    assert page.get_by_role('heading', name='回归测试赛事').count() == 1
    low = context.new_page()
    low.goto(server['url']+'&effects=low', wait_until='load')
    assert low.evaluate("document.documentElement.dataset.effects") == 'low'
    assert low.evaluate("getComputedStyle(document.querySelector('dialog')).backdropFilter") == 'none'
    low.screenshot(path=output / 'home-low.png', animations='disabled')
    low.close()



def check_player_content_scope(page):
    main_apk = 'player-update-baseline.apk'
    player_id = page.evaluate('window.__studio.project.package_id')
    source_only_id, coach, pack, tournament, saved_id = (str(uuid.UUID(int=i)) for i in range(91, 96))
    page.route('**/api/native-apk/sources', lambda route: route.fulfill(json={
        'recorded': True, 'source_key': 'player-content-test', 'apk': main_apk,
    }))
    page.route('**/api/native-apk/write-sources', lambda route: route.fulfill(json={
        'rows': [], 'latest': {'path': main_apk},
    }))
    page.route('**/api/native-apk/candidates', lambda route: route.fulfill(json={
        'write_source': {'token': 'fixture', 'players': 1, 'coaches': 1, 'packs': 1},
        'rows': [
            {'package_id': player_id, 'name': '修改球员', 'current': True, 'write_state': 'modified'},
            {'package_id': saved_id, 'name': '修改球员', 'write_state': 'new'},
            {'package_id': source_only_id, 'name': '来源包已有球员', 'source_only': True,
             'write_state': 'source_only'},
        ],
        'coaches': [{'uuid': coach, 'name': '默认勾选教练', 'write_state': 'modified'}],
        'packs': [{'uuid': pack, 'name': '默认勾选卡包', 'member_count': 1, 'write_state': 'modified'}],
        'tournaments': [{'uuid': tournament, 'name': '默认勾选赛事', 'teams': 2}],
    }))
    page.route('**/api/native-apk/start', lambda route: route.fulfill(json={'phase': 'idle'}))
    page.evaluate("window.__studio.navigate('native')")
    small_export = page.locator('[data-native-player-content]')
    page.locator('[data-native-member]').first.wait_for()
    page.locator('[data-write-mode][value="incremental"]').click()
    selected_player = page.locator(f'[data-native-member="{player_id}"]')
    selected_player.wait_for()
    assert selected_player.is_checked()
    search = page.locator('#native-player-query')
    search.fill(player_id)
    page.locator('[data-native-none]').click()
    assert not selected_player.is_checked()
    page.get_by_role('button', name='全选搜索结果').click()
    assert selected_player.is_checked()
    search.fill('')
    assert [el.get_attribute('data-native-member') for el in page.locator('[data-native-member]:checked').all()] == [player_id]
    page.locator('[data-native-all]').click()
    page.locator(f'[data-native-member="{saved_id}"]').uncheck()
    optional = [page.locator('[data-native-' + item + ']') for item in ('coach', 'pack', 'tournament')]
    assert all(choice.is_checked() for choice in optional)
    with page.expect_request(lambda request: request.url.endswith('/api/native-apk/start')) as posted:
        small_export.click()
    request = posted.value.post_data_json
    assert request['operation'] == 'player_content'
    assert request['selected_ids'] == [player_id]
    assert all(request[key] == [] for key in ('source_only_ids', 'coach_ids', 'pack_ids', 'tournament_ids'))
    assert all(choice.is_checked() for choice in optional)
    with page.expect_request(lambda request: request.url.endswith('/api/native-apk/start')) as posted:
        page.locator('[data-native-build]').click()
    request = posted.value.post_data_json
    assert request['coach_ids'] == [coach] and request['pack_ids'] == [pack]
    assert request['tournament_ids'] == [tournament] and request['source_only_ids'] == [source_only_id]
    selected_player.uncheck()
    small_export.click()
    page.get_by_text('请在上方勾选至少一名新增或已修改的球员。', exact=True).first.wait_for()

    page.route('**/api/players/list', lambda route: route.fulfill(json={
        'rows': [{'package_id': saved_id, 'name': '修改球员', 'card_type': 'Epic', 'level': 1,
                  'max_level': 2, 'overall': {'base': 80, 'trained': 82, 'before_coach': 82}}],
        'total': 1, 'errors': [],
    }))
    page.route('**/api/dialog', lambda route: route.fulfill(json={'path': 'applied-player-update.73pack'}))
    page.locator('[data-native-content-pick]').click()
    page.get_by_text('applied-player-update.73pack', exact=True).wait_for()
    page.locator('[data-write-mode][value="full"]').click()
    page.evaluate("window.__studio.navigate('personal')")
    page.locator(f'[data-personal-game-export="{saved_id}"]').click()
    page.locator(f'[data-native-member="{saved_id}"]:checked').wait_for()
    assert page.locator('[data-write-mode][value="incremental"]').is_checked()
    assert page.locator('[data-native-member]').count() == 1
    with page.expect_request(lambda request: request.url.endswith('/api/native-apk/start')) as posted:
        small_export.click()
    request = posted.value.post_data_json
    assert request['operation'] == 'player_content' and request['selected_ids'] == [saved_id]
    assert request['apk'] == main_apk and request['resource_files'] == ['applied-player-update.73pack']
    assert all(request[key] == [] for key in ('source_only_ids', 'coach_ids', 'pack_ids', 'tournament_ids'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('windows', type=Path)
    parser.add_argument('label')
    parser.add_argument('--browser-deps', type=Path)
    parser.add_argument('--edge', type=Path)
    parser.add_argument('--samples', type=int, default=3)
    parser.add_argument('--record', action='store_true')
    parser.add_argument('--static', action='store_true')
    parser.add_argument('--features', action='store_true')
    args = parser.parse_args()
    local = Path(__file__).resolve().parent / 'local'
    local.mkdir(exist_ok=True)
    windows = args.windows.resolve()
    snapshot_file = Path(__file__).with_name('snapshot.json.gz')
    results = [server_run(windows, local, args.label, static=args.static)
               for _ in range(args.samples)]
    server_run(windows, local, args.label, profile=True)
    startup, idle = (statistics.median(row[i] for row in results) for i in range(2))
    metrics = {'startup_ms': startup, 'idle_rss_bytes': idle,
               'startup_samples_ms': [row[0] for row in results]}
    if args.edge:
        if args.browser_deps:
            sys.path.insert(0, str(args.browser_deps))
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=str(args.edge), args=[
                '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
            *_, browser_metrics = server_run(windows, local, args.label, browser=browser,
                                             static=args.static, features=args.features)
            metrics.update(browser_metrics)
    snapshot = dict(http=results[0][2], **formats(windows, local, args.record))
    snapshot = json.loads(json.dumps(snapshot, ensure_ascii=False))
    if args.record:
        snapshot_file.write_bytes(gzip.compress(
            json.dumps(snapshot, ensure_ascii=False, indent=2).encode('utf8'), mtime=0))
    else:
        baseline = json.loads(gzip.decompress(snapshot_file.read_bytes()))
        for group, value in snapshot.items():
            if value != baseline[group]:
                (local / (args.label+'-snapshot.json')).write_text(
                    json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf8')
                raise AssertionError(f'{group} differs from baseline')
    files = [p for p in (windows / 'web').rglob('*') if p.is_file()]
    metrics.update(web_files=len(files), web_bytes=sum(p.stat().st_size for p in files))
    (local / (args.label+'.json')).write_text(json.dumps(metrics, indent=2), encoding='utf8')
    print(json.dumps(metrics, indent=2))
    print('GET snapshots, fixed calculations and export hashes passed')


if __name__ == '__main__':
    main()
