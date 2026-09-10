"""What this reader does with a file's structure, message by message.

Split from `test_fit_units.py` the way the two XML readers' test modules are: the arithmetic
is there, and what is here is which message carries which member, which one wins where two
could, and what the report says when neither does.

**Most of what is below is the only test several messages will get.** `dive_summary`,
`tank_summary`, `tank_update` and most of `dive_gas` are Garmin's, and there is no Garmin
file in this project — the owner supplies a Descent export after this reader lands, and
`docs/fit-mapping.md` says so in its first paragraph. Until then these encoder-built
messages are the evidence, and they prove the mapping rather than the device: a real file
may still surprise this reader in ways nothing here can predict.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from fitbuild import STARTED_AT, dive_file, fit_file, message, record_stream
from helpers import FIXTURES, device_of, profile_of

from divejson.converter import Scope, SourceTooLargeError, UnsupportedSourceError
from divejson.fit import DEGREES_PER_SEMICIRCLE, FIT, MAX_MESSAGES, MalformedFitError
from divejson.registry import convert, sniff

EXPORTED_AT = datetime(2026, 9, 5, tzinfo=timezone.utc)
OCEAN = (FIXTURES / "fit" / "suunto-ocean.fit").read_bytes()


def _run(data: bytes):
    return FIT.convert(data, exported_at=EXPORTED_AT, scope=Scope())


def _dive(data: bytes) -> dict:
    return _run(data).document["dives"][0]


def _messages(conversion, kind: str) -> list[str]:
    return [note.message for note in conversion.notes if note.kind == kind]


def _at(offset: int, **values):
    return message("record", timestamp=STARTED_AT + timedelta(seconds=offset), **values)


def _gas(index: int, oxygen: int, **rest):
    return message("dive_gas", message_index=index, oxygen_content=oxygen, status="enabled", **rest)


# -- claiming the bytes ---------------------------------------------------------------


def test_the_magic_is_at_offset_eight_and_not_at_zero() -> None:
    """Which is why a container's own head can never decide the format of its members.

    Every other reader here claims a root element in the first bytes of the file. FIT's
    marker sits after the header preamble, unreachable from the front of an archive — so
    the archive walk sniffs each member's own head rather than the container's.
    """
    assert OCEAN[8:12] == b".FIT"
    assert FIT.sniff(OCEAN[:512])
    assert not FIT.sniff(OCEAN[9:512])
    assert sniff(OCEAN[:512]) == "fit"


def test_a_file_that_is_not_fit_is_not_claimed() -> None:
    """The extension is a hint for a message to a human, never how a source is decided."""
    assert not FIT.sniff(b"<divelog program='subsurface'/>")
    assert not FIT.sniff(b"")
    assert not FIT.sniff(b"\x0c\x10\x5c\x08" + b"\x00" * 8)


def test_bytes_that_carry_the_magic_and_nothing_readable_are_refused_as_a_converter_error() -> None:
    """A corrupt file reaches a diver as a sentence, not as a stack trace.

    This is the one place in the package where a third-party decoder walks bytes a stranger
    supplied, and a corrupt file does not reliably present as the decoder's own error type:
    flipped bytes past the header raise `AssertionError` from the reader, `ValueError` from
    a bad field definition and `TypeError` from the processors.
    """
    corrupt = bytearray(OCEAN)
    corrupt[40:200] = b"\xff" * 160
    with pytest.raises(MalformedFitError, match="not a readable FIT file"):
        _run(bytes(corrupt))


def test_a_truncated_file_is_refused_the_same_way() -> None:
    with pytest.raises(MalformedFitError):
        _run(OCEAN[:400])


def test_a_file_with_no_session_describes_no_dive() -> None:
    """The summary is written after the samples, so a file cut short has no dive in it.

    Refused rather than converted from the samples alone: what a truncated file has lost is
    the start time, the duration and the depths, and a confidently empty dive is worse than
    a refusal a diver can act on.
    """
    data = fit_file(message("file_id", type="activity", manufacturer="suunto"), _at(0, depth=5.0))
    with pytest.raises(MalformedFitError, match="carries no session"):
        _run(data)


def test_a_non_diving_activity_with_no_depth_is_refused() -> None:
    data = dive_file(sport="running", session={"max_depth": None, "avg_depth": None})
    with pytest.raises(MalformedFitError, match="records a running activity"):
        _run(data)


def test_a_session_that_names_no_sport_is_accepted_where_the_file_carried_depths() -> None:
    """Depth samples are the stronger evidence, and cost nothing to check."""
    data = dive_file(_at(0, depth=5.0), _at(30, depth=12.0), sport=None, session={"total_elapsed_time": 60.0})
    assert _dive(data)["max_depth"] == 45.91


def test_a_file_past_the_message_cap_is_refused_before_it_is_all_decoded() -> None:
    """Bounded by what one already-bounded file may ask this process to do.

    A run of bare `record`s behind one definition is how a device really encodes a long log,
    about ten bytes each, and is where an unbounded read hurts.
    """
    with pytest.raises(SourceTooLargeError, match="more than 100,000 messages"):
        _run(record_stream(MAX_MESSAGES - 4))


def test_a_file_exactly_at_the_cap_is_read() -> None:
    """The boundary itself, because that is where an off-by-one lives.

    `record_stream` writes five frames of its own around the records — the header, two
    definitions, a `file_id` and the CRC — so this file decodes to exactly `MAX_MESSAGES`
    of them. It then refuses for carrying no session, which is the point: it got there.
    """
    with pytest.raises(MalformedFitError, match="carries no session"):
        _run(record_stream(MAX_MESSAGES - 5))


# -- the local time zone --------------------------------------------------------------


def test_the_offset_is_the_difference_between_the_activity_message_two_timestamps() -> None:
    """Two renderings of one instant, whose difference is the wall clock at the dive site.

    §5.2 asks that a recorded offset be preserved and never supplied. This one is recorded —
    recovered from a subtraction rather than assumed from anything.
    """
    data = dive_file(
        message(
            "activity",
            timestamp=STARTED_AT + timedelta(hours=1),
            local_timestamp=STARTED_AT + timedelta(hours=1, minutes=330),
        )
    )
    assert _dive(data)["started_at"] == "2026-04-17T15:19:23+05:30"


def test_a_file_with_no_activity_message_carries_the_utc_instant_and_says_so() -> None:
    """FIT states outright that its timestamps are UTC, so `+00:00` is not invented.

    What is lost is the wall clock the diver read, and the report is where that is said.
    """
    conversion = _run(dive_file())
    assert conversion.document["dives"][0]["started_at"] == "2026-04-17T09:49:23+00:00"
    assert any("local time zone cannot be recovered" in text for text in _messages(conversion, "absent"))


def test_an_offset_no_zone_has_is_read_as_no_offset() -> None:
    """Past ±14:00 one of the two timestamps is corrupt.

    `timezone()` itself raises past 24 hours, which would leave a converter crash where a
    report line belongs.
    """
    data = dive_file(
        message("activity", timestamp=STARTED_AT, local_timestamp=STARTED_AT + timedelta(hours=20))
    )
    conversion = _run(data)
    assert conversion.document["dives"][0]["started_at"] == "2026-04-17T09:49:23+00:00"
    assert any("local time zone cannot be recovered" in text for text in _messages(conversion, "absent"))


def test_a_session_with_no_start_time_drops_the_dive() -> None:
    """§6.2 makes `started_at` REQUIRED, and nothing else in a FIT can stand in for it."""
    conversion = _run(dive_file(session={"start_time": None}))
    assert "dives" not in conversion.document
    assert any("the dive is dropped" in text for text in _messages(conversion, "dropped"))


# -- the summary scalars --------------------------------------------------------------


def test_the_depths_come_from_the_session_before_a_dive_summary() -> None:
    """The two agree wherever both exist; the summary is the fallback, not the source."""
    data = dive_file(
        message("dive_summary", reference_mesg="session", max_depth=99.0, avg_depth=88.0),
        session={"max_depth": 45.91, "avg_depth": 19.43},
    )
    dive = _dive(data)
    assert dive["max_depth"] == 45.91 and dive["avg_depth"] == 19.43


def test_a_dive_summary_supplies_a_depth_the_session_left_empty_and_it_is_recorded() -> None:
    """Not `inferred`: the number is Garmin's own reading, taken off a different message."""
    data = dive_file(
        message("dive_summary", reference_mesg="session", max_depth=32.41),
        _at(0, depth=5.0),
        session={"max_depth": None, "avg_depth": None},
    )
    conversion = _run(data)
    assert conversion.document["dives"][0]["max_depth"] == 32.41
    assert conversion.document["extensions"]["divejson"]["inferred"] == ["dives/0/avg_depth"]


