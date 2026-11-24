# -*- coding: utf-8 -*-
"""Small aggregated data only; exact site-level rank permutation and SVG plots."""
from __future__ import division, print_function, unicode_literals
import itertools
import os
from xml.sax.saxutils import escape
from common import METRICS, SERVING, month_keys, read_json, validate_monthly, write_json, write_text


def mean(values):
    return sum(values) / len(values)


def median(values):
    values = sorted(values)
    n = len(values)
    return values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2


def holm(values):
    valid = sorted((v, i) for i, v in enumerate(values) if v is not None)
    result, previous = [None] * len(values), 0.0
    # Keep planned but unavailable tests in the family size (conservative).
    for rank, (p, i) in enumerate(valid):
        previous = min(1.0, max(previous, p * (len(values) - rank)))
        result[i] = previous
    return result


def rank_test(a, b):
    if len(a) < 2 or len(b) < 2:
        return {'p_exact': None, 'reason': 'fewer_than_two_valid_sites_in_a_group'}
    values = a + b
    ranks = [1 + sum(x < v for x in values) + (sum(x == v for x in values) - 1) / 2 for v in values]
    observed = sum(ranks[:len(a)])
    chosen = min(len(a), len(b))
    doubled = [int(2 * r) for r in ranks]
    observed_small = int(2 * observed) if len(a) <= len(b) else sum(doubled) - int(2 * observed)
    counts = [dict() for _ in range(chosen + 1)]
    counts[0][0] = 1
    for index, rank in enumerate(doubled):
        for size in range(min(chosen, index + 1), 0, -1):
            for total, ways in counts[size - 1].items():
                score = total + rank
                counts[size][score] = counts[size].get(score, 0) + ways
    distribution = counts[chosen]
    permutations = sum(distribution.values())
    lower = sum(ways for score, ways in distribution.items() if score <= observed_small)
    upper = sum(ways for score, ways in distribution.items() if score >= observed_small)
    p = min(1.0, 2 * min(lower, upper) / permutations)
    delta = sum((x > y) - (x < y) for x in a for y in b) / (len(a) * len(b))
    loo = [median(a[:i] + a[i + 1:]) - median(b) for i in range(len(a))]
    loo += [median(a) - median(b[:i] + b[i + 1:]) for i in range(len(b))]
    return {'p_exact': p, 'permutations': permutations, 'mann_whitney_U_A': observed - len(a) * (len(a) + 1) / 2,
            'cliffs_delta': delta, 'median_A': median(a), 'median_B': median(b),
            'median_difference_A_minus_B': median(a) - median(b),
            'leave_one_out_min': min(loo), 'leave_one_out_max': max(loo), 'reason': None}


def comparisons(rows, sites):
    lookup = dict(((r['site'], r['month']), r) for r in rows)
    specifications = [('primary_2022_2023', 2022, 2023), ('preperiod_2019_2021', 2019, 2021), ('early_2024_vs_2022', 2022, 2024)]
    changes, tests = [], []
    for name, before, after in specifications:
        for metric in METRICS:
            months = list(range(1, 11)) if after != 2024 else ([1] if metric == 'answered_rate' else [1, 2])
            records = []
            for site, group in sorted(sites.items()):
                a = [lookup[site, '%04d-%02d' % (before, m)].get(metric) for m in months]
                b = [lookup[site, '%04d-%02d' % (after, m)].get(metric) for m in months]
                value, reason = None, None
                base, target = None, None
                if any(v is None for v in a + b):
                    reason = 'missing_matched_month'
                else:
                    base, target = mean(a), mean(b)
                    if metric.endswith('_count'):
                        if base == 0:
                            reason = 'zero_baseline'
                        else:
                            value = 100 * (target / base - 1)
                    else:
                        value = 100 * (target - base)
                records.append({'analysis': name, 'site': site, 'domain_group': group, 'metric': metric,
                                'base_mean': base, 'target_mean': target, 'change': value, 'reason': reason,
                                'unit': 'percent' if metric.endswith('_count') else 'percentage_points'})
            changes.extend(records)
            groups = dict((g, [r['change'] for r in records if r['domain_group'] == g and r['change'] is not None]) for g in 'AB')
            result = rank_test(groups['A'], groups['B'])
            result.update(analysis=name, metric=metric, base_year=before, target_year=after, matched_months=months,
                          n_A=len(groups['A']), n_B=len(groups['B']),
                          excluded_sites=[r['site'] for r in records if r['change'] is None])
            tests.append(result)
    for i in range(0, len(tests), 4):
        for t, p in zip(tests[i:i + 4], holm([t['p_exact'] for t in tests[i:i + 4]])):
            t['p_holm_within4'] = p
    for t, p in zip(tests, holm([t['p_exact'] for t in tests])):
        t['p_holm_all12'] = p
    return changes, tests


