# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from copy import deepcopy
import unittest

from test_core import sample_rows
from vm_test import compare


class AuditComparison(unittest.TestCase):
    def setUp(self):
        self.sites = {'cs.stackexchange.com': 'A'}
        self.expected = sample_rows(self.sites)

    def test_key_order_and_ratio_tolerance(self):
        actual = deepcopy(self.expected)
        actual[0]['answered_rate'] += 5e-13
        result = compare(list(reversed(actual)), self.expected, self.sites)
        self.assertTrue(result['match'])
        self.assertEqual(result['cells_compared'], 63 * 14)
        self.assertGreater(result['max_ratio_delta']['answered_rate'], 0)

    def test_reports_count_ratio_and_null_differences(self):
        actual = deepcopy(self.expected)
        actual[0].update(questions_count=0, answered_questions_count=0, answered_rate=None)
        actual[1].update(upvotes_count=8, downvotes_count=2, downvote_ratio=.2)
        result = compare(actual, self.expected, self.sites)
        self.assertFalse(result['match'])
        self.assertEqual(result['mismatched_cells'], 6)
        self.assertEqual(result['mismatches_by_column']['answered_rate'], 1)
        self.assertEqual(result['mismatches_by_column']['downvote_ratio'], 1)
        self.assertIn({'site': actual[0]['site'], 'month': '2019-01', 'column': 'answered_rate',
                       'expected': .5, 'actual': None}, result['differences'])

    def test_missing_null_column_and_duplicate_key_fail(self):
        actual = deepcopy(self.expected)
        del actual[-1]['answered_rate']
        with self.assertRaises(ValueError):
            compare(actual, self.expected, self.sites)
        actual = deepcopy(self.expected)
        actual[1]['month'] = actual[0]['month']
        with self.assertRaises(ValueError):
            compare(actual, self.expected, self.sites)
