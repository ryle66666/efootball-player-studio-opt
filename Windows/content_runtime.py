"""Apply an explicitly imported data package before the game initializes."""
from pathlib import Path
import json
import os
import shutil
import time
import uuid
import zipfile

from content_pack import Reader, atomic_json, relative, streams_equal, BLOCK
from player_append import ACTIVE, CONTAINER, LIVE, MANIFEST, RELEASE, TEMPLATE, MOVIE_PROTOCOL, MEMBER as INDEPENDENT_PLAYER_MEMBER
from player_movie_overlay import PAK as PLAYER_MOVIE_PAK

STATE_PROTOCOL = '73-content-applied-v1'
REPORT_PROTOCOL = '73-import-report-v1'
ROOT_RECEIPT = '.studio-content-applied.json'
PLAYER_TARGETS = {ACTIVE, LIVE, MANIFEST, RELEASE, TEMPLATE, CONTAINER+'.utoc', CONTAINER+'.ucas', PLAYER_MOVIE_PAK}


def required_apply_bytes(root, reader, independent):
    pack_bytes = sum(count for _, count in reader.entries.values())
    if independent:
        root = Path(root)
        ucas = root / (CONTAINER + '.ucas')
        cpk = root / ACTIVE
        if ucas.is_file():
            ucas_size = ucas.stat().st_size
            cpk_size = cpk.stat().st_size if cpk.is_file() else 0
            movie = root / PLAYER_MOVIE_PAK
            movie_size = movie.stat().st_size if movie.is_file() else 0
            return ucas_size + cpk_size * 2 + pack_bytes * 2 + movie_size + 8 * 1024 * 1024
        return pack_bytes * 3 + 1024 * 1024
    return pack_bytes * 2


def _next_action_for(detail):
    text = str(detail or '')
    if '空间不足' in text:
        return '请释放设备存储空间后重试'
    if '旧小包' in text or '版本冲突' in text:
        return '请导出并导入最新的球员小包'
    if '版本' in text or '基准' in text or '匹配' in text or '对应' in text:
        return '请先安装对应的基准 APK'
    return '请检查小包文件完整性后重试'


def _record_import_report(storage, report, *, append_history=False):
    try:
        storage = Path(storage)
        storage.mkdir(parents=True, exist_ok=True)
        atomic_json(storage / 'last-import-result.json', report)
        if append_history:
            history_path = storage / 'import-history.json'
            history = []
            if history_path.is_file():
                try:
                    raw = json.loads(history_path.read_text('utf8'))
                    if isinstance(raw, list):
                        history = raw
                except (OSError, ValueError):
                    history = []
            history.append(report)
            atomic_json(history_path, history[-50:])
    except OSError:
        pass


def notify_import_result(report):
    if not report:
        return
    status = report['status']
    if status == 'skipped':
        text = '内容无变化，已跳过。'
    elif status == 'rejected':
        text = '内容包未应用。' + report.get('detail', '')[:100]
    elif status == 'applied':
        counts = {key: sum(row['status'] == key for row in report['players'])
                  for key in ('added', 'updated', 'skipped')}
        text = ('内容包已应用。新增 {added}，更新 {updated}，跳过 {skipped}。'.format(**counts)
                if report['players'] else '内容包已应用。')
        if report.get('movie_playback') == 'unverified':
            text += '动态卡面播放待核对。'
    else:
        return
    try:
        from java import dynamic_proxy, jclass
        application = jclass('com.chaquo.python.Python').getPlatform().getApplication()
        toast = jclass('android.widget.Toast')

        class ShowResult(dynamic_proxy(jclass('java.lang.Runnable'))):
            def run(self):
                toast.makeText(application, text, toast.LENGTH_LONG).show()

        jclass('android.os.Handler')(jclass('android.os.Looper').getMainLooper()).post(ShowResult())
    except Exception:
        # The on-disk receipt remains authoritative if the Android UI is unavailable.
        import logging
        logging.getLogger(__name__).debug('Unable to display import result', exc_info=True)


class ContentRecoveryPending(OSError):
    pass


class ImportSpaceRequired(ValueError):
    pass


