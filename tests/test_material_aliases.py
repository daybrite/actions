# Copyright © The Daybrite Project
# SPDX-License-Identifier: MPL-2.0

"""Exercise the actual reusable-workflow normalizer and composite input steps."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / '.github/workflows/dayapp.yml').read_text())


class MaterialAliasTests(unittest.TestCase):
    def normalize(self, **values):
        script = next(step['run'] for step in WORKFLOW['jobs']['preflight']['steps']
                      if 'normalized_inputs=$(' in step.get('run', ''))
        code = script.split('normalized_inputs=$(TARGETS_IN="$TARGETS_IN" python3 - <<\'PYEOF\'\n', 1)[1].split('\nPYEOF', 1)[0]
        return subprocess.run(['python3', '-c', code], env={**os.environ, **values}, capture_output=True, text=True)

    def test_aliases_deduplicate_and_do_not_change_command_bodies(self):
        result = self.normalize(TARGETS_IN='android-mdc, android-material, mdc',
                                TARGET_SETUP_IN='android-mdc: echo android-mdc\nandroid-mdc: echo two')
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value['TARGETS_IN'], 'android-material')
        self.assertEqual(value['TARGET_SETUP_IN'], 'android-material: echo android-mdc\nandroid-material: echo two')
        self.assertEqual(result.stderr.count('::warning::'), 1)

    def test_conflicting_override_spellings_are_rejected_for_each_map(self):
        for key in ('TARGET_PACKAGES_IN', 'TARGET_SETUP_IN', 'TARGET_SCRIPTS_IN', 'GRANTS_IN'):
            with self.subTest(key=key):
                result = self.normalize(TARGETS_IN='android-material', **{key: 'android-mdc: one\nandroid-material: two'})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('name the same target', result.stderr)

    def test_composites_normalize_direct_callers(self):
        for name in ('setup-day-deps', 'store-upload'):
            action = yaml.safe_load((ROOT / f'.github/actions/{name}/action.yml').read_text())
            step = next(step for step in action['runs']['steps'] if step.get('id') == 'canonical-target')
            for target in ('mdc', 'android-mdc', 'android-material'):
                with self.subTest(action=name, target=target), tempfile.TemporaryDirectory() as directory:
                    output = Path(directory) / 'output'
                    result = subprocess.run(['bash', '-c', step['run']], env={**os.environ, 'RAW_TARGET': target, 'GITHUB_OUTPUT': str(output)}, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(output.read_text(), 'target=android-material\n')

    def test_flavor_manifests_accept_deprecated_target_lists(self):
        script = next(step['run'] for step in WORKFLOW['jobs']['preflight']['steps']
                      if 'flavor_targets=$(' in step.get('run', ''))
        code = script.split('flavor_targets=$(', 1)[1].split("<<'PYEOF'\n", 1)[1].split('\nPYEOF', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'Day-fixture.toml').write_text(
                "[app]\ntargets = ['android-mdc', 'android-material']\n")
            result = subprocess.run(['python3', '-c', code], env={**os.environ,
                'FLAVORS': 'fixture', 'PROJECT_PATH': directory}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {'fixture': ['android-material']})
            self.assertEqual(result.stderr.count('::warning::'), 1)