def test_the_oxygen_accounting_comes_from_the_dive_summary_before_the_session() -> None:
    """The mirror of the depths, and the order is opposite for a reason.

    On a multi-dive Garmin file the session's CNS and OTU totals cover the whole activity
    while the summary describes the dive being read, and these members are the dive's.
    """
    data = dive_file(
        message("dive_summary", reference_mesg="session", start_cns=3, end_cns=11, o2_toxicity=23),
        session={"start_cns": 90, "end_cns": 95, "o2_toxicity": 300},
    )
    dive = _dive(data)
    assert (dive["cns_start"], dive["cns_end"], dive["otu_end"]) == (3.0, 11.0, 23.0)


def test_the_session_supplies_the_oxygen_accounting_where_there_is_no_dive_summary() -> None:
    data = dive_file(session={"start_cns": 5, "end_cns": 20, "o2_toxicity": 55})
    dive = _dive(data)
    assert (dive["cns_start"], dive["cns_end"], dive["otu_end"]) == (5.0, 20.0, 55.0)


def test_the_summary_describing_the_whole_activity_is_the_one_read() -> None:
    """A Garmin freediving activity writes one `dive_summary` per descent *plus* a
    session-level one, and `reference_mesg` is what separates them.

    Taking the first would read one descent's depth and bottom time as the whole dive's.
    """
    data = dive_file(
        message("dive_summary", reference_mesg="lap", max_depth=12.0),
        message("dive_summary", reference_mesg="session", max_depth=32.41),
        session={"max_depth": None, "avg_depth": None},
    )
    assert _dive(data)["max_depth"] == 32.41


