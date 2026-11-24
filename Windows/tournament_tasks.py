"""Durable per-fixture CPU work. A result transaction never runs this work."""
import copy,hashlib,json,logging,sqlite3,threading,time,uuid,zlib
from contextlib import contextmanager,closing
from pathlib import Path
import tournament_rules as rules

PREFIX='studio_tournament_cpu_task_v2:'
PROCESS=uuid.uuid4().hex
LEASE=60
PREPARATION_REVISION='android-2.9.11-tournament-preparation-v3'


@contextmanager
def transaction(conn):
    """Own our transaction, or only our savepoint inside the caller's one."""
    outer=conn.in_transaction;name='studio_'+uuid.uuid4().hex
    conn.execute('savepoint '+name if outer else'begin immediate')
    try:
        yield
        conn.execute('release '+name)if outer else conn.commit()
    except BaseException:
        if outer:conn.execute('rollback to '+name);conn.execute('release '+name)
        else:conn.rollback()
        raise


def cat_revision(document):
    index=document.get('native_player_catalogue')or{}
    return index.get('revision')or index.get('table_sha256')or'legacy-unindexed'


def policy(current,document):
    import tournament_runtime as rt
    return [rt.progress_signature(current['definition']),current['definition']['include_diy'],current['definition']['player_team_id'],cat_revision(document),document.get('revision')]


def key(current,fixture):return PREFIX+':'.join((current['definition']['uuid'],current['run_id'],str(fixture['id'])))


def binding(fixture):return {k:fixture.get(k)for k in('id','stage','round','home','away','tie','leg','legs')}


def rows(conn,current):
    prefix=PREFIX+current['definition']['uuid']+':'+current['run_id']+':'
    return [(k,json.loads(raw))for k,raw in conn.execute("select key,value from meta where key like ? and typeof(value)='text'",(prefix+'%',))]


def supersede(conn,current):
    """Cancel unfinished work and its prepared input for a replaced edition."""
    import tournament_runtime as rt
    for ident,job in rows(conn,current):
        if job.get('status')not in('completed','superseded'):
            job.update(status='superseded',error=None,lease_until=0,retry_after=0)
            job.pop('token',None);job.pop('input_sha256',None)
            rt.put(conn,ident,job)
        conn.execute('delete from meta where key=?',(ident+':input',))


def refresh(current,jobs):
    from tournament_team_choice import setup_pending
    if setup_pending(current):
        current['cpu_progress_v2']={'version':2,'status':'awaiting_setup','pending':0,'completed':0,'total':0,
            'running':0,'retrying':0,'round_total':0,'round_completed':0,'round_index':0,
            'stage_index':current['stage_index'],'errors':[],'blocked':[],'result_saved':False}
        return
    outstanding=[j for j in jobs if j['status']not in('completed','superseded')]
    blocked=[j for j in outstanding if j['status']in('waiting_repair','paused')]
    due=rules.due(current)
    active_round=due[0]['round']if due else max((f['round']for f in current['fixtures']if f['stage']==current['stage_index']),default=0)
    round_fixtures=[f for f in current['fixtures']if f['stage']==current['stage_index']and f['round']==active_round and not f.get('bye')]
    current['cpu_progress_v2']={'version':2,'status':'blocked'if blocked and len(blocked)==len(outstanding)else'processing'if outstanding else'completed',
        'pending':len(outstanding),'completed':sum(j['status']=='completed'for j in jobs),
        'total':len(jobs),'running':sum(j['status']=='processing'for j in outstanding),
        'retrying':sum(j['status']=='retry'for j in outstanding),
        'round_total':len(round_fixtures),'round_completed':sum(bool(f.get('played'))for f in round_fixtures),
        'round_index':active_round,'stage_index':current['stage_index'],
        'errors':[{'fixture_id':j['fixture_id'],'status':j['status'],**j['error']}for j in outstanding if j.get('error')],
        'blocked':[{'fixture_id':j['fixture_id'],**(j.get('error')or{})}for j in blocked],
        'result_saved':bool(current.get('receipts'))}
    legacy=current.get('cpu_remainder_v1')
    if legacy:
        legacy.update(status='completed'if current['completed']else'failed'if blocked and len(blocked)==len(outstanding)else'pending',
            settled_cpu=sum(j['status']=='completed'for j in jobs),error=blocked[0]['error']['message']if blocked else None)


