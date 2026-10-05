import copy
import importlib.util
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("arkweb", Path(__file__).resolve().parents[1] / ".github/actions/setup-harmony-webview/arkweb.py")
arkweb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(arkweb)


class RuntimeGuards(unittest.TestCase):
    def test_unknown_input_bridge_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported ArkWeb input bridge"):
            arkweb.patch_input_bridge(b"different emulator build")

    def test_unknown_input_bridge_stops_install_before_guest_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(arkweb, "validate_hap"), patch.object(arkweb, "Device") as device:
                guest = device.return_value
                guest.shell.side_effect = ["x86_64", "0", "OpenHarmony-7.0.0.39"]
                def receive(*args):
                    self.assertEqual(args[:3], ("file", "recv", arkweb.INPUT_BRIDGE_PATH))
                    args[3].write_bytes(b"different emulator build")
                guest.run.side_effect = receive
                with self.assertRaisesRegex(ValueError, "Unsupported ArkWeb input bridge"):
                    arkweb.install(Path("unused.hap"), "test", Path(directory))
                self.assertEqual(guest.shell.call_count, 3)
                self.assertEqual(guest.run.call_count, 1)

    @unittest.skipUnless(platform.system() == "Linux" and platform.machine() == "x86_64"
                         and all(shutil.which(t) for t in ("clang", "clang++", "ld", "objcopy")),
                         "requires Linux x86_64 clang and binutils")
    def test_mouse_adapter_reproduces_bytes_and_preserves_event_abi(self):
        source = Path(arkweb.__file__).with_name("mouse_compat.S")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["clang", "-c", str(source), "-o", str(root / "mouse.o")], check=True)
            subprocess.run(["ld", "-Ttext=0x1070e0", "--defsym=legacy_mouse=0x1027a0",
                            "--defsym=stack_chk_fail=0x1c8de0", "-e", "arkweb_mouse_compat",
                            str(root / "mouse.o"), "-o", str(root / "mouse.elf")], check=True)
            subprocess.run(["objcopy", "-O", "binary", "--only-section=.text",
                            str(root / "mouse.elf"), str(root / "mouse.bin")], check=True)
            self.assertEqual((root / "mouse.bin").read_bytes(), arkweb.MOUSE_ADAPTER)
            self.assertEqual(len(arkweb.MOUSE_ADAPTER), 0x126)
            self.assertEqual(arkweb.MOUSE_WHEEL_ADAPTER,
                             b"\xe9" + struct.pack("<i", 0x102790 - (0x106780 + 5)))
            subprocess.run(["clang++", "-std=c++17", "-O2",
                            str(Path(__file__).with_name("arkweb_mouse_abi.cpp")),
                            str(root / "mouse.o"), "-o", str(root / "abi-test")], check=True)
            subprocess.run([str(root / "abi-test")], check=True)

    @unittest.skipUnless(os.environ.get("ARKWEB_TEST_BRIDGE"), "optional original emulator library")
    def test_real_input_bridge_patch_is_bounded_and_idempotent(self):
        original = Path(os.environ["ARKWEB_TEST_BRIDGE"]).read_bytes()
        self.assertEqual(arkweb.hashlib.sha256(original).hexdigest(), arkweb.INPUT_BRIDGE_SHA)
        patched = arkweb.patch_input_bridge(original)
        self.assertEqual(len(patched), len(original))
        self.assertEqual(arkweb.patch_input_bridge(patched), patched)
        self.assertEqual(patched[:0x105780], original[:0x105780])
        self.assertEqual(patched[0x105785:0x1060e0], original[0x105785:0x1060e0])
        self.assertEqual(patched[0x106206:], original[0x106206:])

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
