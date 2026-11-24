"""Shared, non-destructive native player availability facts (stdlib only).

V17 exports a complete dt870 snapshot and activates that exact CPK. Its live
download is a second copy, not a separate record-level overlay. Within an
ordered mount set each table is replaced as a file; delete lists are never
unioned, and a Player row cannot cancel an effective deletion by itself.
"""
import copy,hashlib,json,sqlite3,uuid
from pathlib import Path

PLAYER='common/etc/pesdb/Player.bin'
DELETED='common/etc/pesdb/PlayerDeleteList.bin'
ACTIVE='custom-resources/legends-v10/dt870_mobile_and.cpk'
LIVE='livedata/dt870_mobile_and.cpk'
REVISION='v17-effective-player-tables-v2'
APTITUDE_BITS=(414,584,318,592,594,510,588,576,580,590,596,598,586)


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def resolve(layers,recycled=()):
    """Resolve ordered *file* replacements, then apply independent exclusions."""
    winners={};sources=[]
    for name,tables in layers:
        source={'name':name,'tables':{}}
        for key in(PLAYER,DELETED):
            if key not in tables:continue
            raw=tables[key]
            if not isinstance(raw,bytes):raise ValueError('球员目录资料不是二进制表')
            source['tables'][key]=hashlib.sha256(raw).hexdigest();winners[key]=(name,raw)
        sources.append(source)
    if set(winners)!={PLAYER,DELETED}:raise ValueError('有效球员目录缺少最终球员表或原生删除名单，未沿用旧白名单')
    player=winners[PLAYER][1];deleted=winners[DELETED][1]
    if len(player)%400 or len(deleted)%8:raise ValueError('有效球员目录原生表步长改变')
    ids=[];seen=set();incomplete=set();membership={}
    for at in range(0,len(player),400):
        ident=int.from_bytes(player[at+8:at+16],'little')
        if not 0<ident<2**64 or ident in seen:raise ValueError('最终球员表编号无效或重复')
        seen.add(ident);ids.append(ident)
        bits=int.from_bytes(player[at:at+400],'little')
        membership[ident]={'club':(bits>>128)&0xffffffff,'nation':(bits>>329)&511}
        if not 40<=((bits>>248)&255)<=120 or ((bits>>556)&15)>=13 or any(((bits>>bit)&3)>2 for bit in APTITUDE_BITS):incomplete.add(ident)
    removed={int.from_bytes(deleted[at:at+8],'little')for at in range(0,len(deleted),8)}
    if any(not 0<ident<2**64 for ident in removed):raise ValueError('最终原生删除名单编号无效')
    trash=set(map(int,recycled));excluded={}
    for ident in sorted(seen):
        reasons=[]
        if ident in removed:reasons.append('native_deleted')
        if ident in trash:reasons.append('workshop_recycled')
        if ident in incomplete:reasons.append('incomplete_native_record')
        if reasons:excluded[str(ident)]=reasons
    result={'version':2,'rule_version':REVISION,'resolution':'last-table-file-wins; delete-after-player-lookup',
        'sources':sources,'table_sources':{key:value[0]for key,value in winners.items()},
        'table_sha256':hashlib.sha256(player).hexdigest(),'delete_table_sha256':hashlib.sha256(deleted).hexdigest(),
        'source_card_ids':sorted(seen),'card_ids':sorted(seen-set(map(int,excluded))),
        'native_deleted_ids':sorted(removed),'exclusions':excluded,
        'exclusion_metadata':{key:membership[int(key)]for key in excluded}}
    result['revision']=digest(result)
    return result


def from_cpk(raw,document=None,*,name=ACTIVE):
    from native_cpk import Cpk,unwrap
    cpk=Cpk(raw);tables={key:unwrap(cpk.read(key))for key in(PLAYER,DELETED)if key in cpk.files}
    result=resolve([(name,tables)],(r['id']for r in(document or{}).get('catalogue_recycle_bin',[])))
    result['snapshot_sha256']=hashlib.sha256(raw).hexdigest()
    result['revision']=digest({k:v for k,v in result.items()if k!='revision'})
    return result


def from_reader(reader,document=None,prefix='assets/gateway-release/'):
    active=reader.read(prefix+ACTIVE,limit=None)
    # This is the factory's checked V17 activation contract: same physical
    # snapshot for native activation and live download, never a deletion union.
    live=reader.read(prefix+LIVE,limit=None)
    if hashlib.sha256(active).digest()!=hashlib.sha256(live).digest():raise ValueError('激活与下载球员资料不同，无法建立有效目录')
    return from_cpk(active,document)


