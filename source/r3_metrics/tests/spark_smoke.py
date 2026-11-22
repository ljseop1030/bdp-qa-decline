# -*- coding: utf-8 -*-
"""Synthetic boundary cases. --modern-host is semantic QA, not VM certification."""
from __future__ import print_function
import argparse
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'load'))
from common import SERVING, calendar_rows, epoch, validate_monthly
from spark_job import project_partition, configure_sql, input_quality, empty_frame, create_sql_context, calculate, signature, register, save_serving, schemas


def host_table(sql, rows, schema, name):
    # Windows Python 3.13 workers may fail even on a tiny parallelized list.
    # Host-only JVM VALUES injection; VM tests exercise createDataFrame normally.
    def literal(v, field):
        value = 'NULL' if v is None else ('true' if v else 'false') if isinstance(v, bool) else ("'" + v.replace("'", "''") + "'") if isinstance(v, str) else str(v)
        return 'CAST(%s AS %s)' % (value, field.dataType.simpleString())
    values = ','.join('(' + ','.join(literal(v, f) for v, f in zip(row, schema.fields)) + ')' for row in rows)
    frame = sql.sql('SELECT * FROM VALUES ' + values + ' AS t(' + ','.join(schema.fieldNames()) + ')')
    if name is not None:
        frame.createOrReplaceTempView(name)
    return frame


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--modern-host', action='store_true')
    p.add_argument('--output', help='New HDFS/local Spark path for Parquet readback; required on VM')
    args = p.parse_args()
    from pyspark import SparkContext
    from pyspark.sql.types import StructType, StructField, StringType, IntegerType
    if args.modern_host:
        from pyspark.sql import SparkSession
        spark = (SparkSession.builder.master('local[1]').appName('R3-semantic-test')
                 .config('spark.ui.enabled', 'false').config('spark.sql.shuffle.partitions', '2')
                 .config('spark.driver.host', '127.0.0.1').config('spark.driver.bindAddress', '127.0.0.1')
                 .config('spark.sql.session.timeZone', 'UTC').getOrCreate())
        sc, sql = spark.sparkContext, spark
    else:
        if not args.output:
            p.error('--output is required on VM for Parquet write/readback')
        sc = SparkContext(appName='R3-VM-smoke')
        if not sc.version.startswith('1.3.'):
            sc.stop()
            raise ValueError('Expected Spark 1.3.x')
        sql = create_sql_context(sc)
    try:
        if args.modern_host:
            sql.conf.set('spark.sql.shuffle.partitions', '2')
            sql.conf.set('spark.sql.parquet.compression.codec', 'snappy')
        else:
            configure_sql(sql)
        jan = epoch('2019-01-01T00:00:00Z')
        dec = epoch('2019-12-31T00:00:00Z')
        posts = [(1, 1, jan, None, 1, 'a', 2019), (1, 1, jan, None, 1, 'b', 2019),
                 (2, 2, jan + 604800, 1, 1, 'a', 2019), (3, 2, jan + 604801, 1, 1, 'a', 2019),
                 (4, 1, dec, None, 12, 'a', 2019), (5, 2, dec + 86400, 4, 1, 'a', 2020),
                 (6, 1, jan, None, 1, 'a', 2019), (7, 2, jan + 604801, 6, 1, 'a', 2019),
                 (8, 2, jan - 1, 6, 12, 'a', 2018)]
        users = [(-1, jan, 1, 'a', 2019), (2, jan, 1, 'a', 2019)]
        votes = [(1, 2, jan, 1, 'a', 2019), (2, 3, jan, 1, 'a', 2019), (3, 1, jan, 1, 'a', 2019)]
        for table, data in [('posts', posts), ('users', users), ('votes', votes)]:
            print('SMOKE: validate input ' + table, file=sys.stderr)
            schema = StructType(schemas()[table].fields + [StructField('site', StringType(), True), StructField('year', IntegerType(), True)])
            if args.modern_host:
                checked = host_table(sql, data, schema, None)
            else:
                empty = empty_frame(sc, sql, schema)
                assert empty.count() == 0
                assert signature(empty) == [(f.name, f.dataType.simpleString()) for f in schema.fields]
                checked = empty.unionAll(sql.createDataFrame(data, schema))
            from pyspark import StorageLevel
            checked.persist(StorageLevel.DISK_ONLY)
            # Spark 1.3 registers the logical plan, which must not refer to its own table.
            register(checked, table)
            try:
                input_quality(sql, table, len(data))
            finally:
                checked.unpersist()
        # Multiple pending SQL projections must keep their own year input.
        print('SMOKE: validate year partitions', file=sys.stderr)
        pieces = []
        raw_schema = schemas()['users']
        for year, ids in [(2019, [101]), (2020, [201, 202])]:
            data = [(item, jan, 1) for item in ids]
            if args.modern_host:
                part = host_table(sql, data, raw_schema, None)
            else:
                part = sql.createDataFrame(data, raw_schema)
            pieces.append(project_partition(sql, part, ['id', 'creation_date', 'month'],
                                            'users', 'regression.example', year))
        merged = pieces[0].unionAll(pieces[1])
        observed = sorted((r['year'], r['id']) for r in merged.collect())
        assert observed == [(2019, 101), (2020, 201), (2020, 202)], observed
        calendar = []
        for site, group in [('a', 'A'), ('b', 'B')]:
            calendar.extend(calendar_rows(site, group, epoch('2018-01-01T00:00:00Z'), epoch('2024-03-01T00:00:00Z')))
        if args.modern_host:
            host_table(sql, calendar, schemas()['calendar'], 'analysis_calendar')
        # COALESCE(bigint aggregate, integer zero) must retain the serving types.
        print('SMOKE: calculate monthly metrics', file=sys.stderr)
        actual = validate_monthly([r.asDict() for r in calculate(sql, None if args.modern_host else calendar).collect()], {'a': 'A', 'b': 'B'})
        serving_types = dict(signature(sql.sql('SELECT * FROM monthly_audit')))
        assert serving_types['questions_count'] == 'bigint', serving_types
        assert serving_types['new_users_count'] == 'bigint', serving_types
        assert serving_types['answered_rate'] == 'double', serving_types
        assert serving_types['downvote_ratio'] == 'double', serving_types

        lookup = dict(((r['site'], r['month']), r) for r in actual)
        checks = [lookup['a', '2019-01']['answered_rate'] == .5,
                  lookup['b', '2019-01']['answered_rate'] == 0,
                  lookup['a', '2019-12']['answered_rate'] == 1,
                  lookup['a', '2019-01']['new_users_count'] == 1,
                  lookup['a', '2019-01']['downvote_ratio'] == .5,
                  lookup['a', '2019-02']['questions_count'] == 0,
                  lookup['a', '2019-02']['answered_rate'] is None,
                  lookup['a', '2024-02']['answered_rate'] is None,
                  lookup['a', '2024-02']['questions_count'] == 0,
                  lookup['a', '2024-03']['questions_count'] is None]
        if not all(checks):
            raise ValueError('Boundary checks failed: ' + str(checks))
        serving = sql.sql('SELECT %s FROM monthly_audit' % ','.join(SERVING))
        if args.output:
            print('SMOKE: write and read serving Parquet', file=sys.stderr)
            if args.modern_host:
                serving.write.mode('error').parquet(args.output)
                if sorted(tuple(r) for r in serving.collect()) != sorted(tuple(r) for r in sql.read.parquet(args.output).collect()):
                    raise ValueError('Readback mismatch')
            else:
                save_serving(sql, serving, args.output)
        print('SMOKE PASS: cross-site IDs, 7-day boundaries, negative delay, year crossing, Community, votes, zero/NULL and tail censoring.')
        print('Runtime: Spark %s; course-runtime smoke test: %s; full R1 run still required.' % (sc.version, not args.modern_host))
    finally:
        sc.stop()


if __name__ == '__main__':
    main()
