"""Every Suunto app JSON fixture converts to the `.divejson` beside it.

The pairs in `fixtures/suunto_json/` are a conformance suite for *converters*, the way
`fixtures/valid` and `fixtures/invalid` are one for validators: an input, and the document
a correct reader produces from it. The Python converter in this repository is the first to
be run against them and deliberately not the last, so a port in another language can take
the same directory and expect the same answers.

**Every input here is hand-built, reduced from a real export**, which is the corpus rule
for a text format (`fixtures/README.md`) and the reason each file is short enough to read:
the dive the two Ocean pairs are reduced from carries 7,477 samples, and a fixture whose
point is one rule is easier to check when it is not surrounded by all of them. The readings
in them are real, so the answers below are the answers the whole file gives.

Two members are excluded from the comparison, and only two — `exported_at` and `generator`.
Both are facts about the *run* rather than about the input.
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
SUUNTO_FIXTURES = sorted((FIXTURES / "suunto_json").glob("*.json"))

OCEAN = FIXTURES / "suunto_json" / "suunto-ocean.json"
PURGED = FIXTURES / "suunto_json" / "purged-regulator.json"
D5 = FIXTURES / "suunto_json" / "suunto-d5.json"
HEADER_ONLY = FIXTURES / "suunto_json" / "header-only.json"
NOT_A_DIVE = FIXTURES / "suunto_json" / "not-a-dive.json"


def _dive(source) -> dict:
    return convert(source.read_bytes(), exported_at=EXPORTED_AT).document["dives"][0]


@pytest.mark.parametrize("source", SUUNTO_FIXTURES, ids=lambda path: path.stem)
def test_fixture_converts_to_its_expected_document(source) -> None:
    expected_path = source.with_suffix(".divejson")
    assert expected_path.is_file(), f"{source.name} has no expected output beside it"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    produced = convert(source.read_bytes(), exported_at=EXPORTED_AT).document
    assert compared(produced) == compared(expected)


@pytest.mark.parametrize("source", SUUNTO_FIXTURES, ids=lambda path: path.stem)
def test_expected_document_is_conforming(source) -> None:
    """Checked here as well as in CI, so a hand-edited expectation fails at the same desk."""
    expected = json.loads(source.with_suffix(".divejson").read_text(encoding="utf-8"))
    assert validate_document(expected) == []


@pytest.mark.parametrize("source", SUUNTO_FIXTURES, ids=lambda path: path.stem)
def test_this_reader_computes_nothing_and_so_lists_nothing(source) -> None:
    """This export summarises its own dive, so there is nothing for a reader to derive.

    Both halves of the coupling are empty on every pair, and they have to be empty
    *together*: `extensions.divejson.inferred` lists the members whose value this converter
    computed, and an `inferred` finding says one was. Either half without the other is a
    document saying two different things about itself.
    """
    conversion = convert(source.read_bytes(), exported_at=EXPORTED_AT)
    listed = conversion.document.get("extensions", {}).get(PRODUCER_KEY, {}).get(INFERRED, [])
    noted = [note.where for note in conversion.notes if note.kind == "inferred"]
    assert listed == [] and noted == []


@pytest.mark.parametrize("source", SUUNTO_FIXTURES, ids=lambda path: path.stem)
def test_this_reader_settles_no_scale_and_so_resolves_nothing(source) -> None:
    """Every member this format states carries one unit, so no scale is in doubt.

    That is what separates it from UDDF, whose `<o2>` and `<tankvolume>` are numbers with no
    stated unit and which therefore emits `resolved` findings. The two coordinate units in
    one file are not this case: a sample fix is radians and a `DiveRouteOrigin` is degrees,
    and those are different members each with one unit rather than one member with two
    readings.
    """
    conversion = convert(source.read_bytes(), exported_at=EXPORTED_AT)
    assert [note.where for note in conversion.notes if note.kind == "resolved"] == []


def test_every_expected_document_has_an_input() -> None:
    """The other direction: an expectation whose input was deleted still passes above."""
    inputs = {path.stem for path in SUUNTO_FIXTURES}
    expectations = {path.stem for path in (FIXTURES / "suunto_json").glob("*.divejson")}
    assert expectations == inputs


# -- the Ocean shape ------------------------------------------------------------------


def test_the_ocean_dive_is_read_off_a_header_that_carries_no_gas_block() -> None:
    """The known answers, and the sub-second fraction among them.

    `started_at` keeps the `.510` the export records: §5.2 makes a fraction OPTIONAL rather
    than forbidden, and dropping it would discard a value the file states. That is also
    where this reading stops agreeing with the other three readings of this same dive — its
    FIT, its `.ssrf` and its UDDF all carry whole seconds, and the two XML ones carry no
    offset at all — so the agreement is on the wall clock, to the second, and no further.
    """
    dive = _dive(OCEAN)
    assert dive["started_at"] == "2026-04-17T11:49:23.510+02:00"
    assert dive["started_at"].startswith("2026-04-17T11:49:23")
    assert dive["max_depth"] == 45.91
    assert dive["avg_depth"] == 20.87
    # `DiveTime`, not `Duration`: the in-water time rather than the whole logged period,
    # which on this dive is 4,302 s and includes breaking down kit on the boat.
    assert dive["duration"] == 4001


def test_the_ocean_cylinders_come_from_the_gas_switches() -> None:
    """Two cylinders on a dive where only one slot ever transmitted.

    The count is the one `DiveEvents.GasSwitch` gives, and it is also the count this dive's
    FIT reading gives from a `dive_gas` list — two independent files, two readers, one
    answer. Building the list from telemetry would give one cylinder carrying both
    pressures, which is the shape a gas-consumption figure is derived from, so a stage
    bottle's drop would be attributed to the whole dive.
    """
    cylinders = _dive(OCEAN)["cylinders"]
    assert len(cylinders) == 2
    assert cylinders[0] == {"start_pressure": 211.625, "end_pressure": 127.15625, "gas_number": 0}
    assert cylinders[1] == {"gas_number": 1}


def test_the_ocean_carries_no_gas_mixture_and_says_so_per_cylinder() -> None:
    """The string `Oxygen` appears nowhere in this shape, so absent means not recorded."""
    conversion = convert(OCEAN.read_bytes(), exported_at=EXPORTED_AT)
    for cylinder in conversion.document["dives"][0]["cylinders"]:
        assert "oxygen" not in cylinder and "helium" not in cylinder and "volume" not in cylinder
    mixture = [note for note in conversion.notes if "no gas mixture" in note.message]
    assert len(mixture) == 2 and {note.kind for note in mixture} == {"absent"}


def test_the_pressure_channel_outlives_the_end_pressure() -> None:
    """The bound applies to the cylinder and deliberately not to the profile.

    The last reading in this file was taken after the dive ended, so it is not the dive's
    end pressure — but it is telemetry the device really recorded, and the profile is where
    a diver sees all of it. The two numbers differing is the rule made executable: a reader
    that bounded neither would report the tail as the end pressure, and one that bounded
    both would drop surface readings the depth and temperature channels keep.
    """
    dive = _dive(OCEAN)
    channel = profile_of(dive)["pressures"][0]
    assert channel["gas_number"] == 0
    # 127.26562 bar in tenths, against an end pressure of 127.15625 taken 300 s earlier.
    assert channel["values"][-1] == 1273
    assert dive["cylinders"][0]["end_pressure"] == 127.15625


def test_the_purged_regulator_is_not_the_dive_s_end_pressure() -> None:
    """The case the bound exists for, and the one where it is worth two thirds of a tank.

    Unbounded, this dive ends on 0.14062 bar — a diver who breathed their cylinder dry, and
    a respiratory minute volume to match. The truth is 53.34375, recorded six minutes
    earlier and eight seconds before `DiveTime` ran out.
    """
    dive = _dive(PURGED)
    assert dive["cylinders"] == [{"start_pressure": 204.6875, "end_pressure": 53.34375, "gas_number": 0}]
    assert profile_of(dive)["pressures"][0]["values"][-1] == 1


def test_the_ocean_profile_takes_each_channel_only_where_it_was_recorded() -> None:
    """No channel is padded to another's length, and the entries at one second merge.

    This exporter appends its sensor streams as separate entries, so a depth and a
    temperature recorded at the same instant arrive as two objects. They are readings of two
    channels rather than two readings of one, so they are merged onto the second rather than
    one of them being dropped — which is what keeps the depth channel whole.
    """
    profile = profile_of(_dive(OCEAN))
    assert profile["duration"] == 4300
    assert profile["depth"]["times"] == [0, 1200, 4000]
    assert profile["depth"]["values"] == [145, 4464, 132]
    # One temperature and one non-zero ceiling among them, on their own axes.
    assert profile["temperature"] == {"times": [60], "values": [224]}
    assert profile["ceiling"] == {"times": [1200], "values": [300]}


def test_a_ceiling_of_zero_is_not_a_ceiling() -> None:
    """This shape writes `"Ceiling": 0` on every no-deco sample, twice in this file."""
    raw = json.loads(OCEAN.read_text(encoding="utf-8"))
    assert [sample.get("Ceiling") for sample in raw["DeviceLog"]["Samples"]].count(0) == 2
    assert profile_of(_dive(OCEAN))["ceiling"]["times"] == [1200]


def test_the_ocean_events_name_the_cylinder_they_switched_to() -> None:
    """A gas switch is resolved through the same list the cylinders were built from.

    The source's own number is a label — §6.3 says so in as many words — so it is resolved
    to the cylinder's position rather than passed through. Here the two happen to coincide;
    a file whose first switch is to gas 3 would not.
    """
    events = profile_of(_dive(OCEAN))["events"]
    assert events == [
        {"time": 0, "type": "gas_switch", "gas_number": 0},
        {"time": 1230, "type": "ceiling_violation", "label": "Ceiling Broken"},
        {"time": 2075, "type": "gas_switch", "gas_number": 1},
        {"time": 3840, "type": "safety_stop"},
    ]


def test_the_ocean_positions_come_off_two_channels_in_two_units() -> None:
    """The entry is the origin block, in degrees; the exit is a sample fix, in radians.

    Every satellite fix in this stream lands after the diver surfaced — a receiver has
    nothing to talk to through seawater — so without the origin this shape yields an exit
    and no entry. The exit is the same position this dive's FIT reading gives from a
    completely different file, which is the strongest check either reader has.
    """
    dive = _dive(OCEAN)
    assert dive["entry_position"] == {"latitude": 28.567251205444336, "longitude": 34.53325653076172}
    assert dive["exit_position"] == {"latitude": 28.56723, "longitude": 34.533233}


# -- the D5 shape ---------------------------------------------------------------------


def test_the_d5_cylinders_come_from_the_header_s_own_gas_block() -> None:
    """`Diving.Gases` in SI units, against the units §6.3 holds.

    Every value here is converted: Pascal to bar, cubic metres to litres, a 0-1 fraction to
    whole percent. The second gas is one the diver carried and never transmitted from, which
    the block records and the telemetry cannot.
    """
    assert _dive(D5)["cylinders"] == [
        {
            "volume": 22.0,
            "start_pressure": 207.14062,
            "end_pressure": 122.4375,
            "oxygen": 21.0,
            "helium": 0.0,
            "po2_limit": 1.4,
            "role": "bottom",
            "gas_number": 0,
        },
        {
            "volume": 11.0,
            "oxygen": 49.0,
            "helium": 0.0,
            "po2_limit": 1.6,
            "role": "bottom",
            "gas_number": 1,
        },
    ]


def test_the_d5_numbers_its_gases_from_one_and_the_ocean_from_zero() -> None:
    """A source gas number is resolved to a cylinder's position, and the two shapes differ.

    A `Gases[]` block carries no number of its own and the same dive's
    `Samples[].Cylinders[].GasNumber` reports 1 for its first entry, so a D5's numbering is
    one-based over the block's order. The pressure channel below arrived on slot 1 and is
    numbered 0, which is the position §6.3 numbers a cylinder by.
    """
    raw = json.loads(D5.read_text(encoding="utf-8"))
    slots = [
        slot["GasNumber"]
        for sample in raw["DeviceLog"]["Samples"]
        for slot in sample.get("Cylinders", ())
    ]
    assert set(slots) == {1}
    profile = profile_of(_dive(D5))
    assert [channel["gas_number"] for channel in profile["pressures"]] == [0]
    assert [event.get("gas_number") for event in profile["events"] if event["type"] == "gas_switch"] == [0, 1]


def test_the_d5_oxygen_clock_is_a_fraction_here_and_percent_in_the_document() -> None:
    """`EndTissue.CNS: 0.153` is 15.3 %, and reading it as written reports 0.153 %.

    OTU needs no conversion — it is the same absolute count everywhere — and is carried at
    the precision the file states, this export writing a full float32 where the same
    vendor's desktop export rounds to a whole number.
    """
    dive = _dive(D5)
    assert (dive["cns_start"], dive["cns_end"]) == (7.2, 15.3)
    assert (dive["otu_start"], dive["otu_end"]) == (23.09649658203125, 45.12766647338867)
    # 104 900 Pa, in a member §6.2 measures in bar and bounds at 0.4 to 1.2.
    assert dive["surface_pressure"] == 1.049


# -- the third shape, and what is not a dive ------------------------------------------


def test_a_header_with_no_gas_and_no_samples_is_still_a_dive() -> None:
    """The third header shape: no `Diving` block, and nothing in the samples either.

    It carries no cylinders and no profile, and neither is reported — the source recorded no
    samples in the first place, which is an absence rather than something this converter
    could not carry.
    """
    conversion = convert(HEADER_ONLY.read_bytes(), exported_at=EXPORTED_AT)
    dive = conversion.document["dives"][0]
    assert dive["max_depth"] == 32.41 and dive["duration"] == 2001
    assert "cylinders" not in dive and profile_of(dive) is None
    assert [note.kind for note in conversion.notes] == ["absent"]


def test_an_activity_that_is_not_a_dive_is_skipped_and_reported() -> None:
    """The app exports a run in this same shape, and nothing else tells the two apart.

    No file in the corpus of real exports is anything but a dive, so this pair is the only
    place the rule runs — which is exactly why it is a pair rather than only an assertion.
    """
    conversion = convert(NOT_A_DIVE.read_bytes(), exported_at=EXPORTED_AT)
    assert "dives" not in conversion.document
    assert [(note.kind, note.where) for note in conversion.notes] == [("dropped", "dive/0")]
    assert "activity type 3" in conversion.notes[0].message


def test_the_provenance_names_the_device_that_wrote_the_file() -> None:
    """The computer on the diver's wrist, not the app that exported it."""
    for source, name, version in ((OCEAN, "Suunto Ocean", "2.40.56"), (D5, "Suunto D5", "3.0.2143")):
        block = convert(source.read_bytes(), exported_at=EXPORTED_AT).document["extensions"][PRODUCER_KEY]
        assert block["converted_from"] == "suunto_json"
        assert block["source_generator"] == {"name": name, "version": version}
