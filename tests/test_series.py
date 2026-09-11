"""The sample axis, checked away from any one format's elements.

`tests/test_uddf_parsing.py` exercises these rules through `<waypoint>`, which is what
proves the UDDF reader still obeys them; these prove the rules themselves, so the next
three adapters inherit something that has been tested rather than something that happened
to work for XML.
"""

from __future__ import annotations

from divejson.converter import NoteKind
from divejson.series import Channel, SampleAxis


class Reported:
    """A note sink that keeps what it was told, in order."""

    def __init__(self) -> None:
        self.notes: list[tuple[str, str, NoteKind]] = []

    def __call__(self, where: str, message: str, kind: NoteKind) -> None:
        self.notes.append((where, message, kind))

    @property
    def messages(self) -> list[str]:
        return [message for _, message, _ in self.notes]


def _axis(report: Reported) -> SampleAxis:
    return SampleAxis(report, "dive/0", noun="record", time_member="timestamp")


# -- the axis -------------------------------------------------------------------------


def test_samples_are_ordered_by_their_recorded_time_not_their_position() -> None:
    """§6.5 wants strictly increasing times and no writer guarantees it emitted them so."""
    report = Reported()
    axis = _axis(report)
    for second in (120, 30, 90):
        axis.offer(second, f"at {second}")
    assert [pair[0] for pair in axis.ordered()] == [30, 90, 120]
    assert report.notes == []


def test_a_sample_with_no_time_is_dropped_and_reported_where_it_sat() -> None:
    report = Reported()
    axis = _axis(report)
    axis.offer(10, "first")
    axis.offer(None, "second")
    assert [pair[1] for pair in axis.ordered()] == ["first"]
    assert report.notes == [
        (
            "dive/0/record/1",
            "the record records no timestamp, so it has no place on the profile's time axis; dropped",
            "dropped",
        )
    ]


def test_a_notes_path_counts_samples_in_document_order() -> None:
    """Including the ones that were dropped, because the path is where a diver would look.

    Counting only the samples that found a place would number a drop after the last good
    one, and send someone to an element that converted perfectly.
    """
    report = Reported()
    axis = _axis(report)
    axis.offer(None, "no time")
    axis.offer(10, "kept")
    axis.offer(None, "no time")
    axis.offer(None, "no time")
    assert [where for where, _, _ in report.notes] == [
        "dive/0/record/0",
        "dive/0/record/2",
        "dive/0/record/3",
    ]


def test_a_sample_before_the_dive_began_is_dropped() -> None:
    report = Reported()
    axis = _axis(report)
    axis.offer(-1, "impossible")
    assert axis.ordered() == []
    assert report.messages == ["the record is at -1 s, before the dive began; dropped"]


def test_two_samples_on_one_second_keep_the_first_and_report_the_second() -> None:
    report = Reported()
    axis = _axis(report)
    axis.offer(30, "kept")
    axis.offer(30, "later")
    assert [pair[1] for pair in axis.ordered()] == ["kept"]
    assert "two records share the second 30" in report.messages[0]


def test_the_order_is_worked_out_once_however_often_it_is_asked_for() -> None:
    """A second walk of the samples must not double the report."""
    report = Reported()
    axis = _axis(report)
    axis.offer(30, "kept")
    axis.offer(30, "later")
    axis.ordered()
    axis.ordered()
    assert len(report.notes) == 1


# -- channels -------------------------------------------------------------------------


def test_a_channel_takes_only_the_seconds_that_carried_a_reading_for_it() -> None:
    """No channel is padded to another's length, which is the whole shape of §6.5."""
    depth, temperature = Channel("depth"), Channel("temperature")
    for second in (0, 10, 20):
        depth.record(second, second * 10)
    temperature.record(10, 214)
    assert len(depth) == 3
    assert temperature.member() == {"times": [10], "values": [214]}


def test_a_channel_refuses_a_second_reading_at_one_second() -> None:
    channel = Channel("depth")
    assert channel.record(30, 100) is True
    assert channel.record(30, 200) is False
    assert channel.member() == {"times": [30], "values": [100]}


# -- the profile ----------------------------------------------------------------------


def test_the_duration_is_the_span_of_the_samples_themselves() -> None:
    """§6.4 defines it that way, so it is structural and carries no note of its own."""
    report = Reported()
    axis = _axis(report)
    for second in (0, 30, 90):
        axis.offer(second, second)
    depth, temperature = Channel("depth"), Channel("temperature")
    for second, _ in axis.ordered():
        depth.record(second, 100)
    temperature.record(30, 214)

    profile = axis.profile({"depth": depth, "temperature": temperature})
    assert profile is not None
    assert profile["duration"] == 90
    assert list(profile) == ["duration", "depth", "temperature"]
    assert report.notes == []


