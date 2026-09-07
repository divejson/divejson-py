"""What the release-bump script computes and rewrites, without a runner or a remote.

`.github/scripts/release_bump.py` moves the two numbers a release is made of, and it runs
once per release with nobody watching the intermediate state — so a wrong answer reaches
PyPI. Everything here is one of its pure halves: what a window of commit subjects means,
and what the two rewrites do to a file.

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
    "release_bump", ROOT / ".github" / "scripts" / "release_bump.py"
)
assert _spec is not None and _spec.loader is not None
release_bump = importlib.util.module_from_spec(_spec)
sys.modules["release_bump"] = release_bump
_spec.loader.exec_module(release_bump)

ReleaseError = release_bump.ReleaseError


CHANGELOG = """\
# Changelog

Prose about the file.

## Unreleased

- **A thing landed.** With a second line.

## 0.2.0

- The one before.
"""


def test_the_window_this_release_is_cut_from_is_a_minor() -> None:
    """The subjects of `v0.2.0..main` as they stand, and the answer they must give."""
    subjects = [
        "feat(suunto): read the Suunto app's JSON logbooks (#5)",
        "feat(fit): read FIT dives, and a zip of them (#4)",
        "feat(ssrf): read Subsurface .ssrf logbooks (#3)",
        "feat!: a format registry, sniffing, convert() and a kind on every note (#2)",
    ]
    assert release_bump.next_version("0.2.0", subjects) == "0.3.0"


@pytest.mark.parametrize(
    ("subject", "kind"),
    [
        ("feat: a reader", "feature"),
        ("feat(fit): a reader", "feature"),
        ("feat!: a reader", "breaking"),
        ("feat(fit)!: a reader", "breaking"),
        ("fix: a factor", "fix"),
        ("fix(uddf)!: a factor", "breaking"),
        ("docs: a paragraph", "none"),
        ("chore: a bump", "none"),
        ("Initial commit", "none"),
        ("Merge pull request #5 from somewhere", "none"),
        ("feat something without the colon", "none"),
    ],
)
def test_what_one_subject_asks_for(subject: str, kind: str) -> None:
    assert release_bump.bump_kind([subject]) == kind


def test_a_break_anywhere_in_the_window_wins() -> None:
    assert release_bump.bump_kind(["docs: a paragraph", "fix!: a factor", "feat: a reader"]) == "breaking"


def test_a_feature_outranks_a_fix() -> None:
    assert release_bump.bump_kind(["fix: a factor", "feat: a reader"]) == "feature"


@pytest.mark.parametrize(
    ("current", "subjects", "expected"),
    [
        # Below 1.0 a break has no major to spend, so it lands where a feature does.
        ("0.2.0", ["feat!: a break"], "0.3.0"),
        ("0.2.0", ["feat: a reader"], "0.3.0"),
        ("0.2.1", ["feat: a reader"], "0.3.0"),
        ("0.2.0", ["fix: a factor"], "0.2.1"),
        ("0.2.3", ["docs: a paragraph"], "0.2.4"),
        # From 1.0.0 it is ordinary semver.
        ("1.4.2", ["feat!: a break"], "2.0.0"),
        ("1.4.2", ["feat: a reader"], "1.5.0"),
        ("1.4.2", ["fix: a factor"], "1.4.3"),
        ("1.4.2", ["chore: a bump"], "1.4.3"),
    ],
)
def test_the_bump(current: str, subjects: list[str], expected: str) -> None:
    assert release_bump.next_version(current, subjects) == expected


@pytest.mark.parametrize(
    "text",
    [
        "1.0", "0.2.0a1", "v0.2.0", "0.2.0+1", "01.2.0", "",
        # Python's `$` also matches before a trailing newline, so an anchored `match`
        # would accept these — and the version then lands in `__version__ = "..."` as two
        # physical lines, leaving a package that does not import and a tag already pushed.
        "0.2.0\n", "0.2.0 ", " 0.2.0", "0.2\n.0",
    ],
)
def test_a_version_that_is_not_three_integers_is_refused(text: str) -> None:
    """A suffix would make the bump ambiguous and the tag↔sdist check scale-dependent."""
    with pytest.raises(ReleaseError):
        release_bump.parse_version(text)


def test_the_version_line_moves_and_nothing_else_does() -> None:
    before = '"""A docstring mentioning __version__."""\n\n__version__ = "0.2.0"\n\nX = 1\n'
    after = release_bump.rewrite_version(before, "0.3.0")
    assert after == '"""A docstring mentioning __version__."""\n\n__version__ = "0.3.0"\n\nX = 1\n'


def test_a_package_declaring_no_version_is_refused() -> None:
    with pytest.raises(ReleaseError):
        release_bump.rewrite_version("X = 1\n", "0.3.0")


def test_a_package_declaring_two_versions_is_refused() -> None:
    with pytest.raises(ReleaseError):
        release_bump.rewrite_version('__version__ = "0.2.0"\n__version__ = "0.1.0"\n', "0.3.0")


def test_the_unreleased_heading_becomes_the_version_and_is_replaced_empty() -> None:
    after = release_bump.rewrite_changelog(CHANGELOG, "0.3.0")
    assert after == """\
