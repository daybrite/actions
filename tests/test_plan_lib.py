# Copyright © The Daybrite Project
# SPDX-License-Identifier: MPL-2.0
"""plan-lib: the host, cross-target and publish plans daylib.yml runs, from a library's manifests."""
import importlib.util
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location('plan', ROOT / '.github/actions/plan-lib/plan.py')
plan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plan)
DAYLIB = yaml.safe_load((ROOT / '.github/workflows/daylib.yml').read_text())
DAYAPP = yaml.safe_load((ROOT / '.github/workflows/dayapp.yml').read_text())


def write(root, path, text):
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(textwrap.dedent(text))


@unittest.skipUnless(shutil.which('cargo'), 'plan.py reads the workspace through cargo metadata')
class PlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write(self.root, 'src/lib.rs', '')

    def tearDown(self):
        self.tmp.cleanup()

    def library(self, features=''):
        write(self.root, 'Cargo.toml', f'''
            [package]
            name = "day-part-fixture"
            version = "0.1.0"
            edition = "2024"

            [features]
            {features}
        ''')

    def demo(self, targets):
        listed = ', '.join(f'"{t}"' for t in targets)
        write(self.root, 'demo/Day.toml', f'''
            schema = 1
            [app]
            targets = [{listed}]
        ''')

    def run_plan(self, **env):
        return plan.plan(self.root, env)

    def test_a_part_without_toolkits_checks_every_host_and_the_demos_mobile_targets(self):
        self.library()
        self.demo(['macos-appkit', 'linux-gtk', 'ios-uikit', 'android-material', 'web-dom'])
        result = self.run_plan()
        self.assertEqual([h['name'] for h in result['host']], ['ubuntu-latest', 'macos-15', 'windows-latest'])
        self.assertEqual([h['fmt'] for h in result['host']], [True, False, False])
        self.assertEqual([c['triple'] for c in result['cross']],
                         ['aarch64-apple-ios-sim', 'aarch64-linux-android', 'wasm32-unknown-unknown'])
        self.assertTrue(all(c['features'] == '' for c in result['cross']))
        self.assertIn('getrandom_backend', result['cross'][2]['rustflags'])
        self.assertTrue(result['demo'])
        self.assertEqual(result['publish-paths'], ['.'])

    def test_toolkit_features_add_host_rows_and_name_the_targets_without_a_demo(self):
        self.library('gtk = []\nqt = []\nappkit = []\nuikit = []\nmaterial = []\nmock = []')
        result = self.run_plan()
        self.assertEqual([h['name'] for h in result['host']],
                         ['ubuntu-latest, gtk', 'ubuntu-latest, qt', 'macos-15, appkit', 'windows-latest'])
        self.assertEqual([h['day-target'] for h in result['host']], ['linux-gtk', 'linux-qt', 'macos-appkit', ''])
        self.assertEqual([(c['name'], c['features']) for c in result['cross']],
                         [('ios-uikit', 'uikit'), ('android-material', 'material')])
        self.assertFalse(result['demo'])

    def test_a_library_with_no_toolkit_and_no_demo_is_checked_for_every_mobile_and_web_target(self):
        self.library()
        self.assertEqual([c['name'] for c in self.run_plan()['cross']],
                         ['ios-uikit', 'android-material', 'harmony-arkui', 'web-dom'])

    def test_named_targets_override_the_demos(self):
        self.library()
        self.demo(['ios-uikit', 'android-material'])
        self.assertEqual([c['name'] for c in self.run_plan(TARGETS='web-dom')['cross']], ['web-dom'])

    def test_target_packages_reach_the_rows_of_those_targets(self):
        self.library('gtk = []\nqt = []')
        self.demo(['linux-gtk', 'linux-qt', 'android-material'])
        rows = self.run_plan(TARGET_PACKAGES='linux-gtk, android-material: libfoo\nlinux-gtk: libbar\n')
        host = {h['day-target']: h['packages'] for h in rows['host']}
        self.assertEqual(host['linux-gtk'], 'libfoo libbar')
        self.assertEqual(host['linux-qt'], '')
        self.assertEqual(rows['cross'][0]['packages'], 'libfoo')

    def test_a_package_beside_the_workspace_that_depends_on_it_publishes_after_it(self):
        self.library()
        self.demo(['macos-appkit'])
        write(self.root, 'gallery/src/lib.rs', '')
        write(self.root, 'gallery/Cargo.toml', '''
            [package]
            name = "day-part-fixture-gallery"
            version = "0.1.0"
            edition = "2024"
            [dependencies]
            day-part-fixture = { path = "..", version = "0.1" }
            [workspace]
        ''')
        write(self.root, 'tools/src/main.rs', 'fn main() {}')
        write(self.root, 'tools/Cargo.toml', '''
            [package]
            name = "fixture-tools"
            version = "0.1.0"
            edition = "2024"
            publish = false
            [dependencies]
            day-part-fixture = { path = ".." }
            [workspace]
        ''')
        self.assertEqual(self.run_plan()['publish-paths'], ['.', 'gallery'])
        self.assertEqual(self.run_plan(PUBLISH_PATHS='. extra')['publish-paths'], ['.', 'extra'])


class WorkflowTests(unittest.TestCase):
    def test_every_demo_input_is_one_dayapp_takes(self):
        demo = DAYLIB['jobs']['demo']
        dayapp_inputs = (DAYAPP.get(True) or DAYAPP['on'])['workflow_call']['inputs']
        self.assertEqual(sorted(set(demo['with']) - set(dayapp_inputs)), [])

    def test_publish_waits_for_the_library_checks_and_not_for_the_demo(self):
        publish = DAYLIB['jobs']['publish']
        self.assertEqual(publish['needs'], ['plan', 'host', 'cross-check'])
        self.assertEqual(publish['environment'], 'release')
        self.assertEqual(publish['permissions']['id-token'], 'write')

    def test_every_job_builds_against_the_day_plan_resolved(self):
        for name in ('host', 'cross-check'):
            steps = DAYLIB['jobs'][name]['steps']
            deps = [s for s in steps if s.get('uses', '').startswith('daybrite/actions/.github/actions/day-deps')]
            self.assertEqual(deps[0]['with']['day-deps'], '${{ needs.plan.outputs.day-deps }}', name)
        self.assertEqual(DAYLIB['jobs']['demo']['with']['day-deps'], '${{ needs.plan.outputs.day-deps }}')


if __name__ == '__main__':
    unittest.main()
