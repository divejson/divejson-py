"""What this reader refuses, what it reports, and the rules the fixtures only imply.

The pairs in `fixtures/suunto_json/` are whole files with real readings in them. These
build the smallest export that exhibits one behaviour, so that a test about the gas-switch
rule reads as the gas-switch rule rather than as a diff of two logbooks.
"""

from __future__ import annotations

import json

import pytest
from helpers import EXPORTED_AT, SUUNTO_STARTED_AT, suunto_json, suunto_sample, suunto_slots

from divejson import MalformedSuuntoJsonError, convert, sniff
from divejson.converter import Scope
from divejson.suunto_json import SUUNTO_JSON, SUUNTO_JSON_ID_NAMESPACE


def _conversion(header: dict, samples: list[dict] | None = None):
    return convert(suunto_json(header, samples), exported_at=EXPORTED_AT)


def _dive(header: dict, samples: list[dict] | None = None) -> dict:
    return _conversion(header, samples).document["dives"][0]


def _messages(conversion, kind: str | None = None) -> list[str]:
    return [note.message for note in conversion.notes if kind is None or note.kind == kind]


# -- what this reader claims ----------------------------------------------------------


def test_the_format_id_and_its_frozen_namespace() -> None:
    """Both are load-bearing beyond this module and neither may move.

    The id names the conformance corpus's pair directory and is what `--from` takes; the
    namespace is what every dive's UUID is derived under, so changing it renumbers every
    document this reader has ever produced.
    """
    import uuid

    assert SUUNTO_JSON.format == "suunto_json"
    assert SUUNTO_JSON.suffixes == (".json",)
    assert SUUNTO_JSON_ID_NAMESPACE == uuid.uuid5(
        uuid.NAMESPACE_URL, "https://divejson.org/ns/suunto_json"
    )
    assert str(SUUNTO_JSON_ID_NAMESPACE) == "1766426d-4c62-54a6-bc80-264daeab1494"


def test_the_sniff_reads_the_shape_and_not_the_extension() -> None:
    """A DiveJSON document is a `.json` file too, and nothing else here opens `DeviceLog`."""
    assert sniff(suunto_json({})[:512]) == "suunto_json"
    assert sniff(b'{"format": "divejson", "version": "1.0"}') is None
    assert sniff(b'{"DeviceLog": ') == "suunto_json"
    # An object is the shape; the name alone in some other document is not this format.
    assert sniff(b'["DeviceLog"]') is None


def test_a_leading_byte_order_mark_does_not_hide_the_shape() -> None:
    assert sniff(b"\xef\xbb\xbf" + suunto_json({})[:512]) == "suunto_json"


# -- refusals -------------------------------------------------------------------------


def test_bytes_that_are_not_json_are_refused_as_a_sentence() -> None:
    with pytest.raises(MalformedSuuntoJsonError, match="not readable JSON"):
        SUUNTO_JSON.convert(b'{"DeviceLog": ', exported_at=EXPORTED_AT, scope=Scope())


def test_json_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(MalformedSuuntoJsonError, match="is a JSON object"):
        SUUNTO_JSON.convert(b"[1, 2, 3]", exported_at=EXPORTED_AT, scope=Scope())


def test_json_with_no_device_log_header_is_refused() -> None:
    """Named rather than raising a `KeyError` from three members down."""
    with pytest.raises(MalformedSuuntoJsonError, match="no DeviceLog.Header"):
        SUUNTO_JSON.convert(b'{"DeviceLog": {}}', exported_at=EXPORTED_AT, scope=Scope())


def test_a_dive_with_no_readable_start_time_is_dropped_and_reported() -> None:
    """`started_at` is REQUIRED (spec §6.2), so there is no dive to write without one."""
    conversion = convert(
        json.dumps({"DeviceLog": {"Header": {"ActivityType": 51, "DateTime": "last Tuesday"}}}).encode(),
        exported_at=EXPORTED_AT,
    )
    assert "dives" not in conversion.document
    assert any("no start time this converter can read" in message for message in _messages(conversion))


