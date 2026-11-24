"""Joint football-score distribution with explicit outcome probabilities."""
import math
from functools import lru_cache
import tournament_simulation as legacy
from tournament_sim_strength import REVISION as STRENGTH_VERSION
HOME_S=8.0
FORM_S=4.0
TAIL_TOLERANCE=1e-13


def _strength(ratings):
    value=ratings.get('squad_strength') if isinstance(ratings,dict) else None
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=9999:
        raise ValueError('电脑赛果需要实际阵容的总实力评分')
    return value


def _form_s(form):
    state=legacy.form_state(form)
    return FORM_S if state['win_streak']>=3 else -FORM_S if state['nonwin_streak']>=3 else 0.0


def _poisson_grid(rate):
    values=[math.exp(-rate)];mass=values[0]
    for k in range(1,100):
        values.append(values[-1]*rate/k);mass+=values[-1]
        if k>=16 and 1.-mass<=TAIL_TOLERANCE:break
    tail=max(0.,1.-mass)
    if tail>1e-10:raise ValueError('比分分布尾部未达到校准精度')
    return values,tail


@lru_cache(maxsize=512)
def _joint(rates,target):
    if rates==(0.,0.):return (((0,0),),(1.,),(0.,0.),(0.,1.,0.),0.)
    hp,ht=_poisson_grid(rates[0]);ap,at=_poisson_grid(rates[1]);raw=[];masses=[0.,0.,0.]
    rho=max(legacy.DEFAULT_RHO,-.95/max(rates))
    for h,p in enumerate(hp):
        for a,q in enumerate(ap):
            sector=0 if h>a else 1 if h==a else 2
            mass=p*q*legacy.dixon_coles_tau(h,a,*rates,rho)
            raw.append((h,a,sector,mass));masses[sector]+=mass
    weighted=[(h,a,sector,mass*target[sector]/masses[sector]) for h,a,sector,mass in raw]
    norm=math.fsum(x[3]for x in weighted)
    scores=[];cdf=[];summed=0.;xg=[0.,0.];actual=[0.,0.,0.]
    for h,a,sector,weight in weighted:
        p=weight/norm;summed+=p;scores.append((h,a));cdf.append(summed)
        xg[0]+=h*p;xg[1]+=a*p;actual[sector]+=p
    cdf[-1]=1.
    return tuple(scores),tuple(cdf),tuple(xg),tuple(actual),ht+at
