"""Exercise the release job's publish step without touching GitHub.

`gh` is a stub that answers `release view` from a file and records every other call, so each
release-mode reaches the command it should and no other.
"""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/dayapp.yml"
JOBS = yaml.safe_load(WORKFLOW.read_text())["jobs"]
STEPS = {step.get("name"): step for step in JOBS["release"]["steps"]}

GH_STUB = """#!/bin/sh
if [ "$1" = release ] && [ "$2" = view ]; then
  cat "$RUNNER_TEMP/state.json"
  exit 0
fi
echo "$@" >> "$RUNNER_TEMP/gh-calls"
"""


class PublishStepTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "bin").mkdir()
        stub = self.root / "bin/gh"
        stub.write_text(GH_STUB)
        stub.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": f"{self.root / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "RUNNER_TEMP": str(self.root),
            "GITHUB_REF_NAME": "v2.3.4",
            "GITHUB_REPOSITORY": "Games-Fair/Games-Fair",
        }

    def publish(self, mode, draft=True, prerelease=False):
        (self.root / "state.json").write_text(
            json.dumps({"isDraft": draft, "isPrerelease": prerelease})
        )
        result = subprocess.run(
            ["bash", "-c", STEPS["Publish the release"]["run"]],
            cwd=self.root,
            env={**self.env, "MODE": mode},
            text=True,
            capture_output=True,
        )
        calls = self.root / "gh-calls"
        return result, (calls.read_text().splitlines() if calls.exists() else [])

    def test_publish_makes_the_release_public_and_not_a_pre_release(self):
        result, calls = self.publish("publish")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ["release edit v2.3.4 --draft=false --prerelease=false"])

    def test_pre_release_publishes_the_release_behind_the_pre_release_flag(self):
        """`releases/latest` skips it, so the site and the install URLs keep describing the
        version before it."""
        result, calls = self.publish("pre-release")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ["release edit v2.3.4 --draft=false --prerelease"])
        self.assertIn("PRE-RELEASE", result.stdout)

    def test_a_release_already_marked_latest_is_left_alone(self):
        """The maintainer promoted it; a re-run of the tag must not put it back."""
        result, calls = self.publish("pre-release", draft=False, prerelease=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [])
        self.assertIn("already the latest release", result.stdout)

    def test_a_staged_release_is_re_flagged_on_a_re_run(self):
        result, calls = self.publish("pre-release", draft=False, prerelease=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ["release edit v2.3.4 --draft=false --prerelease"])

    def test_draft_edits_nothing(self):
        result, calls = self.publish("draft")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [])
        self.assertIn("DRAFT", result.stdout)

    def test_an_unknown_mode_fails_the_job(self):
        result, _ = self.publish("whenever")
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::", result.stdout)


PLAN = [s for s in JOBS["preflight"]["steps"] if s.get("id") == "plan"][0]


class PlanTests(unittest.TestCase):
    """The preflight's release decisions, run as the workflow runs them."""

    BASE = {
        "TARGETS_IN": "web-dom",
        "TARGET_SCRIPTS_IN": "", "ANDROID_ABIS_IN": "", "IOS_PROFILES_IN": "",
        "IOS_DEVICES_IN": "", "ANDROID_PROFILES_IN": "", "ANDROID_DEVICES_IN": "",
        "TARGET_PACKAGES_IN": "", "TARGET_SETUP_IN": "", "FLAVORS_IN": "",
        "RELEASE_MODE_IN": "", "PUBLISH_RELEASE_IN": "true",
        "DEPLOY_WEB_IN": "false", "DEPLOY_WEBSITE_IN": "", "WEB_PATTERN": "",
        "DEFAULT_BRANCH": "main", "PROJECT_PATH": "",
        "GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v2.3.4", "GITHUB_EVENT_NAME": "push",
    }

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def plan(self, **overrides):
        out = self.root / "outputs"
        out.write_text("")
        result = subprocess.run(
            ["bash", "-c", PLAN["run"]],
            cwd=self.root,
            env={**os.environ, **self.BASE, **overrides, "GITHUB_OUTPUT": str(out)},
            text=True,
            capture_output=True,
        )
        values = dict(
            line.split("=", 1) for line in out.read_text().splitlines() if "=" in line
        )
        return result, values

    def test_the_older_boolean_still_decides_when_no_mode_is_named(self):
        _, values = self.plan()
        self.assertEqual(values["release_mode"], "publish")
        _, values = self.plan(PUBLISH_RELEASE_IN="false")
        self.assertEqual(values["release_mode"], "draft")

    def test_a_named_mode_wins(self):
        _, values = self.plan(RELEASE_MODE_IN="pre-release", PUBLISH_RELEASE_IN="true")
        self.assertEqual(values["release_mode"], "pre-release")
        self.assertEqual(values["release"], "true")

    def test_a_mode_that_is_not_one_of_the_three_stops_the_run(self):
        result, _ = self.plan(RELEASE_MODE_IN="prerelease")
        self.assertEqual(result.returncode, 1)
        self.assertIn("publish, pre-release or draft", result.stdout)

    def test_a_release_event_is_a_promotion(self):
        _, values = self.plan(GITHUB_EVENT_NAME="release", RELEASE_MODE_IN="pre-release")
        self.assertEqual(values["promotion"], "true")
        # The tag still reads as a release ref, which is what keeps the website job's gate on.
        self.assertEqual(values["release"], "true")

    def test_a_release_event_without_the_staging_mode_warns(self):
        result, values = self.plan(GITHUB_EVENT_NAME="release")
        self.assertEqual(values["promotion"], "true")
        self.assertIn("::warning::a release event arrived", result.stdout)

    def test_a_promotion_deploys_the_website_of_a_project_that_has_one(self):
        (self.root / "website").mkdir()
        (self.root / "website/site.toml").write_text('host = "games-fair.github.io"\n')
        _, values = self.plan(GITHUB_EVENT_NAME="release", RELEASE_MODE_IN="pre-release")
        self.assertEqual(values["website"], "true")


class PromotionGateTests(unittest.TestCase):
    """A `release: released` run rebuilds the site; it assembles no release and uploads nothing."""

    def condition(self, job):
        return " ".join(str(JOBS[job]["if"]).split())

    def test_the_release_and_upload_jobs_stand_down_on_a_promotion(self):
        for job in ("release", "stores", "sign-macos"):
            self.assertIn("promotion != 'true'", self.condition(job), job)

    def test_a_promotion_builds_only_when_there_is_a_site_to_rebuild(self):
        self.assertIn("promotion != 'true' || needs.preflight.outputs.website == 'true'",
                      self.condition("build"))

    def test_the_website_job_tolerates_the_skipped_release(self):
        """It needs the release job, and a promotion run skips it."""
        self.assertIn("needs.release.result != 'cancelled'", self.condition("website"))


if __name__ == "__main__":
    unittest.main()
