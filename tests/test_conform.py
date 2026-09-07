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
from divejson.conform import DIFF_LINES, IGNORED, compared, run
from divejson.registry import adapter_for, known_formats, read_formats, write_formats, writer_for

# The stand-in for a format nothing here registers. It used to be `ssrf`, which stopped
# standing in for anything the day the Subsurface reader landed and took several tests with
# it — so this one is a name no adapter will ever claim, rather than the next format along.
UNREAD = "unregistered"

# One pair per registered format, since `--strict` is what most of these run under and a
# format with no pairs is a shape error there. Derived from the registry rather than
# listed, so registering a reader without a corpus directory fails here and not in CI.
# The smallest pair each format has, since every test below copies the whole set: the
# Suunto Ocean's 4,295 records make a 165 KB expectation, and the D5's 200 make a 15 KB one
# that exercises the same reader.
PAIRS = {
    "uddf": "mix-only-cylinder",
    "ssrf": "refusals",
    "fit": "suunto-d5",
    "suunto_json": "header-only",
}

# The same, for the formats this implementation *writes*: `--strict` wants a `write/<id>/`
# directory for each of those too, and for the same reason.
WRITER_PAIRS = {"uddf": "opendiving"}


def _corpus(tmp_path: Path) -> Path:
    """One of everything, so that a mutation is the only thing a test changes."""
    corpus = tmp_path / "corpus"
    (corpus / "valid").mkdir(parents=True)
    (corpus / "invalid").mkdir()
    shutil.copy(FIXTURES / "valid" / "minimal.divejson", corpus / "valid")
    shutil.copy(FIXTURES / "invalid" / "missing-format.divejson", corpus / "invalid")
    for fmt in read_formats():
        (corpus / fmt).mkdir()
        stem = PAIRS[fmt]
        # The adapter's own first suffix rather than the format id: a pair directory is
        # named for the id and its inputs are named for the extension a file of that
        # format carries, and for `suunto_json` those are not the same string.
        for name in (f"{stem}{adapter_for(fmt).suffixes[0]}", f"{stem}.divejson"):
            shutil.copy(FIXTURES / fmt / name, corpus / fmt)
    for fmt in write_formats():
        directory = corpus / "write" / fmt
        directory.mkdir(parents=True)
        stem = WRITER_PAIRS[fmt]
        for name in (f"{stem}.divejson", f"{stem}{writer_for(fmt).suffix}"):
            shutil.copy(FIXTURES / "write" / fmt / name, directory)
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
    (corpus / UNREAD).mkdir()
    (corpus / UNREAD / f"logbook.{UNREAD}").write_text("<divelog/>", encoding="utf-8")
    _write(corpus / UNREAD / "logbook.divejson", {"format": "divejson", "version": "1.0"})

    assert main(["conform", str(corpus)]) == 2
    out = capsys.readouterr().out
    assert UNREAD in out
    assert "does not read" in out


def test_a_zip_directory_is_not_a_format_either(tmp_path, capsys) -> None:
    """`sniff` answers `zip`, and that is a container rather than a format id.

    Nothing registers it, so a corpus that kept archives in a `zip/` directory would be
    claiming this implementation reads a format it does not — the same status 2 as any
    other name nothing answers to.
    """
    corpus = _corpus(tmp_path)
    (corpus / "zip").mkdir()
    (corpus / "zip" / "logbook.zip").write_bytes(b"PK\x03\x04")

    assert main(["conform", str(corpus)]) == 2
    out = capsys.readouterr().out
    assert "zip: is a format this implementation does not read" in out
    for fmt in read_formats():
        assert fmt in out


def test_a_format_this_implementation_does_not_read_cannot_be_skipped(tmp_path, capsys) -> None:
    """`--skip` is not a way to make an unanswerable corpus pass."""
    corpus = _corpus(tmp_path)
    (corpus / UNREAD).mkdir()
    (corpus / UNREAD / f"logbook.{UNREAD}").write_text("<divelog/>", encoding="utf-8")

    assert main(["conform", str(corpus), "--skip", UNREAD]) == 2
    assert f"no such format: {UNREAD}" in capsys.readouterr().out


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
    """Reading a format and writing it are separate registrations under one id.

    `ssrf` is the case that matters, and it is not a hypothetical: this implementation
    reads Subsurface's save format and cannot write it, so a corpus that carries writer
    pairs for it is one this build cannot answer for — however fluently it answers for the
    same format's reader pairs two directories away.
    """
    corpus = _corpus(tmp_path)
    (corpus / "write" / "ssrf").mkdir(parents=True)
    shutil.copy(
        corpus / "uddf" / "mix-only-cylinder.divejson", corpus / "write" / "ssrf" / "one.divejson"
    )

    assert main(["conform", str(corpus)]) == 2
    out = capsys.readouterr().out
    assert "write/ssrf" in out and "does not write" in out


def test_an_empty_write_directory_is_a_shape_error(tmp_path, capsys) -> None:
    corpus = _corpus(tmp_path)
    shutil.rmtree(corpus / "write")
    (corpus / "write").mkdir()

    assert main(["conform", str(corpus)]) == 2
    assert "write: holds no writer-pair directories" in capsys.readouterr().out


def test_a_writer_pair_whose_expected_file_is_missing_is_a_shape_error(tmp_path, capsys) -> None:
    corpus = _corpus(tmp_path)
    (corpus / "write" / "uddf" / f"{WRITER_PAIRS['uddf']}.uddf").unlink()

    assert main(["conform", str(corpus)]) == 2
    assert "beside it to be checked against" in capsys.readouterr().out


