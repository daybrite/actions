"""Execute the real workflow generator and its output with a synthetic CLI."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

WORKFLOW = yaml.safe_load((Path(__file__).parents[1] / '.github/workflows/dayapp.yml').read_text())
STEPS = WORKFLOW['jobs']['build']['steps']


DAY_DEPS = yaml.safe_load((Path(__file__).parents[1] / '.github/actions/day-deps/action.yml').read_text())


def preflight_step(step_id):
    return next(s['run'] for s in WORKFLOW['jobs']['preflight']['steps'] if s.get('id') == step_id)


def day_deps_step(step_id):
    return next(s['run'] for s in DAY_DEPS['runs']['steps'] if s.get('id') == step_id)


class DayDepsTests(unittest.TestCase):
    """Execute the day-deps resolver with a synthetic git; no network."""

    def pick(self, deps='auto', event='push', ref='refs/heads/main', version='auto'):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git = root / 'git'
            git.write_text('#!/usr/bin/env bash\nprintf "%s\\trefs/heads/main\\n" "$FIXTURE_TIP"\n')
            git.chmod(0o755)
            env = dict(os.environ, PATH=directory + os.pathsep + os.environ['PATH'],
                       DEPS_IN=deps, VERSION_IN=version, DAY_GIT='https://example.invalid/day.git',
                       EVENT=event, REF=ref, FIXTURE_TIP='c' * 40,
                       GITHUB_OUTPUT=str(root / 'output'))
            result = subprocess.run(['bash', '-c', day_deps_step('pick')], cwd=root, env=env,
                                    capture_output=True, text=True)
            out = (root / 'output').read_text() if (root / 'output').exists() else ''
            return result, dict(line.split('=', 1) for line in out.splitlines())

    def test_a_push_builds_against_main_with_its_cli(self):
        result, out = self.pick()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(out, {'mode': 'main', 'rev': 'c' * 40, 'cli': 'c' * 40})

    def test_schedule_tag_and_dispatch_build_against_the_release(self):
        for event, ref in [('schedule', 'refs/heads/main'), ('push', 'refs/tags/v1.2.3'),
                           ('workflow_dispatch', 'refs/heads/main')]:
            result, out = self.pick(event=event, ref=ref)
            self.assertEqual(result.returncode, 0, result.stderr)
            # The release's CLI is decided once the lock names the version.
            self.assertEqual(out, {'mode': 'published', 'rev': '', 'cli': ''}, event)

    def test_a_pull_request_builds_against_main(self):
        result, out = self.pick(event='pull_request', ref='refs/pull/1/merge')
        self.assertEqual(out['mode'], 'main')

    def test_a_commit_pins_day_and_its_cli(self):
        result, out = self.pick(deps='b' * 40, event='schedule')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(out, {'mode': 'main', 'rev': 'b' * 40, 'cli': 'b' * 40})

    def test_locked_keeps_the_lock_and_installs_main(self):
        result, out = self.pick(deps='locked')
        self.assertEqual(out, {'mode': 'locked', 'rev': '', 'cli': 'main'})

    def test_an_explicit_cli_wins(self):
        result, out = self.pick(version='v0.6.0')
        self.assertEqual(out['cli'], 'v0.6.0')

    def test_a_generated_project_installs_main_unless_told(self):
        for version, want in [('auto', 'main'), ('v0.6.0', 'v0.6.0')]:
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'output'
                env = dict(os.environ, VERSION_IN=version, GITHUB_OUTPUT=str(output))
                subprocess.run(['bash', '-c', preflight_step('generated')], env=env, check=True)
                self.assertEqual(output.read_text(), f'cli={want}\n')

    def test_nonsense_is_refused(self):
        result, out = self.pick(deps='mian')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(out, {})


PUBLISHED_LOCK = '''version = 4

[[package]]
name = "day-core"
version = "0.6.1"
source = "registry+https://github.com/rust-lang/crates.io-index"

[[package]]
name = "day-piece-charts"
version = "0.4.2"
source = "registry+https://github.com/rust-lang/crates.io-index"

[[package]]
name = "day-rise"
version = "0.5.0"

[[package]]
name = "serde"
version = "1.0.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
'''


class PublishedDepsTests(unittest.TestCase):
    """Execute the newest-release updater with a synthetic cargo."""

    def update(self, lock, cli=''):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'Cargo.lock').write_text(lock)
            cargo = root / 'cargo'
            cargo.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$ARG_LOG"\n')
            cargo.chmod(0o755)
            env = dict(os.environ, PATH=directory + os.pathsep + os.environ['PATH'],
                       CLI_IN=cli, ARG_LOG=str(root / 'args'),
                       GITHUB_OUTPUT=str(root / 'output'), GITHUB_STEP_SUMMARY=str(root / 'summary'))
            result = subprocess.run(['bash', '-c', day_deps_step('published')], cwd=root, env=env,
                                    capture_output=True, text=True)
            read = lambda name: (root / name).read_text() if (root / name).exists() else ''
            return result, read('args'), read('output')

    def test_only_day_crates_move_and_the_release_cli_follows(self):
        result, args, output = self.update(PUBLISHED_LOCK)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(args, 'update -p day-core@0.6.1 -p day-piece-charts@0.4.2\n')
        self.assertEqual(output, 'version=0.6.1\ncli=v0.6.1\n')

    def test_an_explicit_cli_is_kept(self):
        result, _, output = self.update(PUBLISHED_LOCK, cli='main')
        self.assertIn('cli=main\n', output)

    def test_a_lock_without_published_day_crates_is_refused(self):
        result, args, output = self.update('version = 4\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((args, output), ('', ''))


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