def csv_file(path, rows, fields):
    def cell(value):
        text = '' if value is None else str(value)
        return '"' + text.replace('"', '""') + '"'
    lines = [','.join(cell(f) for f in fields)]
    lines += [','.join(cell(r.get(f)) for f in fields) for r in rows]
    write_text(path, '\n'.join(lines) + '\n')


def indexed(rows, sites):
    result, baselines = [], []
    for site in sorted(sites):
        selected = [r for r in rows if r['site'] == site]
        for metric in METRICS:
            values = [r.get(metric) for r in selected if r['month'].startswith('2019-')]
            baseline, reason = None, None
            if len(values) != 12 or any(v is None for v in values):
                reason = 'incomplete_2019'
            elif mean(values) <= 0:
                reason = 'zero_2019_mean'
            else:
                baseline = mean(values)
            baselines.append({'site': site, 'metric': metric, 'baseline': baseline, 'reason': reason})
            for r in selected:
                v = r.get(metric)
                result.append({'site': site, 'domain_group': sites[site], 'month': r['month'], 'metric': metric,
                               'raw': v, 'index_2019': 100 * v / baseline if v is not None and baseline else None})
    return result, baselines


def plot_svg(path, title, panels):
    """Deterministic vector charts, no display server or third-party libraries."""
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="1120" height="730" viewBox="0 0 1120 730">',
             '<rect width="1120" height="730" fill="white"/>',
             '<g font-family="sans-serif" font-size="12" fill="#243349">',
             '<text x="28" y="28" font-size="20">%s</text>' % escape(title)]
    for p, (label, series) in enumerate(panels):
        left, top, width, height = 70 + (p % 2) * 550, 82 + (p // 2) * 330, 440, 230
        values = [v for name, color, ys in series for v in ys if v is not None]
        low = min([0] + values)
        high = max([1] + values)
        span = high - low
        high += span * .08
        low -= span * .03
        def xy(i, v):
            return left + i * width / 62, top + height - (v - low) * height / (high - low)
        parts.append('<text x="%d" y="%d" font-size="14">%s</text>' % (left, top - 24, escape(label)))
        for tick in range(5):
            value = low + (high - low) * tick / 4
            y = xy(0, value)[1]
            parts.append('<path d="M %d %.2f H %d" stroke="#e1e5eb"/><text x="%d" y="%.2f" text-anchor="end">%.2g</text>' % (left, y, left + width, left - 8, y + 4, value))
        for i in range(0, 63, 12):
            x = xy(i, low)[0]
            parts.append('<text x="%.2f" y="%d" text-anchor="middle">%s</text>' % (x, top + height + 20, month_keys()[i][:4]))
        transition = xy(47, low)[0]
        parts.append('<path d="M %.2f %d V %d" stroke="#8794a4" stroke-dasharray="4 4"/>' % (transition, top, top + height))
        for j, (name, color, ys) in enumerate(series):
            commands, continuous = [], False
            for i, v in enumerate(ys):
                if v is None:
                    continuous = False
                    continue
                x, y = xy(i, v)
                commands.append(('%s %.2f %.2f' % ('L' if continuous else 'M', x, y)))
                parts.append('<circle cx="%.2f" cy="%.2f" r="1.4" fill="%s"/>' % (x, y, color))
                continuous = True
            parts.append('<path d="%s" stroke="%s" stroke-width="2" fill="none"/>' % (' '.join(commands), color))
            parts.append('<text x="%d" y="%d" fill="%s">%s</text>' % (left + j * 190, top + height + 44, color, escape(name)))
    parts += ['<text x="28" y="716">Dashed line: Nov 2022. Gaps: unavailable values. Descriptive trends; no causal estimate.</text>', '</g></svg>']
    write_text(path, '\n'.join(parts))


def distribution_svg(path, changes):
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="1120" height="810" viewBox="0 0 1120 810">',
             '<rect width="1120" height="810" fill="white"/>', '<g font-family="sans-serif" font-size="12" fill="#243349">',
             '<text x="24" y="28" font-size="20">Site changes: 2022 to 2023, matched Jan-Oct</text>']
    panel_height = max(370, 27 * len(set(r['site'] for r in changes)) + 90)
    chart_height = panel_height * 2 + 70
    parts[0] = '<svg xmlns="http://www.w3.org/2000/svg" width="1120" height="%d" viewBox="0 0 1120 %d">' % (chart_height, chart_height)
    parts[1] = '<rect width="1120" height="%d" fill="white"/>' % chart_height
    for index, metric in enumerate(METRICS):
        rows = sorted([r for r in changes if r['analysis'] == 'primary_2022_2023' and r['metric'] == metric],
                      key=lambda r: (r['domain_group'], r['site']))
        values = [r['change'] for r in rows if r['change'] is not None]
        low, high = min([0] + values), max([1] + values)
        margin = (high - low) * .1
        low, high = low - margin, high + margin
        left, top, width = 155 + index % 2 * 550, 82 + index // 2 * panel_height, 330
        def x(v):
            return left + (v - low) * width / (high - low)
        parts.append('<text x="%d" y="%d" font-size="14">%s (%s)</text>' % (left - 120, top - 20, metric, '%' if metric.endswith('_count') else 'pp'))
        parts.append('<path d="M %.2f %d V %d" stroke="#a7b1be" stroke-dasharray="4 4"/>' % (x(0), top - 5, top + panel_height - 95))
        for j, r in enumerate(rows):
            y = top + j * 27
            color = '#1769aa' if r['domain_group'] == 'A' else '#cb6425'
            parts.append('<text x="%d" y="%d" text-anchor="end">%s %s</text>' % (left - 10, y + 4, r['domain_group'], escape(r['site'].split('.')[0])))
            if r['change'] is None:
                parts.append('<text x="%d" y="%d">NA</text>' % (left, y + 4))
            else:
                parts.append('<circle cx="%.2f" cy="%d" r="4" fill="%s"><title>%s: %.5g</title></circle>' % (x(r['change']), y, color, escape(r['site']), r['change']))
        for j in range(5):
            value = low + (high - low) * j / 4
            parts.append('<text x="%.2f" y="%d" text-anchor="middle">%.3g</text>' % (x(value), top + panel_height - 65, value))
    parts += [('<text x="24" y="%d">One point per site. Blue: A; orange: B. Negative = decline. Missing values are not replaced with zero.</text>' % (chart_height - 15)), '</g></svg>']
    write_text(path, '\n'.join(parts))


