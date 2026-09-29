import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("arkweb", Path(__file__).resolve().parents[1] / ".github/actions/setup-harmony-webview/arkweb.py")
arkweb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(arkweb)


class RuntimeGuards(unittest.TestCase):
    def test_cached_runtime_is_verified_without_downloading(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            (cache / "ArkWebCore.hap").write_bytes(b"cached fixture")
            with patch.object(arkweb, "validate_hap") as validate, patch.object(arkweb.subprocess, "run") as run:
                self.assertEqual(arkweb.prepare(cache), cache / "ArkWebCore.hap")
                validate.assert_called_once_with(cache / "ArkWebCore.hap")
                run.assert_not_called()

    def test_corrupt_cache_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            (cache / "ArkWebCore.hap").write_bytes(b"corrupted")
            with patch.object(arkweb.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "Unexpected SHA256"):
                    arkweb.prepare(cache)
                run.assert_not_called()

    def test_failed_extraction_does_not_publish_cache_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            def fail_extract(archive, output):
                output.write_bytes(b"incomplete")
                raise ValueError("invalid runtime")
            with patch.object(arkweb.subprocess, "run"), patch.object(arkweb, "extract", side_effect=fail_extract):
                with self.assertRaisesRegex(ValueError, "invalid runtime"):
                    arkweb.prepare(cache)
            self.assertEqual(list(cache.iterdir()), [])

    def test_unknown_wrapper_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported EGL wrapper"):
            arkweb.patch_egl(b"EGL_EXT_create_context_robustness ")

    def test_wrong_archive_is_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "wrong.zip"
            archive.write_bytes(b"untrusted archive")
            with self.assertRaisesRegex(ValueError, "Unexpected SHA256"):
                arkweb.extract(archive, Path(directory) / "output.hap")

    def test_sandbox_only_removes_unavailable_internal_namespaces(self):
        config = {"common": [{"app-base": [{"sandbox-ns-flags": ["net"]}]}],
                  "individual": [{key: [{"sandbox-ns-flags": ["pid", "net", "mnt"]}]
                                  for key in ("__internal__.com.ohos.render", "__internal__.com.ohos.gpu")} ]}
        patched = arkweb.patch_sandbox(copy.deepcopy(config), ["mnt", "net"])
        self.assertEqual(patched["common"], config["common"])
        for rules in patched["individual"][0].values():
            self.assertEqual(rules[0]["sandbox-ns-flags"], ["net", "mnt"])
        self.assertEqual(arkweb.patch_sandbox(copy.deepcopy(patched), ["mnt", "net"]), patched)

    def test_unknown_sandbox_layout_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Missing renderer/GPU"):
            arkweb.patch_sandbox({}, [])



if __name__ == "__main__":
    unittest.main()
