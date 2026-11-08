# Copyright © The Daybrite Project
# SPDX-License-Identifier: MPL-2.0
"""Synthetic repositories exercise the naming policy without calling GitHub."""

import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

import yaml


ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / ".github/actions/check-crate-names"
spec = importlib.util.spec_from_file_location("project_names", ACTION / "check-project-names.py")
names = importlib.util.module_from_spec(spec)
spec.loader.exec_module(names)


def fork(parent, source):
    return {"fork": True, "parent": {"full_name": parent}, "source": {"full_name": source}}


class ProjectNamesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.fetch = Mock(return_value={"fork": False})

    def write(self, path, text):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)
        return file

    def check(self, repo="someone/example", project_path=""):
        return names.check(self.root, repo, project_path, self.fetch)

    def test_daybrite_names_pass_without_a_network_request(self):
        self.write("Cargo.toml", '[package]\nname = "day-example"\n')
        self.write("Day.toml", '[app]\ntitle = "Day-Example"\n')
        self.assertEqual(self.check("Daybrite/Day-Example"), [])
        self.fetch.assert_not_called()

    def test_unreserved_projects_can_depend_on_day_without_network_access(self):
        self.write("Cargo.toml", '''[package]
name = "dayext-piece-example"
[dependencies]
day = { package = "day-app", version = "0.*" }
day-core = "0.*"
[workspace.dependencies]
day-pieces = "0.*"
''')
        self.write("Cargo.lock", 'name = "day-core"\n')
        self.write("Day.toml", '[app]\ntitle = "Day Trip"\nid = "dev.example.day-trip"\n')
        self.assertEqual(self.check("someone/dayext-example"), [])
        self.fetch.assert_not_called()

    def test_repository_prefixes_are_reserved_case_insensitively(self):
        for repo in ["someone/day-example", "someone/Day-Example", "someone/DAY-example"]:
            with self.subTest(repo=repo):
                errors = self.check(repo)
                self.assertEqual(len(errors), 1)
                self.assertIn(repo, errors[0])
        self.assertEqual(len(self.check("daybrite-copy/day-example")), 1)

    def test_checks_all_packages_including_nested_workspaces_and_unpublished_crates(self):
        self.write("Cargo.toml", '[workspace]\nmembers = ["crates/*"]\n')
        self.write("crates/one/Cargo.toml", '[package]\nname = "day-one"\npublish = false\n')
        tracked = self.write("examples/demo/Cargo.toml", '[package]\nname = "day-two"\n[workspace]\n')
        subprocess.run(["git", "add", str(tracked)], cwd=self.root, check=True)
        errors = self.check()
        self.assertEqual(len(errors), 2)
        self.assertIn("crates/one/Cargo.toml", errors[0])
        self.assertIn("examples/demo/Cargo.toml", errors[1])

    def test_app_names_titles_flavors_and_binary_names_are_checked(self):
        self.write("Day.toml", '[app]\nname = "Day-One"\ntitle = "Day-Two"\n')
        self.write("demo/Day-preview.toml", '[app]\ntitle = "Day-Preview"\n')
        self.write("Cargo.toml", '''[package]
name = "independent"
[lib]
name = "day-library"
[[bin]]
name = "Day-Executable"
''')
        self.assertEqual(len(self.check()), 5)

    def test_a_flavor_filename_is_not_itself_an_app_name(self):
        self.write("Day-preview.toml", '[app]\ntitle = "Independent Preview"\n')
        self.assertEqual(self.check(), [])

    def test_direct_forks_are_exempt(self):
        self.fetch.return_value = fork("daybrite/Day-Example", "daybrite/Day-Example")
        self.assertEqual(self.check("someone/Day-Example"), [])

    def test_forks_of_forks_are_exempt_from_source_metadata(self):
        self.fetch.return_value = fork("another/Day-Example", "daybrite/Day-Example")
        self.assertEqual(self.check("someone/Day-Example"), [])
        self.fetch.assert_called_once()

    def test_daybrite_can_be_an_intermediate_fork_not_the_original_source(self):
        self.fetch.side_effect = [fork("another/copy", "upstream/original"),
                                  fork("daybrite/copy", "upstream/original")]
        self.assertEqual(self.check("someone/Day-Example"), [])
        self.assertEqual(self.fetch.call_count, 2)

    def test_renamed_daybrite_forks_can_keep_reserved_package_names(self):
        self.write("Cargo.toml", '[package]\nname = "day-example"\n')
        self.fetch.return_value = fork("daybrite/example", "daybrite/example")
        self.assertEqual(self.check("someone/unreserved"), [])

    def test_unrelated_forks_do_not_qualify(self):
        self.fetch.side_effect = [fork("other/original", "other/original"), {"fork": False}]
        self.assertEqual(len(self.check("someone/day-example")), 1)

    def test_manifest_repository_urls_do_not_establish_fork_ancestry(self):
        self.write("Cargo.toml", '''[package]
name = "day-example"
repository = "https://github.com/daybrite/example"
''')
        self.assertEqual(len(self.check()), 1)

    def test_missing_and_cyclic_fork_metadata_fail_closed(self):
        for info in [{}, {"fork": True}, fork("someone/day-example", "upstream/original")]:
            with self.subTest(info=info):
                self.fetch.return_value = info
                with self.assertRaises(names.AuditError):
                    self.check("someone/day-example")

    def test_ignored_build_output_is_not_a_project_definition(self):
        self.write(".gitignore", "target/\nbuild/\n")
        self.write("target/generated/Cargo.toml", '[package]\nname = "day-generated"\n')
        self.write("build/Day.toml", '[app]\ntitle = "Day-Build"\n')
        self.assertEqual(self.check(), [])

    def test_generated_project_is_checked_even_when_ignored_and_has_nested_git(self):
        self.write(".gitignore", "generated/\n")
        project = self.root / "generated"
        self.write("generated/Cargo.toml", '[workspace]\nmembers = ["member"]\n')
        self.write("generated/member/Cargo.toml", '[package]\nname = "day-example"\n')
        self.write("generated/Day.toml", '[app]\ntitle = "Day-Example"\n')
        self.write("generated/target/Cargo.toml", '[package]\nname = "day-artifact"\n')
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        self.assertEqual(self.check(), [])
        self.assertEqual(len(self.check(project_path="generated")), 2)

    def test_invalid_manifests_and_paths_are_not_silently_skipped(self):
        self.write("Cargo.toml", "[package\n")
        with self.assertRaisesRegex(names.AuditError, "Cannot parse"):
            self.check()
        for path in ["../", "missing"]:
            with self.subTest(path=path), self.assertRaises(names.AuditError):
                self.check(project_path=path)

    def test_symlinked_manifests_cannot_escape_the_checkout(self):
        with tempfile.TemporaryDirectory() as external:
            file = Path(external) / "Cargo.toml"
            file.write_text('[package]\nname = "day-example"\n')
            (self.root / "Cargo.toml").symlink_to(file)
            with self.assertRaisesRegex(names.AuditError, "outside the checkout"):
                self.check()

    def test_api_failures_are_reported_without_exposing_credentials(self):
        for failure in [HTTPError("https://api.github.com", 403, "secret-token", {}, None),
                        URLError("secret-token"), TimeoutError("secret-token")]:
            with self.subTest(failure=failure), patch.object(names, "urlopen", side_effect=failure):
                with self.assertRaises(names.AuditError) as result:
                    names.github_repository("someone/day-example")
                self.assertNotIn("secret-token", str(result.exception))

    def test_api_uses_current_repository_metadata_and_read_token(self):
        response = io.BytesIO(json.dumps({"full_name": "someone/day-example", "fork": False}).encode())
        with patch.dict(os.environ, {"GH_TOKEN": "read-token", "GITHUB_API_URL": "https://api.github.com"}), \
                patch.object(names, "urlopen", return_value=response) as request:
            self.assertFalse(names.github_repository("someone/day-example")["fork"])
        self.assertEqual(request.call_args.args[0].full_url, "https://api.github.com/repos/someone/day-example")
        self.assertEqual(request.call_args.args[0].get_header("Authorization"), "Bearer read-token")

    def test_cli_failures_link_policy_recommend_dayext_and_escape_annotations(self):
        for result in [["Rejected name\n::notice::injected%"], names.AuditError("Cannot verify ancestry")]:
            with self.subTest(result=result), patch.object(names, "check") as check, \
                    patch.object(names.sys, "argv", ["check-project-names.py"]), \
                    patch.object(names.sys, "stderr", new_callable=io.StringIO) as output:
                if isinstance(result, Exception):
                    check.side_effect = result
                else:
                    check.return_value = result
                self.assertEqual(names.main(), 1)
                message = output.getvalue()
                self.assertIn("dayext-", message)
                self.assertIn("https://daybrite.dev/contributing/", message)
                self.assertEqual(len(message.splitlines()), 1, repr(message))


