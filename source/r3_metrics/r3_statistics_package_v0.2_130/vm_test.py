# -*- coding: utf-8 -*-
"""Run the course VM experiment and report its verification results."""
from __future__ import print_function, unicode_literals
import argparse
from collections import Counter
from datetime import datetime
import os
import re
import subprocess
import sys
import time
import traceback

from common import AUDIT, ROOT, check_paths, digest, read_json, validate_monthly, write_json, write_text
from run import exists_hdfs


RATIO_FIELDS = ('answered_rate', 'downvote_ratio')
TOLERANCE = 1e-12
TRIAL_SITE = 'cs.stackexchange.com'


def timestamp():
    return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


def checked_rows(rows, sites):
    for row in rows:
        if set(row) != set(AUDIT):
            raise ValueError('Expected the full 14-column audit schema at %r/%r' %
                             (row.get('site'), row.get('month')))
    return validate_monthly(rows, sites)


def compare(actual, expected, sites):
    actual = checked_rows(actual, sites)
    expected = checked_rows(expected, sites)
    differences = []
    max_delta = dict((field, 0.0) for field in RATIO_FIELDS)
    for row, reference in zip(actual, expected):
        for field in AUDIT:
            got, want = row[field], reference[field]
            equal = got == want
            if field in RATIO_FIELDS and got is not None and want is not None:
                delta = abs(got - want)
                max_delta[field] = max(max_delta[field], delta)
                equal = delta <= TOLERANCE
            if not equal:
                differences.append(dict(site=row['site'], month=row['month'], column=field,
                                        expected=want, actual=got))
    return {'match': not differences, 'rows_compared': len(actual), 'columns': list(AUDIT),
            'cells_compared': len(actual) * len(AUDIT), 'ratio_absolute_tolerance': TOLERANCE,
            'max_ratio_delta': max_delta, 'mismatched_cells': len(differences),
            'mismatches_by_column': dict(Counter(d['column'] for d in differences)),
            'differences': differences}


def read_run(root, name, sites, input_mode, full):
    folder = os.path.join(root, name)
    receipt = read_json(os.path.join(folder, 'validated.json'))
    if (receipt.get('complete') is not True or receipt.get('full_scope') is not full
            or receipt.get('rows') != 63 * len(sites) or receipt.get('sites') != sites
            or receipt.get('input_mode') != input_mode):
        raise ValueError('Unexpected R3 completion receipt: ' + name)
    filename = 'monthly_metrics_audit.json'
    path = os.path.join(folder, filename)
    if digest(path) != receipt['output_sha256'].get(filename):
        raise ValueError('Audit JSON hash differs from completion receipt: ' + name)
    return checked_rows(read_json(path), sites), receipt


