"""What this reader refuses, what it reports, and the rules the fixtures only imply.

The pairs in `fixtures/suunto_json/` are whole files with real readings in them. These
build the smallest export that exhibits one behaviour, so that a test about the gas-switch
rule reads as the gas-switch rule rather than as a diff of two logbooks.
"""

from __future__ import annotations

import json

import pytest
from helpers import (
    EXPORTED_AT,
    SUUNTO_STARTED_AT,
    device_of,
    profile_of,
    suunto_json,
    suunto_sample,
    suunto_slots,
)

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
    assert profile_of(conversion.document["dives"][0])["depth"]["times"] == [0]
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
    assert profile_of(dive)["depth"]["times"] == [0, 30, 60]
    assert profile_of(dive)["depth"]["values"] == [100, 1000, 2000]


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
    profile = profile_of(dive)
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
    assert profile_of(conversion.document["dives"][0])["depth"]["values"] == [100, 2000]
    assert _messages(conversion, "dropped") == [
        (
            "2 depth readings land on a second the dive already has one at; the later reading is "
            "dropped, because the format's sample times are strictly increasing (spec §6.5)"
        )
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
    assert any("cannot hold as a duration" in m for m in _messages(conversion, "absent"))


def test_a_duration_that_rounds_to_zero_is_read_as_not_recorded() -> None:
    """The member's rule is asked about the whole seconds, not the fraction behind them.

    0.4 s is above §6.2's floor and the integer written from it is not, so a reader that
    checks before it rounds writes a `duration: 0` its own validation then rejects — which
    would lose the whole conversion over a header field.
    """
    conversion = _conversion({"DiveTime": 0.4, "Duration": 2001})
    assert conversion.document["dives"][0]["duration"] == 2001
    assert any("records DiveTime as 0.4 s" in m for m in _messages(conversion, "absent"))


def test_a_negative_duration_is_read_as_not_recorded() -> None:
    conversion = _conversion({"DiveTime": -30})
    assert "duration" not in conversion.document["dives"][0]
    assert any("cannot hold as a duration" in m for m in _messages(conversion, "absent"))


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
    assert [event.get("gas_number") for event in profile_of(dive)["events"]] == [0, 1, 0]


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
    assert profile_of(dive)["pressures"] == [{"times": [0, 600], "values": [2000, 1500], "gas_number": 0}]


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
    assert profile_of(dive)["pressures"][0]["values"] == [2000, 500, 1]


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


def test_the_extremes_are_taken_over_the_samples_and_not_off_the_profile() -> None:
    """The reading that shares its second with an earlier entry is still the dive's first.

    The merged axis is **not** what would lose it: `axis` folds this cylinder reading into
    the second the depth entry 0.1 s earlier already holds, so an axis-based reading of the
    extremes would reach 211.625 too. The two answers that are wrong, on the real dive this
    reader is measured against, are an *unmerged* one-entry-per-second axis, where the first
    entry takes the second whole and the start pressure becomes 211.26562, and the axis's
    pressure channel, which §6.5 stores in tenths of a bar and would give 211.6.

    So this case is about the rule rather than about a difference it makes here, and the
    channel below is where the difference is: the channel rounds and the cylinder does not.
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
    assert profile_of(dive)["pressures"][0]["values"][0] == 2116  # 211.6 bar, in tenths


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


def test_a_transmitter_reading_past_that_range_is_dropped_too() -> None:
    """The same check on the reconstructed path, which is where a noisy pod arrives.

    A gas block is the app's own summary and a transmitter reading is telemetry, so this is
    the path a wild value actually comes down. Left unchecked it reaches the document, and
    the converter's own validation then rejects it — losing the dive over one sample rather
    than dropping the reading and saying so.
    """
    for first, last, dropped, kept in (
        (40_000_000, 15_000_000, "start_pressure", "end_pressure"),
        (20_000_000, -100, "end_pressure", "start_pressure"),
    ):
        conversion = _conversion(
            {},
            [
                suunto_sample(0, Depth=1.0, Cylinders=suunto_slots(first)),
                suunto_sample(600, Depth=20.0, Cylinders=suunto_slots(last)),
            ],
        )
        cylinder = conversion.document["dives"][0]["cylinders"][0]
        assert dropped not in cylinder and kept in cylinder
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
    assert profile_of(conversion.document["dives"][0]) is None
    assert any("no reading this format can hold" in message for message in _messages(conversion, "dropped"))


def test_a_dive_that_recorded_no_samples_at_all_is_not_reported() -> None:
    """The source said nothing, which is an absence rather than something uncarriable."""
    conversion = _conversion({"Duration": 2400})
    assert profile_of(conversion.document["dives"][0]) is None
    assert _messages(conversion, "dropped") == []


def test_the_device_s_own_ambient_pressure_sensor_is_not_a_tank_pressure() -> None:
    """`DeviceInternalAbsPressure` sits beside `Cylinders` and reads ~96 400 Pa at the
    surface. Labelling it tank pressure on a chart divers plan gas from would be wrong."""
    dive = _dive({}, [suunto_sample(0, Depth=1.0, DeviceInternalAbsPressure=96_417)])
    assert "cylinders" not in dive and "pressures" not in profile_of(dive)


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
    assert "events" not in profile_of(dive)


def test_only_the_active_edge_of_a_paired_notify_is_marked() -> None:
    """A stop arrives as a true and a false, and marking both doubles every stop."""
    dive = _dive(
        {},
        [
            suunto_sample(0, Depth=1.0, DiveEvents=[{"Notify": {"Active": True, "Type": "Deep Stop"}}]),
            suunto_sample(30, Depth=1.0, DiveEvents=[{"Notify": {"Active": False, "Type": "Deep Stop"}}]),
        ],
    )
    assert profile_of(dive)["events"] == [{"time": 0, "type": "deep_stop"}]


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
    assert "events" not in profile_of(dive)


def test_an_alarm_carries_the_type_its_wording_earns_and_the_wording() -> None:
    """§6.6's vocabulary was seeded from this list, so an alert is classified *and* labelled."""
    dive = _dive(
        {},
        [suunto_sample(0, Depth=1.0, Events=[{"Alarm": {"Active": True, "Type": "Ceiling Broken"}}])],
    )
    assert profile_of(dive)["events"] == [
        {"time": 0, "type": "ceiling_violation", "label": "Ceiling Broken"}
    ]


def test_two_wordings_of_one_occurrence_share_one_type() -> None:
    """`safety_stop_violation` is one value rather than two because the device has two names
    for the same thing, and §6.6 keeps one spelling per meaning (§5.4)."""
    dive = _dive(
        {},
        [
            suunto_sample(0, Depth=1.0, Events=[{"Alarm": {"Active": True, "Type": "Safety Stop Broken"}}]),
            suunto_sample(
                30, Depth=1.0, Events=[{"Alarm": {"Active": True, "Type": "Mandatory Safety Stop Broken"}}]
            ),
        ],
    )
    assert [event["type"] for event in profile_of(dive)["events"]] == [
        "safety_stop_violation",
        "safety_stop_violation",
    ]
    assert [event["label"] for event in profile_of(dive)["events"]] == [
        "Safety Stop Broken",
        "Mandatory Safety Stop Broken",
    ]


def test_an_alert_the_table_does_not_name_arrives_with_no_type() -> None:
    """§6.6 makes an event with a label and no type the spelling of an unclassified one, so
    the vocabulary grows in a minor version rather than a wording being forced into it."""
    dive = _dive(
        {},
        [suunto_sample(0, Depth=1.0, Events=[{"Warning": {"Active": True, "Type": "Battery Low"}}])],
    )
    assert profile_of(dive)["events"] == [{"time": 0, "label": "Battery Low"}]


def test_the_two_carried_notify_values_are_typed_and_unlabelled() -> None:
    """A `Notify`'s `Type` names the computer's state rather than wording the diver was shown,
    so writing it as a label would put "Deco" on a marker nobody read."""
    dive = _dive(
        {},
        [
            suunto_sample(0, Depth=1.0, DiveEvents=[{"Notify": {"Active": True, "Type": "Deco"}}]),
            suunto_sample(
                30, Depth=1.0, DiveEvents=[{"Notify": {"Active": True, "Type": "Safety Stop Broken"}}]
            ),
        ],
    )
    assert profile_of(dive)["events"] == [
        {"time": 0, "type": "ndl_reached"},
        {"time": 30, "type": "safety_stop_violation"},
    ]


def test_a_gas_switch_arrives_under_either_member_name() -> None:
    """The D5 shapes write `Events` and the Ocean writes `DiveEvents`; reading one loses a
    generation's worth of switches."""
    for key in ("Events", "DiveEvents"):
        dive = _dive({}, [suunto_sample(0, Depth=1.0, **{key: [{"GasSwitch": {"GasNumber": 0}}]})])
        assert profile_of(dive)["events"] == [{"time": 0, "type": "gas_switch", "gas_number": 0}]


def test_a_switch_to_a_gas_this_file_describes_nowhere_still_happened() -> None:
    """It is emitted with no number rather than guessed at a position."""
    dive = _dive(
        _gas_block(Oxygen=0.21),
        [suunto_sample(0, Depth=1.0, Events=[{"GasSwitch": {"GasNumber": 7}}])],
    )
    assert profile_of(dive)["events"] == [{"time": 0, "type": "gas_switch"}]


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


# -- the device ------------------------------------------------------------------------


def test_the_brand_is_the_formats_and_not_the_files() -> None:
    """The one place this reader supplies a value the file does not state, and not §5.4's
    fabrication: a vendor-proprietary export format is the vendor saying so."""
    data = suunto_json({"Device": {"Name": "Porvoo"}})
    assert device_of(convert(data).document["dives"][0]) == {"brand": "Suunto", "name": "Porvoo"}


def test_the_device_name_is_a_name_and_never_a_model() -> None:
    """It is settable by the owner — `Porvoo` on one real Ocean — and this format states no
    product name anywhere, so §6.4b's `model` has no source here."""
    data = suunto_json({"Device": {"Name": "Suunto Ocean", "SerialNumber": "253810000400",
                                   "Info": {"SW": "2.51.28"}}})
    assert device_of(convert(data).document["dives"][0]) == {
        "brand": "Suunto",
        "serial": "253810000400",
        "firmware": "2.51.28",
        "name": "Suunto Ocean",
    }


def test_the_series_number_is_the_devices_counter() -> None:
    """§6.2's `dive_number` is the diver's own numbering and a counter that restarts on a
    new device is not it, which is why the two are different members."""
    data = suunto_json({"Device": {"Name": "Porvoo"}, "Diving": {"NumberInSeries": 3}})
    dive = convert(data).document["dives"][0]
    assert "dive_number" not in dive
    assert device_of(dive)["dive_number"] == 3


def test_a_header_naming_a_device_above_no_samples_is_a_device_only_recording() -> None:
    """A computer worn is a fact about the dive even when it sampled nothing (§6.4a)."""
    dive = convert(suunto_json({"Device": {"Name": "Suunto D5"}})).document["dives"][0]
    assert dive["recordings"] == [{"device": {"brand": "Suunto", "name": "Suunto D5"}}]


def test_a_header_naming_no_device_and_holding_no_samples_has_no_recording() -> None:
    """§6.4a forbids a recording that carries nothing at all."""
    assert "recordings" not in convert(suunto_json({})).document["dives"][0]


# -- the mode and the decompression model ------------------------------------------------


def _recording(header: dict, samples: list[dict] | None = None) -> dict:
    """The dive's one recording, on a file that kept a sample.

    The sample is not decoration: §3's rule 4 says a recording carries at least one of
    `device`, `profile` and `source_files`, and neither §6.4a member this plan adds is one of
    them — so a header stating a mode and nothing else produces no recording to put it in.
    """
    recordings = _dive(header, samples or [suunto_sample(0, Depth=20.0)]).get("recordings") or []
    return recordings[0] if recordings else {}


def test_a_mode_and_a_model_alone_do_not_make_a_recording() -> None:
    """§3's rule 4 names `device`, `profile` and `source_files`, and these are neither."""
    assert "recordings" not in _dive({"Diving": {"DiveMode": "Air", "Conservatism": 0}})


@pytest.mark.parametrize("stated", ["Air", "Nitrox", "Mixed"])
def test_every_dive_mode_a_file_in_hand_carries_is_open_circuit(stated: str) -> None:
    """All three are gas modes of an open-circuit computer; the D5 has no rebreather mode."""
    assert _recording({"Diving": {"DiveMode": stated}})["mode"] == "open_circuit"


@pytest.mark.parametrize("stated", ["Gauge", "Free", "CCR", "air"])
def test_a_dive_mode_this_reader_has_not_seen_is_reported_rather_than_guessed(stated: str) -> None:
    """§6.4a is explicit that a reader must not assume open circuit, and the D5's free and
    gauge modes write no `Header.Diving` this reader has ever seen — so there is no string to
    map and nothing to guess from."""
    conversion = _conversion({"Diving": {"DiveMode": stated}})
    recordings = conversion.document["dives"][0].get("recordings") or [{}]
    assert "mode" not in recordings[0]
    assert any(f"dive mode {stated!r}" in message for message in _messages(conversion, "dropped"))


def test_an_ocean_file_states_no_mode_and_that_is_correct() -> None:
    """The Ocean header shape has no `Header.Diving` at all, so a file of it yields the
    channels and no mode — an absence rather than a gap."""
    assert "mode" not in _recording({}, [suunto_sample(0, Depth=1.0)])


@pytest.mark.parametrize("stated", ["Suunto Fused2 RGBM", "Suunto Fused RGBM 2"])
def test_both_algorithm_spellings_are_the_same_family(stated: str) -> None:
    """Each is sourced separately: the first is what 16 exports in hand carry and the second
    is `fixtures/suunto_json/suunto-d5.json`'s. `name` keeps whichever the file used."""
    assert _recording({"Diving": {"Algorithm": stated}})["deco_model"] == {
        "algorithm": "rgbm",
        "name": stated,
    }


def test_an_algorithm_outside_the_table_still_names_itself() -> None:
    """A family is a claim about the mathematics, and this reader will not derive one from a
    product string it has not seen — but §6.4c's `name` is the device's own name for its
    model, whatever that name is."""
    assert _recording({"Diving": {"Algorithm": "Suunto Fused RGBM"}})["deco_model"] == {
        "name": "Suunto Fused RGBM"
    }


@pytest.mark.parametrize("stated", [0, -1, -2, 2])
def test_a_conservatism_is_carried_with_its_sign(stated: int) -> None:
    """§6.4c puts no floor on the member because Suunto's own scale runs P−2 to P2, so a `0`
    is the P0 setting and a `-1` is P−1 — this reader's one member where a negative is a
    reading."""
    assert _recording({"Diving": {"Conservatism": stated}})["deco_model"] == {"conservatism": stated}


# -- the decompression readouts ----------------------------------------------------------


def _channels(*samples: dict) -> dict:
    return profile_of(_dive({}, list(samples))) or {}


def test_a_no_decompression_time_of_zero_is_a_reading() -> None:
    """It is what a computer shows the moment a dive stops being a no-decompression dive: a
    D5 export in hand writes it at 42.6 to 44.5 m with a time to surface beside it."""
    found = _channels(
        suunto_sample(0, Depth=44.5, NoDecTime=0, TimeToSurface=256),
        suunto_sample(10, Depth=42.6, NoDecTime=6000),
    )
    assert found["ndl"] == {"times": [0, 10], "values": [0, 6000]}


def test_a_time_to_surface_of_zero_is_the_absent_marker_and_is_reported() -> None:
    """Only the file could settle this one: the Ocean writes it at every depth from 0 to
    19 m, including two rows from a sample at 14.63 m that says `88`."""
    conversion = _conversion(
        {},
        [
            suunto_sample(0, Depth=14.63, TimeToSurface=0),
            suunto_sample(10, Depth=14.63, TimeToSurface=88),
        ],
    )
    profile = profile_of(conversion.document["dives"][0])
    assert profile["tts"] == {"times": [10], "values": [88]}
    assert any("time to surface of zero" in message for message in _messages(conversion, "dropped"))


def test_a_negative_readout_is_dropped_by_the_channels_own_floor_and_reported() -> None:
    """`NoDecTime: -1` and `gf99: -100` are this device's absent-markers, and §6.4 floors
    both channels at zero — so the drop is the channel's rule rather than this reader's."""
    conversion = _conversion(
        {},
        [
            suunto_sample(0, Depth=20.0, NoDecTime=-1, RtGradientFactors={"gf99": -100, "gfSurface": 0}),
            suunto_sample(10, Depth=20.0, NoDecTime=600, RtGradientFactors={"gf99": 42, "gfSurface": 30}),
        ],
    )
    profile = profile_of(conversion.document["dives"][0])
    assert profile["ndl"] == {"times": [10], "values": [600]}
    assert profile["gradient_factor"] == {"times": [10], "values": [42]}
    # A zero surface gradient factor is a reading, and stays.
    assert profile["surface_gradient_factor"] == {"times": [0, 10], "values": [0, 30]}
    dropped = _messages(conversion, "dropped")
    assert any("ndl readings" in message and "negative" in message for message in dropped)
    assert any("gradient_factor readings" in message and "negative" in message for message in dropped)


@pytest.mark.parametrize("spelling", ["gfSurface", "gtSurface"])
def test_both_firmware_spellings_reach_the_surface_gradient_factor(spelling: str) -> None:
    """The Ocean's 2.40.56 export writes `gtSurface` and its 2.51.28 export `gfSurface` — a
    vendor typo fixed in an update — and both files are real, so a reader that knew one would
    lose the channel on every dive written by the other."""
    found = _channels(suunto_sample(0, Depth=20.0, RtGradientFactors={"gf99": 42, spelling: 77}))
    assert found["surface_gradient_factor"] == {"times": [0], "values": [77]}


def test_the_leading_tissue_number_is_not_a_loading_and_is_not_mapped() -> None:
    """`gfLeadingTissue` is which compartment is leading rather than how loaded it is, and no
    member holds a compartment number."""
    found = _channels(
        suunto_sample(0, Depth=20.0, RtGradientFactors={"gf99": 42, "gfLeadingTissue": 3, "gfSurface": 77})
    )
    assert found["gradient_factor"]["values"] == [42]
    assert found["surface_gradient_factor"]["values"] == [77]


def test_a_four_figure_gradient_factor_is_carried_as_written() -> None:
    """The Ocean's `gf99` reaches 12 575 on a decompression ascent and Suunto publishes no
    definition of the field, so the converter writes the reading and explains nothing — §6.4
    puts no ceiling on the channel for the same reason, and a cap is §5.4's forbidden guess
    wearing a plausible number."""
    found = _channels(suunto_sample(0, Depth=7.62, RtGradientFactors={"gf99": 12575}))
    assert found["gradient_factor"]["values"] == [12575]