def test_a_lone_summary_with_no_reference_is_used() -> None:
    """A single-dive export commonly writes one with no `reference_mesg` at all."""
    data = dive_file(
        message("dive_summary", max_depth=32.41),
        session={"max_depth": None, "avg_depth": None},
    )
    assert _dive(data)["max_depth"] == 32.41


@pytest.mark.parametrize(
    ("fields", "summary", "duration"),
    [
        ({"total_elapsed_time": 4301.72, "total_timer_time": 4302.208}, None, 4302),
        ({"total_elapsed_time": None, "total_timer_time": 3000.0}, None, 3000),
        ({"total_elapsed_time": None, "total_timer_time": None}, 2400, 2400),
    ],
)
def test_the_duration_falls_back_in_the_order_a_diver_means(fields, summary, duration) -> None:
    """Elapsed time, then timer time, then Garmin's bottom time — and last for a reason.

    `total_elapsed_time` is the wall clock from the moment the dive started to the moment it
    ended, which is what a diver means by a dive's duration; `total_timer_time` excludes
    pauses, a distinction that barely exists underwater. `bottom_time` measures time *at
    depth*, not the dive.
    """
    extra = [message("dive_summary", reference_mesg="session", bottom_time=summary)] if summary else []
    assert _dive(dive_file(*extra, session=fields))["duration"] == duration


def test_a_file_with_neither_summary_nor_session_depth_infers_from_its_samples_and_lists_it() -> None:
    """The third source, and the only one that is this converter's own arithmetic.

    Every `inferred` note obliges the document to list that member under
    `extensions.divejson.inferred`, which is what §5.4 asks a writer to do with a derived
    value — so a downstream reader can tell a derivation from a reading.
    """
    data = dive_file(
        _at(0, depth=10.0),
        _at(60, depth=30.5),
        session={"max_depth": None, "avg_depth": None, "total_elapsed_time": 60.0},
    )
    conversion = _run(data)
    assert conversion.document["dives"][0]["max_depth"] == 30.5
    assert conversion.document["extensions"]["divejson"]["inferred"] == [
        "dives/0/max_depth",
        "dives/0/avg_depth",
    ]
    noted = [note.where for note in conversion.notes if note.kind == "inferred"]
    assert len(noted) == 2


def test_a_computed_mean_deeper_than_the_recorded_maximum_is_dropped_and_says_nothing_else() -> None:
    """The one route to a mean deeper than a maximum: one recorded and one computed.

    §6.2 forbids it, so the mean goes — and the report must then say only that. An
    `inferred` line about a value the document does not carry would leave the report and
    `extensions.divejson.inferred` disagreeing, which is the pairing `converter.py` says can
    never come apart: every `inferred` note's member is on that list, and every member on
    that list has a note.
    """
    data = dive_file(
        _at(0, depth=30.0),
        _at(30, depth=40.0),
        _at(60, depth=35.0),
        session={"max_depth": 20.0, "avg_depth": None, "total_elapsed_time": 60.0},
    )
    conversion = _run(data)
    dive = conversion.document["dives"][0]
    assert dive["max_depth"] == 20.0
    assert "avg_depth" not in dive
    assert any("is deeper than the greatest depth" in text for text in _messages(conversion, "dropped"))
    assert _messages(conversion, "inferred") == []
    assert "inferred" not in conversion.document["extensions"]["divejson"]


def test_the_inferred_report_and_the_derived_list_name_the_same_members() -> None:
    """Both halves, on a file that infers one member and drops the other.

    The aggregate `bool(noted) == bool(listed)` check the fixtures run would pass on a
    document that inferred two and listed one, so this pins the correspondence itself on the
    case where the two could come apart.
    """
    data = dive_file(
        _at(0, depth=30.0),
        _at(30, depth=40.0),
        _at(60, depth=35.0),
        session={"max_depth": None, "avg_depth": 10.0, "total_elapsed_time": 60.0},
    )
    conversion = _run(data)
    assert conversion.document["dives"][0]["max_depth"] == 40.0
    assert conversion.document["dives"][0]["avg_depth"] == 10.0
    assert conversion.document["extensions"]["divejson"]["inferred"] == ["dives/0/max_depth"]
    assert len(_messages(conversion, "inferred")) == 1
    assert "max_depth is computed from its own depth samples" in _messages(conversion, "inferred")[0]


