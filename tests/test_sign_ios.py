"""The iOS signing job: the build leg packs unsigned, `sign-ios` signs afterwards through the
`sign-package` action.

The action's steps run against a stub `day`, so nothing is signed and no keychain is touched.
"""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/dayapp.yml"
JOBS = yaml.safe_load(WORKFLOW.read_text())["jobs"]
ACTION = yaml.safe_load((ROOT / ".github/actions/sign-package/action.yml").read_text())
SIGN_PACKAGE = "daybrite/actions/.github/actions/sign-package@main"


def steps(job):
    return {step.get("name"): step for step in JOBS[job]["steps"]}


def action_step(name):
    return next(s for s in ACTION["runs"]["steps"] if s.get("name") == name)


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

    def test_sign_ios_signs_through_the_shared_action(self):
        sign = JOBS["sign-ios"]
        self.assertEqual(sorted(sign["needs"]), ["build", "preflight"])
        condition = " ".join(str(sign["if"]).split())
        self.assertIn("!cancelled()", condition)
        self.assertIn("release == 'true'", condition)
        self.assertIn("promotion != 'true'", condition)
        names = [s.get("name") for s in sign["steps"]]
        for name in ("Check the signing material is present", "Download the unsigned .ipa",
                     "Set up the day CLI", "Sign the .ipa", "Upload the signed package"):
            self.assertIn(name, names)
        self.assertLess(names.index("Set up the day CLI"), names.index("Sign the .ipa"))
        self.assertLess(names.index("Sign the .ipa"), names.index("Upload the signed package"))
        signing = steps("sign-ios")["Sign the .ipa"]
        self.assertEqual(signing["uses"], SIGN_PACKAGE)
        self.assertEqual(signing["with"]["target"], "ios-uikit")
        for key in ("apple-cert-p12", "apple-cert-password", "apple-profile-b64"):
            self.assertIn("secrets.", signing["with"][key], key)
        upload = steps("sign-ios")["Upload the signed package"]
        self.assertEqual(upload["with"]["name"], "${{ inputs.artifact-prefix }}dist-ios-uikit")
        # The keychain is the action's, created and destroyed there; the job holds none itself.
        self.assertFalse(any("security create-keychain" in str(s.get("run", "")) for s in sign["steps"]))
        # No checkout: the job never sees repository code.
        self.assertFalse(any(str(s.get("uses", "")).startswith("actions/checkout") for s in sign["steps"]))

    def test_the_action_destroys_its_keychain_whatever_happened(self):
        last = ACTION["runs"]["steps"][-1]
        self.assertEqual(last["name"], "Destroy the signing keychain")
        self.assertEqual(str(last["if"]), "always()")
        self.assertIn("security delete-keychain", last["run"])
        for key in ("target", "package"):
            self.assertTrue(ACTION["inputs"][key]["required"], key)

    def test_the_release_and_the_upload_wait_for_the_signed_package(self):
        self.assertIn("sign-ios", JOBS["release"]["needs"])
        self.assertIn("needs.sign-ios.result == 'skipped'", " ".join(str(JOBS["release"]["if"]).split()))
        self.assertIn("sign-ios", JOBS["appstore-ios"]["needs"])


class SignStepTests(unittest.TestCase):
    """The action's steps, with the day CLI a recorder."""

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
            "GITHUB_OUTPUT": str(self.root / "outputs"),
            "DAY_BIN": str(day),
            "PACKAGE": str(self.ipa),
            "SIGNED": str(self.root / "dist/day-showcase-ios-uikit.ipa"),
            "SIGN_PROFILE": str(self.root / "appstore.mobileprovision"),
            "SIGN_ID": "0FD8A837309AC6BC2675F014736B8BE4E9AEF17C",
        }

    def run_step(self, name, **env):
        (self.root / "outputs").write_text("")
        return subprocess.run(
            ["bash", "-c", action_step(name)["run"]],
            cwd=self.root, env={**self.env, **env}, text=True, capture_output=True,
        )

    def test_an_unsigned_package_is_named_after_the_signed_one(self):
        result = self.run_step("Name the signed package", RENAME="true")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.root / "outputs").read_text().strip(), f"signed={self.root}/dist/day-showcase-ios-uikit.ipa")
        result = self.run_step("Name the signed package", RENAME="false")
        self.assertEqual((self.root / "outputs").read_text().strip(), f"signed={self.ipa}")
        result = self.run_step("Name the signed package", RENAME="true", PACKAGE=str(self.root / "dist/app.aab"))
        self.assertEqual((self.root / "outputs").read_text().strip(), f"signed={self.root}/dist/app.aab", "a package not marked unsigned keeps its name")

    def test_the_signed_ipa_replaces_the_unsigned_one_and_keeps_its_sidecars(self):
        result = self.run_step("Sign the .ipa")
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
        result = self.run_step("Sign the .ipa")
        self.assertEqual(result.returncode, 1)
        self.assertIn("wrote no", result.stdout)
        self.assertTrue(self.ipa.exists(), "the unsigned package is kept when signing fails")

    def test_the_android_package_is_signed_with_the_keystore_and_its_apks_best_effort(self):
        aab = self.root / "dist/app-android-mdc.aab"
        aab.write_bytes(b"aab")
        apk = self.root / "dist/app-android-mdc.apk"
        apk.write_bytes(b"apk")
        (self.root / "bin/day").write_text(
            '#!/bin/sh\necho "$@" >> "$RUNNER_TEMP/day-calls"\n'
            'case "$3" in *.apk) exit 1 ;; esac\nexit 0\n')
        import base64
        result = self.run_step(
            "Sign the Android package", PACKAGE=str(aab), SIGNED=str(aab),
            KEYSTORE_B64=base64.b64encode(b"ks").decode(), KEY_ALIAS="upload", ALSO=f"{apk}\n",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = (self.root / "day-calls").read_text().splitlines()
        self.assertEqual(calls[0], f"sign apply {aab} --out {aab} --keystore {self.root}/upload.keystore --key-alias upload")
        self.assertEqual(calls[1], f"sign apply {apk} --keystore {self.root}/upload.keystore --key-alias upload")
        self.assertTrue(aab.exists())
        self.assertFalse(apk.exists(), "an .apk this runner cannot sign is left out")
        self.assertIn("could not be signed", result.stdout)
        self.assertFalse((self.root / "upload.keystore").exists(), "the keystore does not outlive the step")


if __name__ == "__main__":
    unittest.main()
