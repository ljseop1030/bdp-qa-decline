# -*- coding: utf-8 -*-
"""Shared Python 2.7 / 3 standard-library contracts."""
from __future__ import print_function
import calendar
import datetime
import hashlib
import json
import math
import os
import re
import subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))
TABLES = ('posts', 'users', 'votes')
METRICS = ('questions_count', 'new_users_count', 'answered_rate', 'downvote_ratio')
SERVING = ('site', 'month', 'domain_group') + METRICS
AUDIT = SERVING + ('is_meta', 'parent_site', 'complete_month', 'answer_mature',
                   'answered_questions_count', 'upvotes_count', 'downvotes_count')


def read_json(path):
    with open(path, 'rb') as f:
        return json.loads(f.read().decode('utf-8'))


def write_text(path, text):
    with open(path, 'wb') as f:
        f.write(text.encode('utf-8'))


def write_json(path, value):
    write_text(path, json.dumps(value, ensure_ascii=True, sort_keys=True,
                               indent=2, allow_nan=False) + '\n')


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def capture(args):
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = p.communicate()
    if p.returncode:
        raise RuntimeError('%s failed (%s): %s' % (args, p.returncode, err.decode('utf-8', 'replace')))
    return out.decode('utf-8')


def epoch(text):
    return calendar.timegm(datetime.datetime.strptime(text, '%Y-%m-%dT%H:%M:%SZ').timetuple())


def next_month(year, month):
    return (year + 1, 1) if month == 12 else (year, month + 1)


def month_keys():
    return ['%04d-%02d' % (2019 + i // 12, i % 12 + 1) for i in range(63)]


def calendar_rows(site, group, first_epoch, cutoff):
    first_full = cutoff + 1
    if first_epoch is not None:
        dt = datetime.datetime.utcfromtimestamp(first_epoch)
        y, m = next_month(dt.year, dt.month)
        first_full = calendar.timegm(datetime.datetime(y, m, 1).timetuple())
    rows = []
    for key in month_keys():
        y, m = map(int, key.split('-'))
        ny, nm = next_month(y, m)
        start = calendar.timegm(datetime.datetime(y, m, 1).timetuple())
        end = calendar.timegm(datetime.datetime(ny, nm, 1).timetuple())
        rows.append((site, group, False, site, y, m, key,
                     start >= first_full and end <= cutoff,
                     start >= first_full and end + 604800 <= cutoff))
    return rows


def check_status(status, site, table):
    if (status.get('site'), status.get('table'), status.get('parser_version'), status.get('dump_date')) != (
            site, table, 'v0.3', '2024-04-02'):
        raise ValueError('R1 v0.3 / 2024-04-02 status contract mismatch: %s/%s' % (site, table))
    n = status.get('rows')
    if isinstance(n, bool) or not isinstance(n, type(1)) and not isinstance(n, type(2 ** 64)):
        raise ValueError('Invalid status row count')
    if n < 0:
        raise ValueError('Negative status row count')
    years = status.get('years')
    if not isinstance(years, list) or any(type(y) is not int or not 1900 <= y <= 2024 for y in years):
        raise ValueError('Invalid status years')
    if sorted(set(years)) != years or bool(years) != bool(n):
        raise ValueError('Inconsistent status years/rows')
    if not status.get('finished_at'):
        raise ValueError('Missing status completion timestamp')
    return status


def safe_hdfs(path):
    if not re.match(r'^/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_.=-]+)+$', path) or '..' in path.split('/'):
        raise ValueError('Use an absolute HDFS namespace path, e.g. /project/r3/run-001')
    return path


def check_paths(source, target):
    safe_hdfs(source)
    safe_hdfs(target)
    if source == target or source.startswith(target + '/') or target.startswith(source + '/'):
        raise ValueError('R1 input and R3 output must be separate directory trees')


def validate_monthly(rows, sites):
    expected = set((s, m) for s in sites for m in month_keys())
    keys = [(r['site'], r['month']) for r in rows]
    if len(keys) != len(set(keys)) or set(keys) != expected:
        raise ValueError('Missing, duplicate or unexpected site/month rows')
    for r in rows:
        if r['domain_group'] != sites[r['site']] or r.get('is_meta') is not False:
            raise ValueError('Group/meta mismatch')
        if type(r.get('complete_month')) is not bool or type(r.get('answer_mature')) is not bool:
            raise ValueError('Missing calendar flags')
        if r['answer_mature'] and not r['complete_month']:
            raise ValueError('Mature month must be complete')
        for name in ('questions_count', 'new_users_count', 'upvotes_count', 'downvotes_count', 'answered_questions_count'):
            value = r.get(name)
            valid = r['answer_mature'] if name == 'answered_questions_count' else r['complete_month']
            if (value is not None) != valid:
                raise ValueError('Count NULL policy: ' + name)
            if value is not None and (isinstance(value, bool) or int(value) != value or value < 0):
                raise ValueError('Invalid count: ' + name)
        q, a = r.get('questions_count'), r.get('answered_questions_count')
        if a is not None and a > q:
            raise ValueError('Answered questions exceed questions')
        up, down = r.get('upvotes_count'), r.get('downvotes_count')
        expected_ratios = {
            'answered_rate': float(a) / q if a is not None and q else None,
            'downvote_ratio': float(down) / (up + down) if up is not None and up + down else None}
        for name, expected_value in expected_ratios.items():
            got = r.get(name)
            if expected_value is None:
                if got is not None:
                    raise ValueError('Ratio should be NULL: ' + name)
            elif got is None or math.isnan(got) or math.isinf(got) or abs(got - expected_value) > 1e-12:
                raise ValueError('Ratio numerator/denominator mismatch: ' + name)
    return [dict((k, r.get(k)) for k in AUDIT) for r in sorted(rows, key=lambda r: (r['site'], r['month']))]


def load_scope(config=None):
    config = config or read_json(os.path.join(ROOT, 'config.json'))
    path = os.path.join(ROOT, 'selected_sites.json')
    if digest(path) != config['selection_sha256']:
        raise ValueError('Selection hash mismatch')
    selection = read_json(path)
    sites = selection['sites']
    if selection['protocol_version'] != config['scope_id']:
        raise ValueError('Scope version mismatch')
    counts = dict((g, list(sites.values()).count(g)) for g in ('A', 'B'))
    if len(sites) != 130 or counts != {'A': 118, 'B': 12}:
        raise ValueError('Expected the approved A118/B12 scope')
    if config['expected_sites'] != 130 or config['expected_groups'] != counts or config['expected_rows'] != 8190:
        raise ValueError('Scope completion contract mismatch')
    for site in sites:
        if (not re.match(r'^[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z]{2,}$', site)
                or site == 'stackoverflow.com' or 'meta' in site.split('.')):
            raise ValueError('Invalid or excluded main site hostname: ' + site)
    evidence = read_json(os.path.join(ROOT, 'classification_evidence.json'))
    if len(evidence) != len(sites) or dict((r['site'], r['group']) for r in evidence) != sites:
        raise ValueError('Classification evidence differs from approved scope')
    return sites
