"""Run queued background work after the HTTP response has been flushed."""
import logging
import threading
import time

_local=threading.local()


def defer(key,callback):
    pending=getattr(_local,'pending',None)
    if pending is None:return False
    pending[key]=callback
    return True


def _run(callbacks):
    for callback in callbacks:
        try:callback()
        except Exception:logging.getLogger(__name__).exception('Deferred tournament work remains available for retry')


def install_gateway(namespace):
    """Retain deferred jobs across the Android Java-to-Python request call."""
    previous=namespace.get('handle')
    if not callable(previous) or getattr(previous,'_studio_gateway_reply',False):return
    def handle(*arguments,**options):
        if getattr(_local,'pending',None) is not None:
            return previous(*arguments,**options)
        _local.pending={}
        try:
            result=previous(*arguments,**options)
            callbacks=list(_local.pending.values()) if isinstance(result,(bytes,bytearray,memoryview)) and result else []
        finally:
            del _local.pending
        if callbacks:
            def dispatch():
                time.sleep(.15)
                _run(callbacks)
            threading.Thread(target=dispatch,name='tournament-after-gateway-response',daemon=True).start()
        return result
    handle._studio_gateway_reply=True
    namespace['handle']=handle


def install():
    from http.server import BaseHTTPRequestHandler
    previous=BaseHTTPRequestHandler.handle_one_request
    if getattr(previous,'_studio_after_reply',False):return
    def handle(handler):
        outer=getattr(_local,'pending',None)
        if outer is not None:return previous(handler)
        _local.pending={}
        handler._studio_reply_status=200
        previous_response=handler.send_response
        def send_response(code,message=None):
            handler._studio_reply_status=code
            return previous_response(code,message)
        handler.send_response=send_response
        try:
            result=previous(handler)
            if not handler.wfile.closed:handler.wfile.flush()
            callbacks=list(_local.pending.values())if 200<=handler._studio_reply_status<400 else[]
        finally:
            handler.send_response=previous_response
            del _local.pending
        if callbacks:
            threading.Thread(target=_run,args=(callbacks,),name='tournament-after-response',daemon=True).start()
        return result
    handle._studio_after_reply=True
    BaseHTTPRequestHandler.handle_one_request=handle
