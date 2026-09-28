"""Signing after the build, on one rule for every signer: a release that is not a promotion,
the whole build matrix green, and the platform's material there. The store rows (`sign`, one
per platform whose material exists) sign through the `sign-package` action what the build leg
packed unsigned for them; `sign-macos` signs the .app from its environment's material.
The action's steps run against a stub `day`, so nothing is signed and no keychain is touched.
"""
import base64
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
SIGN_PACKAGE = "daybrite/actions/.github/actions/sign-package@v1"
STORE_TARGETS = ("ios-uikit", "android-mdc", "harmony-arkui")


def steps(job):
    return {step.get("name"): step for step in JOBS[job]["steps"]}


def action_step(name):
    return next(s for s in ACTION["runs"]["steps"] if s.get("name") == name)


def condition(job):
    return " ".join(str(JOBS[job]["if"]).split())


class ShapeTests(unittest.TestCase):
    def test_the_build_leg_holds_no_signing_material(self):
        """The job that runs the app's code and dayscripts holds no key for any platform: not
        an Apple certificate (imported there once, automatic signing minted a development
        certificate per run until the account was full), not the Android upload keystore, not
        the HarmonyOS material. It packs every store package unsigned for the sign job."""
        build = steps("build")
        self.assertNotIn("Materialize signing material", build)
        self.assertNotIn("Import the Apple signing certificate", build)
        for step in JOBS["build"]["steps"]:
            self.assertNotIn("import-codesign-certs", str(step.get("uses", "")))
            self.assertNotIn("secrets.DAY_", str(step.get("env", {})), step.get("name"))
        env = str(JOBS["build"].get("env", {}))
        for name in ("DAY_ANDROID_", "DAY_OHOS_", "DAY_KS_PASS", "DAY_KEY_PASS", "DAY_APPLE_", "DAY_IOS_PROFILE"):
            self.assertNotIn(name, env, name)
        pack = build["Pack (${{ matrix.target }})"]["run"]
        self.assertIn("--no-sign", pack)
        # Unsigned exactly where a sign row will sign, so a package without a signer keeps its
        # dev tier (a dev-signed .apk installs; an unsigned one does not).
        self.assertIn('case " ${{ needs.preflight.outputs.sign-targets }} " in', pack)
        self.assertIn('*" ${{ matrix.target }} "*) NO_SIGN="--no-sign"', pack)

    def test_preflight_plans_a_sign_row_only_where_the_material_exists(self):
        """Presence crosses into preflight as booleans from the base64 blobs alone: a short
        secret referenced there (an alias, a password) would be masked wherever its text
        recurs, and an output that carries one is dropped with the matrix in it."""
        plan = next(s for s in JOBS["preflight"]["steps"] if s.get("id") == "plan")
        env = plan["env"]
        self.assertEqual(env["HAS_IOS_MATERIAL"], "${{ secrets.DAY_APPLE_CERT_P12 != '' && secrets.DAY_IOS_PROFILE_B64 != '' }}")
        self.assertEqual(env["HAS_ANDROID_MATERIAL"], "${{ secrets.DAY_ANDROID_KEYSTORE_B64 != '' }}")
        self.assertIn("DAY_OHOS_KEYSTORE_B64", env["HAS_OHOS_MATERIAL"])
        for value in env.values():
            for short in ("ALIAS", "PASS", "JSON", "KEY_ID", "ISSUER", "TEAM"):
                self.assertNotIn(short, str(value), value)
        run = plan["run"]
        self.assertIn('if [ "$rel" = true ] && [ "$promotion" != true ]; then', run)
        self.assertIn('(.target == "ios-uikit" and $ios)', run)
        self.assertIn('(.target == "android-mdc" and $android)', run)
        self.assertIn('(.target == "harmony-arkui" and $ohos)', run)
        self.assertLess(run.index('echo "promotion=$promotion"'), run.index("sign_matrix="))

    def test_every_signer_shares_one_gate(self):
        """sign-macos and the store rows run on the same terms: a release, not a promotion,
        the whole build green, and their material there (the named environment, or the
        planned rows). A red leg anywhere holds every signer, the release and every upload."""
        for job in ("sign-macos", "sign"):
            cond = condition(job)
            for clause in ("!cancelled()", "needs.build.result == 'success'", "release == 'true'",
                           "promotion != 'true'"):
                self.assertIn(clause, cond, job)
            self.assertEqual(sorted(JOBS[job]["needs"]), ["build", "preflight"], job)
        self.assertIn("inputs.signing-environment != ''", condition("sign-macos"))
        self.assertIn("sign-targets != ''", condition("sign"))
        self.assertEqual(JOBS["sign-macos"]["environment"], "${{ inputs.signing-environment }}")

    def test_a_sign_row_fails_on_incomplete_material_instead_of_degrading(self):
        check = steps("sign")["Check the signing material is complete"]
        self.assertIn("::error::", check["run"])
        self.assertIn('[ "$complete" != true ]', check["run"])
        self.assertIn("DAY_APPLE_CERT_PASSWORD != ''", check["env"]["HAS_APPLE"])
        self.assertIn("DAY_KEY_PASS != ''", check["env"]["HAS_ANDROID"])
        for step in JOBS["sign"]["steps"]:
            self.assertNotIn("steps.material", str(step.get("if", "")), step.get("name"))

    def test_every_job_that_runs_repository_code_holds_a_read_only_token(self):
        """The token follows the keys: the build legs and every other job that runs the
        repository's code narrow the caller's grant to `contents: read`, so a build script
        cannot push, edit a release or mint an OIDC credential with it. The three jobs that
        need the caller's write grants declare nothing (a called job may narrow a grant, never
        widen it, and a read-only caller such as day's own CI must still run this workflow)."""
        for job in ("preflight", "build", "validate", "stores", "sign", "sign-macos",
                    "appstore-ios", "appstore-macos", "playstore-android"):
            self.assertEqual(JOBS[job].get("permissions"), {"contents": "read"}, job)
        for job in ("release", "pages", "website"):
            self.assertNotIn("permissions", JOBS[job], job)

    def test_an_upload_without_its_signer_is_refused_where_it_is_decided(self):
        decide = steps("stores")["Decide the store uploads"]
        self.assertEqual(decide["env"]["SIGN_TARGETS"], "${{ needs.preflight.outputs.sign-targets }}")
        self.assertEqual(decide["env"]["SIGNING_ENVIRONMENT"], "${{ inputs.signing-environment }}")
        for line in ('signer upload_ios "$ios_signed"', 'signer upload_play "$android_signed"',
                     'signer upload_macos "$macos_signed"', "is on but its package will not be signed"):
            self.assertIn(line, decide["run"])

    def test_preflight_plans_one_signing_row_per_store_target(self):
        outputs = JOBS["preflight"]["outputs"]
        self.assertEqual(outputs["sign-matrix"], "${{ steps.plan.outputs.sign_matrix }}")
        self.assertEqual(outputs["sign-targets"], "${{ steps.plan.outputs.sign_targets }}")
        plan = next(s for s in JOBS["preflight"]["steps"] if s.get("id") == "plan")["run"]
        self.assertIn("sign_matrix=", plan)
        for target in STORE_TARGETS:
            self.assertIn(f'.target == "{target}"', plan)
        self.assertIn('if .target == "ios-uikit" then $mac else "ubuntu-latest"', plan)

    def test_the_sign_job_signs_each_target_with_only_its_own_material(self):
        sign = JOBS["sign"]
        self.assertEqual(sorted(sign["needs"]), ["build", "preflight"])
        cond = condition("sign")
        for clause in ("!cancelled()", "needs.build.result == 'success'", "release == 'true'",
                       "promotion != 'true'", "sign-targets != ''"):
            self.assertIn(clause, cond)
        self.assertEqual(sign["strategy"]["matrix"], "${{ fromJSON(needs.preflight.outputs.sign-matrix) }}")
        self.assertEqual(sign["runs-on"], "${{ matrix.os }}")
        names = [s.get("name") for s in sign["steps"]]
        for name in ("Check the signing material is complete", "Download the unsigned package",
                     "Set up the day CLI", "Sign the package", "Upload the signed package"):
            self.assertIn(name, names)
        self.assertLess(names.index("Set up the day CLI"), names.index("Sign the package"))
        self.assertLess(names.index("Sign the package"), names.index("Upload the signed package"))
        signing = steps("sign")["Sign the package"]
        self.assertEqual(signing["uses"], SIGN_PACKAGE)
        self.assertEqual(signing["with"]["target"], "${{ matrix.target }}")
        # Every secret is gated on the row's target, so an Android row never receives an Apple
        # certificate and the other way round.
        gates = {"apple-cert-p12": "ios-uikit", "apple-cert-password": "ios-uikit", "apple-profile-b64": "ios-uikit",
                 "keystore-b64": "android-mdc", "key-alias": "android-mdc", "keystore-password": "android-mdc",
                 "key-password": "android-mdc", "ohos-keystore-b64": "harmony-arkui", "ohos-cert-b64": "harmony-arkui",
                 "ohos-profile-b64": "harmony-arkui", "ohos-key-alias": "harmony-arkui",
                 "ohos-keystore-password": "harmony-arkui", "ohos-key-password": "harmony-arkui"}
        for key, target in gates.items():
            value = signing["with"][key]
            self.assertIn("secrets.", value, key)
            self.assertIn(f"matrix.target == '{target}' &&", value, key)
        upload = steps("sign")["Upload the signed package"]
        self.assertEqual(upload["with"]["name"], "${{ inputs.artifact-prefix }}dist-${{ matrix.target }}")
        self.assertTrue(upload["with"]["overwrite"])
        # The keychain is the action's; the job holds none and checks out no code.
        self.assertFalse(any("security create-keychain" in str(s.get("run", "")) for s in sign["steps"]))
        self.assertFalse(any(str(s.get("uses", "")).startswith("actions/checkout") for s in sign["steps"]))
        sdk = steps("sign")["Set up the HarmonyOS SDK"]
        self.assertIn("matrix.target == 'harmony-arkui'", str(sdk["if"]))

    def test_the_action_signs_all_three_store_platforms(self):
        for key in ("target", "package"):
            self.assertTrue(ACTION["inputs"][key]["required"], key)
        for key in ("ohos-keystore-b64", "ohos-cert-b64", "ohos-profile-b64", "ohos-key-alias",
                    "ohos-keystore-password", "ohos-key-password"):
            self.assertIn(key, ACTION["inputs"], key)
        check = ACTION["runs"]["steps"][0]["run"]
        self.assertIn("ios-uikit|android-mdc|harmony-arkui)", check)
        self.assertEqual(str(action_step("Sign the HarmonyOS package")["if"]), "inputs.target == 'harmony-arkui'")

    def test_the_action_destroys_its_keychain_whatever_happened(self):
        last = ACTION["runs"]["steps"][-1]
        self.assertEqual(last["name"], "Destroy the signing keychain")
        self.assertEqual(str(last["if"]), "always()")
        self.assertIn("security delete-keychain", last["run"])

    def test_every_store_upload_waits_for_a_green_build_and_its_signer(self):
        """The same gate for every store: the whole build succeeded and the signer ran; a
        release with a failed leg or an unsigned package stands down instead of failing late
        with a message about missing secrets (Day-Showcase v0.4.19, 2026-09-28)."""
        self.assertIn("sign", JOBS["release"]["needs"])
        self.assertIn("needs.sign.result == 'skipped'", condition("release"))
        for job, signer in (("appstore-ios", "sign"), ("playstore-android", "sign"), ("appstore-macos", "sign-macos")):
            self.assertIn(signer, JOBS[job]["needs"], job)
            cond = condition(job)
            self.assertIn("needs.build.result == 'success'", cond, job)
            self.assertIn(f"needs.{signer}.result == 'success'", cond, job)


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

    def test_the_harmony_package_is_signed_from_its_three_files_which_do_not_outlive_the_step(self):
        hap = self.root / "dist/app-harmony-arkui-unsigned.hap"
        hap.write_bytes(b"hap")
        signed = self.root / "dist/app-harmony-arkui.hap"
        b64 = base64.b64encode(b"x").decode()
        result = self.run_step(
            "Sign the HarmonyOS package", PACKAGE=str(hap), SIGNED=str(signed),
            KEYSTORE_B64=b64, CERT_B64=b64, PROFILE_B64=b64, KEY_ALIAS="release",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = (self.root / "day-calls").read_text().splitlines()
        self.assertEqual(calls, [
            f"sign apply {hap} --out {signed} --keystore {self.root}/ohos.p12 --cert {self.root}/ohos.cer "
            f"--profile {self.root}/ohos.p7b --key-alias release"
        ])
        self.assertTrue(signed.exists() and not hap.exists())
        for name in ("ohos.p12", "ohos.cer", "ohos.p7b"):
            self.assertFalse((self.root / name).exists(), name)
        result = self.run_step("Sign the HarmonyOS package", PACKAGE=str(signed), SIGNED=str(signed),
                               KEYSTORE_B64=b64, CERT_B64="", PROFILE_B64=b64, KEY_ALIAS="release")
        self.assertEqual(result.returncode, 1)
        self.assertIn("needs ohos-keystore-b64, ohos-cert-b64 and ohos-profile-b64", result.stdout)


if __name__ == "__main__":
    unittest.main()
