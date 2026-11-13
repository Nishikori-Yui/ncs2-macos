#!/usr/bin/env python3
"""Export or verify an unpublished source candidate using only the standard library."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import uuid

ROOT = Path(__file__).absolute().parents[1]
MANIFEST = 'SOURCE_MANIFEST.json'
SOURCE_SUFFIXES = frozenset({'.md', '.json', '.py', '.sh', '.cpp', '.hpp', '.swift'})
ROOT_FILES = frozenset({
    'README.md', 'THIRD_PARTY_NOTICES.md', 'CONTRIBUTING.md', '.gitignore',
    'toolchain.lock.json', 'ncs2', 'ncs2_log_run.sh', 'ncs2_client.py',
    'ncs2_multi_client.py', 'ncs2_async.py', 'read_ncs2_usb.py', 'Package.swift',
})
DIRECTORY_SUFFIXES = {
    'docs': {'.md'}, 'scripts': {'.py', '.sh'},
    'tests': {'.py'}, 'examples': {'.swift'}, 'swift-tests': {'.swift'},
}
SOURCE_DIRECTORY_PATHS = frozenset(
    '/'.join(parts[:index])
    for directory in DIRECTORY_SUFFIXES
    for parts in [PurePosixPath(directory).parts]
    for index in range(1, len(parts) + 1)
)
REQUIRED_FILES = ROOT_FILES | frozenset({
    'ncs2_openvino_probe.cpp', 'ncs2_json.hpp', 'ncs2_worker.cpp', 'ncs2_multi_worker.cpp',
    'examples/NCS2WorkerClient.swift', 'examples/NCS2MultiWorkerClient.swift',
    'examples/NCS2Runtime.swift', 'swift-tests/NCS2KitTests.swift', 'swift-tests/NCS2RuntimeTests.swift',
    'docs/SOURCE_EXPORT.md', 'docs/THIRD_PARTY_REVIEW.md', 'docs/TOOLCHAIN.md',
    'docs/WORKER_API.md', 'docs/ASYNC_SDK.md', 'tests/test_async_sdk.py', 'tests/test_check_arm64.py', 'tests/test_export_source.py',
    'tests/test_multi_worker.py', 'tests/test_swift_client.py',
    'tests/test_swift_multi_client.py', 'tests/test_usb_parser.py', 'tests/test_worker.py',
    'scripts/bootstrap.py', 'scripts/build_probe.sh', 'scripts/build_runtime.sh', 'scripts/build_swift_clients.sh',
    'scripts/check_arm64.py', 'scripts/check_arm64.sh', 'scripts/export_source.py',
    'scripts/log_run.py', 'scripts/run_probe.sh',
})
EXCLUDED = ['vendor', 'third_party', 'build*', 'dist', 'firmware-cache', 'results',
            'raw notebook/logs', 'runtime binaries', 'caches', 'nested source directories']
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024


def allowed_path(name):
    """Location and suffix must both match; never recurse into source subtrees."""
    if not isinstance(name, str) or not name or '\\' in name:
        return False
    path = PurePosixPath(name)
    if path.is_absolute() or path.as_posix() != name or any(
            part in ('.', '..') or not re.fullmatch(r'[A-Za-z0-9_.-]+', part)
            for part in path.parts):
        return False
    if len(path.parts) == 1 and name in ROOT_FILES | {'LICENSE'}:
        return True
    if path.suffix not in SOURCE_SUFFIXES:
        return False
    if len(path.parts) == 1:
        return path.name.startswith('ncs2') and path.suffix in {'.cpp', '.hpp'}
    if len(path.parts) != 2:
        return False
    directory, leaf = path.parts
    return (path.suffix in DIRECTORY_SUFFIXES.get(directory, set())
            and not leaf.startswith('.')
            and (directory != 'tests' or leaf.startswith('test_')))


def absolute_path(path):
    path = Path(path)
    if '..' in path.parts:
        raise ValueError('Parent traversal is not allowed: ' + str(path))
    return path.absolute()


def open_directory(path, create=False):
    """Walk from / using pinned descriptors; no component may be a symlink."""
    path = absolute_path(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def open_relative_directory(directory_fd, path):
    """Open each selected source/output directory without following symlinks."""
    current = os.dup(directory_fd)
    try:
        for part in PurePosixPath(path).parts:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def read_regular(directory_fd, name):
    """No-follow, nonblocking open also rejects FIFOs, devices and hardlinks."""
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError('Expected an unlinked regular source file: ' + name)
        if before.st_size > MAX_FILE_BYTES:
            raise ValueError('File exceeds source size limit: ' + name)
        data = stream.read(MAX_FILE_BYTES + 1)
        after = os.fstat(stream.fileno())
        if (len(data) > MAX_FILE_BYTES or len(data) != before.st_size
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise ValueError('Source changed during read or exceeds size limit: ' + name)
        return data, bool(before.st_mode & 0o111)


def validate_text(name, data, forbidden=()):
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError as error:
        raise ValueError('Non-UTF-8 source: ' + name) from error
    if any(ord(char) < 32 and char not in '\t\r\n' or 127 <= ord(char) <= 159
           for char in text):
        raise ValueError('Binary/control data in source: ' + name)
    # Some binary signatures (e.g. DOS executables) can also be valid UTF-8.
    if data.startswith((b'MZ', b'PK\x03\x04', b'\x7fELF', b'\x00asm',
                        b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xca\xfe\xba\xbe')):
        raise ValueError('Binary signature in source: ' + name)
    if any(token and token in text for token in forbidden):
        raise ValueError('Known local absolute path in ' + name)


def collect_sources(root):
    root = absolute_path(root)
    root_fd = open_directory(root)
    sources = {}
    forbidden = (str(root), str(Path.home()) + '/')
    try:
        def collect(fd, prefix):
            for leaf in sorted(os.listdir(fd)):
                name = prefix + leaf
                if allowed_path(name):
                    data, executable = read_regular(fd, leaf)
                    validate_text(name, data, forbidden)
                    sources[name] = (data, executable)
                    if sum(len(item[0]) for item in sources.values()) > MAX_TOTAL_BYTES:
                        raise ValueError('Source candidate exceeds total size limit')
        collect(root_fd, '')
        for directory in DIRECTORY_SUFFIXES:
            fd = open_relative_directory(root_fd, directory)
            try:
                collect(fd, directory + '/')
            finally:
                os.close(fd)
    finally:
        os.close(root_fd)
    missing = REQUIRED_FILES - sources.keys()
    if missing:
        raise ValueError('Missing required source files: ' + ', '.join(sorted(missing)))
    if not sources['ncs2'][1] or not sources['ncs2_log_run.sh'][1]:
        raise ValueError('Required launchers must be executable: ncs2, ncs2_log_run.sh')
    return dict(sorted(sources.items()))


def file_records(sources):
    return {name: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                   'executable': executable}
            for name, (data, executable) in sorted(sources.items())}


def summary(records):
    canonical = json.dumps(records, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return {'file_count': len(records), 'total_bytes': sum(item['bytes'] for item in records.values()),
            'inventory_sha256': hashlib.sha256(canonical).hexdigest()}


def make_manifest(sources):
    records = file_records(sources)
    return {'schema_version': 2, 'artifact': 'source-candidate', 'published': False,
            'own_license_selected': 'LICENSE' in sources, 'release_ready': False,
            'remaining_checks': ['owner license review', 'fresh-machine reproduction',
                                 'manual secret/provenance review', 'review exact file list before publishing'],
            'excluded': EXCLUDED, 'files': records, 'summary': summary(records)}


def validate_output_path(output, root):
    output = absolute_path(output)
    # Do not create new files in an input directory, even when output is empty.
    for directory in SOURCE_DIRECTORY_PATHS:
        if output.is_relative_to(root / directory):
            raise ValueError('Output must not be inside a selected source directory')
    # Check all existing components before creating even the first output parent.
    for path in reversed((output, *output.parents)):
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValueError('Output path contains a symlink or non-directory: ' + str(path))
        if path == output:
            raise FileExistsError('Output already exists: ' + str(output))
    return output


def write_new(fd, name, data, executable=False):
    target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(target, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fchmod(stream.fileno(), 0o755 if executable else 0o644)


def export_source(root, output):
    root = absolute_path(root)
    sources = collect_sources(root)  # Snapshot and validate every byte before mkdir.
    output = validate_output_path(output, root)
    manifest = make_manifest(sources)
    parent_fd = open_directory(output.parent, create=True)
    try:
        os.mkdir(output.name, dir_fd=parent_fd)  # Never reuse an existing output.
        output_fd = os.open(output.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)
    try:
        for name, (data, executable) in sources.items():
            parts = PurePosixPath(name).parts
            fd = output_fd
            try:
                for directory in parts[:-1]:
                    try:
                        os.mkdir(directory, dir_fd=fd)
                    except FileExistsError:
                        pass
                    child = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    if fd != output_fd:
                        os.close(fd)
                    fd = child
                write_new(fd, parts[-1], data, executable)
            finally:
                if fd != output_fd:
                    os.close(fd)
        # Completion marker comes last; errors leave an incomplete, never-overwritten candidate.
        write_new(output_fd, MANIFEST, (json.dumps(manifest, indent=2) + '\n').encode('utf-8'))
    finally:
        os.close(output_fd)
    return check_output(output)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate manifest key: ' + key)
        result[key] = value
    return result


def check_output(output):
    """Read-only, exact-tree verification; does not read the current source tree."""
    fd = open_directory(output)
    try:
        raw, _ = read_regular(fd, MANIFEST)
        manifest = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object)
        if not isinstance(manifest, dict) or type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 2:
            raise ValueError('Unsupported source manifest schema (expected 2)')
        records = manifest.get('files')
        if not isinstance(records, dict) or not REQUIRED_FILES <= records.keys():
            raise ValueError('Manifest missing required source files')
        for name, record in records.items():
            if (not allowed_path(name) or not isinstance(record, dict)
                    or set(record) != {'bytes', 'sha256', 'executable'}
                    or type(record['bytes']) is not int or not 0 <= record['bytes'] <= MAX_FILE_BYTES
                    or type(record['executable']) is not bool
                    or not isinstance(record['sha256'], str)
                    or not re.fullmatch('[0-9a-f]{64}', record['sha256'])):
                raise ValueError('Invalid manifest file record: ' + name)
        sources = {}
        expected_by_directory = {'': {MANIFEST}}
        for name in records:
            parts = PurePosixPath(name).parts
            for index in range(len(parts)):
                parent = '/'.join(parts[:index])
                expected_by_directory.setdefault(parent, set()).add(parts[index])
                if index < len(parts) - 1:
                    expected_by_directory.setdefault('/'.join(parts[:index + 1]), set())
        for directory, expected in expected_by_directory.items():
            directory_fd = fd if not directory else open_relative_directory(fd, directory)
            try:
                if set(os.listdir(directory_fd)) != expected:
                    raise ValueError('Unexpected or missing output entries in ' + (directory or '.'))
            finally:
                if directory:
                    os.close(directory_fd)
        for name in sorted(records):
            parts = PurePosixPath(name).parts
            parent = '/'.join(parts[:-1])
            directory_fd = fd if not parent else open_relative_directory(fd, parent)
            try:
                sources[name] = read_regular(directory_fd, parts[-1])
            finally:
                if parent:
                    os.close(directory_fd)
        for name, (data, _) in sources.items():
            validate_text(name, data)
        if sum(len(data) for data, _ in sources.values()) > MAX_TOTAL_BYTES:
            raise ValueError('Source candidate exceeds total size limit')
        if not sources['ncs2'][1] or not sources['ncs2_log_run.sh'][1]:
            raise ValueError('Required launchers must be executable')
        # Canonical serialization distinguishes bool/int metadata and checks all flags/totals.
        if json.dumps(manifest, sort_keys=True) != json.dumps(make_manifest(sources), sort_keys=True):
            raise ValueError('Manifest integrity, summary or candidate metadata mismatch')
        return manifest
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--output', type=Path, help='new candidate directory (must not exist)')
    group.add_argument('--check', type=Path, metavar='DIRECTORY', help='read-only schema-2 candidate verification')
    args = parser.parse_args(argv)
    try:
        if args.check is not None:
            output = absolute_path(args.check)
            manifest = check_output(output)
            label = 'SOURCE_CHECK=PASS'
        else:
            name = 'source-candidate-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
            output = absolute_path(args.output or ROOT / 'dist' / name)
            manifest = export_source(ROOT, output)
            label = 'SOURCE_CANDIDATE=' + str(output)
        info = manifest['summary']
        print(f"{label}; files={info['file_count']}; bytes={info['total_bytes']}; "
              f"inventory_sha256={info['inventory_sha256']}; published=false; release_ready=false")
        return 0
    except (OSError, ValueError) as error:
        print('SOURCE_EXPORT_ERROR: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
