# -*- coding: utf-8 -*-
"""Transaction boundaries with simulated external jobs, no Spark/HDFS required."""
from __future__ import print_function
import argparse
import os
import shutil
import sys
import tempfile
import unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import run as runner
from common import ROOT, read_json, write_json
from test_core import sample_rows


class VersionBanner(unittest.TestCase):
    def test_banner_with_legacy_exit_and_invalid_versions(self):
        original = runner.subprocess.Popen
        class Process(object):
            def communicate(self):
                return (self.banner.encode('utf-8'), None)
        process = Process()
        runner.subprocess.Popen = lambda *a, **k: process
        try:
            for code in (0, 1):
                process.returncode = code
                process.banner = 'Welcome to Spark   version 1.3.0\nType --help for more information.'
                self.assertEqual(runner.spark_version_banner(), process.banner)
            for banner, code in [('version 1.30.0', 0), ('version 4.0.1', 0),
                                 ('Java failed', 1), ('version 1.3.0', -9)]:
                process.returncode, process.banner = code, banner
                with self.assertRaises(ValueError):
                    runner.spark_version_banner()
        finally:
            runner.subprocess.Popen = original


class Completion(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix='r3-unit-')
        self.originals = dict((n, getattr(runner, n)) for n in ('preflight', 'spark', 'capture', 'status_snapshot'))
        self.sites = read_json(os.path.join(ROOT, 'selected_sites.json'))['sites']
        self.commands = []
        self.args = argparse.Namespace(site=None, input_root='/project/parquet', output_root='/project/r3/run-001',
                    local_output=os.path.join(self.temp, 'new-run'), driver_memory='768m', preflight=False, dry_run=False,
                    run_id='run-001', output_base='/project/statistics', work_root=os.path.join(self.temp, 'work'), log_root=os.path.join(self.temp, 'logs'))
        statuses = dict((s, {}) for s in self.sites)
        runner.preflight = lambda args, sites: (statuses, {'snapshot': 'before'}, 'simulated')
        runner.status_snapshot = lambda source, sites: (statuses, {'snapshot': 'before'})
        def capture(args):
            self.commands.append(args)
            return ''
        runner.capture = capture
        def spark(task, local, args):
            if task.get('mode') == 'combine':
                write_json(task['receipt'], {'rows': len(task['sites']) * 63})
            else:
                write_json(os.path.join(local, 'audit.json'), sample_rows({task['site']: task['group']}))
                write_json(os.path.join(local, 'quality.json'), {'simulated_test': True})
                write_json(os.path.join(local, 'spark_success.json'), {'site': task['site'], 'rows': 63})
        runner.spark = spark

    def tearDown(self):
        for name, value in self.originals.items():
            setattr(runner, name, value)
        absolute = os.path.abspath(self.temp)
        expected_parent = os.path.abspath(tempfile.gettempdir())
        if os.path.dirname(absolute) != expected_parent or not os.path.basename(absolute).startswith('r3-unit-'):
            raise ValueError('Unsafe test cleanup path')
        shutil.rmtree(absolute)

    def test_dry_run_has_no_writes(self):
        self.args.dry_run = True
        runner.run(self.args)
        self.assertFalse(os.path.exists(self.args.local_output))
        self.assertEqual(self.commands, [])
        self.assertFalse(os.path.exists(self.args.log_dir))

    def test_shared_path_defaults(self):
        self.args.output_root = None
        self.args.local_output = None
        runner.resolve_paths(self.args)
        self.assertEqual(self.args.output_root, '/project/statistics/run-001')
        self.assertEqual(self.args.local_output, os.path.join(self.temp, 'work', 'run-001'))
        self.assertEqual(self.args.log_dir, os.path.join(self.temp, 'logs', 'run-001'))

    def test_run_id_rejects_path_traversal(self):
        self.args.run_id = '../parquet'
        with self.assertRaises(ValueError):
            runner.resolve_paths(self.args)

    def test_all_path_roots_can_be_overridden(self):
        self.args.input_root = '/team/parquet'
        self.args.output_base = '/team/statistics/'
        self.args.output_root = None
        self.args.local_output = None
        self.args.work_root = os.path.join(self.temp, 'custom-work')
        self.args.log_root = os.path.join(self.temp, 'custom-logs')
        runner.resolve_paths(self.args)
        self.assertEqual(self.args.input_root, '/team/parquet')
        self.assertEqual(self.args.output_root, '/team/statistics/run-001')
        self.assertEqual(self.args.local_output, os.path.join(self.temp, 'custom-work', 'run-001'))
        self.assertEqual(self.args.log_dir, os.path.join(self.temp, 'custom-logs', 'run-001'))

    def test_full_success_requires_combined_receipt(self):
        runner.run(self.args)
        result = read_json(os.path.join(self.args.local_output, 'validated.json'))
        self.assertTrue(result['full_scope'])
        self.assertEqual(result['rows'], 8190)
        self.assertFalse(result['full_pilot'])
        self.assertEqual(result['target_sites'], 130)
        self.assertEqual(result['scope_id'], 'team-adjudicated-130-v1')
        self.assertEqual(self.commands[-1][-1], '/project/r3/run-001/_R3_SUCCESS.json')

    def test_hive_mode_reaches_tasks_and_receipts(self):
        self.args.input_mode = 'hive'
        self.args.hive_database = 'qa_decline'
        self.args.site = ['cs.stackexchange.com']
        original = runner.spark
        tasks = []
        def spark(task, local, args):
            tasks.append(task)
            original(task, local, args)
        runner.spark = spark
        runner.run(self.args)
        self.assertEqual(tasks[0]['input_mode'], 'hive')
        self.assertEqual(tasks[0]['hive_database'], 'qa_decline')
        for name in ('run_plan.json', 'validated.json'):
            result = read_json(os.path.join(self.args.local_output, name))
            self.assertEqual(result['input_mode'], 'hive')
            self.assertEqual(result['hive_database'], 'qa_decline')

    def test_hive_driver_receives_metastore_configuration(self):
        runner.resolve_paths(self.args)
        self.args.hive_conf_dir = '/team/hive/conf'
        original = runner.subprocess.Popen
        commands = []
        class Process(object):
            def wait(self):
                return 0
        def launch(command, **kwargs):
            commands.append(command)
            return Process()
        runner.subprocess.Popen = launch
        try:
            for mode in ('hive', 'parquet'):
                local = os.path.join(self.temp, 'driver-' + mode)
                os.mkdir(local)
                self.originals['spark']({'input_mode': mode,
                    'log_path': os.path.join(local, 'spark.log')}, local, self.args)
            self.assertIn('--driver-class-path', commands[0])
            self.assertEqual(commands[0][commands[0].index('--driver-class-path') + 1], '/team/hive/conf')
            self.assertNotIn('--driver-class-path', commands[1])
        finally:
            runner.subprocess.Popen = original

    def test_subset_does_not_publish_ab_inference(self):
        self.args.site = ['cs.stackexchange.com']
        runner.run(self.args)
        result = read_json(os.path.join(self.args.local_output, 'validated.json'))
        self.assertFalse(result['full_scope'])
        self.assertFalse(os.path.exists(os.path.join(self.args.local_output, 'statistical_tests.json')))

    def test_old_ten_site_subset_is_not_scope_completion(self):
        self.args.site = ['craftcms', 'cs', 'drupal', 'emacs', 'gardening',
                          'interpersonal', 'parenting', 'pets', 'vi', 'woodworking']
        self.args.site = [s + '.stackexchange.com' for s in self.args.site]
        runner.run(self.args)
        result = read_json(os.path.join(self.args.local_output, 'validated.json'))
        self.assertFalse(result['full_scope'])
        self.assertEqual(result['rows'], 630)
        self.assertEqual(result['expected_rows'], 8190)
        self.assertFalse(os.path.exists(os.path.join(self.args.local_output, 'statistical_tests.json')))

    def test_changed_r1_snapshot_prevents_completion(self):
        self.args.site = ['cs.stackexchange.com']
        runner.status_snapshot = lambda source, sites: ({}, {'snapshot': 'changed'})
        with self.assertRaises(ValueError):
            runner.run(self.args)
        self.assertFalse(os.path.exists(os.path.join(self.args.local_output, 'validated.json')))
        self.assertTrue(os.path.exists(os.path.join(self.args.local_output, 'failure.json')))
        self.assertFalse(any('-put' in c for c in self.commands))

    def test_job_without_success_receipt_fails(self):
        self.args.site = ['cs.stackexchange.com']
        runner.spark = lambda *args: None
        with self.assertRaises(IOError):
            runner.run(self.args)
        self.assertFalse(os.path.exists(os.path.join(self.args.local_output, 'validated.json')))
        self.assertFalse(any('-put' in c for c in self.commands))


if __name__ == '__main__':
    unittest.main()
