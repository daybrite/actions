"""The store-upload jobs' screenshot steps, run against a stub day CLI: nothing is fetched.

`store-screenshots` takes the listing's screenshots from this run's own screenshot artifact, so
the step indexes the download, checks the set with the day CLI, and hands the staging step the
index's path.
"""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/dayapp.yml"
DOC = yaml.safe_load(WORKFLOW.read_text())
JOBS = DOC["jobs"]
INPUTS = DOC.get("on", DOC.get(True))["workflow_call"]["inputs"]
DOWNLOAD = "Download this run's screenshots"
CHECK = "Check the listing's screenshots"
STAGE = "Stage the store listing"
TARGETS = (("appstore-ios", "ios-uikit"), ("playstore-android", "android-mdc"))


def steps(job):
    return {step.get("name"): step for step in JOBS[job]["steps"]}


class ShapeTests(unittest.TestCase):
    def test_the_input_is_off_by_default(self):
        self.assertEqual(INPUTS["store-screenshots"]["type"], "boolean")
        self.assertIs(INPUTS["store-screenshots"]["default"], False)

    def test_the_set_is_this_run_s_own_artifact(self):
        """The walkthrough ran in this workflow, so its captures are the tagged version's; no
        site, no release and no other run is consulted."""
        for job, target in TARGETS:
            self.assertNotIn("website", JOBS[job]["needs"], job)
            download = steps(job)[DOWNLOAD]
            self.assertEqual(" ".join(str(download["if"]).split()), "${{ inputs.store-screenshots }}")
            # One artifact per device profile (`screenshots-<target>`, `screenshots-<target>-<slug>`).
            self.assertIn(f"screenshots-{target}*", download["with"]["pattern"])
            self.assertIn("store-flavor", download["with"]["pattern"], "a flavor submission takes the flavor's captures")
            self.assertEqual(download["with"]["path"], "shots")
            self.assertIs(download["with"]["merge-multiple"], True)
            check = steps(job)[CHECK]
            self.assertEqual(" ".join(str(check["if"]).split()), "${{ inputs.store-screenshots }}")
            self.assertEqual(check["env"]["TARGET"], target)
            names = [s.get("name") for s in JOBS[job]["steps"]]
            self.assertLess(names.index("Set up the day CLI"), names.index(CHECK))
            self.assertLess(names.index(CHECK), names.index(STAGE))
            self.assertIn("steps.shots.outputs.index", steps(job)[STAGE]["env"]["SHOTS_INDEX"])


class CheckStepTests(unittest.TestCase):
    """The step's shell, with the day CLI a recorder over a downloaded capture tree."""

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
            "OWN_FASTFILE": "false",
        }

    def captures(self):
        shot = self.root / "shots/ios-uikit/iphone/light/home.png"
        shot.parent.mkdir(parents=True)
        shot.write_bytes(b"png")

    def run_step(self, name=CHECK, job="appstore-ios", **env):
        (self.root / "outputs").write_text("")
        result = subprocess.run(
            ["bash", "-c", steps(job)[name]["run"]],
            cwd=self.root,
            env={**self.env, **env},
            text=True,
            capture_output=True,
        )
        calls = self.root / "day-calls"
        return result, (calls.read_text().splitlines() if calls.exists() else [])

    def outputs(self):
        return dict(line.split("=", 1) for line in (self.root / "outputs").read_text().splitlines() if "=" in line)

    def test_the_download_is_indexed_checked_and_handed_on(self):
        self.captures()
        result, calls = self.run_step()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        index = f"{self.root}/shots/gallery.json"
        self.assertEqual(calls, [
            f"--project . screenshot index --screenshot-paths shots --out shots/gallery.json",
            f"--project . store screenshots {index} -p ios-uikit",
        ])
        self.assertEqual(self.outputs()["index"], index)

    def test_the_project_path_reaches_the_cli(self):
        self.captures()
        _, calls = self.run_step(PROJECT_PATH="apps/demo")
        self.assertTrue(all(c.startswith("--project apps/demo ") for c in calls), calls)

    def test_an_empty_artifact_fails_before_the_cli_runs(self):
        (self.root / "shots").mkdir()
        result, calls = self.run_step()
        self.assertEqual(result.returncode, 1)
        self.assertIn("holds no captures", result.stdout)
        self.assertEqual(calls, [])

    def test_an_app_with_its_own_fastfile_is_refused(self):
        self.captures()
        result, calls = self.run_step(OWN_FASTFILE="true")
        self.assertEqual(result.returncode, 1)
        self.assertIn("its own Fastfile", result.stdout)
        self.assertEqual(calls, [])

    def test_a_refused_set_fails_the_upload(self):
        self.captures()
        result, _ = self.run_step(DAY_EXIT="1")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("index=", (self.root / "outputs").read_text())

    def test_staging_takes_the_index_only_when_the_check_produced_one(self):
        for job, target in TARGETS:
            (self.root / "day-calls").unlink(missing_ok=True)
            _, calls = self.run_step(STAGE, job=job, SHOTS_INDEX="/w/shots/gallery.json")
            self.assertEqual(calls, [f"store stage -p {target} --screenshots /w/shots/gallery.json"])
            (self.root / "day-calls").unlink()
            _, calls = self.run_step(STAGE, job=job, SHOTS_INDEX="")
            self.assertEqual(calls, [f"store stage -p {target}"])


if __name__ == "__main__":
    unittest.main()
