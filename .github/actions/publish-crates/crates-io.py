#!/usr/bin/env python3
# Copyright © The Daybrite Project
# SPDX-License-Identifier: MPL-2.0
"""crates-io.py — publish a workspace to crates.io one crate at a time, within the rate limits.

crates.io lets an account upload 30 new versions at once and then one a minute, and 5 new crates
at once and then one every 10 minutes. `cargo publish --workspace` uploads as fast as it can and
stops at the first refusal, so a workspace of this size needs a throttled loop instead.

    crates-io.py [--root DIR]... publish [--dry-run] [--max N] [--only NAME ...]
    crates-io.py [--root DIR]... reserve [--dry-run] [--only NAME ...] [--work DIR]

`--root` is a workspace (default: the current directory): daybrite/day, or a piece's or part's
own repository. It repeats, in order, for a repository that keeps a package beside its workspace
rather than in it (a piece's `gallery/`, which depends on the piece). The publish-crates action
next to this script runs it in CI.

`publish` uploads every publishable member at its manifest version, in dependency order. A
version crates.io already has is skipped, so a re-run finishes what an interrupted run started,
and `--max` stops after N uploads so CI can split the run across jobs that each authenticate
afresh (a Trusted Publishing token lasts 30 minutes). `--dry-run` prints the plan.

`reserve` holds a new crate name with an empty 0.0.1 placeholder whose README points at the
crate's directory in its repository. Trusted Publishing can only be configured for a crate that
already exists, so a new member is reserved the day it is named. The placeholder carries the
crate's `repository`, which is what `day patch --main` follows to its default branch.

On a rate-limit refusal either command sleeps until the time crates.io names and tries again; a
dependency the index has not caught up with yet is retried a few times; any other error stops the
run. Uploads use CARGO_REGISTRY_TOKEN, or the token `cargo login` stored. Under GitHub Actions,
`publish` also writes `remaining=<n>` to $GITHUB_OUTPUT: the uploads still to do.
"""

import argparse
import email.utils
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOTS = [Path.cwd()]
PLACEHOLDER_VERSION = "0.0.1"
USER_AGENT = "daybrite-crates-io (https://github.com/daybrite/day)"
# How often to retry an upload whose dependency the index does not list yet, and how long to wait.
INDEX_RETRIES = 10
INDEX_WAIT = 30


def log(msg):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {msg}", flush=True)


def workspace_order():
    """The publishable members of every root, root by root, each in dependency order."""
    return [m for root in ROOTS for m in root_order(root)]


