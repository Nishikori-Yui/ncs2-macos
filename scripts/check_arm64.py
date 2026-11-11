#!/usr/bin/env python3
"""Audit dependency paths recursively, including libusb and IR frontend."""
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / 'vendor/openvino-2022.3/bin/arm64/Release'
PROBE = ROOT / 'build-arm64/ncs2_openvino_probe'


def main():
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise RuntimeError('Native arm64 macOS is required.')
    pending = [PROBE, ROOT / 'build-arm64/ncs2_lab', ROOT / 'build-arm64/ncs2_perf', ROOT / 'build-arm64/ncs2_worker', ROOT / 'build-arm64/ncs2_apps', RUNTIME / 'libopenvino.dylib', RUNTIME / 'libopenvino_intel_myriad_plugin.so',
               RUNTIME / 'libopenvino_ir_frontend.dylib']
    seen = set()
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
        print(f'PASS arm64 {path}')
        lines = subprocess.check_output(['otool', '-L', str(path)], text=True).splitlines()[1:]
        for line in lines:
            dep = line.strip().split(' (compatibility version', 1)[0]
            if dep.startswith(('/usr/lib/', '/System/Library/')):
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
            pending.append(candidate)
    print(f'Architecture check passed: {len(seen)} non-system Mach-O files; system shared-cache libraries excluded.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'architecture: {error}', file=sys.stderr)
        sys.exit(1)