def generate(rows, sites, dest, full_scope):
    rows = validate_monthly(rows, sites)
    write_json(os.path.join(dest, 'monthly_metrics_audit.json'), rows)
    csv_file(os.path.join(dest, 'site_metrics_monthly.csv'), rows, SERVING)
    idx, baselines = indexed(rows, sites)
    csv_file(os.path.join(dest, 'monthly_indices.csv'), idx, ['site', 'domain_group', 'month', 'metric', 'raw', 'index_2019'])
    csv_file(os.path.join(dest, 'baseline_quality.csv'), baselines, ['site', 'metric', 'baseline', 'reason'])
    lookup = dict(((r['site'], r['month'], r['metric']), r) for r in idx)
    def series(site, metric):
        records = [lookup[site, m, metric] for m in month_keys()]
        return [r['index_2019'] if metric.endswith('_count') else (100 * r['raw'] if r['raw'] is not None else None) for r in records]
    def label(metric):
        return metric + (' (2019 mean = 100)' if metric.endswith('_count') else ' (%)')
    charts = os.path.join(dest, 'charts')
    os.mkdir(charts)
    for site in sorted(sites):
        plot_svg(os.path.join(charts, site + '.svg'), site,
                 [(label(m), [(sites[site], '#1769aa', series(site, m))]) for m in METRICS])
    group_rows, panels = [], []
    for metric in METRICS:
        lines = []
        for group, color in [('A', '#1769aa'), ('B', '#cb6425')]:
            members = [s for s in sites if sites[s] == group]
            ys = []
            for i, month in enumerate(month_keys()):
                values = [series(s, metric)[i] for s in members]
                valid = [v for v in values if v is not None]
                value = median(valid) if len(valid) == len(members) and valid else None
                ys.append(value)
                group_rows.append({'group': group, 'month': month, 'metric': metric, 'median': value,
                                   'valid_sites': len(valid), 'target_sites': len(members)})
            lines.append((group + ': site median', color, ys))
        panels.append((label(metric), lines))
    plot_svg(os.path.join(charts, 'ab_monthly.svg'), 'R3 A/B monthly trends', panels)
    csv_file(os.path.join(dest, 'group_monthly.csv'), group_rows, ['group', 'month', 'metric', 'median', 'valid_sites', 'target_sites'])
    notes = ['# R3 월별 지표와 Q1 분석', '',
             '대상: 본 사이트 %d개. meta 제외. %s' % (len(sites), ('선택한 A/B %d사이트 범위 전체.' % len(sites)) if full_scope else '**부분 실행: 전체 A/B 검정은 수행하지 않았습니다.**'), '',
             '2019-01~2024-03의 월별 행을 보존합니다. 건수·투표는 2024-02까지, 7일 답변률은 2024-01까지 유효합니다. '
             '2024-03-01 UTC는 보수적 운영 가정이며, R1 dump_date가 역사적 자료의 완전성을 인증하지 않습니다.', '',
             'Community(id=-1) 제외. 비율 분모 0은 NULL. 답변창은 0~604800초 양끝 포함이며 질문–답변은 site와 id로 연결합니다. '
             '원본 month와 경로 year를 집계에 사용합니다. 최초 게시물이 있는 달은 불완전한 달로 제외합니다.', '',
             '네 지표의 2019 평균=100 지수는 monthly_indices.csv에 별도로 제공합니다. 12개월 미충족·평균0이면 NULL입니다. '
             '그림은 건수를 지수, 비율을 %로 표시합니다. 군별 선은 구성 사이트가 모두 유효한 달의 동일 가중 중앙값입니다.', '',
             '![A/B 월별 추세](charts/ab_monthly.svg)', '']
    if full_scope:
        changes, tests = comparisons(rows, sites)
        distribution_svg(os.path.join(charts, 'site_changes.svg'), changes)
        write_json(os.path.join(dest, 'statistical_tests.json'), tests)
        csv_file(os.path.join(dest, 'site_changes.csv'), changes, ['analysis', 'site', 'domain_group', 'metric', 'base_mean', 'target_mean', 'change', 'unit', 'reason'])
        csv_file(os.path.join(dest, 'statistical_tests.csv'), tests, ['analysis', 'metric', 'n_A', 'n_B', 'median_A', 'median_B', 'median_difference_A_minus_B', 'cliffs_delta', 'p_exact', 'p_holm_within4', 'p_holm_all12', 'permutations', 'leave_one_out_min', 'leave_one_out_max', 'reason'])
        notes += ['## 주 분석: 2022→2023 같은 1~10월 비교', '',
                  '건수는 월평균 상대 변화(%), 비율은 월별 비율의 동일 가중 평균 차이(%p)입니다. '
                  '월이 하나라도 결측이면 해당 사이트·지표 비교에서 제외하고 이유를 기록합니다.', '',
                  '|지표|유효 A/B|A 중앙값|B 중앙값|Cliff delta|정확 p|Holm 4|Holm 12|',
                  '|---|---:|---:|---:|---:|---:|---:|---:|']
        def fmt(v):
            return 'NA' if v is None else '%.5g' % v
        for t in tests[:4]:
            notes.append('|%s|%d/%d|%s|%s|%s|%s|%s|%s|' % (t['metric'], t['n_A'], t['n_B'],
                fmt(t.get('median_A')), fmt(t.get('median_B')), fmt(t.get('cliffs_delta')), fmt(t['p_exact']), fmt(t['p_holm_within4']), fmt(t['p_holm_all12'])))
        notes += ['', '![사이트별 변화량 분포](charts/site_changes.svg)', '', '독립 관측 단위는 사이트입니다. 동점을 평균 순위로 처리하는 양측 순위합 순열검정이며, '
                  '작은 군의 두 배 평균순위 합 분포를 동적계획법으로 정확 계산합니다. 동점과 군 크기를 보존하며 몬테카를로 근사를 사용하지 않습니다. p=2×min(양쪽 꼬리 확률), 최대1입니다. '
                  'Cliff delta는 음수일수록 A 변화량이 더 작습니다. 순위검정을 중앙값 차이 검정으로 단정하지 않습니다.', '',
                  '주 분석 4개와 보조 분석(2019→2021 같은 1~10월, 2022→2024 1~2월·답변률만 1월) 8개를 제공합니다. '
                  'Holm은 각 4개와 전체 12개 묶음에 각각 적용합니다. 결측 검정도 계획된 묶음 크기에 포함합니다. '
                  'leave-one-site-out 범위는 한 사이트 제외 민감도이며 신뢰구간이 아닙니다.', '',
                  '**탐색적 분석입니다.** 기존 분석을 본 뒤 패키지화했으며 무작위 표집·무작위 군 배정이 아닙니다. '
                  '사이트 간 공통 충격과 이용자 공유로 독립성·라벨 교환가능성이 보장되지 않습니다. '
                  '유의하지 않음은 차이 없음의 증거가 아니며, 차이가 보여도 LLM 인과효과를 입증하지 않습니다. '
                  'Q2 DID·추세 반사실 모형, 전수 네트워크의 기존 24개 검정은 이 패키지에 포함하지 않습니다.']
    notes += ['', '## 품질 확인', '', 'baseline_quality.csv의 NULL 이유, sites/*/quality.json의 고아 답변·음수 지연을 확인하세요. '
              '해당 답변은 유효한 첫 답변 계산에 포함되지 않습니다. 삭제된 과거 기록은 복원하지 않습니다. '
              '실제 VM 성공 여부는 이 보고서가 아니라 validated.json과 HDFS _R3_SUCCESS.json으로 확인합니다.', '']
    write_text(os.path.join(dest, 'REPORT_KO.md'), '\n'.join(notes))
