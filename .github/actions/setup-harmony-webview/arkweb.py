#!/usr/bin/env python3
"""Pinned ArkWeb runtime for disposable Oniro 6.1 x86_64 test emulators.

Used automatically by dayapp.yml after booting a fresh CI emulator.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import zipfile

ARCHIVE_URL = "https://update.dbankcdn.com/download/data/pub_13/HWHOTA_hota_900_9/4a/v3/SurZ60PYSryhwf0W4QEK1g/system-image-phone-x86.zip"
ARCHIVE_SHA = "874e359f7f07b93c2b6761b9052fc2681ac2da0202eed7fa0c299a2179487d4a"
HAP_SHA = "7120ee8df5207d592cfdf7448da79ac16a6f791f916ec060b55525d43f24f4a7"
EGL_SHA = "0f16b8f14a24200f590c74661c21d82f64df14d2aa12e93e407062e301e5ead3"
PATCHED_EGL_SHA = "a3ed3f41a13cc30ddc941bd446d3a7758233ebca6a7672828e55b5721b63d8e6"
BUNDLE = "com.huawei.hmos.arkwebcore"
EGL_PATH = "/system/lib64/platformsdk/libEGL.so"
SANDBOX_PATH = "/system/etc/sandbox/appdata-sandbox.json"


def require_hash(path, expected):
    with Path(path).open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected:
        raise ValueError(f"Unexpected SHA256 for {path}: {actual}; expected {expected}")


def validate_hap(path):
    require_hash(path, HAP_SHA)
    with zipfile.ZipFile(path) as package:
        module = json.loads(package.read("module.json"))
        if module["app"]["bundleName"] != BUNDLE:
            raise ValueError("Unexpected ArkWeb bundle")
        libraries = [n for n in package.namelist() if n.endswith(".so")]
        if "libs/x86_64/libarkweb_engine.so" not in libraries:
            raise ValueError("Missing x86_64 engine")
        for name in libraries:
            with package.open(name) as library:
                header = library.read(20)
            if header[:6] != b"\x7fELF\x02\x01" or header[18:20] != b"\x3e\x00":
                raise ValueError(f"Not an x86_64 ELF library: {name}")
            print(f"{name} x86_64", flush=True)


def extract(archive, output):
    require_hash(archive, ARCHIVE_SHA)
    with tempfile.TemporaryDirectory(prefix="arkweb-extract-") as directory:
        root = Path(directory)
        with zipfile.ZipFile(archive) as package:
            with package.open("system.img") as source, (root / "system.img").open("wb") as target:
                shutil.copyfileobj(source, target)
        for source, member, target in [
            (root / "system.img", "/system/module_update/ArkWebCore/module.img", root / "module.img"),
            (root / "module.img", f"/app/{BUNDLE}/ArkWebCore.hap", root / "ArkWebCore.hap"),
        ]:
            subprocess.run(["debugfs", "-R", f"dump {member} {target}", str(source)], check=True)
            if not target.is_file() or not target.stat().st_size:
                raise RuntimeError(f"debugfs could not extract {member}")
        validate_hap(root / "ArkWebCore.hap")
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / "ArkWebCore.hap", output)


def prepare(cache):
    """Cache only the verified 97 MB HAP, not the 2.16 GB source disk archive."""
    cache.mkdir(parents=True, exist_ok=True)
    hap = cache / "ArkWebCore.hap"
    if hap.exists():
        validate_hap(hap)
        print(f"Using verified cached runtime: {hap}", flush=True)
        return hap
    with tempfile.TemporaryDirectory(prefix="arkweb-download-") as directory:
        archive = Path(directory) / "system-image-phone-x86.zip"
        subprocess.run(["curl", "--fail", "--location", "--retry", "3",
                        "--connect-timeout", "30", "--max-time", "900",
                        "--output", str(archive), ARCHIVE_URL], check=True)
        pending = cache / "ArkWebCore.hap.partial"
        try:
            extract(archive, pending)
            pending.replace(hap)
        finally:
            pending.unlink(missing_ok=True)
    return hap


def patch_egl(data):
    digest = hashlib.sha256(data).hexdigest()
    if digest == PATCHED_EGL_SHA:
        return data
    if digest != EGL_SHA:
        raise ValueError(f"Unsupported EGL wrapper: {digest}; refusing to patch")
    token = b"EGL_EXT_create_context_robustness "
    if data.count(token) != 1:
        raise ValueError("Unexpected EGL extension table")
    return data.replace(token, b" " * len(token))


def patch_sandbox(config, namespaces):
    found = set()

    def visit(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("__internal__.com.ohos.render", "__internal__.com.ohos.gpu"):
                    found.add(key)
                    for rule in value:
                        flags = rule["sandbox-ns-flags"]
                        rule["sandbox-ns-flags"] = [
                            flag for flag in flags if flag not in ("pid", "net") or flag in namespaces
                        ]
                else:
                    visit(value)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(config)
    if len(found) != 2:
        raise ValueError("Missing renderer/GPU sandbox configuration")
    return config


class Device:
    def __init__(self, target):
        self.target = target

    def run(self, *args, timeout=60):
        result = subprocess.run(["hdc", "-t", self.target, *map(str, args)],
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=timeout, check=True)
        if "[Fail]" in result.stdout:
            raise RuntimeError(result.stdout)
        return result.stdout.replace("\r", "")

    def shell(self, command):
        # hdc often returns zero even when the remote command fails.
        result = self.run("shell", f"({command}); rc=$?; echo __ARKWEB_EXIT__=$rc")
        if not result.rstrip().endswith("__ARKWEB_EXIT__=0"):
            raise RuntimeError(f"Guest command failed: {command}\n{result}")
        return result.rsplit("__ARKWEB_EXIT__=", 1)[0].strip()


def install(hap, target, diagnostics):
    validate_hap(hap)
    device = Device(target)
    if device.shell("uname -m") != "x86_64" or device.shell("id -u") != "0":
        raise ValueError("Requires a rooted x86_64 disposable emulator")
    diagnostics.mkdir(parents=True, exist_ok=True)
    device.run("file", "recv", EGL_PATH, diagnostics / "libEGL.original.so")
    patched_egl = patch_egl((diagnostics / "libEGL.original.so").read_bytes())
    device.run("file", "recv", SANDBOX_PATH, diagnostics / "appdata-sandbox.original.json")
    config = json.loads((diagnostics / "appdata-sandbox.original.json").read_text())
    namespaces = device.shell("ls /proc/1/ns").split()
    patched_config = patch_sandbox(config, namespaces)
    (diagnostics / "libEGL.patched.so").write_bytes(patched_egl)
    (diagnostics / "appdata-sandbox.patched.json").write_text(json.dumps(patched_config, indent=2) + "\n")
    device.shell("mkdir -p /data/local/tmp/day-arkweb")
    guest_hap = "/data/local/tmp/day-arkweb/ArkWebCore.hap"
    device.run("file", "send", hap, guest_hap)
    result = device.shell(f"bm install -p {guest_hap}")
    if "successfully" not in result:
        raise RuntimeError(f"Bundle installation failed: {result}")
    installed = f"/data/app/el1/bundle/public/{BUNDLE}/entry.hap"
    device.shell(f"test -f {installed}")
    device.shell(f"param set persist.arkwebcore.package_name {BUNDLE}")
    device.shell(f"param set persist.arkwebcore.install_path {installed}")
    device.shell("mount -o remount,rw /")
    device.run("file", "send", diagnostics / "libEGL.patched.so", EGL_PATH)
    device.run("file", "send", diagnostics / "appdata-sandbox.patched.json", SANDBOX_PATH)
    device.shell("sync")
    # Reboot is essential: appspawn otherwise retains the original EGL mapping.
    device.run("shell", "reboot")
    time.sleep(5)
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        subprocess.run(["hdc", "tconn", target], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=10)
        try:
            if device.shell("param get bootevent.boot.completed") == "true":
                device.run("file", "recv", EGL_PATH, diagnostics / "libEGL.installed.so")
                require_hash(diagnostics / "libEGL.installed.so", PATCHED_EGL_SHA)
                print(f"ArkWeb ready: {installed}", flush=True)
                return
        except (RuntimeError, subprocess.SubprocessError):
            pass
        time.sleep(3)
    raise RuntimeError("Emulator did not finish rebooting")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    unpack = commands.add_parser("extract")
    unpack.add_argument("archive", type=Path)
    unpack.add_argument("output", type=Path)
    setup = commands.add_parser("install")
    setup.add_argument("hap", type=Path)
    setup.add_argument("--target", required=True)
    setup.add_argument("--disposable-emulator", action="store_true", required=True,
                       help="acknowledge guest system changes and reduced renderer isolation")
    setup.add_argument("--diagnostics", type=Path, required=True)
    cached = commands.add_parser("prepare")
    cached.add_argument("cache", type=Path)
    args = parser.parse_args()
    if args.command == "extract":
        extract(args.archive, args.output)
    elif args.command == "install":
        install(args.hap, args.target, args.diagnostics)
    else:
        prepare(args.cache)


if __name__ == "__main__":
    main()