def root_order(root):
    """Return one root's publishable members in dependency order, as dicts of name, version, root,
    dir (relative to the first root), repository, and lib (the library name when it differs from
    the package's)."""
    out = subprocess.run(
        ["cargo", "metadata", "--no-deps", "--format-version", "1"],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout
    # `publish = false` reads as an empty registry list.
    packages = [p for p in json.loads(out)["packages"] if p["publish"] != []]
    names = {p["name"] for p in packages}
    deps = {
        p["name"]: {d["name"] for d in p["dependencies"]
                    if d["name"] in names and d["kind"] in (None, "build")}
        for p in packages
    }
    level = {}

    def depth(name):
        if name not in level:
            level[name] = 0  # cycle guard; normal dependencies cannot cycle
            level[name] = 1 + max((depth(d) for d in deps[name]), default=-1)
        return level[name]

    def lib(p):
        for t in p["targets"]:
            if "lib" in t["kind"] and t["name"] != p["name"].replace("-", "_"):
                return t["name"]
        return None

    def rel(p):
        d = Path(p["manifest_path"]).parent
        return d.relative_to(ROOTS[0]) if d.is_relative_to(ROOTS[0]) else d.relative_to(root)

    order = sorted(packages, key=lambda p: (depth(p["name"]), p["name"]))
    return [{"name": p["name"], "version": p["version"], "root": root, "dir": rel(p),
             "repository": (p.get("repository") or "").removesuffix("/").removesuffix(".git"),
             "lib": lib(p)}
            for p in order]


def on_crates_io(name, version=None):
    """Whether crates.io has the crate (or, with a version, that version of it)."""
    url = f"https://crates.io/api/v1/crates/{name}" + (f"/{version}" if version else "")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def retry_after(output):
    """The Unix time a rate-limit refusal says to try again, or None if this is not one."""
    if "too many" not in output and "429" not in output:
        return None
    m = re.search(r"try again after (.+? GMT)", output)
    if m:
        try:
            return email.utils.parsedate_to_datetime(m.group(1)).timestamp()
        except (TypeError, ValueError):
            pass
    return datetime.now(timezone.utc).timestamp() + 600


def index_lag(output):
    """Whether cargo failed because the index does not list a just-published dependency yet."""
    return "failed to select a version for the requirement" in output or (
        "no matching package named" in output)


def upload(cmd, cwd, env=None):
    """Run one `cargo publish`, waiting out rate limits and index lag; stop the run otherwise."""
    lag = 0
    while True:
        res = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
        output = res.stdout + res.stderr
        if res.returncode == 0:
            return
        when = retry_after(output)
        if when is not None:
            wait = max(30, when - time.time() + 15)
            log(f"rate limited; sleeping {wait / 60:.1f} min")
            time.sleep(wait)
            continue
        if index_lag(output) and lag < INDEX_RETRIES:
            lag += 1
            log(f"a dependency is not in the index yet; retrying in {INDEX_WAIT} s")
            time.sleep(INDEX_WAIT)
            continue
        sys.stderr.write(output)
        raise SystemExit(f"`{' '.join(cmd)}` failed; stopping. A re-run resumes here.")


def write_placeholder(work, member):
    name, crate_dir, repository = member["name"], member["dir"], member["repository"]
    if not repository:
        raise SystemExit(f"{name}: set `repository` in its manifest; the placeholder points there")
    pkg = work / name
    (pkg / "src").mkdir(parents=True, exist_ok=True)
    source = f"{repository}/tree/main/{crate_dir}" if str(crate_dir) != "." else repository
    lib = f'\n[lib]\nname = "{member["lib"]}"\n' if member["lib"] else ""
    (pkg / "Cargo.toml").write_text(f"""[package]
name = "{name}"
version = "{PLACEHOLDER_VERSION}"
edition = "2024"
license = "MPL-2.0"
description = "Part of Day, which builds native apps from one Rust codebase. This placeholder holds the name until the first release."
repository = "{repository}"
homepage = "https://daybrite.dev"
readme = "README.md"
{lib}""")
    (pkg / "README.md").write_text(f"""# {name}

Part of [Day](https://daybrite.dev), which builds native apps from one Rust codebase.

This 0.0.1 release is a placeholder that holds the name. The code is developed in
[{repository.removeprefix("https://github.com/")}]({source}) and ships here from the next release.
""")
    (pkg / "src" / "lib.rs").write_text(
        f"//! Placeholder for `{name}`. The code ships from the next release; see <{source}>.\n")
    return pkg


def select(plan, only):
    if not only:
        return plan
    unknown = set(only) - {p["name"] for p in plan}
    if unknown:
        raise SystemExit(f"not publishable workspace members: {', '.join(sorted(unknown))}")
    return [p for p in plan if p["name"] in only]


def publish(args):
    plan = select(workspace_order(), args.only)
    uploaded = skipped = 0
    remaining = sum(1 for p in plan if not on_crates_io(p["name"], p["version"]))
    for i, member in enumerate(plan, 1):
        name, version = member["name"], member["version"]
        if on_crates_io(name, version):
            log(f"{i}/{len(plan)} {name} {version}: already on crates.io, skipped")
            skipped += 1
            continue
        if args.max is not None and uploaded >= args.max:
            log(f"stopping after {uploaded} uploads (--max); {len(plan) - i + 1} remain")
            break
        if args.dry_run:
            log(f"{i}/{len(plan)} {name} {version}: would publish")
        else:
            # --no-verify: the caller's own gate builds the packages before this runs (day's CI
            # builds every one from its archive), and a failed rebuild mid-run would leave the
            # release half published.
            # `--locked` holds the upload to the lock CI tested, where the root commits one.
            locked = ["--locked"] if (member["root"] / "Cargo.lock").exists() else []
            upload(["cargo", "publish", "-p", name, "--no-verify", *locked], member["root"])
            log(f"{i}/{len(plan)} {name} {version}: published")
            remaining -= 1
        uploaded += 1
    verb = "to publish" if args.dry_run else "published"
    log(f"done: {uploaded} {verb}, {skipped} already on crates.io, {remaining} remaining")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as out:
            out.write(f"remaining={remaining}\n")


def reserve(args):
    # Placeholders are built outside the checkout, so no .cargo/config.toml patch table applies.
    work = (args.work or Path(os.environ.get("TMPDIR", "/tmp")) / "day-reserve-names").resolve()
    if any(work.is_relative_to(root) for root in ROOTS):
        raise SystemExit("--work must be outside the checkout")
    work.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, CARGO_TARGET_DIR=str(work / "target"))

    plan = select(workspace_order(), args.only)
    reserved = skipped = 0
    for i, member in enumerate(plan, 1):
        name = member["name"]
        if on_crates_io(name):
            log(f"{i}/{len(plan)} {name}: already on crates.io, skipped")
            skipped += 1
            continue
        pkg = write_placeholder(work, member)
        cmd = ["cargo", "publish", "--allow-dirty"] + (["--dry-run"] if args.dry_run else [])
        upload(cmd, pkg, env)
        reserved += 1
        verb = "verified" if args.dry_run else "published"
        log(f"{i}/{len(plan)} {name}: {verb} {PLACEHOLDER_VERSION}")
    verb = "verified" if args.dry_run else "reserved"
    log(f"done: {reserved} {verb}, {skipped} already on crates.io")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, action="append",
                    help="a workspace to publish (repeatable, in order; default: the current directory)")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("publish", help="upload the workspace version of every member")
    p.add_argument("--dry-run", action="store_true", help="print the plan, upload nothing")
    p.add_argument("--max", type=int, metavar="N", help="stop after N uploads")
    p.add_argument("--only", nargs="+", metavar="NAME", help="publish only these members")
    p.set_defaults(run=publish)
    r = sub.add_parser("reserve", help="hold new crate names with 0.0.1 placeholders")
    r.add_argument("--dry-run", action="store_true", help="package and verify, upload nothing")
    r.add_argument("--only", nargs="+", metavar="NAME", help="reserve only these names")
    r.add_argument("--work", type=Path, help="directory for the generated placeholders")
    r.set_defaults(run=reserve)
    args = ap.parse_args()
    global ROOTS
    ROOTS = [r.resolve() for r in (args.root or [Path.cwd()])]
    args.run(args)


if __name__ == "__main__":
    main()
