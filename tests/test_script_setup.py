"""Execute the real workflow generator and its output with a synthetic CLI."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

WORKFLOW = yaml.safe_load((Path(__file__).parents[1] / '.github/workflows/dayapp.yml').read_text())
STEPS = WORKFLOW['jobs']['build']['steps']


class DayRevisionTests(unittest.TestCase):
    """Execute the dependency resolver with synthetic Cargo output; no network or Git writes."""

    def resolve(self, requested='', resolved='a' * 40, cargo_exit='0'):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cargo = root / 'cargo'
            cargo.write_text('''#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$ARG_LOG"
if [[ "$FIXTURE_CARGO_EXIT" != 0 ]]; then exit "$FIXTURE_CARGO_EXIT"; fi
printf '[[package]]\\nname = "day-core"\\nversion = "0.0.0"\\nsource = "git+https://example.invalid/day#%s"\\n' "$FIXTURE_REVISION" > Cargo.lock
''')
            cargo.chmod(0o755)
            env = dict(os.environ, PATH=directory + os.pathsep + os.environ['PATH'],
                       DAY_REVISION_IN=requested, FIXTURE_REVISION=resolved,
                       FIXTURE_CARGO_EXIT=cargo_exit, ARG_LOG=str(root / 'args'),
                       GITHUB_OUTPUT=str(root / 'output'), GITHUB_STEP_SUMMARY=str(root / 'summary'))
            script = next(s['run'] for s in WORKFLOW['jobs']['preflight']['steps']
                          if s.get('id') == 'day-dep')
            result = subprocess.run(['bash', '-c', script], cwd=root, env=env,
                                    capture_output=True, text=True)
            read = lambda name: (root / name).read_text() if (root / name).exists() else ''
            return result, read('args'), read('output')

    def test_default_resolves_tracked_tip(self):
        result, args, output = self.resolve()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(args, 'update -p day-core\n')
        self.assertEqual(output, 'rev=' + 'a' * 40 + '\n')

    def test_caller_revision_is_used_and_reported(self):
        revision = 'b' * 40
        result, args, output = self.resolve(revision, revision)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(args, 'update -p day-core --precise ' + revision + '\n')
        self.assertEqual(output, 'rev=' + revision + '\n')

    def test_invalid_revision_never_reaches_cargo(self):
        result, args, output = self.resolve('main')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((args, output), ('', ''))

    def test_wrong_resolved_revision_is_not_published(self):
        result, _, output = self.resolve('b' * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output, '')

    def test_cargo_failure_is_not_published(self):
        result, _, output = self.resolve(cargo_exit='7')
        self.assertEqual(result.returncode, 7)
        self.assertEqual(output, '')


class ScriptSetupTests(unittest.TestCase):
    def run_fixture(self, fail=False, legacy=False, fast="true", launch_env="",
                    target="android-material", once="", grants="", help_flags=None):
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
                       CAPTURE_SIZE_IN='', GRANTS_IN=grants, COMBO=target, DEVICE_SLUG='',
                       HELP_FLAGS=(help_flags if help_flags is not None
                                   else '' if legacy else '--locales'),
                       LAUNCH_EXIT='7' if fail else '0')
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

    def test_grants_reach_every_launch_when_the_cli_knows_the_flag(self):
        # One --grant per name, on the per-variant and the once-only runners alike.
        for target, once in [("android-material", ""), ("ios-uikit", "two.yaml")]:
            with self.subTest(target=target, once=once):
                launches = self.run_fixture(target=target, once=once,
                                            grants="camera, microphone",
                                            help_flags="--locales --grant")
                self.assertEqual(len(launches), 2)
                for args in launches:
                    granted = [args[i + 1] for i, a in enumerate(args) if a == "--grant"]
                    self.assertEqual(granted, ["camera", "microphone"])

    def test_grants_are_dropped_when_the_cli_predates_the_flag(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                launches = self.run_fixture(legacy=legacy, grants="camera")
                for args in launches:
                    self.assertNotIn("--grant", args)

    def test_fast_policy_reaches_every_launch_and_explicit_env_wins(self):
        # Run the actual generated shell for modern, legacy, web and once-only paths.
        for legacy, target, once in [(False, "android-material", ""),
                                     (True, "android-material", ""),
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
