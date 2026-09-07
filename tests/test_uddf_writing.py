"""The decisions the writer makes, one at a time, on the smallest document that shows each.

`test_uddf_write_fixtures.py` runs whole logbooks through and checks that what comes back is
what went in; this file is about *why* the file in between looks the way it does. Almost
every case here is a place UDDF requires something DiveJSON does not, and the three answers
available are: write the format's own spelling for "not recorded", drop the value and say
so, or invent something. The third is never taken, and each test below says which of the
first two it is and what a reader will make of it.

The XSD is asserted throughout rather than in one place. It is the only check that can see
element *order*, and the order is a live hazard here — `informationbeforedive` and
`waypoint` are `xs:sequence`, so a member added to the writer in the wrong place produces a
file this repository's own reader is perfectly happy with and no other implementation can
open.
"""

from __future__ import annotations

from typing import Any

import pytest
import xmlschema
from helpers import ROOT

from divejson import convert
from divejson.uddf_write import compared, write_uddf

STARTED_AT = "2026-04-17T11:49:23+02:00"

DIVE_UUID = "0198a6f0-9999-7001-8000-000000000001"
SITE_UUID = "0198a6f0-9999-7002-8000-000000000002"
TRIP_UUID = "0198a6f0-9999-7003-8000-000000000003"
GEAR_UUID = "0198a6f0-9999-7004-8000-000000000004"


@pytest.fixture(scope="module")
def schema() -> xmlschema.XMLSchema:
    return xmlschema.XMLSchema(ROOT / "tests" / "fixtures" / "uddf_3.2.2.xsd")


def document(**members: Any) -> dict[str, Any]:
    """The smallest conforming document, plus whatever a test is about."""
    return {
        "format": "divejson",
        "version": "1.0",
        "exported_at": "2026-09-05T00:00:00+00:00",
        "generator": {"name": "tests"},
        **members,
    }


def one_dive(**members: Any) -> dict[str, Any]:
    """A document whose only dive carries `members`, `started_at` being REQUIRED."""
    return document(dives=[{"uuid": DIVE_UUID, "started_at": STARTED_AT, **members}])


def written(source: dict[str, Any], schema: xmlschema.XMLSchema | None = None) -> str:
    """The document as UDDF text, validated against the schema when one is passed."""
    data = write_uddf(source).data
    if schema is not None:
        schema.validate(data.decode("utf-8"))
    return data.decode("utf-8")


def read_back(source: dict[str, Any]) -> dict[str, Any]:
    return convert(write_uddf(source).data, format="uddf").document


def notes(source: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(note.kind, note.where, note.message) for note in write_uddf(source).notes]


def messages(source: dict[str, Any], where: str) -> list[str]:
    return [message for _, at, message in notes(source) if at == where]


# -- what a required element does when the document has nothing for it -----------------


def test_a_dive_with_no_depth_or_duration_writes_the_zeros_the_format_forces(schema) -> None:
    """`<greatestdepth>` and `<diveduration>` are both mandatory, and DiveJSON's are not.

    Zero is not a fabricated reading here: it is the spelling UDDF leaves a writer with
    nothing to say, and this format's own reader takes it straight back off — `max_depth`
    and `duration` both exclude zero in the schema, so a reader that read it as a
    measurement would be reading "the surface" out of "we do not know".
    """
    source = one_dive()
    text = written(source, schema)
    assert "<greatestdepth>0</greatestdepth>" in text
    assert "<diveduration>0</diveduration>" in text

    dive = read_back(source)["dives"][0]
    assert "max_depth" not in dive and "duration" not in dive
    assert [kind for kind, where, _ in notes(source) if where == "dives/0"] == ["absent", "absent"]


def test_a_cylinder_with_no_start_pressure_keeps_everything_else(schema) -> None:
    """`<tankpressurebegin>` is mandatory, so the alternative is dropping the cylinder.

    The reference writer does drop it, having a `logbook.divejson` beside the export to keep
    it in. A converter has no such second file, and §6.3 names 0 as the absent-marker
    devices write — so the cylinder is written with one and arrives back with its size and
    its gas.
    """
    source = one_dive(cylinders=[{"volume": 12.0, "oxygen": 32.0, "end_pressure": 60.0}])
    assert "<tankpressurebegin>0</tankpressurebegin>" in written(source, schema)

    cylinder = read_back(source)["dives"][0]["cylinders"][0]
    assert cylinder == {"volume": 12.0, "oxygen": 32.0, "end_pressure": 60.0}
    assert [kind for kind, where, _ in notes(source) if where == "dives/0/cylinders/0"] == ["absent"]


