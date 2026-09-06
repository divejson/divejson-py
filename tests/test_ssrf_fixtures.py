"""Every `.ssrf` fixture converts to the `.divejson` beside it.

The pairs in `fixtures/ssrf/` are a conformance suite for *converters*, the way
`fixtures/valid` and `fixtures/invalid` are one for validators: an input, and the document
a correct reader produces from it. The Python converter in this repository is the first to
be run against them and deliberately not the last, so a port in another language can take
the same directory and expect the same answers.

Two members are excluded from the comparison, and only two — `exported_at` and
`generator`. Both are facts about the *run* rather than about the input.
"""

from __future__ import annotations

import json

import pytest
from helpers import EXPORTED_AT, FIXTURES

from divejson import compared, convert
from divejson.converter import INFERRED, PRODUCER_KEY
from divejson.validate import validate_document

# A glob that silently matches nothing is how a suite stops testing anything, and the guard
# against it is `divejson conform`, which refuses a pair directory with no inputs and which
# `test_conform.py` runs over this very corpus.
SSRF_FIXTURES = sorted((FIXTURES / "ssrf").glob("*.ssrf"))


@pytest.mark.parametrize("source", SSRF_FIXTURES, ids=lambda path: path.stem)
def test_fixture_converts_to_its_expected_document(source) -> None:
    expected_path = source.with_suffix(".divejson")
    assert expected_path.is_file(), f"{source.name} has no expected output beside it"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    produced = convert(source.read_bytes(), exported_at=EXPORTED_AT).document
    assert compared(produced) == compared(expected)


@pytest.mark.parametrize("source", SSRF_FIXTURES, ids=lambda path: path.stem)
def test_expected_document_is_conforming(source) -> None:
    """Checked here as well as in CI, so a hand-edited expectation fails at the same desk."""
    expected = json.loads(source.with_suffix(".divejson").read_text(encoding="utf-8"))
    assert validate_document(expected) == []


@pytest.mark.parametrize("source", SSRF_FIXTURES, ids=lambda path: path.stem)
def test_an_inferred_note_and_a_listed_member_arrive_together(source) -> None:
    """The report's `inferred` kind and `extensions.divejson.inferred` are one decision.

    §5.4 asks a writer to label a derived value, so a converter that computed one owes both
    halves: the note that tells the diver, and the member path that tells a downstream
    reader. Either without the other is a document that says two different things about
    itself. Both halves are empty for every file here — this reader computes nothing — and
    the day one of them is not, this fails unless the other moved with it.
    """
    conversion = convert(source.read_bytes(), exported_at=EXPORTED_AT)
    listed = conversion.document.get("extensions", {}).get(PRODUCER_KEY, {}).get(INFERRED, [])
    noted = [note.where for note in conversion.notes if note.kind == "inferred"]
    assert bool(noted) == bool(listed), f"inferred notes {noted}, listed members {listed}"


@pytest.mark.parametrize("source", SSRF_FIXTURES, ids=lambda path: path.stem)
def test_this_reader_settles_no_scale_and_so_resolves_nothing(source) -> None:
    """The property that separates this format from UDDF, asserted rather than assumed.

    Every measurement in a `.ssrf` carries its unit in the text, so there is no
    fraction-or-percent and no litres-or-cubic-metres for a magnitude test to settle —
    which is why the two kinds this reader's report can carry are `absent` and `dropped`,
    and why `docs/ssrf-mapping.md` has no ambiguities section. A `resolved` finding
    appearing here would mean this reader had started guessing at a scale.
    """
    conversion = convert(source.read_bytes(), exported_at=EXPORTED_AT)
    assert {note.kind for note in conversion.notes} <= {"absent", "dropped"}


def test_every_expected_document_has_an_input() -> None:
    """The other direction: a `.divejson` with no `.ssrf` beside it is a leftover."""
    orphans = [
        path.name
        for path in sorted((FIXTURES / "ssrf").glob("*.divejson"))
        if not path.with_suffix(".ssrf").is_file()
    ]
    assert orphans == []


def test_the_two_readings_of_one_subsurface_logbook_agree_where_they_can() -> None:
    """`fixtures/ssrf/subsurface.ssrf` and `fixtures/uddf/subsurface.uddf` are the same two
    dives out of one Subsurface logbook, through two of its export paths.

    The members below are the ones both files carry, and they are equal — the same wall
    clock with no offset on either side, the same depths, the same cylinder. What differs
    is Subsurface's own doing rather than either reader's, and `docs/ssrf-mapping.md`
    records each one.
    """
    ssrf = convert((FIXTURES / "ssrf" / "subsurface.ssrf").read_bytes()).document["dives"][0]
    uddf = convert((FIXTURES / "uddf" / "subsurface.uddf").read_bytes()).document["dives"][0]
    for member in ("started_at", "dive_number", "duration", "max_depth", "avg_depth", "notes", "profile"):
        assert ssrf[member] == uddf[member], member
    assert ssrf["cylinders"][0]["volume"] == uddf["cylinders"][0]["volume"] == 12.0
    assert ssrf["cylinders"][0]["start_pressure"] == uddf["cylinders"][0]["start_pressure"] == 200.0
    assert ssrf["cylinders"][0]["end_pressure"] == uddf["cylinders"][0]["end_pressure"] == 80.0


def test_where_the_two_readings_differ_it_is_the_exporters_doing() -> None:
    """Four differences, none of them a disagreement about the mapping.

    Subsurface's UDDF export writes the five-star visibility as `15` metres, invents an
    `mix(21/0)` air blend for a cylinder its own save file records no gas for, writes
    `<leadquantity>0</leadquantity>` for a logbook holding no weights, and carries no
    `<lowesttemperature>` for the water temperature the save file does keep. The `.ssrf`
    reading is the closer one to what the diver logged in every case, which is the argument
    for reading the save file rather than the export.
    """
    ssrf = convert((FIXTURES / "ssrf" / "subsurface.ssrf").read_bytes()).document["dives"][0]
    uddf = convert((FIXTURES / "uddf" / "subsurface.uddf").read_bytes()).document["dives"][0]
    assert "visibility" not in ssrf and uddf["visibility"] == 15.0
    assert "oxygen" not in ssrf["cylinders"][0] and uddf["cylinders"][0]["oxygen"] == 21.0
    assert "weight" not in ssrf and uddf["weight"] == 0.0
    assert ssrf["bottom_temperature"] == 22.4 and "bottom_temperature" not in uddf
