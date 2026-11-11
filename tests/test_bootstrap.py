"""Local Git fixtures for interrupted bootstrap; no network, firmware or USB."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import bootstrap


class BootstrapTests(unittest.TestCase):
    def fixture(self, directory):
        remote = Path(directory) / 'upstream'
        remote.mkdir()
        subprocess.run(['git', 'init', '-q', str(remote)], check=True)
        (remote / 'pinned.txt').write_text('upstream fixture\n')
        subprocess.run(['git', '-C', str(remote), 'add', 'pinned.txt'], check=True)
        subprocess.run(['git', '-C', str(remote), '-c', 'user.name=Fixture', '-c',
                        'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false',
                        'commit', '-qm', 'fixture'], check=True)
        commit = subprocess.check_output(['git', '-C', str(remote), 'rev-parse', 'HEAD'], text=True).strip()
        return {'url': str(remote), 'commit': commit}

    def test_initial_fetch_and_empty_interrupted_repository_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            ov = self.fixture(directory)
            for resumed in (False, True):
                with self.subTest(resumed=resumed):
                    source = Path(directory) / ('resumed' if resumed else 'new')
                    if resumed:
                        subprocess.run(['git', 'init', '-q', str(source)], check=True)
                        subprocess.run(['git', '-C', str(source), 'remote', 'add', 'origin', ov['url']], check=True)
                    bootstrap.prepare_source(source, ov, False)
                    self.assertEqual((source / 'pinned.txt').read_text(), 'upstream fixture\n')
                    bootstrap.prepare_source(source, ov, True)

    def test_incomplete_worktree_files_and_wrong_origin_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            ov = self.fixture(directory)
            source = Path(directory) / 'source'
            subprocess.run(['git', 'init', '-q', str(source)], check=True)
            subprocess.run(['git', '-C', str(source), 'remote', 'add', 'origin', 'https://example.invalid/untrusted'], check=True)
            with self.assertRaisesRegex(RuntimeError, 'origin'):
                bootstrap.prepare_source(source, ov, False)
            (source / 'precious.txt').write_text('preserve')
            with self.assertRaisesRegex(RuntimeError, 'existing work-tree'):
                bootstrap.prepare_source(source, ov, False)
            self.assertEqual((source / 'precious.txt').read_text(), 'preserve')
            with self.assertRaisesRegex(RuntimeError, 'incomplete'):
                bootstrap.prepare_source(source, ov, True)

    def test_transfer_retries_stop_after_three_and_success_stops_early(self):
        failure = subprocess.CalledProcessError(128, 'git fetch')
        for outcomes, expected, fails in (([failure, None], 2, False), ([failure]*3, 3, True)):
            with self.subTest(fails=fails), patch.object(bootstrap, 'run', side_effect=outcomes) as run, \
                    patch.object(bootstrap.time, 'sleep'):
                if fails:
                    with self.assertRaises(subprocess.CalledProcessError):
                        bootstrap.fetch_revision(Path('unused'), {'commit': 'a'*40})
                else:
                    bootstrap.fetch_revision(Path('unused'), {'commit': 'a'*40})
                self.assertEqual(run.call_count, expected)


if __name__ == '__main__':
    unittest.main()
