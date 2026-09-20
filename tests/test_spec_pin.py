"""What the mid-flight pin check asks of a specification repository, and of what.

`.github/scripts/spec_pin.py` is the only thing watching the window where `SPEC_REF`
points at a specification branch rather than at a commit on its `main`: the byte check
passes against a superseded pin and the ancestor check is red throughout, so a vendored
file going a commit behind is invisible without this. The window is also the one state a
pull request here cannot be in while it is being reviewed, which is why every situation
below is built rather than waited for.

The repositories are built in place, and git's own configuration is passed per invocation
with `-c` so that a global signing setting can neither break these nor be changed by them.
The script is loaded by path because `.github/` is not an importable package and has no
business becoming one.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import ROOT

_spec = importlib.util.spec_from_file_location(
    "spec_pin", ROOT / ".github" / "scripts" / "spec_pin.py"
)
assert _spec is not None and _spec.loader is not None
spec_pin = importlib.util.module_from_spec(_spec)
sys.modules["spec_pin"] = spec_pin
_spec.loader.exec_module(spec_pin)


GIT_CONFIG = [
    "-c", "user.name=Test",
    "-c", "user.email=test@example.invalid",
    "-c", "commit.gpgsign=false",
    "-c", "tag.gpgsign=false",
    "-c", "init.defaultBranch=main",
]

# The three trees this repository vendors, one file each — enough for a diff to have
# somewhere to land under every one of them.
OWNED = {
    "schema/1.0/divejson.schema.json": '{"$id": "https://divejson.org/schema/1.0"}\n',
    "fixtures/valid/a-dive.divejson": '{"divejson": "1.0", "dives": []}\n',
    "docs/uddf-mapping.md": "# UDDF\n\nWhich element a reader reads.\n",
}

# What the specification carries and this repository does not: its normative prose, and
# its own CI. A commit touching only these is the case a check like this gets wrong by
# failing on it.
UNVENDORED = {
    "spec/divejson.md": "# DiveJSON\n\nThe format.\n",
    ".github/workflows/ci.yml": "name: CI\n",
}


def run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *GIT_CONFIG, *args], cwd=repo, check=True, capture_output=True)


def write(repo: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def commit(repo: Path, subject: str, files: dict[str, str]) -> None:
    write(repo, files)
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "--quiet", "-m", subject)


def local_branches(repo: Path) -> list[str]:
    listed = subprocess.run(
        ["git", *GIT_CONFIG, "for-each-ref", "--format=%(refname:short)", "refs/heads"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return listed.stdout.split()


def rev(repo: Path, ref: str) -> str:
    listed = subprocess.run(
        ["git", *GIT_CONFIG, "rev-parse", "--verify", ref],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return listed.stdout.strip()


@pytest.fixture(autouse=True)
def plain_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """A runner's annotations are a different string, and CI runs this suite on a runner."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


@pytest.fixture
def spec(tmp_path: Path) -> Path:
    """A specification repository mid-flight, in the shape the first order makes.

    `main` at a release, and above it a branch of three commits: the one this repository
    would pin and vendor from, one that edits a document the specification owns, and one
    that touches only files it owns alone. So `spec-1-trip-parts~2` is the pin that has
    gone stale, `~1` is the pin that has not, and the branch itself is the head.
    """
    repo = tmp_path / "divejson"
    repo.mkdir()
    run_git(repo, "init", "--quiet")
    commit(repo, "chore(release): 0.8.0", {**OWNED, **UNVENDORED})

    run_git(repo, "checkout", "--quiet", "-b", "spec-1-trip-parts")
    commit(
        repo,
        "feat(spec)!: a trip is a sequence of dated parts",
        {
            "schema/1.0/divejson.schema.json": '{"$id": "https://divejson.org/schema/1.0", "parts": true}\n',
            "fixtures/valid/a-dive.divejson": '{"divejson": "1.0", "dives": [], "trips": []}\n',
        },
    )
    commit(
        repo,
        "docs(uddf): a nameless, dateless part loses its place and itself",
        {"docs/uddf-mapping.md": "# UDDF\n\nWhich element a reader reads, and which wins.\n"},
    )
    commit(
        repo,
        "ci: the corpus runs on the implementation's head while this is in flight",
        {
            "spec/divejson.md": "# DiveJSON\n\nThe format, restated.\n",
            ".github/workflows/ci.yml": "name: CI\n# against the implementation's head\n",
        },
    )

    # Left on `main`, because a clone that checked the pin out is what CI hands the script
    # and the branch being current would hide a bug in how its head is read.
    run_git(repo, "checkout", "--quiet", "main")
    return repo


