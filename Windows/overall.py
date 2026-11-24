"""Android native integer card and lineup OVR, shared by editor/export summaries."""
import copy


def training_cost(count):
    return sum(1+i//4 for i in range(count))


def auto_allocate_training(project,catalog):
    """Same maximum-level reference allocation as the editor's position tool."""
    groups=catalog['training_groups']
    training={g['id']:0 for g in groups}
    remaining=0 if project['card_type']=='Trending' else 2*(project['max_level']-1)
    target=project.get('preview',{}).get('training_position') or project['identity']['position']
    weights=catalog['auto_position_weights'].get(target) or catalog['auto_position_weights'][project['identity']['position']]
    while remaining>0:
        best=None
        for index,group in enumerate(groups):
            count=training[group['id']];cost=1+count//4
            if count>=40 or cost>remaining:continue
            available=sum(project['base_abilities'][str(i)]+count<105 for i in group['indices'])/len(group['indices'])
            score=(weights[index] if index<len(weights) else 0)*available/((count+1)*cost)
            if score>0 and (best is None or score>best[0]):best=(score,group['id'],cost)
        if best is None:break
        training[best[1]]+=1;remaining-=best[2]
    return training


def normalize_training(project,catalog,force_max=False):
    """Repair legacy level/budget overflow instead of rejecting its export."""
    cap=max(1,min(100,int(project.get('max_level',35))))
    raw_level=int(project.get('initial_level',1))
    project['max_level']=cap;project['initial_level']=max(1,min(raw_level,cap))
    source=project.get('training') or {}
    project['training']=dict(source)
    for group in catalog['training_groups']:
        project['training'][group['id']]=max(0,min(40,int(source.get(group['id'],0))))
    spent=sum(training_cost(project['training'][g['id']])for g in catalog['training_groups'])
    budget=0 if project['card_type']=='Trending' else 2*(project['initial_level']-1)
    if force_max or raw_level>cap or spent>budget:
        project['initial_level']=cap
        project['training']=auto_allocate_training(project,catalog)
    return project


def player_boosts(project,catalog):
    known={b['id']:b for b in catalog['boosters']+project.get('custom_boosters',[])}
    result={}
    for ident in dict.fromkeys(project.get('booster_ids',[])):
        booster=known.get(ident)
        if not booster or booster.get('conditional')and not project.get('conditional_boosters'):continue
        for key,amount in booster['effects'].items():result[int(key)]=result.get(int(key),0)+amount
    return result


def _aptitude(project,position):
    return (project.get('position_aptitudes')or{}).get(position,2 if position==project['identity']['position']else 0)


def calculate(project,catalog):
    """Card, development and lineup scores share the checked Android integer core."""
    from native_overall import rating,lineup_rating,parameters_for_project,REVISION
    base=dict(project['base_abilities']);trained=dict(base)
    for group in catalog['training_groups']:
        for index in group['indices']:
            key=str(index);trained[key]=min(105,trained[key]+project['training'].get(group['id'],0))
    boosts=player_boosts(project,catalog)
    selected=project.get('preview',{}).get('coach_id')
    coach=next((c for c in project.get('custom_coaches',[])+catalog.get('coaches',[])if c['id']==selected),None)
    tactics=(coach or{}).get('tactics')or{}
    style=project.get('preview',{}).get('team_style')or(max(tactics,key=tactics.get)if tactics else None)
    proficiency=tactics.get(style)if coach else None
    coach_boosts={int(k):v for k,v in ((coach or{}).get('effects')or{}).items()}
    combined=dict(boosts)
    for key,amount in coach_boosts.items():combined[key]=combined.get(key,0)+amount
    pos=project['identity']['position'];index=catalog['POSITIONS'].index(pos)
    base_params=parameters_for_project(project,catalog,base)
    trained_params=parameters_for_project(project,catalog,trained)
    positions={position:lineup_rating(trained_params,i,combined,proficiency,_aptitude(project,position),bool(coach))
               for i,position in enumerate(catalog['POSITIONS'])}
    card_current=rating(trained_params,index,boosts)
    return {'base':rating(base_params,index),'trained':rating(trained_params,index),
            'before_coach':card_current,'card_current':card_current,
            'current':positions[pos],'lineup':positions[pos],
            'positions':positions,
            'potential_positions':{position:rating(trained_params,i,boosts)for i,position in enumerate(catalog['POSITIONS'])},
            'method':REVISION,'algorithm_version':REVISION,
            'coach_id':selected if coach else None,'team_style':style,'position':pos,
            'position_aptitude':_aptitude(project,pos),'includes_condition_arrow':False}


def maximum_development(project,catalog):
    """Exact maximum card score for its real budget, without coach/slot modifiers.

    Native weights are integers. Each disjoint training group's score gain is
    separable before the final rounding, so an integer knapsack finds a true
    maximum without a regression fit or a saved source-rating correction.
    This computes a derived preview plan; the player's own training is untouched.
    """
    from native_overall import rating,parameters_for_project,PARAMETERS,WEIGHTS,POSITION_COLUMNS,REVISION
    p=copy.deepcopy(project);p.setdefault('preview',{})['coach_id']=None
    p['initial_level']=p['max_level'];budget=0 if p['card_type']=='Trending'else 2*(p['max_level']-1)
    position=catalog['POSITIONS'].index(p['identity']['position'])
    column=POSITION_COLUMNS[position]
    weights={index:row[column]for index,row in zip(PARAMETERS,WEIGHTS)}
    boosts=player_boosts(p,catalog)
    states={0:(0,{})}
    def component(index,value):return weights[index]*max(15,min(95,max(0,value-25)+boosts.get(index,0)))
    for group in catalog['training_groups']:
        choices=[]
        for count in range(41):
            spent=training_cost(count)
            if spent>budget:break
            gain=sum(component(i,min(105,p['base_abilities'][str(i)]+count))-component(i,p['base_abilities'][str(i)])for i in group['indices'])
            choices.append((spent,count,gain))
        next_states={}
        for used,(value,training)in states.items():
            for spent,count,gain in choices:
                total=used+spent
                if total>budget:continue
                if total not in next_states or value+gain>next_states[total][0]:
                    next_states[total]=(value+gain,{**training,group['id']:count})
        states=next_states
    used,(_,plan)=max(states.items(),key=lambda row:(row[1][0],-row[0]))
    trained=dict(p['base_abilities'])
    for group in catalog['training_groups']:
        for i in group['indices']:trained[str(i)]=min(105,trained[str(i)]+plan[group['id']])
    params=parameters_for_project(p,catalog,trained)
    return {'overall':rating(params,position,boosts),'level':p['max_level'],'training':plan,
            'spent':used,'budget':budget,'parameters':trained,'includes_player_boosters':True,
            'includes_coach':False,'conditional_boosters_enabled':bool(p.get('conditional_boosters')),
            'algorithm_version':REVISION}


def refresh_definition_ratings(document,catalog):
    """Upgrade only generated card-rating caches, including unselected cards.

    Source APKs, editor projects and owned training/experience are never written.
    The maximum plan is an independent catalogue preview, not the owned ledger.
    Unknown legacy definitions are left intact and explicitly reported.
    """
    from native_overall import ABILITIES,REVISION,rating
    report={'algorithm_version':REVISION,'updated':[],'already_current':[],'unsupported':[]}
    boosters={int(b['id']):dict(b)for b in catalog['boosters']}
    for key,booster in (document.get('preview_boosters')or{}).items():boosters[int(key)]={'id':int(key),**booster}
    cache_catalog={**catalog,'boosters':list(boosters.values())}
    for definition in document.get('players',[]):
        owned=definition.get('owned')or{};old=definition.get('maximum_development')or{}
        ident=str((definition.get('identity')or{}).get('card_id','unknown'))
        if old.get('algorithm_version')==REVISION and definition.get('rating_cache',{}).get('algorithm_version')==REVISION:
            report['already_current'].append(ident);continue
        base=definition.get('base_parameters')or owned.get('_careerplan_base_parameters')
        trained=owned.get('parameters');cap=(owned.get('level_info')or{}).get('level_limit',old.get('level'))
        if not isinstance(base,list)or len(base)!=147 or not isinstance(trained,list)or len(trained)!=147 or type(cap)is not int or not 1<=cap<=100 or type(base[0])is not int or not 0<=base[0]<13:
            report['unsupported'].append({'id':ident,'reason':'missing_verified_base_or_level'});continue
        project={'identity':{'position':catalog['POSITIONS'][base[0]],'height_cm':base[47]},
                 'traits':{'weak_foot_accuracy':['低','普通','高','极高'][max(0,min(3,base[33]))]},
                 'source_player':{'original_payload':{'parameters':base}},
                 'base_abilities':{str(i):base[i]for i in ABILITIES},'training':{},
                 'card_type':definition.get('card_type','Standard'),'max_level':cap,'initial_level':cap,
                 'booster_ids':list(owned.get('initial_booster_id_list')or trained[144:147]),
                 'conditional_boosters':bool(old.get('conditional_boosters_enabled')), 'preview':{}}
        maximum=maximum_development(project,cache_catalog)
        params=list(base)
        for key,value in maximum.pop('parameters').items():params[int(key)]=value
        maximum['parameters']=params
        definition['maximum_development']=maximum
        owned['_overall']=rating(base)
        # These are cache-only fields in the original preview adapter. They
        # must not keep a template player's or the old regression's score.
        for key in ('overall','default_overall','disp_overall','disp_max_overall'):owned.pop(key,None)
        definition['rating_cache']={'algorithm_version':REVISION,'base':rating(base),
                                    'card_current':rating(trained,boosts=player_boosts(project,cache_catalog)),
                                    'maximum':maximum['overall'],'includes_coach':False}
        report['updated'].append(ident)
    return report
