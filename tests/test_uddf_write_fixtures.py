"""Every writer pair, and the three checks that are the writer's real bar.

`fixtures/write/uddf/` holds a DiveJSON document and the UDDF a correct writer produces
from it, the way `fixtures/uddf/` holds an input and the document a correct reader produces
— the same shape with the halves swapped. `divejson conform` runs the pair comparison, and
this file runs the three things a corpus cannot hold.

**The XSD.** `tests/fixtures/uddf_3.2.2.xsd` is the referee for every ordering decision in
the writer, and reading a document back cannot check one: `informationbeforedive` and
`waypoint` are `xs:sequence`, so a `<divenumber>` after a `<datetime>` makes the file
invalid while this repository's own reader, which takes children by name, would read it
back perfectly. That is exactly the failure a self round trip is blind to.

**The self round trip.** Reading a written pair back through the UDDF reader returns the
document it was written from, and everything that does not come back is named in the
report. `LOST` below is that list, written out per pair rather than derived, because it is
the writer's fidelity claim and a change to it should be a diff somebody reads.

Two members are outside the comparison. `exported_at` and `generator` are `compared`'s
own — facts about a run rather than about the data. **`extensions` is the third and it is
this file's**: a converted document keeps the *source file's* generator and declared
version under the `divejson` producer key, and after this writer has run the source file is
one this package produced. Carrying the old block across would mean writing "Open Diving"
into a `<generator>` that means "what wrote this file", which is the one thing that element
is for. The writer reports it, and `test_the_report_names_everything_that_changed` is what
holds the report to that.

**Agreement with the reference writer**, at the foot of the file: the same logbook written
by both, compared after reading rather than as two files, because the writers are allowed to
differ and the logbook is not. Profile channels are compared by count and extremes there,
the reference writer snapping its other channels onto the depth axis where this one leaves
every reading on its own second.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
import xmlschema
from helpers import FIXTURES, ROOT, device_of, profile_of

from divejson import compared, convert
from divejson.uddf_write import compared as compared_xml
from divejson.uddf_write import write_uddf
from divejson.validate import validate_document

# A glob rather than a count, for the reason `test_uddf_fixtures.py` gives: `divejson
# conform` refuses a writer-pair directory with no documents in it, and `test_conform.py`
# runs it over this corpus.
WRITE_FIXTURES = sorted((FIXTURES / "write" / "uddf").glob("*.divejson"))

# What each pair does not come back **identical** on, as paths into the document. Almost
# every one is a member UDDF has no slot for, a value it cannot spell, or a numbering it
# does not record, and every one of those is named in the writer's report, which the test
# below checks separately. The exceptions are `RETURNED` below, which are gains rather than
# losses. `opendiving.divejson` losing nothing at all is the point of that pair: it is a
# logbook this format's own reference writer produced, and it survives the trip out and back
# whole.
#
# **`gear` is one entry rather than nine**, and that is `_differences` rather than the
# writer: it short-circuits a list-length mismatch to the bare path, and this pair's
# read-back mints a fourth gear item (see `RETURNED`). The `gear/0/…`-`gear/2/…` entries
# this set used to hold are inside that one line now.
LOST: dict[str, frozenset[str]] = {
    "opendiving": frozenset(),
    "technical-dive": frozenset(
        {
            # No slot anywhere in UDDF.
            "courses",
            "species",
            "gear_sets",
            "gear_service_schedules",
            "gear_service_records",
            "certifications",
            "diver/created_at",
            "sites/0/created_at",
            "trips/0/created_at",
            "trips/0/locations/0/bbox",
            "trips/0/locations/1/bbox",
            "dives/0/water_type",
            "dives/0/cns_start",
            "dives/0/cns_end",
            "dives/0/otu_start",
            "dives/0/otu_end",
            "dives/0/entry_position",
            "dives/0/exit_position",
            "dives/0/course_uuid",
            "dives/0/species_uuids",
            "dives/0/created_at",
            "dives/1/species_uuids",
            "dives/0/recordings/0/profile/ceiling",
            "dives/0/recordings/0/profile/extensions",
            # UDDF has no time-to-surface element and no surface gradient factor at all.
            "dives/0/recordings/0/profile/tts",
            "dives/0/recordings/0/profile/surface_gradient_factor",
            # `<decomodel>` requires a tissue table §6.4c has no member for, and nothing is
            # invented to satisfy a required element (`docs/uddf-writing.md`).
            "dives/0/recordings/0/deco_model",
            # `equipmentPieceType` carries no firmware element at all (§6.4b).
            "dives/0/recordings/0/device/firmware",
            *(f"dives/0/cylinders/{index}/{member}" for index in range(4) for member in ("role", "usage")),
            # A place UDDF will not record without a name for it.
            "trips/0/locations/1/position",
            # An empty note, which reads back as no note: `<para></para>` and no `<notes>`
            # at all are the same file to every reader here.
            "trips/0/notes",
            # `<setmarker>` is one string, so a labelled bookmark keeps the type.
            "dives/0/recordings/0/profile/events/6/label",
            # UDDF records no cylinder numbering, so the labels come back as positions.
            *(f"dives/0/cylinders/{index}/gas_number" for index in range(4)),
            *(f"dives/0/recordings/0/profile/pressures/{index}/gas_number" for index in range(4)),
            *(f"dives/0/recordings/0/profile/events/{index}/gas_number" for index in (0, 2, 3)),
            # The gain below, which lands in this set too because `_differences` sees a
            # difference either way.
            "gear",
            "dives/0/gear_uuids",
        }
    ),
}

# The one documented exception to the self round trip (`docs/uddf-writing.md`), and the one
# place a written file returns **more** than it was written from: a device that folded into
# no gear item — or into one its dive does not link — takes a `<divecomputer
# id="device-<n>">` of its own, and that element reads back as a gear item the input never
# had, with a link from the dive that used it. `technical-dive`'s gear list holds no
# computer at all, so it is that half of the fold; `opendiving`'s dive lists its computer
# and carries the same serial on both records, so the fold fires and it is the other half.
#
# Exempt from the report check below and from nothing else. The report is what says a value
# was **lost**, and nothing was: a writer reporting a gain would be describing a document it
# did not produce.
RETURNED: dict[str, frozenset[str]] = {
    "opendiving": frozenset(),
    "technical-dive": frozenset({"gear", "dives/0/gear_uuids"}),
}


@pytest.fixture(scope="module")
def schema() -> xmlschema.XMLSchema:
    """The vendored UDDF schema, parsed once — it is 75 KB and every test below wants it."""
    return xmlschema.XMLSchema(ROOT / "tests" / "fixtures" / "uddf_3.2.2.xsd")


def _document(path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _compared(document: dict[str, Any]) -> dict[str, Any]:
    """A document reduced to what the round trip is about — see the module docstring."""
    reduced = compared(document)
    reduced.pop("extensions", None)
    # `<equipment>` is an `xs:sequence`, so a written logbook's gear is grouped by type and
    # comes back in that order rather than in the document's. Nothing is lost by it and
    # every piece keeps its uuid, so the two lists are compared as the sets of records they
    # are — which is also what makes a *missing* piece still fail this test.
    if "gear" in reduced:
        reduced["gear"] = sorted(reduced["gear"], key=lambda item: item["uuid"])
    return reduced


def _differences(before: Any, after: Any, path: str = "") -> list[str]:
    """Every path at which two documents disagree, as `dives/0/cylinders/1/volume`.

    Deep rather than a whole-document `==`, because "these differ" is not an answer a
    fidelity claim can be written from: the test wants the *members*, so that a new loss
    shows up as one line rather than as a diff of two logbooks.
    """
    if isinstance(before, dict) and isinstance(after, dict):
        differences = []
        for key in {*before, *after}:
            child = f"{path}/{key}" if path else key
            if key not in before or key not in after:
                differences.append(child)
            else:
                differences += _differences(before[key], after[key], child)
        return sorted(differences)
    if isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            return [path]
        differences = []
        for index, (one, other) in enumerate(zip(before, after)):
            differences += _differences(one, other, f"{path}/{index}")
        return differences
    return [] if before == after else [path]


def _reported(path: str, notes) -> bool:
    """Whether the report accounts for a member that did not come back — see below.

    A **top-level** member is matched on its name appearing in a `$` note as a whole word.
    The word boundaries are not decoration: `gear` is a prefix of `gear_sets`,
    `gear_service_schedules` and `gear_service_records`, all three of which this corpus
    reports at `$`, so a substring test called `gear` reported when nothing had said a word
    about it.
    """
    if "/" not in path:
        pattern = re.compile(rf"\b{re.escape(path)}\b")
        return any(pattern.search(note.message) for note in notes if note.where == "$")
    return any(
        path == note.where or path.startswith(f"{note.where}/") for note in notes if note.where != "$"
    )


@pytest.mark.parametrize("source", WRITE_FIXTURES, ids=lambda path: path.stem)
def test_the_document_is_written_as_the_file_beside_it(source) -> None:
    expected = source.with_suffix(".uddf")
    assert expected.is_file(), f"{source.name} has no expected output beside it"
    assert compared_xml(write_uddf(_document(source)).data) == compared_xml(expected.read_bytes())


@pytest.mark.parametrize("source", WRITE_FIXTURES, ids=lambda path: path.stem)
def test_the_input_document_conforms(source) -> None:
    """A pair that starts from a document the format rejects proves nothing about a writer."""
    assert validate_document(_document(source)) == []


@pytest.mark.parametrize("source", WRITE_FIXTURES, ids=lambda path: path.stem)
def test_the_written_file_validates_against_the_uddf_schema(source, schema) -> None:
    schema.validate(write_uddf(_document(source)).data.decode("utf-8"))


@pytest.mark.parametrize("source", WRITE_FIXTURES, ids=lambda path: path.stem)
def test_the_committed_file_validates_against_the_uddf_schema(source, schema) -> None:
    """The bytes in the tree, not only the ones this run produced.

    They are the same file today and the corpus is what a port will be handed, so a hand
    edit that made one of them invalid is worth catching here rather than in somebody
    else's implementation.
    """
    schema.validate(source.with_suffix(".uddf").read_text(encoding="utf-8"))


@pytest.mark.parametrize("source", WRITE_FIXTURES, ids=lambda path: path.stem)
def test_writing_twice_produces_one_file(source) -> None:
    """No clock is read, so the bytes are a function of the document and of nothing else."""
    document = _document(source)
    assert write_uddf(document).data == write_uddf(document).data


@pytest.mark.parametrize("source", WRITE_FIXTURES, ids=lambda path: path.stem)
def test_reading_the_written_file_back_returns_the_document(source) -> None:
    """Out through the writer, in through the reader, and back to the document it started as.

    The check the writer's fidelity claim actually rests on: a comparison against another
    implementation's bytes would make this one a mirror of that one, where this asks whether
    the *logbook* survived the trip.
    """
    document = _document(source)
    read_back = convert(write_uddf(document).data, format="uddf").document
    assert set(_differences(_compared(document), _compared(read_back))) == LOST[source.stem]


@pytest.mark.parametrize("source", WRITE_FIXTURES, ids=lambda path: path.stem)
def test_the_report_names_everything_that_changed(source) -> None:
    """Nothing is lost quietly, which is the half of the output the report is.

    Matched on the note's `where` rather than on its wording: a note at `dives/0` covers
    everything under that dive, and holding the messages to a string here would make every
    rewording a test failure without making a silent loss any harder. A **top-level**
    member is the exception, because `$` is every path's prefix and a blanket that covers
    everything covers nothing — so those are matched on the member's own name appearing in
    a note, as a whole word.
    """
    document = _document(source)
    written = write_uddf(document)
    read_back = convert(written.data, format="uddf").document

    unreported = [
        path
        for path in _differences(_compared(document), _compared(read_back))
        if path not in RETURNED[source.stem] and not _reported(path, written.notes)
    ]
    assert unreported == []


# -- agreement with the reference writer ------------------------------------------------

# The same logbook written twice: `fixtures/uddf/opendiving.uddf` is what DiveJSON's
# reference writer produced for it, and `fixtures/write/uddf/opendiving.uddf` is what this
# one produces from the document that file reads as. What has to agree is **the logbook after
# reading** and not the two files: the writers are allowed to differ — each of those places is
# marked in `docs/uddf-writing.md` — and the dive nobody took is what neither may invent.
REFERENCE = FIXTURES / "uddf" / "opendiving.uddf"
OURS = FIXTURES / "write" / "uddf" / "opendiving.uddf"

# What is compared record by record. Identity and the dive-level scalars: everything a
# diver would notice, and nothing about how either file was laid out.
IDENTITY = ("uuid", "name", "brand", "type", "location", "position", "notes", "starts_on", "ends_on")
SCALARS = (
    "dive_number",
    "started_at",
    "duration",
    "max_depth",
    "avg_depth",
    "bottom_temperature",
    "visibility",
    "weight",
    "altitude",
    "surface_pressure",
    "trip_uuid",
    "site_uuids",
    "gear_uuids",
)


def _picked(record: dict[str, Any], members) -> dict[str, Any]:
    return {member: record[member] for member in members if member in record}


def _channel(channel: dict[str, Any] | None) -> tuple[Any, ...]:
    """A channel as `(count, first time, last time, smallest value, largest value)`.

    Not sample for sample, and the reason is the one difference between the two writers
    that is not a bug in either: the reference writer snaps temperature and pressure onto
    the depth waypoints and drops a reading that cannot reach one within `_snap_tolerance`,
    capped at `MAX_SNAP_SECONDS`. Two files of one dive can therefore carry different
    numbers of temperatures and still describe the same dive, so what has to agree is the
    shape — how many readings, over what span, between what extremes.
    """
    if not channel:
        return ()
    return (
        len(channel["times"]),
        channel["times"][0],
        channel["times"][-1],
        min(channel["values"]),
        max(channel["values"]),
    )


@pytest.fixture(scope="module")
def both() -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        convert(REFERENCE.read_bytes(), format="uddf").document,
        convert(OURS.read_bytes(), format="uddf").document,
    )


def test_the_two_writers_agree_on_every_dive_level_scalar(both) -> None:
    reference, ours = both
    assert [_picked(dive, SCALARS) for dive in ours["dives"]] == [
        _picked(dive, SCALARS) for dive in reference["dives"]
    ]


def test_the_two_writers_agree_on_the_device_each_recording_names(both) -> None:
    """The fold, seen from the reading side: one computer, one element, one device.

    The reference writer's own export states the computer as a kit item with a `<name>` and
    a `<manufacturer>` and nothing else; this writer folds the same kit item and the
    document's device into one element and adds the `<model>`, `<serialnumber>` and
    `<internaldivenumber>` the device carries. So the two agree on the members the
    reference file has and this one is richer, which is the pair's whole point — and what
    they must not do is disagree.
    """
    reference, ours = both
    for mine, theirs in zip(ours["dives"], reference["dives"]):
        mine_device, their_device = device_of(mine) or {}, device_of(theirs) or {}
        assert their_device.items() <= mine_device.items()


def test_the_two_writers_agree_on_the_cylinders(both) -> None:
    reference, ours = both
    assert [dive.get("cylinders") for dive in ours["dives"]] == [
        dive.get("cylinders") for dive in reference["dives"]
    ]


@pytest.mark.parametrize("collection", ["sites", "trips", "gear"])
def test_the_two_writers_agree_on_identity(both, collection) -> None:
    """Sorted by uuid: `<equipment>` is an `xs:sequence` and the two writers order a gear
    list differently, which is a fact about the schema rather than about the logbook."""
    reference, ours = both
    assert sorted((_picked(row, IDENTITY) for row in ours[collection]), key=lambda row: row["uuid"]) == sorted(
        (_picked(row, IDENTITY) for row in reference[collection]), key=lambda row: row["uuid"]
    )


def test_the_two_writers_agree_on_the_profile_channels(both) -> None:
    """By sample count and extremes — see `_channel` for why not sample for sample."""
    reference, ours = both
    for mine, theirs in zip(ours["dives"], reference["dives"]):
        one, other = profile_of(mine) or {}, profile_of(theirs) or {}
        assert one.get("duration") == other.get("duration")
        for name in ("depth", "temperature"):
            assert _channel(one.get(name)) == _channel(other.get(name))
        assert [_channel(channel) for channel in one.get("pressures") or []] == [
            _channel(channel) for channel in other.get("pressures") or []
        ]
        assert one.get("events") == other.get("events")
