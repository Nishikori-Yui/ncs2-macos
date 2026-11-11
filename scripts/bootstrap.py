#!/usr/bin/env python3
"""Fetch/check pinned inputs. No system installation, reset, or USB access."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import shlex
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def run(*args, capture=False):
    print('+ ' + shlex.join(str(arg) for arg in args), flush=True)
    return subprocess.run(args, check=True, text=True, timeout=300,
                          stdout=subprocess.PIPE if capture else None).stdout


def fetch_revision(source, ov):
    """A failed first transfer leaves only .git; permit a bounded safe retry."""
    for attempt in range(1, 4):
        try:
            run('git', '-C', str(source), '-c', 'http.version=HTTP/1.1',
                'fetch', '--depth', '1', 'origin', ov['commit'])
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if attempt == 3:
                raise
            print(f'Pinned Git transfer failed (attempt {attempt}/3); retrying.', flush=True)
            time.sleep(1)


def prepare_source(source, ov, offline):
    if not (source / '.git').exists():
        if offline:
            raise RuntimeError('Pinned source is missing; run ./ncs2 fetch online first.')
        if source.exists():
            raise RuntimeError(f'Refusing to overwrite existing non-Git path: {source}')
        source.parent.mkdir(parents=True, exist_ok=True)
        run('git', 'init', str(source))
        run('git', '-C', str(source), 'remote', 'add', 'origin', ov['url'])
    head = subprocess.run(['git', '-C', str(source), 'rev-parse', '--verify', 'HEAD'],
                          capture_output=True, text=True, timeout=15)
    if head.returncode != 0:
        # Only a completely empty work tree with the expected origin can resume.
        if offline:
            raise RuntimeError('Pinned source checkout is incomplete; online fetch required.')
        if any(path.name != '.git' for path in source.iterdir()):
            raise RuntimeError('Refusing incomplete checkout with existing work-tree files.')
        origin = run('git', '-C', str(source), 'remote', 'get-url', 'origin', capture=True).strip()
        if origin != ov['url']:
            raise RuntimeError('Incomplete checkout origin differs from lockfile.')
        fetch_revision(source, ov)
        run('git', '-C', str(source), 'checkout', '--detach', ov['commit'])


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true', help='Validate existing files only; never fetch or extract.')
    args = parser.parse_args()
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise RuntimeError('This toolchain requires native arm64 macOS.')
    lock = json.loads((ROOT / 'toolchain.lock.json').read_text())
    ov = lock['openvino']
    source = ROOT / ov['directory']
    prepare_source(source, ov, args.offline)
    actual = run('git', '-C', str(source), 'rev-parse', 'HEAD', capture=True).strip()
    if actual != ov['commit']:
        raise RuntimeError(f'Source revision mismatch: {actual}; existing checkout was not modified.')
    changed = run('git', '-C', str(source), 'status', '--porcelain', '--untracked-files=no', capture=True).strip()
    if changed:
        raise RuntimeError('Pinned checkout has tracked modifications; preserve/review them before building: ' + changed)
    if not args.offline:
        run('git', '-C', str(source), 'submodule', 'update', '--init', '--depth', '1', *ov['submodules'])
    for path, commit in ov['submodules'].items():
        actual = run('git', '-C', str(source / path), 'rev-parse', 'HEAD', capture=True).strip()
        if actual != commit:
            raise RuntimeError(f'Submodule mismatch: {path}: {actual}')
    cache = ROOT / 'firmware-cache'
    if not args.offline:
        cache.mkdir(exist_ok=True)
    for item in lock['firmware']:
        archive, output = cache / item['archive'], cache / item['output']
        if not archive.exists():
            if args.offline:
                raise RuntimeError(f'Missing archive: {archive}')
            partial = archive.with_suffix('.zip.part')
            run('/usr/bin/curl', '--fail', '--location', '--proto', '=https', '--proto-redir', '=https',
                '--retry', '2', '--connect-timeout', '30', '--max-time', '300', '--output', str(partial), item['url'])
            if sha256(partial) != item['sha256']:
                raise RuntimeError(f'Archive checksum mismatch: {partial}; not extracted.')
            partial.replace(archive)
        if sha256(archive) != item['sha256']:
            raise RuntimeError(f'Archive checksum mismatch: {archive}; not extracted.')
        if not output.exists():
            if args.offline:
                raise RuntimeError(f'Missing firmware: {output}')
            with zipfile.ZipFile(archive) as zipped:
                members = [name for name in zipped.namelist() if Path(name).name == item['member']]
                if len(members) != 1:
                    raise RuntimeError(f'Ambiguous firmware member in {archive}')
                data = zipped.read(members[0])
            if hashlib.sha256(data).hexdigest() != item['output_sha256']:
                raise RuntimeError(f'Firmware checksum mismatch in {archive}')
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(data)
        if sha256(output) != item['output_sha256']:
            raise RuntimeError(f'Firmware checksum mismatch: {output}; not overwritten.')
        print('verified: ' + item['archive'] + ' and ' + item['output'], flush=True)
    print('Pinned source, submodules, and firmware checks passed.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired, zipfile.BadZipFile) as error:
        print(f'bootstrap: {error}', file=sys.stderr)
        sys.exit(1)