def test_an_activity_that_is_not_a_dive_is_dropped_before_anything_else_is_read() -> None:
    conversion = _conversion({"ActivityType": 3}, [suunto_sample(0, Depth=20.0)])
    assert "dives" not in conversion.document
    assert _messages(conversion, "dropped") == [
        "the export records activity type 3, and this reader carries dives (type 51); the activity is dropped"
    ]


def test_a_header_that_states_no_activity_type_is_read_on() -> None:
    """Absence is not a claim, and schema validity is never a precondition."""
    conversion = convert(
        json.dumps({"DeviceLog": {"Header": {"DateTime": SUUNTO_STARTED_AT, "Duration": 2400}}}).encode(),
        exported_at=EXPORTED_AT,
    )
    assert conversion.document["dives"][0]["duration"] == 2400


# -- time -----------------------------------------------------------------------------


def test_the_recorded_offset_and_fraction_are_both_preserved() -> None:
    """§5.2's whole point, and a fraction it makes OPTIONAL rather than forbidden."""
    assert _dive({})["started_at"] == "2026-04-17T11:49:23.510+02:00"


def test_an_offset_with_no_colon_is_rewritten_and_never_invented() -> None:
    """The spelling is normalised; the value is the source's and no offset is supplied."""
    assert _dive({"DateTime": "2026-04-17T11:49:23+0200"})["started_at"] == "2026-04-17T11:49:23+02:00"
    assert _dive({"DateTime": "2026-04-17T11:49:23z"})["started_at"] == "2026-04-17T11:49:23Z"


def test_a_start_time_with_no_offset_travels_alone_and_says_so() -> None:
    conversion = _conversion({"DateTime": "2026-04-17T11:49:23"})
    assert conversion.document["dives"][0]["started_at"] == "2026-04-17T11:49:23"
    assert any("records no UTC offset" in message for message in _messages(conversion, "absent"))


def test_a_one_digit_fraction_survives_the_python_floor() -> None:
    """`datetime.fromisoformat` rejects this on 3.10, which is why it is not the parser."""
    assert _dive({"DateTime": "2021-04-06T11:16:42.6+02:00"})["started_at"] == "2021-04-06T11:16:42.6+02:00"


def test_a_sample_before_the_dive_began_has_no_place_on_the_axis() -> None:
    conversion = _conversion({}, [suunto_sample(-30, Depth=1.0), suunto_sample(0, Depth=2.0)])
    assert conversion.document["dives"][0]["profile"]["depth"]["times"] == [0]
    assert any("before the dive began" in message for message in _messages(conversion, "dropped"))


def test_a_sample_with_no_timestamp_is_dropped_and_reported() -> None:
    conversion = _conversion({}, [{"Depth": 20.0}, suunto_sample(0, Depth=1.0)])
    assert any("records no TimeISO8601" in message for message in _messages(conversion, "dropped"))


def test_samples_are_ordered_by_their_own_time_and_not_by_position() -> None:
    """The union of this exporter's sample timestamps is not monotonic — see the module."""
    dive = _dive(
        {},
        [
            suunto_sample(60, Depth=20.0),
            suunto_sample(0, Depth=1.0),
            suunto_sample(30, Depth=10.0),
        ],
    )
    assert dive["profile"]["depth"]["times"] == [0, 30, 60]
    assert dive["profile"]["depth"]["values"] == [100, 1000, 2000]


def test_two_entries_on_one_second_merge_rather_than_one_being_dropped() -> None:
    """Different channels at one instant were never in competition for that second."""
    dive = _dive(
        {},
        [
            suunto_sample(0.1, Depth=1.0),
            suunto_sample(0.4, Temperature=293.75),
            suunto_sample(0.2, Cylinders=suunto_slots(20_000_000)),
        ],
    )
    profile = dive["profile"]
    assert profile["depth"]["times"] == [0]
    assert profile["temperature"]["times"] == [0]
    assert profile["pressures"][0]["times"] == [0]


