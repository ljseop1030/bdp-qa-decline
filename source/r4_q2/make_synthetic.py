# -*- coding: utf-8 -*-
"""Synthetic site_metrics_monthly with a KNOWN LLM effect, to check q2_trend_model_v2.py.
A sites: counts x0.85 (-15%) and answered_rate -3%p from 2022-12. B sites: no effect.
Includes seasonality, noise, and one small site with zero-count months."""
import sys
import numpy as np
import pandas as pd

rng = np.random.RandomState(int(sys.argv[2]) if len(sys.argv) > 2 else 1)
months = ['%04d-%02d' % (y, m) for y in range(2017, 2025) for m in range(1, 13)]
months = [m for m in months if m <= '2024-02']
rows = []
for i in range(30):
    g = 'A' if i < 20 else 'B'
    lvl = np.exp(rng.uniform(3.5, 7)) if i != 29 else 6.0       # site 29: tiny, has zero months
    slope = rng.uniform(-0.25, 0.05)                              # log change per year
    amp = rng.uniform(0, 0.15)
    ar0 = rng.uniform(0.5, 0.85)
    for k, m in enumerate(months):
        t = k / 12.0
        post = m >= '2022-12'
        seas = amp * np.cos(2 * np.pi * (int(m[5:]) - 1) / 12)
        mu = lvl * np.exp(slope * t + seas) * (0.85 if (post and g == 'A') else 1.0)
        q = rng.poisson(mu * np.exp(rng.normal(0, 0.08)))
        u = rng.poisson(1.3 * mu * np.exp(rng.normal(0, 0.08)))
        ar = ar0 - 0.01 * t - (0.03 if (post and g == 'A') else 0) + rng.normal(0, 0.02)
        dv = 0.1 + rng.normal(0, 0.01)
        rows.append(dict(site='s%02d.%s' % (i, g), month=m, domain_group=g,
                         questions_count=q, new_users_count=u,
                         answered_rate=None if m > '2024-01' else ar, downvote_ratio=dv))
pd.DataFrame(rows).to_csv(sys.argv[1], index=False)