def test_a_trip_with_no_end_date_repeats_the_start(schema) -> None:
    """`<dateoftrip>`'s two attributes are both required, and an open trip has one date."""
    source = document(trips=[{"uuid": TRIP_UUID, "name": "Weekend", "starts_on": "2026-05-01"}])
    text = written(source, schema)
    assert 'startdate="2026-05-01T00:00:00" enddate="2026-05-01T00:00:00"' in text

    assert read_back(source)["trips"][0]["ends_on"] == "2026-05-01"
    assert [kind for kind, where, _ in notes(source) if where == "trips/0"] == ["absent"]


# -- what is dropped rather than invented ----------------------------------------------


def test_a_site_with_coordinates_and_no_location_keeps_neither_a_name_nor_a_lie(schema) -> None:
    """`geographyType` makes `<location>` mandatory, and there is nothing honest to put in it.

    The reference writer repeats the site's own name there, which is defensible for an app
    exporting its own data and wrong here: a round trip would hand the diver back a
    `location` they never wrote, which is §5.4's fabrication with an extra step. So the
    position goes and the report says the position went.
    """
    source = document(
        sites=[{"uuid": SITE_UUID, "name": "The Chimney", "position": {"latitude": 1.5, "longitude": 2.5}}]
    )
    text = written(source, schema)
    assert "<geography>" not in text
    assert "<latitude>" not in text

    assert read_back(source)["sites"][0] == {"uuid": SITE_UUID, "name": "The Chimney"}
    assert "the position is dropped" in messages(source, "sites/0")[0]


def test_a_site_with_a_location_carries_its_coordinates(schema) -> None:
    """The other half of the rule: a place name is all `<geography>` was ever waiting for."""
    source = document(
        sites=[
            {
                "uuid": SITE_UUID,
                "name": "The Chimney",
                "location": "Milford Sound",
                "position": {"latitude": -44.6301, "longitude": 167.8901},
            }
        ]
    )
    written(source, schema)
    assert read_back(source)["sites"][0]["position"] == {"latitude": -44.6301, "longitude": 167.8901}
    assert messages(source, "sites/0") == []


def test_a_dive_numbered_zero_is_not_written(schema) -> None:
    """`<divenumber>` is an `xs:positiveInteger`; §6.2 puts no floor under `dive_number`.

    One element wrong would be a cheap price. A zero there makes the whole document
    invalid, which is a file nobody can open rather than a dive nobody can number.
    """
    source = one_dive(dive_number=0)
    assert "<divenumber>" not in written(source, schema)
    assert "dive_number" not in read_back(source)["dives"][0]
    assert "is a positive integer" in messages(source, "dives/0")[0]


def test_an_unlabelled_other_event_is_dropped_rather_than_written_as_the_word(schema) -> None:
    source = one_dive(
        profile={
            "duration": 60,
            "depth": {"times": [0, 60], "values": [0, 500]},
            "events": [{"time": 30, "type": "other"}],
        }
    )
    assert "<setmarker>" not in written(source, schema)
    assert "events" not in read_back(source)["dives"][0]["profile"]
    assert "rather than written as the word" in messages(source, "dives/0/profile/events/0")[0]


def test_a_gas_switch_to_a_cylinder_this_dive_does_not_have_is_dropped(schema) -> None:
    """`<switchmix>`'s `ref` is an `xs:IDREF`, so there is nothing valid to point it at.

    Pointing it at some other dive's mix would say the diver breathed a gas they did not
    carry, which is worse than the switch going missing and saying so.
    """
    source = one_dive(
        cylinders=[{"start_pressure": 200.0, "oxygen": 21.0, "gas_number": 0}],
        profile={
            "duration": 60,
            "depth": {"times": [0, 60], "values": [0, 500]},
            "events": [{"time": 30, "type": "gas_switch", "gas_number": 7}],
        },
    )
    assert "<switchmix" not in written(source, schema)
    assert "the switch is dropped" in messages(source, "dives/0/profile/events/0")[0]


# -- gases, and why a cylinder does not share one inside a dive ------------------------


