"""Versioned CPU models preserving already started and settled matches."""
import tournament_simulation as previous
import tournament_model_v3 as current

MODEL_VERSION=current.MODEL_VERSION
SUPPORTED_MODELS=(*previous.SUPPORTED_MODELS,MODEL_VERSION)
form_state=previous.form_state
advance_form=previous.advance_form
form_modifier=previous.form_modifier


def get(version):
    if version==MODEL_VERSION:return current
    if version in previous.SUPPORTED_MODELS:return previous
    raise ValueError('赛事赛果算法版本不可用：'+str(version))


def expected_goals(home,away,*,model_version=MODEL_VERSION,**options):
    return get(model_version).expected_goals(home,away,model_version=model_version,**options)


def sample_period(home,away,*,model_version=MODEL_VERSION,**options):
    return get(model_version).sample_period(home,away,model_version=model_version,**options)


def penalty_shootout(home,away,*,model_version=MODEL_VERSION,**options):
    return get(model_version).penalty_shootout(home,away,model_version=model_version,**options)


def policy(state,fixture):
    from tournament_sim_strength import REVISION
    existing=fixture.get('simulation_v1')or{}
    if existing.get('frozen')or fixture.get('played'):
        return {key:existing[key]for key in('model_version','strength_version')}
    saved=state.get('simulation_policy_v1')
    round_rows=[row for row in state['fixtures']if(row['stage'],row['round'])==(fixture['stage'],fixture['round'])]
    started=any(row.get('played')or(row.get('simulation_v1')or{}).get('frozen')for row in round_rows)
    active=state.get('active')or{}
    started=started or active.get('fixture_id')in{row['id']for row in round_rows}and bool(active.get('started')or active.get('start_reply'))
    if saved is None or not started:
        updated={'model_version':MODEL_VERSION,'strength_version':REVISION}
        if saved!=updated:
            for row in round_rows:
                if not row.get('played')and not(row.get('simulation_v1')or{}).get('frozen'):row.pop('simulation_v1',None)
            state['simulation_policy_v1']=updated
        return updated
    get(saved['model_version'])
    return saved
