"""Build a signed V14 APK with native personal resources and a progress hook."""
from archive_data import read_json as read_archive_json
from export_errors import call as _export_data
from pathlib import Path
from export_errors import export_context, exported, call as export_call, failure
import copy
import contextlib
from collections import ChainMap
import hashlib
import importlib.util
import io
import json
import marshal
import os
import shutil
import sqlite3
import struct
import sys
import threading
import time
import uuid
import zipfile
from export_options import selection as selected_ids
from export_recovery import Recovery, ExportCancelled, disabled, skipped, retain_selection, pause_message, cause

from native_cpk import Cpk,unwrap,cached_export,decode_statistics
from native_tables import PLAYER,rows,compile_tables
from native_player import reserve_identity,compile_player
from native_art import compile_art
from native_iostore import Container,append_container
from apk_format import replace_entries,local_signer,sign_v2,verify_v2,UINT32_LIMIT
from export_payload import FilePayload,measure,metadata as payload_metadata,verified_engine,verify_replacements
from export_timing import ExportTimings
from packs import load as load_packs,compile_packs
from native_live import reserve_shared,compile_template,verify_template
from personal_binding import reserve_asset_key,compose,fingerprint
from coaches import CoachStore
from native_coaches import compile_coaches
from overall import maximum_development
from native_v14_repairs import card_series
from native_models import person_keys
from player_identity_binding import owned_person_aliases
import personal_source
from custom_boosters import issues as booster_issues,validate_projects as validate_boosters,canonical as booster_definition,context as booster_context,reserve_booster

PREFIX='assets/gateway-release/'
NATIVE='custom-resources/legends-v10/'
COMPILER_REVISION='v17-selected-273-rebuild-3.0.16'


def compact(value):return json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf8')


def _reserve_live_version(library,store,release,template,fingerprint):
    """One real export observes its selected source and current library APK."""
    from native_live import library_version
    observed=library_version(library,store)
    shared=library.home.parent/'.player-studio-shared/native-live-versions.json'
    local=store.root/'native-live-versions.json'
    seeds=[library.home.parent/name/'data/native-live-versions.json'for name in('球员工坊','球员工坊-V14','球员工坊-V15')]
    return reserve_shared(shared,local,release,template,fingerprint,seeds,observed_versions=(observed,))


def _claim_export(store):
    """Nonblocking per-store exclusion also covers a newly imported UI instance."""
    path=store.root/'active-apk-export.lock';path.parent.mkdir(parents=True,exist_ok=True)
    stream=path.open('a+b')
    try:
        if stream.seek(0,2)==0:stream.write(b'\0');stream.flush()
        stream.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        return stream
    except OSError as exc:
        stream.close();raise ValueError('已有 APK 实装任务正在进行或收尾，请稍后重试')from exc


def _release_export(stream):
    try:
        stream.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
    finally:stream.close()


def bytecode(source,filename):
    code=compile(source,filename,'exec',dont_inherit=True)
    return importlib.util.MAGIC_NUMBER+struct.pack('<III',0,0,len(source.encode('utf8')))+marshal.dumps(code)


from runtime_modules import WORLD_HELPERS


def competition_menu_hooks(engine,resources,menu_sha256,tournaments):
    """The original League cache bridge is needed even without custom events."""
    from native_tournament_home import hooks as home_hooks
    result=home_hooks(engine,resources,menu_sha256=menu_sha256)
    if tournaments:
        from native_tournament_schedule_ui_entry import hooks as schedule_entry_hooks
        result=schedule_entry_hooks(engine,[t['event_id']for t in tournaments],resources,menu_sha256=menu_sha256)+result
    return result

def hook_archive(raw,runtime_source,binding_source=None,division_source=None,rating_source=None,helper_sources=None):
    src=zipfile.ZipFile(io.BytesIO(raw));stub=src.read('embedded_gateway.pyc')
    if stub[:4]!=importlib.util.MAGIC_NUMBER:raise ValueError('游戏 Python 字节码版本不同，未修改 APK')
    # Keep the original protected loader and all its modules unchanged.
    source='import _protected_runtime\n_protected_runtime.exec_into(__name__, globals())\nimport personal_runtime\npersonal_runtime.install(globals())\n'
    dest=io.BytesIO()
    with zipfile.ZipFile(dest,'w') as out:
        def emit(name,raw,compress_type=zipfile.ZIP_DEFLATED):
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.compress_type=compress_type
            info.external_attr=0o100644<<16
            out.writestr(info,raw)
        for entry in src.infolist():
            if entry.filename in tuple(name+'.pyc'for name in WORLD_HELPERS)+('tournament_rewards.py','tournament_rewards.pyc','tournament_reward_ui.py','tournament_reward_ui.pyc','embedded_gateway.pyc','personal_runtime.pyc','personal_binding.pyc','division_squads.pyc','native_overall.pyc','ai_tactical_profiles.pyc','ai_division_sessions.pyc','ai_match_manager.pyc','ai_match_channel.pyc','ai_match_runtime.pyc','ai_match_state.pyc','action_trace.pyc','league_rules.pyc','art_registry.pyc','native_diy_binding.pyc','draw_policy.pyc','draw_journal.pyc','draw_v22.pyc'):continue
            out.writestr(entry,src.read(entry))
        emit('embedded_gateway.pyc',bytecode(source,'embedded_gateway.py'))
        emit('personal_runtime.pyc',bytecode(runtime_source,'personal_runtime.py'))
        if binding_source is not None:emit('personal_binding.pyc',bytecode(binding_source,'personal_binding.py'))
        if division_source is not None:emit('division_squads.pyc',bytecode(division_source,'division_squads.py'))
        if rating_source is not None:emit('native_overall.pyc',bytecode(rating_source,'native_overall.py'))
        for name,content in (helper_sources or {}).items():
            if name not in WORLD_HELPERS+('ai_tactical_profiles','ai_division_sessions','ai_match_manager','ai_match_channel','ai_match_runtime','ai_match_state','action_trace','league_rules','art_registry','native_diy_binding','draw_v22'):raise ValueError('Unknown game helper module')
            emit(name+'.pyc',bytecode(content,name+'.py'))
    return dest.getvalue()


def checked_players(projects,stage,operation):
    return [export_call('球员',project,stage,operation,project) for project in projects]