def completed(root, storage, identity):
    import json
    try:
        receipt = Path(root) / ROOT_RECEIPT
        if not receipt.is_file() or receipt.stat().st_size > 4096:
            return None
        state = json.loads(receipt.read_text('utf8'))
        if not isinstance(state, dict):
            return None
        if (state.get('protocol'), state.get('package'), state.get('program_id')) != (
                STATE_PROTOCOL, identity['package'], identity['program_id']):
            return None
        uuid.UUID(state['pack_id'])
        saved = Path(storage) / 'active-state.json'
        if saved.is_file():
            if saved.stat().st_size > 4096 or json.loads(saved.read_text('utf8')) != state:
                return None
        else:
            try:atomic_json(saved, state)
            except OSError:pass
        return state
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def target(root, name):
    relative(name)
    root = Path(root).resolve()
    path = root / name
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink() or part.is_junction():
            raise ValueError('内容目标不能经过链接目录')
    if not path.resolve().is_relative_to(root):
        raise ValueError('内容目标不在游戏目录中')
    if path.exists() and not path.is_file():
        raise ValueError('内容目标已有目录：' + name)
    return path


def publication_paths(root, storage, work, names, receipt):
    pairs = [(target(root, name), target(work/'previous', name)) for name in names]
    if receipt:
        pairs += [(root/ROOT_RECEIPT, work/'receipt-0'),
                  (storage/'active-state.json', work/'receipt-1')]
    return pairs


def recover_publications(root, storage, program_id):
    for journal in storage.glob('apply-*/player-publish.json'):
        work = journal.parent
        if (work.is_symlink() or work.is_junction() or work.resolve().parent != storage.resolve()
                or journal.is_symlink() or journal.stat().st_size > 4096):
            raise ContentRecoveryPending('小包恢复记录无效，请保留当前存档和资料')
        try:
            state = json.loads(journal.read_text('utf8'))
            names, receipt, existed = state['names'], state['receipt'], state['existed']
            if (set(names)-PLAYER_TARGETS or len(names)!=len(set(names)) or type(receipt) is not bool
                    or len(existed)!=len(names)+2*receipt or any(type(flag) is not bool for flag in existed)):
                raise ValueError('小包恢复记录格式无效')
            if state['program_id'] == program_id and not state.get('committed'):
                pairs = publication_paths(root, storage, work, names, receipt)
                for (destination, backup), prior in reversed(list(zip(pairs, existed))):
                    if backup.is_file():
                        os.replace(backup, destination)
                    elif not prior:
                        destination.unlink(missing_ok=True)
            journal.unlink()
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ContentRecoveryPending('内容恢复尚未完成，已保留原文件。请恢复存储访问后继续。') from exc
        try:shutil.rmtree(work)
        except OSError:pass