# Changelog

Prose about the file.

## Unreleased

## 0.3.0

- **A thing landed.** With a second line.

## 0.2.0

- The one before.
"""


def test_the_entries_are_not_touched() -> None:
    """The rewrite is a heading, not an edit of anybody's notes."""
    after = release_bump.rewrite_changelog(CHANGELOG, "0.3.0")
    assert after.count("- **A thing landed.** With a second line.") == 1
    assert after.count("- The one before.") == 1


def test_an_empty_unreleased_section_is_refused() -> None:
    """A release whose notes say nothing is the one failure nobody notices in time."""
    empty = "# Changelog\n\n## Unreleased\n\n## 0.2.0\n\n- The one before.\n"
    with pytest.raises(ReleaseError, match="empty"):
        release_bump.rewrite_changelog(empty, "0.3.0")


def test_an_unreleased_section_that_ends_the_file_still_reads() -> None:
    trailing = "# Changelog\n\n## Unreleased\n\n- A thing.\n"
    assert release_bump.rewrite_changelog(trailing, "0.3.0") == (
        "# Changelog\n\n## Unreleased\n\n## 0.3.0\n\n- A thing.\n"
    )


@pytest.mark.parametrize(
    "text",
    [
        "# Changelog\n\n## 0.2.0\n\n- The one before.\n",
        "# Changelog\n\n## Unreleased\n\n- A thing.\n\n## Unreleased\n\n- Another.\n",
    ],
)
def test_a_changelog_without_exactly_one_unreleased_heading_is_refused(text: str) -> None:
    with pytest.raises(ReleaseError, match="headings"):
        release_bump.rewrite_changelog(text, "0.3.0")


def test_this_repositorys_own_changelog_has_the_heading_the_script_looks_for() -> None:
    """The rewrite is a string match against a file a human edits by hand."""
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.count("\n## Unreleased\n") == 1


def test_this_repositorys_own_version_line_is_the_one_the_script_moves() -> None:
    init = (ROOT / "divejson" / "__init__.py").read_text(encoding="utf-8")
    assert release_bump.read_version(init) == __import__("divejson").__version__



# --- the half that talks to git -------------------------------------------------------
#
# `plan` is where every refusal lives, and each of them is the last thing standing between
# a wrong number and PyPI. They are exercised in a repository built for the test: git's own
# configuration is passed per invocation with `-c` and never written anywhere, so a global
# signing setting can neither break these nor be changed by them.

GIT_CONFIG = [
    "-c", "user.name=Test",
    "-c", "user.email=test@example.invalid",
    "-c", "commit.gpgsign=false",
    "-c", "tag.gpgsign=false",
    "-c", "init.defaultBranch=main",
]

# The window this repository is actually cutting at, plus the tag that closed the one
# before: three commits, one tag, and a `!` that must not reach for a major.
HISTORY = [
    ("chore: the tree", None),
    ("chore: release 0.2.0", "v0.2.0"),
    ("feat!: a registry", None),
    ("feat: a reader", None),
]