def test_two_cylinders_on_one_blend_get_a_mix_each(schema) -> None:
    """The sidemount pair, and the reason the mix key carries an occurrence.

    `<tankpressure ref>` addresses a *mix*, and the reader resolves a shared reference
    positionally: repeated references on one waypoint take the linking cylinders in order.
    A pair on one blend sharing a `<mix>` therefore comes back crossed the moment one of
    them misses a waypoint the other has — here the second cylinder's only reading is at
    600 s, where the first has none.
    """
    source = one_dive(
        cylinders=[
            {"start_pressure": 220.0, "oxygen": 18.0, "helium": 45.0, "gas_number": 0},
            {"start_pressure": 218.0, "oxygen": 18.0, "helium": 45.0, "gas_number": 1},
        ],
        profile={
            "duration": 600,
            "depth": {"times": [0, 600], "values": [0, 3000]},
            "pressures": [
                {"times": [0], "values": [2200], "gas_number": 0},
                {"times": [600], "values": [2000], "gas_number": 1},
            ],
        },
    )
    text = written(source, schema)
    assert text.count("<mix id=") == 2

    channels = read_back(source)["dives"][0]["profile"]["pressures"]
    assert channels == [
        {"times": [0], "values": [2200], "gas_number": 0},
        {"times": [600], "values": [2000], "gas_number": 1},
    ]


def test_one_blend_across_two_dives_is_one_mix(schema) -> None:
    """Deduplication is across the logbook, so a hundred air dives do not define a hundred
    gases — it is only inside one dive that two cylinders are kept apart."""
    source = document(
        dives=[
            {
                "uuid": DIVE_UUID,
                "started_at": STARTED_AT,
                "cylinders": [{"start_pressure": 200.0, "oxygen": 21.0}],
            },
            {
                "uuid": "0198a6f0-9999-7005-8000-000000000005",
                "started_at": "2026-04-18T09:00:00+02:00",
                "cylinders": [{"start_pressure": 210.0, "oxygen": 21.0}],
            },
        ]
    )
    assert written(source, schema).count("<mix id=") == 1


def test_a_cylinder_whose_gas_nobody_recorded_still_gets_a_mix(schema) -> None:
    """"No mix was recorded" and "air" are different gases, and `<mix><name>` says which.

    The mix carries no `<o2>` at all, which is how UDDF says nothing about a gas — and it
    exists so that the cylinder's pressure channel has something to point at.
    """
    source = one_dive(
        cylinders=[{"start_pressure": 200.0, "gas_number": 0}],
        profile={
            "duration": 60,
            "depth": {"times": [0, 60], "values": [0, 500]},
            "pressures": [{"times": [0, 60], "values": [2000, 1500], "gas_number": 0}],
        },
    )
    text = written(source, schema)
    assert "<name>unrecorded</name>" in text
    assert "<o2>" not in text

    cylinder = read_back(source)["dives"][0]["cylinders"][0]
    assert cylinder == {"start_pressure": 200.0, "gas_number": 0}


def test_a_gas_number_that_is_a_label_comes_back_as_a_position() -> None:
    """§6.3 calls `gas_number` a label; UDDF records no numbering at all, so a reader
    recovers one by counting `<tankdata>` elements and the labels do not survive."""
    source = one_dive(
        cylinders=[
            {"start_pressure": 200.0, "oxygen": 21.0, "gas_number": 3},
            {"start_pressure": 190.0, "oxygen": 50.0, "gas_number": 5},
        ],
        profile={
            "duration": 60,
            "depth": {"times": [0, 60], "values": [0, 500]},
            "pressures": [{"times": [0], "values": [2000], "gas_number": 3}],
        },
    )
    assert [cylinder["gas_number"] for cylinder in read_back(source)["dives"][0]["cylinders"]] == [0, 1]
    assert any("records no cylinder numbering" in message for message in messages(source, "dives/0"))


def test_a_dive_whose_profile_needs_no_numbering_loses_its_gas_numbers_too() -> None:
    """The quieter half of the same rule, and the one a positional check alone would miss.

    A reader numbers cylinders **only** where the profile asks for it — a pressure channel,
    or a gas switch — so a dive with no profile at all comes back with no `gas_number`
    anywhere, however faithfully the document numbered them from 0.
    """
    source = one_dive(
        cylinders=[
            {"start_pressure": 200.0, "oxygen": 21.0, "gas_number": 0},
            {"start_pressure": 190.0, "oxygen": 50.0, "gas_number": 1},
        ]
    )
    assert all("gas_number" not in cylinder for cylinder in read_back(source)["dives"][0]["cylinders"])
    assert any("records no cylinder numbering" in message for message in messages(source, "dives/0"))


def test_a_numbering_that_survives_is_not_reported() -> None:
    """The one case the labels do come back: numbered from 0 by position, on a dive whose
    profile needs a numbering — which is what this converter's own reader produces."""
    source = one_dive(
        cylinders=[
            {"start_pressure": 200.0, "oxygen": 21.0, "gas_number": 0},
            {"start_pressure": 190.0, "oxygen": 50.0, "gas_number": 1},
        ],
        profile={
            "duration": 60,
            "depth": {"times": [0, 60], "values": [0, 500]},
            "pressures": [{"times": [0], "values": [2000], "gas_number": 1}],
        },
    )
    assert [cylinder["gas_number"] for cylinder in read_back(source)["dives"][0]["cylinders"]] == [0, 1]
    assert not any("records no cylinder numbering" in message for message in messages(source, "dives/0"))


