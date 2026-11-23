# -*- coding: utf-8 -*-
"""
Build mock-ui/pilot_data.js from R3's monthly metrics and the Q2 v2 output tables.
(v2: reads q2/v2 output; keeps only the primary model per site x metric.)

    python3 v2/q2_trend_model_v2.py --input <site_metrics_monthly> --out v2/out_pilot
    python3 build_ui_data.py --input monthly_metrics_audit.json --q2 v2/out_pilot --ui ../mock-ui

--input accepts .json (list of rows), .csv or .parquet.
Output is a plain ES5 data file (window.PILOT_DATA = {...}); no build tools needed on the VM.

v2 changes vs v1
  - site_trend_model / site_trend_summary have a trailing `unit` column (index | rate).
    index: actual/counterfactual/band are 2019=100 index, gap in index points.
    rate : actual/counterfactual/band/gap are raw ratios (0..1). Summary gaps are in percentage points.
  - Both models (q2v2_trend / q2v2_seas) are stored; only rows with is_primary=True are exported.
  - Summary gains post_gap_12m, placebo_values, n_fit_months, ... (see README).
  - q2_group_summary.csv is exported as `group_summary` (A vs B DID per metric).
"""
import argparse
import json
import math
import os

import pandas as pd

METRICS = ['questions_count', 'new_users_count', 'answered_rate', 'downvote_ratio']
MODEL_COLS = ['month', 'segment', 'actual', 'counterfactual', 'band_low', 'band_high', 'gap']


def clean(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if hasattr(v, 'item'):  # numpy scalar -> python
        v = v.item()
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return int(v)
    return v


def parse_placebo(s):
    """'2020-07:6.431;2021-01:NA' -> {'2020-07': 6.431, '2021-01': None}"""
    out = {}
    if not isinstance(s, str):
        return out
    for part in s.split(';'):
        if ':' not in part:
            continue
        k, v = part.split(':', 1)
        try:
            out[k] = float(v)
        except ValueError:
            out[k] = None
    return out


def read_input(path):
    if path.endswith('.parquet'):
        return pd.read_parquet(path)
    if path.endswith('.csv'):
        return pd.read_csv(path)
    return pd.DataFrame(json.load(open(path)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', default='monthly_metrics_audit.json')
    ap.add_argument('--q2', default='v2/out_pilot')
    ap.add_argument('--ui', default='../mock-ui')
    ap.add_argument('--source', default='R3 local pilot (Phase 1), 10 sites')
    ap.add_argument('--coverage-last', default='2024-02')
    ap.add_argument('--excluded-tail', type=int, default=1)
    a = ap.parse_args()

    df = read_input(a.input)
    if 'is_meta' in df.columns:
        df = df[~df.is_meta.astype(bool)]
    df = df[['site', 'month', 'domain_group'] + METRICS].sort_values(['site', 'month'])

    sites = []
    for site, x in df.groupby('site'):
        sites.append({
            'site': site,
            'domain_group': x.domain_group.iloc[0],
            'rows': [[r.month] + [clean(getattr(r, m)) for m in METRICS] for r in x.itertuples()]
        })

    model = pd.read_csv(os.path.join(a.q2, 'site_trend_model.csv'))
    summary = pd.read_csv(os.path.join(a.q2, 'site_trend_summary.csv'))
    summary['is_primary'] = summary['is_primary'].astype(str).str.lower().isin(['true', '1'])
    prim = summary[summary.is_primary][['site', 'metric', 'model_id']]
    model = model.merge(prim, on=['site', 'metric', 'model_id'], how='inner')

    trend = {}
    for (site, metric), x in model.groupby(['site', 'metric']):
        x = x.sort_values('month')
        trend.setdefault(site, {})[metric] = [[clean(getattr(r, c)) for c in MODEL_COLS] for r in x.itertuples()]

    summ = {}
    for r in summary[summary.is_primary].to_dict('records'):
        o = {}
        for k, v in r.items():
            if k in ('site', 'metric', 'is_primary'):
                continue
            if k == 'placebo_values':
                o[k] = parse_placebo(v)
            elif isinstance(v, (bool, str)):
                o[k] = v
            else:
                o[k] = clean(v)
        summ.setdefault(r['site'], {})[r['metric']] = o

    gpath = os.path.join(a.q2, 'q2_group_summary.csv')
    group_summary = {}
    if os.path.exists(gpath):
        for r in pd.read_csv(gpath).to_dict('records'):
            group_summary[r['metric']] = {k: (v if isinstance(v, (bool, str)) else clean(v))
                                          for k, v in r.items() if k != 'metric'}

    last = a.coverage_last
    y, m = int(last[:4]), int(last[5:7])
    end_excl = '%d-%02d-01' % ((y + (m == 12)), (m % 12) + 1)
    data = {
        'source': a.source,
        'dump_date': '2024-04-02',
        'coverage_end_exclusive': end_excl,
        'coverage_last_month': last,
        'coverage_status': 'conservative cutoff, not the exact dump end',
        'answer_window_days': 7,
        'excluded_months_tail': a.excluded_tail,
        'q2_version': 'q2v2',
        'metrics': METRICS,
        'row_columns': ['month'] + METRICS,
        'trend_columns': MODEL_COLS,
        'sites': sites,
        'trend': trend,
        'trend_summary': summ,
        'group_summary': group_summary
    }
    path = os.path.join(a.ui, 'pilot_data.js')
    with open(path, 'w') as f:
        f.write('/* Generated by q2/build_ui_data.py (Q2 v2). Do not edit by hand. */\n')
        f.write('window.PILOT_DATA = ')
        json.dump(data, f, separators=(',', ':'), allow_nan=False)
        f.write(';\n')
    print('wrote %s (%d sites, %.0f KB)' % (path, len(sites), os.path.getsize(path) / 1024.0))


if __name__ == '__main__':
    main()
