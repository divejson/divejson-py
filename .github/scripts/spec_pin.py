#!/usr/bin/env python3
"""Whether the specification has moved under a `SPEC_REF` that is not on its `main` yet.

`.github/workflows/ci.yml`'s `specification` job runs this over the clone its first step
makes, between the byte check and the ancestor check.

It covers the window `CONTRIBUTING.md` calls the first order, where a change to what
validates has started in the specification repository and `SPEC_REF` here names that pull
request's head. Neither check either side of this one can see that branch move while the
window lasts: the byte check compares this tree against whatever commit the pin names, so
it stays green against a superseded one, and the ancestor check is red for the whole
window by design and carries no signal either. A commit added to the branch after the pin
then leaves a vendored file a commit behind with both repositories green, until the pin
moves to the merged commit and the byte check fails on a file nobody was expecting.

The signal is a file, not the ref. A specification branch may gain commits that touch
nothing this repository vendors — its own prose, its own CI — and failing on those would
make a check that fires only mid-flight into noise. So this fails only when a file under
one of the vendored trees differs between the pinned commit and the branch's head, and it
names the files rather than the ref.

Run it by hand against a checkout of the specification that has the branch:

    python3 .github/scripts/spec_pin.py --spec /path/to/divejson
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

# The trees the specification owns, which the byte check in `ci.yml` names too and
# `CONTRIBUTING.md` lists as the vendored ones. They are one list in three places, and a
# test asserts that this copy and the workflow's still agree.
OWNED = ("schema", "fixtures", "docs")

# `git diff --name-status`' letters, as something a failure message can be read out of.
# Anything else — a type change, a merge's `U` — falls through to the letter itself rather
# than being guessed at.
MOVED = {"A": "added", "D": "removed", "M": "changed"}

PIN = Path("SPEC_REF")


class SpecPinError(Exception):
    """The question could not be asked at all — a ref the checkout does not have."""


class Behind(NamedTuple):
    """One branch that holds the pin and has moved a file the specification owns since."""

    branch: str
    head: str
    files: list[tuple[str, str]]


def git(spec: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(spec), *args], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise SpecPinError(f"`git {' '.join(args)}` failed: {result.stderr.strip()}")
    return result.stdout


def is_ancestor(spec: Path, commit: str, of: str) -> bool:
    """Whether `commit` is in `of`'s history. Exit 1 is the answer "no", not a failure."""
    result = subprocess.run(
        ["git", "-C", str(spec), "merge-base", "--is-ancestor", commit, of],
        capture_output=True,
        text=True,
    )
    if result.returncode > 1:
        raise SpecPinError(f"cannot tell whether {commit} is in {of}: {result.stderr.strip()}")
    return result.returncode == 0


def branches_holding(spec: Path, commit: str) -> list[tuple[str, str]]:
    """Every branch whose history holds `commit`, each with the head it has now.

    Local and remote-tracking alike, because a `git clone` keeps its branches under
    `refs/remotes/origin/` and a repository built in place keeps them under `refs/heads/`.
    `main` cannot appear here whichever it is: the caller asks only once the pin is known
    not to be in its history, so there is nothing to filter out.
    """
    listed = git(
        spec,
        "for-each-ref",
        "--contains",
        commit,
        "--format=%(refname:short)%09%(objectname)",
        "refs/heads",
        "refs/remotes",
    )
    holding = []
    for line in listed.splitlines():
        branch, _, head = line.partition("\t")
        holding.append((branch, head))
    return holding


def moved_files(spec: Path, pinned: str, head: str) -> list[tuple[str, str]]:
    """What the specification owns that differs between the two commits.

    `--no-renames`, because this list is read as the files to copy across: a rename
    reported as one line names the path that arrives and hides the one that has to go.
    """
    listed = git(
        spec, "diff", "--name-status", "--no-renames", pinned, head, "--", *OWNED
    )
    moved = []
    for line in listed.splitlines():
        status, _, path = line.partition("\t")
        moved.append((MOVED.get(status, status), path))
    return moved


def problem(message: str, *, file: str | None = None) -> None:
    """One failure, as an annotation where a runner reads it and a line where a human does."""
    if os.environ.get("GITHUB_ACTIONS"):
        where = f" file={file}" if file else ""
        print(f"::error{where}::{message}")
    else:
        where = f"{file}: " if file else ""
        print(f"error: {where}{message}", file=sys.stderr)


def run(spec: Path, ref: str, main: str) -> int:
    """Ask, report, and answer with the exit status — 1 meaning a vendored file has moved."""
    pinned = git(spec, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()

    # The resting state. The first order is over, the pin is a commit on the
    # specification's `main`, and a branch that holds it is one somebody cut afterwards.
    if is_ancestor(spec, pinned, main):
        print(f"{pinned[:12]} is on {main} — the specification is not mid-flight")
        return 0

    holding = branches_holding(spec, pinned)
    if not holding:
        # A branch that was deleted, or a head that was force-pushed away. Which of those
        # it is does not change the answer here, and the ancestor check is where a pin
        # nothing carries is already refused.
        print(f"no branch holds {pinned[:12]} — the ancestor check is the one that answers that")
        return 0

    behind: list[Behind] = []
    for branch, head in holding:
        if head == pinned:
            print(f"{branch} is at the pin")
            continue
        files = moved_files(spec, pinned, head)
        if not files:
            print(f"{branch} has moved to {head[:12]}, and nothing vendored moved with it")
            continue
        behind.append(Behind(branch, head, files))

    if not behind:
        return 0

    for branch, _, files in behind:
        for change, path in files:
            problem(f"{change} on {branch} since {pinned[:12]} was pinned", file=path)
    heads = ", ".join(f"{branch} at {head[:12]}" for branch, head, _ in behind)
    problem(
        f"the specification has moved under SPEC_REF: {heads}. Re-vendor the files above "
        f"from that head and move SPEC_REF to it in the same commit."
    )
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spec", type=Path, required=True, help="a checkout of the specification repository"
    )
    parser.add_argument(
        "--ref",
        default="",
        help=f"the pin to ask about (default: what {PIN} in the working directory says)",
    )
    parser.add_argument(
        "--main",
        default="origin/main",
        help="the specification's main, as --spec names it (default: origin/main)",
    )
    args = parser.parse_args(argv)

    try:
        ref = args.ref.strip() or PIN.read_text(encoding="utf-8").strip()
        return run(args.spec.resolve(), ref, args.main)
    except (SpecPinError, OSError) as error:
        problem(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
