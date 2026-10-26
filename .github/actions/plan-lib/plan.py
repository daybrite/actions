#!/usr/bin/env python3
# Copyright © The Daybrite Project
# SPDX-License-Identifier: MPL-2.0
"""plan.py — the jobs a Day library repository's CI runs, read from its manifests.

A piece or part says what it builds for in two places it already keeps: the toolkit features of
its crates (`appkit = [...]`, `gtk = [...]`) and the targets its demo app declares
(`demo/Day.toml`, `[app] targets`). daylib.yml asks this script for three plans:

    host     one row per desktop runner: clippy and test on the crate's own OS, plus clippy
             with each toolkit feature that OS draws with (gtk and qt on Linux, appkit on
             macOS, winui on Windows), with the toolkit's system libraries installed.
    cross    one row per mobile and web target: clippy against the target's Rust triple, with
             its toolkit feature when the crate has one.
    publish  the workspaces publish-crates uploads, in order: "." first, then any package the
             repository keeps beside the workspace that depends on it by path (a gallery, a
             second backend).

Inputs come from the environment, as daylib.yml passes them:

    PROJECT_PATH     the demo app's directory ("demo"); no Day.toml there means no demo.
    TARGETS          "all" (the demo's [app] targets) or a comma/space list.
    EXTRA_TARGETS    "true" keeps the extra tier (macos-gtk, macos-qt) that "all" leaves out.
    TARGET_PACKAGES  `<targets>: <packages>` lines, the same input dayapp.yml takes.
    PUBLISH_PATHS    "auto", or the workspaces to publish, space-separated.

It writes `host`, `cross`, `demo`, `targets` and `publish-paths` to $GITHUB_OUTPUT when that is
set, and prints the plan either way.
"""

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

# The desktop runner and the day target whose system libraries a toolkit feature needs. The
# portable toolkits are linted on Linux, their first-class home; macos-gtk and macos-qt are
# local conveniences and add no coverage here.
HOST_TOOLKITS = {
    "gtk": ("ubuntu-latest", "linux-gtk"),
    "qt": ("ubuntu-latest", "linux-qt"),
    "appkit": ("macos-15", "macos-appkit"),
    "winui": ("windows-latest", "windows-winui"),
}
HOST_RUNNERS = ["ubuntu-latest", "macos-15", "windows-latest"]

# Every mobile and web target: the runner that cross-compiles for it, its Rust triple, the
# toolkit feature that draws on it, and any RUSTFLAGS the triple needs. getrandom 0.3 on
# wasm32-unknown-unknown takes its backend from a cfg; Day's dom toolkit supplies the custom one.
CROSS_TARGETS = {
    "ios-uikit": ("macos-15", "aarch64-apple-ios-sim", "uikit", ""),
    "android-material": ("ubuntu-latest", "aarch64-linux-android", "material", ""),
    "harmony-arkui": ("ubuntu-latest", "aarch64-unknown-linux-ohos", "arkui", ""),
    "web-dom": ("ubuntu-latest", "wasm32-unknown-unknown", "dom", '--cfg getrandom_backend="custom"'),
}

# The target each toolkit feature implies, for a library with no demo to declare its targets.
TOOLKIT_TARGET = {
    "appkit": "macos-appkit", "uikit": "ios-uikit", "gtk": "linux-gtk", "qt": "linux-qt",
    "material": "android-material", "arkui": "harmony-arkui", "winui": "windows-winui",
    "dom": "web-dom",
}
# GTK and Qt off Linux: a local convenience on a Mac, and two more macOS legs per run in CI, so
# "all" leaves them out unless the caller asks.
EXTRA_TARGETS = {"macos-gtk", "macos-qt"}
PRIMARY_TARGETS = ["macos-appkit", "ios-uikit", "linux-gtk", "linux-qt", "android-material",
                   "harmony-arkui", "windows-winui", "web-dom"]


def split_list(text):
    return [t for t in text.replace(",", " ").split() if t]


def per_target(text):
    """`<targets>: <value>` lines (dayapp.yml's target-packages shape) as {target: value}."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise SystemExit(f"::error::target-packages: expected '<targets>: …' but found '{line}'")
        names, value = line.split(":", 1)
        for name in split_list(names):
            out[name] = (out.get(name, "") + " " + value.strip()).strip()
    return out


def workspace(root):
    """The root workspace's packages and member directories, from `cargo metadata`."""
    out = subprocess.run(
        ["cargo", "metadata", "--no-deps", "--format-version", "1"],
        cwd=root, check=True, capture_output=True, text=True).stdout
    meta = json.loads(out)
    members = set(meta["workspace_members"])
    packages = [p for p in meta["packages"] if p["id"] in members]
    return packages, {Path(p["manifest_path"]).parent.resolve() for p in packages}


