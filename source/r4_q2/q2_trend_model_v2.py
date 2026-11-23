# -*- coding: utf-8 -*-
"""
Q2 main implementation v2 (R4) -- follows "Q2 사양 R3와 통일 / Q2 방법 v2 (확정, 11/17)".

Input : site_metrics_monthly (.parquet / .csv / .json) with at least
        site, month(YYYY-MM), domain_group, questions_count, new_users_count, answered_rate, downvote_ratio
        (optional is_meta column -> meta rows dropped). Only domain_group A/B are used.
Output (in --out):
        site_trend_model.{csv,parquet}    site x metric x month x model_id   (schema v1 + `unit`, appended last)
        site_trend_summary.{csv,parquet}  site x metric x model_id           (schema v1 + v2 columns, appended last)
        q2_group_summary.csv              metric-level DID, permutation p, placebo DID, break signal
        q2_backtest.csv                   seasonality choice (3-cutoff backtest)
        run_meta.json                     parameters + input fingerprint

Run outside the VM (Python 3 + numpy + pandas; pyarrow only needed for Parquet):
    python3 q2_trend_model_v2.py --input site_metrics_monthly.parquet --out out_v2
Sensitivity runs (write to separate --out dirs):
    --fit-start 2017-01          2017 fit start (needs 2017-2018 months in the input)
    --log1p                      counts on log(x+1), zero months kept in fitting and DID
    --exclude-site <host>        drop a site (repeatable)
    --sites sites.json           restrict to an eligible-site list ({"sites": {host: "A"|"B"}} or [hosts])
"""
import argparse
import hashlib
import itertools
import json
import os

import numpy as np
import pandas as pd

VERSION = 'q2v2'
METRICS = ['questions_count', 'new_users_count', 'answered_rate', 'downvote_ratio']
COUNT_METRICS = {'questions_count', 'new_users_count'}
FIT_START = '2019-01'
FIT_END = '2022-11'
POST_START = '2022-12'
POST_END = {'questions_count': '2024-02', 'new_users_count': '2024-02',
            'answered_rate': '2024-01', 'downvote_ratio': '2024-02'}
POST_12M_END = '2023-11'
PLACEBO_BREAKS = ['2020-07', '2021-01', '2021-07', '2021-11']
PLACEBO_WINDOW = 12
BACKTEST_CUTOFFS = ['2020-12', '2021-06', '2021-11']   # last training month; predict next 12
BREAK_SEARCH = ('2020-06', '2023-06')
MODELS = ('trend', 'seas')                              # linear trend / linear trend + 11 month dummies


# ---------------------------------------------------------------- month helpers
def mnum(m):
    return int(m[:4]) * 12 + int(m[5:7]) - 1


