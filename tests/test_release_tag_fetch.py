"""Reproduce checkout's synthetic local tag against a real annotated Git tag."""
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ReleaseTagFetchTests(unittest.TestCase):
    def git(self, directory, *args):
        return subprocess.run(['git', *args], cwd=directory, check=True,
                              capture_output=True, text=True).stdout.strip()

    def fixture(self, directory):
        remote, runner = directory / 'origin', directory / 'runner'
        remote.mkdir(); runner.mkdir()
        self.git(remote, 'init')
        self.git(remote, 'config', 'user.name', 'Release test')
        self.git(remote, 'config', 'user.email', 'test@example.invalid')
        (remote / 'file').write_text('first')
        self.git(remote, 'add', 'file'); self.git(remote, 'commit', '-m', 'first')
        commit = self.git(remote, 'rev-parse', 'HEAD')
        self.git(remote, 'tag', '-a', 'v0.6.0', '-m', 'annotated release')
        self.git(runner, 'init'); self.git(runner, 'remote', 'add', 'origin', str(remote))
        self.git(runner, 'fetch', '--no-tags', '--depth=1', 'origin', commit + ':refs/tags/v0.6.0')
        self.git(runner, 'checkout', 'refs/tags/v0.6.0')
        return remote, runner, commit

    def configured_fetch(self, runner):
        workflow = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
        fetch = next(line.strip() for line in workflow.splitlines() if line.strip().startswith('git fetch --no-tags origin'))
        gate = next(line.strip() for line in workflow.splitlines() if line.strip().startswith('test "$(git rev-parse HEAD)"'))
        match = re.fullmatch(r'test "\$\(git rev-parse HEAD\)" = "\$\(git rev-parse "([^"]+)"\)"', gate)
        self.assertIsNotNone(match, 'The exact HEAD-to-release commit equality gate must remain')
        command = shlex.split(fetch.replace('$release_version', 'v0.6.0'))
        subprocess.run(command, cwd=runner, check=True, capture_output=True, text=True)
        return self.git(runner, 'rev-parse', match.group(1).replace('$release_version', 'v0.6.0'))

    def test_annotated_remote_does_not_clobber_checkout_tag(self):
        with tempfile.TemporaryDirectory() as temp:
            remote, runner, commit = self.fixture(Path(temp))
            self.assertEqual(self.git(runner, 'cat-file', '-t', 'refs/tags/v0.6.0'), 'commit')
            remote_tag = self.git(remote, 'rev-parse', 'refs/tags/v0.6.0')
            self.assertEqual(self.configured_fetch(runner), commit)
            self.assertEqual(self.git(runner, 'rev-parse', 'HEAD'), commit)
            self.assertEqual(self.git(runner, 'rev-parse', 'refs/tags/v0.6.0'), commit)
            self.assertEqual(self.git(remote, 'rev-parse', 'refs/tags/v0.6.0'), remote_tag)

    def test_different_checked_out_commit_still_fails_equality_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            _, runner, original = self.fixture(Path(temp))
            self.git(runner, 'config', 'user.name', 'Release test')
            self.git(runner, 'config', 'user.email', 'test@example.invalid')
            (runner / 'file').write_text('different candidate')
            self.git(runner, 'add', 'file'); self.git(runner, 'commit', '-m', 'different')
            target = self.configured_fetch(runner)
            self.assertEqual(target, original)
            self.assertNotEqual(self.git(runner, 'rev-parse', 'HEAD'), target)

    def test_manual_recovery_checks_out_the_immutable_release_everywhere(self):
        lines = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8').splitlines()
        checkouts = [i for i, line in enumerate(lines) if '- uses: actions/checkout@' in line]
        self.assertGreater(len(checkouts), 3)
        for i in checkouts:
            self.assertEqual(lines[i + 1].strip(), 'with:')
            self.assertEqual(lines[i + 2].strip(), "ref: ${{ github.event_name == 'workflow_dispatch' && inputs.release_tag || github.sha }}")