def enqueue(conn,current,document):
    """Only metadata and fixed seeds; no roster building, training or sampling."""
    import tournament_runtime as rt
    if not conn.in_transaction:raise ValueError('CPU 待办必须与本场状态一起保存')
    from tournament_team_choice import setup_pending
    if setup_pending(current):
        refresh(current,[])
        return
    player=current['definition']['player_team_id'];expected=policy(current,document)
    for fixture in rules.due(current):
        if fixture.get('played')or player in(fixture['home'],fixture['away']):continue
        ident=key(current,fixture);job=rt.get(conn,ident)
        if job is None:
            # Preserve an old model/contract without trying to validate or run
            # that CPU model inside the player's settlement transaction.
            contract=rt.simulation_contract(current,fixture)
            job={'version':2,'uid':current['definition']['uuid'],'run_id':current['run_id'],
                'stage_uuid':current['definition']['stages'][fixture['stage']]['uuid'],
                'fixture_id':fixture['id'],'fixture':binding(fixture),'policy':expected,
                'catalogue_revision':cat_revision(document),'seeds':copy.deepcopy(contract.get('seeds')),
                'model_version':contract.get('model_version'),'strength_version':contract.get('strength_version'),
                'status':'waiting','attempts':0,'retry_after':0,'error':None,'lease_until':0}
            rt.put(conn,ident,job)
        elif job['status']in('waiting_repair','retry','superseded')and job['policy']!=expected:
            job.update(status='waiting',policy=expected,catalogue_revision=cat_revision(document),error=None,retry_after=0,lease_until=0)
            job.pop('input_sha256',None);conn.execute('delete from meta where key=?',(ident+':input',));rt.put(conn,ident,job)
    refresh(current,[job for _,job in rows(conn,current)])


def locate(conn,job):
    import tournament_runtime as rt
    for name in(rt.PREFIX+job['uid'],rt.PREFIX+job['uid']+':history:'+job['run_id']):
        value=rt.get(conn,name)
        if value and value.get('run_id')==job['run_id']:return name,rules.State(value)
    return None,None


def validate(current,job,document):
    if current is None:return None
    fixture=next((f for f in current['fixtures']if str(f['id'])==str(job['fixture_id'])),None)
    if fixture is None or binding(fixture)!=job['fixture']:return None
    if current['definition']['player_team_id']in(fixture['home'],fixture['away']):return None
    if current['definition']['stages'][fixture['stage']]['uuid']!=job['stage_uuid']:return None
    return fixture