def test_a_second_session_is_reported_and_not_converted() -> None:
    """A file describes one dive here, and a diver is told when it described more."""
    data = fit_file(
        message("file_id", type="activity", manufacturer="suunto"),
        _at(0, depth=5.0),
        message("session", sport="diving", start_time=STARTED_AT, total_elapsed_time=60.0, max_depth=10.0),
        message(
            "session",
            sport="diving",
            start_time=STARTED_AT + timedelta(hours=2),
            total_elapsed_time=60.0,
            max_depth=20.0,
        ),
    )
    conversion = _run(data)
    assert len(conversion.document["dives"]) == 1
    assert conversion.document["dives"][0]["max_depth"] == 10.0
    assert any("describes 2 sessions" in text for text in _messages(conversion, "dropped"))


# -- water type -----------------------------------------------------------------------


@pytest.mark.parametrize("water", ["fresh", "salt", "en13319"])
def test_the_devices_water_type_is_carried_under_its_own_name(water: str) -> None:
    """`en13319` stays `en13319` rather than being folded into `salt`.

    It is the calibration a computer ships set to, and rewriting it as the nearest real
    water would be inventing a reading.
    """
    data = dive_file(message("dive_settings", water_type=water))
    assert _dive(data)["water_type"] == water


def test_a_custom_water_density_is_reported_rather_than_rounded_to_the_nearest_water() -> None:
    data = dive_file(message("dive_settings", water_type="custom", water_density=1025.0))
    conversion = _run(data)
    assert "water_type" not in conversion.document["dives"][0]
    assert any("water type is 'custom'" in text for text in _messages(conversion, "dropped"))


def test_a_device_that_wrote_no_dive_settings_raises_nothing() -> None:
    """The message is the computer's configuration, not a reading of the dive.

    Unlike the session summaries, which the device *was* describing this dive when it left
    them empty — those get an `absent` line and this does not.
    """
    conversion = _run(dive_file())
    assert "water_type" not in conversion.document["dives"][0]
    assert not any("water" in note.message for note in conversion.notes)


# -- the gas list ---------------------------------------------------------------------


def test_a_disabled_gas_is_a_cylinder_nobody_dived() -> None:
    """A device stores its whole configured list, so a recreational air dive on a computer
    with two deco gases programmed into it would otherwise arrive with three cylinders."""
    data = dive_file(
        _gas(0, 21),
        message("dive_gas", message_index=1, oxygen_content=50, status="disabled"),
        message("dive_gas", message_index=2, oxygen_content=100, status="disabled"),
    )
    conversion = _run(data)
    assert [c["oxygen"] for c in conversion.document["dives"][0]["cylinders"]] == [21.0]
    dropped = [text for text in _messages(conversion, "dropped") if "records as disabled" in text]
    assert len(dropped) == 1, "the disabled-gas line is reported once, not once per reader of the list"
    assert "holds 2 gases" in dropped[0]


def test_a_backup_only_gas_is_carried() -> None:
    """A pony bottle: carried and not breathed, which is a cylinder that was on the dive."""
    data = dive_file(_gas(0, 21), message("dive_gas", message_index=1, oxygen_content=21, status="backup_only"))
    assert len(_dive(data)["cylinders"]) == 2


def test_the_message_index_is_masked_before_it_is_sorted_on() -> None:
    """The low 12 bits are the index and the top bits are flags.

    Without the mask a gas at index 1 with the "selected" bit set arrives as 32769 and sorts
    after an unflagged gas at index 2, reordering the cylinders.
    """
    data = dive_file(_gas(2, 50), _gas(0x8000 | 1, 32), _gas(0, 21))
    assert [c["oxygen"] for c in _dive(data)["cylinders"]] == [21.0, 32.0, 50.0]


def test_a_device_that_re_announces_its_gas_list_does_not_double_the_cylinders() -> None:
    data = dive_file(_gas(0, 21), _gas(1, 50), _gas(0, 21), _gas(1, 50))
    assert len(_dive(data)["cylinders"]) == 2


def test_a_gas_with_no_index_at_all_is_appended_rather_than_keyed_by_position() -> None:
    """Keying a position into the same table as a real `message_index` makes a gas at
    position 0 collide with a gas declaring index 0, and one of the two vanishes."""
    data = dive_file(
        message("dive_gas", oxygen_content=32, status="enabled"),
        _gas(0, 21),
    )
    assert [c["oxygen"] for c in _dive(data)["cylinders"]] == [21.0, 32.0]


def test_a_closed_circuit_diluent_says_what_it_was_for() -> None:
    """`mode` is the only field on `dive_gas` that speaks to a cylinder's role, and it
    answers half the question: `open_circuit` covers a back gas and a stage alike."""
    data = dive_file(_gas(0, 21, mode="closed_circuit_diluent"), _gas(1, 50, mode="open_circuit"))
    cylinders = _dive(data)["cylinders"]
    assert cylinders[0]["role"] == "diluent"
    assert "role" not in cylinders[1]


def test_a_mix_summing_past_a_hundred_percent_is_dropped_whole() -> None:
    data = dive_file(message("dive_gas", message_index=0, oxygen_content=60, helium_content=60, status="enabled"))
    conversion = _run(data)
    assert conversion.document["dives"][0]["cylinders"] == [{}]
    assert any("sum above 100 percent" in text for text in _messages(conversion, "dropped"))


