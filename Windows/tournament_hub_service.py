"""Local tournament-center requests, serialized with native entry and results."""
import json
import logging
import queue
import sqlite3
import threading
import uuid
from concurrent.futures import Future
from contextlib import closing
from pathlib import Path

BASE = '/player-studio/tournaments'


def route(path, action=False):
    parts = path.rstrip('/').split('/')
    if parts[:3] != ['', 'player-studio', 'tournaments']:
        raise ValueError('锦标赛中心地址无效')
    if not action and len(parts) == 3:
        return None
    if len(parts) != (5 if action else 4):
        raise ValueError('锦标赛中心地址无效')
    uid = str(uuid.UUID(parts[3]))
    if uid != parts[3]:
        raise ValueError('锦标赛编号无效')
    return (uid, parts[4]) if action else uid


def listing(service, conn):
    import tournament_hub as hub
    definitions=service.definitions()
    if len(definitions)==1:return hub.page(service,conn,definitions[0])
    return hub.index_page(service,conn,definitions)


class Worker:
    """Each SQLite connection is opened, used and closed by one daemon worker."""
    def __init__(self, service, database):
        self.service = service
        self.database = Path(database) if database else None
        self.jobs = queue.Queue(maxsize=16)
        self.thread = None
        self.lock = threading.Lock()

    def call(self, uid=None, action=None, payload=None):
        if uid is not None and str(uuid.UUID(uid)) != uid:
            raise ValueError('锦标赛编号无效')
        if action is not None and not isinstance(payload, dict):
            raise ValueError('锦标赛操作参数无效')
        if self.database is None or not self.database.is_file():
            raise ValueError('游戏存档尚未就绪')
        future = Future()
        with self.lock:
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self.run, name='tournament-hub', daemon=True)
                self.thread.start()
        self.jobs.put_nowait((future, uid, action, payload))
        try:
            return future.result(timeout=120)
        except TimeoutError:
            future.cancel()
            raise ValueError('锦标赛中心处理超时，请刷新后重试') from None

    def run(self):
        while True:
            future, uid, action, payload = self.jobs.get()
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(self.execute(uid, action, payload))
            except BaseException as exc:
                future.set_exception(exc)

    def execute(self, uid, action, payload):
        import tournament_hub as hub
        from tournament_runtime import LOCK, publish_saved_home
        from tournament_tasks import transaction
        readonly=action is None or action in ('targets','live','portraits','detail')
        mode = 'ro' if readonly else 'rw'
        with closing(sqlite3.connect(self.database.resolve().as_uri() + '?mode=' + mode, uri=True, timeout=30)) as conn, LOCK:
            if readonly:conn.execute('PRAGMA query_only=ON')
            if action=='targets' and uid is None:return hub.targets(self.service,conn,self.service.definitions())
            if action is None:
                if uid is None:
                    return listing(self.service, conn)
                t = next((t for t in self.service.definitions() if t['uuid'] == uid), None)
                return hub.page(self.service, conn, t) if t else None
            t = next((t for t in self.service.definitions() if t['uuid'] == uid), None)
            if t is None:
                raise hub.HubError('该锦标赛已不在当前目录')
            if readonly:return hub.act(self.service,conn,t,action,payload)
            with transaction(conn):
                out = hub.act(self.service, conn, t, action, payload)
            if action == 'team':
                from tournament_team_choice import choose
                choose(self.service.root, uid, out['team'], t['player_team_id'], out['run_id'])
                self.service.entry_binding = None
                self.service._entry_checks.clear()
            if action in ('open', 'team', 'progress-seen'):
                try:
                    publish_saved_home(conn, self.service.definitions(), self.service.names(),
                                       self.service.document().get('tournament_national_team_nations', {}))
                except Exception:
                    logging.getLogger(__name__).exception('Tournament center homepage refresh deferred')
            return out
