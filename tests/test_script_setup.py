"""Execute the real workflow generator and its output with a synthetic CLI."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

WORKFLOW = yaml.safe_load((Path(__file__).parents[1] / '.github/workflows/dayapp.yml').read_text())
STEPS = WORKFLOW['jobs']['build']['steps']


class ScriptSetupTests(unittest.TestCase):
    def run_fixture(self, fail=False, legacy=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cli = root / 'day'
            cli.write_text('''#!/usr/bin/env bash
if [[ "$*" == 'launch --help' ]]; then
  echo "${HELP_FLAGS:-}"
  exit 0
fi
printf 'launch:%s:%s\\n' "$DAY_SCRIPT_TARGET" "${FIXTURE_READY:-missing}" >> "$EVENT_LOG"
exit "${LAUNCH_EXIT:-0}"
''')
            cli.chmod(0o755)
            setup = '''printf 'setup:%s\\n' "$DAY_SCRIPT_TARGET" >> "$EVENT_LOG"
export FIXTURE_READY='quotes " and $() remain literal'
trap 'echo cleanup >> "$EVENT_LOG"' EXIT
'''
            env = dict(os.environ, DAY_BIN=str(cli), RUNNER_TEMP=directory,
                       PATH=directory+os.pathsep+os.environ['PATH'],
                       GITHUB_OUTPUT=str(root/'output'), EVENT_LOG=str(root/'events'),
                       SCRIPT_SETUP_IN=setup, SCRIPTS_IN='one.yaml two.yaml',
                       LOCALES_IN='', THEMES_IN='', LAUNCH_ENV_IN='', SCRIPTS_ONCE_IN='',
                       CAPTURE_SIZE_IN='', COMBO='android-mdc', DEVICE_SLUG='',
                       HELP_FLAGS='' if legacy else '--locales', LAUNCH_EXIT='7' if fail else '0')
            generator = next(s['run'] for s in STEPS if s.get('id') == 'scripts')
            subprocess.run(['bash', '-c', generator], cwd=root, env=env, check=True,
                           capture_output=True, text=True)
            self.assertFalse((root/'events').exists(), 'setup must wait until execution/device boot')
            result = subprocess.run(['bash', str(root/'run-dayscripts.sh')], cwd=root, env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode == 0, not fail, result.stderr)
            events = (root/'events').read_text().splitlines()
            self.assertEqual(events[0], 'setup:android-mdc')
            self.assertEqual(events[-1], 'cleanup')
            self.assertEqual(len(events), 3 if fail else 4)
            self.assertTrue(all(e == 'launch:android-mdc:quotes " and $() remain literal' for e in events[1:-1]))

    def test_setup_runs_once_and_cleanup_runs_after_success(self):
        self.run_fixture()

    def test_cleanup_preserves_failed_launch_status(self):
        self.run_fixture(fail=True)

    def test_legacy_runner_also_sources_setup(self):
        self.run_fixture(legacy=True)

    def test_extra_targets_build_and_script_without_packaging(self):
        pack = next(s for s in STEPS if s.get('id') == 'pack')
        self.assertIn("matrix.tier == 'primary'", pack['if'])
        build = next(s for s in STEPS if s.get('id') == 'build')
        self.assertNotIn('tier', build.get('if', ''))


if __name__ == '__main__':
    unittest.main()