def test_a_gas_the_device_recorded_nothing_about_is_not_read_as_air() -> None:
    """§6.3: absent oxygen means not recorded, never air."""
    conversion = _run(dive_file(message("dive_gas", message_index=0, status="enabled")))
    assert conversion.document["dives"][0]["cylinders"] == [{}]
    assert any("never air" in text for text in _messages(conversion, "absent"))


# -- tank telemetry -------------------------------------------------------------------


def test_a_tank_summary_and_its_transmitters_stream_are_joined_per_pod_and_per_field() -> None:
    """The realistic case is the partial one: a pod that drops out near the end writes a
    summary with a start pressure and no end, and the last real reading is the one a gas
    calculation turns on."""
    data = dive_file(
        _gas(0, 21),
        message("tank_summary", timestamp=STARTED_AT, sensor=7, start_pressure=210.0),
        message("tank_update", timestamp=STARTED_AT, sensor=7, pressure=209.0),
        message("tank_update", timestamp=STARTED_AT + timedelta(seconds=60), sensor=7, pressure=64.0),
        _at(0, depth=5.0),
        _at(60, depth=12.0),
        session={"total_elapsed_time": 60.0},
    )
    cylinder = _dive(data)["cylinders"][0]
    assert cylinder["start_pressure"] == 210.0
    assert cylinder["end_pressure"] == 64.0


def test_a_transmitters_pressures_become_a_profile_channel_numbered_by_its_cylinder() -> None:
    """§6.3 calls `gas_number` a label rather than an array index, so a numbering is
    asserted only where something in the profile depends on it."""
    data = dive_file(
        _gas(0, 21),
        message("tank_update", timestamp=STARTED_AT, sensor=7, pressure=210.0),
        message("tank_update", timestamp=STARTED_AT + timedelta(seconds=60), sensor=7, pressure=64.0),
        _at(0, depth=5.0),
        _at(60, depth=12.0),
        session={"total_elapsed_time": 60.0},
    )
    dive = _dive(data)
    assert dive["cylinders"][0]["gas_number"] == 0
    assert profile_of(dive)["pressures"] == [{"times": [0, 60], "values": [2100, 640], "gas_number": 0}]


def test_a_pods_ends_are_its_earliest_and_latest_readings_not_the_files_first_and_last() -> None:
    """No writer guarantees it emitted its samples in order, which is why the axis sorts.

    A pod's channel comes off that axis, so ends read out of file order would put one pair
    of readings on the cylinder and a different pair at the ends of its own channel in the
    same document — and here they invert, so the `end > start` guard would drop the real
    end pressure and keep the later reading as the start.
    """
    data = dive_file(
        _gas(0, 21),
        # Emitted last-first: 64 bar at +60 s arrives before 210 bar at +0 s.
        message("tank_update", timestamp=STARTED_AT + timedelta(seconds=60), sensor=7, pressure=64.0),
        message("tank_update", timestamp=STARTED_AT, sensor=7, pressure=210.0),
        _at(0, depth=5.0),
        _at(60, depth=12.0),
        session={"total_elapsed_time": 60.0},
    )
    conversion = _run(data)
    dive = conversion.document["dives"][0]
    assert dive["cylinders"][0]["start_pressure"] == 210.0
    assert dive["cylinders"][0]["end_pressure"] == 64.0
    assert profile_of(dive)["pressures"][0]["values"] == [2100, 640]
    assert _messages(conversion, "dropped") == []


def test_pressures_are_dropped_where_the_counts_do_not_match_exactly() -> None:
    """Nothing in a FIT file links a gas to a pod, so position is the only signal there is —
    and attaching a start pressure to a cylinder it may not have been measured in is worse
    than carrying no pressure at all."""
    data = dive_file(
        _gas(0, 21),
        _gas(1, 50),
        message("tank_update", timestamp=STARTED_AT, sensor=7, pressure=210.0),
        _at(0, depth=5.0),
        session={"total_elapsed_time": 60.0},
    )
    conversion = _run(data)
    assert conversion.document["dives"][0]["cylinders"] == [{"oxygen": 21.0}, {"oxygen": 50.0}]
    assert any("2 gases and 1 cylinder pressure sources" in text for text in _messages(conversion, "dropped"))


def test_telemetry_with_no_gas_list_at_all_becomes_the_cylinders() -> None:
    """Evidence of a tank is evidence of a tank."""
    data = dive_file(
        message("tank_summary", timestamp=STARTED_AT, sensor=7, start_pressure=210.0, end_pressure=64.0),
        message("tank_summary", timestamp=STARTED_AT, sensor=8, start_pressure=200.0, end_pressure=180.0),
        session={"total_elapsed_time": 60.0},
    )
    assert _dive(data)["cylinders"] == [
        {"start_pressure": 210.0, "end_pressure": 64.0},
        {"start_pressure": 200.0, "end_pressure": 180.0},
    ]


