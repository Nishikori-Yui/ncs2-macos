"""Regressions for the notebook's prior self-reading expansion failure."""
import os
import select
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

LOGGER = Path(__file__).resolve().parents[1] / 'scripts/log_run.py'


class NotebookTests(unittest.TestCase):
    def test_streaming_ready_precedes_input_and_keeps_stdout_clean(self):
        with tempfile.TemporaryDirectory() as directory:
            log=Path(directory)/"record.md"
            code="import sys; print('warning',file=sys.stderr,flush=True); print('ready',flush=True); print(input(),flush=True)"
            with subprocess.Popen([sys.executable,str(LOGGER),"--stream","stream-regression",sys.executable,"-c",code],
                                  stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                  env={**os.environ,"NCS2_LOG_FILE":str(log)}) as child:
                self.assertTrue(select.select([child.stdout],[],[],3)[0], "logger buffered ready")
                self.assertEqual(child.stdout.readline(),b"ready\n")
                child.stdin.write(b"response\n");child.stdin.flush()
                stdout,stderr=child.communicate(timeout=3)
            self.assertEqual(stdout,b"response\n")
            self.assertIn(b"[recorded]",stderr)
            self.assertIn(b"warning",stderr)
            self.assertIn("response",log.read_text())
            self.assertIn("warning",log.read_text())

    def invoke(self, log, *command):
        return subprocess.run([sys.executable, str(LOGGER), 'regression', *command],
                              env={**os.environ, 'NCS2_LOG_FILE': str(log)},
                              capture_output=True, text=True, timeout=10)

    def test_exit_status_and_stderr(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'record.md'
            result = self.invoke(log, sys.executable, '-c', "import sys; print('failure',file=sys.stderr); sys.exit(7)")
            self.assertEqual(result.returncode, 7)
            self.assertIn('failure', log.read_text())
            self.assertIn('退出码：7', log.read_text())

    def test_reading_own_log_stays_finite(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'record.md'
            log.write_text('one original line\n')
            result = self.invoke(log, '/bin/cat', str(log))
            self.assertEqual(result.returncode, 0)
            self.assertLess(log.stat().st_size, 2000)
            self.assertEqual(log.read_text().count('one original line'), 2)

    def test_arguments_are_not_shell_code(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'record.md'
            marker = Path(directory) / 'must-not-exist'
            literal = f'$(touch {marker})'
            result = self.invoke(log, sys.executable, '-c', 'import sys; print(sys.argv[1])', literal)
            self.assertEqual(result.returncode, 0)
            self.assertFalse(marker.exists())
            self.assertIn(literal, result.stdout)

    def test_large_output_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'record.md'
            result = self.invoke(log, sys.executable, '-c', "print('x' * 3000000)")
            self.assertEqual(result.returncode, 0)
            self.assertLess(log.stat().st_size, 2100000)
            self.assertIn('Output truncated', log.read_text())


if __name__ == '__main__':
    unittest.main()
