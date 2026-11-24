"""League names and membership from the game's native competition tables."""
import copy
import json
from pathlib import Path
import struct
import threading

_cache={}
_lock=threading.RLock()
PREFIX='assets/gateway-release/'


def parse_competitions(unit,entry,known=None):
    if len(unit)%2472 or len(entry)%12:raise ValueError('原生赛事表结构不匹配')
    units={}
    for at in range(0,len(unit),2472):
        row=unit[at:at+2472];identity=int.from_bytes(row[10:12],'little')
        if identity in units:raise ValueError('原生赛事编号重复')
        labels=[]
        for position in (1551,1781):
            slot=row[position:position+115]
            if b'\0'not in slot:raise ValueError('原生赛事名称格式无效')
            labels.append(slot.split(b'\0',1)[0].decode('utf8'))
        chinese,english=labels
        units[identity]={'id':str(identity),'name':chinese or english or str(identity),
                         'name_en':english or chinese,'team_ids':[]}
    for packed_team,packed_unit,flags in struct.iter_unpack('<III',entry):
        team,identity=packed_team&131071,packed_unit>>16
        if team>0 and identity in units and(known is None or team in known):
            if team not in units[identity]['team_ids']:units[identity]['team_ids'].append(team)
    return {'source':'native-competition-tables','competitions':{row['id']:row for row in units.values()if row['team_ids']}}


def from_archive(archive):
    from native_cpk import Cpk,unwrap
    selected={};members=set(archive.namelist())
    for path in ('assets/dt200_mobile_all.cpk',PREFIX+'livedata/dt870_mobile_and.cpk',
                 PREFIX+'custom-resources/legends-v10/dt870_mobile_and.cpk'):
        if path not in members:continue
        raw=archive.read(path,limit=None)if hasattr(archive,'file_payload')else archive.read(path)
        package=Cpk(raw)
        for name in ('CompetitionUnit.bin','CompetitionEntry.bin'):
            key='common/etc/pesdb/'+name
            if key in package.files:selected[name]=unwrap(package.read(key))
    if len(selected)!=2:raise ValueError('游戏缺少原生赛事名称或球队成员表')
    return parse_competitions(selected['CompetitionUnit.bin'],selected['CompetitionEntry.bin'])


def for_library(library):
    path=library.resources/'team-leagues.json'
    if path.is_file():
        value=json.loads(path.read_text('utf8'))
        if value.get('source')=='native-competition-tables':return value
    from apk_sources import recorded,resource_paths
    if not getattr(library,'store',None):return {'source':'native-competition-tables','competitions':{}}
    source=recorded(library,library.store).get('apk')
    if not source or not Path(source).is_file():return {'source':'native-competition-tables','competitions':{}}
    stat=Path(source).stat();key=(str(Path(source).resolve()),stat.st_size,stat.st_mtime_ns)
    with _lock:
        if key in _cache:return copy.deepcopy(_cache[key])
    from resource_set import Reader
    with Reader(source,resource_paths(library,library.store,source)) as archive:value=from_archive(archive)
    with _lock:_cache.clear();_cache[key]=value
    return copy.deepcopy(value)
