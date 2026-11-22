# -*- coding: utf-8 -*-
from __future__ import division
import itertools
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'load'))
from common import load_scope, read_json, ROOT
from report import rank_test


class ApprovedScope(unittest.TestCase):
    def test_scope_membership_and_adjusted_labels(self):
        sites = load_scope()
        self.assertEqual(len(sites), 130)
        self.assertEqual(list(sites.values()).count('A'), 118)
        self.assertEqual(list(sites.values()).count('B'), 12)
        for site, group in [('ham', 'A'), ('photo', 'A'), ('outdoors', 'B'), ('diy', 'B')]:
            self.assertEqual(sites[site + '.stackexchange.com'], group)
        for site in ['islam.stackexchange.com', 'stackoverflow.com', 'meta.stackexchange.com']:
            self.assertNotIn(site, sites)
        self.assertIn('askubuntu.com', sites)
        self.assertIn('serverfault.com', sites)

    def test_changed_selection_hash_or_completion_target_is_rejected(self):
        config = read_json(os.path.join(ROOT, 'config.json'))
        for key, value in [('selection_sha256', 'changed'), ('expected_rows', 630),
                           ('expected_groups', {'A': 117, 'B': 13}), ('expected_sites', 10)]:
            altered = dict(config)
            altered[key] = value
            with self.assertRaises(ValueError):
                load_scope(altered)


class ExactRankDistribution(unittest.TestCase):
    def test_matches_brute_force_with_ties_and_unequal_groups(self):
        for a, b in [([1, 2, 2, 4], [2, 3]), ([1, 3], [2, 2, 5, 6]),
                     ([1, 1, 1], [1, 1, 1]), ([1, 2, 5], [3, 4, 5])]:
            values = a + b
            ranks = [1 + sum(x < v for x in values) + (sum(x == v for x in values) - 1) / 2 for v in values]
            observed = sum(ranks[:len(a)])
            totals = [sum(ranks[i] for i in c) for c in itertools.combinations(range(len(values)), len(a))]
            expected = min(1, 2 * min(sum(t <= observed for t in totals), sum(t >= observed for t in totals)) / len(totals))
            result = rank_test(a, b)
            self.assertEqual(result['permutations'], len(totals))
            self.assertEqual(result['p_exact'], expected)

    def test_full_scope_complete_separation(self):
        result = rank_test(list(range(118)), list(range(118, 130)))
        total = math.factorial(130) // math.factorial(118) // math.factorial(12)
        self.assertEqual(result['permutations'], total)
        self.assertAlmostEqual(result['p_exact'] / (2 / total), 1)
