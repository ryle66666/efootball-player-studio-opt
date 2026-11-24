"""Relevant SQLite changes and reusable tournament preparation inputs."""
import copy
import json
import sqlite3
import threading
import uuid

TABLE='studio_tournament_source_versions_v1'
TABLES={'managed_cards':('catalogue',('card_id','fingerprint','payload')),
        'excluded_catalog_ids':('catalogue',('card_id','reason')),
        'coaches':('coaches',('coach_id','serial','json')),
        'gameplayers':('owned',()),'careerplan':('owned',()),'warehouse':('owned',()),'squads':('owned',())}
DOCUMENT_KEYS=('players','native_player_catalogue','tournament_standard_cards',
 'tournament_national_team_nations','division_squads','preview_boosters',
 'native_booster_catalogue','coaches','tournament_fallback_standard_cards','catalogue_recycle_bin','revision')


def _schema(conn):
    names={row[0]for row in conn.execute("select name from sqlite_master where type='table'")}
    return {name:{row[1]for row in conn.execute('pragma table_info('+name+')')}for name in TABLES if name in names}


def _triggers(schema):
    for table,(kind,columns)in TABLES.items():
        if table not in schema or not set(columns)<=schema[table]:continue
        for operation in('insert','update','delete'):
            condition=''
            if operation=='update'and columns:condition=' WHEN '+' OR '.join('OLD.'+key+' IS NOT NEW.'+key for key in columns)
            name='studio_tournament_source_'+table+'_'+operation+'_v1'
            yield name,('CREATE TRIGGER IF NOT EXISTS '+name+' AFTER '+operation.upper()+' ON '+table+condition+
                        ' BEGIN UPDATE '+TABLE+" SET revision=revision+1,stamp=lower(hex(randomblob(16))) WHERE kind='"+kind+"'; END")


def initialize_versions(conn):
    """Install on the database owner, retaining its outer transaction."""
    conn.execute('savepoint studio_tournament_source_schema')
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS '+TABLE+'(kind TEXT PRIMARY KEY,revision INTEGER NOT NULL,stamp TEXT NOT NULL)')
        for kind in('catalogue','coaches','owned'):
            conn.execute('INSERT OR IGNORE INTO '+TABLE+' VALUES(?,0,?)',(kind,uuid.uuid4().hex))
        for _,sql in _triggers(_schema(conn)):conn.execute(sql)
        if conn.execute("select 1 from sqlite_master where name='meta'").fetchone():
            for operation in('insert','update','delete'):
                prefix='OLD'if operation=='delete'else'NEW'
                condition=prefix+".key IN ('main_squad_id','main_coach_id')"
                if operation=='update':condition='('+condition+" OR OLD.key IN ('main_squad_id','main_coach_id')) AND (OLD.key IS NOT NEW.key OR OLD.value IS NOT NEW.value)"
                conn.execute('CREATE TRIGGER IF NOT EXISTS studio_tournament_source_meta_'+operation+'_v1 AFTER '+operation.upper()+
                             ' ON meta WHEN '+condition+' BEGIN UPDATE '+TABLE+" SET revision=revision+1,stamp=lower(hex(randomblob(16))) WHERE kind='owned'; END")
        conn.execute('release studio_tournament_source_schema')
    except BaseException:
        conn.execute('rollback to studio_tournament_source_schema');conn.execute('release studio_tournament_source_schema');raise


class SourceContext:
    def __init__(self,document,document_version=None):
        self.document=document;self.document_version=document_version;self.lock=threading.RLock();self.schema_cache={}
        self.projection=None;self.document_base=None;self.document_serial='0'
        self.document_cache={}

    def version(self,conn,kinds=('catalogue','coaches','owned')):
        try:
            schema_version=conn.execute('pragma schema_version').fetchone()[0]
            with self.lock:cached=self.schema_cache.get(id(conn))
            if not cached or cached[0]is not conn or cached[1]!=schema_version:
                schema=_schema(conn)
                actual={row[0]for row in conn.execute("select name from sqlite_master where type='trigger' and name like 'studio_tournament_source_%_v1'")}
                required={name for name,_ in _triggers(schema)}
                if not required<=actual:return None
                with self.lock:
                    self.schema_cache[id(conn)]=(conn,schema_version)
                    while len(self.schema_cache)>8:self.schema_cache.pop(next(iter(self.schema_cache)))
            rows={kind:(revision,stamp)for kind,revision,stamp in conn.execute('select kind,revision,stamp from '+TABLE)}
            if not set(kinds)<=rows.keys():return None
            return tuple((kind,*rows[kind])for kind in kinds)
        except sqlite3.OperationalError as failure:
            if 'no such table'in str(failure).lower():return None
            raise

    def document_token(self,document=None):
        """Reuse explicitly versioned configurations; compare mutable callbacks."""
        value=self.document()if document is None else document
        supplied=self.document_version()if self.document_version is not None else None
        key=None if supplied is None else (supplied,id(value))
        with self.lock:
            cached=self.document_cache.get(key)if key is not None else None
            if cached is not None and cached[0]is value:return cached[1]
        projection={key:value.get(key)for key in DOCUMENT_KEYS}
        base=(value.get('revision'),(value.get('native_player_catalogue')or{}).get('revision'))
        with self.lock:
            if self.projection!=projection:
                self.document_serial='0'if self.document_base!=base else uuid.uuid4().hex
                self.projection=copy.deepcopy(projection);self.document_base=base
            token=json.dumps(projection,ensure_ascii=False,sort_keys=True,separators=(',',':'))if not any(base)else repr((base,self.document_serial))
            if key is not None:
                self.document_cache[key]=(value,token)
                while len(self.document_cache)>2:self.document_cache.pop(next(iter(self.document_cache)))
            return token

    @staticmethod
    def legacy_rows(conn,kinds):
        schema=_schema(conn);rows=[]
        for table,(kind,columns)in TABLES.items():
            if kind not in kinds or table not in schema:continue
            selected=','.join(columns)if columns else'*'
            rows.append((table,tuple(tuple(row)for row in conn.execute('select '+selected+' from '+table+' order by rowid'))))
        return tuple(rows)

    def token(self,conn,document=None,kinds=('catalogue','coaches','owned')):
        version=self.version(conn,kinds)
        if version is None:version=('legacy',self.legacy_rows(conn,kinds))
        return repr((version,self.document_token(document)))
