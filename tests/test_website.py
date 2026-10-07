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

    def theme_config(self, text):
        site = self.root / "app/website"
        site.mkdir(parents=True, exist_ok=True)
        (site / "site.toml").write_text('host = "https://example.test"\n' + text)

    def test_existing_site_selects_no_theme(self):
        self.theme_config("")
        result = self.run_step("Select the website theme")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "outputs").read_text(), "repository=\nref=main\ncustomization=false\n")

    def test_repository_theme_and_pinned_ref(self):
        self.theme_config('[theme]\nrepository = "appfair/appsite"\nref = "v1.2.3"\n')
        result = self.run_step("Select the website theme")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "outputs").read_text(), "repository=appfair/appsite\nref=v1.2.3\ncustomization=true\n")

    def test_local_theme_and_project_configs_require_customization_support(self):
        # Synthetic project files: exercise selection without evaluating an Astro module.
        for config in ("theme", "daysite.config.mjs", "astro.config.ts"):
            with self.subTest(config=config):
                self.theme_config('[theme]\npath = "./theme"\n' if config == "theme" else "")
                project_config = self.root / "app/website" / config
                if config != "theme":
                    project_config.write_text("// synthetic fixture")
                result = self.run_step("Select the website theme")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("customization=true", (self.root / "outputs").read_text())
                project_config.unlink(missing_ok=True)

    def test_pinned_old_template_rejects_customization_but_keeps_default(self):
        for customized in (False, True):
            with self.subTest(customized=customized):
                self.env["DAYSITE_CUSTOMIZATION"] = str(customized).lower()
                result = self.run_step("Install website customization dependencies")
                self.assertEqual(result.returncode == 0, not customized, result.stderr)
                if customized:
                    self.assertIn("update daysite-version", result.stdout)

    def test_invalid_theme_metadata_fails_before_checkout(self):
        for text in (
            'theme = "bad"\n',
            '[theme]\nrepository = "https://github.com/example/theme"\n',
            '[theme]\nrepository = ""\n',
            '[theme]\npath = 42\n',
            '[theme]\npath = ""\n',
            '[theme]\nrepository = "example/theme"\nref = ""\n',
            '[theme]\nrepository = "example/theme"\npath = "../theme"\n',
            '[theme]\nref = "injected\\noutput=value"\n',
        ):
            with self.subTest(text=text):
                self.theme_config(text)
                result = self.run_step("Select the website theme")
                self.assertNotEqual(result.returncode, 0)

    def test_new_and_pinned_old_template_build_commands(self):
        # Execute the workflow body with synthetic commands; no actual build or deployment.
        for new_template in (False, True):
            with self.subTest(new_template=new_template):
                scripts = self.root / "scripts"
                scripts.mkdir(exist_ok=True)
                if new_template:
                    (scripts / "build-site.mjs").write_text("// synthetic fixture")
                for name in ("node", "npx"):
                    command = self.root / "bin" / name
                    command.write_text('#!/bin/sh\nprintf "%s" "$*" > "$RUNNER_TEMP/' + name + '-args"\n')
                    command.chmod(0o755)
                result = self.run_step("Build the site")
                self.assertEqual(result.returncode, 0, result.stderr)
                command = "node" if new_template else "npx"
                self.assertEqual((self.root / (command + "-args")).read_text(),
                                 "scripts/build-site.mjs build" if new_template else "astro build")

    def channels(self, release=False, prerelease=False, main=True):
        channels = []
        if release:
            channels.append({"development": False, "webapp": "webapp", "tag": "v0.4.2"})
        if prerelease:
            channels.append(
                {
                    "development": False,
                    "prerelease": True,
                    "webapp": "prerelease/webapp" if release else "webapp",
                    "tag": "v0.4.3-beta.1",
                }
            )
        if main:
            channels.append(
                {"development": True, "webapp": "main/webapp" if channels else "webapp"}
            )
        for channel in channels:
            if "tag" in channel:
                # What the release lookups write: each release's asset list, under its tag.
                (self.root / f"assets-{channel['tag']}.json").write_text(
                    json.dumps([{"name": f"demo-{channel['tag']}-web-dom.zip", "size": 1}])
                )
        (self.root / "site/channels.json").write_text(json.dumps({"channels": channels}))

    def serve_release_downloads(self):
        # A `gh release download TAG --dir DIR --clobber --pattern NAME` that writes a web dist
        # whose index.html names the tag, so a test can see which release landed where.
        gh = self.root / "bin/gh"
        gh.write_text(
            '#!/bin/sh\n'
            '[ "$1 $2" = "release download" ] || exit 1\n'
            'mkdir -p "$5" && cd "$5" && echo "$3" > index.html && zip -q "$8" index.html\n'
        )
        gh.chmod(0o755)

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

    def test_without_the_main_tab_this_runs_web_build_is_not_required(self):
        # tab-main off: nothing on the site shows this run's build, so deploy-web cannot need it.
        self.channels(release=True, main=False)
        result = self.run_step("Stage each channel's web app")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("::error::", result.stdout)

    def test_release_and_pre_release_tabs_each_host_their_own_web_app(self):
        self.channels(release=True, prerelease=True)
        self.web_dist()
        self.serve_release_downloads()
        result = self.run_step("Stage each channel's web app")
        self.assertEqual(result.returncode, 0, result.stderr)
        public = self.root / "daysite/public"
        self.assertEqual((public / "webapp/index.html").read_text().strip(), "v0.4.2")
        self.assertEqual(
            (public / "prerelease/webapp/index.html").read_text().strip(), "v0.4.3-beta.1"
        )
        self.assertEqual((public / "main/webapp/index.html").read_text(), "Lottie Demo")
        # The template's old build is gone, not left beside the release's.
        self.assertFalse((public / "webapp/stale.js").exists())

    def lookup_pre_release(self, releases, latest_at):
        (self.root / "releases-fixture.json").write_text(json.dumps(releases))
        gh = self.root / "bin/gh"
        gh.write_text(
            '#!/bin/sh\n'
            'case "$2" in\n'
            f'  */releases/latest) echo "{latest_at}" ;;\n'
            '  *) cat "$RUNNER_TEMP/releases-fixture.json" ;;\n'
            'esac\n'
        )
        gh.chmod(0o755)
        (self.root / "outputs").write_text("")
        result = self.run_step("Look up a pending pre-release")
        self.assertEqual(result.returncode, 0, result.stderr)
        return (self.root / "outputs").read_text()

    def test_the_pending_pre_release_is_the_newest_one_after_the_latest_release(self):
        def release(tag, at, prerelease=True, draft=False):
            return {
                "tag_name": tag,
                "published_at": at,
                "prerelease": prerelease,
                "draft": draft,
                "assets": [{"name": f"demo-{tag}-web-dom.zip", "size": 1, "url": "x"}],
            }

        releases = [
            release("v0.5.0-beta.3", None, draft=True),  # a draft is never shown
            release("v0.5.0-beta.2", "2026-09-26T12:00:00Z"),
            release("v0.5.0-beta.1", "2026-09-25T12:00:00Z"),
            release("v0.4.2", "2026-09-24T12:00:00Z", prerelease=False),
            release("v0.4.2-beta.1", "2026-09-20T12:00:00Z"),  # a beta of a released version
        ]
        outputs = self.lookup_pre_release(releases, "2026-09-24T12:00:00Z")
        self.assertIn("tag=v0.5.0-beta.2", outputs)
        assets = json.loads((self.root / "assets-v0.5.0-beta.2.json").read_text())
        self.assertEqual(assets, [{"name": "demo-v0.5.0-beta.2-web-dom.zip", "size": 1}])

        # Once the beta is released, nothing is pending, and no tab is offered.
        outputs = self.lookup_pre_release(releases, "2026-09-27T12:00:00Z")
        self.assertNotIn("tag=", outputs)

    def test_site_without_web_target_can_still_build(self):
        self.channels()
        self.env["REQUIRE_WEB"] = "false"
        result = self.run_step("Stage each channel's web app")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "daysite/public/webapp").exists())


if __name__ == "__main__":
    unittest.main()
