"""Architecture-audit checks using synthetic Mach-O command output."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import check_arm64


class ArchitectureAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='ncs2-arch-audit-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.probe = self.root / 'build-arm64/ncs2_openvino_probe'
        self.runtime = self.root / 'vendor/openvino/bin/arm64/Release'
        self.probe.parent.mkdir(parents=True)
        self.runtime.mkdir(parents=True)
        for path in (self.probe, *(self.probe.parent / name for name in
                                    ('ncs2_lab', 'ncs2_perf', 'ncs2_worker', 'ncs2_apps', 'ncs2_multi_io', 'ncs2_multi_worker'))):
            path.write_bytes(path.name.encode())
        self.library = self.runtime / 'libopenvino.dylib'
        self.library.write_bytes(b'openvino')
        (self.runtime / 'libopenvino_intel_myriad_plugin.so').write_bytes(b'myriad')
        self.rpath_only = self.runtime / 'libopenvino_ir_frontend.dylib'
        self.rpath_only.write_bytes(b'frontend')
        self.libusb = self.root / 'external/libusb-1.0.dylib'
        self.libusb.parent.mkdir()
        self.libusb.write_bytes(b'libusb')
        self.saved = (check_arm64.ROOT, check_arm64.RUNTIME, check_arm64.PROBE)
        check_arm64.ROOT = self.root
        check_arm64.RUNTIME = self.runtime
        check_arm64.PROBE = self.probe

    def tearDown(self):
        check_arm64.ROOT, check_arm64.RUNTIME, check_arm64.PROBE = self.saved

    def mock_command(self, command, text=True):
        if command[0] == 'lipo':
            return 'arm64\n'
        path = Path(command[-1])
        if path == self.probe:
            return f"{path}:\n\t{self.library} (compatibility version 1.0.0, current version 1.0.0)\n\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1.0.0)\n"
        if path == self.library:
            return f"{path}:\n\t@rpath/libopenvino.dylib (compatibility version 1.0.0, current version 1.0.0)\n\t{self.libusb} (compatibility version 1.0.0, current version 1.0.0)\n"
        return f"{path}:\n"

    @patch.object(check_arm64.platform, 'system', return_value='Darwin')
    @patch.object(check_arm64.platform, 'machine', return_value='arm64')
    @patch.object(check_arm64.subprocess, 'check_output')
    def test_manifest_records_architecture_hash_and_dependencies(self, command, _machine, _system):
        command.side_effect = self.mock_command
        report = check_arm64.audit()
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['non_system_macho_count'], 11)
        probe = next(item for item in report['artifacts'] if item['path'].endswith('ncs2_openvino_probe'))
        self.assertEqual(probe['architectures'], ['arm64'])
        self.assertEqual(probe['sha256'], hashlib.sha256(self.probe.read_bytes()).hexdigest())
        self.assertEqual([item['system'] for item in probe['dependencies']], [False, True])
        self.assertNotIn(str(self.root), json.dumps(report))
        self.assertEqual(probe['dependencies'][0]['install_name'], 'external/libopenvino.dylib')
        self.assertEqual({item['resolved_path'] for artifact in report['artifacts']
                          for item in artifact['dependencies'] if not item['system']},
                         {'vendor/openvino/bin/arm64/Release/libopenvino.dylib',
                          'external/libusb-1.0.dylib'})
        self.assertTrue(all(item['resolved_path'] != artifact['path'] for artifact in report['artifacts']
                            for item in artifact['dependencies'] if not item['system']))

    @patch.object(check_arm64.platform, 'system', return_value='Darwin')
    @patch.object(check_arm64.platform, 'machine', return_value='arm64')
    @patch.object(check_arm64.subprocess, 'check_output')
    def test_rejects_universal_or_x86_artifact(self, command, _machine, _system):
        for architectures in ('x86_64', 'arm64 x86_64'):
            with self.subTest(architectures=architectures):
                command.side_effect = lambda argv, text=True: architectures + '\n' if argv[0] == 'lipo' else f"{argv[-1]}:\n"
                with self.assertRaisesRegex(RuntimeError, 'pure arm64'):
                    check_arm64.audit()


class ArchitectureLauncherTests(unittest.TestCase):
    def test_json_launcher_stdout_is_one_document_and_notebook_is_written(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='ncs2-arch-launcher-') as directory:
            notebook = Path(directory) / 'audit.md'
            result = subprocess.run([str(root / 'ncs2'), 'check', '--json'],
                                    env={**os.environ, 'NCS2_LOG_FILE': str(notebook)},
                                    cwd=directory, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report['schema_version'], 1)
            self.assertEqual(report['status'], 'passed')
            self.assertEqual(report['non_system_macho_count'], len(report['artifacts']))
            self.assertGreaterEqual(report['non_system_macho_count'], 8)
            self.assertTrue(all(item['architectures'] == ['arm64'] for item in report['artifacts']))
            self.assertIn('[recorded] exit=0', result.stderr)
            self.assertIn('"status":"passed"', notebook.read_text())


if __name__ == '__main__':
    unittest.main()