def test_an_expected_file_with_no_document_beside_it_is_a_shape_error(tmp_path, capsys) -> None:
    """The reader pair's orphan rule, inverted with the pair: here the document is input."""
    corpus = _corpus(tmp_path)
    shutil.copy(
        corpus / "write" / "uddf" / f"{WRITER_PAIRS['uddf']}.uddf", corpus / "write" / "uddf" / "stray.uddf"
    )

    assert main(["conform", str(corpus)]) == 2
    assert "is an expected file with no document beside it" in capsys.readouterr().out


def test_a_write_directory_with_no_documents_is_a_shape_error(tmp_path, capsys) -> None:
    corpus = _corpus(tmp_path)
    (corpus / "write" / "uddf" / f"{WRITER_PAIRS['uddf']}.divejson").unlink()

    assert main(["conform", str(corpus)]) == 2
    assert "holds no documents to write" in capsys.readouterr().out


def test_a_mismatched_writer_pair_fails_and_prints_a_diff(tmp_path, capsys) -> None:
    """Status 1 rather than 2: the case ran, and the writer's answer was not the corpus's."""
    corpus = _corpus(tmp_path)
    expected = corpus / "write" / "uddf" / f"{WRITER_PAIRS['uddf']}.uddf"
    expected.write_bytes(expected.read_bytes().replace(b"<greatestdepth>29.6", b"<greatestdepth>28.5"))

    assert main(["conform", str(corpus)]) == 1
    out = capsys.readouterr().out
    assert "differs from" in out
    assert "greatestdepth" in out


def test_a_writer_pair_ignores_the_generator_element(tmp_path) -> None:
    """A release moves the version stamped there, and no corpus can be re-cut for that."""
    corpus = _corpus(tmp_path)
    expected = corpus / "write" / "uddf" / f"{WRITER_PAIRS['uddf']}.uddf"
    expected.write_bytes(expected.read_bytes().replace(b"<version>", b"<version>9.9.9-", 1))

    assert main(["conform", str(corpus), "--strict"]) == 0


def test_a_writer_pair_whose_document_does_not_conform_fails(tmp_path, capsys) -> None:
    """A pair that starts from a document the format rejects proves nothing about a writer."""
    corpus = _corpus(tmp_path)
    source = corpus / "write" / "uddf" / f"{WRITER_PAIRS['uddf']}.divejson"
    document = json.loads(source.read_text(encoding="utf-8"))
    document["dives"][0]["max_depth"] = -1
    _write(source, document)

    assert main(["conform", str(corpus)]) == 1
    assert "does not itself conform" in capsys.readouterr().out


def test_a_non_conforming_document_is_never_handed_to_the_writer(tmp_path, capsys) -> None:
    """One failure, and the rest of the corpus still runs.

    A writer places a document into another format's shape and reads every REQUIRED member
    unguarded, so writing one the validator has just rejected raises out of the runner and
    takes every remaining case with it — a traceback in place of the failure that was
    already recorded, and no answer at all about the cases that never ran. `uuid` is the
    case that shows it: §6.2 requires it and `write_uddf` subscripts it.
    """
    corpus = _corpus(tmp_path)
    source = corpus / "write" / "uddf" / f"{WRITER_PAIRS['uddf']}.divejson"
    document = json.loads(source.read_text(encoding="utf-8"))
    del document["dives"][0]["uuid"]
    _write(source, document)

    assert main(["conform", str(corpus)]) == 1
    out = capsys.readouterr().out
    assert "does not itself conform" in out
    # Nothing is printed until the whole walk returns a `Result`, so any output at all is
    # what says the run finished rather than fell over on the way — this asserts on a group
    # from earlier in the walk, `write/uddf` being the last of them.
    assert "uddf: 1 reader pair checked" in out


def test_the_result_counts_what_it_checked(tmp_path) -> None:
    result = run(_corpus(tmp_path), strict=True)
    assert result.status == 0
    assert {group.name: group.checked for group in result.groups} == {
        "valid": 1,
        "invalid": 1,
        **dict.fromkeys(read_formats(), 1),
        **{f"write/{fmt}": 1 for fmt in write_formats()},
    }
    assert result.checked == 2 + len(read_formats()) + len(write_formats())


def test_compared_drops_exactly_the_two_members_a_run_owns() -> None:
    """The comparison rule a port and an application both have to use."""
    document = json.loads(
        (FIXTURES / "uddf" / "subsurface.divejson").read_text(encoding="utf-8")
    )
    assert set(IGNORED) <= set(document)
    assert set(document) - set(compared(document)) == set(IGNORED)


def test_the_registered_formats_are_the_ones_with_pair_directories() -> None:
    """A corpus directory is named for a format id, and that is the whole coupling.

    Derived rather than listed, so a reader registered without a pair directory beside it
    fails here — which is the same gap `--strict` catches, one desk earlier.

    The list of ids the build registers is asserted once, in `test_registry.py`, and
    deliberately not repeated here: this test opened with a hand-written copy of it, which
    said `uddf` when `ssrf` landed and `uddf, ssrf` when `fit` did, and each time reported
    the stale copy rather than anything about pair directories.
    """
    assert known_formats()
    for fmt in known_formats():
        assert (FIXTURES / fmt).is_dir(), fmt