class NativePackage:
    def __init__(self,library,resources,runtime_source,profile_resources=None):
        self.library=library;self.resources=Path(resources);self.store=library.store;self.runtime_source=runtime_source
        self.profile_resources=Path(profile_resources or resources)
        self.lock=threading.Lock();self.state={'phase':'idle'}
        self._job_local=threading.local();self._job_serial=0
        self._cancel_event=threading.Event();self._resume_request=None
        try:
            resume=json.loads((self.store.root/'export-resume.json').read_text('utf8'))
            if resume.get('status','paused')in('working','paused')and isinstance(resume.get('request'),dict):
                self._resume_request=resume['request']
                self.state={'phase':'paused','message':'上次生成的工程和可用文件已保留，可以继续生成。','can_resume':True,
                            'warnings':resume.get('warnings',[]),'diagnostics':resume.get('diagnostics',[])}
        except (OSError,ValueError,TypeError,AttributeError):pass

    def status(self):
        with self.lock:return copy.deepcopy(self.state)

    def reference_model(self):
        # A custom mesh is compiled into the pc3600 carrier. Its bind poses
        # must come from that same carrier, independently of preview quality.
        path='PesMobile/Content/Assets/character/RealFace/SK_133543_Face.uasset'
        target=self.library.converted/Path(path).with_name('SK_133543_Face-bind-template-native.glb')
        if not target.is_file():
            self.update(message='自动准备原版脸模骨架与接合模板')
            from native_skeletal import mesh_glb
            assets=self.library.face_assets()
            container=next((c for c in assets.containers if c.path.stem=='pc3600_mobile_and'),None)
            if container is None:raise ValueError('离线资源包缺少原版脸模骨架模板')
            class Carrier:
                def raw(self,name):return container.read('../../../'+path)
            source=target.with_name('SK_133543_Face-bind-template.glb')
            source.parent.mkdir(parents=True,exist_ok=True);source.write_bytes(mesh_glb(Carrier(),path))
            self.library.texture_model(source,{'path':path})
            if not target.is_file():raise ValueError('原版骨架模板自动读取失败')
        return target

    def update(self,**values):
        with self.lock:
            job=getattr(getattr(self,'_job_local',None),'serial',None)
            if job is not None and job!=getattr(self,'_job_serial',job):return
            recovery=getattr(self._job_local,'recovery',None)
            if recovery is not None:
                if values.get('stage'):recovery.stage=values['stage']
                values.setdefault('warnings',copy.deepcopy(recovery.warnings))
            if job is not None and values.get('phase')in('done','error','paused','preserved'):
                self._job_local.terminal=copy.deepcopy(values)
                return
            self.state.update(values)

    def step(self,name,message=None):
        self._job_local.stage=name
        if getattr(self,'_cancel_event',None)and self._cancel_event.is_set():
            raise ExportCancelled('已暂停本次生成，工程和可用结果已保留。')
        self.update(stage=name,**({'message':message}if message else{}))

    def notice(self,code,item,message,*,error=None,detail=''):
        recovery=getattr(self._job_local,'recovery',None)
        if recovery is not None:recovery.warn(code,item,message,detail,error=error)

    def local_operation(self,stage,operation,*,fallback=None,snapshots=()):
        saved=[copy.deepcopy(value)for value in snapshots]
        for attempt in range(3):
            self.step(stage)
            try:return operation()
            except Exception as error:
                for value,previous in zip(snapshots,saved):
                    value.clear()
                    if isinstance(value,dict):value.update(copy.deepcopy(previous))
                    elif isinstance(value,set):value.update(previous)
                    else:value.extend(copy.deepcopy(previous))
                root=cause(error)
                if isinstance(root,ExportCancelled)or type(root).__name__=='SourceChanged':raise
                if isinstance(root,(OSError,TimeoutError,MemoryError))and attempt<2:
                    self.notice('retry_step',stage,'正在重试当前步骤，已完成的项目保留。',error=error)
                    continue
                if fallback is not None:
                    import logging
                    logging.warning('Export stage retained previous content: %s',stage,exc_info=True)
                    return fallback(error)
                raise

    def omit_item(self,body,kind,item,error):
        if isinstance(cause(error),ExportCancelled)or type(cause(error)).__name__=='SourceChanged':raise error
        ident=(item.get('package_id')or item.get('uuid'))if isinstance(item,dict)else str(item)
        label=((item.get('identity')or{}).get('name')or item.get('name')or ident)if isinstance(item,dict)else ident
        recovery=getattr(self._job_local,'recovery',None)
        if recovery is not None:
            recovery.skipped[kind].add(ident)
            recovery.warn('preserve_item',ident,label+'暂未写入，工程和原有内容已保留，其它项目继续。',
                          error=error)
        body.get('write_designs',{}).get(kind,{}).pop(ident,None)

    def preserve_feature(self,body,feature,error,value):
        recovery=getattr(self._job_local,'recovery',None)
        if recovery is not None:recovery.disable(feature,error)
        flags=body.setdefault('_recovery',{}).setdefault('disabled',[])
        if feature not in flags:flags.append(feature)
        option=body.get('export_settings',{}).get(feature)
        if isinstance(option,dict)and'enabled'in option:option['enabled']=False
        return value

    def library_appearance_donors(self,projects,appearances,checked):
        """Recover exact appearance references removed by a full export."""
        requested=set()
        for project in projects:
            value=(project.get('model') or {}).get('appearance_source_id')
            try:requested.add(int(value))
            except (TypeError,ValueError):continue  # Invalid donors are reported per player.
        missing=requested-appearances.keys()
        if not missing:return {}
        from native_tables import APPEARANCE
        from resource_set import Reader
        packages={row['id']:row['path'] for row in checked['library_resource_dependencies']}
        with Reader(checked['library_path'],packages=packages,verify_identities=False) as source:
            cpk=Cpk(source.read(PREFIX+NATIVE+'dt870_mobile_and.cpk',limit=None))
            library_rows=rows(unwrap(cpk.read(APPEARANCE)),64)
        return {ident:library_rows[ident] for ident in missing if ident in library_rows}

    def compile_selected_players(self,body,*,inherited,owners,records,appearances,catalog,costs,template,
                                 registry,booster_registry,asset_registry,occupied_boosters,cache=None):
        from native_appearance import donor as appearance_donor,patch as patch_appearance
        from player_identity_binding import owned_person_aliases
        from private_card_motion import prepare as private_motion
        occupied=set(records)|{key&0xfffffff for key in records}
        original_registry=copy.deepcopy(registry)
        original_boosters=copy.deepcopy(booster_registry)
        original_assets=copy.deepcopy(asset_registry)
        source_boosters={value for row in owners.values()for value in row['boosters']}
        seen={};templates={};completed=[]

        def compile_one(row):
            p=copy.deepcopy(row)
            validate_boosters([p],catalog['ABILITIES'])
            rig=self.model_binding(p)
            donor=appearance_donor(p,appearances,int(p['native_card']['source_id']))
            patch_appearance(appearances[donor],p.get('model',{}))
            active=[b for b in p.get('custom_boosters',[])if b['id']in p['booster_ids']]
            remap={}
            for booster in active:
                uid=booster['uuid'];design=booster_definition(booster)
                if uid in seen and seen[uid]!=design:
                    raise ValueError(booster_context(p,booster)+'：同一永久增能的定义不同')
                seen[uid]=design
                prior=inherited.get('editor_registries',{}).get('boosters',{}).get(uid)
                prior_alias=inherited.get('editor_registries',{}).get('boosters',{}).get(uid+':slot0')
                assigned=reserve_booster(uid,booster_registry,occupied_boosters|source_boosters,prior,
                    first_slot=booster['id']==p['booster_ids'][0],prior_alias=prior_alias)
                remap[booster['id']]=assigned;booster['id']=assigned
            p['booster_ids']=[remap.get(value,value)for value in p['booster_ids']]
            p['custom_boosters']=active
            own=owners.get(p['package_id'],{}).get('aliases',set())
            identity=reserve_identity(p,occupied-own,registry)
            occupied.update((identity['card_id'],*owned_person_aliases(identity)))
            nation=p['identity'].get('nation_id')
            team=p['identity'].get('team_id')
            if not nation:
                with sqlite3.connect(self.resources/'library.sqlite3')as db:
                    nation_row=db.execute('select id from identities where kind=? and name=?',
                        ('nation',p['identity']['nationality'])).fetchone()
                if not nation_row:raise ValueError('请选择原版国家或地区后再实装')
                nation=nation_row[0]
            if not team:raise ValueError('请选择原版球队后再实装')
            source_id=(p.get('source_player')or{}).get('id')or p['native_card'].get('source_id')
            source_record=records.get(int(source_id))if source_id else None
            definition=compile_player(p,catalog,costs,identity,template,int(nation),int(team),source_record,
                                      game_version=self.library.profile.get('game_version','V14'))
            reference=None;metadata={}
            if not(p.get('model',{}).get('native_source')or{}).get('path'):
                if 'reference'not in templates:templates['reference']=self.reference_model()
                if 'metadata'not in templates:
                    templates['metadata']=json.loads((self.resources/'native-face-template.json').read_text('utf8'))
                reference=templates['reference'];metadata=templates['metadata']
            asset_key=reserve_asset_key(p['package_id'],{value&0xfffffff for value in records}-own,asset_registry)
            if disabled(body,'private_motion'):p.get('native_card',{}).pop('custom_motion',None)
            self.update(message='编译卡图与脸模：'+p['identity']['name'])
            packages,binding,report=compile_art(p,identity,self.library,rig,reference,metadata,asset_key,cache=cache)

            def movie_build():
                if cache and(p.get('native_card')or{}).get('custom_motion'):
                    from compile_cache import assets_in
                    inputs={'uuid':p['package_id'],'custom_motion':p['native_card']['custom_motion'],
                            'thumbnail_animation':p['appearance'].get('thumbnail_animation',True),'binding':binding}
                    def build():return(*private_motion(p,binding,self.library),copy.deepcopy(binding))
                    value,receipt=cache.run('animation',inputs,build,
                        files=[self.store.root/'assets'/name for name in assets_in(inputs)],encoded=True)
                    report.setdefault('compile_cache',{})['animation']=receipt
                    return value
                return(*private_motion(p,binding,self.library),binding)

            def static_motion(error):
                self.notice('preserve_motion',p['package_id'],p['identity']['name']+'本次使用静态卡面，其它内容继续。',error=error)
                body.get('write_designs',{}).get('players',{}).pop(p['package_id'],None)
                return([],{},None,copy.deepcopy(binding))

            if disabled(body,'private_motion'):private_art,private_files,private_report=[],{},None
            else:
                private_art,private_files,private_report,binding=self.local_operation('private_motion',movie_build,
                    snapshots=(binding,),fallback=static_motion)
            if private_report:report['custom_motion']=private_report
            return dict(project=p,identity=identity,definition=definition,packages=packages+private_art,
                        binding=binding,report=report,private_art=private_art,private_files=private_files)

        for row in body['projects']:
            result=self.local_operation('player_art',lambda row=row:export_call('球员',row,'编译球员内容',compile_one,row),
                snapshots=(registry,booster_registry,asset_registry,occupied,seen),
                fallback=lambda error,row=row:self.omit_item(body,'players',row,error))
            if result is not None:completed.append(result)

        while True:
            ids={row['project']['package_id']for row in completed}
            selected=set(body.get('write_selected',{}).get('players',ids))
            kept=[row for row in inherited.get('players',[])if row['identity']['package_id']not in ids and
                  (body.get('write_mode','full')=='incremental'or row['identity']['package_id']in selected)]
            rejected=[]
            for result in completed:
                for booster in result['project'].get('custom_boosters',[]):
                    ident=booster['id']
                    if not any(ident in row['base_parameters'][144:147]for row in kept):continue
                    previous=inherited.get('preview_boosters',{}).get(str(ident))
                    effects={str(key):value for key,value in booster['effects'].items()if value}
                    if not previous or {str(key):value for key,value in previous['effects'].items()if value}!=effects:
                        rejected.append(result);break
            if not rejected:break
            for result in rejected:
                self.omit_item(body,'players',result['project'],ValueError('共享增能需要保留来源中的定义'))
            rejected_ids={result['project']['package_id']for result in rejected}
            completed=[result for result in completed if result['project']['package_id']not in rejected_ids]
        successful={row['project']['package_id']for row in completed}
        for uid in {row['package_id']for row in body['projects']}-successful:
            for current,previous in ((registry,original_registry),(asset_registry,original_assets)):
                if uid in previous:current[uid]=copy.deepcopy(previous[uid])
                else:current.pop(uid,None)
        used={booster['uuid']for row in completed for booster in row['project'].get('custom_boosters',[])}
        allocated={key:value for key,value in booster_registry.items()if any(key==uid or key.startswith(uid+':')for uid in used)}
        booster_registry.clear();booster_registry.update(original_boosters);booster_registry.update(allocated)
        return completed

    def _failure(self,error,body,stage=None):
        stage=stage or getattr(self._job_local,'stage','source')
        recovery=getattr(self._job_local,'recovery',None)
        cancelled=isinstance(cause(error),ExportCancelled)
        import logging
        logging.exception('Export step retained its previous data: %s',stage)
        if recovery is not None and not cancelled and not getattr(self._job_local,'worker_started',False)and recovery.plan(error,stage):
            self._job_local.retry=True
            self.update(phase='working',stage='recovering',message='正在保留原内容并重新准备可用项目。',warnings=recovery.warnings)
            return
        self._job_local.keep_work=True
        if recovery is not None:
            if not cancelled:recovery.record_failure(error,stage)
            self._resume_request=copy.deepcopy(recovery.original)
            recovery.save(self.store.root)
        checked=body.get('_source_preflight')or{}
        source=checked.get('selected_path')or body.get('apk')or body.get('companion')
        try:usable=bool(checked and source and Path(source).is_file())
        except OSError:usable=False
        message=str(cause(error))if cancelled else pause_message(error,stage)
        self.update(phase='preserved'if usable and not cancelled else'paused',
                    message=message,can_resume=True,source_preserved=str(source)if usable else None,
                    preserved=True,recovery_stage=stage)

    def cancel(self):
        self._cancel_event.set()
        self.update(message='正在暂停，已完成的文件和工程会保留。')
        return self.status()

    def cleanup_work(self,work,prefix):
        if work is None or not work.exists():return
        if getattr(self._job_local,'keep_work',False):
            self.update(preserved_work=str(work));return
        parent=(self.store.root/'tmp').resolve();resolved=work.resolve()
        if resolved.parent!=parent or resolved.name!=work.name or not work.name.startswith(prefix) or work.is_symlink() or work.is_junction():
            self.update(cleanup_warning='构建临时目录不在预期位置，未自动清理');return
        try:shutil.rmtree(resolved)
        except OSError:self.update(cleanup_warning='部分构建临时文件仍被占用，可稍后清理')

    def model_binding(self,p):
        model=p.get('model',{});asset=model.get('asset')
        if not asset:raise ValueError('未选择脸模')
        if (model.get('native_source') or {}).get('path'):return None
        from native_glb import Glb,prove_binding
        static=Glb((self.store.root/'assets'/asset).read_bytes())
        if static.doc.get('skins'):return None
        options=[]
        if model.get('rigged_asset'):options.append(self.store.root/'assets'/model['rigged_asset'])
        folder=self.library.home.parent/'我的脸模'
        if folder.is_dir():options.extend(sorted(folder.glob('*.glb'))[:50])
        for path in options:
            try:
                bound=Glb(path.read_bytes())
                if bound.doc.get('skins'):
                    prove_binding(static,bound);return str(path)
            except (ValueError,KeyError,OSError):continue
        return None  # Export attaches foreign/static heads to a neutral native rig with a warning.

    def candidates(self,current):
        saved=self.store.list_players();projects={r['package_id']:self.store.saved_player(r['package_id'])['project'] for r in saved['rows']}
        projects[current['package_id']]=current;rows=[]
        catalog=_export_data("资料文件",self.resources / 'catalog.json',"读取并解析 JSON",lambda: json.loads((self.resources/'catalog.json').read_text(encoding='utf8')))
        from skill_localization import apply_labels
        apply_labels(catalog,self.resources)
        for uid,p in projects.items():
            error=''
            try:self.model_binding(p)
            except (ValueError,OSError) as exc:error=str(exc)
            warning=''
            if p.get('model',{}).get('asset') and not (p['model'].get('native_source') or {}).get('path'):
                warning='自制头模将检查骨架；非原生骨架会提示，并使用原生头骨中性绑定继续生成。原模型文件保留。'
            rows.append({'package_id':uid,'name':p['display_name'] or p['identity']['name'],'card_type':p['card_type'],
                         'current':uid==current['package_id'],'model_error':error,'model_warning':warning,
                         'booster_error':'；'.join(booster_issues(p,catalog['ABILITIES'])),
                         'maximum_overall':maximum_development(p,catalog)['overall'],'max_level':p['max_level']})
        from coach_models import prepare as prepare_coach_model
        coach_rows=[prepare_coach_model(c,self.library) for c in self.coach_candidates(current)]
        return {'rows':rows,'errors':saved['errors'],'coaches':[{'uuid':c['uuid'],'name':c['name'],'has_model':bool(c.get('head_model'))} for c in coach_rows],
                'packs':[{'uuid':p['uuid'],'name':p['name'],'enabled':p['enabled'],'member_count':len(p['members'])} for p in load_packs(self.store)]}

    def coach_candidates(self,current):
        abilities=_export_data("资料文件",self.resources / 'catalog.json',"读取并解析 JSON",lambda: json.loads((self.resources/'catalog.json').read_text(encoding='utf8')))['ABILITIES']
        rows={c['uuid']:c for c in CoachStore(self.store.root,abilities).list()['rows']}
        for c in current.get('custom_coaches',[]):rows[c['uuid']]=c
        return list(rows.values())

    def write_candidates(self,current,writing='full',apk=None,resource_packages=None):
        if apk and not Path(apk).is_file():
            from apk_sources import recorded,resolve
            # Candidate enumeration may run synchronously on the HTTP thread.
            # A system same-package/signer hint is safe to display, but cannot
            # mutate the trusted source record or trigger a multi-GB hash here.
            record=recorded(self.library,self.store);identity=record.get('source_identities',{}).get('apk',{})
            aliases={str(record.get('apk')or'')}
            for prior in record.get('source_path_history',{}).get('apk',[]):
                if all(prior.get(k)==identity.get(k)for k in('package','signers','compatibility')):
                    aliases.add(prior.get('file',{}).get('path'))
            for prior in record.get('legacy_source_aliases',{}).get('apk',[]):
                if all(prior.get(k)==identity.get(k)for k in('package','signers','compatibility')):aliases.add(prior.get('path'))
            if str(apk)in aliases:
                hint=resolve(self.library,self.store)
                if hint.get('apk')and Path(hint['apk']).is_file():apk=hint['apk']
        from write_modes import mode,source,statuses,design,requirements
        from apk_sources import resource_paths
        mode(writing);doc,summary=source(apk,requirements(self.library),resource_packages=resource_paths(self.library,self.store,apk,resource_packages))if apk else({},None)
        # The source document is a fresh read-only copy; refresh only its rating
        # summaries so retained/unselected players show the same export maximum.
        if doc:
            from overall import refresh_definition_ratings
            refresh_definition_ratings(doc,json.loads((self.resources/'catalog.json').read_text(encoding='utf8')))
        result=self.candidates(current)
        projects={r['package_id']:self.store.saved_player(r['package_id'])['project']for r in self.store.list_players()['rows']}
        projects[current['package_id']]=current
        local={'players':list(projects.values()),'coaches':self.coach_candidates(current),'packs':load_packs(self.store)}
        fields={'players':'package_id','coaches':'uuid','packs':'uuid'};positive={}
        for kind,field in fields.items():
            states=statuses(kind,local[kind],doc,self.store)
            positive[kind]={v[field]:design(kind,v,self.store)for v in local[kind]if states[v[field]]=='unchanged'}
            rows=result['rows'if kind=='players'else kind]
            for row in rows:
                row['write_state']=states.get(row[field],'new')
                if kind=='players'and row['write_state']=='unchanged':
                    row['model_error']='';row['model_warning']='未修改，直接复用来源包现有卡面与脸模'
            if writing=='full':
                seen={r[field]for r in rows}
                for old in doc.get(kind,[]):
                    uid=old['identity']['package_id']if kind=='players'else old['uuid']
                    if uid in seen:continue
                    if kind=='players':
                        payload=old['owned'];rows.append({'package_id':uid,'name':payload.get('name','来源包球员'),'card_type':old['card_type'],
                            'current':False,'max_level':payload.get('level_info',{}).get('level_limit',1),
                            'maximum_overall':old.get('maximum_development',{}).get('overall',0),'model_warning':'来自来源包，保留其现有卡面与脸模',
                            'source_only':True,'write_state':'source_only'})
                    elif kind=='coaches':rows.append({'uuid':uid,'name':old['payload']['name'],'has_model':any(m['uuid']==uid for m in doc.get('coach_models',[])),'source_only':True,'write_state':'source_only'})
                    else:rows.append({'uuid':uid,'name':old['name'],'enabled':old['enabled'],'member_count':len(old['members']),'source_only':True,'write_state':'source_only'})
        result['hidden_unchanged']=0
        if writing=='incremental':
            result['hidden_unchanged']=sum(r['write_state']=='unchanged'for r in result['rows'])
            result['rows']=[r for r in result['rows']if r['write_state']!='unchanged']
        result['write_source']=summary
        from world_workspace import load as load_world,deleted_tournaments
        from tournament_runtime import export_rows
        result['tournaments']=export_rows(load_world(self.store)['tournaments'],doc.get('tournaments',[]),deleted_tournaments(self.store))
        if summary:
            with self.lock:self._write_positive=(summary['token'],positive)
        return result

    @exported('APK','实装前检查',lambda self,body: body.get('apk') or body.get('companion') or '生成所选内容')
    def start(self,body):
        with self.lock:
            if self.state['phase']=='working':return copy.deepcopy(self.state)
            body=copy.deepcopy(body)
            if body.get('resume'):
                if self._resume_request is None:
                    try:self._resume_request=json.loads((self.store.root/'export-resume.json').read_text('utf8'))['request']
                    except (OSError,ValueError,KeyError,TypeError):self._resume_request=None
                if self._resume_request is None:return {'phase':'paused','message':'工程已保留，请选择来源后继续生成。','can_resume':False}
                body=copy.deepcopy(self._resume_request)
            if body.get('installation_mode','update')not in('update','coexist'):body['installation_mode']='update'
            if body.get('write_mode','full')not in('full','incremental'):body['write_mode']='incremental'
            if body.get('operation')!='companion':
                from match_feel import settings as match_feel_settings
                if 'match_feel' not in body:
                    body['match_feel']=match_feel_settings(self.store)
                current=body.get('project')or{}
                if not isinstance(current,dict)or not current.get('package_id'):
                    current={'package_id':str(uuid.uuid4()),'identity':{'name':'当前工程'}}
                    body.update(project=current,selected_ids=body.get('selected_ids')or[])
                ids=body.get('selected_ids',[current['package_id']])
                if not isinstance(ids,list):ids=[]
                ids=[value for value in ids if isinstance(value,str)]
                if type(body.get('force_fps160',False))is not bool:body['force_fps160']=False
                ids=selected_ids(ids,'球员',10000)
                body=dict(body,selected_ids=ids)
            request=copy.deepcopy(body)
            guard=None
            self._job_serial=getattr(self,'_job_serial',0)+1
            if not hasattr(self,'_job_local'):self._job_local=threading.local()
            serial=self._job_serial
            self._cancel_event.clear();self._resume_request=copy.deepcopy(request)
            self.state={'phase':'working','message':'核对当前资料库来源与导出所选 APK','job_id':serial,'stage':'source_preflight'}
        Recovery(request).save(self.store.root,status='working')
        try:threading.Thread(target=self._run_job,args=(request,serial,guard),daemon=True).start()
        except Exception as start_error:
            if guard is not None:_release_export(guard)
            recovery=Recovery(request);recovery.record_failure(start_error,'request');recovery.save(self.store.root)
            self.update(phase='paused',message='任务暂未启动，工程已保留，可以继续生成。',can_resume=True,
                        diagnostics=recovery.diagnostics())
        return self.status()

    def _run_job(self,body,serial,guard):
        self._job_local.serial=serial
        self._job_local.recovery=Recovery(body);self._job_local.keep_work=False
        self._job_local.worker_started=False
        try:
            while guard is None:
                try:guard=_claim_export(self.store)
                except ValueError:
                    self.update(phase='working',stage='waiting',message='正在等待前一个生成任务完成。')
                    if self._cancel_event.wait(.5):raise ExportCancelled('已暂停等待，工程已保留。')
            while True:
                self._job_local.retry=False
                self._job_local.keep_work=False
                self._prepare(self._job_local.recovery.request())
                if not self._job_local.retry:break
                if self._cancel_event.wait(.05):raise ExportCancelled('已暂停本次生成，工程已保留。')
        except Exception as exc:self._failure(exc,body)
        finally:
            terminal=getattr(self._job_local,'terminal',None)
            if terminal and terminal.get('phase')=='done':
                try:
                    (self.store.root/'export-resume.json').unlink(missing_ok=True)
                except OSError:
                    try:self._job_local.recovery.save(self.store.root,status='done')
                    except OSError as error:
                        self.notice('recovery_record','apk','任务已完成，临时任务记录可稍后清理。',error=error)
                result=terminal.get('result',{})
                if result.get('base_apk'):
                    from managed_game_base import prune
                    try:prune(self.store,self.library.home,result)
                    except (OSError,ValueError) as error:
                        self.notice('managed_base_cleanup','apk','新底包已登记，旧文件可稍后重试清理。',error=error)
            try:
                if guard is not None:
                    try:_release_export(guard)
                    except OSError as release_error:
                        self.notice('release_deferred','export-lock','任务文件仍被使用，可以稍后清理。',error=release_error)
                        self.update(cleanup_warning='任务文件仍被使用，可以稍后清理。')
            finally:
                with self.lock:
                    if serial==self._job_serial:
                        self.state.update(terminal or{'phase':'paused','message':'任务已暂停，工程和可用结果已保留。','can_resume':True})
                        self.state['warnings']=copy.deepcopy(self._job_local.recovery.warnings)
                        self.state['diagnostics']=self._job_local.recovery.diagnostics()
                        if isinstance(self.state.get('result'),dict):
                            self.state['result']['diagnostics']=copy.deepcopy(self.state['diagnostics'])
                if terminal is not None:del self._job_local.terminal
                del self._job_local.serial
                del self._job_local.recovery

    def _prepare(self,body):
        try:
            body.pop('_player_append_marker',None)
            player_update=body.get('operation')=='player_content'
            if player_update and (body.get('write_mode')!='incremental' or any(body.get(name) for name in ('pack_ids','coach_ids','tournament_ids'))):
                raise ValueError('球员小包请使用增量模式，只勾选本次要实装的球员')
            if body.get('candidates_unavailable'):
                self.notice('selection_refresh','selection','来源列表暂未刷新，已保留来源中的现有内容，并重新读取本次选择。')
            self.step('source')
            if body.get('operation')=='companion':
                self._companion(body['companion'],body.get('installation_mode','update'))
                return
            from apk_sources import export_preflight,assert_export_current
            from resource_source_bindings import selected_files
            provided=dict(body.get('resource_packages')or{});provided.update(selected_files(body.get('resource_files')or[]))
            with export_context('游戏 APK',body.get('apk'),'在编译模型之前核对资料库来源与导出来源'):
                checked=body.get('_source_preflight')
                if checked:assert_export_current(self.library,self.store,checked)
                else:
                    checked=export_preflight(self.library,self.store,body.get('apk'),
                        explicit_source_selection=body.get('explicit_source_selection',False),write_source_token=body.get('write_source_token'),
                        resource_packages=provided,
                        progress=lambda message:self.update(message=message))
            recovery=getattr(self._job_local,'recovery',None)
            if recovery is not None:recovery.source_ready(checked)
            body=dict(body,apk=checked['selected_path'],resource_packages=copy.deepcopy(checked.get('resource_packages',{})),_source_preflight=checked)
            if player_update:
                from player_updates import capable
                from player_append import supports_replacement
                from program_content import program
                with zipfile.ZipFile(body['apk'])as archive:
                    marker=program(archive)
                    if not capable(marker):
                        raise ValueError('请先用新版工坊生成并安装完整基准 APK，再生成球员小包')
                if supports_replacement(marker) and not body['resource_packages'].get('content_pack'):
                    body['_player_append_marker']=marker
            if checked.get('rebind_notice'):self.update(source_rebind_notice=checked['rebind_notice'])
            assert_export_current(self.library,self.store,checked)
            self.step('selection')
            from editor_drafts import require_committed
            pack_ids=selected_ids(body.get('pack_ids',[]),'卡包')
            coach_ids=selected_ids(body.get('coach_ids',[]),'教练')
            for kind,values,label in (('coaches',coach_ids,'教练'),('packs',pack_ids,'卡包')):
                for value in values:
                    if disabled(body,kind)or value in skipped(body,kind):continue
                    with export_context(label,{'uuid':value,'name':label},'读取本次编辑'):
                        require_committed(self.store,[('coach:'if kind=='coaches'else'pack:')+value])
            from coach_models import CoachModelStore
            # Old clients may still send include_coach_heads / coach_heads. Neither
            # can activate the retired global store or inject arbitrary targets.
            self.step('stock_heads')
            head_rows=[]if player_update or disabled(body,'stock_heads')else CoachModelStore(self.store.root).list()
            self.step('selection')
            current=body['project'];ids=body.get('selected_ids',[current['package_id']])
            if type(body.get('force_fps160',False)) is not bool:raise ValueError('160 帧开关无效')
            ids=selected_ids(ids,'球员',10000)
            from write_modes import mode,source,statuses,designs,requirements
            writing=mode(body.get('write_mode','full'))
            source_doc,source_summary=source(body.get('apk'),requirements(self.library),resource_packages=body.get('resource_packages'))
            if body.get('write_source_token')and body['write_source_token']!=source_summary['token']:
                self.notice('source_refresh','apk','来源记录已刷新，继续使用当前可用内容。')
            old_ids={p['identity']['package_id']for p in source_doc.get('players',[])}
            ids,omitted_players=retain_selection(body,'players',ids,old_ids)
            source_only=(set(body.get('source_only_ids',[]))&old_ids&set(ids))|(omitted_players&old_ids)
            self.step('player_data')
            from app import normalize_project
            selected_projects=[copy.deepcopy(current) if uid==current['package_id'] else export_call('球员',uid,'读取已保存工程',self.store.saved_player,uid)['project'] for uid in ids if uid not in source_only]
            selected_projects=[export_call('球员',p,'读取球员设置',normalize_project,p) for p in selected_projects]
            player_states=statuses('players',selected_projects,source_doc,self.store)
            projects=[p for p in selected_projects if player_states[p['package_id']]!='unchanged']
            if player_update and not projects:raise ValueError('请选择至少一名新增或有修改的球员')
            from player_append import supports_movies
            append_marker=body.get('_player_append_marker')
            movie_append=supports_movies(append_marker)
            if append_marker and not movie_append and any((p.get('native_card') or {}).get('custom_motion') for p in projects):
                raise ValueError('自定义视频卡面涉及共用游戏资源，请使用完整 APK 导出')
            from project_resource_migration import prepare as prepare_project
            projects=[export_call('球员',p,'迁移原版卡面与脸模引用',prepare_project,p,self.resources,self.library.face_assets())[0] for p in projects]
            self.step('packs')
            old_packs={p['uuid']for p in source_doc.get('packs',[])}
            old_coaches={p['uuid']for p in source_doc.get('coaches',[])}
            pack_ids,omitted_packs=retain_selection(body,'packs',pack_ids,old_packs)
            coach_ids,omitted_coaches=retain_selection(body,'coaches',coach_ids,old_coaches)
            selected_packs=[]if disabled(body,'packs')or not set(pack_ids)-omitted_packs else [p for p in load_packs(self.store) if p['uuid'] in pack_ids and p['uuid']not in omitted_packs]
            self.step('coaches')
            selected_coaches=[]if disabled(body,'coaches')or not set(coach_ids)-omitted_coaches else [c for c in self.coach_candidates(current) if c['uuid'] in coach_ids and c['uuid']not in omitted_coaches]
            known_coaches={c['uuid']for c in selected_coaches}|old_coaches
            known_packs={p['uuid']for p in selected_packs}|old_packs
            for kind,wanted,known in (('教练',coach_ids,known_coaches),('卡包',pack_ids,known_packs)):
                for ident in set(wanted)-known:self.notice('missing_item',ident,kind+'已不在当前来源中，其它项目继续。')
            coach_ids=[v for v in coach_ids if v in known_coaches];pack_ids=[v for v in pack_ids if v in known_packs]
            if writing=='full':
                existing_members={str(cid):p['identity']['package_id']for p in source_doc.get('players',[])for cid in {p['identity']['card_id'],*p['identity'].get('previous_card_ids',[])}}
                local_pack_ids={p['uuid']for p in selected_packs}
                inspect_packs=selected_packs+[p for p in source_doc.get('packs',[])if p['uuid']in pack_ids and p['uuid']not in local_pack_ids]
                for pack in inspect_packs:
                    for member in pack['members']:
                        uid=member.get('id')if member.get('kind')=='custom'else existing_members.get(str(member.get('card_id')))
                        if uid and uid not in ids:
                            with export_context('卡包',pack,'补齐引用的球员'):
                                if uid in omitted_players:
                                    raise ValueError('卡包引用的球员暂不可更新，将保留原卡包。')
                                if uid in old_ids:source_only.add(uid)
                                else:
                                    project=self.store.saved_player(uid)['project']
                                    project=prepare_project(project,self.resources,self.library.face_assets())[0]
                                    selected_projects.append(project);projects.append(project)
                                ids.append(uid)
                                self.notice('include_dependency',uid,'已自动包含卡包引用的球员。')
            coach_states=statuses('coaches',selected_coaches,source_doc,self.store);pack_states=statuses('packs',selected_packs,source_doc,self.store)
            coaches=[c for c in selected_coaches if coach_states[c['uuid']]!='unchanged']
            packs=[p for p in selected_packs if pack_states[p['uuid']]!='unchanged']
            design_rows=designs(selected_projects,selected_coaches,selected_packs,self.store)
            if disabled(body,'private_motion')or disabled(body,'card_motion'):design_rows['players']={}
            if disabled(body,'pack_covers'):design_rows['packs']={}
            if getattr(self,'_write_positive',(None,))[0]==source_summary['token']:
                for kind,values in self._write_positive[1].items():design_rows[kind]={**values,**design_rows[kind]}
            body=dict(body,write_mode=writing,write_source_token=source_summary['token'],write_designs=design_rows,
                write_selected={'players':ids,'coaches':coach_ids,'packs':pack_ids})
            for pack in packs:
                if not pack['members']:
                    with export_context('卡包',pack,'读取卡包成员'):
                        raise ValueError('卡包还没有成员，将保留来源中的原卡包。')
            from export_options import settings as export_settings,progress as export_progress,world as export_world
            self.step('options')
            overrides={}
            if body.get('force_fps160'):overrides['frame_rate']={'enabled':True,'target':160}
            if 'frame_rate' in body:overrides['frame_rate']=body['frame_rate']
            if disabled(body,'settings'):
                from game_settings import DEFAULTS
                option_settings=copy.deepcopy(DEFAULTS)
                for value in option_settings.values():value['enabled']=False
            else:option_settings=export_settings(self.store,overrides)
            for feature in ('controller','frame_rate','display','card_motion','ai_division_squads','ai_cut_in_shots','action_trace'):
                if disabled(body,feature):option_settings[feature]['enabled']=False
            from match_feel import effective as effective_feel,normalize as normalize_feel
            requested_feel=normalize_feel(body.get('match_feel'))
            if player_update:
                requested_feel=normalize_feel(source_doc.get('gameplay',{}).get('feel',{'mode':'workshop'}))
            if self.library.profile.get('game_version')=='V17' and requested_feel['mode']!='workshop':
                if not effective_feel(requested_feel)['ai']:
                    for feature in ('ai_division_squads','ai_cut_in_shots'):
                        option_settings[feature]['enabled']=False
                # The combined action observer installs the V8 gameplay bundle.
                option_settings['action_trace']['enabled']=False
            progress_options=({'enabled':False,'style_mode':'shuffle_bag'}if disabled(body,'progress')else
                              export_progress(self.store,style_enabled=option_settings['ai_division_squads']['enabled']))
            if player_update:
                option_settings=source_doc['game_settings']
                progress_options=source_doc['game_options']
                body['source_gameplay']=source_doc.get('gameplay',{})
            body=dict(body,export_settings=option_settings,
                      export_progress=progress_options,match_feel=requested_feel)
            self.step('world')
            if player_update or disabled(body,'stadium')and disabled(body,'tournaments'):
                from world_workspace import default
                world=default();world['stadium']['unlock_all']=False
            else:world=export_world(self.store,[]if disabled(body,'tournaments')else body.get('tournament_ids'),ignore_stadium=disabled(body,'stadium'))
            from tournament_runtime import export_selection
            from world_workspace import deleted_tournaments
            removed_tournaments=[]if player_update else deleted_tournaments(self.store)
            tournament_ids=body.get('tournament_ids')
            if tournament_ids is None:tournament_ids=[t['uuid']for t in world['tournaments']if t.get('enabled')]
            tournament_ids=selected_ids(tournament_ids,'赛事')
            self.step('tournaments')
            body['tournament_config']=(copy.deepcopy(source_doc.get('tournaments',[]))if disabled(body,'tournaments')else
                export_selection(world['tournaments'],source_doc.get('tournaments',[]),tournament_ids,writing,removed_tournaments))
            if (body['tournament_config']or removed_tournaments) and source_doc and not ids and not coach_ids and not pack_ids:
                # A tournament-only update must retain the user's other content,
                # including tournaments they did not select for replacement.
                writing='incremental'
                body.update(write_mode=writing,tournament_only_update=True)
                if not disabled(body,'tournaments'):body['tournament_config']=export_selection(world['tournaments'],source_doc.get('tournaments',[]),tournament_ids,writing,removed_tournaments)
            # Source selection is finished. The worker loads its own verified source.
            del source_doc
            from world_workspace import selection_warnings
            body['tournament_selection_warnings']=[]if disabled(body,'tournaments')else selection_warnings(self.library,self.store,body['tournament_config'])
            # Stadium compilation does not validate unrelated, unselected drafts.
            world['tournaments']=[]
            from editor_drafts import listing as draft_listing
            world_draft=draft_listing(self.store).get('world:world')or{}
            has_world=(self.store.root/'world-workspace.json').is_file()or(not world_draft.get('committed')and world_draft.get('value')is not None)
            body['world_workspace']=world if not player_update and has_world and not disabled(body,'stadium')else None
            self.step('player_data')
            self.update(message='来源核对完成，准备所选球员与内容',players=len(projects),stage='preparing_selected_content')
            assert_export_current(self.library,self.store,checked)
            self._worker(dict(body,projects=projects,packs=packs,coaches=coaches,stock_coach_models=head_rows))
        except Exception as exc:
            error=failure(exc,'游戏 APK',body.get('apk'),'来源预检与导出准备')
            self._failure(error,body)

    def _companion(self,path,installation_mode='update'):
        work=None;output_created=False;verified=None;output=None
        try:
            from apk_sources import export_preflight,assert_export_current,publication_guard,SourceChanged
            self.update(message='核对离线资源 APK 来源与签名',stage='source_preflight')
            checked=export_preflight(self.library,self.store,path,key='companion',progress=lambda message:self.update(message=message))
            source=Path(checked['selected_path']).absolute()
            assert_export_current(self.library,self.store,checked)
            from apk_installation import coexist_entries
            with zipfile.ZipFile(source) as z:
                if 'assets/resource-bootstrap/resources.zip' not in z.namelist():raise ValueError('请选择原版离线资源 APK')
                changes,installation=coexist_entries(z,installation_mode)
            work=self.store.root/'tmp'/('resource-signer-'+uuid.uuid4().hex);work.mkdir()
            assert_export_current(self.library,self.store,checked)
            unsigned=work/'unsigned.apk';apk_build=replace_entries(source,unsigned,changes,prepare_signing=True)
            assert_export_current(self.library,self.store,checked)
            key,cert=local_signer(self.store.root/'signing')
            output=self.store.root/'exports'/(('73efootball-工坊共存资源-' if installation_mode=='coexist' else '73efootball-离线资源-个人签名-')+time.strftime('%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:4]+'.apk')
            sign_v2(unsigned,output,key,cert,prepared=apk_build,consume=True);verified=verify_v2(output)
            output_created=True
            from apk_installation import identity,remember_resource
            with publication_guard(self.library,self.store,checked):
                remember_resource(self.store,installation_mode,identity(output)['package'],verified['signer_sha256'])
                self.remember_installation_mode(source,installation_mode)
            self.update(phase='done',message='同签名资源 APK 已生成；首次安装请与个人球员 APK 配套使用',
                        result={'kind':'companion','path':str(output),'url':'/exports/'+output.name,'size':output.stat().st_size,'signature':verified,'installation':installation})
        except Exception as exc:
            import logging
            logging.exception('Native APK compilation failed')
            error=failure(exc,'离线资源 APK',path,'读取、重签名和校验资源包')
            if output_created and verified and output.is_file():
                self.notice('resource_record','companion','资源 APK 已生成，安装记录将在下次读取时更新。')
                self.update(phase='done',message='同签名资源 APK 已生成。',result={'kind':'companion','name':output.name,'path':str(output),'url':'/exports/'+output.name,'size':output.stat().st_size,'signature':verified,'installation':installation})
            else:self._failure(error,{'apk':path})
        finally:self.cleanup_work(work,'resource-signer-')

    @cached_export
    def _worker(self,body):
        self._job_local.worker_started=True
        body=dict(body,projects=copy.deepcopy(body['projects']))
        player_update=body.get('operation')=='player_content'
        from player_append import supports_movies
        movie_append=player_update and supports_movies(body.get('_player_append_marker'))
        work=None;compile_cache=None;texture_scope=None;texture_reports=[];prepared_set=None;timings=ExportTimings();output_created=False
        program_content=None;logical_changes=None;program_remove=set();result=None
        try:
            from apk_sources import export_preflight,assert_export_current,publication_guard,SourceChanged
            self.update(message='重新确认资料库来源和所选 APK 未变化',stage='source_preflight')
            previous=body.get('_source_preflight')
            if previous:
                assert_export_current(self.library,self.store,previous)
                checked=previous
            else:
                checked=export_preflight(self.library,self.store,body.get('apk'),
                    explicit_source_selection=body.get('explicit_source_selection',False),write_source_token=body.get('write_source_token'),
                    resource_packages=body.get('resource_packages'),progress=lambda message:self.update(message=message))
            body=dict(body,apk=checked['selected_path'],resource_packages=copy.deepcopy(checked.get('resource_packages',{})),_source_preflight=checked)
            timings.mark('source_identity_preflight')
            work=self.store.root/'tmp'/('native-'+uuid.uuid4().hex);work.mkdir()
            from texture_codec import compilation_scope
            texture_scope=compilation_scope(self.store.root/'compiler-cache/texture-etc2',texture_reports,getattr(self.library,'native',None));texture_scope.__enter__()
            projects=body['projects'];apk=Path(body['apk']).absolute()
            self.step('model_binding')
            timings.mark('input_setup')
            registry_path=self.store.root/'native-player-registry.json'
            registry=_export_data("资料文件",registry_path,"读取并解析 JSON",lambda: json.loads(registry_path.read_text(encoding='utf8'))) if registry_path.exists() else {}
            booster_path=self.store.root/'native-booster-registry.json'
            booster_registry=_export_data("资料文件",booster_path,"读取并解析 JSON",lambda: json.loads(booster_path.read_text(encoding='utf8'))) if booster_path.exists() else {}
            asset_path=self.store.root/'native-asset-registry.json'
            asset_registry=_export_data("资料文件",asset_path,"读取并解析 JSON",lambda: json.loads(asset_path.read_text(encoding='utf8'))) if asset_path.exists() else {}
            coach_path=self.store.root/'native-coach-registry.json'
            coach_registry=_export_data("资料文件",coach_path,"读取并解析 JSON",lambda: json.loads(coach_path.read_text(encoding='utf8'))) if coach_path.exists() else {}
            assert_export_current(self.library,self.store,checked)
            from resource_set import Reader
            from apk_sources import resource_paths
            with Reader(apk,packages=body.get('resource_packages')or resource_paths(self.library,self.store,apk),verify_identities=False) as source:
                from apk_installation import coexist_entries
                install_replacements,installation=coexist_entries(source,body.get('installation_mode','update'))
                release=_export_data("资料文件",PREFIX + 'release-manifest.json',"读取并解析 JSON",lambda: json.loads(source.read(PREFIX+'release-manifest.json')))
                from write_modes import compatible,requirements
                if not previous:compatible(source,release,requirements(self.library))
                from content_autofill import synchronize
                self.step('content_sync')
                if player_update or disabled(body,'content_sync'):content_replacements={};content_sync_report=None
                else:source,content_replacements,content_sync_report=self.local_operation('content_sync',
                    lambda:synchronize(source,self.library,work,self.update),
                    fallback=lambda error:self.preserve_feature(body,'content_sync',error,(source,{},None)))
                if not player_update:
                    from incremental_delta import materialize as materialize_content
                    source,incremental_replacements,incremental_report=materialize_content(source,self.library,work,self.update)
                    content_replacements.update(incremental_replacements)
                    if incremental_report:content_sync_report={'previous':content_sync_report,'incremental':incremental_report}
                release=json.loads(source.read(PREFIX+'release-manifest.json'))
                gameplay_replacements={};gameplay_report={'enabled':False}
                feel=None
                self.step('gameplay')
                if self.library.profile.get('game_version')=='V17' and not disabled(body,'gameplay'):
                    from gameplay_v8 import prepare as prepare_gameplay, GROUND_PASS_REVISION
                    from match_feel import effective as effective_feel
                    feel=effective_feel(body['match_feel'])
                    if player_update:
                        gameplay_report=copy.deepcopy(body['source_gameplay'])
                    else:
                        gameplay_replacements,gameplay_report=self.local_operation('gameplay',
                            lambda:prepare_gameplay(source,self.profile_resources,work,self.update,feel=feel),
                            fallback=lambda error:self.preserve_feature(body,'gameplay',error,({}, {'enabled':False})))
                    if disabled(body,'gameplay'):
                        feel=None
                    else:
                        gameplay_report.update(enabled=feel['mode']=='workshop',feel=body['match_feel'],applied_feel=feel)
                        gameplay_report['runtime']={'architecture':'arm64',
                            'groups':['ai','space','ball','contact','keeper','action','outside','statistics'] if gameplay_report['enabled'] else [],
                            'ground_pass_revision':GROUND_PASS_REVISION,
                            'response_revision':'pc-v8-response-20261113-5' if feel['response'] else None,
                            'input_buffer':'native','in_game_verified':False}
                self.step('player_data')
                from native_tables import BOOSTERS
                source_cpk=source.read(PREFIX+NATIVE+'dt870_mobile_and.cpk',limit=None)
                booster_data=unwrap(Cpk(source_cpk).read(BOOSTERS))
                if len(booster_data)%40:raise ValueError('原生增能表结构改变')
                occupied_boosters={int.from_bytes(booster_data[i+14:i+16],'little')&1023 for i in range(0,len(booster_data),40)}
                inherited=personal_source.read(source,release)
                # The fallback pool is rebuilt from the final catalogue.
                inherited.pop('tournament_fallback_standard_cards',None)
                from write_modes import mode,retained,removals,refresh_packs,prune_tables,source_container,final_designs
                writing=mode(body.get('write_mode','full'))
                selected_packs=body['packs'];adjusted_packs=[]
                compiled={'players':{p['package_id']for p in projects},'coaches':{c['uuid']for c in body.get('coaches',[])},'packs':{p['uuid']for p in selected_packs}}
                selected={k:set(v)for k,v in body.get('write_selected',compiled).items()}
                from content_delta import read_catalogue
                content_catalogue=read_catalogue(source)
                owners=personal_source.seed(inherited,registry,asset_registry)
                self.update(message='一次解压并完整校验引擎，供所有接口检查复用')
                timings.mark('source_registry_setup')
                engine_file=verified_engine(source,work,checked['selected_identity']['engine_sha256'])
                with engine_file.open() as lib:
                    lib.seek(self.library.profile['resource_key_offset']);engine_key=lib.read(32)
                if hashlib.sha256(engine_key).hexdigest()!=self.library.profile['resource_key_sha256']:
                    raise ValueError('此 APK 的原生引擎与 V14 模板不同')
                timings.mark('engine_extract_hash_and_key')
                original=source_cpk
                from catalogue_trash import patch_tables as recycle_tables,active as recycle_items,policy as recycle_policy,patch_seed as recycle_seed
                original=recycle_tables(original,self.store,restore_only=True)
                recycled=recycle_items(self.store)
                original_cpk=Cpk(original)
                records=rows(unwrap(original_cpk.read(PLAYER)),400,8)
                from native_tables import APPEARANCE
                original_appearances=rows(unwrap(original_cpk.read(APPEARANCE)),64)
                appearance_donors=self.library_appearance_donors(projects,original_appearances,checked)
                catalog=_export_data("资料文件",self.resources/'catalog.json',"读取并解析 JSON",
                    lambda:json.loads((self.resources/'catalog.json').read_text('utf8')))
                from skill_localization import apply_labels
                apply_labels(catalog,self.resources)
                costs=json.loads((self.resources/'native-progression.json').read_text('utf8'))['costs']
                with sqlite3.connect(self.resources/'library.sqlite3')as db:
                    template=json.loads(db.execute('select payload from players where id=?',('7511',)).fetchone()[0])
                cache_warning=None
                try:
                    from compile_cache import CompileCache,source_context
                    compile_cache=CompileCache(self.store.root/'compiler-cache',source_context(self.library,self.resources))
                except (OSError,ValueError,TypeError,ImportError,AttributeError)as exc:
                    cache_warning='编译缓存不可用，本次正常编译。'
                    self.notice('cache_unavailable','compiler-cache',cache_warning,error=exc)
                completed_players=self.compile_selected_players(body,inherited=inherited,owners=owners,records=records,
                    appearances=ChainMap(original_appearances,appearance_donors),catalog=catalog,costs=costs,template=template,
                    registry=registry,booster_registry=booster_registry,asset_registry=asset_registry,
                    occupied_boosters=occupied_boosters,cache=compile_cache)
                del original_appearances
                projects=[row['project']for row in completed_players]
                definitions=[row['definition']for row in completed_players]
                identities=[row['identity']for row in completed_players]
                bindings=[row['binding']for row in completed_players]
                art=[part for row in completed_players for part in row['packages']]
                art_reports=[row['report']for row in completed_players]
                private_animation_art=[part for row in completed_players for part in row['private_art']]
                private_movie_files={name:data for row in completed_players for name,data in row['private_files'].items()}
                del completed_players
                compiled['players']={p['package_id']for p in projects}
                kept=retained(inherited,writing,selected,compiled)
                kept_owner_ids={p['identity']['package_id']for p in kept.get('players',[])}-compiled['players']
                selected_owners=[owner for uid,owner in owners.items()if uid in compiled['players']or writing=='full'and uid not in kept_owner_ids]
                replace_ids=set().union(*(p['aliases']for p in selected_owners))if selected_owners else set()
                replace_boosters=set().union(*(p['boosters']for p in selected_owners))if selected_owners else set()
                all_definitions={d['identity']['package_id']:d for d in kept.get('players',[])}
                all_definitions.update({d['identity']['package_id']:d for d in definitions})
                from pack_covers import compile_covers
                from pack_catalogue import from_archive
                blocked={**from_archive(source),**{cid:{'name':row['name'],'reason':'回收站'}for cid,row in recycled.items()}}
                cover_files={};cover_bindings={};cover_routes={};packs=[];pack_slots=set();successful_packs=[]
                def compile_one_pack(pack):
                    self.step('pack_covers')
                    files,cover_binding,routes=({}, {}, {})if disabled(body,'pack_covers')else compile_covers([pack],self.store)
                    self.step('packs')
                    definition=compile_packs([pack],list(all_definitions.values()),self.resources,cover_binding,blocked)[0]
                    if definition['agent_id']in pack_slots:raise ValueError('卡包编号与其它已选卡包重复')
                    pack_slots.add(definition['agent_id'])
                    return definition,files,cover_binding,routes
                for pack in selected_packs:
                    value=self.local_operation('packs',lambda pack=pack:export_call('卡包',pack,'编译卡包内容',compile_one_pack,pack),
                        snapshots=(pack_slots,),fallback=lambda error,pack=pack:self.omit_item(body,'packs',pack,error))
                    if value is None:continue
                    definition,files,cover_binding,routes=value
                    packs.append(definition);successful_packs.append(pack)
                    cover_files.update(files);cover_bindings.update(cover_binding);cover_routes.update(routes)
                selected_packs=successful_packs
                compiled['packs']={p['uuid']for p in selected_packs}
                timings.mark('player_art_and_private_animation')
                self.update(message='写入原生球员表、卡面索引及增能效果')
                self.step('coaches')
                coach_catalog=_export_data("资料文件",self.resources / 'coaches.json',"读取并解析 JSON",lambda: json.loads((self.resources/'coaches.json').read_text(encoding='utf8')))['coaches']
                selected_coach_ids={c['uuid']for c in body.get('coaches',[])}
                original_coaches=[c for c in inherited.get('coaches',[])if c['uuid']in selected_coach_ids]
                retired_coaches=[c for c in inherited.get('coaches',[])if writing=='full'and c['uuid']not in selected['coaches']]
                coach_registry.setdefault('coaches',{}).update({c['uuid']:c['id']for c in original_coaches})
                coach_registry.setdefault('boosters',{}).update(inherited.get('editor_registries',{}).get('coach_boosters',{}))
                coach_registry.setdefault('links',{}).update(inherited.get('editor_registries',{}).get('coach_links',{}))
                from native_coach_links import compile_links,schema as link_schema
                from coach_models import compile_models as compile_coach_models,verify_loader as verify_coach_model_loader
                from coach_portraits import compile_portraits,owned_paths
                def compile_coach_group():
                    chosen=copy.deepcopy(body.get('coaches',[]))
                    link_resources,link_ids,new_links=compile_links(original_cpk,chosen,coach_registry,link_schema(self.profile_resources))
                    for coach in chosen:coach['_compiled_link_ids']=link_ids[coach['uuid']]
                    resources,definitions=compile_coaches(original_cpk,chosen,coach_catalog,coach_registry,
                        {c['id']for c in original_coaches},{b for c in original_coaches for b in c['payload'].get('ability_id',[])[:2]if b})
                    resources.update(link_resources)
                    models,model_reports=compile_coach_models(self.library,chosen,definitions,{c['id']for c in original_coaches})
                    portraits,portrait_reports=compile_portraits(chosen,definitions,self.library)
                    loader=None
                    if models:
                        with engine_file.open()as engine:loader=verify_coach_model_loader(engine,self.profile_resources)
                    return resources,definitions,new_links,models,model_reports,portraits,portrait_reports,loader
                def preserve_coaches(error):
                    for coach in body.get('coaches',[]):self.omit_item(body,'coaches',coach,error)
                    return {},[],[],[],[],[],[],None
                coach_resources,coaches,new_link_routes,coach_model_art,coach_model_report,coach_art,coach_portrait_report,coach_model_loader=self.local_operation(
                    'coaches',compile_coach_group,snapshots=(coach_registry,),fallback=preserve_coaches)
                compiled['coaches']={c['uuid']for c in coaches}
                kept=retained(inherited,writing,selected,compiled)
                old_coaches=[c for c in original_coaches if c['uuid']in compiled['coaches']]
                if kept.get('coach_models')and coach_model_loader is None:
                    with engine_file.open()as engine:coach_model_loader=verify_coach_model_loader(engine,self.profile_resources)
                art.extend(coach_art)
                from coach_heads import replacement_indices as head_replacements
                from coach_models import compile_stock_models,collect_head_resources
                self.update(message='编译教练三维头模')
                self.step('stock_heads')
                head_art,coach_head_report=self.local_operation('stock_heads',
                    lambda:compile_stock_models(self.library,body.get('stock_coach_models',[]),coach_catalog),
                    fallback=lambda error:self.preserve_feature(body,'stock_heads',error,([],[])))
                art.extend(collect_head_resources(coach_model_art,coach_model_report,head_art,coach_head_report))
                from world_workspace import compile_stadium
                from tournament_runtime import configuration as tournament_configuration
                world=body.get('world_workspace');stadium_art=[];stadium_report={'enabled':False};stadium_config=inherited.get('stadium',{'enabled':False});tournament_config=body['tournament_config']
                self.step('stadium')
                if world is not None:
                    if self.library.profile.get('game_version')!='V17'and(world['stadium']['enabled']or tournament_config):raise ValueError('球场与赛事仅适用于已核对的 V17 接口')
                    self.update(message='编译球场 TIFO、场边装置与赛事规则')
                    stadium_art,stadium_config,stadium_report=self.local_operation('stadium',
                        lambda:compile_stadium(self.store,self.library,world),
                        fallback=lambda error:self.preserve_feature(body,'stadium',error,
                            ([],inherited.get('stadium',{'enabled':False}),{'enabled':False})))
                    art.extend(stadium_art)
                stadium_stock=[{'target':'PesMobile/Content/'+p[6:]+'.uasset','private_packages':[a[0]for a in stadium_art if '/PlayerStudio/Stadium/'in a[0]]}for p in stadium_report.get('native_table_paths',[])]
                from native_draw_v22 import compile_assets as compile_draw_assets
                self.step('draw')
                draw_art,draw_replacements,draw_stock_groups,draw_report=([],{},[],{'enabled':False})if disabled(body,'draw')else self.local_operation(
                    'draw',lambda:export_call('抽卡演出','V22 音效蓝图与音效库','编译抽卡资源',compile_draw_assets,
                        self.library,self.profile_resources,source,content_replacements),
                    fallback=lambda error:self.preserve_feature(body,'draw',error,([],{},[],{'enabled':False})))
                art.extend(draw_art)
                from card_presentation import repair_routes
                carried_aliases=set().union(*(owners[p['identity']['package_id']]['aliases']for p in kept.get('players',[])))if kept.get('players')else set()
                carried_routes=[r for r in inherited.get('native_binding',{}).get('routes',[])if int(r['cardId'])in carried_aliases]
                prior_card_routes=repair_routes(copy.deepcopy(carried_routes),kept.get('players',[]),records)
                from card_motion import prepare as prepare_card_motion,replacement_indices as motion_replacements
                self.update(message='编译大小头像的动态背景与材质')
                self.step('card_motion')
                def preserved_motion(error):
                    if any(row.get('private_movie_textures')for row in bindings):raise error
                    for p in projects:body.get('write_designs',{}).get('players',{}).pop(p['package_id'],None)
                    return self.preserve_feature(body,'card_motion',error,
                        {'bindings':bindings,'routes':prior_card_routes,'reports':[],'packages':[],'stock_routes':[]})
                motion=({'bindings':bindings,'routes':prior_card_routes,'reports':[],'packages':[],'stock_routes':[]}
                    if disabled(body,'card_motion')else self.local_operation('card_motion',
                        lambda:prepare_card_motion(self.library,bindings,prior_card_routes,
                            records,body['export_settings']['card_motion']['enabled'],self.profile_resources),
                        fallback=preserved_motion))
                bindings=motion['bindings'];inherited_card_routes=motion['routes']
                card_motion_report=motion['reports'];art.extend(motion['packages'])
                for name,raw in motion.get('movie_files',{}).items():
                    if name in private_movie_files and private_movie_files[name]!=raw:raise ValueError('小头像兼容视频与自制视频资源名冲突：'+name)
                    private_movie_files[name]=raw
                self.update(message='合并原生球员、外观、技能与增能资料表')
                timings.mark('coach_and_card_motion')
                entry_art=[];entry_stock=[]
                self.step('tournaments')
                if tournament_config:
                    from tournament_entry import artwork,image_files
                    entry_art=artwork(self.library);art.extend(entry_art);entry_files,entry_routes=image_files(tournament_config,self.store)
                    entry_stock=[{'target':'PesMobile/Content/'+p[6:]+'.uasset','private_packages':[]}for p,_,_ in entry_art]
                    cover_files.update(entry_files);cover_routes.update(entry_routes)
                self.step('player_data')
                cpk,table_report=self.local_operation('player_data',lambda:compile_tables(original,projects,definitions,self.resources,
                    bindings,coach_resources,replace_ids,replace_boosters,source_cpk=original_cpk,
                    tournaments=bool(tournament_config),appearance_donors=appearance_donors))
                removed_aliases=set().union(*(p['aliases']for uid,p in owners.items()if writing=='full'and uid not in selected['players']))if owners else set()
                cpk=prune_tables(cpk,removed_aliases,{c['id']for c in retired_coaches})
                cpk=recycle_tables(cpk,self.store)
                timings.mark('cpk_tables_and_text')
                self.step('iostore')
                src=source_container(source,work,engine_key)
                out=work/'pc3700_mobile_and.utoc'
                from native_body import container_parts
                additions,paths,packages=container_parts(art)
                removed_coach_ids={c['id']for c in retired_coaches}
                retired_prefixes=tuple(prefix+str(cid)+'_'for cid in removed_coach_ids for prefix in ('SK_','MI_','T_'))
                retired_model_paths={index for path,index in src.paths.items()if retired_prefixes and '/character/RealFace/'in path and path.rsplit('/',1)[-1].startswith(retired_prefixes)}
                owned_replacements=personal_source.owned_art_paths(src,selected_owners)|owned_paths(src,[c['id'] for c in old_coaches+retired_coaches])|retired_model_paths
                from private_card_motion import replacement_indices as private_movie_replacements
                replace_art=owned_replacements|motion_replacements(src,motion['packages'])|private_movie_replacements(src,private_animation_art)|head_replacements(src,head_art+coach_model_art+draw_art+stadium_art+entry_art)
                resource_build=self.local_operation('iostore',lambda:append_container(src,out,additions,paths,packages,replace_art,cache=compile_cache,remove_indices=owned_replacements))
                timings.mark('iostore_build')
                replacements={**content_replacements,PREFIX+NATIVE+'dt870_mobile_and.cpk':cpk,
                              PREFIX+'livedata/dt870_mobile_and.cpk':cpk}
                replacements.update(gameplay_replacements)
                replacements.update(draw_replacements)
                replacements.update(recycle_seed(source,self.store))
                from coach_heads import patch_stock_containers
                self.update(message='替换教练头模与抽卡音效原始容器，核对加载路径')
                self.step('stock_containers')
                stock_head_replacements,stock_head_report=self.local_operation('stock_containers',
                    lambda:patch_stock_containers(source,self.library,head_art+draw_art+stadium_art+entry_art,
                        coach_head_report+draw_stock_groups+stadium_stock+entry_stock,work))
                replacements.update(stock_head_replacements)
                timings.mark('coach_stock_containers')
                native_movie_files=motion.get('native_movie_files',{})
                movie_package_report=None
                self.step('private_motion')
                packaged_private_movies={} if movie_append else private_movie_files
                if packaged_private_movies or native_movie_files:
                    from movie_package import merge,MEMBER
                    self.update(message='核对动态卡面视频包缓存并合并新增视频')
                    replacements[MEMBER],movie_package_report=self.local_operation('private_motion',
                        lambda:merge(source,replacements.get(MEMBER),packaged_private_movies,native_movie_files,work,compile_cache))
                timings.mark('movie_package')
                for suffix in ('.utoc','.ucas'):replacements[PREFIX+NATIVE+'pc3700_mobile_and'+suffix]=FilePayload.from_path(out.with_suffix(suffix))
                # Compress private textures belonging to the compiled player definitions.
                from custom_card_compression import owners_for_export,recompress_archive
                compression_owners=owners_for_export(definitions,inherited,identities,bindings)
                if compression_owners:self.update(message='检查并压缩本次新增和修改球员的贴图')
                self.step('texture_compression')
                compression_updates,forced_texture_report=({}, {'enabled':False})if disabled(body,'texture_compression')else self.local_operation(
                    'texture_compression',lambda:recompress_archive(source,replacements,compression_owners,
                        engine_key,work,cache_dir=self.store.root/'compiler-cache/texture-etc2',
                        native_folder=getattr(self.library,'native',None),progress=lambda message:self.update(message=message)),
                    fallback=lambda error:self.preserve_feature(body,'texture_compression',error,({}, {'enabled':False})))
                replacements.update(compression_updates)
                timings.mark('retained_custom_card_texture_compression')
                # Original activation verifies all four files, including its empty PAK.
                resource_files={};original_resource_files={}
                for name in ('dt870_mobile_and.cpk','pc3700_mobile_and.pak','pc3700_mobile_and.utoc','pc3700_mobile_and.ucas'):
                    if name=='dt870_mobile_and.cpk':original_resource_files[name]=payload_metadata(original)
                    else:
                        with source.open(PREFIX+NATIVE+name)as stream:info=measure(stream)
                        original_resource_files[name]={k:info[k]for k in('bytes','sha256')}
                    value=replacements.get(PREFIX+NATIVE+name)
                    resource_files[name]=original_resource_files[name]if value is None else payload_metadata(value)
                timings.mark('resource_hashes_and_payloads')
                runtime_phase='runtime_hooks_and_manifests'
                template_path=PREFIX+'work/templates/CMD_LOGIN_LIVEDATA.json'
                live_template=_export_data("资料文件",template_path,"读取并解析 JSON",lambda: json.loads(source.read(template_path)))
                live_registry_path=self.store.root/'native-live-versions.json'
                from native_live import container_metadata,content_fingerprint
                live_containers=container_metadata(source,replacements,live_template)
                live_fingerprint=content_fingerprint(resource_files['dt870_mobile_and.cpk']['sha256'],live_containers)
                live_version,live_registry=_reserve_live_version(self.library,self.store,release['release_id'],live_template,live_fingerprint)
                live_template=compile_template(live_template,cpk,live_version,live_containers)
                live_report=verify_template(live_template,cpk)
                replacements[template_path]=compact(live_template)
                binding_source=(Path(getattr(sys,'_MEIPASS',Path(__file__).absolute().parent))/'personal_binding.py').read_text(encoding='utf8')
                division_source=(Path(getattr(sys,'_MEIPASS',Path(__file__).absolute().parent))/'division_squads.py').read_text(encoding='utf8')
                rating_source=(Path(getattr(sys,'_MEIPASS',Path(__file__).absolute().parent))/'native_overall.py').read_text(encoding='utf8')
                helper_sources={name:(Path(getattr(sys,'_MEIPASS',Path(__file__).absolute().parent))/(name+'.py')).read_text(encoding='utf8')
                                for name in WORLD_HELPERS+('ai_tactical_profiles','ai_division_sessions','ai_match_manager','ai_match_channel','ai_match_runtime','ai_match_state','action_trace','league_rules','art_registry','native_diy_binding','draw_v22')}
                from tournament_schedule_ui import runtime_source as schedule_runtime_source
                helper_sources['tournament_schedule_ui']=schedule_runtime_source(helper_sources['tournament_schedule_ui'],self.profile_resources/'tournament_schedule_ui')
                from tournament_hub import runtime_source as hub_runtime_source
                helper_sources['tournament_hub']=hub_runtime_source(helper_sources['tournament_hub'],self.profile_resources/'tournament_hub',self.profile_resources/'catalog.json')
                coaching_fingerprint=hashlib.sha256((self.profile_resources/'native-ai-match-adapter.json').read_bytes()).hexdigest()
                from studio_resources import bundled_resources
                trace_fingerprint=hashlib.sha256((bundled_resources()/'native-action-trace.json').read_bytes()).hexdigest()
                if tournament_config:
                    trace_fingerprint+=hashlib.sha256((self.profile_resources/'tournament_schedule_ui/native-entry.json').read_bytes()).hexdigest()
                    trace_fingerprint+=hashlib.sha256((self.profile_resources/'tournament_schedule_ui/native-home.json').read_bytes()).hexdigest()
                timings.mark('live_template_and_runtime_sources',group=runtime_phase)
                compiler_fingerprint=hashlib.sha256((self.runtime_source+'\0'+binding_source+'\0'+division_source+'\0'+rating_source+'\0'+'\0'.join(helper_sources.values())+'\0'+coaching_fingerprint+'\0'+trace_fingerprint+'\0'+COMPILER_REVISION).encode('utf8')).hexdigest()
                document={'compiler_fingerprint':compiler_fingerprint,'game_version':self.library.profile.get('game_version','V14'),'adapter_family':self.library.profile.get('family','V14'),'draw_profiles':_export_data("资料文件",self.profile_resources / 'native-draw-profiles.json',"读取并解析 JSON",lambda: json.loads((self.profile_resources/'native-draw-profiles.json').read_text(encoding='utf8'))),'format':'personal-player-native-v1','version':1,'players':definitions,'packs':packs,'coaches':coaches,
                          'training_groups':catalog['training_groups'],'resource_files':resource_files,
                          'original_resource_files':inherited.get('original_resource_files',original_resource_files),'live_data':live_report,
                          'card_series_by_id':card_series(cpk),
                          'preview_boosters':{str(b['id']):{'effects':b['effects'],'conditional':bool(b.get('conditional'))} for b in catalog['boosters']}}
                self.step('tournaments')
                document['players']=list(all_definitions.values())
                document.update(stadium=stadium_config,stadium_preflight=stadium_report,tournaments=tournament_config,gameplay=gameplay_report)
                if tournament_config:document['tournament_entry_image']=next(iter(entry_routes))
                if tournament_config:
                    document['tournament_entry_images']={shape:next(path for path,entry in entry_routes.items()if entry['path'].endswith('-'+shape+'.jpg'))for shape in('portrait','landscape')}
                    from team_names import display as team_display
                    document['tournament_team_names']={str(r['id']):team_display(r)['name']for r in self.library.identities()['team']}
                    from world_workspace import national_teams
                    document['tournament_national_team_nations']=national_teams(self.library)
                    from tournament_schedule_ui_emblems import extract as extract_schedule_emblems
                    emblems=extract_schedule_emblems(self.library.containers,
                        sorted({team for t in tournament_config for team in t['team_ids']}),self.library.native,
                        nation_ids=document['tournament_national_team_nations'])
                    document['tournament_emblems']=emblems['teams']
                    document['tournament_emblem_report']={k:v for k,v in emblems.items()if k!='teams'}
                from world_workspace import unlock_catalogue
                document['stadium'].setdefault('unlock_all',True)
                unlock=unlock_catalogue()
                document['stadium']['unlock_catalogue']=unlock
                # Tournament rules use the scoped native match channel even
                # when the optional automatic coach is switched off.
                needs_coaching=bool(tournament_config)
                document['draw_presentation_revision']=draw_report.get('revision')if draw_report['enabled']else None
                document['draw_presentation_assets']=draw_report
                document['content_catalogue']=content_catalogue
                document['catalogue_recycle_bin']=recycle_policy(self.store)
                from effective_players import from_cpk as effective_player_catalogue
                document['native_player_catalogue']=effective_player_catalogue(cpk,document)
                document['catalogue_recycle_policy']='remove-owned-exact-cards; backup-save-before-removal; shared-art-retained'
                if tournament_config:
                    from tournament_eligibility import load_eligibility_index,eligible_teams
                    index=load_eligibility_index(self.library,document)
                    team_ids=sorted({team for t in tournament_config for team in t['team_ids']})
                    availability={str(team):{}for team in team_ids}
                    for include,key in((True,'with_diy'),(False,'without_diy')):
                        for cpu,side in((True,'cpu'),(False,'player')):
                            for team,facts in eligible_teams(self.library,document,team_ids,include_diy=include,cpu=cpu,index=index).items():
                                availability[team].setdefault(key,{})[side]=facts
                    del index
                    document['tournament_team_availability']=availability
                    from tournament_squads import export_standard_cards,export_fallback_standard_cards
                    document['tournament_standard_cards']=export_standard_cards(self.library,document)
                    # Reserve the manifest field order before building the large fallback pool.
                    document['tournament_fallback_standard_cards']={}
                    document['tournament_standard_card_policy']={'unknown_level_limit':35,'scope':'private-tournament-squads','source':'selected-native-library; effective native tables and final deletion list'}
                document['coach_heads']=coach_head_report
                document['coach_head_containers']=stock_head_report
                document['coach_models']=personal_source.merge(kept.get('coach_models',[]),coach_model_report,'uuid')
                document['coach_model_loader']=coach_model_loader
                document['pack_policy']={'allow_china_edition':True}
                document['game_options']=copy.deepcopy(body['export_progress'])
                export_settings=body['export_settings']
                document['game_settings']=export_settings
                document['card_motion_preflight']=card_motion_report
                document['frame_rate']={**export_settings['frame_rate'],'force_160':export_settings['frame_rate']['enabled'] and export_settings['frame_rate']['target']==160}
                document['installation']=installation
                self.step('display')
                def preserve_display(error):
                    self.preserve_feature(body,'display',error,None)
                    self.preserve_feature(body,'frame_rate',error,None)
                    document['frame_rate'].update(enabled=False,force_160=False)
                    return {'enabled':False}
                if export_settings['display']['enabled'] or export_settings['frame_rate']['enabled']:
                    from native_display import verify_display
                    with engine_file.open() as render_engine:
                        document['render_preflight']=self.local_operation('display',
                            lambda:verify_display(render_engine,self.profile_resources),fallback=preserve_display)
                self.step('frame_rate')
                if document['frame_rate']['enabled']:
                    from native_fps import verify_fps160
                    with engine_file.open() as fps_engine:
                        document['frame_rate']['preflight']=self.local_operation('frame_rate',
                            lambda:verify_fps160(apk,fps_engine,document['frame_rate']['target']),fallback=preserve_display)
                from pack_catalogue import REFERENCE
                excluded=_export_data("资料文件",REFERENCE,"读取并解析 JSON",lambda: read_archive_json(source,REFERENCE,limit=None)) if REFERENCE in source.namelist() else {}
                excluded={cid:row for cid,row in excluded.items() if row.get('type')not in ('China edition','Workshop recycle bin')}
                excluded.update({cid:{'name':row['name'],'type':'Workshop recycle bin'}for cid,row in recycled.items()})
                replacements[REFERENCE]=compact(excluded)
                document['packs']=refresh_packs(personal_source.merge(kept.get('packs',[]),packs,'uuid'),inherited.get('players',[]),document['players'])
                final_pack_ids={p['uuid']for p in document['packs']}
                document['coaches']=personal_source.merge(kept.get('coaches',[]),coaches,'uuid')
                document['write_selection']={'mode':writing,'removed':removals(inherited,document),'source_players':len(inherited.get('players',[])),
                    'selected_players':len(selected['players']),'compiled_players':len(projects),'retained_players':len(document['players'])-len(projects),
                    'final_players':len(document['players']),'final_coaches':len(document['coaches']),'final_packs':len(document['packs'])}
                fallback=next((c for c in coach_catalog if c.get('raw_payload')and c.get('raw_payload_verified',True)),None)
                if fallback:document['write_selection']['fallback_coach']=copy.deepcopy(fallback['raw_payload'])
                document['write_designs']=final_designs(inherited.get('write_designs',{}),body.get('write_designs',{}),document)
                for uid in adjusted_packs:document['write_designs']['packs'].pop(uid,None)
                self.step('ai_match')
                if document['game_version']=='V17':
                    from division_squads import configuration
                    from pack_catalogue import exclusions
                    document['division_squads']=configuration(catalog,exclusions(self.resources),export_settings['ai_division_squads'])
                    from division_squads import random_booster_pool
                    custom_ids=set(booster_registry.values())|set(inherited.get('editor_registries',{}).get('boosters',{}).values())
                    document['division_squads']['random_boosters']=random_booster_pool(catalog,cpk,custom_ids)
                    from division_squads import empty_booster_ids
                    document['division_squads']['empty_booster_ids']=empty_booster_ids(cpk,custom_ids)
                    from division_squads import booster_catalogue
                    document['native_booster_catalogue']=booster_catalogue(cpk)
                    document['division_squads']['excluded_ids']=list(set(document['division_squads']['excluded_ids'])|set(recycled))
                    document['division_squads']['style_mode']=document['game_options'].get('style_mode','shuffle_bag')
                    if tournament_config:
                        from world_workspace import selection_warnings, validate_selection_warnings
                        self.update(message='校验锦标赛最终参赛阵容')
                        document['tournament_selection_warnings']=selection_warnings(self.library,self.store,tournament_config,document=document)
                        validate_selection_warnings(document['tournament_selection_warnings'])
                    if document['division_squads']['enabled'] or needs_coaching:
                        from native_overall import verify_engine as verify_rating_engine
                        with engine_file.open() as rating_engine:
                            document['division_squads']['native_rating_preflight']=verify_rating_engine(rating_engine)
                if tournament_config:
                    self.step('tournaments','生成锦标赛备用球员目录')
                    document['tournament_fallback_standard_cards']=export_fallback_standard_cards(self.library,document)
                document['preview_boosters']={**kept.get('preview_boosters',{}),**document['preview_boosters']}
                for p in projects:
                    with export_context('球员',p,'分配和编译自定义增能'):
                        for b in p.get('custom_boosters',[]):document['preview_boosters'][str(b['id'])]={'effects':b['effects'],'conditional':bool(b.get('conditional'))}
                from overall import refresh_definition_ratings
                document['rating_cache_migration']=refresh_definition_ratings(document,catalog)
                from tournament_entry import merge_image_routes
                document['pack_covers']=merge_image_routes(kept.get('pack_covers',{}),cover_routes)
                document['editor_registries']={'boosters':booster_registry,'coach_boosters':coach_registry.get('boosters',{}),'coach_links':coach_registry.get('links',{})}
                document['coach_links']=list({r['id']:r for r in [*kept.get('coach_links',[]),*new_link_routes]}.values())
                for relative,content in cover_files.items():replacements[PREFIX+relative]=content
                timings.mark('runtime_document_and_catalogues',group=runtime_phase)
                self.step('native_binding')
                if document['game_version']=='V17' or identities or inherited.get('native_binding') or content_catalogue['routes'] or document['coach_links'] or tournament_config or export_settings['controller']['enabled'] or export_settings['card_motion']['enabled']:
                    profile=_export_data("资料文件",self.resources / 'native-route-templates.json',"读取并解析 JSON",lambda: json.loads((self.resources/'native-route-templates.json').read_text(encoding='utf8')))
                    profile['club_emblems']=True
                    profile['external_routes']=True
                    profile['position_hook']=_export_data("资料文件",self.resources / 'native-position-hook.json',"读取并解析 JSON",lambda: json.loads((self.resources/'native-position-hook.json').read_text(encoding='utf8')))
                    profile['acquisition_hook']=_export_data("资料文件",self.resources / 'native-acquisition-hook.json',"读取并解析 JSON",lambda: json.loads((self.resources/'native-acquisition-hook.json').read_text(encoding='utf8')))
                    profile['overall_hook']=_export_data("资料文件",self.resources / 'native-overall-hook.json',"读取并解析 JSON",lambda: json.loads((self.resources/'native-overall-hook.json').read_text(encoding='utf8')))
                    mini_profile=self.resources/'native-thumbnail-hook.json'
                    if not mini_profile.is_file():mini_profile=self.profile_resources/'native-thumbnail-hook.json'
                    profile['thumbnail_hook']=_export_data("资料文件",mini_profile,"读取并解析 JSON",lambda: json.loads(mini_profile.read_text(encoding='utf8')))
                    # Imported databases may carry an older copy of this
                    # interface. Upgrade only an identical native entry point.
                    current_mini=_export_data("资料文件",self.profile_resources / 'native-thumbnail-hook.json',"读取并解析 JSON",lambda: json.loads((self.profile_resources/'native-thumbnail-hook.json').read_text(encoding='utf8')))
                    if current_mini.get('front_date_offset') is not None:
                        if any(profile['thumbnail_hook'].get(k)!=current_mini.get(k) for k in ('address','original_call','assign','mini_offset','expected_hex')):raise ValueError('此版本的大卡日期接口不同，未套用其他版本的图层位置')
                        for k in ('front_date_offset','front_date_evidence'):profile['thumbnail_hook'][k]=current_mini[k]
                    profile['thumbnail_hook']['stock_routes']=motion['stock_routes']
                    self.update(message='核对并写入技能、手柄与 AI 游戏适配资料')
                    from native_extensions import link_hooks
                    with engine_file.open() as engine:
                        profile['native_hooks']=export_call('游戏资料','native-coach-links.json','编译教练心有灵犀适配',lambda:link_hooks(document['coach_links'],engine,link_schema(self.profile_resources)))
                        self.step('controller')
                        from game_settings import controller_hooks
                        def preserve_controller(error):
                            export_settings['controller']['enabled']=False
                            return self.preserve_feature(body,'controller',error,[])
                        profile['native_hooks']+=self.local_operation('controller',
                            lambda:export_call('游戏资料','native-controller.json','编译手柄键位',controller_hooks,
                                export_settings['controller'],engine,self.profile_resources),fallback=preserve_controller)
                        self.step('skill_effects')
                        from native_skill_effects import hooks as skill_hooks,required as skill_effects_required
                        if feel is not None or skill_effects_required(document['players'],catalog):
                            profile['native_hooks']+=export_call('游戏资料',self.profile_resources/'native-skill-effects.json','核对并写入操作响应与比赛技能效果',skill_hooks,engine,self.profile_resources,definitions=document['players'],feel=feel)
                        self.step('ai_style')
                        from native_ai_style_ui import preflight as ai_style_preflight
                        document['ai_style_display']=({'enabled':False}if disabled(body,'ai_style')else self.local_operation(
                            'ai_style',lambda:ai_style_preflight(document['players'],profile['native_hooks']),
                            fallback=lambda error:self.preserve_feature(body,'ai_style',error,{'enabled':False})))
                        if document['game_version']=='V17':
                            from native_action_trace import hooks as action_trace_hooks,REVISION as ACTION_TRACE_REVISION
                            document['action_trace']={'enabled':export_settings['action_trace']['enabled'],'revision':ACTION_TRACE_REVISION,
                                                      'coverage':'native states, motion attempts and movement samples; candidate pass/dribble/shot before/after; request/power and native control flags; full-match acceptance pending'}
                            self.step('ai_match')
                            if document.get('division_squads',{}).get('enabled') or needs_coaching:
                                from native_ai_match import hooks as match_hooks
                                profile['native_hooks']+=export_call('游戏资料','native-ai-match-adapter.json','编译 AI 组队与临场调整适配',match_hooks,engine,self.profile_resources)
                                document['division_squads']['native_coaching_phase']='verified native execution; situational policy v5; local scenario validation; gameplay acceptance pending'
                            from native_ai_cut_in_shots import hooks as ai_cut_in_hooks,REVISION as AI_CUT_IN_REVISION
                            from game_settings import ai_decision_options
                            ai_options=ai_decision_options(export_settings,document['players'])
                            self.step('action_trace' if export_settings['action_trace']['enabled']else'ai_cut_in_shots')
                            if export_settings['action_trace']['enabled']:
                                # One combined bundle owns each entry exactly once. Logging
                                # never replaces an enabled AI policy with native behavior.
                                if ai_options['enabled']:ai_cut_in_hooks(engine,ordinary_enabled=True,require_v8=gameplay_report.get('enabled',False))
                                profile['native_hooks']+=action_trace_hooks(engine,ai_enabled=ai_options['enabled'],require_v8=gameplay_report.get('enabled',False))
                            elif ai_options['enabled'] or gameplay_report.get('enabled'):
                                profile['native_hooks']+=export_call('游戏资料','native-ai-cut-in-shots.json','编译比赛与 AI 决策适配',ai_cut_in_hooks,engine,ordinary_enabled=ai_options['enabled'],require_v8=gameplay_report.get('enabled',False),include_v8=feel is None or gameplay_report['enabled'])
                            document['ai_cut_in_shots']={**ai_options,'experimental':False,'revision':AI_CUT_IN_REVISION,
                                                           'ordinary_choice':'v8'if gameplay_report.get('enabled')else'native',
                                                         'lone_wolf':{'probability':0.8,'scope':'independent native pass attempt in attacking half with legal native non-pass alternative',
                                                                      'native_shot_dribble_preserved':True,'candidate_order':'original native priority',
                                                                      'combined_incisive':'native possession with independent Lone Wolf'},
                                                           'priority':(['lone_wolf']if ai_options['enabled']else[])+(['v8_candidates']if gameplay_report.get('enabled')else[])+['original_native_candidate_order']}
                            self.step('skill_capacity')
                            from native_skill_capacity import hooks as capacity_hooks,REVISION as SKILL_CAPACITY_REVISION
                            profile['native_hooks']+=export_call('游戏资料','native-skill-capacity.json','编译技能数量与显示扩展',capacity_hooks,engine)
                            document['skill_capacity']={'innate':100,'additional':100,'display':'native-lists',
                                                        'revision':SKILL_CAPACITY_REVISION}
                    self.step('native_binding')
                    with engine_file.open() as engine:
                        for name in ('position_hook','acquisition_hook','overall_hook','thumbnail_hook'):
                            h=profile[name];engine.seek(h['file_offset'])
                            if engine.read(64).hex()!=h['expected_hex']:raise ValueError('此版本的小头像动画接口不同' if name=='thumbnail_hook' else '此版本的抽卡显示接口不同')
                        for evidence in profile['thumbnail_hook'].get('front_date_evidence',[]):
                            engine.seek(evidence['file_offset']);expected=bytes.fromhex(evidence['expected_hex'])
                            if engine.read(len(expected))!=expected:raise ValueError('此版本的 F09 日期图层布局不同，未生成错误的卡面绑定')
                    booster_routes={r['id']:r for r in inherited.get('native_binding',{}).get('profile',{}).get('boosters',[])}
                    booster_routes.update({r['id']:r for r in table_report['personal_booster_text_routes']})
                    if booster_routes or document['game_version']=='V17':
                        profile['boosters']=list(booster_routes.values())
                        profile['text_hooks']=_export_data("资料文件",self.resources / 'native-text-hooks.json',"读取并解析 JSON",lambda: json.loads((self.resources/'native-text-hooks.json').read_text(encoding='utf8')))
                        with engine_file.open() as engine:
                            for kind in ('name','description'):
                                hook=profile['text_hooks'][kind];engine.seek(hook['file_offset'])
                                if engine.read(64).hex()!=hook['expected_hex']:raise ValueError('此版本的增能文字接口不同')
                    from native_binding_source import read_installer
                    original_binding,installer_code=read_installer(apk,content_catalogue=content_catalogue)
                    routes=[r for r in inherited_card_routes if int(r['cardId']) not in replace_ids]
                    for identity,binding,definition in zip(identities,bindings,definitions):
                        for cid in sorted({identity['card_id'],*owned_person_aliases(identity),int(binding['asset_key']),*identity['previous_card_ids']}):
                            routes.append({'cardId':cid,'suiteDonorId':identity['card_id'],
                                           'portraitKey':binding['asset_key'],'modelFaceKey':binding['asset_key'],
                                           'thumbnail_names':binding['thumbnail_names'],
                                           **({'front_names':binding['front_names'],'private_movie_textures':True} if binding.get('front_names') else {}),
                                           'club_id':definition['owned']['initial_base_team_id'],
                                           'position':definition['base_parameters'][0],
                                           'maximum_overall':definition['maximum_development']['overall'],
                                           'presentation_rarity':10 if definition['card_type'] in ('BigTime','Epic','Legendary') else 0})
                    from card_presentation import repair_routes
                    routes=repair_routes(routes,document['players'],records)
                    self.step('competition_menu')
                    if (document['game_version']=='V17' or tournament_config) and not disabled(body,'competition_menu'):
                        menu=self.library.face_assets().get('PesMobile/Content/Assets/menu/online/evCompe/MainMenu/BP_MenuEvCompeEventMainMenu.uasset')[0]
                        with engine_file.open()as schedule_engine:
                            profile.setdefault('native_hooks',[]).extend(competition_menu_hooks(schedule_engine,self.profile_resources,
                                hashlib.sha256(menu).hexdigest(),tournament_config))
                    self.step('native_binding')
                    self.local_operation('native_binding',lambda:compose(original_binding,routes,profile))
                    document['native_binding']={'routes':routes,'profile':profile,'original_fingerprint':fingerprint(original_binding)}
                    from native_binding_source import installer_metadata
                    if installer_metadata(installer_code):
                        document['native_binding']['source']=installer_metadata(installer_code)
                        if installer_metadata(installer_code).get('registry'):
                            document['native_binding']['original_fingerprint']=installer_metadata(installer_code)['raw_fingerprint']
                            document['native_binding']['prepared_fingerprint']=fingerprint(original_binding)
                    from art_registry import seal_personal_order
                    document['native_binding']=seal_personal_order(document['native_binding'],original_binding)
                    # Run the same closure/fingerprint verification performed
                    # on Android before signing, so a startup mismatch cannot
                    # leave the desktop compiler as a successful APK.
                    from native_binding_source import verify_installer
                    document['native_binding_preflight']=verify_installer(installer_code,document['native_binding'])
                timings.mark('native_bindings_and_preflight',group=runtime_phase)
                self.step('runtime')
                from art_registry import verify_content
                document['content_binding_preflight']=verify_content(document)
                document['revision']=hashlib.sha256(compact(document)).hexdigest()
                from write_modes import patch_seed as patch_write_seed
                patch_write_seed(source,replacements,document)
                from runtime_document import partition as partition_runtime_document
                startup_document,sections=partition_runtime_document(document)
                replacements.update({PREFIX+name:value for name,value in sections.items()})
                replacements[PREFIX+'custom-resources/personal-player-studio/players.json']=compact(startup_document)
                if not player_update:
                    replacements['assets/chaquopy/app.imy']=hook_archive(source.read('assets/chaquopy/app.imy'),self.runtime_source,
                        binding_source,division_source,rating_source,helper_sources)
                    from export_options import cache_metadata
                    replacements['assets/chaquopy/build.json']=_export_data("资料文件",'assets/chaquopy/build.json',"更新 Python 资源清单",lambda:cache_metadata(source.read('assets/chaquopy/build.json'),replacements['assets/chaquopy/app.imy']))
                timings.mark('runtime_archive_and_seed',group=runtime_phase)
                release['personal_player_revision']=document['revision']
                release['base_release_id']=release.get('base_release_id',release['release_id'])
                release['release_id']=release['base_release_id']+'-studio-'+document['revision'][:16]
                listed={row['path']:row for row in release['files']}
                for name,value in replacements.items():
                    if not name.startswith(PREFIX):continue
                    rel=name[len(PREFIX):];row={'path':rel,**payload_metadata(value)}
                    if rel in listed:listed[rel].update(row)
                    else:release['files'].append(row)
                replacements[PREFIX+'release-manifest.json']=compact(release)
                replacements.update(install_replacements)
                from content_pack import plan_export as plan_content
                from resource_planner import estimate as estimate_apk
                from program_bootstrap import changes as startup_changes
                from resource_loader import changes as loader_changes
                loader_replacements,loader_report=loader_changes(source,self.profile_resources,installation['package'])
                replacements.update(loader_replacements)
                startup_id=str(uuid.uuid4())
                if not player_update:
                    supports_append=document['game_version']=='V17'
                    profile=self.library.profile
                    append_engine={'offset':profile['resource_key_offset'],'sha256':profile['resource_key_sha256']} if supports_append else None
                    experimental_movies=supports_append and os.environ.get('STUDIO_EXPERIMENTAL_MOVIE_PACKS')=='1'
                    if experimental_movies:
                        from player_movie_overlay import verify_experimental_slot
                        verify_experimental_slot(source,live_template,work,engine_key)
                    startup_changes(source,replacements,self.profile_resources,installation['package'],startup_id,
                        player_updates=supports_append,player_append_engine=append_engine,player_append_updates=supports_append,
                        game_version=document.get('game_version'),experimental_movie_overlay=experimental_movies)
                from program_content import complete_changes,describe,encode,BASE,split as split_content
                replacements=complete_changes(source,replacements)
                replacements[BASE]=encode(describe(source,replacements))
                program_remove=set(loader_report.get('retired_entries',()))|{'assets/player-studio/resource-set.json'}
                self.step('packaging','核对球员小包与基准 APK 的兼容性' if player_update else '按 APK 容量选择安装包或内容包')
                timings.mark('release_manifest_and_bootstrap',group=runtime_phase)
                capacity=estimate_apk(apk,replacements,remove_entries=program_remove,allow_oversized=True)
                timings.mark('apk_capacity_estimate',group=runtime_phase)
                def open_program(prior):
                    return Reader(prior['apk'],packages=prior.get('resource_packages'),metadata_only=True)
                if player_update:
                    from content_pack import plan
                    delivery=plan(self.store,installation['package'],source,replacements,open_program,
                        remove_entries=program_remove,player_update=True)
                    if delivery['kind']!='content_pack':
                        raise ValueError('本次改动涉及游戏程序或内置动态卡面资源，请使用完整 APK 导出')
                    delivery.update(apk_capacity=capacity,apk_limit=UINT32_LIMIT,policy='player-update-only')
                else:
                    delivery=plan_content(self.store,installation['package'],source,replacements,open_program,
                        capacity=capacity,limit=UINT32_LIMIT,program_id=startup_id,remove_entries=program_remove)
                if delivery['kind']=='content_pack':
                    staged_content=work/'content.73pack'
                    if player_update:
                        self.update(message='生成并逐项校验球员小包')
                        if body.get('_player_append_marker'):
                            from player_append import ACTIVE,LIVE,MANIFEST,TEMPLATE,CONTAINER,RELEASE
                            from player_append_export import require_runtime_hooks,write_append
                            allowed={ACTIVE,LIVE,MANIFEST,TEMPLATE,RELEASE,CONTAINER+'.utoc',CONTAINER+'.ucas'}
                            unsupported=delivery['files'].keys()-allowed
                            if unsupported or delivery['removed']:
                                raise ValueError('本次还改变了独立小包不支持的资源，请使用完整 APK：'+', '.join(sorted(unsupported|set(delivery['removed']))[:5]))
                            original_hooks=inherited['native_binding']['profile'].get('native_hooks',[])
                            final_hooks=document['native_binding']['profile'].get('native_hooks',[])
                            require_runtime_hooks(original_hooks,final_hooks)
                            toc=replacements[PREFIX+CONTAINER+'.utoc']
                            data=replacements[PREFIX+CONTAINER+'.ucas']
                            final_art=Container(toc.path,engine_key,data=data)
                            content_manifest=write_append(staged_content,body['_player_append_marker'],source_cpk,cpk,
                                final_art,document,compiled['players'],inherited=inherited,
                                store=self.store,target_game=document.get('game_version'),studio_version='3.0.16',
                                movie_files=private_movie_files if movie_append else None)
                            delivery['policy']='independent-player-append'
                        else:
                            from player_update_export import write_update
                            content_manifest=write_update(staged_content,base_path=delivery['base_path'],package=installation['package'],
                                program_id=delivery['program_id'],files=delivery['files'],removed=delivery['removed'],parent=delivery['parent'])
                    else:
                        self.update(message='游戏 APK 超出容量上限，生成兼容现有游戏程序的内容包')
                        from content_pack import write as write_content
                        content_manifest=write_content(staged_content,package=installation['package'],program_id=delivery['program_id'],
                            files=delivery['files'],removed=delivery['removed'])
                elif delivery['kind']=='program_content':
                    self.update(message='将游戏数据写入内容包，生成首次安装所需的程序 APK')
                    separated=split_content(source,replacements)
                    from content_pack import write as write_content
                    staged_content=work/'content.73pack'
                    content_manifest=write_content(staged_content,package=installation['package'],program_id=delivery['program_id'],files=separated['files'])
                    replacements=separated['changes'];program_remove.update(separated['removed'])
                    logical_changes=dict(separated['logical_changes'])
                    logical_changes['assets/player-studio/program.json']=replacements['assets/player-studio/program.json']
                    logical_changes[BASE]=replacements[BASE]
                    program_content={'path':staged_content,'filename':separated['filename']}
                    capacity=estimate_apk(apk,replacements,remove_entries=program_remove)
                    delivery['program_capacity']=capacity
                timings.mark('delivery_plan_and_content_pack',group=runtime_phase)
            del original_cpk,original,source_cpk,src,inherited
            import gc
            gc.collect()
            registry_updates={registry_path:compact(registry),booster_path:compact(booster_registry),
                              live_registry_path:compact(live_registry),asset_path:compact(asset_registry),coach_path:compact(coach_registry)}
            if delivery['kind']=='content_pack':
                name=('73efootball-球员小包-'if player_update else'73efootball-')+time.strftime('%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:8]+'.73pack'
                output=self.store.root/'exports'/name
                with publication_guard(self.library,self.store,checked):
                    output.parent.mkdir(parents=True,exist_ok=True)
                    os.replace(staged_content,output)
                    from content_pack import publish_registries
                    try:publish_registries(self.store,registry_updates)
                    except Exception as registry_error:
                        self.notice('registry_deferred','content_pack','内容包已生成，资料库记录将在下次生成时恢复。',error=registry_error)
                        self.update(registry_warning=str(registry_error))
                result={'kind':'content_pack','name':name,'path':str(output),'url':'/exports/'+name,
                        'bytes':output.stat().st_size,'size':output.stat().st_size,'files':content_manifest.get('materialized_files',len(content_manifest['files'])),'program_id':delivery['program_id'],
                        'installation':installation,'apk_capacity':delivery['apk_capacity'],'apk_limit':delivery['apk_limit'],
                        'delivery_policy':delivery['policy'],'timings':timings.snapshot()}
                self.remember_installation_mode(apk,body.get('installation_mode','update'))
                self.update(phase='done',message='内容包已生成。保存文件后，在文件管理器中选择使用对应游戏打开，下次启动游戏时应用。',result=result)
                return
            self.step('packaging','整理游戏程序与内容包文件')
            assert_export_current(self.library,self.store,checked)
            from resource_export import prepare as prepare_resource_set,publish as publish_resource_set
            game_name='73efootball-'+self.library.profile.get('game_version','V14')+('-工坊共存-' if body.get('installation_mode')=='coexist' else '-个人球员-')+time.strftime('%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:4]+'.apk'
            signing_phase='resource_set_signing_and_cross_package_verification'
            prepared_set=prepare_resource_set(self.store,self.library,apk,replacements,work,game_name=game_name,
                installation=installation,resource_packages=checked.get('resource_packages',{}),companion=body.get('companion'),profile_resources=self.profile_resources,
                progress=lambda message:self.update(message=message),measured=delivery.get('program_capacity',delivery['apk_capacity']),
                remove_entries=program_remove,content=program_content,logical_changes=logical_changes)
            timings.mark('apk_build_sign_and_resource_verification',group=signing_phase)
            # Verify the actual signed JSON, not the pre-serialization dict.
            # Sorting/repacking must preserve both stock and personal dispatch.
            from native_binding_source import verify_apk_startup
            art_startup_readback=verify_apk_startup(prepared_set['game'],prepared_set['packages'])
            from effective_players import verify_apk as verify_effective_catalogue
            effective_catalogue_readback=verify_effective_catalogue(prepared_set['game'],prepared_set['packages'],
                observed={**prepared_set['live'],'files':prepared_set['stamps']},expected=document['native_player_catalogue'])
            assert_export_current(self.library,self.store,checked)
            apk_build=prepared_set['apk_build'];signing_performance=prepared_set['signing_performance']
            verified=prepared_set['signature'];live_apk_report=prepared_set['live']
            output=prepared_set['destination']/game_name
            timings.mark('signed_startup_and_catalogue_readback',group=signing_phase)
            self.step('publication','保存永久编号注册表和导出报告')
            report={'identities':identities,'native_art':art_reports,'packs':[{'uuid':p['uuid'],'agent_id':p['agent_id'],'name':p['name'],'members':len(p['members']),'skipped_members':p.get('skipped_members',[])} for p in packs],
                    'native_tables':table_report,'signature':verified,'live_data':live_report,'live_apk_preflight':live_apk_report,'content_binding_preflight':document['content_binding_preflight'],
                    'art_startup_readback':art_startup_readback,
                    'effective_catalogue_readback':effective_catalogue_readback,
                    'apk_compaction':apk_build['compaction'],'resource_compaction':resource_build.compaction_report,
                    'compiler_cache':compile_cache.report()if compile_cache else{'enabled':False,'warning':cache_warning},
                    'texture_compression':texture_reports,'retained_custom_card_compression':forced_texture_report,
                    'resource_set':prepared_set['report'],'resource_loader':loader_report,
                    'signing_performance':signing_performance,
                    'export_io':{'revision':'2.1.3','engine_full_hash_verified':True,'engine_extractions':1,
                                 'engine_bytes':engine_file.bytes,'engine_sha256':engine_file.sha256,
                                 'file_payloads':sum(isinstance(v,FilePayload)for v in replacements.values()),
                                 'file_payload_bytes':sum(v.bytes for v in replacements.values()if isinstance(v,FilePayload)),
                                 'movie_package':movie_package_report,'replacement_verification':'streamed-sha256-size-and-zip-crc'},
                    'timings':timings.snapshot(),'cpk_decode':decode_statistics(),
                    'native_binding_routes':len(document.get('native_binding',{}).get('routes',[])),'frame_rate':document['frame_rate'],'game_settings':export_settings,'match_feel':gameplay_report.get('applied_feel'),'render_preflight':document.get('render_preflight'),'card_motion_preflight':card_motion_report,'installation':installation,
                    'coaches':[{'uuid':c['uuid'],'id':c['id'],'name':c['payload']['name']} for c in coaches],'coach_portraits':coach_portrait_report,'coach_heads':coach_head_report,'coach_head_containers':stock_head_report,'coach_models':coach_model_report,'coach_model_loader':coach_model_loader,
                    'write_selection':document['write_selection'],'content_autofill':content_sync_report,'source_apk':str(apk),
                    'delivery_policy':delivery['policy'],'apk_capacity':delivery['apk_capacity'],'apk_limit':delivery['apk_limit'],
                    'source_preflight':{'library_apk':checked['library_path'],'selected_apk':checked['selected_path'],
                        'library_package':checked['library_identity']['package'],'selected_package':checked['selected_identity']['package'],
                        'real_signature_verification':True,'adapted_base_interface_verified':True,
                        'identity_relation':checked['library_identity'].get('identity_relation','previous_verified_anchor')},
                    'output':str(output),'engine_profile':release['base_release_id'],'resource_release':release['release_id'],
                    'in_game_tested':False,'progress_policy':'first grant only; existing instance retains live XP, training and statistics'}
            registry_updates={registry_path:compact(registry),booster_path:compact(booster_registry),
                              live_registry_path:compact(live_registry),asset_path:compact(asset_registry),coach_path:compact(coach_registry)}
            recovery=getattr(self._job_local,'recovery',None)
            if recovery is not None:report['diagnostics']=recovery.diagnostics()
            with publication_guard(self.library,self.store,checked):
                result=publish_resource_set(self.store,prepared_set,report,registry_updates=registry_updates,defer_commit=True)
                output=Path(result['path']);output_created=True
            for warning in result.get('report',{}).get('warnings',[]):
                self.notice(warning['code'],warning['item'],warning['message'],detail=warning.get('detail',''))
            from export_publication import finish as finish_publication
            finish_publication(self.store)
            from content_pack import remember_program
            published_packages={}
            for artifact in result.get('artifacts',[]):
                if artifact['kind']=='content_pack':
                    published_packages['content_pack']=artifact['path'];continue
                dependency=artifact.get('dependency',{})
                if dependency.get('role')=='game':continue
                if dependency.get('id'):published_packages[dependency['id']]=artifact['path']
                if dependency.get('package'):published_packages[dependency['package']]=artifact['path']
            program_registered=False
            from export_delivery import expose
            if not program_content and os.name=='nt':
                from managed_game_base import publish as publish_base
                try:
                    result=publish_base(self.store,result,installation['package'],delivery['program_id'],
                                        source=apk,library=self.library)
                    program_registered=True
                    if result.get('storage_warning'):
                        self.notice('managed_base_cleanup','apk',result['storage_warning'])
                except (OSError,ValueError,KeyError) as base_error:
                    self.notice('managed_base','apk','专用底包未登记，仍使用完整导出资源集。',error=base_error)
            try:
                if not program_registered:
                    remember_program(self.store,installation['package'],delivery['program_id'],output,
                                     published_packages,source=apk)
                    program_registered=True
            except Exception as record_error:
                self.notice('program_record','apk','程序已生成，稍后将重新读取程序记录。',error=record_error)
            if not result.get('base_apk'):
                result=expose(self.store,result)
            timings.mark('registry_saving')
            self.remember_installation_mode(apk,body.get('installation_mode','update'))
            if program_registered and not program_content:
                from storage_usage import review_after_export
                try:
                    review=review_after_export(self.store,self.library.home)
                    if review is not None:result['storage_review']=review
                except (OSError,ValueError,RecursionError) as review_error:
                    self.notice('storage_review','apk','旧 APK 清理预览暂未生成，请在存储管理中查看。',error=review_error)
            self.update(phase='done',message='游戏程序 APK 和内容包已生成，请先安装程序，再导入对应内容包'if program_content else'游戏 APK 已生成并通过签名与内容检查',result=result)
        except Exception as exc:
            import logging
            logging.exception('Native APK compilation failed')
            from apk_sources import SourceChanged
            rollback_error=None
            if prepared_set is not None:
                from export_publication import rollback as rollback_publication,JOURNAL
                try:
                    if (self.store.root/JOURNAL).exists():rollback_publication(self.store,preserve_files=output_created)
                except Exception as recovery_error:
                    logging.exception('Export recovery deferred; journal retained')
                    rollback_error=str(recovery_error)
            error=failure(exc,'游戏 APK',body.get('apk'),self.state.get('message') or '生成游戏 APK')
            if output_created and result:
                self._job_local.keep_work=True
                recovery=getattr(self._job_local,'recovery',None)
                if recovery is not None:recovery.save(self.store.root)
                self.update(phase='preserved',message='成品和工程已保留，保存入口暂未准备完成。请恢复存储访问后继续。',
                            available_files=[row['path']for row in result.get('artifacts',[])],can_resume=True,preserved=True)
            else:self._failure(error,body)
            if rollback_error:self.update(recovery_pending=True)
        finally:
            for resource,close in ((texture_scope,lambda:texture_scope.__exit__(None,None,None)),(compile_cache,lambda:compile_cache.close())):
                if resource is not None:
                    try:close()
                    except Exception:self.update(cleanup_warning='临时文件仍被使用，可以稍后清理。')
            self.cleanup_work(work,'native-')

    def remember_installation_mode(self,source,mode):
        from apk_sources import remember_mode
        try:remember_mode(self.store,source,mode)
        except OSError as record_error:
            import logging
            logging.exception('APK exported, but installation mode could not be remembered')
            self.notice('installation_record','apk','安装方式记录暂未保存。',error=record_error)

    def _booster_labels(self):
        path=self.resources/'catalog.json'
        return _export_data("资料文件",path,"读取并解析 JSON",lambda: json.loads(path.read_text(encoding='utf8')))['ABILITIES'] if path.is_file() else {}