def apply(root, storage, pack, *, package, program_id, receipt=None, previous_pack_id=None, base_apk=None, report_out=None):
    root = Path(root).resolve()
    storage = Path(storage).resolve()
    reader = Reader(pack)
    manifest = reader.manifest
    if manifest['package'] != package:
        raise ValueError('内容包属于另一个游戏安装模式')
    if manifest['program_id'] != program_id:
        raise ValueError('请先安装此内容包对应的游戏程序')
    from player_updates import is_update
    independent = INDEPENDENT_PLAYER_MEMBER in reader.entries
    compact = is_update(reader)
    changed = []
    for name in (() if compact or independent else reader.entries):
        path = target(root, name)
        if path.is_file():
            with path.open('rb') as previous, reader.open(name) as incoming:
                if streams_equal(previous, incoming):
                    continue
        changed.append(name)
    removed = [name for name in manifest['removed'] if target(root, name).exists()]
    if not (compact or independent or changed or removed or receipt is not None):
        return {'status': 'current', 'files': 0, 'pack_id': manifest['pack_id']}
    storage.mkdir(parents=True, exist_ok=True)
    needed = required_apply_bytes(root, reader, independent)
    if shutil.disk_usage(storage).free < needed:
        need_mb = max(1, (needed + 1024**2 - 1) // 1024**2)
        raise ImportSpaceRequired(f'可用存储空间不足，至少需要约 {need_mb} MB，请释放空间后重试')
    work = storage / ('apply-' + uuid.uuid4().hex)
    incoming = work / 'incoming'
    incoming.mkdir(parents=True)
    previous_files = []
    installed = []
    cleanup = True
    metadata_only = False
    try:
        if independent:
            from player_append import stage
            from player_updates import android_apk
            with zipfile.ZipFile(base_apk or android_apk()) as source:
                marker = json.loads(source.read('assets/player-studio/program.json'))
                if (marker.get('package'), marker.get('program_id')) != (package, program_id):
                    raise ValueError('独立球员小包与已安装的基准 APK 不匹配')
                stage_report = {}
                changed = stage(reader, source, marker, lambda name: target(root, name),
                                lambda name: target(incoming, name), report=stage_report)
                if report_out is not None:
                    report_out.update(stage_report)
                if not changed:
                    return {'status': 'current', 'files': 0, 'removed': 0, 'pack_id': manifest['pack_id']}
                metadata_only = stage_report.get('metadata_only', False)
                if metadata_only:
                    receipt = None
        elif compact:
            from player_updates import android_apk, capable, open_update
            with open_update(reader, work) as update, zipfile.ZipFile(base_apk or android_apk()) as source:
                marker = json.loads(source.read('assets/player-studio/program.json'))
                if not capable(marker) or (marker.get('package'), marker.get('program_id')) != (package, program_id):
                    raise ValueError('球员小包与已安装的基准 APK 不匹配')
                if previous_pack_id is not None and previous_pack_id != update.parent:
                    raise ValueError('球员小包的上一次更新与当前游戏不一致，请按顺序导入')
                for name in update.files:
                    target(root, name)
                    update.materialize(name, source, target(incoming, name), lambda alias: target(incoming, alias))
                changed = list(update.files)
        for name in (() if compact or independent else changed):
            destination = incoming / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open('xb') as output, reader.open(name) as source:
                shutil.copyfileobj(source, output, BLOCK)
                output.flush()
                os.fsync(output.fileno())
        changed_names = set(changed)
        journal = work/'player-publish.json'
        if independent:
            paths = publication_paths(root, storage, work, changed, receipt is not None)
            for name in changed:
                with target(incoming, name).open('r+b') as stream:
                    os.fsync(stream.fileno())
            publication = {'program_id': program_id, 'names': changed, 'receipt': receipt is not None,
                           'existed': [destination.is_file() for destination, _ in paths]}
            atomic_json(journal, publication)
        try:
            for name in [*changed, *removed]:
                destination = target(root, name)
                if destination.is_file():
                    previous = work / 'previous' / name
                    previous.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(destination, previous)
                    previous_files.append((destination, previous))
                if name in changed_names:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(incoming / name, destination)
                    installed.append(destination)
            if receipt is not None:
                for index, destination in enumerate((root / ROOT_RECEIPT, storage / 'active-state.json')):
                    if destination.is_file():
                        previous = work / ('receipt-' + str(index))
                        os.replace(destination, previous)
                        previous_files.append((destination, previous))
                    atomic_json(destination, receipt)
                    installed.append(destination)
            if independent:
                atomic_json(journal, dict(publication, committed=True))
        except BaseException:
            cleanup = False
            try:
                for path in reversed(installed):
                    path.unlink(missing_ok=True)
                for destination, previous in reversed(previous_files):
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(previous, destination)
            except OSError as exc:
                raise ContentRecoveryPending('内容恢复尚未完成，已保留原文件。请恢复存储访问后继续。') from exc
            cleanup = True
            raise
        return {'status': 'current' if metadata_only else 'applied',
                'files': 0 if metadata_only else len(changed), 'removed': len(removed), 'pack_id': manifest['pack_id']}
    finally:
        if cleanup and work.resolve().parent == storage:
            try:shutil.rmtree(work)
            except OSError:pass


def prepare(root, *, base_apk=None):
    root = Path(root).resolve()
    storage = root.parent / '.content-packs'
    program = storage / 'program.json'
    if not program.is_file():
        return {'status': 'unmanaged'}
    from native_live import _file_lock
    with _file_lock(storage/'apply.lock'):
        identity = json.loads(program.read_text('utf8'))
        recover_publications(root, storage, identity['program_id'])
        return prepare_locked(root, storage, identity, base_apk)


def _finish_pending(pending, before, *, retain_as=None):
    """Consume only the inbox file examined by this import."""
    after = pending.lstat()
    def stamp(value):
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns
    if stamp(before) != stamp(after):
        return
    if retain_as is None:
        pending.unlink()
    else:
        os.replace(pending, retain_as)


def prepare_locked(root, storage, identity, base_apk, *, _fallback=False):
    pending = storage / 'pending.73pack'
    active = storage / 'active.73pack'
    state = completed(root, storage, identity)
    if not pending.is_file() and state:
        return {'status': 'current', 'files': 0, 'pack_id': state['pack_id']}
    selected = pending if pending.is_file() else active
    if not selected.is_file():
        if identity.get('content_required'):
            raise ValueError('请先导入此游戏程序对应的 .73pack 内容包')
        return {'status': 'ready'}
    is_pending = (selected == pending)
    should_record = not _fallback and (is_pending or state is None)
    manifest = None
    stage_report = {}
    before = None
    try:
        before = selected.lstat()
        reader = Reader(selected)
        manifest = reader.manifest
        if (manifest['package'],manifest['program_id']) != (identity['package'],identity['program_id']):
            raise ValueError('请先安装此内容包对应的游戏程序')
        independent = INDEPENDENT_PLAYER_MEMBER in reader.entries
        if state and state.get('independent_players') and not independent:
            raise ValueError('当前游戏已独立追加球员，请继续导入独立球员小包')
        report = {
            'protocol': REPORT_PROTOCOL,
            'stage': 'received',
            'status': 'received',
            'pack_id': manifest['pack_id'],
            'program_id': identity['program_id'],
            'package': identity['package'],
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'files': 0,
            'players': [],
            'detail': '',
            'next_action': '',
        }
        if state and state['pack_id'] == manifest['pack_id']:
            result = {'status': 'current', 'files': 0, 'pack_id': state['pack_id']}
            if is_pending:
                report.update(stage='skipped', status='skipped', next_action='当前内容包已应用，无需重复导入')
                _record_import_report(storage, report, append_history=True)
                result['import_report'] = report
        else:
            if should_record:
                _record_import_report(storage, report, append_history=False)
                report['stage'] = 'applying'
                report['status'] = 'applying'
                _record_import_report(storage, report, append_history=False)
            receipt = {'protocol': STATE_PROTOCOL, 'package': identity['package'],
                       'pack_id': manifest['pack_id'], 'program_id': identity['program_id']}
            if independent:
                receipt['independent_players'] = True
            result = apply(root, storage, selected, package=identity['package'],
                           program_id=identity['program_id'], receipt=receipt,
                           previous_pack_id=state['pack_id'] if state else None,
                           base_apk=base_apk, report_out=stage_report)
            final_status = 'skipped' if result['files'] == 0 and not result.get('removed') else 'applied'
            report.update(
                stage=final_status,
                status=final_status,
                files=result['files'],
                players=stage_report.get('players', []),
                timestamp=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                next_action='已是最新，无需重复导入' if final_status == 'skipped' else '',
            )
            if stage_report.get('target_game'):
                report['target_game'] = stage_report['target_game']
            if final_status == 'applied' and stage_report.get('protocol') == MOVIE_PROTOCOL:
                report['movie_playback'] = 'unverified'
            if should_record:
                _record_import_report(storage, report, append_history=True)
            result['import_report'] = report
    except ContentRecoveryPending:
        raise
    except Exception as failure:
        rejected_report = None
        if should_record:
            rejected_report = {
                'protocol': REPORT_PROTOCOL,
                'stage': 'rejected',
                'status': 'rejected',
                'pack_id': manifest.get('pack_id') if isinstance(manifest, dict) else None,
                'program_id': identity['program_id'],
                'package': identity['package'],
                'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'files': 0,
                'players': stage_report.get('players', []),
                'detail': str(failure),
                'next_action': _next_action_for(failure),
            }
            _record_import_report(storage, rejected_report, append_history=True)
        transient=isinstance(failure,(OSError,MemoryError,ImportSpaceRequired))
        if is_pending and before is not None and not transient:
            try:_finish_pending(pending, before)
            except OSError:pass
        if state:
            reply = {'status':'current', 'files':0, 'pack_id':state['pack_id'],
                     'warning':'新内容暂未应用，继续使用已安装内容。', 'detail':str(failure)}
            if rejected_report is not None:
                reply['import_report'] = rejected_report
            return reply
        if selected == pending and not pending.exists() and active.is_file():
            reply = prepare_locked(root, storage, identity, base_apk, _fallback=True)
            if rejected_report is not None:
                reply['import_report'] = rejected_report
            return reply
        if not identity.get('content_required'):
            reply = {'status':'ready', 'warning':'新内容暂未应用，继续使用原版内容。', 'detail':str(failure)}
            if rejected_report is not None:
                reply['import_report'] = rejected_report
            return reply
        raise ValueError('当前内容暂不可用，已保留存档。请导入匹配的内容包或释放空间后继续。') from None
    if is_pending:
        skipped = report['status'] == 'skipped'
        try:
            _finish_pending(pending, before, retain_as=None if skipped else active)
        except FileNotFoundError:
            pass
        except OSError:
            result['warning'] = ('内容无变化，已跳过，暂存文件暂未清理。' if skipped else
                                 '内容已应用，安装记录已保存，暂存文件将在后续启动时整理。')
    return result
