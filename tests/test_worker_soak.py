"""Hardware-free checks for the outer soak supervisor and independent inputs."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import worker_soak as soak


class SoakTests(unittest.TestCase):
    def test_inputs_vary_and_constant_convolution_is_analytic(self):
        self.assertEqual(len({tuple(soak.input_values(i)) for i in range(65)}), 65)
        self.assertEqual(soak.reference([1.0] * 64), [0.0] * 36)
        impulse = [0.0] * 64
        impulse[0] = 1.0
        output = soak.reference(impulse)
        self.assertEqual(output[0], -0.125)
        self.assertEqual(output[1:], [0.0] * 35)

    def test_rss_reads_real_current_macos_process(self):
        self.assertGreater(soak.resident_bytes(os.getpid()), 0)

    def test_supervisor_times_out_and_kills_entire_group(self):
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(overall_timeout=0.2)
            with patch.object(soak, 'ROOT', Path(directory)), \
                    patch.object(soak.subprocess, 'Popen') as spawn, \
                    patch.object(soak.os, 'killpg') as kill:
                child = spawn.return_value
                child.pid = 123456
                child.wait.side_effect = [subprocess.TimeoutExpired('fake', 0.2), -9]
                self.assertEqual(soak.supervise(args), 124)
                self.assertTrue(spawn.call_args.kwargs['start_new_session'])
                kill.assert_called_once_with(123456, signal.SIGKILL)
                child.wait.assert_any_call(timeout=5)
            output = next((Path(directory) / 'results').iterdir())
            report = json.loads((output / 'report.json').read_text())
            self.assertTrue(report['complete'])
            self.assertFalse(report['passed'])
            self.assertTrue(report['supervisor_timeout'])
            self.assertTrue(json.loads((output / 'supervisor.json').read_text())['timed_out'])

    def test_success_still_cleans_up_remaining_group(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(soak, 'ROOT', Path(directory)), \
                    patch.object(soak.subprocess, 'Popen') as spawn, \
                    patch.object(soak.os, 'killpg') as kill:
                child = spawn.return_value
                child.pid = 123457
                child.wait.return_value = 0
                self.assertEqual(soak.supervise(argparse.Namespace(overall_timeout=3)), 0)
                kill.assert_called_once_with(123457, signal.SIGKILL)

    def test_real_supervisor_reaps_term_ignoring_process(self):
        real_spawn = subprocess.Popen
        children = []

        def spawn_silent_child(*args, **kwargs):
            child = real_spawn([
                sys.executable, '-c',
                'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(20)',
            ], start_new_session=True)
            children.append(child)
            return child

        with tempfile.TemporaryDirectory() as directory:
            started = time.monotonic()
            with patch.object(soak, 'ROOT', Path(directory)), \
                    patch.object(soak.subprocess, 'Popen', side_effect=spawn_silent_child):
                self.assertEqual(soak.supervise(argparse.Namespace(overall_timeout=0.2)), 124)
            self.assertLess(time.monotonic() - started, 3)
            self.assertEqual(children[0].returncode, -signal.SIGKILL)


if __name__ == '__main__':
    unittest.main()
