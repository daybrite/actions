#!/usr/bin/env python3
# Copyright © The Daybrite Project
# SPDX-License-Identifier: MPL-2.0
"""Reserve Day project names for the daybrite organization and its GitHub forks."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


GUIDANCE = (
    "Use 'dayext-' for non-Daybrite packages and crate names, and an unreserved name "
    "for independent repositories and apps. See https://daybrite.dev/contributing/"
)
GENERATED = {".git", "target", "build", "node_modules", ".venv", ".cache"}


class AuditError(Exception):
    pass


def repository_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[\w.-]+/[\w.-]+", value, re.ASCII):
        raise AuditError("Expected a GitHub repository identity in owner/name form.")
    return value


def daybrite_repository(value):
    return repository_name(value).split("/", 1)[0].casefold() == "daybrite"


def reserved(value):
    # Repository names are case-insensitive on GitHub; cover Day- app names as well.
    return isinstance(value, str) and value.casefold().startswith("day-")


def github_repository(repository):
    repository_name(repository)
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "daybrite-naming-check"}
    token = os.environ.get("GH_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(f"{api}/repos/{repository}", headers=headers)
    try:
        with urlopen(request, timeout=20) as response:
            result = json.load(response)
    except HTTPError as error:
        error.close()
        raise AuditError(
            f"Cannot verify GitHub fork ancestry for {repository!r} (HTTP {error.code}). "
            "Check repository access and API limits, then rerun the check."
        ) from error
    except (URLError, TimeoutError, OSError, ValueError) as error:
        raise AuditError(
            f"Cannot read GitHub fork ancestry for {repository!r}. Rerun the check."
        ) from error
    if not isinstance(result, dict) or str(result.get("full_name", "")).casefold() != repository.casefold():
        raise AuditError(f"GitHub returned unexpected repository metadata for {repository!r}.")
    return result


def permitted_repository(repository, fetch=github_repository):
    seen = set()
    # Follow parent links too: daybrite may itself have forked a non-Daybrite repository.
    for _ in range(32):
        if daybrite_repository(repository):
            return True
        identity = repository.casefold()
        if identity in seen:
            raise AuditError("GitHub returned a cycle in the repository's fork ancestry.")
        seen.add(identity)
        info = fetch(repository)
        if info.get("fork") is False:
            return False
        if info.get("fork") is not True:
            raise AuditError(f"GitHub did not report fork status for {repository!r}.")
        for key in ("source", "parent"):
            ancestor = info.get(key)
            if isinstance(ancestor, dict) and daybrite_repository(ancestor.get("full_name")):
                return True
        parent = info.get("parent")
        if not isinstance(parent, dict):
            raise AuditError(f"GitHub did not report a parent for fork {repository!r}.")
        repository = repository_name(parent.get("full_name"))
    raise AuditError("Could not resolve the repository's fork ancestry within 32 requests.")


def is_manifest(path):
    return path.name == "Cargo.toml" or path.name == "Day.toml" or (
        path.name.startswith("Day-") and path.suffix == ".toml"
    )


def manifests(root, project_path=""):
    # Inspect repository files, including nested workspaces, without running Cargo or build scripts.
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root, check=True, capture_output=True,
    )
    paths = {root / os.fsdecode(name) for name in result.stdout.split(b"\0") if name}
    paths = {path for path in paths if is_manifest(path)}
    if project_path:
        # setup-command can create an ignored project or initialize a nested Git repository.
        # Inspect that explicit project after generation, excluding build output.
        project = (root / project_path).resolve()
        if not project.is_relative_to(root) or not project.is_dir():
            raise AuditError("The generated project path must be a directory inside the checkout.")
        for directory, dirs, files in os.walk(project):
            dirs[:] = [name for name in dirs if name not in GENERATED]
            paths.update(Path(directory) / name for name in files if is_manifest(Path(name)))
    for path in sorted(paths):
        if not path.resolve().is_relative_to(root):
            raise AuditError(f"Manifest {str(path.relative_to(root))!r} points outside the checkout.")
        if not path.is_file():
            raise AuditError(f"Cannot read manifest {str(path.relative_to(root))!r}.")
        yield path


def project_names(root, repository, project_path=""):
    names = []
    repo = repository_name(repository).split("/", 1)[1]
    if reserved(repo):
        names.append(f"Repository {repository!r}")
    for path in manifests(root, project_path):
        location = str(path.relative_to(root))
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise AuditError(f"Cannot parse {location!r} as TOML.") from error
        if path.name == "Cargo.toml":
            # Dependencies and Cargo.lock may refer to Day crates. Only definitions count.
            entries = [("package.name", data.get("package", {}).get("name")),
                       ("lib.name", data.get("lib", {}).get("name"))]
            entries.extend(("bin.name", item.get("name")) for item in data.get("bin", []))
        else:
            app = data.get("app", {})
            entries = [(f"app.{key}", app.get(key)) for key in ("name", "title")]
        for field, name in entries:
            if reserved(name):
                names.append(f"{location}: {field} = {name!r}")
    return names


def check(root, repository, project_path="", fetch=github_repository):
    if daybrite_repository(repository):
        return []
    names = project_names(root.resolve(), repository, project_path)
    if not names or permitted_repository(repository, fetch):
        return []
    return [f"{name} uses a reserved Day prefix. Only daybrite repositories and their "
            "GitHub forks may use 'day-' or 'Day-'." for name in names]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--project-path", default="")
    args = parser.parse_args()
    try:
        errors = check(args.root, os.environ.get("GITHUB_REPOSITORY", ""), args.project_path)
    except (AuditError, OSError, subprocess.CalledProcessError, TypeError, AttributeError) as error:
        errors = [str(error)]
    for error in errors:
        message = f"{error} {GUIDANCE}".replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error::{message}", file=sys.stderr)
    if errors:
        return 1
    print("Day project naming: organization, fork ancestry, or unreserved names verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
