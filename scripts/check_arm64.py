#!/usr/bin/env python3
"""Audit dependency paths recursively, including libusb and IR frontend."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / 'vendor/openvino-2022.3/bin/arm64/Release'
PROBE = ROOT / 'build-arm64/ncs2_openvino_probe'


def display_path(path):
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return 'external/' + path.name


def display_install_name(name, system):
    if system or not name.startswith('/'):
        return name
    return 'external/' + Path(name).name


def audit():
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise RuntimeError('Native arm64 macOS is required.')
    pending = [PROBE, ROOT / 'build-arm64/ncs2_lab', ROOT / 'build-arm64/ncs2_perf', ROOT / 'build-arm64/ncs2_worker', ROOT / 'build-arm64/ncs2_apps', ROOT / 'build-arm64/ncs2_multi_io', RUNTIME / 'libopenvino.dylib', RUNTIME / 'libopenvino_intel_myriad_plugin.so',
               RUNTIME / 'libopenvino_ir_frontend.dylib']
    seen = set()
    artifacts = []
    while pending:
        path = pending.pop().resolve()
        if path in seen:
            continue
        seen.add(path)
        if not path.is_file():
            raise RuntimeError(f'Missing dependency: {path}')
        arch = subprocess.check_output(['lipo', '-archs', str(path)], text=True).strip()
        if arch != 'arm64':
            raise RuntimeError(f'Expected a pure arm64 artifact: {path}: {arch}')
        dependencies = []
        lines = subprocess.check_output(['otool', '-L', str(path)], text=True).splitlines()[1:]
        for line in lines:
            dep = line.strip().split(' (compatibility version', 1)[0]
            if dep.startswith(('/usr/lib/', '/System/Library/')):
                dependencies.append({'install_name': display_install_name(dep, True), 'system': True})
                continue  # Apple libraries reside in the macOS dyld shared cache.
            if dep.startswith('@rpath/'):
                candidate = RUNTIME / dep.removeprefix('@rpath/')
            elif dep.startswith('@loader_path/'):
                candidate = path.parent / dep.removeprefix('@loader_path/')
            elif dep.startswith('@executable_path/'):
                candidate = PROBE.parent / dep.removeprefix('@executable_path/')
            elif dep.startswith('/'):
                candidate = Path(dep)
            else:
                raise RuntimeError(f'Unresolved dependency: {dep}')
            if candidate.resolve() == path:
                continue  # otool -L also prints a dylib's own LC_ID_DYLIB.
            dependencies.append({'install_name': display_install_name(dep, False), 'system': False,
                                 'resolved_path': display_path(candidate.resolve())})
            pending.append(candidate)
        artifacts.append({
            'path': display_path(path),
            'architectures': [arch],
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'dependencies': dependencies,
        })
    artifacts.sort(key=lambda item: item['path'])
    return {
        'schema_version': 1,
        'host': {'os': platform.system(), 'architecture': platform.machine()},
        'status': 'passed',
        'non_system_macho_count': len(artifacts),
        'artifacts': artifacts,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description='Recursively audit native macOS dependency architectures.')
    parser.add_argument('--json', action='store_true', help='print the machine-readable audit manifest')
    args = parser.parse_args(argv)
    report = audit()
    if args.json:
        print(json.dumps(report, sort_keys=True, separators=(',', ':')))
    else:
        for item in report['artifacts']:
            print(f"PASS arm64 {item['path']}")
        print(f"Architecture check passed: {report['non_system_macho_count']} non-system Mach-O files; system shared-cache libraries excluded.")


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'architecture: {error}', file=sys.stderr)
        sys.exit(1)