def ids(document):
    index=document.get('native_player_catalogue')
    if index is None:return None
    if not isinstance(index,dict)or index.get('version')not in(1,2)or not isinstance(index.get('card_ids'),list):
        raise ValueError('原生球员索引无效，未使用过期卡池')
    values=index['card_ids']
    if any(type(cid)is not int or not 0<cid<2**64 for cid in values)or len(set(values))!=len(values):raise ValueError('原生球员索引编号无效或重复')
    if index['version']==2:
        # Runtime overlays are reconstructed from actual CPK bytes. Do not
        # mistake a later DIY definition for proof of a restored native row.
        deleted=set(index.get('native_deleted_ids',[]));blocked=set(map(int,index.get('exclusions',{})))
        if set(values)&(deleted|blocked):raise ValueError('有效目录包含已排除球员')
    return frozenset(values)


def reason(document,ident,*,valid=None):
    index=document.get('native_player_catalogue')or{}
    if str(ident)in index.get('exclusions',{}):return index['exclusions'][str(ident)]
    if valid is None:valid=ids(document)
    return []if valid is None or ident in valid else['missing_final_player_record']


def from_library(library,document):
    """Read only committed source paths, without reimporting/editing the DB."""
    from apk_sources import path_for_resources,packages_for_resources,file_stamp
    from resource_set import Reader
    resources=getattr(library,'resources',None)
    if resources is None:return document.get('native_player_catalogue')  # mathematical fixtures
    try:source=path_for_resources(resources)
    except ValueError:
        if not(Path(resources)/'import-report.json').is_file():return document.get('native_player_catalogue')
        raise
    packages=packages_for_resources(resources)
    key=digest([file_stamp(source),[(k,file_stamp(p))for k,p in sorted(packages.items())],document.get('catalogue_recycle_bin',[])])
    cache=getattr(library,'_effective_player_catalogue',None)
    if cache and cache[0]==key:return copy.deepcopy(cache[1])
    with Reader(source,packages,verify_identities=False)as reader:catalogue=from_reader(reader,document)
    library._effective_player_catalogue=(key,catalogue)
    return copy.deepcopy(catalogue)


def runtime_overlay(root,document):
    """Reuse the installed release's directory until its resource files change."""
    root=Path(root);active=root/ACTIVE;live=root/LIVE
    if not active.is_file()or not live.is_file():raise ValueError('配套球员资料缺失，有效目录准备未完成，原存档保留')
    def stamp(path):
        value=path.stat()
        return [value.st_size,value.st_mtime_ns,value.st_ctime_ns,value.st_ino]
    declared=document.get('native_player_catalogue')or{}
    binding={'version':1,'root':str(root.resolve()),'document':document.get('revision'),
             'catalogue':declared.get('revision'),'files':[stamp(active),stamp(live)]}
    cache_path=root/'outputs/efootball/effective-player-runtime-v1.json'
    cache=None
    try:
        if cache_path.stat().st_size<=16*1024*1024:cache=json.loads(cache_path.read_text(encoding='utf8'))
    except (OSError,ValueError,TypeError):pass
    if isinstance(cache,dict)and cache.get('binding')==binding:
        catalogue=cache.get('catalogue',declared)
        if isinstance(catalogue,dict)and catalogue.get('version')==2:
            result=copy.copy(document);result['native_player_catalogue']=catalogue
            return result
    metadata=document.get('resource_files',{}).get('dt870_mobile_and.cpk',{})
    installed=(declared.get('version')==2 and declared.get('rule_version')==REVISION
        and bool(declared.get('revision'))and bool(document.get('revision'))
        and all(row[0]==metadata.get('bytes')for row in binding['files']))
    previous_binding=cache.get('binding')if isinstance(cache,dict)else None
    unchanged_release=not isinstance(previous_binding,dict)or previous_binding.get('document')!=binding['document']
    if installed and unchanged_release:
        catalogue=declared;record={'binding':binding}
    else:
        raw=active.read_bytes()
        if live.read_bytes()!=raw:raise ValueError('配套球员资料副本不一致，原存档保留')
        catalogue=from_cpk(raw,document);record={'binding':binding,'catalogue':catalogue}
    temporary=None
    try:
        cache_path.parent.mkdir(parents=True,exist_ok=True)
        temporary=cache_path.with_name(cache_path.name+'.'+uuid.uuid4().hex+'.tmp')
        temporary.write_text(json.dumps(record,ensure_ascii=False,separators=(',',':')),encoding='utf8')
        temporary.replace(cache_path)
    except OSError:
        if temporary is not None:
            try:temporary.unlink(missing_ok=True)
            except OSError:pass
    result=copy.copy(document);result['native_player_catalogue']=catalogue
    return result