# -- the profile -----------------------------------------------------------------------


def test_a_reading_between_two_depth_samples_gets_its_own_waypoint(schema) -> None:
    """The union of the channels' times, not the depth channel's axis.

    The reference writer snaps everything onto depth and drops what cannot reach a
    waypoint, because Subsurface discards a depth-less waypoint and divelogs.de reads its
    missing depth as zero. Those are the right trades for a file written *for* those two
    and the wrong ones for a file written to be read back: a temperature at 30 s is a
    temperature at 30 s, and here it keeps its second and arrives with it.
    """
    source = one_dive(
        profile={
            "duration": 60,
            "depth": {"times": [0, 60], "values": [0, 500]},
            "temperature": {"times": [30], "values": [245]},
        }
    )
    text = written(source, schema)
    assert text.count("<waypoint>") == 3

    profile = read_back(source)["dives"][0]["profile"]
    assert profile["temperature"] == {"times": [30], "values": [245]}
    assert profile["depth"] == {"times": [0, 60], "values": [0, 500]}


def test_a_profile_duration_longer_than_its_samples_is_reported() -> None:
    """§6.4 defines the member as the span of the samples, and UDDF records no such member
    at all — so a reader recomputes it and a document claiming more comes back with less."""
    source = one_dive(profile={"duration": 900, "depth": {"times": [0, 60], "values": [0, 500]}})
    assert read_back(source)["dives"][0]["profile"]["duration"] == 60
    assert "its samples span 60 s" in messages(source, "dives/0/profile")[0]


def test_two_events_on_one_second_keep_the_first(schema) -> None:
    """`waypointType` carries one `<setmarker>`; the reference writer joins simultaneous
    markers with a separator, which comes back as one event labelled with two labels."""
    source = one_dive(
        profile={
            "duration": 60,
            "depth": {"times": [0, 60], "values": [0, 500]},
            "events": [
                {"time": 30, "type": "other", "label": "first"},
                {"time": 30, "type": "other", "label": "second"},
            ],
        }
    )
    written(source, schema)
    assert read_back(source)["dives"][0]["profile"]["events"] == [
        {"time": 30, "type": "other", "label": "first"}
    ]
    assert "the later event is dropped" in messages(source, "dives/0/profile/events/1")[0]


def test_a_marker_event_keeps_its_type_and_loses_its_label(schema) -> None:
    """The three named types are the only thing a round trip through `<setmarker>` has to
    go on, so a labelled safety stop keeps the half a reader can recognise."""
    source = one_dive(
        profile={
            "duration": 60,
            "depth": {"times": [0, 60], "values": [0, 500]},
            "events": [{"time": 30, "type": "safety_stop", "label": "at the line"}],
        }
    )
    assert "<setmarker>safety_stop</setmarker>" in written(source, schema)
    assert read_back(source)["dives"][0]["profile"]["events"] == [{"time": 30, "type": "safety_stop"}]
    assert "the label is dropped" in messages(source, "dives/0/profile/events/0")[0]


# -- identity, gear and the diver ------------------------------------------------------


def test_ids_are_written_so_that_the_uuids_come_back(schema) -> None:
    """`xs:ID` is an `NCName` and cannot start with a digit, which a hex uuid regularly
    does — so the prefix is what lets `converting.md`'s identity rule read it back off."""
    source = document(
        diver={"uuid": "0198a6f0-9999-7000-8000-000000000000", "name": "Sam Reef"},
        dives=[{"uuid": DIVE_UUID, "started_at": STARTED_AT, "site_uuids": [SITE_UUID]}],
        sites=[{"uuid": SITE_UUID, "name": "The Chimney"}],
    )
    text = written(source, schema)
    assert f'id="dive-{DIVE_UUID}"' in text
    assert f'ref="site-{SITE_UUID}"' in text

    back = read_back(source)
    assert back["dives"][0]["uuid"] == DIVE_UUID
    assert back["dives"][0]["site_uuids"] == [SITE_UUID]
    assert back["diver"]["uuid"] == "0198a6f0-9999-7000-8000-000000000000"


