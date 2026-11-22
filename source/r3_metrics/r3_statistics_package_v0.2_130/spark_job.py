# -*- coding: utf-8 -*-
"""Spark 1.3 job; all raw rows stay in Spark, only 63 monthly rows are collected."""
from __future__ import print_function
import json
import os
import re
import sys
try:
    from urlparse import urlsplit
except ImportError:
    from urllib.parse import urlsplit
from common import (ROOT, SERVING, TABLES, calendar_rows, capture, check_status,
                    epoch, read_json, validate_monthly, write_json)


def create_sql_context(sc, use_hive=False):
    # Parquet and synthetic smoke data do not require a Hive metastore.
    # Import/create HiveContext only for explicitly requested R2 table reads.
    if use_hive:
        from pyspark.sql import HiveContext
        return HiveContext(sc)
    from pyspark.sql import SQLContext
    return SQLContext(sc)


def empty_frame(sc, sql, schema):
    # Spark 1.3 Python SparkContext has no emptyRDD method.
    return sql.createDataFrame(sc.parallelize([], 1), schema)


def configure_sql(sql):
    sql.setConf('spark.sql.shuffle.partitions', '2')
    sql.setConf('spark.sql.parquet.compression.codec', 'snappy')


def hive_locations(rows):
    locations = []
    for row in rows:
        # Spark 1.3 HiveNativeCommand returns Row(result=<tab-delimited text>).
        cells = row[0].split('\t') if len(row) == 1 and row[0] else list(row)
        if len(cells) >= 2 and cells[0] and cells[0].strip().rstrip(':').lower() == 'location':
            locations.append(cells[1].strip())
    return locations


def input_quality(sql, table, expected_rows):
    count = sql.sql('SELECT COUNT(*) FROM %s' % table).collect()[0][0]
    if count != expected_rows:
        raise ValueError('R1 status/count mismatch: %s; expected=%s, actual=%s' % (table, expected_rows, count))
    invalid = sql.sql('SELECT id FROM %s WHERE id IS NULL OR creation_date IS NULL OR month IS NULL OR month < 1 OR month > 12' % table).count()
    duplicates = sql.sql('SELECT id FROM %s GROUP BY site,id HAVING COUNT(*) > 1' % table).count()
    type_column = {'posts': 'post_type_id', 'votes': 'vote_type_id'}.get(table)
    if type_column:
        invalid += sql.sql('SELECT id FROM %s WHERE %s IS NULL' % (table, type_column)).count()
    if invalid or duplicates:
        raise ValueError('Invalid required values or duplicate IDs: ' + table)
    return {'rows': count, 'invalid_required': invalid, 'duplicate_ids': duplicates}


def schemas():
    from pyspark.sql.types import StructType, StructField, StringType, LongType, IntegerType, BooleanType
    def schema(items):
        return StructType([StructField(n, t, True) for n, t in items])
    lng, integer, string, boolean = LongType(), IntegerType(), StringType(), BooleanType()
    return {
        'posts': schema([('id', lng), ('post_type_id', integer), ('creation_date', lng), ('parent_id', lng), ('month', integer)]),
        'users': schema([('id', lng), ('creation_date', lng), ('month', integer)]),
        'votes': schema([('id', lng), ('vote_type_id', integer), ('creation_date', lng), ('month', integer)]),
        'calendar': schema([('site', string), ('domain_group', string), ('is_meta', boolean), ('parent_site', string),
                            ('year', integer), ('month_number', integer), ('month_key', string),
                            ('complete_month', boolean), ('answer_mature', boolean)])}


def register(df, name):
    df.registerTempTable(name)


def calculate(sql, calendar):
    if calendar is not None:
        register(sql.createDataFrame(calendar, schemas()['calendar']), 'analysis_calendar')
    with open(os.path.join(ROOT, 'metrics.sql'), 'r') as f:
        for block in f.read().split('-- @view '):
            if block.strip():
                name, query = block.split('\n', 1)
                register(sql.sql(query.strip()), name.strip())
    return sql.sql('SELECT * FROM monthly_audit ORDER BY site,month')