def test_samples_that_carry_a_time_and_no_reading_produce_no_profile_and_say_so() -> None:
    """A bare `duration: 0` would assert a sampled record of zero length, which nobody said."""
    report = Reported()
    axis = _axis(report)
    axis.offer(0, "nothing usable")
    axis.offer(30, "nothing usable")
    axis.ordered()

    assert axis.profile({"depth": Channel("depth")}) is None
    assert "the dive's 2 records carry a time but no reading" in report.messages[0]


def test_one_such_sample_is_reported_in_the_singular() -> None:
    report = Reported()
    axis = _axis(report)
    axis.offer(0, "nothing usable")
    axis.ordered()
    assert axis.profile({"depth": Channel("depth")}) is None
    assert "the dive's 1 record carries a time" in report.messages[0]


def test_a_dive_whose_samples_all_lost_their_place_says_nothing_further() -> None:
    """Every one of them was already reported as it was dropped; a second line adds none."""
    report = Reported()
    axis = _axis(report)
    axis.offer(None, "no time")
    assert axis.profile({"depth": Channel("depth")}) is None
    assert len(report.notes) == 1


def test_pressures_are_emitted_in_gas_number_order_with_their_labels() -> None:
    report = Reported()
    axis = _axis(report)
    axis.offer(0, 0)
    axis.ordered()
    second, first = Channel("pressures"), Channel("pressures")
    second.record(0, 2000)
    first.record(0, 1000)

    profile = axis.profile({"depth": Channel("depth")}, pressures=((1, second), (0, first)))
    assert profile is not None
    assert [series["gas_number"] for series in profile["pressures"]] == [0, 1]
    assert profile["duration"] == 0


def test_events_alone_are_a_profile_and_are_ordered_by_time() -> None:
    report = Reported()
    axis = _axis(report)
    axis.offer(0, 0)
    axis.ordered()
    events = [{"time": 300, "type": "bookmark"}, {"time": 120, "type": "safety_stop"}]

    profile = axis.profile({"depth": Channel("depth")}, events=events)
    assert profile is not None
    assert [event["time"] for event in profile["events"]] == [120, 300]
    # `duration` spans the samples, and an event after the last one is conforming (§6.4).
    assert profile["duration"] == 0


# -- a channel's floor ------------------------------------------------------------------


def test_a_reading_below_the_channels_floor_is_refused_and_counted() -> None:
    """§6.4 floors the decompression readouts at zero, and the floor comes off the schema
    rather than out of an adapter — which is what keeps it one rule instead of five."""
    channel = Channel("ndl")
    assert channel.record(0, -1) is False
    assert channel.record(10, 0) is True
    assert (channel.times, channel.values) == ([10], [0])
    assert channel.refused == 1


def test_a_signed_channel_keeps_its_negative_readings() -> None:
    """An under-ice dive is a negative temperature, and a `-20` there is a reading."""
    channel = Channel("temperature")
    assert channel.record(0, -20) is True
    assert channel.values == [-20]
    assert channel.refused == 0


def test_the_refusals_are_reported_once_per_channel_rather_than_once_per_sample() -> None:
    """A device writes its absent-marker for a run of samples — 5 531 of one export's 7 194
    `gf99` readings — and a line apiece would bury every other finding in the report."""
    report = Reported()
    axis = _axis(report)
    for second in (0, 10, 20):
        axis.offer(second, second)
    axis.ordered()
    channel = Channel("gradient_factor")
    for second in (0, 10, 20):
        channel.record(second, -100)
    depth = Channel("depth")
    depth.record(0, 100)
    axis.profile({"depth": depth, "gradient_factor": channel})
    assert [note for note in report.notes if "gradient_factor" in note[1]] == [
        (
            "dive/0",
            "3 of the dive's gradient_factor readings are negative, which is how a device spells a readout "
            "it does not have; those samples are dropped from the channel, §6.4 recording it from zero up",
            "dropped",
        )
    ]


def test_one_refusal_is_reported_in_the_singular() -> None:
    report = Reported()
    axis = _axis(report)
    axis.offer(0, 0)
    axis.ordered()
    channel = Channel("tts")
    channel.record(0, -1)
    depth = Channel("depth")
    depth.record(0, 100)
    axis.profile({"depth": depth, "tts": channel})
    assert any("1 of the dive's tts readings is negative" in note[1] for note in report.notes)


def test_the_profile_reads_down_section_6_4_whatever_order_the_channels_arrive_in() -> None:
    """`pressures` sits between `temperature` and `ndl` in the section, and an adapter that
    listed its channels in any other order would emit a profile that reads down nothing."""
    report = Reported()
    axis = _axis(report)
    axis.offer(0, 0)
    axis.ordered()
    built = {}
    for name in ("ndl", "depth", "temperature"):
        channel = Channel(name)
        channel.record(0, 1)
        built[name] = channel
    pressure = Channel("pressures")
    pressure.record(0, 2000)
    profile = axis.profile(built, pressures=((0, pressure),))
    assert list(profile) == ["duration", "depth", "temperature", "pressures", "ndl"]