def toolkit_features(packages):
    """The toolkit features any publishable member declares, in a stable order."""
    seen = set()
    for p in packages:
        if p.get("publish") == []:
            continue
        seen.update(f for f in p.get("features", {}) if f in TOOLKIT_TARGET)
    return [f for f in TOOLKIT_TARGET if f in seen]


def demo_targets(root, project_path):
    """The demo's `[app] targets`, or None when there is no demo."""
    manifest = root / project_path / "Day.toml"
    if not project_path or not manifest.is_file():
        return None
    app = tomllib.loads(manifest.read_text()).get("app", {})
    return list(app.get("targets", []))


def pick_targets(requested, demo, features, extras=False):
    if requested and requested != "all":
        return split_list(requested)
    if demo:
        return [t for t in demo if extras or t not in EXTRA_TARGETS]
    if features:
        return [TOOLKIT_TARGET[f] for f in features]
    return list(PRIMARY_TARGETS)


def host_rows(features, packages_by_target):
    rows = []
    for runner in HOST_RUNNERS:
        toolkits = [f for f in features if HOST_TOOLKITS.get(f, ("",))[0] == runner]
        for toolkit in toolkits or [""]:
            day_target = HOST_TOOLKITS[toolkit][1] if toolkit else ""
            rows.append({
                "name": f"{runner}, {toolkit}" if toolkit else runner,
                "os": runner,
                "features": toolkit,
                "day-target": day_target,
                "packages": packages_by_target.get(day_target, ""),
            })
    # Formatting is the same on every OS; check it once.
    for row in rows:
        row["fmt"] = row is rows[0]
    return rows


def cross_rows(targets, features, packages_by_target):
    rows = []
    for target in CROSS_TARGETS:
        if target not in targets:
            continue
        runner, triple, toolkit, rustflags = CROSS_TARGETS[target]
        rows.append({
            "name": target,
            "os": runner,
            "triple": triple,
            "day-target": target,
            "features": toolkit if toolkit in features else "",
            "rustflags": rustflags,
            "packages": packages_by_target.get(target, ""),
        })
    return rows


def depends_on(manifest, names):
    """Whether a Cargo.toml names any of `names` as a path dependency."""
    tables = [manifest.get("dependencies", {}), manifest.get("build-dependencies", {})]
    tables += [t.get("dependencies", {}) for t in manifest.get("target", {}).values()]
    for table in tables:
        for key, spec in table.items():
            name = spec.get("package", key) if isinstance(spec, dict) else key
            if name in names and isinstance(spec, dict) and "path" in spec:
                return True
    return False


def publish_paths(root, requested, packages, members, project_path):
    """"." and every directory beside the workspace whose package depends on it by path."""
    if requested and requested != "auto":
        return split_list(requested)
    names = {p["name"] for p in packages if p.get("publish") != []}
    paths = ["."]
    demo = (root / project_path).resolve() if project_path else None
    for child in sorted(root.iterdir()):
        manifest_path = child / "Cargo.toml"
        if not child.is_dir() or not manifest_path.is_file() or child.name.startswith("."):
            continue
        if child.resolve() in members or child.resolve() == demo:
            continue
        manifest = tomllib.loads(manifest_path.read_text())
        package = manifest.get("package")
        if not package or package.get("publish") is False:
            continue
        if depends_on(manifest, names):
            paths.append(child.name)
    return paths


def plan(root, env):
    project_path = env.get("PROJECT_PATH", "demo").strip()
    packages, members = workspace(root)
    features = toolkit_features(packages)
    demo = demo_targets(root, project_path)
    extras = env.get("EXTRA_TARGETS", "false").strip().lower() == "true"
    targets = pick_targets(env.get("TARGETS", "all").strip(), demo, features, extras)
    by_target = per_target(env.get("TARGET_PACKAGES", ""))
    return {
        "host": host_rows(features, by_target),
        "cross": cross_rows(targets, features, by_target),
        "demo": demo is not None,
        "publish-paths": publish_paths(root, env.get("PUBLISH_PATHS", "auto").strip(),
                                       packages, members, project_path),
        "targets": targets,
        "features": features,
    }


def main():
    result = plan(Path.cwd(), os.environ)
    print(json.dumps(result, indent=2))
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"host={json.dumps(result['host'])}\n")
            f.write(f"cross={json.dumps(result['cross'])}\n")
            f.write(f"demo={'true' if result['demo'] else 'false'}\n")
            f.write(f"targets={', '.join(result['targets'])}\n")
            f.write(f"publish-paths={' '.join(result['publish-paths'])}\n")


if __name__ == "__main__":
    sys.exit(main())