def test_a_zero_start_pressure_is_a_devices_absent_marker() -> None:
    """§6.3 is explicit: writers must not emit one, and a reader must not carry it."""
    data = dive_file(
        message("tank_summary", timestamp=STARTED_AT, sensor=7, start_pressure=0.0, end_pressure=64.0),
        session={"total_elapsed_time": 60.0},
    )
    conversion = _run(data)
    assert conversion.document["dives"][0]["cylinders"] == [{"end_pressure": 64.0}]
    assert any("read as not recorded" in text for text in _messages(conversion, "absent"))


def test_an_end_pressure_above_its_start_drops_the_end() -> None:
    data = dive_file(
        message("tank_summary", timestamp=STARTED_AT, sensor=7, start_pressure=100.0, end_pressure=200.0),
        session={"total_elapsed_time": 60.0},
    )
    conversion = _run(data)
    assert conversion.document["dives"][0]["cylinders"] == [{"start_pressure": 100.0}]
    assert any("above its start pressure" in text for text in _messages(conversion, "dropped"))


def test_a_pressure_past_the_formats_ceiling_is_dropped() -> None:
    data = dive_file(
        message("tank_summary", timestamp=STARTED_AT, sensor=7, start_pressure=400.0, end_pressure=64.0),
        session={"total_elapsed_time": 60.0},
    )
    conversion = _run(data)
    assert conversion.document["dives"][0]["cylinders"] == [{"end_pressure": 64.0}]
    assert any("outside the 0 to 350" in text for text in _messages(conversion, "dropped"))


def test_a_bare_tank_summary_naming_a_pod_and_no_pressures_grows_no_cylinder() -> None:
    data = dive_file(
        message("tank_summary", timestamp=STARTED_AT, sensor=7, volume_used=1400.0),
        session={"total_elapsed_time": 60.0},
    )
    assert "cylinders" not in _dive(data)


# -- events ---------------------------------------------------------------------------


def _event(offset: int, name: str, **rest):
    return message(
        "event",
        timestamp=STARTED_AT + timedelta(seconds=offset),
        event=name,
        event_type="marker",
        **rest,
    )


def test_a_gas_switch_names_the_cylinder_the_logbook_shows() -> None:
    """`data` holds the switched-to gas's `message_index`, which is the device's own key
    for a `dive_gas` entry and not the position §6.3 numbers a cylinder by."""
    data = dive_file(
        _gas(0, 21),
        _gas(4, 50),
        _at(0, depth=30.0),
        _at(60, depth=6.0),
        _event(60, "dive_gas_switched", data=4),
        session={"total_elapsed_time": 60.0},
    )
    dive = _dive(data)
    assert profile_of(dive)["events"] == [{"time": 60, "type": "gas_switch", "gas_number": 1}]
    assert [c["gas_number"] for c in dive["cylinders"]] == [0, 1]


def test_a_switch_to_a_gas_this_file_does_not_describe_is_still_a_switch() -> None:
    """Saying "a gas switch, to something this file does not describe" is honest where
    guessing a position would not be."""
    data = dive_file(
        _gas(0, 21),
        _at(0, depth=30.0),
        _at(60, depth=6.0),
        _event(60, "dive_gas_switched", data=9),
        session={"total_elapsed_time": 60.0},
    )
    dive = _dive(data)
    assert profile_of(dive)["events"] == [{"time": 60, "type": "gas_switch"}]
    assert "gas_number" not in dive["cylinders"][0]


def test_a_user_marker_is_a_bookmark() -> None:
    data = dive_file(
        _at(0, depth=30.0),
        _at(60, depth=6.0),
        _event(60, "user_marker"),
        session={"total_elapsed_time": 60.0},
    )
    assert profile_of(_dive(data))["events"] == [{"time": 60, "type": "bookmark"}]


def test_a_dive_alert_carries_the_devices_own_wording() -> None:
    """`data` renders through the profile's `dive_alert` enum, so this is the device's word
    rather than a number. §6.5 requires a label on `other`."""
    data = dive_file(
        _at(0, depth=30.0),
        _at(60, depth=6.0),
        _event(60, "dive_alert", data=0),
        session={"total_elapsed_time": 60.0},
    )
    assert profile_of(_dive(data))["events"] == [
        {"time": 60, "type": "other", "label": "ndl_reached"}
    ]


def test_an_alert_the_device_gives_no_code_for_is_dropped_rather_than_failing_the_file() -> None:
    data = dive_file(
        _at(0, depth=30.0),
        _at(60, depth=6.0),
        _event(60, "dive_alert"),
        session={"total_elapsed_time": 60.0},
    )
    conversion = _run(data)
    assert "events" not in profile_of(conversion.document["dives"][0])
    assert any("gives no code for" in text for text in _messages(conversion, "dropped"))


def test_the_timer_event_every_file_carries_is_not_an_event_of_this_format() -> None:
    """Its start/stop pair says where the dive begins and ends, which `started_at` and the
    profile's own axis already say twice over."""
    data = dive_file(
        _at(0, depth=30.0),
        _at(60, depth=6.0),
        _event(0, "timer"),
        _event(60, "timer"),
        session={"total_elapsed_time": 60.0},
    )
    assert "events" not in profile_of(_dive(data))