def test_one_channel_twice_on_a_second_keeps_the_first_and_is_reported_once() -> None:
    """The collision that survives merging, counted per channel rather than per sample.

    A file whose streams overlap throughout would otherwise write one line per sample
    saying one thing about the file.
    """
    conversion = _conversion(
        {},
        [
            suunto_sample(0.1, Depth=1.0),
            suunto_sample(0.4, Depth=9.9),
            suunto_sample(0.2, Depth=8.8),
            suunto_sample(60, Depth=20.0),
        ],
    )
    assert conversion.document["dives"][0]["profile"]["depth"]["values"] == [100, 2000]
    assert _messages(conversion, "dropped") == [
        "2 depth readings land on a second the dive already has one at; the later reading is dropped, "
        "because the format's sample times are strictly increasing (spec §6.5)"
    ]


# -- the duration pair ----------------------------------------------------------------


def test_dive_time_wins_over_duration_and_duration_is_the_fallback() -> None:
    """The in-water time against the whole logged period, which differ by minutes."""
    assert _dive({"DiveTime": 4001.4, "Duration": 4302.208})["duration"] == 4001
    assert _dive({"Duration": 2001})["duration"] == 2001


def test_a_zero_duration_is_a_placeholder_rather_than_a_dive_of_no_length() -> None:
    """§6.2 gives `duration` `exclusiveMinimum: 0`, and the member's own rule decides."""
    conversion = _conversion({"DiveTime": 0, "Duration": 2001})
    assert conversion.document["dives"][0]["duration"] == 2001
    assert any("not a length of time a dive can have" in m for m in _messages(conversion, "absent"))


# -- cylinders, the Ocean shape -------------------------------------------------------


def test_the_cylinder_list_is_the_gas_switches_and_not_the_transmitters() -> None:
    """One slot transmits, two gases were breathed, and the answer is two cylinders."""
    dive = _dive(
        {},
        [
            suunto_sample(0, Depth=1.0, Cylinders=suunto_slots(20_000_000, None), DiveEvents=[{"GasSwitch": {"GasNumber": 0}}]),
            suunto_sample(600, Depth=20.0, Cylinders=suunto_slots(15_000_000, None)),
            suunto_sample(1200, Depth=6.0, DiveEvents=[{"GasSwitch": {"GasNumber": 1}}]),
        ],
    )
    assert dive["cylinders"] == [
        {"start_pressure": 200.0, "end_pressure": 150.0, "gas_number": 0},
        {"gas_number": 1},
    ]


def test_the_switch_order_is_the_cylinder_order() -> None:
    """Chronological, so the back gas comes first and a deco gas follows it."""
    dive = _dive(
        {},
        [
            suunto_sample(0, Depth=1.0, DiveEvents=[{"GasSwitch": {"GasNumber": 4}}]),
            suunto_sample(600, Depth=20.0, DiveEvents=[{"GasSwitch": {"GasNumber": 2}}]),
            suunto_sample(900, Depth=6.0, DiveEvents=[{"GasSwitch": {"GasNumber": 4}}]),
        ],
    )
    assert len(dive["cylinders"]) == 2
    # The source numbers 4 and 2 became positions 0 and 1, which is what the markers name.
    assert [event.get("gas_number") for event in dive["profile"]["events"]] == [0, 1, 0]


def test_a_slot_that_transmitted_without_a_switch_is_still_a_cylinder() -> None:
    """Evidence of a tank is evidence of a tank, whichever way round it arrived."""
    dive = _dive({}, [suunto_sample(0, Depth=1.0, Cylinders=suunto_slots(None, 18_000_000))])
    assert dive["cylinders"] == [{"start_pressure": 180.0, "end_pressure": 180.0, "gas_number": 0}]


def test_a_null_pressure_is_skipped_rather_than_ending_the_series() -> None:
    """An Ocean numbers five slots on every sample and nulls the four nothing is paired to.

    Its last samples null out even the live slot, so a reader that stopped at the first
    `null` would report a dive that ended at whatever it had reached by then.
    """
    dive = _dive(
        {},
        [
            suunto_sample(0, Depth=1.0, Cylinders=suunto_slots(20_000_000, None, None, None, None)),
            suunto_sample(600, Depth=20.0, Cylinders=suunto_slots(15_000_000, None, None, None, None)),
            suunto_sample(1200, Depth=1.0, Cylinders=suunto_slots(None, None, None, None, None)),
        ],
    )
    assert dive["cylinders"][0]["end_pressure"] == 150.0
    assert dive["profile"]["pressures"] == [{"times": [0, 600], "values": [2000, 1500], "gas_number": 0}]


