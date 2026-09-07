"""The `divejson convert` command's contract.

Where the document lands, what goes to stdout, and what the exit status says. The report
is not a diagnostic here: it is the half of the output that tells a diver what their file
did not carry, so "it printed something" is not the assertion.
"""

from __future__ import annotations

import json
import zipfile

import pytest
from helpers import FIXTURES

from divejson.cli import main
from divejson.conform import compared
from divejson.converter import NOTE_KINDS


@pytest.fixture()
def source(tmp_path):
    """A copy of a fixture, so the command writes into a directory the test owns."""
    path = tmp_path / "logbook.uddf"
    path.write_bytes((FIXTURES / "uddf" / "subsurface.uddf").read_bytes())
    return path


def test_the_document_lands_beside_the_input(source, capsys) -> None:
    assert main(["convert", str(source)]) == 0
    written = source.with_suffix(".divejson")
    assert json.loads(written.read_text(encoding="utf-8"))["format"] == "divejson"
    assert f"→ {written}" in capsys.readouterr().out


def test_the_summary_counts_what_was_carried(source, capsys) -> None:
    main(["convert", str(source)])
    assert "2 dives, 2 sites" in capsys.readouterr().out


def test_the_report_names_what_the_source_did_not_carry(source, capsys) -> None:
    main(["convert", str(source)])
    out = capsys.readouterr().out
    assert "no UTC offset" in out
    assert "dive/0, dive/1" in out  # one finding, both its locations


def test_every_report_line_says_which_kind_of_news_it_is(source, capsys) -> None:
    """`absent` and `dropped` are different news, and an undifferentiated list gets skimmed.

    Read off `NOTE_KINDS` rather than spelled out, so a kind added there does not need
    remembering here — a list restated away from its definition is a second place for it
    to go stale.
    """
    main(["convert", str(source)])
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("  ")]
    assert lines
    for line in lines:
        assert line[2:].split(" ", 1)[0] in NOTE_KINDS, line


def test_a_zip_of_logbooks_converts_as_one(tmp_path, capsys) -> None:
    """The shape a watch's account export arrives in: one file per dive, in an archive."""
    archive = tmp_path / "export.zip"
    with zipfile.ZipFile(archive, "w") as written:
        for name in ("subsurface.uddf", "divelogs.uddf"):
            written.writestr(name, (FIXTURES / "uddf" / name).read_bytes())

    assert main(["convert", str(archive)]) == 0
    document = json.loads((tmp_path / "export.divejson").read_text(encoding="utf-8"))
    assert len(document["dives"]) == 4
    assert "4 dives" in capsys.readouterr().out


def test_output_names_another_destination(source, tmp_path, capsys) -> None:
    destination = tmp_path / "elsewhere" / "out.divejson"
    destination.parent.mkdir()
    assert main(["convert", str(source), "--output", str(destination)]) == 0
    assert destination.is_file()
    assert not source.with_suffix(".divejson").exists()


def test_output_refuses_more_than_one_input(source, tmp_path, capsys) -> None:
    other = tmp_path / "second.uddf"
    other.write_bytes(source.read_bytes())
    assert main(["convert", str(source), str(other), "--output", str(tmp_path / "one.divejson")]) == 1
    assert "names one file" in capsys.readouterr().out


def test_an_existing_output_is_refused_until_forced(source, capsys) -> None:
    assert main(["convert", str(source)]) == 0
    capsys.readouterr()
    assert main(["convert", str(source)]) == 1
    assert "already exists" in capsys.readouterr().out
    assert main(["convert", str(source), "--force"]) == 0


def test_exported_at_pins_the_one_member_that_otherwise_moves(source, capsys) -> None:
    """Two conversions of one input have to be diffable, and this is the only thing between.

    It is also what makes the documented regeneration of `fixtures/uddf/` reproduce the
    committed bytes instead of churning a line per file (see `CONTRIBUTING.md`).
    """
    assert main(["convert", str(source), "--exported-at", "2026-09-05T00:00:00+00:00"]) == 0
    first = source.with_suffix(".divejson").read_text(encoding="utf-8")
    assert main(["convert", str(source), "--force", "--exported-at", "2026-09-05T00:00:00+00:00"]) == 0
    assert source.with_suffix(".divejson").read_text(encoding="utf-8") == first
    assert json.loads(first)["exported_at"] == "2026-09-05T00:00:00+00:00"