def test_an_event_at_an_instant_the_samples_do_not_reach_is_dropped() -> None:
    data = dive_file(
        _at(0, depth=30.0),
        _at(60, depth=6.0),
        _event(999, "user_marker"),
        session={"total_elapsed_time": 60.0},
    )
    conversion = _run(data)
    assert "events" not in profile_of(conversion.document["dives"][0])
    assert any("no place on the profile's time axis" in text for text in _messages(conversion, "dropped"))


# -- the sample axis ------------------------------------------------------------------


def test_a_record_with_no_timestamp_has_no_place_on_the_axis() -> None:
    data = dive_file(_at(0, depth=5.0), message("record", depth=9.0), session={"total_elapsed_time": 60.0})
    conversion = _run(data)
    assert profile_of(conversion.document["dives"][0])["depth"]["values"] == [500]
    assert any("records no timestamp" in text for text in _messages(conversion, "dropped"))


def test_two_messages_at_one_instant_are_one_sample() -> None:
    """A device writing depth on one `record` and temperature on the next is the ordinary
    case, and a `tank_update` beside a `record` is the same shape."""
    data = dive_file(
        _at(0, depth=5.0),
        _at(0, temperature=22),
        session={"total_elapsed_time": 60.0},
    )
    profile = profile_of(_dive(data))
    assert profile["depth"] == {"times": [0], "values": [500]}
    assert profile["temperature"] == {"times": [0], "values": [220]}


def test_two_readings_of_one_channel_at_one_instant_report_the_collision() -> None:
    """§6.5's times are strictly increasing, so only one of them can be kept."""
    data = dive_file(_at(0, depth=5.0), _at(0, depth=9.0), session={"total_elapsed_time": 60.0})
    conversion = _run(data)
    assert profile_of(conversion.document["dives"][0])["depth"]["values"] == [500]
    assert any("two messages record a depth at one instant" in text for text in _messages(conversion, "dropped"))


def test_a_record_before_the_session_started_is_dropped() -> None:
    """The origin is the session's own start time, so the profile's seconds are elapsed time
    from the moment the dive began — the same instant `started_at` names."""
    data = dive_file(_at(-30, depth=1.0), _at(0, depth=5.0), session={"total_elapsed_time": 60.0})
    conversion = _run(data)
    assert profile_of(conversion.document["dives"][0])["depth"] == {"times": [0], "values": [500]}
    assert any("before the dive began" in text for text in _messages(conversion, "dropped"))


def test_samples_carrying_a_time_and_no_reading_produce_no_profile_at_all() -> None:
    """Rather than one with a bare `duration: 0`, which is a claim the source did not make."""
    data = dive_file(_at(0, heart_rate=70), _at(30, heart_rate=72), session={"total_elapsed_time": 60.0})
    conversion = _run(data)
    assert profile_of(conversion.document["dives"][0]) is None
    assert any("no reading this format can hold" in text for text in _messages(conversion, "dropped"))


def test_a_file_with_no_samples_at_all_says_nothing_about_a_profile() -> None:
    """The source said nothing here, where above it said something uncarriable."""
    conversion = _run(dive_file())
    assert profile_of(conversion.document["dives"][0]) is None
    assert not any("profile" in note.message for note in conversion.notes)


# -- positions ------------------------------------------------------------------------


def test_the_fixes_either_side_of_the_deepest_sample_are_the_entry_and_the_exit() -> None:
    """No fix is taken underwater, so the only question worth asking of one is which surface
    interval it belongs to. The last before the pivot and the first after are the two kept:
    the fix that says where a diver got in is the one taken just before they descended."""
    fix = round(28.5 / float(DEGREES_PER_SEMICIRCLE))
    data = dive_file(
        _at(0, depth=1.0, position_lat=fix, position_long=fix),
        _at(30, depth=2.0, position_lat=fix + 1000, position_long=fix + 1000),
        _at(60, depth=40.0),
        _at(90, depth=3.0, position_lat=fix + 2000, position_long=fix + 2000),
        _at(120, depth=1.0, position_lat=fix + 3000, position_long=fix + 3000),
        session={"total_elapsed_time": 120.0},
    )
    dive = _dive(data)
    assert dive["entry_position"]["latitude"] < dive["exit_position"]["latitude"]
    assert dive["entry_position"]["latitude"] == pytest.approx(28.500084, abs=1e-6)
    assert dive["exit_position"]["latitude"] == pytest.approx(28.500168, abs=1e-6)


def test_a_file_with_fixes_and_no_depth_channel_cannot_say_which_is_the_entry() -> None:
    """With no depth channel there is no pivot and so no answer, and nothing is written."""
    fix = round(28.5 / float(DEGREES_PER_SEMICIRCLE))
    data = dive_file(
        _at(0, position_lat=fix, position_long=fix),
        _at(60, position_lat=fix + 1000, position_long=fix + 1000),
        session={"total_elapsed_time": 60.0},
    )
    dive = _dive(data)
    assert "entry_position" not in dive and "exit_position" not in dive