def build_repo(path: Path, version: str, history: list[tuple[str, str | None]]) -> Path:
    """A repository with `history` — (subject, tag) pairs, the first carrying the tree."""
    def run(*args: str) -> None:
        subprocess.run(["git", *GIT_CONFIG, *args], cwd=path, check=True, capture_output=True)

    path.mkdir(parents=True, exist_ok=True)
    (path / "divejson").mkdir()
    (path / "divejson" / "__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    (path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## Unreleased\n\n- A thing.\n\n## 0.1.0\n\n- The first.\n",
        encoding="utf-8",
    )
    run("init", "--quiet")
    run("add", "-A")
    for index, (subject, tag) in enumerate(history):
        run("commit", "--quiet", *([] if index == 0 else ["--allow-empty"]), "-m", subject)
        if tag:
            run("tag", tag)
    return path


def declared(root: Path) -> str:
    return (root / "divejson" / "__init__.py").read_text(encoding="utf-8")


def test_the_window_since_the_last_tag_is_what_the_bump_is_read_from(tmp_path: Path) -> None:
    root = build_repo(tmp_path / "repo", "0.2.0", HISTORY)
    current, version, tag, subjects = release_bump.plan(root, declared(root), None)
    assert (current, version, tag) == ("0.2.0", "0.3.0", "v0.2.0")
    assert subjects == ["feat: a reader", "feat!: a registry"]


def test_a_version_the_tree_declares_but_nobody_tagged_is_refused(tmp_path: Path) -> None:
    """A bump landed and its tag never did: tag what is there, do not bump past it."""
    root = build_repo(tmp_path / "repo", "0.3.0", HISTORY)
    with pytest.raises(ReleaseError, match="v0.3.0"):
        release_bump.plan(root, declared(root), None)


def test_a_window_with_nothing_in_it_is_refused(tmp_path: Path) -> None:
    root = build_repo(tmp_path / "repo", "0.2.0", HISTORY[:2])
    with pytest.raises(ReleaseError, match="nothing has landed"):
        release_bump.plan(root, declared(root), None)


def test_a_requested_version_that_is_not_ahead_is_refused(tmp_path: Path) -> None:
    root = build_repo(tmp_path / "repo", "0.2.0", HISTORY)
    with pytest.raises(ReleaseError, match="not ahead"):
        release_bump.plan(root, declared(root), "0.2.0")


def test_a_requested_version_whose_tag_exists_is_refused(tmp_path: Path) -> None:
    """PyPI refuses a duplicate, and by then `main` has already moved."""
    history: list[tuple[str, str | None]] = [("chore: the tree", "v0.9.0"), *HISTORY[1:]]
    root = build_repo(tmp_path / "repo", "0.2.0", history)
    with pytest.raises(ReleaseError, match="already exists"):
        release_bump.plan(root, declared(root), "0.9.0")


def test_a_run_writes_both_files_and_names_the_version_it_wrote(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = build_repo(tmp_path / "repo", "0.2.0", HISTORY)
    # The workflow reads the version back out of this file to name the branch, the pull
    # request and the tag. Pointed at the test's own copy, because `GITHUB_OUTPUT` is set
    # for every Actions step — so under CI an unset one would append to the real file the
    # runner made for `pytest`.
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    assert release_bump.main(["--root", str(root)]) == 0
    assert '__version__ = "0.3.0"' in declared(root)
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## Unreleased\n\n## 0.3.0\n\n- A thing.\n" in changelog
    assert "0.3.0" in capsys.readouterr().out
    assert output.read_text(encoding="utf-8") == "version=0.3.0\nprevious=0.2.0\n"


def test_a_version_typed_with_whitespace_round_it_is_the_version_it_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dispatch form's value arrives verbatim, and a stray newline is not a version."""
    root = build_repo(tmp_path / "repo", "0.2.0", HISTORY)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "github-output"))

    assert release_bump.main(["--root", str(root), "--version", " 0.4.0\n"]) == 0
    assert declared(root) == '__version__ = "0.4.0"\n'
    assert (tmp_path / "github-output").read_text(encoding="utf-8").startswith("version=0.4.0\n")


def test_a_refusal_leaves_the_tree_alone_and_names_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither file is written until both rewrites have been computed."""
    root = build_repo(tmp_path / "repo", "0.2.0", HISTORY)
    (root / "CHANGELOG.md").write_text(
        "# Changelog\n\n## Unreleased\n\n## 0.1.0\n\n- The first.\n", encoding="utf-8"
    )
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    before = declared(root)

    assert release_bump.main(["--root", str(root)]) == 1
    assert declared(root) == before
    assert not output.exists()
