"""Every UDDF fixture converts to the `.divejson` beside it.

The pairs in `fixtures/uddf/` are a conformance suite for *converters*, the way
`fixtures/valid` and `fixtures/invalid` are one for validators: an input, and the document
a correct reader produces from it. The Python converter in this repository is the first to
be run against them and deliberately not the last, so a port in another language can take
the same directory and expect the same answers.

Two members are excluded from the comparison, and only two — `exported_at` and
`generator`. Both are facts about the *run* rather than about the input: the first is the
moment of conversion and the second is whatever software did it, which for a port is not
this one. Everything else is compared, `extensions` included: the provenance block under
the `divejson` producer key records the source's own version and generator, which are
properties of the input like any other.
"""

from __future__ import annotations

import json

import pytest
from helpers import EXPORTED_AT, FIXTURES

from divejson import compared, convert
from divejson.converter import INFERRED, PRODUCER_KEY
from divejson.validate import validate_document

# A glob that silently matches nothing is how a suite stops testing anything, and the
# guard against it is no longer a count kept here: `divejson conform` refuses a pair
# directory with no inputs, and `test_conform.py` runs it over this very corpus. A number
# in this file would have been a second place for the corpus's size to be recorded, and
# the one furthest from the thing it counts.
UDDF_FIXTURES = sorted((FIXTURES / "uddf").glob("*.uddf"))


@pytest.mark.parametrize("uddf", UDDF_FIXTURES, ids=lambda path: path.stem)
def test_fixture_converts_to_its_expected_document(uddf) -> None:
    expected_path = uddf.with_suffix(".divejson")
    assert expected_path.is_file(), f"{uddf.name} has no expected output beside it"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    produced = convert(uddf.read_bytes(), exported_at=EXPORTED_AT).document
    assert compared(produced) == compared(expected)


@pytest.mark.parametrize("uddf", UDDF_FIXTURES, ids=lambda path: path.stem)
def test_expected_document_is_conforming(uddf) -> None:
    """Checked here as well as in CI, so a hand-edited expectation fails at the same desk."""
    expected = json.loads(uddf.with_suffix(".divejson").read_text(encoding="utf-8"))
    assert validate_document(expected) == []


@pytest.mark.parametrize("uddf", UDDF_FIXTURES, ids=lambda path: path.stem)
def test_an_inferred_note_and_a_listed_member_arrive_together(uddf) -> None:
    """The report's `inferred` kind and `extensions.divejson.inferred` are one decision.

    §5.4 asks a writer to label a derived value, so a converter that computed one owes both
    halves: the note that tells the diver, and the member path that tells a downstream
    reader. Either without the other is a document that says two different things about
    itself.

    Both halves are empty for every UDDF file here, and that is the assertion doing the
    work rather than a coincidence being recorded — this reader computes nothing, and the
    two scale resolutions it *does* make report as `resolved`. Putting one of them back on
    `inferred` fails here, on the side that would leave a note with no listed member.
    """
    conversion = convert(uddf.read_bytes(), exported_at=EXPORTED_AT)
    listed = conversion.document.get("extensions", {}).get(PRODUCER_KEY, {}).get(INFERRED, [])
    noted = [note.where for note in conversion.notes if note.kind == "inferred"]
    assert bool(noted) == bool(listed), f"inferred notes {noted}, listed members {listed}"


def test_the_legacy_writers_scales_are_resolved_and_change_nothing_in_its_document() -> None:
    """The one file in the corpus that makes this reader choose a scale, twice.

    Its `<tankvolume>` is written in litres and its `<o2>` in whole percent, so both
    magnitude tests fire. The expected document beside it is unaffected: a resolution
    labels nothing, which is why the pair did not have to be regenerated when the kind
    arrived.
    """
    conversion = convert((FIXTURES / "uddf" / "legacy-writer.uddf").read_bytes(), exported_at=EXPORTED_AT)
    assert sum(note.kind == "resolved" for note in conversion.notes) == 2
    assert INFERRED not in conversion.document.get("extensions", {}).get(PRODUCER_KEY, {})


def test_every_expected_document_has_an_input() -> None:
    """The other direction: a `.divejson` with no `.uddf` beside it is a leftover."""
    orphans = [
        path.name
        for path in sorted((FIXTURES / "uddf").glob("*.divejson"))
        if not path.with_suffix(".uddf").is_file()
    ]
    assert orphans == []


def test_the_mix_only_cylinder_keeps_its_gas_and_loses_only_its_size() -> None:
    """The shape §6.3 blesses and no file on disk carried until this fixture.

    A cylinder converted from a mix-only source is "a cylinder with its vessel members
    absent". The gas, both pressures and a real drop between them are all present, so the
    size is the *only* input a gas-consumption figure lacks — which is what makes this
    fixture able to reach a reader's size-specific refusal rather than an earlier one.
    """
    dives = convert((FIXTURES / "uddf" / "mix-only-cylinder.uddf").read_bytes()).document["dives"]
    cylinder = dives[0]["cylinders"][0]
    assert "volume" not in cylinder
    assert cylinder["oxygen"] == 32.0
    assert cylinder["start_pressure"] > cylinder["end_pressure"]
    assert "avg_depth" in dives[0]
    # The second dive has no `<tankdata>` at all, which is what APD DiveSight exports.
    assert "cylinders" not in dives[1]


def test_the_reference_implementations_own_identities_survive_the_round_trip() -> None:
    """`dive-<uuid>` ids come back as those uuids, rather than as fresh ones.

    §5.3 asks that identifiers be stable across exports of the same data. A writer holding
    real uuids has to prefix them to satisfy `xs:ID`, and recovering them is what keeps a
    logbook that went out through UDDF recognisable when it comes back.
    """
    document = convert((FIXTURES / "uddf" / "opendiving.uddf").read_bytes()).document
    assert document["dives"][0]["uuid"] == "0198a6f0-5555-7001-8000-000000000001"
    assert document["sites"][0]["uuid"] == "0198a6f0-3333-7001-8000-000000000001"
    assert document["trips"][0]["uuid"] == "0198a6f0-4444-7001-8000-000000000001"
    assert document["dives"][0]["site_uuids"] == [document["sites"][0]["uuid"]]
    assert document["dives"][0]["trip_uuid"] == document["trips"][0]["uuid"]