# -- an archive of them ---------------------------------------------------------------


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_a_zip_of_fit_files_converts_as_one_logbook() -> None:
    """A watch writes one file per dive and an account export is an archive of them.

    Nothing here is FIT-specific — the container reader is the registry's, and the same
    walk serves a zip of `.ssrf`. What is FIT-specific is that the archive's own head
    cannot decide it, the magic being at offset 8 of each *member*.
    """
    conversion = convert(_zip({"a.fit": OCEAN, "b.fit": OCEAN}), exported_at=EXPORTED_AT)
    dives = conversion.document["dives"]
    assert len(dives) == 2
    assert dives[0]["max_depth"] == dives[1]["max_depth"] == 45.91
    assert conversion.document["extensions"]["divejson"]["converted_from"] == "fit"


def test_two_copies_of_one_file_are_two_dives_with_two_identities() -> None:
    """Which is right: nothing in either file says they are the same dive.

    A FIT records no id for its dive, so identity is position — and a member of an archive
    prefixes its positional stand-in with its own name, so the two do not collide.
    """
    dives = convert(_zip({"a.fit": OCEAN, "b.fit": OCEAN}), exported_at=EXPORTED_AT).document["dives"]
    assert dives[0]["uuid"] != dives[1]["uuid"]


def test_the_callers_cap_refuses_a_third_copy() -> None:
    """The library streams and the caller sets the caps; an application passes its own."""
    with pytest.raises(SourceTooLargeError, match="holds 3 files"):
        convert(_zip({"a.fit": OCEAN, "b.fit": OCEAN, "c.fit": OCEAN}), max_members=2)


def test_an_archive_mixing_fit_with_another_format_is_refused() -> None:
    """An archive is one logbook, so "we read three of your seven dives" is not an answer."""
    ssrf = (FIXTURES / "ssrf" / "subsurface.ssrf").read_bytes()
    with pytest.raises(UnsupportedSourceError, match="mixes fit and ssrf"):
        convert(_zip({"a.fit": OCEAN, "b.ssrf": ssrf}))


# -- the device ------------------------------------------------------------------------


def test_the_device_is_the_file_ids_maker_and_product_name() -> None:
    """A FIT file is one dive written by one computer, so a document has one recording."""
    data = fit_file(
        message("file_id", type="activity", manufacturer="suunto", product_name="Suunto Ocean"),
        _at(0, depth=5.0),
        message("session", sport="diving", start_time=STARTED_AT, total_elapsed_time=60.0, dive_number=3),
    )
    assert device_of(_dive(data)) == {"brand": "suunto", "model": "Suunto Ocean", "dive_number": 3}


def test_a_numeric_product_id_is_not_a_model() -> None:
    """§6.4b's `model` is the product string as the source names it, and `product` (2) is a
    bare vendor id — the Ocean's is `62`. There is no fall-through to it and none to the
    manufacturer either: copying the maker in would say `suunto` is the product."""
    data = fit_file(
        message("file_id", type="activity", manufacturer="suunto", product=62),
        _at(0, depth=5.0),
        message("session", sport="diving", start_time=STARTED_AT, total_elapsed_time=60.0),
    )
    assert device_of(_dive(data)) == {"brand": "suunto"}


def test_the_serial_and_firmware_come_from_device_index_zero() -> None:
    """libdivecomputer's rule. A computer writes a `device_info` for everything in the
    chain — a transmitter, a heart-rate strap — and reading a transmitter's serial as the
    computer's would pair two dives that were never on one wrist."""
    data = fit_file(
        message("file_id", type="activity", manufacturer="suunto", serial_number=11111111),
        message("device_info", device_index=1, serial_number=99999999, software_version=9.9),
        message("device_info", device_index=0, serial_number=3810000400, software_version=2.51),
        _at(0, depth=5.0),
        message("session", sport="diving", start_time=STARTED_AT, total_elapsed_time=60.0),
    )
    device = device_of(_dive(data))
    assert device["serial"] == "3810000400" and device["firmware"] == "2.51"


def test_the_file_ids_own_serial_is_the_fallback() -> None:
    """The file's own claim about what wrote it, where no `device_info` names index 0 —
    which is both Suunto files in `fixtures/fit/`."""
    data = fit_file(
        message("file_id", type="activity", manufacturer="suunto", serial_number=11111111),
        message("device_info", device_index=1, serial_number=99999999),
        _at(0, depth=5.0),
        message("session", sport="diving", start_time=STARTED_AT, total_elapsed_time=60.0),
    )
    assert device_of(_dive(data))["serial"] == "11111111"
    assert "firmware" not in device_of(_dive(data))


def test_a_file_that_names_no_computer_and_kept_no_sample_has_no_recording() -> None:
    """§6.4a forbids a recording that carries nothing."""
    data = fit_file(
        message("file_id", type="activity"),
        message("session", sport="diving", start_time=STARTED_AT, total_elapsed_time=60.0),
    )
    assert "recordings" not in _dive(data)