def madd(m, k):
    n = mnum(m) + k
    return '%04d-%02d' % (n // 12, n % 12 + 1)


# ---------------------------------------------------------------- loading
def load(path, sites_file, exclude):
    if path.endswith('.parquet'):
        df = pd.read_parquet(path)
    elif path.endswith('.csv'):
        df = pd.read_csv(path, dtype={'month': str})
    else:
        df = pd.DataFrame(json.load(open(path)))
    if 'is_meta' in df.columns:
        df = df[~df.is_meta.astype(bool)]
    df = df[['site', 'month', 'domain_group'] + METRICS].copy()
    df['month'] = df.month.astype(str).str[:7]
    if sites_file:
        s = json.load(open(sites_file))
        s = s.get('sites', s) if isinstance(s, dict) else s
        if isinstance(s, dict):          # host -> group overrides the input's domain_group
            df = df[df.site.isin(s)]
            df['domain_group'] = df.site.map(s)
        else:
            df = df[df.site.isin(s)]
    df = df[df.domain_group.isin(['A', 'B'])]
    if exclude:
        df = df[~df.site.isin(exclude)]
    for m in METRICS:
        df[m] = pd.to_numeric(df[m], errors='coerce')
    df['t'] = df.month.map(mnum).astype(float)
    df['moy'] = df.month.str[5:7].astype(int)
    return df.sort_values(['site', 'month']).reset_index(drop=True)


# ---------------------------------------------------------------- per-site trend model
def design(t, moy, seasonal, t0):
    cols = [np.ones(len(t)), (np.asarray(t, float) - t0) / 12.0]
    if seasonal:
        cols += [(np.asarray(moy) == k).astype(float) for k in range(2, 13)]
    return np.column_stack(cols)


class Fit(object):
    """Trend fitted on [start, end]. predict() returns values on the ORIGINAL scale
    (counts: Duan-smeared exp of the log trend; rates: the rate itself)."""

    def __init__(self, x, m, start, end, seasonal, log1p=False):
        f = x[(x.month >= start) & (x.month <= end) & x[m].notna()]
        self.count = m in COUNT_METRICS
        self.log1p = log1p
        self.n_zero_excluded = 0
        if self.count and not log1p:
            self.n_zero_excluded = int((f[m] <= 0).sum())
            f = f[f[m] > 0]
        self.n = len(f)
        self.seasonal = seasonal
        self.t0 = mnum(start)
        k = 13 if seasonal else 2
        self.ok = self.n >= k + 6            # at least 6 residual d.o.f.
        if not self.ok:
            return
        y = f[m].values.astype(float)
        if self.count:
            y = np.log1p(y) if log1p else np.log(y)
        X = design(f.t.values, f.moy.values, seasonal, self.t0)
        self.beta = np.linalg.lstsq(X, y, rcond=None)[0]
        resid = y - X.dot(self.beta)
        # Duan smearing: E[exp(e)] -- exp(log-trend) is a median, not a mean
        self.smear = float(np.mean(np.exp(resid))) if self.count else 1.0

    def predict(self, t, moy):
        z = design(np.atleast_1d(t), np.atleast_1d(moy), self.seasonal, self.t0).dot(self.beta)
        if not self.count:
            return z
        if self.log1p:
            return np.exp(z) * self.smear - 1.0
        return np.exp(z) * self.smear

    def slope_per_year(self):
        b = self.beta[1]
        return (np.exp(b) - 1) * 100 if self.count else b * 100   # %/yr  or  %p/yr


def gap(x, m, fit, start, end):
    """Counts: sum(actual)/sum(trend) - 1 in %, zero months INCLUDED (actual 0).
    Rates: mean(actual - trend) in %p."""
    p = x[(x.month >= start) & (x.month <= end) & x[m].notna()]
    if len(p) == 0 or not fit.ok:
        return np.nan
    cf = fit.predict(p.t.values, p.moy.values)
    a = p[m].values.astype(float)
    if m in COUNT_METRICS:
        return (a.sum() / cf.sum() - 1) * 100
    return float(np.mean(a - cf)) * 100


def mape(x, m, fit, start, end):
    p = x[(x.month >= start) & (x.month <= end) & x[m].notna()]
    if not fit.ok or len(p) == 0:
        return np.nan
    cf = fit.predict(p.t.values, p.moy.values)
    a = p[m].values.astype(float)
    if m in COUNT_METRICS:
        k = a > 0
        return float(np.mean(np.abs(a[k] - cf[k]) / a[k]) * 100) if k.any() else np.nan
    return float(np.mean(np.abs(a - cf)) * 100)    # MAE in %p for rates


def base_2019(x, m):
    """R3 rule: 2019 needs 12 valid months and a positive mean, else no index."""
    v = x.loc[x.month.str.startswith('2019'), m].dropna()
    return float(v.mean()) if len(v) == 12 and v.mean() > 0 else None


def best_break(t, moy, y, lo, hi):
    """Single level+slope break with month-of-year dummies. Returns (break t, SSE reduction 0..1)."""
    if len(y) < 20:
        return None, np.nan

    def X(k=None):
        cols = [np.ones(len(t)), t] + [(moy == mm).astype(float) for mm in range(2, 13)]
        if k is not None:
            cols += [(t >= k).astype(float), np.maximum(t - k, 0)]
        return np.column_stack(cols)

    def sse(A):
        return float(np.sum((y - A.dot(np.linalg.lstsq(A, y, rcond=None)[0])) ** 2))

    s0 = sse(X())
    cand = [k for k in np.unique(t) if lo <= k <= hi]
    if not cand or s0 <= 0:
        return None, np.nan
    best = min((sse(X(k)), k) for k in cand)
    return best[1], 1 - best[0] / s0


def tmonth(tv):
    n = int(round(tv))
    return '%04d-%02d' % (n // 12, n % 12 + 1)


# ---------------------------------------------------------------- backtest (seasonality choice)
def backtest(df, fit_start, log1p):
    rows = []
    for site, x in df.groupby('site'):
        for m in METRICS:
            for c in BACKTEST_CUTOFFS:
                s, e = madd(c, 1), madd(c, 12)
                for mod in MODELS:
                    f = Fit(x, m, fit_start, c, mod == 'seas', log1p)
                    rows.append(dict(site=site, metric=m, cutoff=c, model=mod,
                                     error=mape(x, m, f, s, e), bias=gap(x, m, f, s, e)))
    bt = pd.DataFrame(rows)
    agg = bt.groupby(['metric', 'model']).agg(median_error=('error', 'median'),
                                               median_bias=('bias', 'median'),
                                               n=('error', 'count')).reset_index()
    choice = {}
    for m in METRICS:
        a = agg[agg.metric == m].set_index('model')
        e_t, e_s = a.loc['trend', 'median_error'], a.loc['seas', 'median_error']
        choice[m] = 'seas' if (np.isfinite(e_s) and e_s < e_t) else 'trend'
        # second criterion: smaller |median bias|; if it disagrees, flag for reporting
        b_t, b_s = abs(a.loc['trend', 'median_bias']), abs(a.loc['seas', 'median_bias'])
        by_bias = 'seas' if b_s < b_t else 'trend'
        agg.loc[agg.metric == m, 'chosen'] = agg.loc[agg.metric == m, 'model'] == choice[m]
        agg.loc[agg.metric == m, 'bias_criterion_agrees'] = by_bias == choice[m]
    return bt, agg, choice


# ---------------------------------------------------------------- per-site outputs
def build_site(df, fit_start, choice, log1p, suffix):
    model_rows, summary_rows = [], []
    for site, x in df.groupby('site'):
        x = x.reset_index(drop=True)
        grp = x.domain_group.iloc[0]
        for m in METRICS:
            is_count = m in COUNT_METRICS
            unit = 'index' if is_count else 'rate'
            post_end = POST_END[m]
            base = base_2019(x, m) if is_count else 1.0
            for mod in MODELS:
                model_id = '%s_%s%s' % (VERSION, mod, suffix)
                fit = Fit(x, m, fit_start, FIT_END, mod == 'seas', log1p)
                if not fit.ok:
                    summary_rows.append(dict(site=site, metric=m, domain_group=grp, model_id=model_id,
                                             fit_start=fit_start, fit_end=FIT_END, post_start=POST_START,
                                             post_end=post_end, unit=unit, is_primary=(mod == choice[m]),
                                             note='insufficient fit months (%d)' % fit.n))
                    continue
                g_full = gap(x, m, fit, POST_START, post_end)
                g_12 = gap(x, m, fit, POST_START, POST_12M_END)
                placebo = []
                for b in PLACEBO_BREAKS:
                    pf = Fit(x, m, fit_start, madd(b, -1), mod == 'seas', log1p)
                    placebo.append(gap(x, m, pf, b, madd(b, PLACEBO_WINDOW - 1)))
                pl = np.array(placebo, float)
                pl_ok = np.isfinite(pl)
                pl_min = float(pl[pl_ok].min()) if pl_ok.any() else np.nan
                pl_max = float(pl[pl_ok].max()) if pl_ok.any() else np.nan
                beyond = bool(np.isfinite(g_12) and pl_ok.any() and (g_12 < pl_min or g_12 > pl_max))

                # change point on the whole observed series (log for counts, zero months dropped)
                s = x[x[m].notna() & (x.month <= post_end)]
                if is_count:
                    s = s[s[m] > 0]
                    yv = np.log(s[m].values.astype(float))
                else:
                    yv = s[m].values.astype(float)
                bk, bs = best_break(s.t.values, s.moy.values, yv,
                                    mnum(BREAK_SEARCH[0]), mnum(BREAK_SEARCH[1]))

                rows = x[(x.month >= fit_start) & (x.month <= post_end)]
                cf_all = fit.predict(rows.t.values, rows.moy.values)
                n_oor = int(np.sum(cf_all < 0)) if is_count else int(np.sum((cf_all < 0) | (cf_all > 1)))

                summary_rows.append(dict(
                    site=site, metric=m, domain_group=grp, model_id=model_id,
                    fit_start=fit_start, fit_end=FIT_END, post_start=POST_START, post_end=post_end,
                    pre_trend_pct_per_year=round(float(fit.slope_per_year()), 3),
                    post_gap_pct=round(float(g_full), 3),
                    placebo_min=round(pl_min, 3), placebo_max=round(pl_max, 3),
                    beyond_placebo=beyond,
                    break_month=tmonth(bk) if bk is not None else None,
                    break_strength=round(float(bs), 4) if np.isfinite(bs) else None,
                    # ---- v2 columns (appended after the v1 columns)
                    unit=unit,
                    post_gap_12m=round(float(g_12), 3),
                    placebo_values=';'.join('%s:%s' % (b, 'NA' if not np.isfinite(v) else round(v, 3))
                                            for b, v in zip(PLACEBO_BREAKS, pl)),
                    n_fit_months=fit.n,
                    n_zero_months_excluded=fit.n_zero_excluded,
                    n_out_of_range=n_oor,
                    smearing_factor=round(fit.smear, 5),
                    is_primary=(mod == choice[m]),
                    note=None))

                # month rows. counts -> 2019=100 index, rates -> raw rate (0..1)
                scale = (100.0 / base) if (is_count and base) else (1.0 if not is_count else None)
                if scale is None:
                    continue           # no valid 2019 base: summary only, no index rows
                post_mask = (rows.month >= POST_START).values
                lo_shift = pl_min if np.isfinite(pl_min) else np.nan
                hi_shift = pl_max if np.isfinite(pl_max) else np.nan
                for i, (_, r) in enumerate(rows.iterrows()):
                    cf = float(cf_all[i]) * scale
                    act = None if pd.isna(r[m]) else round(float(r[m]) * scale, 4)
                    if post_mask[i] and np.isfinite(lo_shift):
                        # band = where actual would sit if the method erred as at the placebo breaks
                        if is_count:
                            bl, bh = cf * (1 + lo_shift / 100.0), cf * (1 + hi_shift / 100.0)
                        else:
                            bl, bh = cf + lo_shift / 100.0, cf + hi_shift / 100.0
                        bl, bh = round(bl, 4), round(bh, 4)
                    else:
                        bl = bh = None
                    model_rows.append(dict(
                        site=site, metric=m, month=r.month, domain_group=grp, model_id=model_id,
                        segment='post' if post_mask[i] else 'fit',
                        actual=act, counterfactual=round(cf, 4), band_low=bl, band_high=bh,
                        gap=None if act is None else round(act - cf, 4), unit=unit))
    mcols = ['site', 'metric', 'month', 'domain_group', 'model_id', 'segment',
             'actual', 'counterfactual', 'band_low', 'band_high', 'gap', 'unit']
    scols = ['site', 'metric', 'domain_group', 'model_id', 'fit_start', 'fit_end', 'post_start', 'post_end',
             'pre_trend_pct_per_year', 'post_gap_pct', 'placebo_min', 'placebo_max', 'beyond_placebo',
             'break_month', 'break_strength',
             'unit', 'post_gap_12m', 'placebo_values', 'n_fit_months', 'n_zero_months_excluded',
             'n_out_of_range', 'smearing_factor', 'is_primary', 'note']
    return pd.DataFrame(model_rows, columns=mcols), pd.DataFrame(summary_rows).reindex(columns=scols)


# ---------------------------------------------------------------- DID (two-way FE) + permutation test
def twfe_demean(v, si, ti, ns, nt, tol=1e-10, iters=500):
    """Residual of v on site + month fixed effects (alternating projections, works for unbalanced panels)."""
    v = v.astype(float).copy()
    cs, ct = np.bincount(si, minlength=ns), np.bincount(ti, minlength=nt)
    cs[cs == 0], ct[ct == 0] = 1, 1
    for _ in range(iters):
        v -= (np.bincount(si, v, ns) / cs)[si]
        d = (np.bincount(ti, v, nt) / ct)[ti]
        v -= d
        if np.max(np.abs(d)) < tol:
            break
    return v


class Did(object):
    def __init__(self, df, m, start, end, brk, log1p=False, drop=()):
        x = df[(df.month >= start) & (df.month <= end) & df[m].notna() & ~df.site.isin(drop)]
        if m in COUNT_METRICS:
            if log1p:
                y = np.log1p(x[m].values.astype(float))
            else:
                x = x[x[m] > 0]
                y = np.log(x[m].values.astype(float))
        else:
            y = x[m].values.astype(float)
        self.m = m
        self.sites = np.array(sorted(x.site.unique()))
        smap = {s: i for i, s in enumerate(self.sites)}
        months = sorted(x.month.unique())
        tmap = {mm: i for i, mm in enumerate(months)}
        self.si = x.site.map(smap).values
        self.ti = x.month.map(tmap).values
        self.ns, self.nt = len(self.sites), len(months)
        self.post = (x.month >= brk).values.astype(float)
        grp = x.groupby('site').domain_group.first()
        self.isA = np.array([grp[s] == 'A' for s in self.sites])
        self.yt = twfe_demean(y, self.si, self.ti, self.ns, self.nt)

    def coef(self, isA=None):
        isA = self.isA if isA is None else isA
        d = twfe_demean(isA[self.si] * self.post, self.si, self.ti, self.ns, self.nt)
        dd = d.dot(d)
        return (d.dot(self.yt) / dd if dd > 0 else np.nan), d

    def estimate(self):
        b, d = self.coef()
        # cluster-robust SE by site (CR1), reference only
        e = self.yt - b * d
        g = np.bincount(self.si, d * e, self.ns)
        G = self.ns
        se = np.sqrt(G / (G - 1.0) * np.sum(g ** 2)) / d.dot(d) if G > 1 else np.nan
        return b, se

    def permutation(self, b_real, n_perm, rng):
        nA, G = int(self.isA.sum()), self.ns
        combos = None
        try:
            from math import comb
            combos = comb(G, nA)
        except ImportError:
            pass
        stats = []
        if combos is not None and combos <= n_perm:
            for idx in itertools.combinations(range(G), nA):
                lab = np.zeros(G, bool)
                lab[list(idx)] = True
                stats.append(self.coef(lab)[0])
            stats = np.array(stats)
            return float(np.mean(np.abs(stats) >= abs(b_real) - 1e-12)), 'exact', len(stats)
        for _ in range(n_perm):
            stats.append(self.coef(rng.permutation(self.isA))[0])
        stats = np.array(stats)
        return float((np.sum(np.abs(stats) >= abs(b_real) - 1e-12) + 1) / (n_perm + 1)), 'random', n_perm


def to_unit(m, b):
    return (np.exp(b) - 1) * 100 if m in COUNT_METRICS else b * 100   # % or %p


def group_analysis(df, fit_start, summary, log1p, n_perm, seed, loo):
    rng = np.random.RandomState(seed)
    rows = []
    prim = summary[summary.is_primary.astype(bool) & summary.note.isna()]
    for m in METRICS:
        r = dict(metric=m, unit='pct' if m in COUNT_METRICS else 'pctpt',
                 n_sites_A=int((df.groupby('site').domain_group.first() == 'A').sum()),
                 n_sites_B=int((df.groupby('site').domain_group.first() == 'B').sum()))
        # real DID: 12-month matched window and full window
        d12 = Did(df, m, fit_start, POST_12M_END, POST_START, log1p)
        b12, se12 = d12.estimate()
        p12, ptype, nperm = d12.permutation(b12, n_perm, rng)
        dfull = Did(df, m, fit_start, POST_END[m], POST_START, log1p)
        bf, sef = dfull.estimate()
        pf, _, _ = dfull.permutation(bf, n_perm, rng)
        r.update(did_12m=to_unit(m, b12), did_12m_perm_p=p12, did_12m_cluster_se_logpt=se12,
                 did_full=to_unit(m, bf), did_full_perm_p=pf, perm_type=ptype, n_perm=nperm)
        # placebo DID, same 12-month window length
        pls = []
        for b in PLACEBO_BREAKS:
            dp = Did(df, m, fit_start, madd(b, PLACEBO_WINDOW - 1), b, log1p)
            pls.append(to_unit(m, dp.estimate()[0]))
            r['did_placebo_%s' % b] = pls[-1]
        r['did_placebo_min'], r['did_placebo_max'] = min(pls), max(pls)
        did_beyond = r['did_12m'] < min(pls) or r['did_12m'] > max(pls)
        r['did_beyond_placebo'] = did_beyond
        # leave-one-site-out range
        if loo:
            vals = [to_unit(m, Did(df, m, fit_start, POST_12M_END, POST_START, log1p, (s,)).estimate()[0])
                    for s in d12.sites]
            r['did_12m_loo_min'], r['did_12m_loo_max'] = min(vals), max(vals)
        # per-site trend extension, group medians (primary model, 12m gap)
        p = prim[prim.metric == m]
        ga = p[p.domain_group == 'A'].post_gap_12m.median()
        gb = p[p.domain_group == 'B'].post_gap_12m.median()
        r.update(trend_gap12_median_A=ga, trend_gap12_median_B=gb, trend_gap12_A_minus_B=ga - gb,
                 share_sites_beyond_placebo_A=p[p.domain_group == 'A'].beyond_placebo.mean(),
                 share_sites_beyond_placebo_B=p[p.domain_group == 'B'].beyond_placebo.mean(),
                 primary_model=p.model_id.iloc[0] if len(p) else None)
        same_sign = np.sign(ga - gb) == np.sign(r['did_12m'])
        r['same_sign_trend_vs_did'] = bool(same_sign)
        # '단절 신호' = real DID beyond placebo range AND trend extension and DID agree in sign
        r['break_signal'] = bool(did_beyond and same_sign)
        # stricter variant: with only 4 placebo DIDs the range is narrow and null metrics pass often
        # (synthetic check: 4/6 runs flagged a no-effect metric). Also require permutation p < 0.05.
        r['break_signal_strict'] = bool(did_beyond and same_sign and p12 < 0.05)
        # change point on the A-B difference of group medians (log for counts)
        x = df[df[m].notna() & (df.month <= POST_END[m])]
        if m in COUNT_METRICS:
            x = x[x[m] > 0].assign(v=lambda z: np.log(z[m]))
        else:
            x = x.assign(v=lambda z: z[m])
        gm = x.groupby(['month', 'domain_group']).v.median().unstack().dropna()
        if 'A' in gm and 'B' in gm:
            tt = np.array([mnum(mm) for mm in gm.index], float)
            moy = np.array([int(mm[5:7]) for mm in gm.index])
            bk, bs = best_break(tt, moy, (gm.A - gm.B).values, mnum(BREAK_SEARCH[0]), mnum(BREAK_SEARCH[1]))
            r['A_minus_B_break_month'] = tmonth(bk) if bk is not None else None
            r['A_minus_B_break_strength'] = bs
        rows.append(r)
    out = pd.DataFrame(rows)
    return out.round(4)


# ---------------------------------------------------------------- output
def save(df, name, out):
    df.to_csv(os.path.join(out, name + '.csv'), index=False)
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        print('  pyarrow not installed: wrote %s.csv only' % name)
        return
    # Impala 2.2.0 on the VM (R2, 10/3): Parquet 1.0, data page v1, Snappy, no dictionary, no TIMESTAMP
    df.to_parquet(os.path.join(out, name + '.parquet'), engine='pyarrow', compression='snappy',
                  version='1.0', data_page_version='1.0', use_dictionary=False, index=False)
    print('  wrote %s.parquet' % name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', required=True)
    ap.add_argument('--out', default='out_v2')
    ap.add_argument('--fit-start', default=FIT_START)
    ap.add_argument('--log1p', action='store_true')
    ap.add_argument('--sites', default=None)
    ap.add_argument('--exclude-site', action='append', default=[])
    ap.add_argument('--n-perm', type=int, default=1999)
    ap.add_argument('--seed', type=int, default=527)
    ap.add_argument('--no-loo', action='store_true', help='skip leave-one-site-out DID range')
    a = ap.parse_args()

    df = load(a.input, a.sites, a.exclude_site)
    if df.month.min() > a.fit_start:
        raise SystemExit('input starts at %s, cannot fit from %s' % (df.month.min(), a.fit_start))
    df = df[df.month >= a.fit_start]
    suffix = ('_fs%s' % a.fit_start[:4] if a.fit_start != FIT_START else '') + ('_log1p' if a.log1p else '')
    os.makedirs(a.out, exist_ok=True)
    print('input: %d rows, %d sites (A %d / B %d), %s..%s' % (
        len(df), df.site.nunique(), df[df.domain_group == 'A'].site.nunique(),
        df[df.domain_group == 'B'].site.nunique(), df.month.min(), df.month.max()))

    bt, bt_agg, choice = backtest(df, a.fit_start, a.log1p)
    print('seasonality choice (3-cutoff backtest):', choice)
    model, summary = build_site(df, a.fit_start, choice, a.log1p, suffix)
    group = group_analysis(df, a.fit_start, summary, a.log1p, a.n_perm, a.seed, not a.no_loo)

    save(model, 'site_trend_model', a.out)
    save(summary, 'site_trend_summary', a.out)
    group.to_csv(os.path.join(a.out, 'q2_group_summary.csv'), index=False)
    bt_agg.to_csv(os.path.join(a.out, 'q2_backtest.csv'), index=False)
    bt.to_csv(os.path.join(a.out, 'q2_backtest_detail.csv'), index=False)
    meta = dict(version=VERSION, input=os.path.basename(a.input),
                input_sha256=hashlib.sha256(open(a.input, 'rb').read()).hexdigest(),
                fit_start=a.fit_start, fit_end=FIT_END, post_start=POST_START, post_end=POST_END,
                post_12m_end=POST_12M_END, placebo_breaks=PLACEBO_BREAKS, placebo_window=PLACEBO_WINDOW,
                backtest_cutoffs=BACKTEST_CUTOFFS, seasonality_choice=choice, log1p=a.log1p,
                excluded_sites=a.exclude_site, sites_file=a.sites, n_perm=a.n_perm, seed=a.seed,
                n_sites=int(df.site.nunique()))
    json.dump(meta, open(os.path.join(a.out, 'run_meta.json'), 'w'), indent=2, ensure_ascii=False)
    print('site_trend_model: %d rows, site_trend_summary: %d rows' % (len(model), len(summary)))
    pd.set_option('display.width', 220)
    pd.set_option('display.max_columns', 30)
    print(group[['metric', 'did_12m', 'did_12m_perm_p', 'did_placebo_min', 'did_placebo_max',
                 'trend_gap12_A_minus_B', 'same_sign_trend_vs_did', 'break_signal', 'break_signal_strict',
                 'A_minus_B_break_month']].to_string(index=False))


if __name__ == '__main__':
    main()
