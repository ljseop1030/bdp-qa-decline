# -*- coding: utf-8 -*-
from __future__ import division
import copy
import os
import sys
import unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import calendar_rows, check_paths, check_status, epoch, month_keys, validate_monthly
from report import holm, indexed, rank_test, comparisons


def sample_rows(sites):
    rows = []
    for site, group in sites.items():
        for c in calendar_rows(site, group, epoch('2018-01-01T00:00:00Z'), epoch('2024-03-01T00:00:00Z')):
            complete, mature = c[-2:]
            rows.append(dict(site=site, domain_group=group, is_meta=False, parent_site=site, month=c[6],
                complete_month=complete, answer_mature=mature, questions_count=10 if complete else None,
                new_users_count=4 if complete else None, answered_questions_count=5 if mature else None,
                upvotes_count=9 if complete else None, downvotes_count=1 if complete else None,
                answered_rate=.5 if mature else None, downvote_ratio=.1 if complete else None))
    return rows


class Contracts(unittest.TestCase):
    def test_r1_metadata(self):
        status = dict(site='cs.stackexchange.com', table='posts', rows=3, years=[2020],
                      parser_version='v0.3', dump_date='2024-04-02', finished_at='2026-10-04T00:00:00Z')
        check_status(status, status['site'], 'posts')
        for key, value in [('rows', True), ('rows', -1), ('years', [2020, 2020]), ('parser_version', 'v0.2'), ('dump_date', '2025-01-01')]:
            bad = dict(status, **{key: value})
            with self.assertRaises(ValueError):
                check_status(bad, status['site'], 'posts')

    def test_empty_r1_table(self):
        check_status(dict(site='s', table='votes', rows=0, years=[], parser_version='v0.3',
                          dump_date='2024-04-02', finished_at='x'), 's', 'votes')

    def test_paths_are_separate(self):
        check_paths('/project/parquet', '/project/r3/run-001')
        for path in ['/project/parquet', '/project/parquet/r3', '/project', '/project/r3/../parquet']:
            with self.assertRaises(ValueError):
                check_paths('/project/parquet', path)

    def test_calendar_start_and_tail(self):
        rows = calendar_rows('s', 'A', epoch('2019-01-01T00:00:00Z'), epoch('2024-03-01T00:00:00Z'))
        self.assertEqual(len(rows), 63)
        self.assertEqual(rows[0][-2:], (False, False))
        self.assertEqual(rows[1][-2:], (True, True))
        self.assertEqual(rows[-3][-2:], (True, True))
        self.assertEqual(rows[-2][-2:], (True, False))
        self.assertEqual(rows[-1][-2:], (False, False))

    def test_validate_full_months(self):
        self.assertEqual(len(validate_monthly(sample_rows({'s': 'A'}), {'s': 'A'})), 63)

    def test_reject_missing_duplicate_and_wrong_group(self):
        rows = sample_rows({'s': 'A'})
        for bad in [rows[:-1], rows + rows[:1], [dict(r, domain_group='B') for r in rows]]:
            with self.assertRaises(ValueError):
                validate_monthly(bad, {'s': 'A'})

    def test_reject_bad_ratios_and_null_policy(self):
        for key, value in [('answered_rate', .8), ('downvote_ratio', float('nan')), ('questions_count', None), ('is_meta', True)]:
            rows = sample_rows({'s': 'A'})
            rows[0][key] = value
            with self.assertRaises(ValueError):
                validate_monthly(rows, {'s': 'A'})

    def test_zero_denominator(self):
        rows = sample_rows({'s': 'A'})
        rows[0].update(questions_count=0, answered_questions_count=0, answered_rate=None,
                       upvotes_count=0, downvotes_count=0, downvote_ratio=None)
        validate_monthly(rows, {'s': 'A'})

    def test_exact_permutation_and_ties(self):
        result = rank_test([1, 2, 3], [4, 5, 6])
        self.assertEqual(result['permutations'], 20)
        self.assertAlmostEqual(result['p_exact'], .1)
        self.assertEqual(result['cliffs_delta'], -1)
        self.assertEqual(rank_test([2, 2], [2, 2])['p_exact'], 1)
        self.assertEqual(rank_test([1, 2], [2, 3])['cliffs_delta'], -.75)

    def test_exact_5_plus_5(self):
        result = rank_test([1, 2, 3, 4, 5], [6, 7, 8, 9, 10])
        self.assertEqual(result['permutations'], 252)
        self.assertAlmostEqual(result['p_exact'], 2 / 252)
        self.assertIsNone(rank_test([1], [2, 3])['p_exact'])

    def test_holm(self):
        actual = holm([.04, .001, .03, .9])
        for a, b in zip(actual, [.09, .004, .09, .9]):
            self.assertAlmostEqual(a, b)
        corrected = holm([None, .01, None, .1])
        self.assertIsNone(corrected[0])
        self.assertEqual(corrected[1], .04)
        self.assertIsNone(corrected[2])
        self.assertAlmostEqual(corrected[3], .3)

    def test_index_missing_and_zero_baseline(self):
        rows = sample_rows({'s': 'A'})
        values, bases = indexed(rows, {'s': 'A'})
        self.assertEqual(values[0]['index_2019'], 100)
        rows[0]['questions_count'] = None
        values, bases = indexed(rows, {'s': 'A'})
        self.assertIsNone(values[0]['index_2019'])
        self.assertEqual(bases[0]['reason'], 'incomplete_2019')
        for r in rows:
            r['questions_count'] = 0
        self.assertEqual(indexed(rows, {'s': 'A'})[1][0]['reason'], 'zero_2019_mean')

    def test_changes_equal_months_and_missing_exclusion(self):
        sites = {'a': 'A', 'b': 'B'}
        rows = sample_rows(sites)
        for r in rows:
            if r['month'].startswith('2023-'):
                r['questions_count'] = 5
                r['answered_rate'] = .4
        changes, tests = comparisons(rows, sites)
        primary = [r for r in changes if r['analysis'] == 'primary_2022_2023' and r['site'] == 'a']
        self.assertEqual(primary[0]['change'], -50)
        self.assertAlmostEqual(primary[2]['change'], -10)
        self.assertEqual(tests[-2]['matched_months'], [1])
        rows[0]['questions_count'] = None
        changes, tests = comparisons(rows, sites)
        self.assertIn('a', tests[4]['excluded_sites'])


if __name__ == '__main__':
    unittest.main()
