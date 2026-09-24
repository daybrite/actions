"""The store-upload jobs: this run's screenshots are indexed in the job and the listing is
checked, staged and uploaded by the `store-upload` action. Both run against a stub day CLI:
nothing is fetched.
"""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/dayapp.yml"
DOC = yaml.safe_load(WORKFLOW.read_text())
JOBS = DOC["jobs"]
INPUTS = DOC.get("on", DOC.get(True))["workflow_call"]["inputs"]
ACTION = yaml.safe_load((ROOT / ".github/actions/store-upload/action.yml").read_text())
STORE_UPLOAD = "daybrite/actions/.github/actions/store-upload@main"
DOWNLOAD = "Download this run's screenshots"
INDEX = "Index this run's screenshots"
TARGETS = (("appstore-ios", "ios-uikit", "Upload to the App Store"), ("playstore-android", "android-mdc", "Upload to Google Play"))


def steps(job):
    return {step.get("name"): step for step in JOBS[job]["steps"]}


def action_step(name):
    return next(s for s in ACTION["runs"]["steps"] if s.get("name") == name)


class ShapeTests(unittest.TestCase):
    def test_the_input_is_off_by_default(self):
        self.assertEqual(INPUTS["store-screenshots"]["type"], "boolean")
        self.assertIs(INPUTS["store-screenshots"]["default"], False)

    def test_the_set_is_this_run_s_own_artifact(self):
        """The walkthrough ran in this workflow, so its captures are the tagged version's; no
        site, no release and no other run is consulted."""
        for job, target, upload in TARGETS:
            self.assertNotIn("website", JOBS[job]["needs"], job)
            download = steps(job)[DOWNLOAD]
            self.assertEqual(" ".join(str(download["if"]).split()), "${{ inputs.store-screenshots }}")
            # One artifact per device profile (`screenshots-<target>`, `screenshots-<target>-<slug>`),
            # each in its own directory: a merged download garbled the two profiles' colliding
            # `<target>/gallery.json` and the listing lost its marks.
            self.assertIn(f"screenshots-{target}*", download["with"]["pattern"])
            self.assertIn("store-flavor", download["with"]["pattern"], "a flavor submission takes the flavor's captures")
            self.assertEqual(download["with"]["path"], "shots-in")
            self.assertNotIn("merge-multiple", download["with"])
            index = steps(job)[INDEX]
            self.assertEqual(" ".join(str(index["if"]).split()), "${{ inputs.store-screenshots }}")
            self.assertEqual(index["env"]["TARGET"], target)
            names = [s.get("name") for s in JOBS[job]["steps"]]
            self.assertLess(names.index("Set up the day CLI"), names.index(INDEX))
            self.assertLess(names.index(INDEX), names.index(upload))

    def test_every_upload_goes_through_the_shared_action(self):
        """The App Fair's queue uploads with the same action, so the two pipelines stage,
        check and run the lane the same way."""
        for job, target, upload in TARGETS:
            step = steps(job)[upload]
            self.assertEqual(step["uses"], STORE_UPLOAD)
            self.assertEqual(step["with"]["target"], target)
            self.assertEqual(step["with"]["package"], "${{ steps.package.outputs.path }}")
            self.assertIn("upload-lane", step["with"]["lane"])
            self.assertEqual(step["with"]["screenshots"], "${{ steps.shots.outputs.index }}")
            self.assertEqual(step["with"]["rules"], "${{ steps.rules.outputs.path }}")
            self.assertEqual(step["with"]["flavor"], "${{ inputs.store-flavor }}")
            # No `day store stage` or fastlane call of the job's own.
            for s in JOBS[job]["steps"]:
                self.assertNotIn("store stage", str(s.get("run", "")))
                self.assertNotIn("fastlane", str(s.get("run", "")))
        ios = steps("appstore-ios")["Upload to the App Store"]["with"]
        self.assertIn("secrets.DAY_ASC_KEY_B64", ios["asc-key-b64"])
        play = steps("playstore-android")["Upload to Google Play"]["with"]
        self.assertIn("secrets.DAY_PLAY_JSON_KEY", play["play-json-key"])
        mac = steps("appstore-macos")["Upload to the Mac App Store"]
        self.assertEqual(mac["uses"], STORE_UPLOAD)
        self.assertEqual(mac["with"]["target"], "macos-appkit")
        self.assertNotIn("screenshots", mac["with"], "the CLI stages no macOS listing")


