# -*- coding: utf-8 -*-
"""Course VM entry point: read-only preflight; unique output per run."""
from __future__ import print_function
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
from common import (ROOT, TABLES, load_config, load_scope, capture, check_paths, check_status, digest,
                    read_json, validate_monthly, write_json, write_text)
from report import generate


def resolve_paths(args):
    args.input_mode = getattr(args, 'input_mode', 'hive')
    args.hive_database = getattr(args, 'hive_database', 'qa_decline')
    args.hive_conf_dir = getattr(args, 'hive_conf_dir', os.environ.get('HIVE_CONF_DIR', '/etc/hive/conf'))
    if args.input_mode not in ('parquet', 'hive'):
        raise ValueError('Invalid input mode')
    if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', args.hive_database):
        raise ValueError('Invalid Hive database identifier')
    args.run_id = args.run_id or datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    if not re.match(r'^[A-Za-z0-9][A-Za-z0-9_-]*$', args.run_id):
        raise ValueError('Invalid --run-id; use letters, numbers, hyphens or underscores')
    args.output_root = args.output_root or args.output_base.rstrip('/') + '/' + args.run_id
    args.local_output = os.path.abspath(args.local_output or os.path.join(args.work_root, args.run_id))
    args.log_dir = os.path.abspath(os.path.join(args.log_root, args.run_id))
    local, logs = os.path.normcase(args.local_output), os.path.normcase(args.log_dir)
    if local == logs or local.startswith(logs + os.sep) or logs.startswith(local + os.sep):
        raise ValueError('Local result and log directories must be separate trees')


def existing_parent(path):
    parent = os.path.dirname(os.path.abspath(path))
    while not os.path.exists(parent):
        parent = os.path.dirname(parent)
    return parent


def make_parent(path):
    parent = os.path.dirname(path)
    if not os.path.isdir(parent):
        os.makedirs(parent)


def exists_hdfs(path):
    p = subprocess.Popen(['hdfs', 'dfs', '-test', '-e', path], stderr=subprocess.PIPE)
    err = p.communicate()[1]
    if p.returncode not in (0, 1) or err.strip():
        raise RuntimeError('HDFS existence check failed: ' + err.decode('utf-8', 'replace'))
    return p.returncode == 0


def status_snapshot(source, sites, input_mode='parquet'):
    statuses, hashes = {}, {}
    expected_files, roots = set(), []
    for site in sorted(sites):
        statuses[site] = {}
        for table in TABLES:
            path = '%s/_status/%s.%s.json' % (source, site, table)
            text = capture(['hdfs', 'dfs', '-cat', path])
            statuses[site][table] = check_status(json.loads(text), site, table)
            hashes[site + '.' + table] = hashlib.sha256(text.encode('utf-8')).hexdigest()
            if statuses[site][table]['years']:
                roots.append('%s/%s/site=%s' % (source, table, site))
            for year in statuses[site][table]['years']:
                parquet = '%s/%s/site=%s/year=%d/part-%d.parquet' % (source, table, site, year, year)
                expected_files.add(parquet)
    if roots:
        # One JVM invocation for all selected trees, rather than one per year/file.
        listing = capture(['hdfs', 'dfs', '-ls', '-R'] + roots)
        actual_files = set(line.split()[-1] for line in listing.splitlines() if line.startswith('-'))
        if input_mode == 'hive':
            # R2 registers year directories, allowing arbitrary Parquet part names.
            expected_years = set(path.rsplit('/', 1)[0] for path in expected_files)
            actual_years = set(path.rsplit('/', 1)[0] for path in actual_files
                               if path.endswith('.parquet'))
            missing = expected_years - actual_years
        else:
            # Direct R1 reads still use the original annual filename contract.
            missing = expected_files - actual_files
        if missing:
            raise ValueError('Missing R1 Parquet: ' + ', '.join(sorted(missing)))
    return statuses, hashes


