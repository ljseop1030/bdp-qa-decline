# -*- coding: utf-8 -*-
"""Input contract tests without a VM; metastore integration remains a VM check."""
import os
import sys
import unittest
import types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from spark_job import load_hive, create_sql_context, empty_frame, hive_locations, project_partition


class Type(object):
    def __init__(self, name):
        self.name = name
    def simpleString(self):
        return self.name


class Field(object):
    def __init__(self, name, kind):
        self.name, self.dataType = name, Type(kind)


class Frame(object):
    def __init__(self, columns, rows=None):
        self.schema = self
        self.fields = [Field(n, t) for n, t in columns]
        self.rows = rows or []
    def collect(self):
        return self.rows
    def registerTempTable(self, name):
        self.registered = name


COLUMNS = [('id', 'bigint'), ('creation_date', 'bigint'), ('month', 'int'),
           ('site', 'string'), ('year', 'int')]


class SQL(object):
    def __init__(self):
        self.columns = list(COLUMNS)
        self.years = [2019, 2020]
        self.root = 'hdfs://namenode:8020/project/parquet/users'
        self.partition_root = self.root
        self.queries = []
        self.selected = Frame(COLUMNS)
    def sql(self, query):
        self.queries.append(query)
        if query.startswith('DESCRIBE'):
            location = self.root
            if 'PARTITION' in query:
                year = query.split('year=')[1].rstrip(')')
                location = self.partition_root + '/site=cs.stackexchange.com/year=' + year
            return Frame([], [('Location:', location, None)])
        if 'LIMIT 0' in query:
            return Frame(self.columns)
        if 'DISTINCT year' in query:
            return Frame([], [(y,) for y in self.years])
        return self.selected


class ContextIsolation(unittest.TestCase):
    def test_hive_native_single_column_and_structured_rows(self):
        self.assertEqual(hive_locations([('Location:   \thdfs:///project/parquet/posts\t',)]),
                         ['hdfs:///project/parquet/posts'])
        self.assertEqual(hive_locations([('Location:', 'hdfs:///project/parquet/posts', '')]),
                         ['hdfs:///project/parquet/posts'])
        self.assertEqual(hive_locations([('',), ('# Detailed Table Information',)]), [])

    def test_partition_projections_survive_lazy_resolution(self):
        catalog = {}
        class Part(object):
            def __init__(self, rows):
                self.rows = rows
            def registerTempTable(self, name):
                catalog[name] = self.rows
        class LazySQL(object):
            def sql(self, query):
                name = query.split(' FROM ')[1]
                return lambda: catalog[name]
        sql = LazySQL()
        a = project_partition(sql, Part([101]), ['id'], 'users', 'cs.stackexchange.com', 2019)
        b = project_partition(sql, Part([201, 202]), ['id'], 'users', 'cs.stackexchange.com', 2020)
        self.assertEqual(a(), [101])
        self.assertEqual(b(), [201, 202])

    def test_empty_frame_works_without_emptyRDD(self):
        calls = []
        class LegacyContext(object):
            def parallelize(self, rows, partitions):
                calls.append((rows, partitions))
                return 'empty-rdd'
        class SQLContext(object):
            def createDataFrame(self, rdd, schema):
                return (rdd, schema)
        self.assertEqual(empty_frame(LegacyContext(), SQLContext(), 'schema'), ('empty-rdd', 'schema'))
        self.assertEqual(calls, [([], 1)])

    def test_parquet_does_not_require_hive_and_hive_is_explicit(self):
        original = dict((n, sys.modules.get(n)) for n in ('pyspark', 'pyspark.sql'))
        package, module = types.ModuleType('pyspark'), types.ModuleType('pyspark.sql')
        calls = []
        module.SQLContext = lambda sc: calls.append(('sql', sc)) or 'plain-context'
        package.sql = module
        sys.modules['pyspark'], sys.modules['pyspark.sql'] = package, module
        try:
            # No HiveContext exists in this simulated install.
            self.assertEqual(create_sql_context('sc'), 'plain-context')
            with self.assertRaises(ImportError):
                create_sql_context('sc', True)
            module.HiveContext = lambda sc: calls.append(('hive', sc)) or 'hive-context'
            self.assertEqual(create_sql_context('sc', True), 'hive-context')
            self.assertEqual(calls, [('sql', 'sc'), ('hive', 'sc')])
        finally:
            for name, value in original.items():
                if value is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = value


class HiveInput(unittest.TestCase):
    def setUp(self):
        self.sql = SQL()
        self.task = {'site': 'cs.stackexchange.com', 'input': '/project/parquet',
                     'hive_database': 'qa_decline'}
    def load(self):
        return load_hive(self.sql, self.task, 'users', {'years': [2019, 2020]}, Frame(COLUMNS))
    def test_selects_completed_site_with_explicit_columns(self):
        self.assertIs(self.load(), self.sql.selected)
        self.assertIn("SELECT `id`,`creation_date`,`month`,`site`,`year` FROM `qa_decline`.`users` WHERE site='cs.stackexchange.com'", self.sql.queries)
        self.assertEqual(self.sql.selected.registered, 'r2_input')
    def test_missing_or_extra_year_rejected(self):
        for years in ([2019], [2019, 2020, 2021], [2019, None]):
            self.sql.years = years
            with self.assertRaises(ValueError):
                self.load()
    def test_wrong_types_rejected(self):
        self.sql.columns[1] = ('creation_date', 'timestamp')
        with self.assertRaises(ValueError):
            self.load()
    def test_reordered_base_columns_rejected(self):
        self.sql.columns[0], self.sql.columns[1] = self.sql.columns[1], self.sql.columns[0]
        with self.assertRaises(ValueError):
            self.load()
    def test_appended_data_column_allowed(self):
        self.sql.columns.insert(3, ('extra', 'string'))
        self.load()
    def test_wrong_table_or_partition_location_rejected(self):
        self.sql.root = 'file:/project/parquet/users'
        with self.assertRaises(ValueError):
            self.load()
        self.sql.root = 'hdfs:///project/parquet/users'
        self.sql.partition_root = 'hdfs:///another/snapshot/users'
        with self.assertRaises(ValueError):
            self.load()
    def test_identifier_injection_rejected(self):
        self.task['hive_database'] = 'qa_decline; DROP TABLE users'
        with self.assertRaises(ValueError):
            self.load()
        self.assertEqual(self.sql.queries, [])


if __name__ == '__main__':
    unittest.main()
