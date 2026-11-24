"""Build-time validation of the native tournament homepage adapter."""
import copy,json
from studio_resources import bundled_resources
from tournament_home import REVISION,SCHEMA,NAVIGATION_REVISION


def hooks(engine,resources=None,menu_sha256=None):
    folder=bundled_resources()if resources is None else resources
    data=json.loads((folder/'tournament_schedule_ui/native-home.json').read_text('utf8'))
    if data['revision']!=REVISION or data['tournament_home_channel']!=SCHEMA:raise ValueError('锦标赛主页协议不同')
    navigation=data.get('menu_navigation',{})
    if(navigation.get('revision')!=NAVIGATION_REVISION
       or navigation.get('return_callback')!='BP_MenuEvCompeEventMainMenu_C::StartFadeOutReturn'
       or navigation.get('return_intent_event')!=0
       or not navigation.get('return_requires_published_event')
       or not navigation.get('return_requires_idle_navigation')
       or not navigation.get('return_event_list_confirmation')):
        raise ValueError('锦标赛返回页面接口不同')
    entry=data.get('entry_installation',{})
    if(entry.get('revision')!='android-native-entry-v4'or entry.get('request_event_offset')!=0x410
       or entry.get('native_team_initializer')!=0x7b7ee24 or entry.get('initializer_argument')!='packed-team-value-w0'
       or entry.get('match_context_getter')!=0x82468d4 or entry.get('match_bank_offset')!=0x40
       or entry.get('entry_match_team_offset')!=0x318e790 or entry.get('selection_getter')!=0x7a42b84
       or entry.get('selection_team_offset')!=8 or not entry.get('identity_verified_before_ack')
       or entry.get('installed_team_action')!=3 or not entry.get('installed_team_requires_native_plan_and_nonce')
       or not entry.get('same_plan_retry')or not entry.get('requires_nonce')
       or entry.get('initial_settings_scope')!='native-converter'):
        raise ValueError('锦标赛首次阵容安装接口不同')
    for key in ('entry_popup_team_identity','match_initialization_team_identity'):
        if not data.get(key,{}).get('country_mismatch_requires_installed_entry'):
            raise ValueError('锦标赛原生选队身份接口不同')
    if data.get('special_plan_team_identity',{}).get('rental_field_encoding')!='native-team-id-shift-left-14':
        raise ValueError('锦标赛租借球队身份未按原生编码核验')
    membership=data.get('special_plan_roster_membership',{})
    if membership.get('policy_offset')!=SCHEMA['membership_offset']or not membership.get('requires_event_special_team_match'):
        raise ValueError('锦标赛原生球员候选归属未核验')
    if membership.get('unrestricted_owned_offset')!=SCHEMA['membership_unrestricted_offset']or not membership.get('unrestricted_owned_requires_explicit_option'):
        raise ValueError('锦标赛队套开关原生适配未核验')
    if not data['execution_proof']['independent_instruction_probe']:raise ValueError('锦标赛主页原生指令尚未核验')
    raw=bytes.fromhex(data['body_hex'])
    if len(raw)!=data.get('body_bytes'):raise ValueError('锦标赛主页模板长度不同')
    for probe in [data,*data.get('extra_entries',[]),*data['native_probes']]:
        engine.seek(probe['file_offset']);expected=bytes.fromhex(probe['expected_hex'])
        if engine.read(len(expected))!=expected:raise ValueError('锦标赛主页原生接口不同：'+probe.get('name','entry'))
    out=copy.deepcopy(data)
    for k in ('assembly','code','functions','constants','writable','deferred_shared_refs','native_probes'):out.pop(k,None)
    return [out]