@pytest.mark.parametrize("written", ["2026-09-05T00:00:00", "yesterday", "2026-13-05T00:00:00+00:00"])
def test_exported_at_refuses_what_the_member_cannot_hold(source, written: str) -> None:
    """§5.2: `exported_at` always carries an offset — it is generated, not recorded history."""
    with pytest.raises(SystemExit):
        main(["convert", str(source), "--exported-at", written])


def test_the_fixture_corpus_regenerates_through_the_documented_recipe(tmp_path) -> None:
    """`CONTRIBUTING.md`'s regeneration recipe, run as written, over the whole corpus.

    A documented command nobody runs is a command that stops working. This is the one
    instruction in the repository whose output is checked in, so it is the one worth
    executing rather than trusting.

    Compared through `compared`, not byte for byte: `generator.version` is this package's
    own version, so a byte comparison would fail on every fixture the first time the
    package is released with no converter change at all — asserting something about the
    release process rather than about the recipe.
    """
    for uddf in sorted((FIXTURES / "uddf").glob("*.uddf")):
        expected = json.loads(uddf.with_suffix(".divejson").read_text(encoding="utf-8"))
        destination = tmp_path / uddf.with_suffix(".divejson").name
        assert main(["convert", str(uddf), "--output", str(destination), "--exported-at", expected["exported_at"]]) == 0
        produced = json.loads(destination.read_text(encoding="utf-8"))
        assert compared(produced) == compared(expected)
        assert produced["exported_at"] == expected["exported_at"]


def test_an_input_named_divejson_would_overwrite_itself(tmp_path, capsys) -> None:
    path = tmp_path / "logbook.divejson"
    path.write_bytes((FIXTURES / "uddf" / "subsurface.uddf").read_bytes())
    assert main(["convert", str(path)]) == 1
    assert "overwrite the input" in capsys.readouterr().out


def test_a_file_no_reader_claims_fails_without_writing_anything(tmp_path, capsys) -> None:
    """The extension is not consulted: what the bytes are is what decides."""
    path = tmp_path / "notes.uddf"
    path.write_text("this is not XML at all", encoding="utf-8")
    assert main(["convert", str(path)]) == 1
    assert not path.with_suffix(".divejson").exists()
    out = capsys.readouterr().out
    assert "nothing here reads these bytes" in out
    assert "uddf (.uddf)" in out


def test_from_names_the_reader_and_gets_that_readers_refusal(tmp_path, capsys) -> None:
    """Saying what a file is gets the reader's own message rather than the sniffer's."""
    path = tmp_path / "notes.uddf"
    path.write_text("this is not XML at all", encoding="utf-8")
    assert main(["convert", str(path), "--from", "uddf"]) == 1
    assert "not well-formed" in capsys.readouterr().out


def test_from_refuses_a_format_this_build_does_not_read() -> None:
    """`zip` included: an archive is a container the sniffer knows, not a reader to ask for."""
    for named in ("unregistered", "zip"):
        with pytest.raises(SystemExit):
            main(["convert", "unused.uddf", "--from", named])


def test_a_missing_file_fails(tmp_path, capsys) -> None:
    assert main(["convert", str(tmp_path / "absent.uddf")]) == 1
    assert "unreadable" in capsys.readouterr().out


def test_one_bad_file_among_several_still_converts_the_others(source, tmp_path, capsys) -> None:
    """Exit status covers the run; a broken file does not abandon the rest of a migration."""
    broken = tmp_path / "broken.uddf"
    broken.write_text("<nonsense/>", encoding="utf-8")
    assert main(["convert", str(broken), str(source)]) == 1
    assert source.with_suffix(".divejson").is_file()


