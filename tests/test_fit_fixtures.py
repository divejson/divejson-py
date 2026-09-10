"""Every `.fit` fixture converts to the `.divejson` beside it.

The pairs in `fixtures/fit/` are a conformance suite for *converters*, the way
`fixtures/valid` and `fixtures/invalid` are one for validators: an input, and the document
a correct reader produces from it. The Python converter in this repository is the first to
be run against them and deliberately not the last, so a port in another language can take
the same directory and expect the same answers.

**These two inputs are the only ones in the corpus that were not hand-built**, and they
could not have been: a FIT file cannot be reduced with a text editor, and a synthetic one
would prove that an encoder and a decoder agree rather than that a device's file reads.
`fixtures/README.md` records that exception. What it costs is that the two files are large
and opaque; what it buys is the only evidence in this repository that the reader works on
bytes a watch actually wrote.

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
FIT_FIXTURES = sorted((FIXTURES / "fit").glob("*.fit"))

OCEAN = FIXTURES / "fit" / "suunto-ocean.fit"
D5 = FIXTURES / "fit" / "suunto-d5.fit"


@pytest.mark.parametrize("source", FIT_FIXTURES, ids=lambda path: path.stem)
def test_fixture_converts_to_its_expected_document(source) -> None:
    expected_path = source.with_suffix(".divejson")
    assert expected_path.is_file(), f"{source.name} has no expected output beside it"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    produced = convert(source.read_bytes(), exported_at=EXPORTED_AT).document
    assert compared(produced) == compared(expected)


@pytest.mark.parametrize("source", FIT_FIXTURES, ids=lambda path: path.stem)
def test_expected_document_is_conforming(source) -> None:
    """Checked here as well as in CI, so a hand-edited expectation fails at the same desk."""
    expected = json.loads(source.with_suffix(".divejson").read_text(encoding="utf-8"))
    assert validate_document(expected) == []


@pytest.mark.parametrize("source", FIT_FIXTURES, ids=lambda path: path.stem)
def test_an_inferred_note_and_a_listed_member_arrive_together(source) -> None:
    """The report's `inferred` kind and `extensions.divejson.inferred` are one decision.

    This is the reader that can compute a value, so the coupling has teeth here in a way it
    does not for the two XML readers. Both halves are empty for both files below — each
    session records its own depths — and the encoder tests are where a file that infers one
    is built. Either half without the other is a document saying two different things about
    itself.
    """
    conversion = convert(source.read_bytes(), exported_at=EXPORTED_AT)
    listed = conversion.document.get("extensions", {}).get(PRODUCER_KEY, {}).get(INFERRED, [])
    noted = [note.where for note in conversion.notes if note.kind == "inferred"]
    assert bool(noted) == bool(listed), f"inferred notes {noted}, listed members {listed}"


@pytest.mark.parametrize("source", FIT_FIXTURES, ids=lambda path: path.stem)
def test_this_reader_settles_no_scale_and_so_resolves_nothing(source) -> None:
    """FIT states every unit in the profile, so there is no scale for a reader to decide.

    That is what separates it from UDDF, whose `<o2>` and `<tankvolume>` are numbers with no
    stated unit and which therefore emits `resolved` findings. A `resolved` appearing here
    would mean this reader had started guessing at something the profile already says.
    """
    conversion = convert(source.read_bytes(), exported_at=EXPORTED_AT)
    assert {note.kind for note in conversion.notes} <= {"absent", "inferred", "dropped"}


def test_every_expected_document_has_an_input() -> None:
    """The other direction: a `.divejson` with no `.fit` beside it is a leftover."""
    orphans = [
        path.name
        for path in sorted((FIXTURES / "fit").glob("*.divejson"))
        if not path.with_suffix(".fit").is_file()
    ]
    assert orphans == []


def test_the_ocean_dive_is_read_from_its_session_and_nothing_is_computed() -> None:
    """The known answer, and the point of the whole `_native` filter.

    45.91 is the native `uint32` scaled by 1000. The developer `float32` beside it on the
    same message renders the same reading as 45.90999984741211, and a reader that walked
    `frame.fields` into a dict by name would carry that instead — a document that validates
    perfectly and is wrong by a rounding error, on every Suunto file there is.

    `duration` is `total_elapsed_time`, the wall clock of the dive, rounded from 4301.72.
    `started_at` carries the +02:00 the `activity` message's two renderings of one instant
    recover. Nothing is computed, so nothing is listed.
    """
    conversion = convert(OCEAN.read_bytes(), exported_at=EXPORTED_AT)
    dive = conversion.document["dives"][0]
    assert dive["max_depth"] == 45.91
    assert dive["avg_depth"] == 19.43
    assert dive["started_at"] == "2026-04-17T11:49:23+02:00"
    assert dive["duration"] == 4302
    assert dive["cns_end"] == 20.0
    assert dive["otu_end"] == 55.0
    assert INFERRED not in conversion.document["extensions"][PRODUCER_KEY]
    assert [note.kind for note in conversion.notes if note.kind == "inferred"] == []


def test_the_only_mapped_member_the_ocean_session_leaves_empty_is_start_cns() -> None:
    """What the report says about a file this reader reads completely.

    Every `absent` line names a member the device could have filled and did not, so the set
    of them is a statement about the *file*. On this one it is `start_cns` alone — the
    session records `end_cns` and `o2_toxicity` natively and carries no `dive_summary` at
    all — beside the identity line every FIT dive gets, a file having no id to give one.
    """
    conversion = convert(OCEAN.read_bytes(), exported_at=EXPORTED_AT)
    assert [note.kind for note in conversion.notes] == ["absent", "absent"]
    identity, unfilled = conversion.notes
    assert "its identity is derived from its position" in identity.message
    assert "records no start_cns" in unfilled.message


def test_the_ocean_profile_takes_each_channel_only_where_it_was_recorded() -> None:
    """4,295 records, 431 of them carrying a depth and 4,294 a temperature.

    No channel is padded to another's length, which is `converting.md`'s rule meeting the
    writer that makes it obvious: padding depth out to the temperature axis would invent
    3,863 depths this dive never reached.
    """
    profile = profile_of(convert(OCEAN.read_bytes(), exported_at=EXPORTED_AT).document["dives"][0])
    assert len(profile["depth"]["times"]) == 431
    assert len(profile["temperature"]["times"]) == 4294
    assert profile["depth"]["times"] != profile["temperature"]["times"][: len(profile["depth"]["times"])]
    # Centimetres, and the deepest sample is shallower than the session's own `max_depth`:
    # the summary is the device's reading rather than a maximum over the samples, which is
    # exactly why it is preferred and why taking it from the samples would be `inferred`.
    assert max(profile["depth"]["values"]) == 4582


def test_the_d5_reads_the_same_way_from_a_different_device() -> None:
    """Product 39 rather than 62, six years earlier, and the same trap on the same field.

    Two devices from one vendor is what makes the developer-field filter a property of the
    exporter rather than of one firmware: this session also carries a `float32` `max_depth`
    of 32.40999984741211 beside the native 32.41.
    """
    dive = convert(D5.read_bytes(), exported_at=EXPORTED_AT).document["dives"][0]
    assert dive["max_depth"] == 32.41
    assert dive["started_at"] == "2021-04-06T11:16:42+02:00"
    assert len(profile_of(dive)["depth"]["times"]) == 200


def test_neither_file_carries_a_gas_the_device_only_had_configured() -> None:
    """The Ocean dive was on two gases and the D5 dive on one, both `enabled`.

    A `disabled` entry would be a gas programmed into the computer and not carried, and
    there is none in either file — so the cylinder counts here are what the diver dived
    rather than what the device knew about.
    """
    ocean = convert(OCEAN.read_bytes(), exported_at=EXPORTED_AT).document["dives"][0]
    d5 = convert(D5.read_bytes(), exported_at=EXPORTED_AT).document["dives"][0]
    assert [cylinder["oxygen"] for cylinder in ocean["cylinders"]] == [21.0, 54.0]
    assert [cylinder["oxygen"] for cylinder in d5["cylinders"]] == [21.0]
    # No `gas_number` on either: §6.3 calls it a label rather than an index, and neither
    # file carries a pressure channel or a gas switch that needs one to point at.
    assert not any("gas_number" in cylinder for cylinder in ocean["cylinders"] + d5["cylinders"])


def test_the_provenance_names_the_device_that_wrote_the_file() -> None:
    """`source_generator` is the computer on the wrist, not an application.

    Neither file's `device_info` carries a `software_version`, so neither generator has a
    version — which is the honest answer rather than an omission to fill in.
    """
    for source, product in ((OCEAN, "Suunto Ocean"), (D5, "Suunto D5")):
        provenance = convert(source.read_bytes(), exported_at=EXPORTED_AT).document["extensions"][PRODUCER_KEY]
        assert provenance["converted_from"] == "fit"
        assert provenance["fit_protocol_version"] == "2.0"
        assert provenance["source_generator"] == {"name": product}


def test_the_ocean_carries_the_fix_taken_on_the_way_out() -> None:
    """No fix is taken underwater, so every position in a dive log is a surface one.

    This file's 28 fixes all fall after its deepest sample, which is the reader's pivot, so
    it has an exit and no entry. The D5 file records no position at all, which is why both
    are in the corpus: one publishes a place and one has none to publish.
    """
    ocean = convert(OCEAN.read_bytes(), exported_at=EXPORTED_AT).document["dives"][0]
    assert ocean["exit_position"] == {"latitude": 28.56723, "longitude": 34.533233}
    assert "entry_position" not in ocean
    d5 = convert(D5.read_bytes(), exported_at=EXPORTED_AT).document["dives"][0]
    assert "entry_position" not in d5 and "exit_position" not in d5
