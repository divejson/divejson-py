#!/usr/bin/env python3
"""Work out what version a release is, and write it into the two files that carry it.

`.github/workflows/release-bump.yml` runs this over a checkout of `main`; the two
rewrites then land through a pull request and the tag goes on the commit that lands. It
is a script beside the workflow rather than part of the package, because nothing a `pip
install divejson` gets has any business knowing how this repository cuts a release.

The version is computed from the conventional-commit subjects since the last `v*` tag,
which — the repository being squash-only with `PR_TITLE` as the subject — are the titles
of the pull requests in the window. **Below 1.0 a breaking change moves the minor**: 0.x
has no major to spend, and the promise 0.x makes is precisely that a minor may break.
From 1.0.0 the rule is ordinary semver.

Everything it will not do is a refusal rather than a guess, because every one of them is
a release that is wrong in a way nobody would notice until PyPI had it:

- an empty `## Unreleased` section publishes a version whose notes say nothing;
- a `__version__` that disagrees with the newest tag means a bump landed and its tag
  never did, where the answer is to tag what is already there rather than to bump past
  it, silently burning a version number;
- a version that is not ahead of the current one, or one whose tag already exists, is a
  release PyPI would refuse after `main` had already moved.

Run it by hand to see what a release would be — it only touches the working tree, and
`git diff` is the whole report:

    python3 .github/scripts/release_bump.py
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

# The line hatchling reads the built version out of (`[tool.hatch.version]`), matched
# whole so that a `__version__` mentioned in a docstring or a comment cannot be the one
# that moves.
VERSION_LINE = re.compile(r'^__version__ = "(?P<version>[^"]*)"$', re.MULTILINE)

# Three plain integers and nothing else. A pre-release or build-metadata suffix would
# make `bump` ambiguous and `release.yml`'s `dist/divejson-$version.tar.gz` check depend
# on how the build normalises it, so it is refused rather than half-supported.
SEMVER = re.compile(r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$")

# The same shape `.github/workflows/pr-title.yml` enforces on every title that becomes a
# subject here, with the type left open: that workflow owns the list of types, and a
# second copy of it in this file is a second place for it to go stale. What this needs
# from a subject is only whether it carries `!`, and whether its type is `feat` or `fix`.
SUBJECT = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]*)\))?(?P<breaking>!)?: ")

UNRELEASED = "## Unreleased"

INIT_PATH = Path("divejson") / "__init__.py"
CHANGELOG_PATH = Path("CHANGELOG.md")


class ReleaseError(Exception):
    """Something about the tree or the request means there is no release to cut."""


def parse_version(text: str) -> tuple[int, int, int]:
    match = SEMVER.match(text)
    if match is None:
        raise ReleaseError(f"{text!r} is not a major.minor.patch version")
    return int(match["major"]), int(match["minor"]), int(match["patch"])


def read_version(init_text: str) -> str:
    """The version the package currently declares."""
    found = VERSION_LINE.findall(init_text)
    if len(found) != 1:
        raise ReleaseError(f"{INIT_PATH} declares __version__ {len(found)} times, not once")
    return found[0]


def bump_kind(subjects: Sequence[str]) -> str:
    """What the window as a whole asks for: `breaking`, `feature`, `fix` or `none`.

    Subjects that are not conventional subjects contribute nothing. They exist — the
    handful of commits that predate `pr-title.yml` — and one of them is not evidence
    either way.
    """
    types: set[str] = set()
    for subject in subjects:
        match = SUBJECT.match(subject)
        if match is None:
            continue
        if match["breaking"]:
            return "breaking"
        types.add(match["type"])
    if "feat" in types:
        return "feature"
    if "fix" in types:
        return "fix"
    return "none"


def next_version(current: str, subjects: Sequence[str]) -> str:
    major, minor, patch = parse_version(current)
    kind = bump_kind(subjects)
    if major == 0:
        # Below 1.0 a break has no major to move, and a minor is what 0.x already warns
        # about — so breaking and feature land on the same answer, and only a window of
        # fixes (or of changes that claim nothing at all) is a patch.
        if kind in ("breaking", "feature"):
            return f"0.{minor + 1}.0"
        return f"0.{minor}.{patch + 1}"
    if kind == "breaking":
        return f"{major + 1}.0.0"
    if kind == "feature":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def rewrite_version(init_text: str, version: str) -> str:
    read_version(init_text)  # exactly one line to move, or nothing is rewritten
    return VERSION_LINE.sub(f'__version__ = "{version}"', init_text, count=1)


def rewrite_changelog(changelog_text: str, version: str) -> str:
    """`## Unreleased` becomes `## <version>`, with a fresh empty `## Unreleased` above."""
    lines = changelog_text.split("\n")
    headings = [i for i, line in enumerate(lines) if line.rstrip() == UNRELEASED]
    if len(headings) != 1:
        raise ReleaseError(
            f"{CHANGELOG_PATH} has {len(headings)} `{UNRELEASED}` headings, not one"
        )
    start = headings[0]
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    if not "\n".join(lines[start + 1 : end]).strip():
        raise ReleaseError(
            f"the `{UNRELEASED}` section of {CHANGELOG_PATH} is empty — a release with no "
            "notes is not one anybody can read, so write the entries first"
        )
    lines[start : start + 1] = [UNRELEASED, "", f"## {version}"]
    return "\n".join(lines)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    )
    return result.stdout


def last_tag(root: Path) -> str | None:
    """The newest `v*` tag reachable from HEAD, or None in a repository with no release.

    Reachability rather than a sort over every tag: a tag on some other branch is not
    what this history was released as, and would silently truncate the window.
    """
    result = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0", "--match", "v*"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def subjects_since(root: Path, tag: str | None) -> list[str]:
    span = f"{tag}..HEAD" if tag else "HEAD"
    return [line for line in git(root, "log", "--format=%s", span).splitlines() if line]


def plan(root: Path, init_text: str, requested: str | None) -> tuple[str, str, str | None, list[str]]:
    """The whole decision, before a byte of the tree is touched."""
    current = read_version(init_text)
    parse_version(current)

    tag = last_tag(root)
    if tag is not None and tag != f"v{current}":
        raise ReleaseError(
            f"the tree declares {current} and the newest tag reachable is {tag}. A bump "
            f"has landed whose tag never did — push v{current} at the commit that moved "
            "it rather than bumping past a version nobody released"
        )

    subjects = subjects_since(root, tag)
    if not subjects:
        raise ReleaseError(f"nothing has landed since {tag or 'the start of the history'}")

    if requested:
        version = requested
        if parse_version(version) <= parse_version(current):
            raise ReleaseError(f"{version} is not ahead of {current}")
    else:
        version = next_version(current, subjects)

    if f"v{version}" in git(root, "tag", "--list").split():
        raise ReleaseError(f"v{version} already exists — PyPI would refuse it as a duplicate")

    return current, version, tag, subjects


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        default="",
        help="the version to release. Empty — which is what the workflow passes when its "
        "own input was left blank — computes it from the commit subjects.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="the checkout to work in (default: the working directory)",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()

    init_text = (root / INIT_PATH).read_text(encoding="utf-8")
    changelog_text = (root / CHANGELOG_PATH).read_text(encoding="utf-8")

    try:
        current, version, tag, subjects = plan(root, init_text, args.version or None)
        # Both rewrites are computed before either is written, so a refusal from the
        # second one does not leave the first one on disk.
        new_init = rewrite_version(init_text, version)
        new_changelog = rewrite_changelog(changelog_text, version)
    except ReleaseError as error:
        prefix = "::error::" if os.environ.get("GITHUB_ACTIONS") else "error: "
        print(f"{prefix}{error}", file=sys.stderr)
        return 1

    (root / INIT_PATH).write_text(new_init, encoding="utf-8")
    (root / CHANGELOG_PATH).write_text(new_changelog, encoding="utf-8")

    # Read back rather than trust the substitution: this string is what `release.yml`
    # will look for as `dist/divejson-$version.tar.gz`, and the tag is pushed before
    # anything builds.
    written = read_version((root / INIT_PATH).read_text(encoding="utf-8"))
    if written != version:
        print(f"error: wrote {written!r}, meant {version!r}", file=sys.stderr)
        return 1

    print(f"released so far: {current}   (tag {tag or 'none'})")
    print(f"releasing:       {version}   ({bump_kind(subjects)})")
    print(f"window:          {len(subjects)} commits")
    for subject in subjects:
        print(f"  {subject}")

    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"version={version}\n")
            handle.write(f"previous={current}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