def signature(df):
    return [(f.name, f.dataType.simpleString()) for f in df.schema.fields]


def save_serving(sql, df, path):
    contract = [('site', 'string'), ('month', 'string'), ('domain_group', 'string'),
                ('questions_count', 'bigint'), ('new_users_count', 'bigint'),
                ('answered_rate', 'double'), ('downvote_ratio', 'double')]
    if signature(df) != contract:
        raise ValueError('Serving schema differs from the seven-column R2/R4 contract')
    df.saveAsParquetFile(path)
    back = sql.parquetFile(path)
    if signature(back) != signature(df):
        raise ValueError('Serving Parquet schema readback mismatch')
    expected = sorted([tuple(r) for r in df.collect()])
    actual = sorted([tuple(r) for r in back.collect()])
    if actual != expected:
        raise ValueError('Serving Parquet full readback mismatch')


def project_partition(sql, part, columns, table, site, year):
    # Spark 1.3 can resolve SQL temp-table references lazily. Never overwrite
    # a name used by a pending UNION branch with another year's DataFrame.
    name = 'r1_%s_%s_%d' % (table, site.replace('.', '_'), year)
    register(part, name)
    return sql.sql("SELECT %s, '%s' AS site, CAST(%d AS INT) AS year FROM %s" % (
        ','.join(columns), site, year, name))


def load_hive(sql, task, table, status, full_schema):
    database = task.get('hive_database', 'qa_decline')
    site = task['site']
    if (not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', database)
            or table not in TABLES or not re.match(r'^[a-z0-9.]+$', site)):
        raise ValueError('Invalid Hive input identifier')
    source = '`%s`.`%s`' % (database, table)
    def check_location(statement, expected):
        details = sql.sql(statement).collect()
        locations = hive_locations(details)
        if len(locations) != 1:
            raise ValueError('Cannot verify R2 Hive LOCATION: ' + statement)
        uri = urlsplit(locations[0])
        if uri.scheme not in ('', 'hdfs') or uri.path.rstrip('/') != expected.rstrip('/'):
            raise ValueError('R2 Hive LOCATION differs from --input-root: ' + locations[0])
    root = task['input'].rstrip('/') + '/' + table
    check_location('DESCRIBE FORMATTED ' + source, root)
    for year in status['years']:
        check_location("DESCRIBE FORMATTED %s PARTITION (site='%s', year=%d)" % (source, site, year),
                       root + '/site=%s/year=%d' % (site, year))
    wanted = [(f.name, f.dataType.simpleString()) for f in full_schema.fields]
    # Inspect the complete schema before selecting: never silently cast R2 types.
    actual = signature(sql.sql('SELECT * FROM %s LIMIT 0' % source))
    base = wanted[:-2]
    physical = [(n, t) for n, t in actual if n not in ('site', 'year')]
    if (physical[:len(base)] != base or
            dict(actual).get('site') != 'string' or dict(actual).get('year') != 'int'):
        raise ValueError('R2 Hive schema mismatch: ' + source)
    # Read only this completed site, but do not hide unexpected registered years.
    selected = sql.sql("SELECT %s FROM %s WHERE site='%s'" % (
        ','.join('`%s`' % n for n, t in wanted), source, site))
    register(selected, 'r2_input')
    years = [r[0] for r in sql.sql('SELECT DISTINCT year FROM r2_input').collect()]
    if set(years) != set(status['years']):
        raise ValueError('R2 registered years differ from R1 status: ' + source)
    return selected


