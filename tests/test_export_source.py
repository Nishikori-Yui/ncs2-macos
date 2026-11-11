"""Source-export regressions: all mutations stay inside temporary fixtures."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/export_source.py'
SPEC = importlib.util.spec_from_file_location('export_source', SCRIPT)
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='source-export-test-')
        self.addCleanup(self.temporary.cleanup)
        # macOS's default temporary directory can itself have a symlink ancestor.
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / 'source'
        self.root.mkdir()
        for name in exporter.REQUIRED_FILES:
            self.put(name, b'# synthetic fixture\n')
        for name in ('ncs2', 'ncs2_log_run.sh'):
            (self.root / name).chmod(0o755)
        self.output = self.base / 'new-parent' / 'candidate'

    def put(self, name, data=b'# synthetic fixture\n'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def export(self):
        return exporter.export_source(self.root, self.output)

    def rejected_before_creation(self):
        with self.assertRaises((OSError, ValueError)):
            self.export()
        self.assertFalse(self.output.parent.exists())

    def manifest_path(self):
        return self.output / exporter.MANIFEST

    def rewrite_manifest(self, manifest):
        self.manifest_path().write_text(json.dumps(manifest), encoding='utf-8')

    def test_manifest_hashes_bytes_summary_and_read_only_check(self):
        data = 'Source fixture — 中文\r\n'.encode('utf-8')
        self.put('docs/fixture.md', data)
        manifest = self.export()
        self.assertEqual(manifest['files']['docs/fixture.md']['sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(manifest['files']['docs/fixture.md']['bytes'], len(data))
        self.assertEqual(manifest['summary']['total_bytes'], sum(
            (self.output / name).stat().st_size for name in manifest['files']))
        self.assertEqual(manifest['summary']['file_count'], len(manifest['files']))
        before = {str(p.relative_to(self.output)): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in self.output.rglob('*') if p.is_file()}
        self.assertEqual(exporter.check_output(self.output), manifest)
        after = {str(p.relative_to(self.output)): (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in self.output.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(manifest['own_license_selected'])
        self.assertFalse(manifest['release_ready'])
        self.assertFalse(manifest['published'])
        self.assertTrue(os.access(self.output / 'ncs2', os.X_OK))

    def test_every_required_file_is_required(self):
        for name in sorted(exporter.REQUIRED_FILES):
            with self.subTest(name=name):
                path = self.root / name
                data, mode = path.read_bytes(), path.stat().st_mode
                path.unlink()
                self.rejected_before_creation()
                path.write_bytes(data)
                path.chmod(mode)

    def test_missing_source_directory(self):
        (self.root / 'examples').rename(self.root / 'hidden-examples')
        self.rejected_before_creation()

    def test_symlink_source_file(self):
        path = self.root / 'README.md'
        path.unlink()
        path.symlink_to(self.root / 'CONTRIBUTING.md')
        self.rejected_before_creation()

    def test_symlink_source_directory(self):
        (self.root / 'scripts').rename(self.base / 'external-scripts')
        (self.root / 'scripts').symlink_to(self.base / 'external-scripts', target_is_directory=True)
        self.rejected_before_creation()

    def test_workflow_is_exactly_allowlisted_and_hash_checked(self):
        allowed = '.github/workflows/native-arm64.yml'
        self.assertTrue(exporter.allowed_path(allowed))
        for name in ('.github/workflows/foreign.yml', '.github/private.yml',
                     '.github/workflows/nested/foreign.yml'):
            self.assertFalse(exporter.allowed_path(name))
            self.put(name, b'ignored secret fixture')
        manifest = self.export()
        self.assertIn(allowed, manifest['files'])
        self.assertEqual(set(manifest['files']), exporter.REQUIRED_FILES)
        (self.output / allowed).write_bytes(b'changed')
        with self.assertRaises(ValueError):
            exporter.check_output(self.output)

    def test_each_workflow_directory_rejects_symlinks(self):
        for directory in ('.github/workflows', '.github'):
            with self.subTest(directory=directory):
                original = self.root / directory
                moved = self.base / 'external-workflows'
                original.rename(moved)
                original.symlink_to(moved, target_is_directory=True)
                self.rejected_before_creation()
                original.unlink()
                moved.rename(original)

    def test_nested_workflow_output_symlink_is_rejected(self):
        self.export()
        original = self.output / '.github/workflows'
        moved = self.base / 'moved-workflows'
        original.rename(moved)
        original.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(OSError):
            exporter.check_output(self.output)

    def test_output_inside_workflow_parent_is_rejected(self):
        with self.assertRaises(ValueError):
            exporter.export_source(self.root, self.root / '.github/candidate')

    def test_symlink_root_parent(self):
        alias = self.base / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            exporter.export_source(alias, self.output)
        self.assertFalse(self.output.parent.exists())

    def test_dangling_optional_license_is_rejected(self):
        (self.root / 'LICENSE').symlink_to(self.base / 'missing')
        self.rejected_before_creation()

    def test_nonregular_matching_entry(self):
        (self.root / 'scripts/fake.py').mkdir()
        self.rejected_before_creation()

    def test_fifo_matching_entry_does_not_block(self):
        os.mkfifo(self.root / 'scripts/fake.py')
        self.rejected_before_creation()

    def test_hardlink_matching_entry(self):
        os.link(self.root / 'README.md', self.root / 'scripts/linked.py')
        self.rejected_before_creation()

    def test_binary_utf8_control_and_signature_rejection(self):
        for data in (b'\xff\xfe', b'print(1)\x00', b'hello\x1b[0m',
                     b'MZdisguised executable', b'PK\x03\x04archive', 'x\u0085y'.encode()):
            with self.subTest(data=data):
                self.put('scripts/fake.py', data)
                self.rejected_before_creation()

    def test_known_absolute_path_rejection(self):
        for text in (str(self.root), str(Path.home()) + '/private-file'):
            with self.subTest(text=text):
                self.put('docs/local.md', text.encode())
                self.rejected_before_creation()

    def test_size_limits(self):
        with mock.patch.object(exporter, 'MAX_FILE_BYTES', 5):
            self.rejected_before_creation()
        with mock.patch.object(exporter, 'MAX_TOTAL_BYTES', 5):
            self.rejected_before_creation()

    def test_excludes_vendor_build_logs_firmware_caches_and_nested_sources(self):
        excluded = ('vendor/leak.py', 'third_party/leak.py', 'build-arm64/leak.py',
                    'firmware-cache/firmware.py', 'results/raw.md', 'dist/old.py',
                    'ncs2_read_attempts.md', 'scripts/output.log', 'scripts/model.bin',
                    'scripts/__pycache__/leak.py', 'docs/vendor/leak.md',
                    'examples/firmware.mvcmd', 'tests/helper.py', '.env')
        for name in excluded:
            self.put(name, b'\x00raw excluded data')
        manifest = self.export()
        self.assertFalse(set(excluded) & manifest['files'].keys())
        self.assertEqual(set(manifest['files']), exporter.REQUIRED_FILES)

    def test_existing_output_is_never_overwritten(self):
        self.output.mkdir(parents=True)
        marker = self.output / 'sentinel'
        marker.write_bytes(b'preserve')
        with self.assertRaises(FileExistsError):
            self.export()
        self.assertEqual(marker.read_bytes(), b'preserve')
        self.assertEqual(list(self.output.iterdir()), [marker])

    def test_symlink_output_and_parent(self):
        destination = self.base / 'destination'
        destination.mkdir()
        self.output.parent.symlink_to(destination, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.export()
        self.assertEqual(list(destination.iterdir()), [])
        self.output.parent.unlink()
        self.output.parent.mkdir()
        self.output.symlink_to(destination, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.export()
        self.assertEqual(list(destination.iterdir()), [])

    def test_output_inside_source_directory(self):
        output = self.root / 'docs/candidate'
        with self.assertRaises(ValueError):
            exporter.export_source(self.root, output)
        self.assertFalse(output.exists())

    def test_parent_traversal_output(self):
        with self.assertRaises(ValueError):
            exporter.export_source(self.root, self.base / 'unused/../candidate')
        self.assertFalse((self.base / 'unused').exists())

    def test_license_presence_never_makes_release_ready(self):
        self.put('LICENSE', b'Synthetic fixture, not a license selection.\n')
        manifest = self.export()
        self.assertTrue(manifest['own_license_selected'])
        self.assertFalse(manifest['release_ready'])

    def test_check_detects_same_size_tamper(self):
        self.export()
        path = self.output / 'README.md'
        data = path.read_bytes()
        path.write_bytes(b'!' + data[1:])
        with self.assertRaisesRegex(ValueError, 'integrity'):
            exporter.check_output(self.output)

    def test_check_detects_missing_extra_and_symlink_files(self):
        self.export()
        for name in ('extra.py', 'scripts/unlisted.py', 'vendor/foreign.py'):
            path = self.output / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'new')
            with self.assertRaises(ValueError):
                exporter.check_output(self.output)
            path.unlink()
            if name.startswith('vendor/'):
                path.parent.rmdir()
        path = self.output / 'README.md'
        data = path.read_bytes()
        path.unlink()
        with self.assertRaises(ValueError):
            exporter.check_output(self.output)
        path.symlink_to(self.root / 'README.md')
        with self.assertRaises(OSError):
            exporter.check_output(self.output)
        path.unlink()
        path.write_bytes(data)

    def test_check_rejects_directory_and_manifest_symlinks(self):
        self.export()
        (self.output / 'scripts').rename(self.base / 'moved-scripts')
        (self.output / 'scripts').symlink_to(self.base / 'moved-scripts', target_is_directory=True)
        with self.assertRaises(OSError):
            exporter.check_output(self.output)
        (self.output / 'scripts').unlink()
        (self.base / 'moved-scripts').rename(self.output / 'scripts')
        self.manifest_path().rename(self.base / 'moved-manifest')
        self.manifest_path().symlink_to(self.base / 'moved-manifest')
        with self.assertRaises(OSError):
            exporter.check_output(self.output)

    def test_check_manifest_metadata_and_unsafe_paths(self):
        manifest = self.export()
        for key, value in (('release_ready', True), ('published', True),
                           ('own_license_selected', True), ('schema_version', 1),
                           ('summary', {}), ('files', {'../escape.py': {}})):
            with self.subTest(key=key):
                self.rewrite_manifest({**manifest, key: value})
                with self.assertRaises(ValueError):
                    exporter.check_output(self.output)
        for name in ('../escape.py', '/escape.py', 'scripts//fake.py',
                     'scripts/../fake.py', 'scripts\\fake.py', 'vendor/fake.py'):
            self.assertFalse(exporter.allowed_path(name))
        self.manifest_path().write_text('{"schema_version":2,"schema_version":2}')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            exporter.check_output(self.output)

    def test_check_executable_tamper(self):
        self.export()
        (self.output / 'ncs2').chmod(0o644)
        with self.assertRaises(ValueError):
            exporter.check_output(self.output)

    def test_check_works_without_original_sources(self):
        self.export()
        self.root.rename(self.base / 'relocated-original')
        exporter.check_output(self.output)

    def test_snapshot_is_used_after_validation(self):
        original = exporter.validate_output_path
        data = (self.root / 'README.md').read_bytes()
        def mutate(output, root):
            (root / 'README.md').write_bytes(b'\x00changed after validation')
            return original(output, root)
        with mock.patch.object(exporter, 'validate_output_path', side_effect=mutate):
            self.export()
        self.assertEqual((self.output / 'README.md').read_bytes(), data)

    def test_cli_export_and_check(self):
        # Run a real exporter copy rooted in a synthetic project; never mutate the project.
        (self.root / 'scripts/export_source.py').write_bytes(SCRIPT.read_bytes())
        command = [sys.executable, '-B', str(self.root / 'scripts/export_source.py')]
        result = subprocess.run(command + ['--output', str(self.output)], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('inventory_sha256=', result.stdout)
        result = subprocess.run(command + ['--check', str(self.output)], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('SOURCE_CHECK=PASS', result.stdout)
        result = subprocess.run(command + ['--output', str(self.output)], capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