def test_readings_after_the_dive_ended_are_dropped_from_the_cylinder() -> None:
    """The purged regulator, in the smallest export that has one."""
    dive = _dive(
        {"DiveTime": 600},
        [
            suunto_sample(0, Depth=1.0, Cylinders=suunto_slots(20_000_000)),
            suunto_sample(590, Depth=1.0, Cylinders=suunto_slots(5_000_000)),
            suunto_sample(900, Cylinders=suunto_slots(14_062)),
        ],
    )
    assert dive["cylinders"][0]["end_pressure"] == 50.0
    # And the channel keeps the reading, because it is telemetry the device did record.
    assert dive["profile"]["pressures"][0]["values"] == [2000, 500, 1]


def test_a_header_with_no_dive_time_leaves_the_readings_unbounded() -> None:
    """Weaker, and never worse than not knowing. `Duration` is deliberately not a fallback:
    bounding a window by its own full length is not a bound."""
    dive = _dive(
        {"Duration": 900},
        [
            suunto_sample(0, Depth=1.0, Cylinders=suunto_slots(20_000_000)),
            suunto_sample(900, Cylinders=suunto_slots(14_062)),
        ],
    )
    assert dive["cylinders"][0]["end_pressure"] == 0.14062


def test_the_extremes_are_taken_over_the_samples_and_not_over_the_axis() -> None:
    """The reading that shares its second with an earlier entry is still the dive's first.

    Taking the extremes off the whole-second axis instead loses it — on the real dive this
    reader is measured against that moves the start pressure from 211.625 bar to 211.26562,
    which is a wrong answer frozen into a conformance corpus.
    """
    dive = _dive(
        {},
        [
            suunto_sample(0.1, Depth=1.45),
            suunto_sample(0.2, Cylinders=suunto_slots(21_162_500)),
            suunto_sample(600, Depth=20.0, Cylinders=suunto_slots(18_000_000)),
        ],
    )
    assert dive["cylinders"][0]["start_pressure"] == 211.625


def test_a_zero_start_pressure_is_a_device_s_absent_marker() -> None:
    """§6.3 says outright that a writer must not emit one."""
    conversion = _conversion(
        {},
        [
            suunto_sample(0, Depth=1.0, Cylinders=suunto_slots(0)),
            suunto_sample(600, Depth=20.0, Cylinders=suunto_slots(15_000_000)),
        ],
    )
    cylinder = conversion.document["dives"][0]["cylinders"][0]
    assert "start_pressure" not in cylinder and cylinder["end_pressure"] == 150.0
    assert any("mean 'not recorded'" in message for message in _messages(conversion, "absent"))


def test_an_end_pressure_above_the_start_cannot_be_and_is_dropped() -> None:
    conversion = _conversion(_gas_block(StartPressure=15_000_000, EndPressure=20_000_000))
    cylinder = conversion.document["dives"][0]["cylinders"][0]
    assert cylinder["start_pressure"] == 150.0 and "end_pressure" not in cylinder
    assert any("above its start pressure" in message for message in _messages(conversion, "dropped"))


def test_a_pressure_past_what_the_format_allows_is_dropped() -> None:
    conversion = _conversion(_gas_block(StartPressure=40_000_000))
    assert conversion.document["dives"][0]["cylinders"] == [{"role": "bottom"}]
    assert any("outside the 0 to 350" in message for message in _messages(conversion, "dropped"))


def test_more_cylinders_than_one_dive_may_describe_are_capped_and_reported() -> None:
    switches = [
        suunto_sample(index, Depth=1.0, DiveEvents=[{"GasSwitch": {"GasNumber": index}}])
        for index in range(20)
    ]
    conversion = _conversion({}, switches)
    assert len(conversion.document["dives"][0]["cylinders"]) == 16
    assert any("at most 16 are read" in message for message in _messages(conversion, "dropped"))


