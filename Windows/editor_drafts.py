"""Persist partial edits and record whether their exact values were committed."""
import copy
import json
import os
import time
import uuid

CATEGORIES={'coach','pack','settings','progress','world'}


def listing(store):
    path=store.root/'editor-drafts.json'
    with store.lock:
        return json.loads(path.read_text(encoding='utf8')) if path.exists() else {}


def _write(store,rows):
    path=store.root/'editor-drafts.json';temp=path.with_name('.draft-'+uuid.uuid4().hex)
    try:
        with temp.open('w',encoding='utf8') as f:
            json.dump(rows,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        if temp.exists():temp.unlink()


def _commit(store,category,value):
    if category=='coach':
        # The caller supplies the current prepared head model. The normal
        # store validates models/portraits without reloading the editor.
        return store.coaches.save(value)
    if category=='pack':
        import packs
        return packs.save(store,value)
    if category=='settings':
        from game_settings import settings
        return {'value':settings(store,value)}
    if category=='progress':
        from workspace_tools import game_options
        return {'value':game_options(store,value)}
    import world_workspace
    return world_workspace.save(store,value)


def update(store,category,value,commit=True):
    if category not in CATEGORIES:raise ValueError('未知编辑草稿')
    if value is not None and not isinstance(value,dict):raise ValueError('编辑草稿须为对象')
    key=str(uuid.UUID(value['uuid'])) if category in ('coach','pack') and value else category
    if len(json.dumps(value).encode())>2*1024*1024:raise ValueError('编辑草稿过大')
    value=copy.deepcopy(value);draft_key=category+':'+key
    # Receipt and business commit share one lock: a slow older request cannot
    # overwrite a newer business object after the newer receipt was issued.
    with store.lock:
        rows=listing(store);previous=rows.get(draft_key) or {}
        same=previous.get('value')==value
        revision=previous.get('revision')if same else None
        row={'category':category,'value':value,'saved_at':time.time_ns(),'revision':revision or uuid.uuid4().hex,'committed':False}
        if same:
            row['saved_at']=previous.get('saved_at',row['saved_at'])
            if not commit and previous.get('committed'):row['committed']=True
        rows[draft_key]=row;_write(store,rows)
        result={'saved':True,'committed':row['committed'],'draft_key':draft_key,'revision':row['revision']}
        if not commit or value is None:return result
        try:
            result.update(_commit(store,category,value))
            result['committed']=row['committed']=True
        except (ValueError,KeyError,TypeError,OSError) as exc:
            result['message']=row['message']=str(exc)
        rows[draft_key]=row;_write(store,rows)
        return result


def require_committed(store,keys):
    """Reject export of a selected object whose current draft is invalid.

    Rows written before this receipt existed carry no 'committed' field; they
    are re-validated once instead of being trusted or rejected blindly.
    """
    with store.lock:
        rows=listing(store)
        for key in keys:
            row=rows.get(key)
            if not row or row.get('committed') or row.get('value') is None:continue
            result=update(store,row['category'],row['value'])
            if not result['committed']:
                labels={'coach':'教练','pack':'卡包','settings':'游戏设置','progress':'进度设置','world':'锦标赛与世界设置'}
                raise ValueError('当前编辑的'+labels.get(row['category'],'内容')+'尚不能应用：'
                                 +result.get('message','请完善资料')+'。请修正后再导出，未使用旧版本代替。')


def remove(store,category,identity):
    key=category+':'+str(uuid.UUID(identity))
    with store.lock:
        rows=listing(store)
        if key not in rows:return
        del rows[key];_write(store,rows)
