# -*- coding: utf-8 -*-
"""Q2 power check: 12 vs 12 sites, questions_count, first-12-month DID + site permutation test (499 draws, p<0.05).
Scenarios: optimistic (make_synthetic heterogeneity) / realistic (pilot-calibrated: site slope sd 0.21 log/yr, site post shock sd 0.15).
Usage (next to q2_trend_model_v2.py): python3 power_sim.py 150  -> power_12v12.csv"""
import os
import sys, numpy as np, pandas as pd, time
R=int(sys.argv[1]) if len(sys.argv)>1 else 150
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__))); import q2_trend_model_v2 as Q
months=[m for m in ['%04d-%02d'%(y,k) for y in range(2019,2025) for k in range(1,13)] if m<='2023-11']
def sim(rng, eff, scen, nA=12, nB=12):
    rows=[]
    for i in range(nA+nB):
        g='A' if i<nA else 'B'
        lvl=np.exp(rng.uniform(3.5,7))
        if scen=='optimistic': slope=rng.uniform(-0.25,0.05); shock=0.0
        else: slope=rng.normal(-0.25,0.21); shock=rng.normal(0,0.15)
        amp=rng.uniform(0,0.15)
        for k,m in enumerate(months):
            post=m>='2022-12'; t=k/12.
            mu=lvl*np.exp(slope*t+amp*np.cos(2*np.pi*(int(m[5:])-1)/12)+(shock if post else 0))*((1+eff) if (post and g=='A') else 1)
            rows.append((('s%02d'%i),m,g,rng.poisson(mu*np.exp(rng.normal(0,0.08)))))
    return pd.DataFrame(rows,columns=['site','month','domain_group','questions_count'])
out=[]
for scen in ['optimistic','realistic']:
    for eff in [0,-0.10,-0.15,-0.25,-0.35]:
        rng=np.random.RandomState(527); rej=0; ests=[]
        for r in range(R):
            df=sim(rng,eff,scen)
            d=Q.Did(df,'questions_count','2019-01','2023-11','2022-12')
            b,_=d.estimate(); p,_,_=d.permutation(b,499,rng)
            rej+=p<0.05; ests.append((np.exp(b)-1)*100)
        out.append(dict(scenario=scen,true_effect_pct=eff*100,power=rej/R,did_median=np.median(ests),did_p10=np.percentile(ests,10),did_p90=np.percentile(ests,90)))
        print(out[-1],flush=True)
pd.DataFrame(out).round(3).to_csv('power_12v12.csv',index=False)