def backup_before_upgrade(conn,catalogue):
    """Consistent SQLite backup, before the first v2 migration write."""
    key='effective_player_catalogue_backup_v2'
    if conn.execute('select 1 from meta where key=?',(key,)).fetchone():return
    if conn.in_transaction:raise ValueError('目录升级需要已提交的存档边界，未提交或撤销调用方事务')
    filename=next((r[2]for r in conn.execute('pragma database_list')if r[1]=='main'and r[2]),None)
    if filename:
        folder=Path(filename).parent/'studio-catalogue-backups';folder.mkdir(exist_ok=True)
        target=folder/('before-effective-v2-'+catalogue['revision'][:12]+'-'+uuid.uuid4().hex[:12]+'.sqlite3')
        if not target.exists():
            temporary=target.with_suffix('.pending');backup=sqlite3.connect(temporary)
            try:
                conn.backup(backup)
                if backup.execute('pragma quick_check').fetchone()[0]!='ok':raise ValueError('目录升级前存档备份校验失败')
            finally:backup.close()
            temporary.replace(target)
    with conn:conn.execute('insert or ignore into meta values(?,?)',(key,json.dumps({'revision':catalogue['revision'],'owned_cards_removed':False})))


class RosterUnavailable(ValueError):
    def __init__(self,team,count,details=None):
        self.details={'team_id':int(team),'effective_people':count,'required_people':11,**(details or{})}
        super().__init__('球队 '+str(team)+' 当前有效球员 '+str(count)+' 人，无法组成11人首发，缺 '+str(max(0,11-count))+' 人；等待资料修复')


def verify_apk(path,resource_packages=None,*,observed=None,expected=None):
    from resource_set import Reader
    reuse=observed is not None and expected is not None
    if reuse:
        from export_payload import identity as file_identity
        files=observed.get('files')or{}
        if not observed.get('verified')or str(Path(path))not in files or any(file_identity(Path(name).stat())!=stamp for name,stamp in files.items()):
            raise ValueError('已核对的导出资源发生变化')
    with Reader(path,packages=resource_packages)as reader:
        document=json.loads(reader.read('assets/gateway-release/custom-resources/personal-player-studio/players.json',limit=None))
        if reuse:
            actual=expected
            if actual.get('snapshot_sha256')!=observed.get('sha256'):raise ValueError('球员目录与已核对的 CPK 不一致')
        else:actual=from_reader(reader,document)
    if actual!=document.get('native_player_catalogue'):raise ValueError('最终 APK 有效球员目录与实际配套资源不一致')
    return {'verified':True,'revision':actual['revision'],'effective_cards':len(actual['card_ids']),
            'native_deleted_cards':len(actual['native_deleted_ids']),'actual_resource_readback':True,
            'shared_resource_verification':reuse}


def roster_facts(document,team,people):
    """Explain legacy shortages from native records, without granting cards."""
    from tournament_identity import native_person_key
    directory=document.get('native_player_catalogue')or{};nation=document.get('tournament_national_team_nations',{}).get(str(team))
    reasons={};card_ids={}
    for cid,member in directory.get('exclusion_metadata',{}).items():
        belongs=member.get('nation')==nation if nation is not None else member.get('club')==int(team)
        if not belongs:continue
        pid=native_person_key(int(cid))
        if pid in people:continue
        for reason in directory['exclusions'].get(cid,[]):
            reasons.setdefault(reason,set()).add(pid);card_ids.setdefault(reason,[]).append(int(cid))
    removed=set().union(*reasons.values())if reasons else set()
    return {'source_people':len(people)+len(removed),'excluded_people_by_reason':{k:len(v)for k,v in reasons.items()},
        'excluded_card_ids_by_reason':card_ids,'catalogue_revision':directory.get('revision')}