class IndexStepTests(unittest.TestCase):
    """The job's index step, with the day CLI a recorder over a downloaded capture tree."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Resolved, since the step records `$PWD`, which is the real path.
        self.root = Path(self.temp.name).resolve()
        (self.root / "bin").mkdir()
        day = self.root / "bin/day"
        day.write_text('#!/bin/sh\necho "$@" >> "$RUNNER_TEMP/day-calls"\nexit "${DAY_EXIT:-0}"\n')
        day.chmod(0o755)
        self.env = {
            **os.environ,
            "RUNNER_TEMP": str(self.root),
            "GITHUB_OUTPUT": str(self.root / "outputs"),
            "DAY_BIN": str(day),
            "TARGET": "ios-uikit",
            "PROJECT_PATH": "",
        }

    def captures(self, *artifacts):
        """A downloaded artifact per name, each holding its own `<target>/…` tree, or a single
        artifact extracted flat when no name is given (which is how one match lands)."""
        for name in artifacts or ("",):
            base = self.root / "shots-in" / name if name else self.root / "shots-in"
            device = "ipad" if name.endswith("-ipad") else "iphone"
            shot = base / f"ios-uikit/{device}/light/home.png"
            shot.parent.mkdir(parents=True)
            shot.write_bytes(b"png")
            (base / "ios-uikit/gallery.json").write_text("{}")

    def run_step(self, job="appstore-ios", **env):
        (self.root / "outputs").write_text("")
        result = subprocess.run(
            ["bash", "-c", steps(job)[INDEX]["run"]],
            cwd=self.root, env={**self.env, **env}, text=True, capture_output=True,
        )
        calls = self.root / "day-calls"
        return result, (calls.read_text().splitlines() if calls.exists() else [])

    def outputs(self):
        return dict(line.split("=", 1) for line in (self.root / "outputs").read_text().splitlines() if "=" in line)

    def test_each_artifact_is_its_own_root_and_the_captures_one_tree(self):
        """Two profiles' artifacts both carry `ios-uikit/gallery.json`; the index reads each
        root's own, and the copied tree holds both devices' captures."""
        self.captures("screenshots-ios-uikit", "screenshots-ios-uikit-ipad")
        result, calls = self.run_step()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        index = f"{self.root}/shots/gallery.json"
        self.assertEqual(len(calls), 1, calls)
        roots = calls[0].split("--screenshot-paths ")[1].split(" --out ")[0].split()
        self.assertEqual(sorted(roots), ["shots-in/screenshots-ios-uikit", "shots-in/screenshots-ios-uikit-ipad"])
        self.assertTrue(calls[0].startswith("--project . screenshot index ") and calls[0].endswith(" --out shots/gallery.json"), calls[0])
        self.assertEqual(self.outputs()["index"], index)
        self.assertTrue((self.root / "shots/ios-uikit/iphone/light/home.png").is_file())
        self.assertTrue((self.root / "shots/ios-uikit/ipad/light/home.png").is_file())

    def test_a_single_flat_artifact_gets_a_root_of_its_own(self):
        self.captures()
        result, calls = self.run_step()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls[0], "--project . screenshot index --screenshot-paths shots-in/screenshots-ios-uikit --out shots/gallery.json")
        self.assertTrue((self.root / "shots/ios-uikit/iphone/light/home.png").is_file())

    def test_the_project_path_reaches_the_cli(self):
        self.captures()
        _, calls = self.run_step(PROJECT_PATH="apps/demo")
        self.assertTrue(all(c.startswith("--project apps/demo ") for c in calls), calls)

    def test_an_empty_artifact_fails_before_the_cli_runs(self):
        (self.root / "shots-in").mkdir()
        result, calls = self.run_step()
        self.assertEqual(result.returncode, 1)
        self.assertIn("holds no captures", result.stdout)
        self.assertEqual(calls, [])


class UploadActionTests(unittest.TestCase):
    """The action's steps, with the day CLI a recorder and a project directory to read."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "bin").mkdir()
        day = self.root / "bin/day"
        day.write_text('#!/bin/sh\necho "$@" >> "$RUNNER_TEMP/day-calls"\nexit "${DAY_EXIT:-0}"\n')
        day.chmod(0o755)
        self.project = self.root / "app"
        self.project.mkdir()
        (self.project / "Day.toml").write_text("[app]\nid = \"x\"\n")
        (self.root / "index.json").write_text("{}")
        self.env = {
            **os.environ,
            "RUNNER_TEMP": str(self.root),
            "GITHUB_OUTPUT": str(self.root / "outputs"),
            "DAY_BIN": str(day),
            "PP": str(self.project),
            "TARGET": "ios-uikit",
            "FLAVOR": "",
        }

    def run_step(self, name, cwd=None, **env):
        (self.root / "outputs").write_text("")
        (self.root / "day-calls").unlink(missing_ok=True)
        result = subprocess.run(
            ["bash", "-c", action_step(name)["run"]],
            cwd=cwd or self.root, env={**self.env, **env}, text=True, capture_output=True,
        )
        calls = self.root / "day-calls"
        return result, (calls.read_text().splitlines() if calls.exists() else [])

    def outputs(self):
        return dict(line.split("=", 1) for line in (self.root / "outputs").read_text().splitlines() if "=" in line)

    def test_the_lane_runs_in_the_app_s_own_fastlane_project_else_the_staged_tree(self):
        result, _ = self.run_step("Find the fastlane project")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.outputs()["dir"], f"{self.project}/build/day/store/ios-uikit")
        self.assertEqual(self.outputs()["own"], "false")
        (self.project / "platform/ios/fastlane").mkdir(parents=True)
        (self.project / "platform/ios/fastlane/Fastfile").write_text("")
        self.run_step("Find the fastlane project")
        self.assertEqual(self.outputs()["dir"], f"{self.project}/platform/ios")
        self.assertEqual(self.outputs()["own"], "true")
        (self.project / "fastlane").mkdir()
        (self.project / "fastlane/Fastfile").write_text("")
        self.run_step("Find the fastlane project")
        self.assertEqual(self.outputs()["dir"], str(self.project))

    def test_a_flavor_is_passed_only_when_the_app_carries_it(self):
        self.run_step("Find the fastlane project", FLAVOR="appfair")
        self.assertEqual(self.outputs()["flavor_args"], "")
        (self.project / "Day-appfair.toml").write_text("")
        self.run_step("Find the fastlane project", FLAVOR="appfair")
        self.assertEqual(self.outputs()["flavor_args"], "--flavor appfair")

    def test_the_screenshots_are_checked_and_refused_for_an_app_with_its_own_fastfile(self):
        result, calls = self.run_step("Check the listing's screenshots", cwd=self.project, INDEX=str(self.root / "index.json"), OWN="false", FLAVOR_ARGS="--flavor appfair")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, [f"--flavor appfair store screenshots {self.root}/index.json -p ios-uikit"])
        result, calls = self.run_step("Check the listing's screenshots", cwd=self.project, INDEX=str(self.root / "index.json"), OWN="true", FLAVOR_ARGS="")
        self.assertEqual(result.returncode, 1)
        self.assertIn("its own Fastfile", result.stdout)
        self.assertEqual(calls, [])
        result, _ = self.run_step("Check the listing's screenshots", cwd=self.project, INDEX=str(self.root / "index.json"), OWN="false", FLAVOR_ARGS="", DAY_EXIT="1")
        self.assertEqual(result.returncode, 1, "a refused set fails the upload")

    def test_staging_takes_the_index_and_the_placeholder_switch(self):
        for target in ("ios-uikit", "android-mdc"):
            _, calls = self.run_step("Stage the listing", cwd=self.project, TARGET=target, INDEX="/w/shots/gallery.json", FLAVOR_ARGS="", ALLOW="false")
            self.assertEqual(calls, [f"store stage -p {target} --screenshots /w/shots/gallery.json"])
            _, calls = self.run_step("Stage the listing", cwd=self.project, TARGET=target, INDEX="", FLAVOR_ARGS="--flavor x", ALLOW="true")
            self.assertEqual(calls, [f"--flavor x store stage -p {target} --allow-placeholders"])
        result, calls = self.run_step("Stage the listing", cwd=self.project, TARGET="macos-appkit", INDEX="", FLAVOR_ARGS="", ALLOW="false")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no lanes for macos-appkit", result.stdout)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
