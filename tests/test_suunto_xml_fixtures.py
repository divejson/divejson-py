"""Every `suunto_xml` fixture converts to the `.divejson` beside it.

The pairs in `fixtures/suunto_xml/` are a conformance suite for *converters*, the way
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
from helpers import EXPORTED_AT, FIXTURES, profile_of

from divejson import compared, convert
from divejson.converter import INFERRED, PRODUCER_KEY
from divejson.validate import validate_document

# A glob that silently matches nothing is how a suite stops testing anything, and the guard
# against it is `divejson conform`, which refuses a pair directory with no inputs and which
# `test_conform.py` runs over this very corpus.
SUUNTO_XML_FIXTURES = sorted((FIXTURES / "suunto_xml").glob("*.xml"))


@pytest.mark.parametrize("source", SUUNTO_XML_FIXTURES, ids=lambda path: path.stem)
def test_fixture_converts_to_its_expected_document(source) -> None:
    expected_path = source.with_suffix(".divejson")
    assert expected_path.is_file(), f"{source.name} has no expected output beside it"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    produced = convert(source.read_bytes(), exported_at=EXPORTED_AT).document
    assert compared(produced) == compared(expected)


@pytest.mark.parametrize("source", SUUNTO_XML_FIXTURES, ids=lambda path: path.stem)
def test_expected_document_is_conforming(source) -> None:
    """Checked here as well as in CI, so a hand-edited expectation fails at the same desk."""
    expected = json.loads(source.with_suffix(".divejson").read_text(encoding="utf-8"))
    assert validate_document(expected) == []


@pytest.mark.parametrize("source", SUUNTO_XML_FIXTURES, ids=lambda path: path.stem)
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


@pytest.mark.parametrize("source", SUUNTO_XML_FIXTURES, ids=lambda path: path.stem)
def test_this_reader_settles_no_scale_and_so_resolves_nothing(source) -> None:
    """Every reading here is in a unit this format uses consistently across its whole corpus.

    The vendor's *two* exports disagree about three of them — CNS, cylinder pressures,
    surface pressure — but inside one DM5 file each element has exactly one unit, so there
    is nothing for a magnitude test to settle. That is why the two kinds this reader's
    report can carry are `absent` and `dropped`, and why `docs/suunto-xml-mapping.md` has
    no ambiguities section. A `resolved` finding here would mean this reader had started
    guessing at a scale.
    """
    conversion = convert(source.read_bytes(), exported_at=EXPORTED_AT)
    assert {note.kind for note in conversion.notes} <= {"absent", "dropped"}


def test_every_expected_document_has_an_input() -> None:
    """The other direction: a `.divejson` with no `.xml` beside it is a leftover."""
    orphans = [
        path.name
        for path in sorted((FIXTURES / "suunto_xml").glob("*.divejson"))
        if not path.with_suffix(".xml").is_file()
    ]
    assert orphans == []


def test_the_known_answer() -> None:
    """`fixtures/suunto_xml/suunto-d5.xml`, reduced from the owner's 2021-04-06 D5 export.

    The two values the plan for this reader states outright, and the two that would move
    first if a unit factor did.
    """
    dive = convert((FIXTURES / "suunto_xml" / "suunto-d5.xml").read_bytes()).document["dives"][0]
    assert dive["max_depth"] == 32.41
    assert dive["started_at"] == "2021-04-06T11:16:42.6"


def test_a_freedive_is_a_dive_and_says_which_kind_it_is() -> None:
    """It converted to a logbook with no dives at all until §6.4a gained `mode`.

    The reason for skipping it was that the format had no member for the kind of a dive, so
    a carried freedive would arrive indistinguishable from a scuba dive that recorded no gas
    and no algorithm. There is a member now, so skipping one would be the data loss this
    format exists to end rather than the guard against it.

    **No `deco_model` beside it**, though the file states a `<PersonalMode>`: a computer in
    freedive mode ran no decompression model, and an object carrying only a conservatism
    would say it had.
    """
    conversion = convert((FIXTURES / "suunto_xml" / "freedive.xml").read_bytes())
    assert validate_document(conversion.document) == []
    recording = conversion.document["dives"][0]["recordings"][0]
    assert recording["mode"] == "freedive"
    assert "deco_model" not in recording
    assert "cylinders" not in conversion.document["dives"][0]


def test_the_two_gas_dive_ties_its_channel_and_its_markers_to_the_right_cylinders() -> None:
    """`nitrox-deco.xml`: a 21 % back gas with the pod on it and a 52 % deco bottle.

    Three things have to agree and each comes from a different element: the pressure
    channel is the back gas's because `<TransmitterId>` is on that mixture, the second
    marker is the deco bottle's because its `<GasChangeTime>` is nested inside that
    `<DiveMixture>`, and the cylinders are numbered in document order. A reader that took
    any of the three from a different place could still produce a document that validates.
    """
    dive = convert((FIXTURES / "suunto_xml" / "nitrox-deco.xml").read_bytes()).document["dives"][0]
    assert [cylinder["gas_number"] for cylinder in dive["cylinders"]] == [0, 1]
    assert dive["cylinders"][0]["oxygen"] == 21.0
    assert dive["cylinders"][1]["oxygen"] == 52.0
    # The deco bottle never transmitted, so it carries a gas and no pressures at all.
    assert dive["cylinders"][0]["start_pressure"] == 211.391
    assert "start_pressure" not in dive["cylinders"][1]
    assert profile_of(dive)["pressures"][0]["gas_number"] == 0
    assert profile_of(dive)["events"] == [
        {"time": 0, "type": "gas_switch", "gas_number": 0},
        {"time": 1592, "type": "gas_switch", "gas_number": 1},
    ]
    assert profile_of(dive)["ceiling"]["times"], "the deco dive's ceiling channel"