@pytest.fixture
def cloned(spec: Path, tmp_path: Path) -> Path:
    """The same specification as `git clone` leaves it, detached at the stale pin.

    Which is the shape CI hands the script, and it is not the shape above: a clone keeps
    every branch but `main` under `refs/remotes/origin/`, so a check that asked about
    local branches alone would find nothing here and pass in every state.
    """
    clone = tmp_path / "spec-clone"
    run_git(tmp_path, "clone", "--quiet", str(spec), str(clone))
    run_git(clone, "checkout", "--quiet", "--detach", rev(spec, "spec-1-trip-parts~2"))
    return clone


def check(
    spec: Path, ref: str, capsys: pytest.CaptureFixture[str], main: str = "main"
) -> tuple[int, str]:
    """The script's exit status, and everything it said on either stream."""
    status = spec_pin.main(["--spec", str(spec), "--ref", ref, "--main", main])
    captured = capsys.readouterr()
    return status, captured.out + captured.err


def test_a_pin_the_branch_has_moved_a_document_past_names_that_document(
    spec: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The failure this exists for: one file behind, and both repositories green without it."""
    status, said = check(spec, "spec-1-trip-parts~2", capsys)
    assert status == 1
    assert "docs/uddf-mapping.md" in said
    assert "changed" in said


def test_and_names_nothing_the_branch_did_not_move(
    spec: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The pin's own commit moved the schema and a fixture; what follows it did not."""
    _, said = check(spec, "spec-1-trip-parts~2", capsys)
    assert "schema/" not in said
    assert "fixtures/" not in said


def test_a_branch_that_moved_nothing_vendored_is_not_a_failure(
    spec: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Only the prose and the specification's own CI followed this pin, and neither is here.

    Failing on a moved ref rather than on a moved file would fail here, and a check that
    fires only mid-flight has to be silent in the cases that do not matter or it is noise.
    """
    status, said = check(spec, "spec-1-trip-parts~1", capsys)
    assert status == 0
    assert "has moved" in said
    assert "spec/divejson.md" not in said


def test_a_pin_at_the_branchs_head_passes(
    spec: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, said = check(spec, "spec-1-trip-parts", capsys)
    assert status == 0
    assert "at the pin" in said


def test_a_document_the_specification_gains_is_named_too(
    spec: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A vendored tree gaining a file is a re-vendor as much as one changing is.

    The byte check walks the specification's files and compares each to this tree, so a
    file added on the branch fails it the moment the pin moves — from a file this
    repository does not have at all.
    """
    run_git(spec, "checkout", "--quiet", "spec-1-trip-parts")
    commit(spec, "docs(fit): how a FIT dive maps", {"docs/fit-mapping.md": "# FIT\n"})
    run_git(spec, "checkout", "--quiet", "main")

    status, said = check(spec, "spec-1-trip-parts~3", capsys)
    assert status == 1
    assert "added" in said
    assert "docs/fit-mapping.md" in said


def test_a_fixture_the_specification_drops_is_named_too(
    spec: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The byte check cannot see this one at all, before or after the pin moves.

    It is a subset check — this repository may carry pairs the specification has not
    adopted — so a fixture the specification deletes stays here silently, and re-vendoring
    means deleting it rather than copying it.
    """
    run_git(spec, "checkout", "--quiet", "spec-1-trip-parts")
    run_git(spec, "rm", "--quiet", "fixtures/valid/a-dive.divejson")
    run_git(spec, "commit", "--quiet", "-m", "fix(fixtures): the pair moves to the reader")
    run_git(spec, "checkout", "--quiet", "main")

    status, said = check(spec, "spec-1-trip-parts~3", capsys)
    assert status == 1
    assert "removed" in said
    assert "fixtures/valid/a-dive.divejson" in said


def test_a_pin_on_main_asks_nothing(spec: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The resting state, where a branch holding the pin is one cut from main afterwards.

    The branch here holds `main`'s head and has moved a vendored document past it, and the
    answer is still silence: a document adopted into the specification after this pin is
    the next pin bump's business, not this one's.
    """
    status, said = check(spec, "main", capsys)
    assert status == 0
    assert "not mid-flight" in said
    assert "docs/uddf-mapping.md" not in said


def test_a_pin_no_branch_holds_is_left_to_the_ancestor_check(
    spec: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A branch that was deleted, which is the state this repository's own history is in."""
    pinned = rev(spec, "spec-1-trip-parts~2")
    run_git(spec, "branch", "--quiet", "-D", "spec-1-trip-parts")

    status, said = check(spec, pinned, capsys)
    assert status == 0
    assert "no branch holds" in said


def test_a_branch_that_does_not_hold_the_pin_is_not_asked_about(
    spec: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two changes in flight at once is the ordinary case, and only one of them is this pin."""
    run_git(spec, "checkout", "--quiet", "-b", "spec-2-draft-becomes-1-0", "main")
    commit(spec, "docs(spec)!: the draft becomes 1.0", {"docs/uddf-mapping.md": "# UDDF 1.0\n"})
    run_git(spec, "checkout", "--quiet", "main")

    status, said = check(spec, "spec-1-trip-parts", capsys)
    assert status == 0
    assert "spec-2-draft-becomes-1-0" not in said


def test_a_ref_the_specification_does_not_have_is_an_error(
    spec: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pin nothing can be asked about fails rather than passing for want of an answer."""
    status, said = check(spec, "0" * 40, capsys)
    assert status == 1
    assert "rev-parse" in said


def test_the_diff_is_taken_over_the_trees_the_specification_owns(spec: Path) -> None:
    """The unit underneath, asked directly: a pathspec, not the whole tree."""
    pinned = rev(spec, "spec-1-trip-parts~2")
    head = rev(spec, "spec-1-trip-parts")
    assert spec_pin.moved_files(spec, pinned, head) == [("changed", "docs/uddf-mapping.md")]


def test_every_branch_holding_the_pin_is_found_with_the_head_it_has_now(spec: Path) -> None:
    pinned = rev(spec, "spec-1-trip-parts~2")
    assert spec_pin.branches_holding(spec, pinned) == [
        ("spec-1-trip-parts", rev(spec, "spec-1-trip-parts"))
    ]


def test_the_branch_is_found_in_a_clone_where_it_is_only_a_remote_ref(
    spec: Path, cloned: Path
) -> None:
    """The half that runs in CI, which every test above reaches by the other half.

    Asked of a repository built in place, `refs/heads` alone answers everything and the
    remote half can be deleted with the suite still green — so this asks a clone, where
    the only local branch is `main` and the pin's branch has nowhere else to be found.
    """
    assert local_branches(cloned) == ["main"]
    assert spec_pin.branches_holding(cloned, rev(spec, "spec-1-trip-parts~2")) == [
        ("origin/spec-1-trip-parts", rev(spec, "spec-1-trip-parts"))
    ]


def test_a_clone_detached_at_a_stale_pin_names_the_document(
    spec: Path, cloned: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """End to end in CI's own shape: a clone, a detached HEAD, a sha, and `origin/main`."""
    status, said = check(cloned, rev(spec, "spec-1-trip-parts~2"), capsys, main="origin/main")
    assert status == 1
    assert "origin/spec-1-trip-parts" in said
    assert "docs/uddf-mapping.md" in said


# --- and the two things the workflow has to keep saying --------------------------------

CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def test_the_trees_here_are_the_trees_the_byte_check_names() -> None:
    """One list of vendored trees in two steps of one job, so the two are asserted equal.

    To the closing bracket of the byte check's own command, which is what makes this an
    equality: without it a list here that had *lost* its last tree would still be a prefix
    of the workflow's, and match.
    """
    assert f"ls-files -- {' '.join(spec_pin.OWNED)})" in CI


def test_this_check_runs_before_the_one_that_is_red_for_the_whole_window() -> None:
    """A step after a failing step never runs, and the ancestor check fails throughout it.

    Ordered the other way this would be a check that passes in every state but the one it
    was written for, and nothing about it would look wrong.
    """
    assert CI.index("spec_pin.py") < CI.index("merge-base --is-ancestor")