# -- cylinders, the D5 shape ----------------------------------------------------------


def _gas_block(**members: object) -> dict:
    return {"Diving": {"Gases": [{"State": "Primary", **members}]}}


def test_a_gases_block_is_authoritative_and_the_samples_do_not_add_to_it() -> None:
    """The block carries a gas fraction and a tank size that telemetry alone cannot."""
    dive = _dive(
        _gas_block(Oxygen=0.31, TankSize=0.011, StartPressure=20_520_312, EndPressure=8_678_125),
        [suunto_sample(0, Depth=1.0, Cylinders=[{"GasNumber": 1, "Pressure": 20_520_000}])],
    )
    assert len(dive["cylinders"]) == 1
    # The block's figures, to the digit, rather than the telemetry's 205.2 bar.
    assert dive["cylinders"][0]["start_pressure"] == 205.20312


def test_a_gas_state_this_reader_does_not_know_is_no_role_rather_than_the_nearest_one() -> None:
    """The vocabulary is Suunto's, and forcing an unknown value into §6.3's is inventing."""
    assert "role" not in _dive({"Diving": {"Gases": [{"State": "Diluent", "Oxygen": 0.21}]}})["cylinders"][0]


def test_a_mix_whose_halves_sum_above_a_hundred_is_dropped_entirely() -> None:
    conversion = _conversion(_gas_block(Oxygen=0.6, Helium=0.6))
    cylinder = conversion.document["dives"][0]["cylinders"][0]
    assert "oxygen" not in cylinder and "helium" not in cylinder
    assert any("sum above 100 percent" in message for message in _messages(conversion, "dropped"))


# -- the profile ----------------------------------------------------------------------


def test_samples_that_carry_a_time_and_no_reading_produce_no_profile() -> None:
    """A zero-length sampled record is a claim the source did not make."""
    conversion = _conversion({}, [suunto_sample(0, Speed=3.2), suunto_sample(60, Speed=3.4)])
    assert "profile" not in conversion.document["dives"][0]
    assert any("no reading this format can hold" in message for message in _messages(conversion, "dropped"))


def test_a_dive_that_recorded_no_samples_at_all_is_not_reported() -> None:
    """The source said nothing, which is an absence rather than something uncarriable."""
    conversion = _conversion({"Duration": 2400})
    assert "profile" not in conversion.document["dives"][0]
    assert _messages(conversion, "dropped") == []


def test_the_device_s_own_ambient_pressure_sensor_is_not_a_tank_pressure() -> None:
    """`DeviceInternalAbsPressure` sits beside `Cylinders` and reads ~96 400 Pa at the
    surface. Labelling it tank pressure on a chart divers plan gas from would be wrong."""
    dive = _dive({}, [suunto_sample(0, Depth=1.0, DeviceInternalAbsPressure=96_417)])
    assert "cylinders" not in dive and "pressures" not in dive["profile"]


def test_the_computer_narrating_its_own_mode_is_not_an_event() -> None:
    """`State`, `DiveState`, `DiveStatus`, `Lap`, `Pause` and `ArrayBegin` all go."""
    dive = _dive(
        {},
        [
            suunto_sample(
                0,
                Depth=1.0,
                Events=[{"State": {"Active": True, "Type": "Below Surface"}}, {"Lap": {"Index": 1}}],
                DiveEvents=[{"DiveState": {"Active": True, "Type": "Dive Active"}}],
            )
        ],
    )
    assert "events" not in dive["profile"]


def test_only_the_active_edge_of_a_paired_notify_is_marked() -> None:
    """A stop arrives as a true and a false, and marking both doubles every stop."""
    dive = _dive(
        {},
        [
            suunto_sample(0, Depth=1.0, DiveEvents=[{"Notify": {"Active": True, "Type": "Deep Stop"}}]),
            suunto_sample(30, Depth=1.0, DiveEvents=[{"Notify": {"Active": False, "Type": "Deep Stop"}}]),
        ],
    )
    assert dive["profile"]["events"] == [{"time": 0, "type": "deep_stop"}]


