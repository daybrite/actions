"""The iOS signing job: the build leg packs unsigned, `sign-ios` signs afterwards.

Run against a stub `day`, so nothing is signed and no keychain is touched.
"""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/dayapp.yml"
JOBS = yaml.safe_load(WORKFLOW.read_text())["jobs"]


def steps(job):
    return {step.get("name"): step for step in JOBS[job]["steps"]}


class ShapeTests(unittest.TestCase):
    def test_the_build_leg_holds_no_apple_material(self):
        """The job that runs the app's code and dayscripts packs the iOS app unsigned; a
        certificate imported there once let automatic signing mint a development certificate
        per run until the account was full."""
        build = steps("build")
        self.assertNotIn("Import the Apple signing certificate", build)
        for step in JOBS["build"]["steps"]:
            self.assertNotIn("import-codesign-certs", str(step.get("uses", "")))
        materialize = build["Materialize signing material"]
        self.assertNotIn("IOS_PROFILE_B64", materialize.get("env", {}))
        self.assertNotIn("ASC_KEY_B64", materialize.get("env", {}))
        pack = build["Pack (${{ matrix.target }})"]
        self.assertIn("--no-sign", pack["run"])
        self.assertNotIn("HAS_APPLE_CERT", str(JOBS["build"].get("env", {})))

    def test_sign_ios_mirrors_sign_macos(self):
        sign = JOBS["sign-ios"]
        self.assertEqual(sorted(sign["needs"]), ["build", "preflight"])
        condition = " ".join(str(sign["if"]).split())
        self.assertIn("!cancelled()", condition)
        self.assertIn("release == 'true'", condition)
        self.assertIn("promotion != 'true'", condition)
        names = [s.get("name") for s in sign["steps"]]
        for name in ("Check the signing material is present", "Download the unsigned .ipa",
                     "Create an ephemeral signing keychain", "Set up the day CLI", "Sign the .ipa",
                     "Upload the signed package", "Destroy the signing keychain"):
            self.assertIn(name, names)
        self.assertLess(names.index("Sign the .ipa"), names.index("Upload the signed package"))
        upload = steps("sign-ios")["Upload the signed package"]
        self.assertEqual(upload["with"]["name"], "${{ inputs.artifact-prefix }}dist-ios-uikit")
        self.assertIn("day sign apply", steps("sign-ios")["Sign the .ipa"]["run"])
        # No checkout: the job never sees repository code.
        self.assertFalse(any(str(s.get("uses", "")).startswith("actions/checkout") for s in sign["steps"]))

    def test_the_release_and_the_upload_wait_for_the_signed_package(self):
        self.assertIn("sign-ios", JOBS["release"]["needs"])
        self.assertIn("needs.sign-ios.result == 'skipped'", " ".join(str(JOBS["release"]["if"]).split()))
        self.assertIn("sign-ios", JOBS["appstore-ios"]["needs"])


class SignStepTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "bin").mkdir()
        (self.root / "dist").mkdir()
        day = self.root / "bin/day"
        day.write_text('#!/bin/sh\necho "$@" >> "$RUNNER_TEMP/day-calls"\n'
                       'out=""; while [ $# -gt 0 ]; do [ "$1" = --out ] && out="$2"; shift; done\n'
                       '[ -n "$out" ] && printf signed > "$out"\n')
        day.chmod(0o755)
        self.ipa = self.root / "dist/day-showcase-ios-uikit-unsigned.ipa"
        self.ipa.write_bytes(b"unsigned")
        for side in ("buildinfo.json", "sbom-cdx.json"):
            (self.root / f"dist/day-showcase-ios-uikit-unsigned.ipa.{side}").write_text("{}")
        self.env = {
            **os.environ,
            "RUNNER_TEMP": str(self.root),
            "DAY_BIN": str(day),
            "IPA": str(self.ipa),
            "SIGNED": str(self.root / "dist/day-showcase-ios-uikit.ipa"),
            "SIGN_ID": "0FD8A837309AC6BC2675F014736B8BE4E9AEF17C",
        }

    def test_the_signed_ipa_replaces_the_unsigned_one_and_keeps_its_sidecars(self):
        result = subprocess.run(
            ["bash", "-c", steps("sign-ios")["Sign the .ipa"]["run"]],
            cwd=self.root, env=self.env, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = (self.root / "day-calls").read_text().splitlines()
        self.assertEqual(calls, [
            f"sign apply {self.ipa} --out {self.root}/dist/day-showcase-ios-uikit.ipa "
            f"--profile {self.root}/appstore.mobileprovision --identity 0FD8A837309AC6BC2675F014736B8BE4E9AEF17C"
        ])
        self.assertEqual(sorted(p.name for p in (self.root / "dist").iterdir()), [
            "day-showcase-ios-uikit.ipa",
            "day-showcase-ios-uikit.ipa.buildinfo.json",
            "day-showcase-ios-uikit.ipa.sbom-cdx.json",
        ])

    def test_a_signer_that_wrote_nothing_fails_the_job(self):
        (self.root / "bin/day").write_text("#!/bin/sh\nexit 0\n")
        result = subprocess.run(
            ["bash", "-c", steps("sign-ios")["Sign the .ipa"]["run"]],
            cwd=self.root, env=self.env, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("wrote no", result.stdout)
        self.assertTrue(self.ipa.exists(), "the unsigned package is kept when signing fails")


if __name__ == "__main__":
    unittest.main()