class Queue:
    def __init__(self,service):
        self.service=service;self.threads={};self.events={};self.lock=threading.RLock()
        self.workers={}

    def prepared_worker(self,conn,document):
        path=self.service.database_path(conn);source=self.service.source_context(conn,document)
        with self.lock:cached=self.workers.get(path)
        if cached is None or cached[0]!=source:
            snapshot=copy.deepcopy(document)
            replacement=(source,self.service.worker_service(snapshot),threading.RLock())
            with self.lock:
                cached=self.workers.get(path)
                if cached is None or cached[0]!=source:self.workers[path]=replacement;cached=replacement
        return cached

    def wake(self,conn):
        # A caller-owned uncommitted transaction is never an invitation to
        # spawn CPU work. An already-running observer sees committed rows only.
        path=self.service.database_path(conn)
        if not path:return
        from reply_jobs import defer
        if not conn.in_transaction:
            def after_response():
                with closing(sqlite3.connect(str(path),timeout=5)) as background:self.wake(background)
            if defer(('tournament-cpu',str(path)),after_response):return
        with self.lock:
            old=self.threads.get(path)
            if old and old.is_alive():self.events[path].set();return
            if conn.in_transaction:return
            event=self.events[path]=threading.Event()
            thread=threading.Thread(target=self.loop,args=(path,event),name='tournament-persistent-cpu',daemon=True)
            self.threads[path]=thread;thread.start()

    @staticmethod
    def request_in_progress():
        """True while a game request holds the gateway lock (another thread)."""
        import tournament_runtime as rt
        if rt.LOCK.acquire(blocking=False):
            rt.LOCK.release();return False
        return True

    def loop(self,path,event):
        while True:
            # Let the visible request finish first; between fixtures only, so
            # durable progress and leases are unchanged.
            waited=0
            while self.request_in_progress()and waited<300:time.sleep(.1);waited+=1
            try:
                worked=self.run_once(path)
                if worked:continue
            except Exception:logging.getLogger(__name__).exception('CPU task observer retained durable work')
            event.wait(1);event.clear()

    def run_once(self,path):
        """One fixture, one independent commit; restartable without threads."""
        import tournament_runtime as rt
        conn=sqlite3.connect(str(path),timeout=.5)
        try:
            self.service.flush_deferred(conn)
            # Never trust an in-memory queue after Android kills the process.
            jobs=[(name,json.loads(raw))for name,raw in conn.execute("select key,value from meta where key like ? and typeof(value)='text' order by key",(PREFIX+'%',))]
            # The idle observer wakes every second. Selection only reads the
            # live document; the private copy (about a second for a large
            # catalogue, holding the GIL) is made only for a job that runs.
            now=time.time();live=self.service.document()
            for ident,job in jobs:
                if job.get('version')!=2 or job['status']in('completed','superseded'):continue
                if job['status']=='processing'and job.get('lease_until',0)>now:continue
                try:
                    _,current=locate(conn,job)
                    if current is None:continue
                    from tournament_team_choice import setup_pending
                    if setup_pending(current):continue
                    fixture=validate(current,job,live)
                    if fixture is not None and not fixture.get('played')and fixture not in rules.due(current):continue
                except (KeyError,TypeError,ValueError,IndexError)as exc:
                    error={'message':str(exc)[:512],'category':'state','exception_type':type(exc).__name__,
                           'preparation_revision':PREPARATION_REVISION}
                    if job.get('status')!='paused'or job.get('error')!=error:
                        with rt.LOCK,transaction(conn):
                            saved=rt.get(conn,ident)
                            if saved==job:
                                saved.update(status='paused',lease_until=0,error=error)
                                rt.put(conn,ident,saved)
                    continue
                if job['status']=='paused'and(job.get('error')or{}).get('category')=='state':
                    with rt.LOCK,transaction(conn):
                        saved=rt.get(conn,ident)
                        if saved!=job:continue
                        job.update(status='waiting',error=None,retry_after=0,consecutive_failures=0)
                        rt.put(conn,ident,job)
                expected=policy(current,live)
                source=self.service.source_context(conn,live)
                if job['status']in('waiting_repair','paused')and job['policy']==expected:
                    if ((job.get('error')or{}).get('preparation_revision')==PREPARATION_REVISION
                            and job.get('preparation_source')==source):continue
                    with rt.LOCK,transaction(conn):
                        saved=rt.get(conn,ident)
                        if saved!=job:continue
                        job.update(status='waiting',automatic_retry_revision=PREPARATION_REVISION,error=None,retry_after=0,consecutive_failures=0)
                        job.pop('input_sha256',None)
                        conn.execute('delete from meta where key=?',(ident+':input',))
                        rt.put(conn,ident,job)
                if job['status']=='retry'and job['policy']==expected and job.get('preparation_source')==source and job.get('retry_after',0)>now:continue
                self.execute(conn,ident,job,live)
                return True
            return False
        finally:conn.close()

    def execute(self,conn,ident,observed,document):
        import tournament_runtime as rt
        token=uuid.uuid4().hex;job=None;current=None;state_key=None
        try:
            with rt.LOCK,transaction(conn):
                job=rt.get(conn,ident);state_key,current=locate(conn,job)
                if job!=observed:return
                if job['status']in('completed','superseded'):return
                from tournament_team_choice import setup_pending
                if setup_pending(current):return
                fixture=validate(current,job,document)
                if fixture is None:
                    job.update(status='superseded',error={'message':'对阵身份或玩家控制队已改变，未模拟或覆盖旧比分'});rt.put(conn,ident,job);return
                if fixture.get('played'):
                    job.update(status='completed',error=None);rt.put(conn,ident,job);return
                if fixture not in rules.due(current):return
                expected=policy(current,document)
                source=self.service.source_context(conn,document)
                if job['policy']!=expected or job.get('preparation_source')not in(None,source):
                    conn.execute('delete from meta where key=?',(ident+':input',));job.pop('input_sha256',None)
                    job.update(policy=expected,catalogue_revision=cat_revision(document),consecutive_failures=0)
                job['preparation_source']=source
                job.update(status='processing',owner=PROCESS,token=token,lease_until=time.time()+LEASE,attempts=job.get('attempts',0)+1,error=None)
                rt.put(conn,ident,job)
            # All expensive work uses a read-only connection and no global
            # gateway lock. A native writer cannot be blocked by our sampling.
            ro=sqlite3.connect(Path(self.service.database_path(conn)).resolve().as_uri()+'?mode=ro',uri=True,timeout=.5)
            try:
                worker_source,worker,worker_lock=self.prepared_worker(ro,document)
                worker_lock.acquire()
                if worker_source!=job['preparation_source']:raise TimeoutError('CPU 资料在阵容准备前发生变化')
                fixture=next(f for f in current['fixtures']if str(f['id'])==str(job['fixture_id']))
                if job.get('input_sha256'):
                    raw=conn.execute('select value from meta where key=?',(ident+':input',)).fetchone()
                    if raw is None:raise ValueError('CPU 固定阵容快照缺失，已保留对阵与种子')
                    decoder=zlib.decompressobj();value=decoder.decompress(raw[0],rt.MAX_CONTRACT_BYTES+1)
                    if len(value)>rt.MAX_CONTRACT_BYTES or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:raise ValueError('CPU 固定阵容快照长度或压缩数据无效')
                    contract=json.loads(value)
                    if hashlib.sha256(value).hexdigest()!=job['input_sha256']or contract['seeds']!=job['seeds']:raise ValueError('CPU 固定阵容快照或种子不匹配')
                    fixture['simulation_v1']=contract
                else:
                    contract=worker.freeze_fixture(ro,current,fixture)
                    if contract['seeds']!=job['seeds']:raise ValueError('CPU 对阵固定种子改变')
                    value=json.dumps(contract,sort_keys=True,separators=(',',':')).encode()
                    if len(value)>rt.MAX_CONTRACT_BYTES:raise ValueError('CPU 固定阵容快照超限')
                    payload=zlib.compress(value,6)
                    with rt.LOCK,transaction(conn):
                        saved=rt.get(conn,ident)
                        if saved.get('token')!=token:return
                        _,latest=locate(conn,saved)
                        if setup_pending(latest):
                            saved.update(status='waiting',lease_until=0);saved.pop('token',None)
                            rt.put(conn,ident,saved);return
                        if (latest is None or policy(latest,self.service.document())!=job['policy']
                                or self.service.source_context(conn)!=job['preparation_source']):raise TimeoutError('CPU 资料在阵容准备期间改变，等待重新核验')
                        conn.execute('insert or replace into meta values(?,?)',(ident+':input',payload))
                        job['input_sha256']=hashlib.sha256(value).hexdigest();job['lease_until']=time.time()+LEASE;rt.put(conn,ident,job)
                worker.simulate_fixture(ro,current,fixture)
            finally:
                if 'worker_lock'in locals():worker_lock.release()
                ro.close()
            with rt.LOCK,transaction(conn):
                saved=rt.get(conn,ident);state_key,latest=locate(conn,saved)
                if saved.get('token')!=token:return
                if setup_pending(latest):
                    saved.update(status='waiting',lease_until=0);saved.pop('token',None)
                    rt.put(conn,ident,saved);return
                target=validate(latest,saved,self.service.document())
                if target is None:raise ValueError('CPU 提交时对阵身份改变')
                if target.get('played'):
                    saved.update(status='completed',error=None);rt.put(conn,ident,saved);return
                if (policy(latest,self.service.document())!=saved['policy']
                        or self.service.source_context(conn)!=saved['preparation_source']):raise TimeoutError('CPU 提交时有效目录或赛事配置改变')
                if target not in rules.due(latest):raise ValueError('CPU 提交时对阵尚未到合法轮次')
                before=rt.promotion.capture(latest)
                target['simulation_v1']=copy.deepcopy(fixture['simulation_v1'])
                rules.record(latest,target['id'],*fixture['score'],penalties=fixture.get('penalties'),extra=fixture.get('extra_time_score',[0,0]))
                rt.settle_progression(latest,before)
                saved.update(status='completed',lease_until=0,error=None,consecutive_failures=0);rt.put(conn,ident,saved)
                rt.compact_contracts(conn,latest,[target['id']]);enqueue(conn,latest,self.service.document())
                rt.put(conn,state_key,latest);conn.execute('delete from meta where key=?',(ident+':input',))
            # Presentation failure cannot undo an already committed CPU result.
            try:rt.publish_saved_home(conn,self.service.definitions(),self.service.names(),self.service.document().get('tournament_national_team_nations',{}))
            except Exception:logging.getLogger(__name__).exception('CPU result saved; homepage refresh deferred')
        except Exception as exc:
            if job is None:raise
            conn.rollback()
            from effective_players import RosterUnavailable
            repairable=isinstance(exc,RosterUnavailable)
            with rt.LOCK,transaction(conn):
                saved=rt.get(conn,ident)
                if saved is None or saved.get('token')!=token:return
                state_key,latest=locate(conn,saved)
                failures=saved.get('consecutive_failures',0)+1
                saved.update(status='waiting_repair'if repairable else'retry'if failures<=3 else'paused',lease_until=0,consecutive_failures=failures,
                    retry_after=0 if repairable or failures>3 else time.time()+(2,10,30)[failures-1],
                    error={'message':str(exc)[:512],**getattr(exc,'details',{}),'category':'roster'if repairable else'processing',
                           'exception_type':type(exc).__name__,'preparation_revision':PREPARATION_REVISION})
                rt.put(conn,ident,saved)
                if latest is not None:
                    refresh(latest,[value for _,value in rows(conn,latest)]);rt.put(conn,state_key,latest)
            try:rt.publish_saved_home(conn,self.service.definitions(),self.service.names(),self.service.document().get('tournament_national_team_nations',{}))
            except Exception:logging.getLogger(__name__).exception('CPU failure saved; homepage refresh deferred')