def test_the_converted_document_validates(source, capsys) -> None:
    """The two subcommands meet here, which is the only end-to-end claim worth making."""
    main(["convert", str(source)])
    capsys.readouterr()
    assert main(["validate", str(source.with_suffix(".divejson"))]) == 0
    assert "OK" in capsys.readouterr().out


def test_validate_still_works(capsys) -> None:
    """The dispatch used to call one command unconditionally; a second one has to not break it."""
    assert main(["validate", str(FIXTURES / "valid" / "minimal.divejson")]) == 0
    assert main(["validate", str(FIXTURES / "invalid" / "missing-format.divejson")]) == 1


# -- `--to`: the writer's one command-line surface --------------------------------------


@pytest.fixture()
def logbook(tmp_path):
    """A conforming document, so the command has something it will agree to write."""
    path = tmp_path / "logbook.divejson"
    path.write_bytes((FIXTURES / "write" / "uddf" / "opendiving.divejson").read_bytes())
    return path


def test_the_written_file_lands_beside_the_document(logbook, capsys) -> None:
    assert main(["convert", "--to", "uddf", str(logbook)]) == 0
    written = logbook.with_suffix(".uddf")
    assert written.read_bytes().startswith(b'<?xml version="1.0" encoding="utf-8"?>')
    out = capsys.readouterr().out
    assert f"→ {written}" in out
    assert "1 dive, 1 trip, 1 site, 3 gear items" in out


def test_the_report_says_what_the_format_could_not_hold(logbook, capsys) -> None:
    """The other half of the output, going out as much as coming in."""
    main(["convert", "--to", "uddf", str(logbook)])
    out = capsys.readouterr().out
    assert any(f"  {kind}" in out for kind in NOTE_KINDS)
    assert "extensions" in out


def test_an_existing_file_is_refused_unless_forced(logbook, capsys) -> None:
    logbook.with_suffix(".uddf").write_text("mine", encoding="utf-8")

    assert main(["convert", "--to", "uddf", str(logbook)]) == 1
    assert "already exists" in capsys.readouterr().out
    assert logbook.with_suffix(".uddf").read_text(encoding="utf-8") == "mine"

    assert main(["convert", "--to", "uddf", "--force", str(logbook)]) == 0
    assert logbook.with_suffix(".uddf").read_text(encoding="utf-8") != "mine"


def test_a_document_that_does_not_conform_is_refused_before_anything_is_written(tmp_path, capsys) -> None:
    """A writer places a document into another format's shape; it does not vet one.

    So the vetting happens here, where the alternative is a UDDF file carrying a member the
    format it came from would have rejected — and nobody downstream to notice.
    """
    path = tmp_path / "broken.divejson"
    path.write_bytes((FIXTURES / "invalid" / "avg-depth-exceeds-max.divejson").read_bytes())

    assert main(["convert", "--to", "uddf", str(path)]) == 1
    assert "does not conform" in capsys.readouterr().out
    assert not path.with_suffix(".uddf").exists()


def test_output_names_the_file(logbook, tmp_path, capsys) -> None:
    elsewhere = tmp_path / "elsewhere.uddf"
    assert main(["convert", "--to", "uddf", "-o", str(elsewhere), str(logbook)]) == 0
    assert elsewhere.is_file()
    assert not logbook.with_suffix(".uddf").exists()


def test_from_and_to_are_opposite_directions(logbook, capsys) -> None:
    assert main(["convert", "--to", "uddf", "--from", "uddf", str(logbook)]) == 1
    assert "opposite directions" in capsys.readouterr().out


def test_exported_at_belongs_to_a_document_being_produced(logbook, capsys) -> None:
    """It is a member of the document a conversion *writes*, and `--to` is given one."""
    assert main(["convert", "--to", "uddf", "--exported-at", "2026-01-01T00:00:00Z", str(logbook)]) == 1
    assert "--exported-at" in capsys.readouterr().out


def test_a_format_this_build_does_not_write_is_refused(logbook, capsys) -> None:
    """`--to ssrf` is the case: this build reads Subsurface's save format and writes none."""
    with pytest.raises(SystemExit):
        main(["convert", "--to", "ssrf", str(logbook)])
    assert "invalid choice" in capsys.readouterr().err