def log_command(stage, command, cwd, log):
    stage.setdefault('commands', []).append(command)
    log.flush()
    code = subprocess.call(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
    log.flush()
    stage.setdefault('exit_codes', []).append(code)
    if code:
        raise RuntimeError('Command exited with status %s. See %s.' % (code, stage['log']))


def save_report(root, report):
    summary_path = os.path.join(root, 'experiment.json')
    write_json(summary_path, report)
    lines = ['# R3 수업 VM 검증 보고서', '',
             '- 판정 **%s**' % report['status'],
             '- 실행 ID `%s`' % report['run_id'],
             '- UTC 시작 `%s`' % report['started_utc'],
             '- UTC 종료 `%s`' % (report.get('finished_utc') or '실행 중'),
             '- HDFS 입력 `%s`' % report['input_root'],
             '- HDFS 출력 `%s`' % report['hdfs_run'],
             '- Hive 1사이트 검사 %s' % ('요청됨' if report['check_hive'] else '미실행'), '',
             '| 단계 | 상태 | 소요 시간 | 로그 |', '|---|---|---:|---|']
    for stage in report['stages']:
        elapsed = '%.1f초' % stage['seconds'] if 'seconds' in stage else '-'
        lines.append('| %s | %s | %s | [%s](%s) |' %
                     (stage['name'], stage['status'], elapsed, stage['log'], stage['log']))
    if report.get('reference_sha256'):
        lines.extend(['', '로컬 기준 파일 SHA256 `%s`' % report['reference_sha256']])
    for filename in ('comparison.json', 'hive_comparison.json'):
        if os.path.isfile(os.path.join(root, filename)):
            result = read_json(os.path.join(root, filename))
            lines.extend(['', '### %s' % filename, '',
                          '- %d행 × %d열 대조, 불일치 %d셀' %
                          (result['rows_compared'], len(result['columns']), result['mismatched_cells']),
                          '- 비율 허용 절대 오차 `1e-12`, NULL 위치 및 그 외 값 정확 일치',
                          '- [열별 요약과 사이트·월별 차이](%s)' % filename])
    lines.extend(['', '사이트별 Spark 및 통합 로그는 `spark_logs/`에 있습니다.',
                  '시험·전체 실행의 R1 상태와 결과 해시는 각 실행의 `validated.json`에 있습니다.',
                  '값 대조는 지정한 기준 결과와의 일치 여부를 확인합니다. 원본 덤프 동일성은 파일 해시가 있어야 확인할 수 있습니다.'])
    if os.path.isfile(os.path.join(root, 'full', 'validated.json')):
        lines.extend(['', '[130사이트 통계 보고서](full/REPORT_KO.md)'])
    if report.get('failed_stage'):
        lines.extend(['', '실패 단계 `%s`. 위 단계 로그 끝의 오류와 해당 Spark 로그를 확인하세요.' %
                      report['failed_stage']])
    write_text(os.path.join(root, 'VM_TEST_REPORT_KO.md'), '\n'.join(lines) + '\n')


def experiment(args, root, hdfs_run):
    names = ['integrity', 'smoke', 'parquet-trial', 'parquet-full', 'local-comparison']
    if args.check_hive:
        names.append('hive-trial')
    report = {'run_id': args.run_id, 'status': 'RUNNING', 'started_utc': timestamp(),
              'input_root': args.input_root, 'hdfs_run': hdfs_run, 'check_hive': args.check_hive,
              'hive_database': args.hive_database, 'python': sys.version, 'driver_memory': args.driver_memory,
              'stages': [dict(name=name, log=name + '.log', status='PENDING') for name in names]}
    state = {}

    def integrity(stage, log):
        log_command(stage, ['sha256sum', '-c', 'SHA256SUMS.txt'], ROOT, log)
        if sys.version_info[:2] != (2, 7):
            raise ValueError('Expected Python 2.7. Set R3_PYTHON to the interpreter used by R1.')
        from common import load_scope
        sites = load_scope()
        if len(sites) != 130 or TRIAL_SITE not in sites:
            raise ValueError('Expected the approved 130-site scope')
        state['sites'] = sites
        state['trial_sites'] = {TRIAL_SITE: sites[TRIAL_SITE]}
        baseline = os.path.join(ROOT, 'scope_reference_audit.json')
        state['reference'] = checked_rows(read_json(baseline), sites)
        report['reference_sha256'] = digest(baseline)
        if exists_hdfs(hdfs_run):
            raise ValueError('HDFS run already exists. Choose a new --run-id: ' + hdfs_run)
        log_command(stage, ['hdfs', 'dfs', '-mkdir', '-p', hdfs_run.rsplit('/', 1)[0]], root, log)
        log_command(stage, ['hdfs', 'dfs', '-mkdir', hdfs_run], root, log)

    def smoke(stage, log):
        log_command(stage, ['bash', os.path.join(ROOT, 'smoke_test.sh'), '--output', hdfs_run + '/smoke'],
                    root, log)
        with open(os.path.join(root, stage['log']), 'rb') as smoke_log:
            if not any(b'SMOKE PASS:' in line for line in smoke_log):
                raise ValueError('Missing SMOKE PASS marker')

    def run_r3(stage, log, name, sites, mode, full=False):
        command = ['bash', os.path.join(ROOT, 'run_r3.sh'), '--input-mode', mode,
                   '--hive-database', args.hive_database, '--input-root', args.input_root,
                   '--output-root', hdfs_run + '/' + name, '--run-id', name,
                   '--local-output', os.path.join(root, name),
                   '--log-root', os.path.join(root, 'spark_logs'), '--driver-memory', args.driver_memory]
        if not full:
            command.extend(['--site', TRIAL_SITE])
        log_command(stage, command, root, log)
        rows, receipt = read_run(root, name, sites, mode, full)
        stage['rows'] = receipt['rows']
        return rows, receipt['r1_status_sha256']

    def parquet_trial(stage, log):
        _, state['trial_hashes'] = run_r3(stage, log, 'trial', state['trial_sites'], 'parquet')

    def parquet_full(stage, log):
        state['full_rows'], hashes = run_r3(stage, log, 'full', state['sites'], 'parquet', full=True)
        if any(hashes.get(key) != value for key, value in state['trial_hashes'].items()):
            raise ValueError('R1 status changed between the trial and full runs')

    def verify(stage, log, result, filename):
        write_json(os.path.join(root, filename), result)
        stage['comparison'] = filename
        log.write(('Compared %d rows, %d columns. Mismatched cells: %d\n' %
                   (result['rows_compared'], len(result['columns']), result['mismatched_cells'])).encode('ascii'))
        if not result['match']:
            raise ValueError('Audit comparison failed. See ' + filename)

    def local_comparison(stage, log):
        result = compare(state['full_rows'], state['reference'], state['sites'])
        verify(stage, log, result, 'comparison.json')

    def hive_trial(stage, log):
        rows, hashes = run_r3(stage, log, 'hive', state['trial_sites'], 'hive')
        if hashes != state['trial_hashes']:
            raise ValueError('R1 status changed between the Parquet and Hive runs')
        expected = [row for row in state['full_rows'] if row['site'] == TRIAL_SITE]
        verify(stage, log, compare(rows, expected, state['trial_sites']), 'hive_comparison.json')

    actions = [integrity, smoke, parquet_trial, parquet_full, local_comparison, hive_trial]
    for stage, action in zip(report['stages'], actions):
        print('RUN ' + stage['name'] + ' -> ' + stage['log'])
        stage.update(status='RUNNING', started_utc=timestamp())
        save_report(root, report)
        start = time.time()
        with open(os.path.join(root, stage['log']), 'wb') as log:
            try:
                action(stage, log)
                stage['status'] = 'PASS'
            except (Exception, KeyboardInterrupt):
                log.write(traceback.format_exc().encode('utf-8', 'replace'))
                stage['status'] = 'FAIL'
                report['failed_stage'] = stage['name']
            stage.update(seconds=round(time.time() - start, 3), finished_utc=timestamp())
        print(stage['status'] + ' ' + stage['name'])
        if stage['status'] == 'FAIL':
            break
    for stage in report['stages']:
        if stage['status'] == 'PENDING':
            stage['status'] = 'SKIPPED'
    report.update(status='FAIL' if report.get('failed_stage') else 'PASS', finished_utc=timestamp())
    save_report(root, report)
    return report['status'] == 'PASS'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', default='vm-test-' + datetime.utcnow().strftime('%Y%m%dT%H%M%SZ'))
    parser.add_argument('--input-root', default='/project/parquet')
    parser.add_argument('--output-base', default='/project/statistics')
    parser.add_argument('--work-root', default=os.path.join(ROOT, 'results', 'vm-tests'))
    parser.add_argument('--driver-memory', default='768m', help='Driver memory for actual R3 runs')
    parser.add_argument('--check-hive', action='store_true', help='Also verify one site through R2 Hive')
    parser.add_argument('--hive-database', default='qa_decline')
    args = parser.parse_args()
    if not re.match(r'^[A-Za-z0-9][A-Za-z0-9_-]*\Z', args.run_id):
        parser.error('Invalid --run-id')
    hdfs_run = args.output_base.rstrip('/') + '/' + args.run_id
    root = os.path.abspath(os.path.join(args.work_root, args.run_id))
    try:
        check_paths(args.input_root, hdfs_run)
        os.makedirs(root)
    except (IOError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print('Experiment directory: ' + root)
    passed = experiment(args, root, hdfs_run)
    print(('TEST PASS' if passed else 'TEST FAIL') + ' -> ' + os.path.join(root, 'VM_TEST_REPORT_KO.md'))
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
