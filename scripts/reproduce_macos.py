#!/usr/bin/env python3
"""Rebuild a source snapshot on physical arm64 macOS without reusing local caches."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time
import uuid

from export_source import export_source, check_output

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def execute_stage(command, cwd, log, timeout):
    """Keep raw output local; each child owns a group with a bounded kill/reap."""
    started = time.monotonic()
    timed_out = False
    with Path(log).open('xb') as stream:
        process = subprocess.Popen([str(value) for value in command], cwd=cwd,
                                   stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            code = 124
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
    return {'exit_code': code, 'timed_out': timed_out,
            'elapsed_seconds': round(time.monotonic() - started, 3), 'log_sha256': sha256(log)}


def sanitized_summary(report):
    """No hostname, username, absolute path, USB ID or raw error/output fields."""
    return {key: report[key] for key in (
        'schema_version', 'complete', 'passed', 'scope', 'host', 'source_inventory_sha256',
        'toolchain_sha256', 'stages', 'failed_stage', 'hardware_executed',
    ) if key in report}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='new local results directory; never overwritten')
    parser.add_argument('--timeout', type=int, default=3600, help='overall command budget, 60..7200 seconds')
    parser.add_argument('--summary-only', action='store_true', help='stdout contains only a sanitized JSON summary')
    args = parser.parse_args(argv)
    if not 60 <= args.timeout <= 7200:
        parser.error('--timeout must be in 60..7200 seconds')
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        parser.error('Native arm64 macOS is required')
    name = 'reproduce-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    output = (args.output or ROOT / 'results' / name).absolute()
    output.mkdir(parents=True, exist_ok=False)
    checkout = output / 'fresh source'
    deadline = time.monotonic() + args.timeout
    report = {
        'schema_version': 1, 'complete': False, 'passed': False,
        'scope': 'fresh-source-and-dependencies-on-current-host',
        'host': {'os': platform.system(), 'macos': platform.mac_ver()[0], 'architecture': platform.machine()},
        'hardware_executed': False, 'failed_stage': 'source-snapshot', 'stages': {},
        'toolchain_sha256': sha256(ROOT / 'toolchain.lock.json'),
    }

    def save():
        temporary = output / 'report.json.part'
        temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
        temporary.replace(output / 'report.json')

    save()
    try:
        manifest = export_source(ROOT, checkout)
        report['source_inventory_sha256'] = manifest['summary']['inventory_sha256']
        assert not any((checkout / name).exists() for name in ('vendor', 'build-arm64', 'firmware-cache'))
        stages = [
            ('environment', ['/bin/zsh', '-euc',
                '[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]]; '
                'command -v python3 cmake pkg-config clang++ git xcrun lipo otool; '
                'python3 --version; cmake --version; clang++ --version; '
                'pkg-config --modversion libusb-1.0; sw_vers']),
            ('fetch-online', [sys.executable, '-B', 'scripts/bootstrap.py']),
            ('fetch-offline', [sys.executable, '-B', 'scripts/bootstrap.py', '--offline']),
            ('build', ['/bin/zsh', 'scripts/build_runtime.sh']),
            ('architecture', ['/bin/zsh', 'scripts/check_arm64.sh']),
            ('ir-export', [sys.executable, '-B', 'scripts/run_suite.py', '--export-only', '--output', 'results/ir-export']),
            ('regression', [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-q']),
            ('source-export', [sys.executable, '-B', 'scripts/export_source.py', '--output', 'dist/verified-source']),
        ]
        for stage, command in stages:
            report['failed_stage'] = stage
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Overall reproduction deadline expired')
            with (output / 'commands.jsonl').open('a') as stream:
                stream.write(json.dumps({'stage': stage, 'argv': command}) + '\n')
            result = execute_stage(command, checkout, output / (stage + '.log'), remaining)
            report['stages'][stage] = result
            save()
            if result['exit_code'] != 0 or result['timed_out']:
                raise RuntimeError('Reproduction stage failed: ' + stage)
        # Verify artifacts rather than treating an exit code alone as completion.
        report['failed_stage'] = 'artifact-verification'
        ir = json.loads((checkout / 'results/ir-export/report.json').read_text())
        if not (ir.get('complete') is True and ir.get('passed') is True and ir.get('mode') == 'export'
                and len(ir.get('cases', [])) == 12 and all(case.get('status') == 'exported'
                    and case.get('hardware_executed') is False for case in ir['cases'])):
            raise RuntimeError('IR export evidence is incomplete')
        check_output(checkout / 'dist/verified-source')
        report['failed_stage'] = None
        report['passed'] = True
    except Exception as error:
        report['error_type'] = type(error).__name__
        # Error details stay in the local report, never the CI stdout summary.
        report['error'] = str(error)
    finally:
        report['complete'] = True
        save()
        print(json.dumps(sanitized_summary(report), indent=2, allow_nan=False))
        if not args.summary_only:
            print('REPRODUCTION_REPORT=' + str(output / 'report.json'))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
