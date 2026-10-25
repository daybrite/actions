# Copyright © The Daybrite Project
# SPDX-License-Identifier: MPL-2.0
"""Exercise the workflow's bounded failure probes with synthetic subprocess results."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import yaml

WORKFLOW = yaml.safe_load((Path(__file__).parents[1] / '.github/workflows/dayapp.yml').read_text())
STEP = next(s for s in WORKFLOW['jobs']['build']['steps']
            if s.get('name') == 'Collect Android failure diagnostics')
CODE = STEP['run'].split("python3 - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]


class AndroidDiagnosticsTests(unittest.TestCase):
    def test_unresponsive_guest_does_not_prevent_remaining_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []

            def probe(command, *, stdout, stderr, timeout, check):
                calls.append(command)
                self.assertEqual(timeout, 10)
                self.assertFalse(check)
                self.assertEqual(stderr, subprocess.STDOUT)
                stdout.write('synthetic diagnostic output\n')
                if command[1:] == ['devices', '-l']:
                    raise subprocess.TimeoutExpired(command, timeout)
                if command[0] == 'journalctl':
                    raise FileNotFoundError('synthetic missing journalctl')
                return subprocess.CompletedProcess(command, 0)

            with patch.dict(os.environ, {'RUNNER_TEMP': directory}), \
                    patch('subprocess.run', side_effect=probe):
                exec(compile(CODE, '<workflow diagnostic collector>', 'exec'), {})
            root = Path(directory) / 'android-diagnostics'
            self.assertIn('probe unavailable:', (root / 'devices.log').read_text())
            self.assertIn('probe unavailable:', (root / 'host-kernel.log').read_text())
            self.assertIn('exit code: 0', (root / 'guest-anr.log').read_text())
            self.assertIn('exit code: 0', (root / 'guest-logcat.log').read_text())
            self.assertIn('synthetic diagnostic output', (root / 'host-memory.log').read_text())


if __name__ == '__main__':
    unittest.main()
