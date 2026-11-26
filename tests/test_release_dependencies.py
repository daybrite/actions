#!/usr/bin/env python3
# Copyright © The Daybrite Project
# SPDX-License-Identifier: MPL-2.0
"""Fail closed on unreproducible release inputs; fixtures never contact a registry."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / '.github/actions/day-deps/release.py'
spec = importlib.util.spec_from_file_location('release_deps', SOURCE)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
ACTION = yaml.safe_load((SOURCE.parent / 'action.yml').read_text())
WORKFLOW = yaml.safe_load((ROOT / '.github/workflows/dayapp.yml').read_text())


class ReleaseDependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'Cargo.toml').write_text('[package]\nname="fixture"\nversion="1.0.0"\n')
        self.lock = ('version = 4\n[[package]]\nname="fixture"\nversion="1.0.0"\n'
                     '[[package]]\nname="day-core"\nversion="0.7.12"\n'
                     f'source="{release.CRATES_IO}"\nchecksum="' + 'a' * 64 + '"\n')
        (self.root / 'Cargo.lock').write_text(self.lock)
        self.metadata = {'workspace_members': ['fixture'], 'packages': [
            {'id': 'fixture', 'name': 'fixture', 'version': '1.0.0', 'source': None,
             'manifest_path': str(self.root / 'Cargo.toml')},
            {'id': 'core', 'name': 'day-core', 'version': '0.7.12', 'source': release.CRATES_IO,
             'manifest_path': '/synthetic-registry/day-core/Cargo.toml'}]}

    def validate(self):
        with patch.object(release.subprocess, 'check_output', return_value=json.dumps(self.metadata).encode()) as cargo:
            version = release.validate(self.root, require_tracked=False)
            self.assertEqual(cargo.call_args.args[0], ['cargo', 'metadata', '--locked', '--format-version', '1'])
            return version

    def test_checked_graph_stays_byte_identical(self):
        self.assertEqual(self.validate(), '0.7.12')
        self.assertEqual((self.root / 'Cargo.lock').read_text(), self.lock)

    def test_missing_lock_is_not_created(self):
        (self.root / 'Cargo.lock').unlink()
        with self.assertRaises(FileNotFoundError):
            self.validate()
        self.assertFalse((self.root / 'Cargo.lock').exists())

    def test_untracked_and_modified_release_locks_are_rejected(self):
        for calls in [0, 1]:
            results = [subprocess.CompletedProcess('git', 0)] * calls
            results.append(subprocess.CalledProcessError(1, 'git'))
            with self.subTest(successful_git_checks=calls), \
                 patch.object(release.subprocess, 'run', side_effect=results), \
                 patch.object(release.subprocess, 'check_output') as cargo:
                with self.assertRaises(subprocess.CalledProcessError):
                    release.validate(self.root)
                cargo.assert_not_called()
                self.assertEqual((self.root / 'Cargo.lock').read_text(), self.lock)

    def test_stale_lock_failure_is_not_repaired(self):
        with patch.object(release.subprocess, 'check_output', side_effect=subprocess.CalledProcessError(101, 'cargo')):
            with self.assertRaises(subprocess.CalledProcessError):
                release.validate(self.root, require_tracked=False)
        self.assertEqual((self.root / 'Cargo.lock').read_text(), self.lock)

    def test_git_and_other_registries_are_rejected_before_cargo_runs(self):
        for source in ['git+https://example.test/day#1234', 'registry+https://example.test/index']:
            with self.subTest(source=source):
                (self.root / 'Cargo.lock').write_text(self.lock.replace(release.CRATES_IO, source))
                with patch.object(release.subprocess, 'check_output') as cargo:
                    with self.assertRaisesRegex(ValueError, 'must come from crates.io'):
                        release.validate(self.root, require_tracked=False)
                    cargo.assert_not_called()

    def test_checksums_are_required(self):
        (self.root / 'Cargo.lock').write_text(self.lock.split('checksum=')[0])
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.validate()

    def test_local_patch_and_source_replacement_are_rejected(self):
        (self.root / '.cargo').mkdir()
        for text in ['[patch.crates-io]\nday-core={path="../day-core"}\n',
                     '[source.crates-io]\nreplace-with="vendor"\n']:
            (self.root / '.cargo/config.toml').write_text(text)
            with self.assertRaisesRegex(ValueError, 'source overrides'):
                self.validate()

    def test_unused_target_and_workspace_git_dependencies_are_rejected(self):
        for table in ['workspace.dependencies', 'target.\'cfg(unix)\'.dependencies', 'dependencies']:
            (self.root / 'Cargo.toml').write_text(f'[{table}]\nx={{git="https://example.test/x",optional=true}}\n')
            with self.assertRaisesRegex(ValueError, 'crates.io version'):
                self.validate()

    def test_path_crate_must_be_inside_the_workspace(self):
        self.metadata['packages'][0]['manifest_path'] = '/elsewhere/Cargo.toml'
        with self.assertRaisesRegex(ValueError, 'outside the app workspace'):
            self.validate()

    def test_unused_path_packages_cannot_hide_in_the_lock(self):
        with (self.root / 'Cargo.lock').open('a') as file:
            file.write('[[package]]\nname="unrelated"\nversion="1.0.0"\n')
        with self.assertRaisesRegex(ValueError, 'unverified path package'):
            self.validate()

    def test_git_sources_in_the_resolved_graph_are_rejected(self):
        self.metadata['packages'][1]['source'] = 'git+https://example.test/day'
        with self.assertRaisesRegex(ValueError, 'outside crates.io'):
            self.validate()

    def test_duplicate_framework_versions_are_rejected(self):
        self.metadata['packages'].append({**self.metadata['packages'][1], 'version': '0.7.4'})
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            self.validate()

    def test_cli_must_be_the_matching_release(self):
        for cli, expected in [('auto', 0), ('v0.7.12', 0), ('main', 1), ('latest', 1), ('v0.7.11', 1)]:
            with self.subTest(cli=cli), patch.object(release, 'validate', return_value='0.7.12'), \
                 patch('sys.argv', [str(SOURCE), str(self.root), '--cli', cli]), \
                 patch.dict(os.environ, {'GITHUB_OUTPUT': str(self.root / 'output')}):
                self.assertEqual(release.main(), expected)

    def test_release_mode_cannot_enter_the_lock_repair_or_update_steps(self):
        steps = ACTION['runs']['steps']
        prepare = next(s for s in steps if s.get('name') == 'Prepare Cargo.lock')
        update = next(s for s in steps if s.get('id') == 'published')
        self.assertEqual(prepare['if'], "steps.pick.outputs.mode != 'release'")
        self.assertEqual(update['if'], "steps.pick.outputs.mode == 'published'")
        pick = next(s['run'] for s in steps if s.get('id') == 'pick')
        result = subprocess.run(['bash', '-c', pick], cwd=self.root, capture_output=True, text=True,
            env={**os.environ, 'DEPS_IN': 'release', 'VERSION_IN': 'auto', 'EVENT': 'push',
                 'REF': 'refs/tags/v1.2.3', 'DAY_GIT': 'https://invalid.test/day',
                 'GITHUB_OUTPUT': str(self.root / 'picked')})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('mode=release', (self.root / 'picked').read_text())

    def test_artifacts_require_preserved_release_lock(self):
        steps = WORKFLOW['jobs']['build']['steps']
        guard = next(s for s in steps if s.get('id') == 'release-lock')
        upload = next(s for s in steps if s.get('name') == 'Upload packages')
        self.assertIn('git diff --exit-code HEAD -- Cargo.lock', guard['run'])
        self.assertIn("steps.release-lock.outcome == 'success'", upload['if'])


if __name__ == '__main__':
    unittest.main()