def test_gear_is_written_in_the_schemas_order_and_not_the_documents(schema) -> None:
    """`equipmentType` is an `xs:sequence`, so a logbook's gear comes back grouped by type.

    Nothing is lost by it — every piece keeps its uuid — which is why it is not reported.
    """
    source = document(
        gear=[
            {"uuid": GEAR_UUID, "name": "Wrist computer", "type": "computer"},
            {"uuid": "0198a6f0-9999-7006-8000-000000000006", "name": "Fins", "type": "fins"},
            {"uuid": "0198a6f0-9999-7007-8000-000000000007", "name": "Boots", "type": "boots"},
        ]
    )
    text = written(source, schema)
    assert text.index("<boots") < text.index("<divecomputer") < text.index("<fins")
    assert [item["name"] for item in read_back(source)["gear"]] == ["Boots", "Wrist computer", "Fins"]


def test_gear_with_no_uddf_element_of_its_own_says_so(schema) -> None:
    """Every element in `equipmentType` is a type, so `<variouspieces>` is the honest one.

    The reference writer sends cutting tools to `<knife>` on the grounds that they are
    cutting tools; a converter does not, because `<knife>` asserts a knife where the
    catch-all asserts nothing and the report can then say what was lost.
    """
    source = document(gear=[{"uuid": GEAR_UUID, "name": "EMT shears", "type": "shears"}])
    assert "<variouspieces" in written(source, schema)
    assert read_back(source)["gear"][0]["type"] == "other"
    assert "does not name 'shears'" in messages(source, "gear/0")[0]


def test_a_gear_finding_names_the_piece_it_is_about(schema) -> None:
    """The paths are the document's order and the elements are the schema's, and the two
    are different numbers — so a finding raised while writing a piece has to take its path
    from the piece rather than from wherever the pass that grouped them ended up.

    The boots are `gear/0` in the document and the *first* element written, `boots` opening
    `equipmentType`'s sequence and `variouspieces` closing it — so the SMB is where the
    grouping pass leaves its path, and only the boots carry the empty note.
    """
    source = document(
        gear=[
            {"uuid": GEAR_UUID, "name": "Rock boots", "type": "boots", "notes": ""},
            {"uuid": "0198a6f0-9999-7008-8000-000000000008", "name": "SMB", "type": "smb"},
        ]
    )
    written(source, schema)
    assert [where for _, where, message in notes(source) if "the note is empty" in message] == ["gear/0"]


def test_a_logbook_with_gear_and_no_diver_keeps_the_gear(schema) -> None:
    """`<equipment>` lives inside `<owner>`, so an absent diver would take the kit with it."""
    source = document(gear=[{"uuid": GEAR_UUID, "name": "Wrist computer", "type": "computer"}])
    text = written(source, schema)
    assert "<owner id=\"owner\">" in text

    back = read_back(source)
    assert "diver" not in back
    assert back["gear"][0]["uuid"] == GEAR_UUID


def test_a_one_token_name_leaves_the_surname_empty(schema) -> None:
    """`personalType` makes both names mandatory and DiveJSON holds one string. An empty
    `xs:string` is valid and says "we do not hold this", where repeating the given name
    would assert a surname the diver never gave."""
    source = document(diver={"name": "Cousteau"})
    text = written(source, schema)
    assert "<firstname>Cousteau</firstname>" in text
    assert "<lastname />" in text or "<lastname/>" in text
    assert read_back(source)["diver"]["name"] == "Cousteau"


# -- the file itself -------------------------------------------------------------------


def test_the_comparison_ignores_the_generator_and_nothing_else() -> None:
    """What a writer pair is compared on: two runs of one writer differ in the version they
    stamp and in nothing else, and a corpus that failed on a release is one nobody keeps."""
    source = one_dive(max_depth=18.0)
    data = write_uddf(source).data
    assert compared(data) == compared(data.replace(b"<version>", b"<version>9.9.9-", 1))
    assert compared(data) != compared(data.replace(b"<greatestdepth>18", b"<greatestdepth>19"))


def test_a_control_character_never_reaches_the_file(schema) -> None:
    """JSON admits every C0 control in a string and XML 1.0 forbids most of them outright,
    even escaped — so one in a note would make the whole document unparseable, which is the
    loudest form of the failure this writer is otherwise careful about."""
    source = one_dive(notes="before\x07after")
    text = written(source, schema)
    assert "\x07" not in text
    assert read_back(source)["dives"][0]["notes"] == "beforeafter"


def test_the_document_declares_the_uddf_namespace_and_version(schema) -> None:
    text = written(document(), schema)
    assert '<uddf xmlns="http://www.streit.cc/uddf/3.2/" version="3.2.2">' in text
    assert "<type>converter</type>" in text
