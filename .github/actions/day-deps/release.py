#!/usr/bin/env python3
# Copyright © The Daybrite Project
# SPDX-License-Identifier: MPL-2.0
"""Validate a release dependency graph without repairing or updating its lock."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

CRATES_IO = "registry+https://github.com/rust-lang/crates.io-index"


def lock_errors(lock):
    errors = []
    packages = lock.get("package", [])
    if not packages:
        errors.append("Cargo.lock contains no packages")
    for package in packages:
        source = package.get("source")
        if source is not None and source != CRATES_IO:
            errors.append(f"{package['name']} must come from crates.io, not {source}")
        if source == CRATES_IO and not package.get("checksum"):
            errors.append(f"{package['name']} has no crates.io checksum")
    return errors


def manifest_errors(value, where):
    errors = []
    for section, table in value.items():
        if section in ("patch", "replace") and table:
            errors.append(f"{where}: release manifests cannot override dependency sources")
        if section in ("dependencies", "build-dependencies", "dev-dependencies") and isinstance(table, dict):
            for name, dep in table.items():
                if not isinstance(dep, dict):
                    continue
                if "git" in dep or dep.get("registry", "crates-io") != "crates-io":
                    errors.append(f"{where}: {name} must declare a crates.io version")
        # Workspace and target-specific declarations, including unused optional dependencies.
        if section in ("workspace", "target") and isinstance(table, dict):
            if section == "workspace":
                errors.extend(manifest_errors(table, where))
            else:
                for target in table.values():
                    if isinstance(target, dict):
                        errors.extend(manifest_errors(target, where))
    return errors


def graph_errors(root, metadata):
    errors = []
    members = set(metadata["workspace_members"])
    for package in metadata["packages"]:
        source = package.get("source")
        manifest = Path(package["manifest_path"]).resolve()
        if source is None:
            if package["id"] not in members or not manifest.is_relative_to(root):
                errors.append(f"{package['name']} is a path dependency outside the app workspace")
            elif manifest.is_file():
                errors.extend(manifest_errors(tomllib.loads(manifest.read_text()), str(manifest)))
        elif source != CRATES_IO:
            errors.append(f"{package['name']} resolves outside crates.io: {source}")
    return errors


def validate(root, require_tracked=True):
    root = root.resolve()
    lock_path = root / "Cargo.lock"
    before = lock_path.read_bytes()  # Missing is an error, never permission to resolve anew.
    if require_tracked:
        subprocess.run(["git", "ls-files", "--error-unmatch", "Cargo.lock"],
                       cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", "Cargo.lock"],
                       cwd=root, check=True, capture_output=True)
    lock = tomllib.loads(before.decode())
    errors = lock_errors(lock)
    for name in (".cargo/config", ".cargo/config.toml"):
        path = root / name
        if path.exists():
            config = tomllib.loads(path.read_text())
            if any(config.get(key) for key in ("patch", "paths", "source")):
                errors.append(f"{name}: release builds cannot use source overrides")
    errors.extend(manifest_errors(tomllib.loads((root / "Cargo.toml").read_text()), "Cargo.toml"))
    if errors:
        raise ValueError("\n".join(errors))
    metadata = json.loads(subprocess.check_output(
        ["cargo", "metadata", "--locked", "--format-version", "1"], cwd=root))
    errors.extend(graph_errors(root, metadata))
    members = set(metadata["workspace_members"])
    local = {(p["name"], p["version"]) for p in metadata["packages"] if p["id"] in members}
    for package in lock["package"]:
        if package.get("source") is None and (package["name"], package["version"]) not in local:
            errors.append(f"{package['name']} is an unverified path package in the release lock")
    if lock_path.read_bytes() != before:
        errors.append("Cargo.lock changed during release validation")
    # Match the framework actually in the resolved graph, not an unused entry in the lock.
    versions = {p["version"] for p in metadata["packages"] if p["name"] == "day-core"}
    if len(versions) != 1:
        errors.append("release requires exactly one crates.io day-core version")
    if errors:
        raise ValueError("\n".join(errors))
    return versions.pop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--cli", default="auto")
    args = parser.parse_args()
    try:
        version = validate(args.path)
        if args.cli not in ("auto", version, "v" + version):
            raise ValueError(f"release CLI must match locked Day v{version}; got {args.cli}")
        if output := os.environ.get("GITHUB_OUTPUT"):
            with open(output, "a") as out:
                out.write(f"version={version}\ncli=v{version}\n")
        print(f"Release dependencies: unchanged crates.io lock, Day CLI v{version}")
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Release dependency validation failed: {error}\n"
              "Prepare and validate a crates.io Cargo.lock, commit it before tagging, "
              "and remove local/Git source overrides. No lockfile was regenerated.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