def implementation_hashes():
    result = {}
    for name in sorted(os.listdir(ROOT)):
        if name.endswith(('.py', '.sql', '.json', '.sh', '.txt')) and name != 'SHA256SUMS.txt':
            result[name] = digest(os.path.join(ROOT, name))
    for base, dirs, files in os.walk(os.path.join(ROOT, 'scopes')):
        for name in sorted(files):
            if name.endswith(('.json', '.txt')):
                path = os.path.join(base, name)
                result[os.path.relpath(path, ROOT).replace(os.sep, '/')] = digest(path)
    result['spark-conf/spark-env.sh'] = digest(os.path.join(ROOT, 'spark-conf', 'spark-env.sh'))
    for name in sorted(os.listdir(os.path.join(os.path.dirname(ROOT), 'sql'))):
        if name.endswith('.sql'):
            result['sql/' + name] = digest(os.path.join(os.path.dirname(ROOT), 'sql', name))
    return result


def spark_version_banner():
    p = subprocess.Popen(['spark-submit', '--version'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    version = p.communicate()[0].decode('utf-8', 'replace')
    # Older launchers can exit nonzero after printing their version/help banner.
    # The job independently checks sc.version once a real SparkContext starts.
    matches = re.findall(r'\bversion\s+(\d+\.\d+\.\d+)(?=[^0-9.]|$)', version)
    if p.returncode < 0 or not matches or any(not v.startswith('1.3.') for v in matches):
        raise ValueError('Expected Spark 1.3.x (exit %s); observed: %s' % (p.returncode, version))
    if p.returncode:
        print('Spark 1.3.x banner detected; --version exit %s accepted. Runtime version will be checked in Spark.' % p.returncode)
    return version


def preflight(args, sites):
    if sys.version_info[:2] != (2, 7):
        raise ValueError('VM runner requires Python 2.7; set R3_PYTHON=/usr/local/bin/python2.7')
    check_paths(args.input_root, args.output_root)
    if not re.match(r'^[1-9][0-9]*[mg]$', args.driver_memory):
        raise ValueError('Invalid --driver-memory (example: 768m)')
    for name in ('spark-submit', 'hdfs', 'java'):
        capture(['which', name])
    version = spark_version_banner()
    local = os.path.abspath(args.local_output)
    parent = existing_parent(local)
    if os.path.exists(local):
        raise ValueError('Local output exists; choose a new run name: ' + local)
    if not os.path.isdir(parent) or not os.access(parent, os.W_OK):
        raise ValueError('Create writable local output parent first: ' + parent)
    if os.path.exists(args.log_dir) or not os.access(existing_parent(args.log_dir), os.W_OK):
        raise ValueError('Log directory exists or parent is not writable: ' + args.log_dir)
    disk = os.statvfs(parent)
    if disk.f_bavail * disk.f_frsize < 2 * 1024 ** 3:
        raise ValueError('Need at least 2 GiB free for initial trial; monitor actual Spark scratch usage')
    capture(['hdfs', 'dfs', '-ls', '/'])
    if exists_hdfs(args.output_root):
        raise ValueError('HDFS output exists; choose a new run name: ' + args.output_root)
    statuses, hashes = status_snapshot(args.input_root, sites, args.input_mode)
    if args.input_mode == 'hive':
        if not os.path.isfile(os.path.join(args.hive_conf_dir, 'hive-site.xml')):
            raise ValueError('Missing hive-site.xml in --hive-conf-dir: ' + args.hive_conf_dir)
        print('Hive table/schema/partition checks run in Spark; preflight does not certify the metastore connection.')
    print('PREFLIGHT PASS: %d sites, %d R1 statuses. Row/schema checks run in Spark.' % (len(sites), len(hashes)))
    return statuses, hashes, version


def spark(task, local, args):
    task_path = os.path.join(local, 'task.json')
    write_json(task_path, task)
    env = os.environ.copy()
    env.update(PYSPARK_PYTHON=sys.executable, PYSPARK_DRIVER_PYTHON=sys.executable, TZ='UTC')
    scratch = os.path.join(local, 'spark-tmp')
    os.mkdir(scratch)
    hive_options = (['--driver-class-path', args.hive_conf_dir]
                    if task.get('input_mode') == 'hive' and task.get('mode') != 'combine' else [])
    command = ['spark-submit'] + hive_options + ['--master', 'local[1]', '--driver-memory', args.driver_memory,
               '--conf', 'spark.local.dir=' + scratch, '--conf', 'spark.ui.enabled=false',
               '--conf', 'spark.driver.extraJavaOptions=-Duser.timezone=UTC',
               os.path.join(ROOT, 'spark_job.py'), task_path]
    log_path = task['log_path']
    with open(log_path, 'wb') as log:
        p = subprocess.Popen(command, cwd=local, env=env, stdout=log, stderr=subprocess.STDOUT)
        if p.wait():
            raise RuntimeError('Spark job failed; inspect ' + log_path)


def run(args):
    resolve_paths(args)
    config = load_config(getattr(args, 'scope', 'category'))
    frozen = load_scope(config)
    if (config['coverage_end_exclusive'], config['answer_window_days'], config['r1_parser_version'], config['r1_dump_date']) != (
            '2024-03-01T00:00:00Z', 7, 'v0.3', '2024-04-02'):
        raise ValueError('Metric contract is fixed; changing it requires a new package version')
    selected = args.site or sorted(frozen)
    if len(set(selected)) != len(selected) or any(s not in frozen for s in selected):
        raise ValueError('--site must name a unique site from selected_sites.json; meta is excluded')
    sites = dict((s, frozen[s]) for s in sorted(selected))
    statuses, hashes, version = preflight(args, sites)
    if args.preflight or args.dry_run:
        for site in sites:
            print('WOULD RUN %s (%s)' % (site, sites[site]))
        print('Output: %s and %s' % (args.output_root, os.path.abspath(args.local_output)))
        print('Logs: %s; run ID: %s' % (args.log_dir, args.run_id))
        return
    local = os.path.abspath(args.local_output)
    make_parent(local)
    make_parent(args.log_dir)
    os.mkdir(local)
    os.mkdir(args.log_dir)
    os.mkdir(os.path.join(local, 'sites'))
    code_hashes = implementation_hashes()
    write_json(os.path.join(local, 'run_plan.json'), {'sites': sites, 'r1_status_sha256': hashes,
               'input_mode': args.input_mode, 'hive_database': args.hive_database, 'hive_conf_dir': args.hive_conf_dir,
               'input_root': args.input_root, 'output_root': args.output_root,
               'run_id': args.run_id, 'log_dir': args.log_dir,
               'implementation_sha256': code_hashes, 'config': config, 'spark_version_output': version})
    summary, rows = [], []
    try:
        # Only parent creation is idempotent; the run namespace is reserved atomically.
        capture(['hdfs', 'dfs', '-mkdir', '-p', args.output_root.rsplit('/', 1)[0]])
        capture(['hdfs', 'dfs', '-mkdir', args.output_root])
        for site, group in sorted(sites.items()):
            print('RUN %s' % site)
            sys.stdout.flush()
            dest = os.path.join(local, 'sites', site)
            os.mkdir(dest)
            task = {'input_mode': args.input_mode, 'hive_database': args.hive_database, 'site': site, 'group': group, 'statuses': statuses[site], 'input': args.input_root,
                    'output': args.output_root + '/sites/' + site, 'local': dest,
                    'log_path': os.path.join(args.log_dir, site + '.log'),
                    'coverage_end_exclusive': config['coverage_end_exclusive']}
            spark(task, dest, args)
            receipt = read_json(os.path.join(dest, 'spark_success.json'))
            if receipt['site'] != site or receipt['rows'] != 63:
                raise ValueError('Wrong Spark receipt')
            actual = validate_monthly(read_json(os.path.join(dest, 'audit.json')), {site: group})
            rows.extend(actual)
            summary.append(site + '\tPASS\t63')
            write_text(os.path.join(local, 'summary.tsv'), 'site\tstatus\trows\n' + '\n'.join(summary) + '\n')
            write_text(os.path.join(args.log_dir, 'summary.tsv'), 'site\tstatus\trows\n' + '\n'.join(summary) + '\n')
        full = sites == frozen
        generate(rows, sites, local, full)
        combine = os.path.join(local, 'combine')
        os.mkdir(combine)
        spark({'mode': 'combine', 'sites': sorted(sites), 'output': args.output_root,
               'log_path': os.path.join(args.log_dir, 'combine.log'),
               'audit': os.path.join(local, 'monthly_metrics_audit.json'),
               'receipt': os.path.join(combine, 'spark_success.json')}, combine, args)
        if read_json(os.path.join(combine, 'spark_success.json'))['rows'] != 63 * len(sites):
            raise ValueError('Missing combined output receipt')
        if status_snapshot(args.input_root, sites, args.input_mode)[1] != hashes:
            raise ValueError('R1 status changed during run; result is not publishable')
        if implementation_hashes() != code_hashes:
            raise ValueError('Implementation changed during run')
        outputs = {}
        for base, dirs, files in os.walk(local):
            dirs[:] = [d for d in dirs if d != 'spark-tmp']
            for name in files:
                if name.endswith(('.json', '.csv', '.svg', '.md', '.tsv')):
                    path = os.path.join(base, name)
                    outputs[os.path.relpath(path, local).replace(os.sep, '/')] = digest(path)
        receipt = {'complete': True, 'full_scope': full, 'full_pilot': False,
                   'scope_id': config['scope_id'], 'target_sites': len(frozen),
                   'expected_rows': 63 * len(frozen), 'selection_sha256': config['selection_sha256'], 'sites': sites, 'rows': len(rows),
                   'run_id': args.run_id, 'log_dir': args.log_dir, 'local_output': local,
                   'input_mode': args.input_mode, 'hive_database': args.hive_database, 'hive_conf_dir': args.hive_conf_dir,
                   'input_root': args.input_root, 'hdfs_serving': args.output_root + '/site_metrics_monthly',
                   'finished_utc': datetime.datetime.utcnow().isoformat() + 'Z',
                   'r1_status_sha256': hashes, 'implementation_sha256': code_hashes,
                   'output_sha256': outputs, 'config': config, 'python_version': sys.version}
        candidate = os.path.join(local, 'completion_candidate.json')
        write_json(candidate, receipt)
        capture(['hdfs', 'dfs', '-put', candidate, args.output_root + '/_R3_SUCCESS.json'])
        os.rename(candidate, os.path.join(local, 'validated.json'))
        print('OVERALL PASS (%s): %d rows. %s' % (('FULL SCOPE %d' % len(frozen)) if full else 'SUBSET ONLY', len(rows), local))
    except Exception as exc:
        write_json(os.path.join(local, 'failure.json'), {'complete': False, 'error': str(exc), 'passed_sites': summary})
        write_json(os.path.join(args.log_dir, 'failure.json'), {'complete': False, 'error': str(exc), 'passed_sites': summary})
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scope', default='category', help='Scope profile under load/scopes/: category (default), size-matched, legacy130')
    p.add_argument('--input-mode', choices=('parquet', 'hive'), default='hive')
    p.add_argument('--hive-database', default='qa_decline')
    p.add_argument('--hive-conf-dir', default=os.environ.get('HIVE_CONF_DIR', '/etc/hive/conf'),
                   help='Directory containing the R2 metastore hive-site.xml; added to Hive driver classpath')
    p.add_argument('--input-root', default='/project/parquet')
    p.add_argument('--output-base', default='/project/statistics', help='HDFS results parent; run ID is appended')
    p.add_argument('--run-id', help='Default: UTC timestamp, matching R1 batch_logs naming')
    p.add_argument('--work-root', default=os.path.join(os.path.dirname(ROOT), 'results'), help='Default: <r3_metrics>/results; local work/results parent')
    p.add_argument('--log-root', default=os.path.join(os.path.dirname(ROOT), 'batch_logs'), help='Default: <r3_metrics>/batch_logs')
    p.add_argument('--output-root', help='Override exact HDFS output; default /project/statistics/<run-id>')
    p.add_argument('--local-output', help='Override exact local output; default <work-root>/<run-id>')
    p.add_argument('--driver-memory', default='768m')
    p.add_argument('--site', action='append', help='Optional frozen main site; omit for all sites in the chosen scope')
    p.add_argument('--preflight', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    try:
        run(args)
    except Exception as exc:
        print('OVERALL FAIL: %s' % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
