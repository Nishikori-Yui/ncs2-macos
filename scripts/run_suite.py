#!/usr/bin/env python3
"""Generate IR, reload it, and run one bounded MYRIAD process per case."""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
LAB = ROOT / 'build-arm64/ncs2_lab'
RUNTIME = ROOT / 'vendor/openvino-2022.3/bin/arm64/Release'


def execute(command, timeout, env=None):
    started = time.monotonic()
    with subprocess.Popen([str(x) for x in command], stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, env=env, start_new_session=True) as child:
        timed_out = False
        try:
            output = child.communicate(timeout=timeout)[0]
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(child.pid, signal.SIGTERM)
            try:
                output = child.communicate(timeout=5)[0]
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                output = child.communicate()[0]
    return {'exit_code': child.returncode, 'timed_out': timed_out,
            'elapsed_s': time.monotonic()-started, 'output': output.decode(errors='replace')}


def parse_record(text, prefix):
    records = [line[len(prefix):] for line in text.splitlines() if line.startswith(prefix)]
    if len(records) != 1:
        raise ValueError(f'Expected exactly one {prefix} record, got {len(records)}.')
    return json.loads(records[0])


def validate_record(record, case, mode, iterations, warmup):
    """Never convert a missing/truncated/misattributed result into a passing case."""
    if not isinstance(record, dict) or record.get('schema_version') != 1 or record.get('case') != case['case']:
        raise ValueError('Invalid result schema or case identity.')
    if record.get('status') == 'failed':
        if not record.get('stage') or not isinstance(record.get('error'), str):
            raise ValueError('Failure record is missing stage/error.')
        return
    for key in ('input_shape', 'output_shape', 'input_dtype', 'output_dtype', 'atol', 'rtol'):
        if record.get(key) != case[key]:
            raise ValueError('Result differs from catalog: '+key)
    if mode == 'export':
        if record.get('status') != 'exported' or record.get('stage') != 'read_ir' or record.get('hardware_executed') is not False:
            raise ValueError('Invalid export result.')
        return
    if record.get('status') != 'passed' or record.get('stage') != 'validation' or record.get('hardware_executed') is not True or record.get('device') != 'MYRIAD':
        raise ValueError('Result does not prove validated MYRIAD execution.')
    if (record.get('iterations'), record.get('warmup')) != (iterations, warmup):
        raise ValueError('Result iteration counts differ from requested counts.')
    if record.get('checked_elements') != (iterations+warmup)*math.prod(case['output_shape']):
        raise ValueError('Result did not validate every requested output element.')
    for key in ('compile_and_boot_ms','mean_ms','p50_ms','p95_ms','min_ms','max_ms','max_abs_error','max_rel_error'):
        value=record.get(key)
        if not isinstance(value,(float,int)) or isinstance(value,bool) or not math.isfinite(value) or value<0:
            raise ValueError('Missing/invalid numeric result: '+key)
    if not record['min_ms'] <= record['p50_ms'] <= record['p95_ms'] <= record['max_ms']:
        raise ValueError('Timing percentiles are inconsistent.')


def write_report(directory, report):
    temporary = directory / 'report.json.part'
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False)+'\n')
    temporary.replace(directory / 'report.json')


def snapshot(env):
    result = execute([sys.executable, ROOT/'read_ncs2_usb.py'], 15, env)
    try:
        result['data'] = json.loads(result.pop('output'))
    except (json.JSONDecodeError, KeyError):
        pass
    return result


