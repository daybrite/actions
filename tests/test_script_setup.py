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
    def run_fixture(self, fail=False, legacy=False, fast="true", launch_env="",
                    target="android-mdc", once=""):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cli = root / 'day'
            cli.write_text('''#!/usr/bin/env bash
if [[ "$*" == 'launch --help' ]]; then
  echo "${HELP_FLAGS:-}"
  exit 0
fi
printf 'launch:%s:%s\\n' "$DAY_SCRIPT_TARGET" "${FIXTURE_READY:-missing}" >> "$EVENT_LOG"
printf '%s\\n' __launch__ "$@" >> "$ARG_LOG"
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
                       ARG_LOG=str(root/'args'), FAST_SCRIPTS_IN=fast,
                       SCRIPT_SETUP_IN=setup, SCRIPTS_IN='one.yaml two.yaml',
                       LOCALES_IN='', THEMES_IN='', LAUNCH_ENV_IN=launch_env, SCRIPTS_ONCE_IN=once,
                       CAPTURE_SIZE_IN='', COMBO=target, DEVICE_SLUG='',
                       HELP_FLAGS='' if legacy else '--locales', LAUNCH_EXIT='7' if fail else '0')
            generator = next(s['run'] for s in STEPS if s.get('id') == 'scripts')
            subprocess.run(['bash', '-c', generator], cwd=root, env=env, check=True,
                           capture_output=True, text=True)
            self.assertFalse((root/'events').exists(), 'setup must wait until execution/device boot')
            result = subprocess.run(['bash', str(root/'run-dayscripts.sh')], cwd=root, env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode == 0, not fail, result.stderr)
            events = (root/'events').read_text().splitlines()
            self.assertEqual(events[0], f'setup:{target}')
            self.assertEqual(events[-1], 'cleanup')
            self.assertEqual(len(events), 3 if fail or (target == "web-dom" and not once) else 4)
            self.assertTrue(all(e == f'launch:{target}:quotes " and $() remain literal' for e in events[1:-1]))
            return [chunk.splitlines() for chunk in (root/'args').read_text().split('__launch__\n')[1:]]

    def test_setup_runs_once_and_cleanup_runs_after_success(self):
        self.run_fixture()

    def test_cleanup_preserves_failed_launch_status(self):
        self.run_fixture(fail=True)

    def test_legacy_runner_also_sources_setup(self):
        self.run_fixture(legacy=True)

    def test_fast_policy_reaches_every_launch_and_explicit_env_wins(self):
        # Run the actual generated shell for modern, legacy, web and once-only paths.
        for legacy, target, once in [(False, "android-mdc", ""),
                                     (True, "android-mdc", ""),
                                     (False, "harmony-arkui", "two.yaml"),
                                     (False, "web-dom", ""),
                                     (False, "web-dom", "two.yaml")]:
            for fast, override, expected in [("true", "", "1"), ("false", "", "0"),
                                             ("true", "DAY_TEST_FAST=0 FIXTURE=1", "0"),
                                             ("false", "DAY_TEST_FAST=1", "1")]:
                with self.subTest(legacy=legacy, target=target, once=once,
                                  fast=fast, override=override):
                    launches = self.run_fixture(legacy=legacy, target=target, once=once,
                                               fast=fast, launch_env=override)
                    for args in launches:
                        policy = [a for a in args if a.startswith("DAY_TEST_FAST=")]
                        self.assertEqual(policy, ["DAY_TEST_FAST=" + expected])
                        self.assertEqual(args[args.index(policy[0])-1], "--env")
                        self.assertNotIn("--fast", args, "older CLIs must still work")
                        if "FIXTURE=1" in override:
                            self.assertIn("FIXTURE=1", args)

    def test_fast_scripts_defaults_on_in_workflow(self):
        # YAML 1.1 reads the unquoted workflow key `on` as True.
        self.assertIs(WORKFLOW[True]['workflow_call']['inputs']['fast-scripts']['default'], True)

    def test_extra_targets_build_and_script_without_packaging(self):
        pack = next(s for s in STEPS if s.get('id') == 'pack')
        self.assertIn("matrix.tier == 'primary'", pack['if'])
        build = next(s for s in STEPS if s.get('id') == 'build')
        self.assertNotIn('tier', build.get('if', ''))


if __name__ == '__main__':
    unittest.main()
