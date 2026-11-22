# -*- coding: utf-8 -*-
"""R2's Hive partition files may have arbitrary Parquet part names."""
import json
import os
import sys
import unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'load'))
import run as runner


class R2Files(unittest.TestCase):
    def setUp(self):
        self.capture = runner.capture
        self.filename = 'part-00000.parquet'
        self.missing = False
        def capture(command):
            if command[2] == '-cat':
                table = command[-1].split('.')[-2]
                return json.dumps(dict(site='cs.stackexchange.com', table=table,
                    rows=2, years=[2019], finished_at='complete',
                    parser_version='v0.3', dump_date='2024-04-02'))
            lines = []
            for table in ('posts', 'users', 'votes'):
                if self.missing and table == 'votes':
                    continue
                for name in (self.filename, 'part-second.parquet'):
                    lines.append('-rw-r--r-- 1 user group 1 2026-01-01 00:00 '
                        '/project/parquet/%s/site=cs.stackexchange.com/year=2019/%s' % (table, name))
            return '\n'.join(lines)
        runner.capture = capture

    def tearDown(self):
        runner.capture = self.capture

    def test_hive_accepts_multiple_arbitrary_part_names(self):
        statuses, hashes = runner.status_snapshot('/project/parquet', ['cs.stackexchange.com'], 'hive')
        self.assertEqual(len(hashes), 3)
        self.assertEqual(statuses['cs.stackexchange.com']['posts']['rows'], 2)

    def test_hive_rejects_year_without_parquet(self):
        self.missing = True
        with self.assertRaises(ValueError):
            runner.status_snapshot('/project/parquet', ['cs.stackexchange.com'], 'hive')

    def test_direct_reader_keeps_r1_filename_contract(self):
        with self.assertRaises(ValueError):
            runner.status_snapshot('/project/parquet', ['cs.stackexchange.com'], 'parquet')
        self.filename = 'part-2019.parquet'
        runner.status_snapshot('/project/parquet', ['cs.stackexchange.com'], 'parquet')


if __name__ == '__main__':
    unittest.main()