class WorkflowWiringTests(unittest.TestCase):
    def test_gate_precedes_rust_and_dependency_setup_in_both_workflows(self):
        for file, job in [("dayapp.yml", "preflight"), ("daylib.yml", "plan")]:
            with self.subTest(file=file):
                data = yaml.safe_load((ROOT / ".github/workflows" / file).read_text())
                steps = data["jobs"][job]["steps"]
                self.assertTrue(steps[0]["uses"].startswith("actions/checkout@"))
                self.assertIn("check-crate-names@", steps[1]["uses"])
                self.assertNotIn("if", steps[1])
                self.assertNotIn("continue-on-error", steps[1])

    def test_generated_projects_are_checked_immediately_after_setup(self):
        data = yaml.safe_load((ROOT / ".github/workflows/dayapp.yml").read_text())
        found = False
        for job in data["jobs"].values():
            steps = job.get("steps", [])
            for index, step in enumerate(steps):
                if step.get("name") == "Setup command":
                    following = steps[index + 1]
                    self.assertIn("check-crate-names@", following["uses"])
                    self.assertEqual(following["if"], step["if"])
                    self.assertEqual(following["with"]["project-path"], "${{ inputs.project-path }}")
                    found = True
        self.assertTrue(found)


if __name__ == "__main__":
    unittest.main()
