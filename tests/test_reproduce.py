"""No network/USB: test reproduction deadline and public-summary boundaries."""
import importlib.util
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import reproduce_macos as reproduction


class ReproductionTests(unittest.TestCase):
    def test_timeout_reaps_real_process_and_retains_output(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'child.log'
            started = time.monotonic()
            result = reproduction.execute_stage([
                sys.executable, '-c',
                'import time; print("started",flush=True); time.sleep(20)',
            ], directory, log, 0.2)
            self.assertLess(time.monotonic() - started, 3)
            self.assertTrue(result['timed_out'])
            self.assertEqual(result['exit_code'], 124)
            self.assertIn('started', log.read_text())
            self.assertEqual(result['log_sha256'], reproduction.sha256(log))

    def test_stage_exit_and_existing_log_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'child.log'
            result = reproduction.execute_stage([sys.executable, '-c', 'raise SystemExit(7)'], directory, log, 3)
            self.assertEqual(result['exit_code'], 7)
            self.assertFalse(result['timed_out'])
            with self.assertRaises(FileExistsError):
                reproduction.execute_stage([sys.executable, '-c', 'print("overwrite")'], directory, log, 3)

    def test_public_summary_omits_local_paths_raw_errors_and_commands(self):
        report = {'schema_version': 1, 'passed': False, 'error': 'private user/path detail',
                  'error_type': 'RuntimeError', 'output': 'raw private log',
                  'absolute_path': 'private-directory', 'commands': ['private argv'],
                  'failed_stage': 'build', 'hardware_executed': False}
        summary = reproduction.sanitized_summary(report)
        self.assertEqual(set(summary), {'schema_version', 'passed', 'failed_stage', 'hardware_executed'})
        self.assertFalse(summary['passed'])


if __name__ == '__main__':
    unittest.main()
