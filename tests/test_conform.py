"""What `divejson conform` decides, and why it has two ways of failing.

The command is the contract between a corpus and an implementation, and both this
repository's CI and the specification repository's run it — so the assertions here are
about the *status*, not about the wording: 1 means a case ran and the answer was wrong, 2
means the corpus's shape stopped cases from running at all. A suite that could not tell
those apart would read an empty corpus as a passing one.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from helpers import FIXTURES

from divejson.cli import main
from divejson.conform import DIFF_LINES, IGNORED, compared, known_formats, run


def _corpus(tmp_path: Path) -> Path:
    """One of everything, so that a mutation is the only thing a test changes."""
    corpus = tmp_path / "corpus"
    (corpus / "valid").mkdir(parents=True)
    (corpus / "invalid").mkdir()
    (corpus / "uddf").mkdir()
    shutil.copy(FIXTURES / "valid" / "minimal.divejson", corpus / "valid")
    shutil.copy(FIXTURES / "invalid" / "missing-format.divejson", corpus / "invalid")
    for name in ("mix-only-cylinder.uddf", "mix-only-cylinder.divejson"):
        shutil.copy(FIXTURES / "uddf" / name, corpus / "uddf")
    return corpus


def _write(path: Path, document: dict) -> None:
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def test_the_vendored_corpus_conforms(capsys) -> None:
    """The whole point of the vendored tree: this implementation answers for all of it.

    `--strict`, because a format this implementation reads and the corpus has no pairs
    for is exactly the gap a release must not ship over.
    """
    assert main(["conform", str(FIXTURES), "--strict"]) == 0
    out = capsys.readouterr().out
    assert "valid:" in out and "invalid:" in out and "uddf:" in out


def test_a_corpus_with_nothing_in_it_is_a_shape_error(tmp_path, capsys) -> None:
    """The emptiness rule, which is what a hand-kept fixture count used to be."""
    assert main(["conform", str(tmp_path)]) == 2
    assert "holds no cases" in capsys.readouterr().out


def test_a_corpus_that_is_not_a_directory_is_a_shape_error(tmp_path, capsys) -> None:
    assert main(["conform", str(tmp_path / "absent")]) == 2
    assert "is not a directory" in capsys.readouterr().out


def test_the_baseline_corpus_passes(tmp_path) -> None:
    """Every mutation below starts from here, so this is what makes them mean anything."""
    assert main(["conform", str(_corpus(tmp_path)), "--strict"]) == 0


def test_a_pair_for_a_format_this_implementation_does_not_read(tmp_path, capsys) -> None:
    """Status 2, named — a corpus that has run ahead of this implementation.

    This is the case that keeps a release honest about which adapters it carries: pairs
    for a format it cannot read are cases nobody ran, and a suite that passed them would
    be reporting the opposite.
    """
    corpus = _corpus(tmp_path)
    (corpus / "ssrf").mkdir()
    (corpus / "ssrf" / "logbook.ssrf").write_text("<divelog/>", encoding="utf-8")
    _write(corpus / "ssrf" / "logbook.divejson", {"format": "divejson", "version": "1.0"})

    assert main(["conform", str(corpus)]) == 2
    out = capsys.readouterr().out
    assert "ssrf" in out
    assert "does not read" in out


def test_a_format_this_implementation_does_not_read_cannot_be_skipped(tmp_path, capsys) -> None:
    """`--skip` is not a way to make an unanswerable corpus pass."""
    corpus = _corpus(tmp_path)
    (corpus / "ssrf").mkdir()
    (corpus / "ssrf" / "logbook.ssrf").write_text("<divelog/>", encoding="utf-8")

    assert main(["conform", str(corpus), "--skip", "ssrf"]) == 2
    assert "no such format: ssrf" in capsys.readouterr().out


def test_a_pair_directory_with_no_inputs_is_a_shape_error(tmp_path, capsys) -> None:
    corpus = _corpus(tmp_path)
    for path in (corpus / "uddf").iterdir():
        path.unlink()

    assert main(["conform", str(corpus)]) == 2
    assert "holds no inputs" in capsys.readouterr().out


def test_valid_holding_no_documents_is_a_shape_error(tmp_path, capsys) -> None:
    corpus = _corpus(tmp_path)
    (corpus / "valid" / "minimal.divejson").unlink()

    assert main(["conform", str(corpus)]) == 2
    assert "valid: holds no documents" in capsys.readouterr().out


def test_an_input_with_no_expected_document_is_a_shape_error(tmp_path, capsys) -> None:
    corpus = _corpus(tmp_path)
    (corpus / "uddf" / "mix-only-cylinder.divejson").unlink()

    assert main(["conform", str(corpus)]) == 2
    assert "has no mix-only-cylinder.divejson beside it" in capsys.readouterr().out


def test_an_expected_document_with_no_input_is_a_shape_error(tmp_path, capsys) -> None:
    corpus = _corpus(tmp_path)
    shutil.copy(
        corpus / "uddf" / "mix-only-cylinder.divejson", corpus / "uddf" / "leftover.divejson"
    )

    assert main(["conform", str(corpus)]) == 2
    assert "leftover.divejson: is an expected document with no input" in capsys.readouterr().out


def test_a_valid_document_that_does_not_conform_is_a_failure(tmp_path, capsys) -> None:
    corpus = _corpus(tmp_path)
    shutil.copy(FIXTURES / "invalid" / "missing-format.divejson", corpus / "valid")

    assert main(["conform", str(corpus)]) == 1
    assert "does not conform" in capsys.readouterr().out


def test_an_invalid_document_that_conforms_is_a_failure(tmp_path, capsys) -> None:
    corpus = _corpus(tmp_path)
    shutil.copy(FIXTURES / "valid" / "minimal.divejson", corpus / "invalid")

    assert main(["conform", str(corpus)]) == 1
    assert "conforms, and every document here must not" in capsys.readouterr().out


def test_a_document_that_cannot_be_parsed_counts_as_refused(tmp_path) -> None:
    """Spec §9: a duplicate member name is refused before the schema is ever reached."""
    corpus = _corpus(tmp_path)
    shutil.copy(FIXTURES / "invalid" / "duplicate-json-member.divejson", corpus / "invalid")

    assert main(["conform", str(corpus), "--strict"]) == 0


def test_a_valid_document_that_is_not_json_at_all_is_a_failure(tmp_path, capsys) -> None:
    corpus = _corpus(tmp_path)
    (corpus / "valid" / "broken.divejson").write_text("{", encoding="utf-8")

    assert main(["conform", str(corpus)]) == 1
    assert "is not readable as JSON" in capsys.readouterr().out


def test_a_mismatched_pair_fails_and_prints_a_diff_of_both_sides(tmp_path, capsys) -> None:
    corpus = _corpus(tmp_path)
    expected_path = corpus / "uddf" / "mix-only-cylinder.divejson"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    expected["dives"][0]["max_depth"] = 28.5
    _write(expected_path, expected)

    assert main(["conform", str(corpus)]) == 1
    out = capsys.readouterr().out
    assert "converts to a document that differs from mix-only-cylinder.divejson" in out
    assert "--- expected" in out and "+++ produced" in out
    assert '"max_depth": 28.5' in out


def test_a_wholly_different_expected_document_does_not_bury_the_log(tmp_path, capsys) -> None:
    corpus = _corpus(tmp_path)
    shutil.copy(
        FIXTURES / "valid" / "demo-logbook.divejson", corpus / "uddf" / "mix-only-cylinder.divejson"
    )

    assert main(["conform", str(corpus)]) == 1
    out = capsys.readouterr().out
    assert "more lines of difference" in out
    # The diff and the count that replaces the rest of it are what the finding indents.
    indented = [line for line in out.splitlines() if line.startswith("  ")]
    assert len(indented) == DIFF_LINES + 1


def test_an_expected_document_that_does_not_itself_conform_is_a_failure(tmp_path, capsys) -> None:
    """The expected side of a pair is a DiveJSON document like any other."""
    corpus = _corpus(tmp_path)
    expected_path = corpus / "uddf" / "mix-only-cylinder.divejson"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    expected["dives"][0]["avg_depth"] = 999.0
    _write(expected_path, expected)

    assert main(["conform", str(corpus)]) == 1
    assert "does not itself conform" in capsys.readouterr().out


def test_a_registered_format_with_no_pairs_is_a_warning_and_an_error_under_strict(
    tmp_path, capsys
) -> None:
    corpus = _corpus(tmp_path)
    shutil.rmtree(corpus / "uddf")

    assert main(["conform", str(corpus)]) == 0
    assert "an error under --strict" in capsys.readouterr().out
    assert main(["conform", str(corpus), "--strict"]) == 2
    assert "the corpus has no pairs for it" in capsys.readouterr().out


def test_only_and_skip_choose_which_pairs_run(tmp_path, capsys) -> None:
    """Neither reaches `valid/` or `invalid/`: those are owed whatever is registered."""
    corpus = _corpus(tmp_path)

    assert main(["conform", str(corpus), "--skip", "uddf"]) == 0
    out = capsys.readouterr().out
    assert "uddf:" not in out
    assert "valid:" in out and "invalid:" in out

    assert main(["conform", str(corpus), "--only", "uddf"]) == 0
    assert "uddf:" in capsys.readouterr().out


def test_a_write_directory_is_for_a_format_this_implementation_does_not_write(
    tmp_path, capsys
) -> None:
    corpus = _corpus(tmp_path)
    (corpus / "write" / "uddf").mkdir(parents=True)
    shutil.copy(
        corpus / "uddf" / "mix-only-cylinder.divejson", corpus / "write" / "uddf" / "one.divejson"
    )

    assert main(["conform", str(corpus)]) == 2
    out = capsys.readouterr().out
    assert "write/uddf" in out and "does not write" in out


def test_an_empty_write_directory_is_a_shape_error(tmp_path, capsys) -> None:
    corpus = _corpus(tmp_path)
    (corpus / "write").mkdir()

    assert main(["conform", str(corpus)]) == 2
    assert "write: holds no writer-pair directories" in capsys.readouterr().out


def test_the_result_counts_what_it_checked(tmp_path) -> None:
    result = run(_corpus(tmp_path), strict=True)
    assert result.status == 0
    assert {group.name: group.checked for group in result.groups} == {
        "valid": 1,
        "invalid": 1,
        "uddf": 1,
    }
    assert result.checked == 3


def test_compared_drops_exactly_the_two_members_a_run_owns() -> None:
    """The comparison rule a port and an application both have to use."""
    document = json.loads(
        (FIXTURES / "uddf" / "subsurface.divejson").read_text(encoding="utf-8")
    )
    assert set(IGNORED) <= set(document)
    assert set(document) - set(compared(document)) == set(IGNORED)


def test_the_registered_formats_are_the_ones_with_pair_directories() -> None:
    """A corpus directory is named for a format id, and that is the whole coupling."""
    assert known_formats() == {"uddf"}
    assert (FIXTURES / "uddf").is_dir()