def run_site(sc, sql, task):
    from pyspark.sql.types import StructType, StructField, StringType, IntegerType
    from pyspark import StorageLevel
    site, group = task['site'], task['group']
    quality, frames = {}, []
    for table in TABLES:
        status = check_status(task['statuses'][table], site, table)
        base_schema = schemas()[table]
        full_schema = StructType(base_schema.fields + [StructField('site', StringType(), True), StructField('year', IntegerType(), True)])
        if task.get('input_mode', 'parquet') == 'hive':
            combined = load_hive(sql, task, table, status, full_schema)
            # Spark 1.3 can retain conflicting Hive relation attribute IDs in
            # self joins of a filtered/cached temp view. Rebind an explicit
            # schema through the distributed RDD; never collect raw rows.
            combined = sql.createDataFrame(combined.rdd, full_schema)
        else:
            combined = empty_frame(sc, sql, full_schema)
            for year in status['years']:
                path = '%s/%s/site=%s/year=%d/part-%d.parquet' % (task['input'], table, site, year, year)
                part = sql.parquetFile(path)
                wanted = [(f.name, f.dataType.simpleString()) for f in base_schema.fields]
                actual = signature(part)
                # Some Spark versions discover partition columns even on a file path.
                if [(n, t) for n, t in actual if n not in ('site', 'year')] != wanted:
                    raise ValueError('R1 physical schema mismatch: ' + path)
                selected = project_partition(sql, part, [n for n, t in wanted], table, site, year)
                combined = combined.unionAll(selected)
        combined.persist(StorageLevel.DISK_ONLY)
        frames.append(combined)
        register(combined, table)
        quality[table] = input_quality(sql, table, status['rows'])
    quality['orphan_answers'] = sql.sql('SELECT a.id FROM posts a LEFT JOIN posts q ON a.site=q.site AND a.parent_id=q.id AND q.post_type_id=1 WHERE a.post_type_id=2 AND q.id IS NULL').count()
    quality['negative_answer_delays'] = sql.sql('SELECT a.id FROM posts a JOIN posts q ON a.site=q.site AND a.parent_id=q.id AND q.post_type_id=1 WHERE a.post_type_id=2 AND a.creation_date<q.creation_date').count()
    first = sql.sql('SELECT MIN(creation_date) AS first_epoch FROM posts').collect()[0][0]
    calendar = calendar_rows(site, group, first, epoch(task['coverage_end_exclusive']))
    audit = calculate(sql, calendar)
    rows = validate_monthly([r.asDict() for r in audit.collect()], {site: group})
    serving = sql.sql('SELECT %s FROM monthly_audit ORDER BY site,month' % ','.join(SERVING))
    save_serving(sql, serving, task['output'] + '/serving')
    # R1 must be idle throughout this run. Compare status bytes again in the wrapper too.
    for table in TABLES:
        path = '%s/_status/%s.%s.json' % (task['input'], site, table)
        if json.loads(capture(['hdfs', 'dfs', '-cat', path])) != task['statuses'][table]:
            raise ValueError('R1 status changed while computing: ' + path)
    write_json(os.path.join(task['local'], 'audit.json'), rows)
    write_json(os.path.join(task['local'], 'quality.json'), quality)
    write_json(os.path.join(task['local'], 'spark_success.json'), {'site': site, 'rows': len(rows), 'spark_version': sc.version})
    for df in frames:
        df.unpersist()


def main():
    from pyspark import SparkContext
    task = read_json(sys.argv[1])
    sc = SparkContext(appName='R3-statistics-v0.1')
    try:
        if not sc.version.startswith('1.3.'):
            raise ValueError('Production runner requires Spark 1.3.x')
        sql = create_sql_context(sc, task.get('input_mode') == 'hive' and task.get('mode') != 'combine')
        configure_sql(sql)
        if task.get('mode') == 'combine':
            frames = [sql.parquetFile(task['output'] + '/sites/' + site + '/serving') for site in task['sites']]
            merged = frames[0]
            for df in frames[1:]:
                merged = merged.unionAll(df)
            expected = [tuple(r.get(k) for k in SERVING) for r in read_json(task['audit'])]
            if sorted(tuple(r) for r in merged.collect()) != sorted(expected):
                raise ValueError('Combined serving differs from validated audit')
            save_serving(sql, merged, task['output'] + '/site_metrics_monthly')
            write_json(task['receipt'], {'rows': len(expected), 'spark_version': sc.version})
        else:
            run_site(sc, sql, task)
    finally:
        sc.stop()


if __name__ == '__main__':
    main()
