"""In-game Android Dialog/WebView host, deliberately separate from draw/matches.

No external-browser intent, no game launch and no live injection on import.
The integrator calls dispatch_saved_schedule at the native schedule action.
The original V17 GameActivity.Get() and NativeCalls.WebViewVisible(boolean)
are verified in the APK. Android UI creation still requires device validation.
"""
import logging
import copy
from collections import deque
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import uuid

LOG = logging.getLogger(__name__)
_HOST = None
_CHANNEL = None
_SERVICE = None
_WORKER = None
_STATUS_LOCK = threading.RLock()
_STATUS_EVENTS = deque(maxlen=32)
_STATUS = {'ready_reason':'service not configured','worker_tick':None,'write_error':None}
_LAST_FLUSH = 0
_EXTERNAL_STATUS_PATH = None
_EXTERNAL_STATUS_ERROR = None
LOADING_HTML = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>正在加载锦标赛</title><style>
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;background:#111731;color:#f1f3ff;
font:16px system-ui,sans-serif}body{display:grid;place-items:center;padding:24px;text-align:center;
background:radial-gradient(ellipse at 25% 0,#302864,transparent 65%),#111731}
main{max-width:640px}.spinner{margin:0 auto 24px;width:48px;height:48px;border:4px solid #393c64;
border-top-color:#a99bff;border-radius:50%;animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}h1{font-size:clamp(22px,4vw,32px);margin:12px}
p{line-height:1.7;color:#c1c8e4}button{margin-top:16px;padding:12px 32px;border:1px solid #68639a;
border-radius:24px;background:#2d2c53;color:inherit;font:inherit}small{color:#a7afce}
</style><main><div id="spinner" class="spinner" role="progressbar" aria-label="正在加载"></div>
<h1 id="heading">正在加载锦标赛</h1><p id="message">正在准备赛事界面，请稍候。</p>
<small id="elapsed">已等待 0 秒</small><br><button id="retry" hidden onclick="document.title='player-studio-schedule:retry'">重试</button><button onclick="tournamentScheduleBack()">返回</button></main>
<script>window.tournamentScheduleBack=()=>document.title='player-studio-schedule:back';
window.tournamentLoadingMessage=(text,failed=false)=>{
document.getElementById('message').textContent=text;document.getElementById('spinner').hidden=failed;
document.getElementById('retry').hidden=!failed;
document.getElementById('heading').textContent=failed?'暂未进入锦标赛':'正在加载锦标赛'};
const started=Date.now();setInterval(()=>document.getElementById('elapsed').textContent=
'已等待 '+Math.floor((Date.now()-started)/1000)+' 秒',1000);</script></html>'''
REVISION = 'v17-tournament-schedule-click-v3'
NAVIGATION_RETRY_SECONDS = 8
NAVIGATION_RETRY_LIMIT = 2
NAVIGATION_TIMEOUT_SECONDS = 30
DIAGNOSTIC_GATES = ('entry','ready','frame','actor','fade_flag','record_pointer','record_length',
                    'record_buffer','record_text','forward_pointer','forward_length','forward_buffer',
                    'forward_text','selected_context','selected_event','event_allowlist','class_pointer',
                    'class_name','state_property','property_offset','menu_state','pending','publish','handled')
CHANNEL_SCHEMA={'bytes':512,'ready':0,'pending':4,'sequence':8,'event_id':16,'actor':24,'acknowledged':32,
                'diagnostics':{'version':1,'version_offset':64,'last_gate':68,'last_reject':72,
                               'observed_state':76,'observed_event':80,'observed_ready':84,
                               'record_count':88,'forward_count':92,'observed_class_name':96,
                               'expected_class_name':104,'property_offset':112,'fade_flag':116,
                               'reached_offset':128,'rejected_offset':256,'capacity':32,
                               'gates':list(DIAGNOSTIC_GATES)}}

# Exact native evidence. This inventory does not install a speculative patch.
ENTRY_EVIDENCE = {
    'engine_sha256': 'efd257d9e41b622d6ecb8f51f7b0a8ee8820113fd427c5572025df1c8eecc7b8',
    'menu_constructor_va': 0x73AA700,
    'menu_update_va': 0x73AA770,
    'schedule_task_constructor_va': 0x7AE8CC4,
    'schedule_request': 'CMD_GET_EVENT_COMPE_TOURNAMENT_MATCH_LIST',
    'challenge_schedule_request': 'CMD_GET_EVENT_MISSION_AI_MATCH_LIST',
    'schedule_visibility_va': 0x32B9ADC,
    'native_click_callback_identified': True,
    'click_function': 'BP_MenuEvCompeEventMainMenu.CallbackTapRecord',
    'click_label': 'Any2W[436]',
    'click_dispatch': 'FlowManager.StartFadeOutFunc(false, record, forward)',
    'click_hook_va': 0x3C9408C,
    'caller_actor': 'FFrame.Object at x20 + 0x18; x0 is FlowManager',
    'required_actor_state': 3,
    'actor_state_name': 'SelectMenu; normal callback does not write MainMenuState',
    'timeout_alert_state': 6,
    'activity': 'com.epicgames.ue4.GameActivity',
    'activity_get_signature': '()Lcom/epicgames/ue4/GameActivity;',
    'visibility_class': 'com.epicgames.ue4.NativeCalls',
    'visibility_method': 'WebViewVisible',
    'visibility_signature': '(Z)V',
    'native_visibility_va': 0x286AB00,
    'external_browser_forbidden': ['jp.konami.android.common.WebBrowser.BrowserStartUp',
                                   'com.epicgames.ue4.GameActivity.AndroidThunkJava_LaunchURL'],
    'original_schedule_button_replaced_in_game': False,
    'device_validated': False,
}


def diagnostic_status():
    """Snapshot our own retained channel; never dereference engine pointers."""
    with _STATUS_LOCK:
        host = _HOST
        return {'schema_version':1,'revision':REVISION,'pid':os.getpid(),
                'timestamp_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
                'configured':_SERVICE is not None,'channel_registered':_CHANNEL is not None,
                'external_status_path':str(_EXTERNAL_STATUS_PATH) if _EXTERNAL_STATUS_PATH else None,
                'external_status_error':_EXTERNAL_STATUS_ERROR,
                'worker_alive':bool(_WORKER and _WORKER.is_alive()),
                'worker':copy.deepcopy(_STATUS),
                'channel':_CHANNEL.snapshot() if _CHANNEL else None,
                'host':None if host is None else {'pending':host.pending,'current_url':host.current_url,
                        'dialog_show_returned':host.shown,'last_title':host.last_title,'last_error':host.last_error},
                'events':list(_STATUS_EVENTS),'game_ui_success_asserted':False}


def _external_status_path():
    """App-owned external files remain available when private adb reads fail."""
    global _EXTERNAL_STATUS_PATH,_EXTERNAL_STATUS_ERROR
    if _EXTERNAL_STATUS_PATH is not None:
        return _EXTERNAL_STATUS_PATH
    try:
        from java import jclass
        activity=jclass(ENTRY_EVIDENCE['activity']).Get()
        if activity is None:raise RuntimeError('GameActivity unavailable')
        folder=activity.getExternalFilesDir(None)
        if folder is None:raise RuntimeError('External files directory unavailable')
        _EXTERNAL_STATUS_PATH=Path(str(folder.getAbsolutePath()))/'player-studio-tournament-schedule-ui-status.json'
        _EXTERNAL_STATUS_ERROR=None
    except Exception as exc:
        _EXTERNAL_STATUS_ERROR=type(exc).__name__+': '+str(exc)
    return _EXTERNAL_STATUS_PATH


def _atomic_status(path,value):
    temporary=None
    try:
        path.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf8',dir=path.parent,
                                         prefix='.tournament-schedule-',suffix='.tmp',delete=False) as output:
            temporary=Path(output.name)
            json.dump(value,output,ensure_ascii=False,indent=2)
        os.replace(temporary,path)
    finally:
        if temporary and temporary.exists():
            try:temporary.unlink()
            except OSError:pass


def write_status(force=False):
    """Atomic primary/external copies; a diagnostic write cannot block the UI."""
    global _LAST_FLUSH
    with _STATUS_LOCK:
        if not force and time.monotonic()-_LAST_FLUSH<5:
            return False
        paths=[]
        if _SERVICE is not None:
            paths.append(Path(_SERVICE.root)/'outputs/efootball/tournament-schedule-ui-status.json')
        external=_external_status_path()
        if external is not None and external not in paths:paths.append(external)
        if not paths:return False
        try:
            value=diagnostic_status()
        except Exception as exc:
            LOG.warning('Tournament schedule diagnostic snapshot failed: %s',exc)
            return False
        errors={};written=0
        for path in paths:
            try:_atomic_status(path,value);written+=1
            except Exception as exc:errors[str(path)]=type(exc).__name__+': '+str(exc)
        if errors and _STATUS.get('write_error')!=errors:
            LOG.warning('Tournament schedule diagnostic file unavailable: %s',errors)
        _STATUS['write_error']=errors or None
        _LAST_FLUSH=time.monotonic()
        return bool(written)


def _event(name, **details):
    with _STATUS_LOCK:
        _STATUS_EVENTS.append({'time_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
                               'event':name,**details})
    LOG.info('Tournament schedule %s %s',name,details)
    write_status(True)


class ScheduleClick:
    """One-shot intent made by a button adapter, never by a network handler.

    A request alone cannot distinguish entry prefetch from the user's click.
    Keep this boundary explicit until the native click adapter is integrated.
    """
    def __init__(self, uid, clock=time.monotonic):
        self.uid=str(uuid.UUID(uid));self.clock=clock;self.created=clock();self.used=False

    def consume(self, uid):
        if self.used or str(uuid.UUID(uid))!=self.uid or not 0<=self.clock()-self.created<=3:
            return False
        self.used=True
        return True


def local_schedule_url(url, uid):
    """Only the already-existing loopback tournament route may be loaded."""
    uid = str(uuid.UUID(uid)) if uid is not None else None
    parts = urlsplit(url)
    if parts.scheme != 'http' or parts.hostname not in ('127.0.0.1', 'localhost', '::1') or parts.username or parts.password:
        raise ValueError('赛程承载只允许游戏自身的本地内容服务')
    # Force port parsing so malformed authorities cannot bypass validation.
    if not parts.port:
        raise ValueError('本地赛程服务缺少端口')
    if parts.path != '/player-studio/tournaments' + ('/' + uid if uid else ''):
        raise ValueError('赛程地址与当前赛事不一致')
    query = [(k,v) for k,v in parse_qsl(parts.query) if k != 'host'] + [('host','android')]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ''))


class AndroidScheduleHost:
    """Modal in the actual GameActivity; back dismisses it over the same game.

    All Java references and callbacks remain alive until dismissal. No JS
    interface is installed. The trusted page's return button changes its title;
    a UI-thread Runnable observes that narrow signal and dismisses the Dialog.
    """
    def __init__(self):
        from java import dynamic_proxy, jclass
        self.jclass = jclass
        self.dynamic_proxy = dynamic_proxy
        self.activity = jclass(ENTRY_EVIDENCE['activity']).Get()
        if self.activity is None:
            raise RuntimeError('游戏 Activity 尚未就绪')
        self.dialog = self.web = self.poller = self.dismiss_listener = self.key_listener = None
        self.current_url = None
        self.last_error = None
        self.last_title = None
        self.shown = False
        self.pending = False
        self.loading = False
        self.generation = 0
        self.waiting = None
        self.menu_loaded_at = None
        self._waiting_generation = None
        self._navigation_keepalive_at = 0.
        self._navigation_reported = None
        self._navigation_started = self._navigation_attempt_at = None
        self._navigation_retries = 0
        self._navigation_event = None
        self.navigation_failed = False
        self.return_url = None
        self.loading_message = '正在准备赛事界面，请稍候。'
        self._show_task = None
        self._close_task = None
        self.handler = jclass('android.os.Handler')(jclass('android.os.Looper').getMainLooper())
        self._ui_tasks = deque()
        self._ui_lock = threading.RLock()
        self._ui_scheduled = False
        self._create_callbacks()

    def _create_callbacks(self):
        jc=self.jclass

        class Runnable(self.dynamic_proxy(jc('java.lang.Runnable'))):
            def __init__(_self, callback):
                super().__init__()
                _self.callback=callback

            def run(_self):
                try:_self.callback()
                except Exception:LOG.exception('Tournament UI callback failed')

        class Dismiss(self.dynamic_proxy(jc('android.content.DialogInterface$OnDismissListener'))):
            def __init__(_self, owner):
                super().__init__()
                _self.owner=owner

            def onDismiss(_self, _dialog):
                _self.owner._dismissed()

        class Back(self.dynamic_proxy(jc('android.content.DialogInterface$OnKeyListener'))):
            def __init__(_self, owner):
                super().__init__()
                _self.owner=owner

            def onKey(_self, _dialog, key, event):
                if key!=4:return False
                owner=_self.owner
                if event.getAction()==1 and owner.web is not None:
                    owner.web.evaluateJavascript(
                        "if(window.tournamentScheduleBack){window.tournamentScheduleBack()}"
                        "else{document.title='player-studio-schedule:close'}",None)
                return True

        self._ui_dispatcher=Runnable(self._run_ui_tasks)
        self._poll_runner=Runnable(self._poll_ui)
        self._dismiss_proxy=Dismiss(self)
        self._back_proxy=Back(self)

    def _ui(self, function):
        with self._ui_lock:
            self._ui_tasks.append(function)
            if self._ui_scheduled:return self._ui_dispatcher
            self._ui_scheduled=True
        try:self.activity.runOnUiThread(self._ui_dispatcher)
        except Exception:
            with self._ui_lock:self._ui_scheduled=False
            raise
        return self._ui_dispatcher

    def _run_ui_tasks(self):
        with self._ui_lock:
            tasks=list(self._ui_tasks)
            self._ui_tasks.clear()
            self._ui_scheduled=False
        for function in tasks:
            try:function()
            except Exception as exc:
                self.last_error=str(exc)
                LOG.exception('Tournament UI operation failed')

    def _poll_ui(self):
        if self.dialog is None or not self.dialog.isShowing():return
        try:
            title=str(self.web.getTitle())
            new_title=title!=self.last_title
            if new_title:
                self.last_title=title
                _event('webview_title',title=title)
            if new_title and title=='player-studio-schedule:close':
                self.dialog.dismiss()
                return
            if new_title and title=='player-studio-schedule:back':
                self.back_from_loading()
                return
            if new_title and title=='player-studio-schedule:retry' and self.loading:
                self.retry_navigation()
                return
            if new_title and title=='player-studio-schedule:handoff' and not self.loading:
                self.return_url=list_return_url(self.current_url)
                self.await_native_menu()
            if self.loading:
                self._check_navigation()
                if self.loading and self.web is not None:
                    self.web.evaluateJavascript('window.tournamentLoadingMessage&&window.tournamentLoadingMessage('
                        +json.dumps(self.loading_message,ensure_ascii=False)+','+json.dumps(self.navigation_failed)+')',None)
        except Exception:
            LOG.exception('Tournament WebView host polling failed')
            self.close()
        finally:
            if self.dialog is not None and self.dialog.isShowing() and self.poller is not None:
                self.handler.postDelayed(self.poller,150)

    def open(self, url):
        if self.pending and not self.loading:
            _event('host_open_already_pending')
            return False
        return self._queue(url,False)

    def show_loading(self, message='正在准备赛事界面，请稍候。'):
        self.loading_message=message
        if self.loading and (self.shown or self.pending):return True
        return self._queue(None,True)

    def _queue(self,url,loading):
        self.generation+=1
        generation=self.generation
        self.loading=loading
        self.navigation_failed=False
        self.pending = True
        navigation_visible(2 if loading else 1)
        self._show_task = self._ui(lambda: self._show(url,loading,generation))
        _event('host_open_queued',url=url)
        return True

    def _load(self,url,loading):
        self.current_url=url
        self.jclass(ENTRY_EVIDENCE['visibility_class']).WebViewVisible(not loading)
        if loading:
            self.web.loadDataWithBaseURL('http://127.0.0.1/',LOADING_HTML,'text/html','UTF-8',None)
        else:self.web.loadUrl(url)
        self.web.requestFocus()

    def _show(self, url, loading=False, generation=None):
        if generation is not None and generation!=self.generation:return
        try:
            if self.activity.isFinishing():
                raise RuntimeError('游戏 Activity 正在退出')
            if self.dialog is not None and self.dialog.isShowing():
                self._load(url,loading)
                return
            jc = self.jclass
            dialog = jc('android.app.Dialog')(self.activity, 16973834)  # Theme_Black_NoTitleBar_Fullscreen
            web = jc('android.webkit.WebView')(self.activity)
            settings = web.getSettings()
            settings.setJavaScriptEnabled(True)
            settings.setDomStorageEnabled(True)
            settings.setAllowFileAccess(False)
            settings.setAllowContentAccess(False)
            settings.setJavaScriptCanOpenWindowsAutomatically(False)
            settings.setSupportMultipleWindows(False)
            settings.setBuiltInZoomControls(False)
            settings.setUseWideViewPort(True)
            settings.setLoadWithOverviewMode(False)
            web.setWebViewClient(jc('android.webkit.WebViewClient')())
            web.setBackgroundColor(-16051680)
            web.setFocusableInTouchMode(True)
            dialog.setContentView(web)
            dialog.setCancelable(True)
            dialog.setCanceledOnTouchOutside(False)
            self.dialog, self.web, self.current_url = dialog, web, url
            self.dismiss_listener, self.poller, self.key_listener = self._dismiss_proxy,self._poll_runner,self._back_proxy
            dialog.setOnDismissListener(self.dismiss_listener)
            dialog.setOnKeyListener(self.key_listener)
            dialog.show()
            self.shown=True
            _event('dialog_show_returned')
            window = dialog.getWindow()
            window.setLayout(-1, -1)
            window.setSoftInputMode(16)  # SOFT_INPUT_ADJUST_RESIZE for team search.
            window.getDecorView().setSystemUiVisibility(self.activity.getWindow().getDecorView().getSystemUiVisibility())
            self._load(url,loading)
            _event('webview_load_requested',url=url)
            self.handler.postDelayed(self.poller, 150)
            self.last_error = None
        except Exception as exc:
            self.last_error = str(exc)
            _event('host_show_failed',error=type(exc).__name__+': '+str(exc))
            LOG.exception('Unable to present the in-game tournament schedule')
            if self.dialog is not None:
                self.dialog.dismiss()
        finally:
            self.pending = False
            if not self.shown:navigation_visible(False)
            write_status(True)

    def back_from_loading(self):
        """返回 on the loading page: back to the tournament list it came
        from (the pending native step is cancelled), else to the game."""
        url,self.return_url=self.return_url,None
        if self.waiting is not None:
            channel,sequence,event=self.waiting
            channel.navigation_cancel(sequence,event)
            self.waiting=None
        if url and self.dialog is not None:
            self._queue(url,False)
            _event('loading_back_to_list')
            return
        if self.dialog is not None:self.dialog.dismiss()

    def _dismissed(self):
        self.generation+=1
        if self.waiting is not None:
            channel,sequence,event=self.waiting
            channel.navigation_cancel(sequence,event)
            self.waiting=None
        try:
            if self.poller is not None:
                self.handler.removeCallbacks(self.poller)
            if self.web is not None:
                self.web.stopLoading()
                self.web.loadUrl('about:blank')
                self.web.destroy()
        finally:
            self.dialog = self.web = self.poller = self.dismiss_listener = self.key_listener = None
            self.current_url = None
            self.shown=False
            self.loading=False
            self.pending=False
            self.navigation_failed=False
            self._navigation_event=None
            self._navigation_started=self._navigation_attempt_at=None
            self._navigation_retries=0
            self.jclass(ENTRY_EVIDENCE['visibility_class']).WebViewVisible(False)
            navigation_visible(False)
            _event('dialog_dismissed')

    def close(self):
        self.generation+=1
        self.pending=False
        def dismiss():
            if self.dialog is not None:
                self.dialog.dismiss()
            else:self._dismissed()
        self._close_task = self._ui(dismiss)

    def await_native_menu(self,event=None):
        import tournament_home
        channel=tournament_home._CHANNEL
        state=channel.navigation_status()if channel is not None else None
        if not state or not state['sequence']or(event is not None and event!=state['event']):
            self.show_loading('赛事菜单暂未就绪，请稍候或返回重试。')
            return False
        identity=(channel,state['sequence'],state['event'])
        if self.waiting==identity and self.loading and self._waiting_generation==self.generation:return True
        self.show_loading('正在进入赛事菜单，页面显示完成后将自动进入。')
        self.waiting=identity
        self._waiting_generation=self.generation
        self.menu_loaded_at=None
        self._navigation_keepalive_at=0.
        self._navigation_reported=None
        self._navigation_started=self._navigation_attempt_at=time.monotonic()
        self._navigation_retries=0
        self._navigation_event=state['event']
        self.navigation_failed=False
        return True

    def retry_navigation(self):
        import tournament_home
        channel=tournament_home._CHANNEL
        event=self._navigation_event
        if not self.navigation_failed or channel is None or event is None:return False
        status=channel.navigation_status()
        if status['sequence']!=status['acknowledged']:
            retry=getattr(channel,'navigation_retry',None)
            accepted=bool(retry and status['event']==event and retry(status['sequence'],event))
        else:accepted=channel.navigation_request(event)
        if not accepted:
            self.loading_message='赛事菜单暂不可用，请返回后重新选择锦标赛。'
            return False
        self.loading_message='正在进入比赛计划，请稍候。'
        self.waiting=None
        self.last_title=None
        self._load(None,True)
        return self.await_native_menu(event)

    def _check_navigation(self):
        """Called by the existing UI poller; all menu completion stays on it."""
        if not self.loading or self.waiting is None:return
        if self._waiting_generation!=self.generation:
            self.waiting=None
            return
        channel,sequence,target=self.waiting
        status=channel.navigation_status()
        seen=(status['sequence'],status['acknowledged'],status['state'],status['error'])
        if seen!=self._navigation_reported:
            self._navigation_reported=seen
            print('EFOOTBALL_NAV sequence=%d ack=%d state=%d error=%d event=%d visible=%d'%(
                status.get('sequence',0),status.get('acknowledged',0),status.get('state',0),status.get('error',0),status.get('event',0),status.get('visibility_mode',0)),flush=True)
        if (status['sequence'],status['event'])!=(sequence,target):
            self.loading_message='赛事请求已变化，请返回后重新选择。'
            self.waiting=None
            self.navigation_failed=True
            return
        if status['acknowledged']==sequence:
            self.waiting=None
            if status['state']==4 and not status['error']:
                if self.dialog is not None:self.dialog.dismiss()
            else:self.loading_message=NAVIGATION_FAILURES.get((status['state'],status['error']),
                '赛事菜单尚未显示（%d/%d），请返回后重试。'%(status['state'],status['error']))
            self.navigation_failed=bool(status['state']!=4 or status['error'])
            return
        now=time.monotonic()
        if self.menu_loaded_at is not None:
            if now-self.menu_loaded_at>=3:
                print('EFOOTBALL_NAV menu loaded by the game; loading page closed',flush=True)
                channel.navigation_cancel(sequence,target)
                self.waiting=None
                if self.dialog is not None:self.dialog.dismiss()
            return
        if self._navigation_started is None:self._navigation_started=self._navigation_attempt_at=now
        if now-self._navigation_started>=NAVIGATION_TIMEOUT_SECONDS:
            channel.navigation_cancel(sequence,target)
            self.waiting=None
            self.navigation_failed=True
            self.loading_message='进入赛事菜单超时，可以重试或返回后重新选择锦标赛。'
            return
        if now-self._navigation_attempt_at>=NAVIGATION_RETRY_SECONDS and self._navigation_retries<NAVIGATION_RETRY_LIMIT:
            self._navigation_attempt_at=now
            self._navigation_retries+=1
            retry=getattr(channel,'navigation_retry',None)
            if retry and retry(sequence,target):
                updated=channel.navigation_status()
                if updated['event']==target and updated['sequence']!=sequence:
                    self.waiting=(channel,updated['sequence'],target)
                    self._navigation_keepalive_at=0.
                    self._navigation_reported=None
                    print('EFOOTBALL_NAV retry event=%d attempt=%d'%(target,self._navigation_retries),flush=True)
                    return
        if now-self._navigation_keepalive_at>=5:
            channel.navigation_keepalive(sequence,target)
            self._navigation_keepalive_at=now


NAVIGATION_FAILURES={
    (5,1):'游戏长时间没有进入赛事菜单，请返回后重试。',
    (6,2):'游戏赛事列表中没有这项锦标赛，请返回活动页后重新选择。',
    (7,3):'游戏赛事菜单无法返回列表，请返回后在活动页重新选择。',
    (7,4):'游戏赛事列表无法进入本赛事，请返回后重试。',
    (9,0):'已取消进入赛事。'}


def list_return_url(url):
    """The tournament list URL when ``url`` is the list or a hub opened from
    it (list=1); otherwise None (返回 then goes back to the game)."""
    if not url:return None
    try:
        parts=urlsplit(url)
        prefix='/player-studio/tournaments'
        if not parts.path.startswith(prefix):return None
        query=parse_qsl(parts.query)
        if parts.path!=prefix and ('list','1')not in query:return None
        query=[(k,v)for k,v in query if k!='list']
        return urlunsplit((parts.scheme,parts.netloc,prefix,urlencode(query),''))
    except ValueError:return None


def open_in_game(url, uid):
    global _HOST
    url = local_schedule_url(url, uid)
    if _HOST is None:
        try:
            _HOST = AndroidScheduleHost()
        except Exception as exc:
            _event('host_create_failed',error=type(exc).__name__+': '+str(exc))
            raise
    return _HOST.open(url)


def begin_loading():
    global _HOST
    try:
        if _HOST is None:_HOST=AndroidScheduleHost()
        if (_HOST.pending or _HOST.shown)and not _HOST.loading:return False
        return _HOST.show_loading()
    except Exception:
        LOG.debug('Tournament loading host unavailable',exc_info=True)
        return False


def dispatch_saved_schedule(command, root, uid, overview_url, names=None, opener=None, click=None):
    """Early native-request adapter. Never enter runtime.state/next_fixture.

    Returns None for unrelated commands or an unsaved event. The caller must
    retain its original native flow until the specific menu replacement is
    validated. A queued UI is explicitly not reported as device-tested success.
    """
    if command not in (ENTRY_EVIDENCE['schedule_request'],ENTRY_EVIDENCE['challenge_schedule_request']):
        return None
    if not isinstance(click,ScheduleClick) or not click.consume(uid):
        return None
    from tournament_schedule_ui import read_saved
    current = read_saved(root, uid)
    if current is None:
        return None
    url = local_schedule_url(overview_url.rstrip('/') + '/' + str(uuid.UUID(uid)), uid)
    queued = (opener or open_in_game)(url, uid)
    return {'uuid': uid, 'run_id': current['run_id'], 'queued': bool(queued),
            'device_validated': False, 'requires_native_menu_integration': True}


def schedule_button_clicked(root, uid, overview_url, opener=None):
    """Preferred integration: consume the actual native button action itself.

    Call before the original action creates/forwards to its stock schedule
    menu, and skip that forwarding if queued. No schedule RPC is needed, so
    closing the Dialog reveals the original event menu rather than a blank
    stock schedule. This function is not automatically wired on import.
    """
    return dispatch_saved_schedule(ENTRY_EVIDENCE['schedule_request'],root,uid,overview_url,
                                   opener=opener,click=ScheduleClick(uid))


class ClickChannel:
    """Retained header/counters only; engine actor addresses are never read."""
    def __init__(self, buffer, offset, schema, fence):
        if schema!=CHANNEL_SCHEMA or not callable(fence) or type(offset)is not int or offset<0 or offset%16:
            raise ValueError('赛程点击通道协议无效')
        if offset+schema['bytes']>len(buffer):
            raise ValueError('赛程点击通道越界')
        self.buffer=buffer;self.offset=offset;self.fence=fence;self.last_result=None
        self.set_ready(False)

    def snapshot(self):
        self.fence()
        raw=bytes(memoryview(self.buffer).cast('B')[self.offset:self.offset+CHANNEL_SCHEMA['bytes']])
        self.fence()
        ui=lambda at:struct.unpack_from('<I',raw,at)[0]
        uq=lambda at:struct.unpack_from('<Q',raw,at)[0]
        diag=CHANNEL_SCHEMA['diagnostics'];gate=lambda n:DIAGNOSTIC_GATES[n-1] if 1<=n<=len(DIAGNOSTIC_GATES) else None
        version=ui(diag['version_offset'])
        return {'ready':ui(0),'pending':ui(4),'sequence':uq(8),'event_id':ui(16),
                'actor_present':bool(uq(24)),'acknowledged':uq(32),'last_dispatch':copy.deepcopy(self.last_result),
                'native_diagnostics_version':version,'last_gate':gate(ui(diag['last_gate'])),
                'last_reject':gate(ui(diag['last_reject'])),
                'observed':None if not version else {
                    'state':None if ui(76)==0xffffffff else ui(76),'event_id':ui(80),'ready':ui(84),
                    'record_count':ui(88),'forward_count':ui(92),'class_fname':uq(96),
                    'expected_class_fname':uq(104),'state_property_offset':struct.unpack_from('<i',raw,112)[0],
                    'fade_flag':ui(116)},
                'reached':{name:ui(diag['reached_offset']+i*4) for i,name in enumerate(DIAGNOSTIC_GATES)},
                'rejected':{name:ui(diag['rejected_offset']+i*4) for i,name in enumerate(DIAGNOSTIC_GATES)}}

    def set_ready(self, ready):
        self.fence()
        struct.pack_into('<I',self.buffer,self.offset,int(bool(ready)))
        self.fence()

    def poll(self, service, opener=None):
        self.fence()
        seq=struct.unpack_from('<Q',self.buffer,self.offset+8)[0]
        pending=struct.unpack_from('<I',self.buffer,self.offset+4)[0]
        event=struct.unpack_from('<I',self.buffer,self.offset+16)[0]
        ack=struct.unpack_from('<Q',self.buffer,self.offset+32)[0]
        self.fence()
        if not seq or not pending or seq==ack or seq!=struct.unpack_from('<Q',self.buffer,self.offset+8)[0]:
            return None
        _event('native_click_received',sequence=seq,event_id=event)
        try:
            if opener is None:begin_loading()
            definition=next((t for t in service.definitions() if t.get('event_id')==event and t.get('enabled',True)),None)
            if definition is None:
                result={'queued':False,'reason':'saved event no longer enabled'}
            else:
                url=service.overview_url()
                result=schedule_button_clicked(service.root,definition['uuid'],url,opener=opener) if url else {'queued':False,'reason':'content service unavailable'}
            self.last_result=result
            _event('dispatch_finished',sequence=seq,event_id=event,result=result,
                   reason='saved tournament unavailable' if result is None else None)
            return result
        except Exception as exc:
            self.last_result={'queued':False,'error':type(exc).__name__+': '+str(exc)}
            _event('dispatch_failed',sequence=seq,event_id=event,error=self.last_result['error'])
            raise
        finally:
            struct.pack_into('<Q',self.buffer,self.offset+32,seq)
            self.fence()
            struct.pack_into('<I',self.buffer,self.offset+4,0)
            self.fence()
            write_status(True)


def attach_descriptor(payload, hook, body_offset, writable_locations):
    """Used by compose() on desktop and Android; no build-only imports."""
    if not hook.get('tournament_schedule_channel'):
        return
    if payload.get('tournament_schedule_channel') or hook['tournament_schedule_channel']!=CHANNEL_SCHEMA or len(writable_locations)!=1 or writable_locations[0][1]!=CHANNEL_SCHEMA['bytes']:
        raise ValueError('赛程点击通道重复或布局不一致')
    at=hook.get('helper_entries',{}).get('studio_schedule_fence')
    raw=bytes.fromhex(hook['body_hex'])
    if type(at)is not int or at%4 or not 0<=at<=len(raw)-8 or raw[at:at+8]!=bytes.fromhex('bf3b03d5c0035fd6'):
        raise ValueError('赛程点击通道屏障入口无效')
    payload['tournament_schedule_channel']={'version':1,'revision':REVISION,'code_region':'root','fence_offset':body_offset+at,
                                           'data_offset':writable_locations[0][0],'schema':copy.deepcopy(CHANNEL_SCHEMA)}


def configure(service):
    """Called once by the tournament installer, after constructing Service."""
    global _SERVICE
    _SERVICE=service
    _event('service_configured')
    _start_worker()


def register_channel(buffer, offset, schema, fence):
    """Called only after the checked binding installer made its fence executable."""
    global _CHANNEL
    if _CHANNEL is not None:
        _CHANNEL.set_ready(False)
    _CHANNEL=ClickChannel(buffer,offset,schema,fence)
    _event('channel_registered',bytes=schema['bytes'],data_offset=offset)
    _start_worker()
    return _CHANNEL


def register_binding_channel(binding, prefix_address, extension_address):
    """Small integration seam for personal_binding._BindingCtypes.memmove."""
    try:
        descriptor=binding.payload['tournament_schedule_channel'];region=descriptor['code_region']
        if descriptor.get('version')!=1 or descriptor.get('revision')!=REVISION or region not in ('root','extension'):
            raise ValueError('赛程点击通道重定位信息无效')
        raw=bytes.fromhex(binding.payload['extension_blob_hex'] if region=='extension' else binding.payload['blob_hex'])
        at=descriptor['fence_offset'];base=extension_address if region=='extension' else prefix_address
        if not base or type(at)is not int or at%4 or not 0<=at<=len(raw)-8 or raw[at:at+8]!=bytes.fromhex('bf3b03d5c0035fd6'):
            raise ValueError('赛程点击通道内存屏障未核验')
        fence=binding.original.CFUNCTYPE(None)(base+at)
        return register_channel(binding.writable,descriptor['data_offset'],descriptor['schema'],fence)
    except Exception as exc:
        _event('channel_registration_failed',error=type(exc).__name__+': '+str(exc))
        raise


def _start_worker():
    global _WORKER
    if _CHANNEL is None or _SERVICE is None:
        with _STATUS_LOCK:
            _STATUS['ready_reason']='channel not registered' if _CHANNEL is None else 'service not configured'
        write_status(True)
        return
    if _WORKER is not None and _WORKER.is_alive():
        return
    def work():
        last_tick=0.
        while True:
            channel,service=_CHANNEL,_SERVICE
            try:
                _poll_native_entry(service)
                now=time.monotonic()
                if now-last_tick>=.1:
                    _worker_tick(channel,service);last_tick=now
                time.sleep(.016)
            except Exception as exc:
                channel.set_ready(False)
                with _STATUS_LOCK:
                    _STATUS.update(ready_reason='worker exception',worker_error=type(exc).__name__+': '+str(exc),worker_tick=time.time())
                _event('worker_failed',error=_STATUS['worker_error'])
                LOG.exception('Tournament click adapter unavailable; event menu retained')
                time.sleep(1)
    _WORKER=threading.Thread(target=work,name='studio-tournament-schedule',daemon=True)
    _WORKER.start()
    _event('worker_started')


def _worker_tick(channel,service):
    definitions=service.definitions();url=service.overview_url()
    enabled=bool(definitions) and bool(url)
    reason='ready' if enabled else ('no tournament definitions' if not definitions else 'content service unavailable')
    with _STATUS_LOCK:
        changed=_STATUS.get('ready_reason')!=reason
        _STATUS.update(ready_reason=reason,definition_count=len(definitions),
                       enabled_event_ids=[t.get('event_id') for t in definitions],
                       overview_available=bool(url),worker_tick=time.time(),worker_error=None)
    channel.set_ready(enabled)
    import tournament_home
    if tournament_home._CHANNEL is not None:tournament_home._CHANNEL.navigation_input_ready(enabled)
    if changed:_event('ready_changed',ready=enabled,reason=reason)
    snapshot=channel.snapshot();hits=snapshot['reached']['entry']
    with _STATUS_LOCK:
        previous=_STATUS.get('last_native_entry_count',0)
        _STATUS['last_native_entry_count']=hits
    if hits!=previous:
        _event('native_guard_activity',entry_count=hits,last_gate=snapshot['last_gate'],
               last_reject=snapshot['last_reject'],observed=snapshot['observed'])
    if enabled:channel.poll(service)
    write_status()


_ACTIVITY_LOCK=threading.Lock()
_ACTIVITY_PENDING=False
_ACTIVITY_LAST=0.


def navigation_visible(visible):
    import tournament_home
    if tournament_home._CHANNEL is not None:
        tournament_home._CHANNEL.navigation_visible(visible)


def request_native_menu(event):
    import tournament_home
    channel=tournament_home._CHANNEL
    return bool(channel is not None and channel.navigation_request(event))


def _poll_native_entry(service):
    import tournament_home
    channel=tournament_home._CHANNEL
    intent=channel.navigation_input()if channel is not None else None
    if intent is None:return False
    begin_loading()
    generation=_HOST.generation if _HOST is not None else None
    def resolve():
        try:
            if intent['event']==0:
                return_to_tournaments(service,generation=generation)
                return
            definition=next((t for t in service.definitions()if t.get('event_id')==intent['event']),None)
            if definition is not None:open_activity(service,definition['uuid'],generation=generation)
        except Exception:
            if _HOST is not None:_HOST.loading_message='赛事加载失败，请返回后重试。'
            LOG.exception('Tournament entry presentation failed')
    threading.Thread(target=resolve,name='studio-tournament-entry',daemon=True).start()
    return True


def return_to_tournaments(service,opener=None,generation=None):
    if generation is not None and(_HOST is None or _HOST.generation!=generation):return False
    if not service.definitions():return False
    base=service.overview_url()
    if not base:return False
    url=local_schedule_url(base,None)
    if _HOST is not None:_HOST.return_url=None
    queued=bool((opener or open_in_game)(url,None))
    if queued:_event('native_return_to_tournaments',url=url)
    return queued


def observe_activity_request(service,command,request):
    if command in ('CMD_GET_EVENT_LIST_AND_LOG','CMD_GET_EVENT_LIST_AND_INFO'):
        if getattr(service,'_hub_activity_previous',None)=='CMD_GET_MAINMENU_MATCH_CREATIVE_INFO'and service.definitions():
            return begin_loading()
    if command in ('CMD_SET_EVENT_ENTER','CMD_ENTER_EVENT_COMPE'):
        events={t.get('event_id')for t in service.definitions()}
        event=next((event for event in events if event is not None and any(str(request.get(key,''))==str(event)for key in('event_id','compe_unit_id'))),None)
        if event is not None:
            service._hub_loading_event=event
            return begin_loading()
    return False


def open_after_match(service):
    """A tournament result was just committed: show its hub over the result
    screen once the reply has reached the game; 返回 goes back to the game."""
    last=getattr(service,'last_result',None)
    if not last or time.monotonic()-last[1]>10:return False
    service.last_result=None;uid=last[0]
    base=service.overview_url()
    if not base:return False
    def show():
        time.sleep(2.5)
        try:open_in_game(base+'/'+uid,uid)
        except Exception:LOG.exception('Tournament hub after match was not opened; the game is unaffected')
    threading.Thread(target=show,name='studio-tournament-after-match',daemon=True).start()
    _event('after_match_open_queued',tournament=uid)
    return True


def observe_activity(service,command,response):
    if command=='CMD_GET_VSCOM_GAME_RESULT' and isinstance(response,dict) and response.get('result')=='NOERR':
        open_after_match(service)
    if command=='CMD_GET_GAMERESULT_LIST' and _HOST is not None and _HOST.loading and _HOST.waiting is not None:
        _HOST.menu_loaded_at=time.monotonic()
    with _ACTIVITY_LOCK:
        previous=getattr(service,'_hub_activity_previous',None)
        service._hub_activity_previous=command
    if command in ('CMD_SET_EVENT_ENTER','CMD_ENTER_EVENT_COMPE')and isinstance(response,dict)and response.get('result')=='NOERR':
        if not response.get('studio_choice_required')and _HOST is not None and _HOST.loading:
            import tournament_home
            channel=tournament_home._CHANNEL
            try:event=int(response.get('event_id',response.get('compe_unit_id',getattr(service,'_hub_loading_event',0))))
            except (TypeError,ValueError):event=0
            if channel is not None and channel.navigation_request(event,observe_only=True):_HOST.await_native_menu(event)
    if previous!='CMD_GET_MAINMENU_MATCH_CREATIVE_INFO' or command not in(
            'CMD_GET_EVENT_LIST_AND_LOG','CMD_GET_EVENT_LIST_AND_INFO'):
        return False
    if not isinstance(response,dict)or response.get('result')!='NOERR':return False
    return defer_activity(service)


def open_activity(service,uid=None,opener=None,generation=None):
    def cancelled():return generation is not None and(_HOST is None or _HOST.generation!=generation)
    if cancelled():return False
    definitions=service.definitions()
    if not definitions:return False
    if uid is not None and not any(t['uuid']==uid for t in definitions):return False
    if uid is None and len(definitions)==1:uid=definitions[0]['uuid']
    if uid is not None:
        worker=getattr(service,'hub_worker',None)
        targets=worker.call(action='targets',payload={})if worker is not None else []
        if cancelled():return False
        target=next((t for t in targets if t['uuid']==uid),None)
        if target and not target['needs_team'] and request_native_menu(target['event_id']):
            if _HOST is not None:_HOST.await_native_menu(target['event_id'])
            return True
    base=service.overview_url()
    if not base:return False
    if cancelled():return False
    if _HOST is not None and(_HOST.pending or _HOST.shown)and not _HOST.loading:return False
    url=local_schedule_url(base+('/'+uid if uid else ''),uid)
    return bool((opener or open_in_game)(url,uid))


def defer_activity(service,uid=None):
    """Cover a successfully delivered native activity list, leaving it underneath."""
    global _ACTIVITY_PENDING,_ACTIVITY_LAST
    from reply_jobs import defer
    now=time.monotonic()
    with _ACTIVITY_LOCK:
        if _ACTIVITY_PENDING or now-_ACTIVITY_LAST<.75:return False
        if _HOST is not None and(_HOST.pending or _HOST.shown)and not _HOST.loading:return False
        _ACTIVITY_PENDING=True
    begin_loading()
    generation=_HOST.generation if _HOST is not None else None
    def show():
        global _ACTIVITY_PENDING,_ACTIVITY_LAST
        try:
            open_activity(service,uid,generation=generation)
        except Exception:
            if _HOST is not None:_HOST.loading_message='赛事加载失败，请返回后重试。'
            LOG.exception('Tournament activity presentation failed')
        finally:
            with _ACTIVITY_LOCK:_ACTIVITY_PENDING=False;_ACTIVITY_LAST=time.monotonic()
    if defer(('tournament-hub-activity',str(service.root)),show):
        _event('activity_open_deferred',tournament=uid)
        return True
    with _ACTIVITY_LOCK:_ACTIVITY_PENDING=False
    _event('activity_open_not_queued',reason='request reply context unavailable',tournament=uid)
    return False