def test_a_notify_this_reader_does_not_map_is_no_marker_at_all() -> None:
    """"Safety Stop Ahead" is the prompt before the stop and "Stop done" the confirmation;
    marking all three would put three ticks on one stop."""
    dive = _dive(
        {},
        [
            suunto_sample(0, Depth=1.0, DiveEvents=[{"Notify": {"Active": True, "Type": "Safety Stop Ahead"}}]),
            suunto_sample(30, Depth=1.0, DiveEvents=[{"Notify": {"Active": True, "Type": "Stop done"}}]),
        ],
    )
    assert "events" not in dive["profile"]


def test_an_alarm_carries_the_device_s_own_wording() -> None:
    """§6.5's `other` exists for this, and §6.6 requires the label it carries."""
    dive = _dive(
        {},
        [suunto_sample(0, Depth=1.0, Events=[{"Alarm": {"Active": True, "Type": "Ceiling Broken"}}])],
    )
    assert dive["profile"]["events"] == [{"time": 0, "type": "other", "label": "Ceiling Broken"}]


def test_a_gas_switch_arrives_under_either_member_name() -> None:
    """The D5 shapes write `Events` and the Ocean writes `DiveEvents`; reading one loses a
    generation's worth of switches."""
    for key in ("Events", "DiveEvents"):
        dive = _dive({}, [suunto_sample(0, Depth=1.0, **{key: [{"GasSwitch": {"GasNumber": 0}}]})])
        assert dive["profile"]["events"] == [{"time": 0, "type": "gas_switch", "gas_number": 0}]


def test_a_switch_to_a_gas_this_file_describes_nowhere_still_happened() -> None:
    """It is emitted with no number rather than guessed at a position."""
    dive = _dive(
        _gas_block(Oxygen=0.21),
        [suunto_sample(0, Depth=1.0, Events=[{"GasSwitch": {"GasNumber": 7}}])],
    )
    assert dive["profile"]["events"] == [{"time": 0, "type": "gas_switch"}]


# -- identity and the archive ---------------------------------------------------------


def test_a_dive_with_no_id_takes_its_position_and_says_so() -> None:
    """This export records no id for its dive anywhere — see the mapping document."""
    conversion = _conversion({"Duration": 2400})
    assert any("no id" in message for message in _messages(conversion, "absent"))


def test_an_archive_of_these_files_is_one_logbook() -> None:
    """A watch writes one file per dive, and an account export is a zip of them."""
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.json", suunto_json({"Duration": 2400, "Depth": {"Max": 20.0}}))
        archive.writestr("b.json", suunto_json({"Duration": 1800, "Depth": {"Max": 12.0}}))
    conversion = convert(buffer.getvalue(), exported_at=EXPORTED_AT)
    dives = conversion.document["dives"]
    assert len(dives) == 2
    # Positional identities prefixed by the member, so two files with no ids do not collide.
    assert dives[0]["uuid"] != dives[1]["uuid"]
    assert {note.where.split("/")[0] for note in conversion.notes} == {"a.json", "b.json"}


# -- numbers that are not readings ----------------------------------------------------


def test_a_non_finite_number_is_read_as_not_recorded() -> None:
    """Python's JSON reader accepts the bare `Infinity` token and RFC 8259 does not.

    Left as a float it survives into a document as a token no other parser reads, and a
    schema validator is happy to call infinity a number greater than zero.
    """
    body = json.dumps({"DeviceLog": {"Header": {"ActivityType": 51, "DateTime": SUUNTO_STARTED_AT}}})
    raw = body.replace('"DateTime"', '"Duration": Infinity, "DateTime"')
    dive = convert(raw.encode(), exported_at=EXPORTED_AT).document["dives"][0]
    assert "duration" not in dive


def test_a_boolean_is_not_a_reading() -> None:
    """Python makes `True` an integer, and a `true` in a numeric member says something else."""
    assert "max_depth" not in _dive({"Depth": {"Max": True}})