def hash_file(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', action='append', dest='cases', help='Repeat to select named cases; default all.')
    parser.add_argument('--iterations', type=int, default=20)
    parser.add_argument('--warmup', type=int, default=3)
    parser.add_argument('--timeout', type=float, default=60, help='Seconds per child, 1..300.')
    parser.add_argument('--export-only', action='store_true', help='IR round-trip only, no USB/compile/infer.')
    parser.add_argument('--output', type=Path, help='New results directory; existing path is never overwritten.')
    parser.add_argument('--test-mismatch', action='store_true', help='Intentionally fail first output check; negative test only.')
    args = parser.parse_args()
    if not 1 <= args.iterations <= 10000 or not 0 <= args.warmup <= 10000 or not 1 <= args.timeout <= 300:
        parser.error('iterations: 1..10000; warmup: 0..10000; timeout: 1..300')
    if args.export_only and args.test_mismatch:
        parser.error('--test-mismatch requires hardware execution')
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        parser.error('Native arm64 macOS is required.')
    if not LAB.is_file():
        parser.error('Missing ncs2_lab; run ./ncs2 build-probe first.')
    env = {**os.environ, 'DYLD_LIBRARY_PATH': str(RUNTIME)}
    catalog_run = execute([LAB, 'catalog'], 10, env)
    if catalog_run['exit_code'] != 0:
        raise RuntimeError('Catalog failed: '+catalog_run['output'])
    catalog = parse_record(catalog_run['output'], 'CATALOG_JSON=')
    by_id = {item['case']: item for item in catalog}
    names = args.cases or list(by_id)
    if len(names) != len(set(names)) or any(name not in by_id for name in names):
        parser.error('Unknown or duplicate cases; supported: '+', '.join(by_id))
    name = dt.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
    directory = (args.output or ROOT/'results'/('suite-'+name)).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': 1, 'created_at': dt.datetime.now().astimezone().isoformat(),
              'host': {'os': platform.system(), 'macos': platform.mac_ver()[0], 'architecture': platform.machine()},
              'toolchain_sha256': hash_file(ROOT/'toolchain.lock.json'),
              'binary_sha256': hash_file(LAB), 'mode': 'export' if args.export_only else 'run',
              'source_sha256': hash_file(ROOT/'ncs2_lab.cpp'),
              'model_source_sha256': hash_file(ROOT/'ncs2_models.hpp'),
              'iterations': args.iterations, 'warmup': args.warmup, 'timeout_s': args.timeout,
              'intentional_mismatch': args.test_mismatch, 'complete': False,
              'timing_scope': 'synchronous infer() only; excludes inputs/reference/validation; boot+compile separate',
              'cases': []}
    if not args.export_only:
        report['usb_before'] = snapshot(env)
    write_report(directory, report)
    print('RESULT_DIRECTORY='+str(directory), flush=True)
    failed = False
    for name in names:
        command = [LAB, RUNTIME/'plugins.xml', report['mode'], name, directory/'models',
                   args.iterations, args.warmup, int(args.test_mismatch)]
        result = execute(command, args.timeout, env)
        (directory/(name+'.log')).write_text(result['output'])
        try:
            record = parse_record(result['output'], 'CASE_JSON=')
            validate_record(record, by_id[name], report['mode'], args.iterations, args.warmup)
        except (ValueError, json.JSONDecodeError) as error:
            record = {'case': name, 'status': 'failed', 'stage': 'timeout' if result['timed_out'] else 'process',
                      'error': str(error)}
        record = {**by_id[name], **record, 'exit_code': result['exit_code'], 'elapsed_s': result['elapsed_s'],
                  'timed_out': result['timed_out']}
        expected = 'exported' if args.export_only else 'passed'
        if result['exit_code'] != 0 or result['timed_out'] or record.get('status') != expected:
            failed = True
            record['status'] = 'failed'
        record['artifacts'] = {}
        for extension in ('xml','bin'):
            artifact = directory/'models'/(name+'.'+extension)
            if artifact.exists():
                record['artifacts'][extension] = {'path': str(artifact.relative_to(directory)), 'sha256': hash_file(artifact)}
        if record['status'] != 'failed' and set(record['artifacts']) != {'xml', 'bin'}:
            record.update(status='failed', stage='artifacts', error='Missing serialized IR artifact.')
            failed = True
        report['cases'].append(record)
        write_report(directory, report)
        print('CASE_JSON='+json.dumps(record, ensure_ascii=False, allow_nan=False), flush=True)
    if not args.export_only:
        report['usb_after'] = snapshot(env)
    report['complete'] = True
    report['passed'] = not failed
    report['finished_at'] = dt.datetime.now().astimezone().isoformat()
    write_report(directory, report)
    print(f'SUITE_RESULT={"PASS" if not failed else "FAIL"}; cases={len(names)}; report={directory / "report.json"}', flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(f'suite: {error}', file=sys.stderr)
        sys.exit(1)
