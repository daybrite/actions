"""Exercise the website workflow's shell steps without publishing or downloading anything."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/dayapp.yml"
STEPS = {
    step.get("name"): step
    for step in yaml.safe_load(WORKFLOW.read_text())["jobs"]["website"]["steps"]
}


class WebsiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ("site", "web-in", "bin", "daysite/public/webapp"):
            (self.root / directory).mkdir(parents=True)
        (self.root / "daysite/public/webapp/index.html").write_text("Day-Skies")
        (self.root / "daysite/public/webapp/stale.js").write_text("old build")
        (self.root / "release-assets.json").write_text("[]")
        self.env = {
            **os.environ,
            "PATH": f"{self.root / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "RUNNER_TEMP": str(self.root),
            "GITHUB_OUTPUT": str(self.root / "outputs"),
            "GITHUB_REPOSITORY": "daybrite/day-piece-lottie",
            "SITE_DIR": str(self.root / "site"),
            "REQUIRE_WEB": "true",
            "TAG": "",
        }
        gh = self.root / "bin/gh"
        gh.write_text('#!/bin/sh\necho called > "$RUNNER_TEMP/gh-called"\nexit 1\n')
        gh.chmod(0o755)

    def run_step(self, name):
        return subprocess.run(
            ["bash", "-c", STEPS[name]["run"]],
            cwd=self.root,
            env=self.env,
            text=True,
            capture_output=True,
        )

    def channels(self, release=False, main=True):
        channels = []
        if release:
            channels.append({"development": False, "webapp": "webapp"})
            self.env["TAG"] = "v0.4.2"
        if main:
            channels.append(
                {"development": True, "webapp": "main/webapp" if release else "webapp"}
            )
        (self.root / "site/channels.json").write_text(json.dumps({"channels": channels}))

    def web_dist(self):
        with zipfile.ZipFile(self.root / "web-in/lottie-demo-web-dom.zip", "w") as archive:
            archive.writestr("index.html", "Lottie Demo")
            archive.writestr("app.wasm", b"lottie-build")

    def test_project_without_release_replaces_template_with_this_runs_webapp(self):
        self.channels()
        self.web_dist()
        result = self.run_step("Stage each channel's web app")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.root / "daysite/public/webapp/index.html").read_text(), "Lottie Demo"
        )
        self.assertEqual(
            (self.root / "daysite/public/webapp/app.wasm").read_bytes(), b"lottie-build"
        )
        self.assertFalse((self.root / "daysite/public/webapp/stale.js").exists())

    def test_release_without_web_artifact_cannot_serve_template(self):
        self.channels(release=True)
        self.web_dist()
        result = self.run_step("Stage each channel's web app")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "daysite/public/webapp").exists())
        self.assertEqual(
            (self.root / "daysite/public/main/webapp/index.html").read_text(), "Lottie Demo"
        )

    def test_required_web_artifact_cannot_be_missing(self):
        self.channels()
        result = self.run_step("Stage each channel's web app")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("::error::deploy-web requires", result.stdout)

    def test_required_web_destination_cannot_be_missing(self):
        self.channels(main=False)
        self.web_dist()
        result = self.run_step("Stage each channel's web app")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("::error::deploy-web requires", result.stdout)

    def test_site_without_web_target_can_still_build(self):
        self.channels()
        self.env["REQUIRE_WEB"] = "false"
        result = self.run_step("Stage each channel's web app")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "daysite/public/webapp").exists())


if __name__ == "__main__":
    unittest.main()
