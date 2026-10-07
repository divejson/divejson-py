"""The converter policy every adapter inherits: notes, identities, how a zero reads, which
satellite fix places a dive, and how long a dive spent in the water.

Nothing here is UDDF's. These are the rules a second reader would otherwise re-implement
slightly differently, which is the failure the shared module exists to prevent — a report
whose kinds mean one thing for one format and another for the next, or a `weight` of zero
read as absence because the adapter that wrote it remembered the `max_depth` rule.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from divejson import Conversion, Note
import divejson
from divejson.converter import (
    IN_WATER_DEPTH,
    INFERRED,
    MAX_MODEL_NAME,
    NOTE_KINDS,
    Claimed,
    Fix,
    Scope,
    channel_floor,
    chosen_fix,
    deco_model,
    header,
    in_seconds,
    in_water,
    milliseconds,
    onto_primary,
    profile_members,
    record_inferred,
    recorded,
    recording,
    zero_is_an_answer,
)


def _conversion(*notes: Note) -> Conversion:
    return Conversion({}, notes)


def _built(members: dict, notes: list | None = None) -> dict | None:
    """§6.4c's object from `members`, appending any report lines to `notes`."""
    collected = notes if notes is not None else []
    return deco_model(
        members,
        note=lambda where, message, kind: collected.append(message),
        where="dive/0",
    )


# -- notes ----------------------------------------------------------------------------


def test_a_note_cannot_be_made_without_a_kind() -> None:
    """The kind is what a reader groups the report by, so nobody may leave it to a default."""
    with pytest.raises(TypeError):
        Note("dive/0", "something")  # type: ignore[call-arg]


def test_the_kinds_are_the_four_the_report_distinguishes() -> None:
    """Pinned, because the set is a contract a report renderer and a port both read.

    `inferred` and `resolved` are the pair that has to stay apart: one is a value this
    converter computed, the other a value the source recorded at a scale the converter
    decided, and only the first is a derivation §5.4 asks a writer to label.
    """
    assert NOTE_KINDS == ("absent", "inferred", "resolved", "dropped")


def test_notes_group_by_kind_as_well_as_message() -> None:
    """A message raised as two kinds is two findings; a caller renders them differently."""
    conversion = _conversion(
        Note("dive/0", "a habit of the whole file", "absent"),
        Note("dive/1", "a habit of the whole file", "absent"),
        Note("dive/2", "a habit of the whole file", "dropped"),
    )
    groups = conversion.grouped()
    assert [(group.kind, group.wheres) for group in groups] == [
        ("absent", ["dive/0", "dive/1"]),
        ("dropped", ["dive/2"]),
    ]
    assert groups[0].message == "a habit of the whole file"


def test_groups_keep_the_order_they_were_first_seen_in() -> None:
    conversion = _conversion(
        Note("$", "second", "dropped"),
        Note("dive/0", "first", "absent"),
        Note("dive/1", "second", "dropped"),
    )
    assert [group.message for group in conversion.grouped()] == ["second", "first"]


# -- the inferred list ----------------------------------------------------------------


def test_an_empty_inferred_list_is_not_written_at_all() -> None:
    """What lets a reader land without regenerating a single expected document.

    A conversion that computed nothing has to produce exactly the bytes it produced before
    this member existed, or every pair in the corpus would have to be regenerated to gain
    an empty list saying so.
    """
    provenance: dict[str, object] = {"converted_from": "uddf"}
    record_inferred(provenance, [])
    assert provenance == {"converted_from": "uddf"}


def test_inferred_members_are_listed_when_there_are_any() -> None:
    provenance: dict[str, object] = {"converted_from": "fit"}
    record_inferred(provenance, ["dives/0/max_depth"])
    assert provenance[INFERRED] == ["dives/0/max_depth"]


# -- identity scope -------------------------------------------------------------------


def test_a_bare_document_has_no_member_prefix() -> None:
    scope = Scope()
    assert scope.where("dive/0") == "dive/0"
    assert scope.positional(3) == "#3"


def test_an_archive_member_prefixes_its_paths_and_its_positions() -> None:
    """Both halves, and the second is the one that keeps two files' records apart."""
    scope = Scope(member="2026-04-17.uddf")
    assert scope.where("dive/0") == "2026-04-17.uddf/dive/0"
    assert scope.positional(3) == "2026-04-17.uddf#3"


def test_members_of_one_archive_share_what_has_been_claimed() -> None:
    """And the claim says which member made it, which is what separates the two cases.

    A record another *member* already carries is one record defined twice; a record this
    same file already named is a source defect. The member is the only thing that tells
    them apart.
    """
    shared: Claimed = {}
    first = Scope(member="a.uddf", claimed=shared)
    second = Scope(member="b.uddf", claimed=shared)
    first.claimed["1b85a949-d5f7-5d67-9d04-dcc78342f907"] = ("a.uddf", "a.uddf/site/0")
    assert second.claimed["1b85a949-d5f7-5d67-9d04-dcc78342f907"][0] != second.member


# -- the document header --------------------------------------------------------------


def test_the_header_opens_with_the_two_members_section_4_asks_for_first() -> None:
    written = header(datetime(2026, 9, 5, tzinfo=timezone.utc))
    assert list(written)[:2] == ["format", "version"]
    assert written["exported_at"] == "2026-09-05T00:00:00+00:00"
    assert written["generator"]["name"] == "divejson convert"


# -- which way a zero reads -----------------------------------------------------------


@pytest.mark.parametrize(
    ("record", "member", "answer"),
    [
        # `exclusiveMinimum: 0` — a writer with nothing to say had to put a zero somewhere.
        ("dive", "max_depth", False),
        ("dive", "avg_depth", False),
        ("dive", "duration", False),
        ("cylinder", "volume", False),
        ("cylinder", "start_pressure", False),
        # `minimum: 0` — the diver can have recorded exactly this.
        ("dive", "weight", True),
        ("dive", "visibility", True),
        ("dive", "altitude", True),
        ("cylinder", "end_pressure", True),
        ("cylinder", "oxygen", True),
        # No floor at all: a zero is freezing air, which the diver can have recorded too.
        ("dive", "air_temperature", True),
        # A floor above zero: a zero is below it, so it is no more an answer than a
        # placeholder is.
        ("recording", "surface_pressure", False),
        # A readout's clock starts at zero, which a first dive of the day reads.
        ("recording", "cns_start", True),
        ("recording", "otu_end", True),
    ],
)
def test_a_zero_reads_the_way_the_schema_constrains_the_member(record: str, member: str, answer: bool) -> None:
    """The pair that matters most is `max_depth` and `weight`, which look identical in a
    source and are opposite here: one is UDDF's mandatory element with nothing to put in
    it, the other is a diver saying they wore no lead."""
    assert zero_is_an_answer(record, member) is answer


def test_a_member_the_schema_does_not_have_raises_rather_than_guessing() -> None:
    """The guarantee: no adapter can put a source zero into a member nothing constrains."""
    with pytest.raises(KeyError):
        zero_is_an_answer("dive", "invented_member")
    with pytest.raises(KeyError):
        zero_is_an_answer("submarine", "max_depth")


@pytest.mark.parametrize(
    ("value", "member", "carried"),
    [
        (Decimal("18.4"), "max_depth", True),
        (Decimal(0), "max_depth", False),
        (Decimal("-1"), "max_depth", False),
        (Decimal(0), "weight", True),
        (Decimal("-1"), "weight", False),
        (None, "weight", False),
    ],
)
def test_recorded_takes_the_members_floor_and_nothing_else(value, member: str, carried: bool) -> None:
    assert recorded(value, record="dive", member=member) is carried


# -- a channel's floor, and §6.4's member order ----------------------------------------


@pytest.mark.parametrize("channel", ["ndl", "tts", "ppo2", "cns", "gradient_factor", "surface_gradient_factor"])
def test_the_decompression_readouts_floor_at_zero(channel: str) -> None:
    """None of those quantities has a negative reading, so a source that writes one is
    spelling *no figure* in the only space it had."""
    assert channel_floor(channel) == 0


@pytest.mark.parametrize("channel", ["depth", "ceiling", "temperature", "pressures"])
def test_the_signed_channels_floor_at_nothing(channel: str) -> None:
    """A ceiling and a temperature are both legitimately negative, and §6.3 bounds a
    cylinder pressure in the reader that reads it rather than in the series definition."""
    assert channel_floor(channel) is None


def test_the_profile_member_order_is_the_schemas() -> None:
    """A converted profile reads down §6.4, and the order is the schema's to say — so
    `pressures` keeps its place between `temperature` and the readouts."""
    assert profile_members()[:6] == ("duration", "depth", "ceiling", "temperature", "pressures", "ndl")
    assert profile_members()[-2:] == ("events", "extensions")


# -- §6.4c's Deco Model ----------------------------------------------------------------


def test_a_model_with_nothing_in_it_is_not_written_at_all() -> None:
    """§6.4b's rule applied to §6.4c: an object with no members is absence, not an object."""
    assert _built({}) is None
    assert _built({"algorithm": None, "name": None, "conservatism": None}) is None


def test_the_members_come_out_in_the_sections_order() -> None:
    built = _built({"conservatism": -1, "gf_high": 85, "gf_low": 30, "name": "ZHL-16C", "algorithm": "buhlmann"})
    assert list(built) == ["algorithm", "name", "gf_low", "gf_high", "conservatism"]


def test_a_name_is_trimmed_and_an_empty_one_is_absence() -> None:
    assert _built({"name": "  Suunto Fused2 RGBM  "}) == {"name": "Suunto Fused2 RGBM"}
    assert _built({"name": "   "}) is None


def test_a_name_past_the_sections_cap_is_cut_and_reported() -> None:
    """§6.4c caps it at 64: this is a product string a manufacturer chose, not free text."""
    notes: list[str] = []
    built = _built({"name": "M" * 100}, notes)
    assert len(built["name"]) == MAX_MODEL_NAME
    assert any("the format caps it at 64" in message for message in notes)


@pytest.mark.parametrize("value", [-1, 101, 1000])
def test_a_gradient_factor_outside_the_schemas_range_takes_its_partner_with_it(value: int) -> None:
    """The pair is `dependentRequired` both ways, so half of it is a document this package's
    own validation would reject."""
    notes: list[str] = []
    assert _built({"gf_low": 30, "gf_high": value}, notes) is None
    assert any("whole percent from 0 to 100" in message for message in notes)
    assert any("both or neither" in message for message in notes)


def test_a_conservatism_has_no_floor_and_a_zero_is_a_setting() -> None:
    """Suunto's scale runs P−2 to P2, so this is the one member here where a negative is a
    reading — and §6.4c is what says so, by putting no `minimum` on it."""
    assert _built({"conservatism": -2}) == {"conservatism": -2}
    assert _built({"conservatism": 0}) == {"conservatism": 0}


def test_a_boolean_is_not_an_integer_here() -> None:
    """`True` is an `int` in Python and is not a gradient factor anywhere."""
    assert _built({"gf_low": True, "gf_high": True, "conservatism": False}) is None


# -- §6.4a's Recording -----------------------------------------------------------------


def test_a_setting_alone_does_not_make_a_recording() -> None:
    """§3's rule 4 names `device`, `profile`, `source_files` and a readout, and none of the
    three settings is one of them — a recording built from a mode alone would emit
    `recordings: [{}]`'s conforming twin and describe no record of a dive at all."""
    assert recording(mode="gauge", deco_model={"conservatism": 0}, salinity="en13319") is None
    assert recording(mode="gauge", device={"model": "Perdix 3"}) == {
        "device": {"model": "Perdix 3"},
        "mode": "gauge",
    }


def test_a_readout_alone_makes_a_recording_and_a_zero_one_counts() -> None:
    """The CNS figure a diver copied off their computer is a record of the dive (§6.4a), and
    the zero a first dive of the day starts on is a figure, not an absence."""
    assert recording(readouts={"cns_end": 0.0}) == {"cns_end": 0.0}
    assert recording(salinity="salt", readouts={"surface_pressure": 1.013}) == {
        "salinity": "salt",
        "surface_pressure": 1.013,
    }


def test_a_member_that_is_not_a_readout_is_refused_as_one() -> None:
    with pytest.raises(KeyError):
        recording(readouts={"water_type": 1.0})


def test_the_recordings_members_come_out_in_the_sections_order() -> None:
    built = recording(
        profile={"duration": 60},
        source_files=[{"uuid": "0198a6f0-2222-7120-8000-000000000120"}],
        readouts={"otu_end": 22.0, "surface_pressure": 1.012, "cns_start": 0.0},
        started_at="2026-04-17T11:49:23+02:00",
        salinity="en13319",
        deco_model={"conservatism": 0},
        mode="open_circuit",
        device={"model": "Perdix 3"},
    )
    assert list(built) == [
        "device",
        "mode",
        "deco_model",
        "salinity",
        "started_at",
        "surface_pressure",
        "cns_start",
        "otu_end",
        "source_files",
        "profile",
    ]


# -- a readout the source states on the dive -----------------------------------------------


def _collected() -> tuple[list[tuple[str, str, str]], Any]:
    notes: list[tuple[str, str, str]] = []
    return notes, lambda where, message, kind: notes.append((where, message, kind))


def test_a_dive_level_readout_joins_the_primary_recording_in_its_place() -> None:
    notes, note = _collected()
    recordings = [{"device": {"model": "Perdix 3"}, "profile": {"duration": 60}}]
    onto_primary(recordings, {"cns_end": 11.0}, note=note, where="dive/0", stated="<dive cns>")
    assert list(recordings[0]) == ["device", "cns_end", "profile"]
    assert notes == []


def test_on_a_dive_with_two_recordings_it_is_the_primarys_and_says_so() -> None:
    """The file states it once and does not say which computer computed it, so where it goes
    is a reading of its meaning — `resolved`, which is what `docs/converting.md` asks."""
    notes, note = _collected()
    recordings = [{"device": {"model": "A"}}, {"device": {"model": "B"}}]
    onto_primary(recordings, {"otu_end": 31.0}, note=note, where="dive/0", stated="<dive otu>")
    assert recordings == [{"device": {"model": "A"}, "otu_end": 31.0}, {"device": {"model": "B"}}]
    assert [(where, kind) for where, _, kind in notes] == [("dive/0", "resolved")]
    assert "the dive states <dive otu> once and has 2 recordings" in notes[0][1]


def test_on_a_dive_with_no_recording_it_is_a_recording_of_its_own() -> None:
    notes, note = _collected()
    recordings: list[dict[str, Any]] = []
    onto_primary(recordings, {"cns_end": 11.0, "otu_end": 31.0}, note=note, where="dive/0", stated="x")
    assert recordings == [{"cns_end": 11.0, "otu_end": 31.0}]
    assert notes == []


def test_no_readout_changes_nothing() -> None:
    notes, note = _collected()
    recordings: list[dict[str, Any]] = []
    onto_primary(recordings, {}, note=note, where="dive/0", stated="x")
    assert recordings == [] and notes == []


# -- the millisecond axis ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        ("0.16", 160),
        ("1200.02", 1_200_020),
        ("4300", 4_300_000),
        # Halves away from zero, at the millisecond's grain (`docs/converting.md`).
        ("0.0005", 1),
        ("-0.0005", -1),
        ("0.0004", 0),
    ],
)
def test_a_sources_seconds_are_a_whole_millisecond_on_the_axis(seconds: str, expected: int) -> None:
    assert milliseconds(Decimal(seconds)) == expected


def test_no_time_is_no_place() -> None:
    assert milliseconds(None) is None


@pytest.mark.parametrize(
    ("at", "said"),
    [(0, "0"), (30_000, "30"), (30_400, "30.4"), (1_200_020, "1200.02"), (-250, "-0.25"), (4_000_000, "4000")],
)
def test_the_report_speaks_seconds(at: int, said: str) -> None:
    assert in_seconds(at) == said


def test_an_inverted_gradient_factor_pair_is_dropped_rather_than_reaching_validation() -> None:
    """§3's rule 6, kept here rather than left to the validator.

    `validate_document` does check it, and a converter that let an inverted pair through
    would raise `NonConformingOutputError` and lose the whole file — every dive in it, under
    an archive — over one setting the source got wrong. `docs/converting.md` calls reaching
    a validation failure a bug in the converter rather than a property of the file.

    **Both halves go**, because nothing in a source that states `85/50` says which of the two
    it meant, and choosing would be §5.4's guess.
    """
    notes: list[str] = []
    assert _built({"algorithm": "buhlmann", "gf_low": 85, "gf_high": 50}, notes) == {
        "algorithm": "buhlmann"
    }
    assert any("§3 rule 6" in message for message in notes)


def test_an_equal_pair_is_not_inverted() -> None:
    """The rule is `gf_low <= gf_high`: a diver who dialled 85/85 ran a model."""
    assert _built({"gf_low": 85, "gf_high": 85}) == {"gf_low": 85, "gf_high": 85}


# -- which fix places a side of the dive ---------------------------------------------------

# Where the deepest sample is, half an hour in. Every fix below is placed off it: forward in
# time on the exit side and backward on the entry side, which is the one thing the two sides
# do differently, so every test runs on both.
SPLIT = 1_800_000

SIDES = pytest.mark.parametrize("side", ["exit", "entry"])


def _fixes(side: str, *stated: tuple[str, int | None]) -> list[Fix]:
    """One side's fixes outward from the split, each `(seconds from the split, stated error)`."""
    sign = 1 if side == "exit" else -1
    return [
        Fix(
            at=SPLIT + sign * milliseconds(Decimal(seconds)),
            where=f"dive/0/sample/{index}",
            latitude=Decimal("28.47") + Decimal(index) / 1000,
            longitude=Decimal("34.50"),
            error=None if error is None else Decimal(error),
        )
        for index, (seconds, error) in enumerate(stated)
    ]


def _chosen(side: str, *stated: tuple[str, int | None]) -> tuple[int | None, list[tuple[str, str, str]]]:
    """Which of the fixes is taken, by its place outward from the split, and the report."""
    notes, note = _collected()
    fixes = _fixes(side, *stated)
    taken = chosen_fix(fixes, side=side, note=note)
    return (None if taken is None else fixes.index(taken)), notes


@SIDES
def test_a_vouched_fix_inside_the_window_replaces_a_poor_nearest_one(side: str) -> None:
    """The receiver's first fix on surfacing is before it settled, and it says so.

    The dive this rule was measured on: 47 m of error, then 31 m three seconds on, and 9 m
    six seconds after that, 79 m from the first. The fix taken is the 9 m one, and the report
    says the converter decided it, at the fix it took.
    """
    taken, notes = _chosen(side, ("0", 47), ("3", 31), ("9", 9))
    assert taken == 2
    assert [(where, kind) for where, _, kind in notes] == [("dive/0/sample/2", "resolved")]
    assert f"the dive's {side}" in notes[0][1]


@SIDES
def test_the_nearest_vouched_fix_is_taken_and_not_the_tightest(side: str) -> None:
    """Nearest the split among the vouched, not lowest error: a tighter fix later on is the
    diver further along the swim."""
    taken, notes = _chosen(side, ("0", 20), ("4", 9), ("12", 3))
    assert taken == 1 and [where for where, _, _ in notes] == ["dive/0/sample/1"]
    assert _chosen(side, ("0", 6), ("3", 2)) == (0, [])


@SIDES
def test_with_nothing_vouched_inside_the_window_the_nearest_stands_and_says_nothing(side: str) -> None:
    """However poor it is: a fix is replaced only by one the receiver vouches for, and the
    lower of two estimates above the bound is not one."""
    assert _chosen(side, ("0", 28), ("6", 12), ("20", 15), ("39", 11)) == (0, [])


@SIDES
def test_a_vouched_fix_past_the_window_does_not_reach_back(side: str) -> None:
    """A lone fix, then nothing for longer than the window: the fix logged stands, because by
    the next one the diver may be somewhere else."""
    assert _chosen(side, ("0", 46), ("41", 8)) == (0, [])


@SIDES
def test_the_window_is_measured_from_the_nearest_fix_and_not_from_the_last(side: str) -> None:
    """Thirty seconds between fixes, sixty from the first: a window counted fix to fix would
    walk along the swim for as long as the receiver kept logging."""
    assert _chosen(side, ("0", 30), ("30", 20), ("60", 5)) == (0, [])


@SIDES
@pytest.mark.parametrize(
    ("stated", "taken"),
    [
        ((("0", 20), ("5", 10)), 1),
        ((("0", 20), ("40", 5)), 1),
        ((("0", 20), ("40", 10)), 1),
        ((("0", 20), ("40.001", 5)), 0),
        ((("0", 20), ("5", 11)), 0),
    ],
)
def test_both_bounds_are_inclusive(side: str, stated: tuple, taken: int) -> None:
    assert _chosen(side, *stated)[0] == taken


@SIDES
def test_where_no_fix_states_an_error_the_nearest_is_taken(side: str) -> None:
    """The rule has nothing to read, and a blind delay would be an invention."""
    assert _chosen(side, ("0", None), ("5", None), ("30", None)) == (0, [])


@SIDES
def test_a_fix_stating_no_error_gives_way_to_a_vouched_one_inside_the_window(side: str) -> None:
    """Suunto's route origin is the nearest fix stating none, and stands unless a fix the
    receiver vouched for is inside its window."""
    taken, notes = _chosen(side, ("0", None), ("7", 9))
    assert taken == 1
    assert [(where, kind) for where, _, kind in notes] == [("dive/0/sample/1", "resolved")]


@SIDES
@pytest.mark.parametrize("error", [47, 6, None])
def test_one_fix_is_that_fix(side: str, error: int | None) -> None:
    assert _chosen(side, ("0", error)) == (0, [])


def test_no_fixes_is_no_fix() -> None:
    notes, note = _collected()
    assert chosen_fix([], side="exit", note=note) is None
    assert notes == []


@SIDES
def test_an_error_below_zero_vouches_for_nothing(side: str) -> None:
    """An estimate is a distance, and a negative one is a device spelling something else."""
    assert _chosen(side, ("0", 20), ("5", -1)) == (0, [])


@SIDES
def test_the_finding_is_one_sentence_whatever_the_file(side: str) -> None:
    """A logbook's report groups a finding by its message, so two dives that moved their fix
    differently still read as one line with two places."""
    _, first = _chosen(side, ("0", 47), ("9", 9))
    _, second = _chosen(side, ("0", 13), ("2", 12), ("6", 10))
    assert [message for _, message, _ in first] == [message for _, message, _ in second]


# -- a dive's time in the water --------------------------------------------------------


def test_the_threshold_is_one_point_two_metres_and_strictly_deeper() -> None:
    """The interval after a sample at exactly 1.2 m does not count; one after 1.21 m does."""
    assert IN_WATER_DEPTH == Decimal("1.2")
    assert in_water([(0, Decimal("1.2")), (10_000, Decimal("5"))]) is None
    derived = in_water([(0, Decimal("1.21")), (10_000, Decimal("5"))])
    assert derived is not None
    assert (derived.duration, derived.avg_depth) == (10, Decimal("3.11"))


def test_a_surface_interval_in_the_middle_and_the_tail_are_not_counted() -> None:
    """Two descents with a surfacing between them, and the end-of-dive delay after the last.

    Down for 70 s, up at 0.5 m for 120 s, down for 40 s, then 300 s at the surface: 110 s in
    the water, and the mean is weighted by those 110 s alone — (60 × 7.5 + 10 × 5.25 + 20 ×
    5.5 + 20 × 4.25) / 110 is 6.34, each counted interval taking the mean of both its ends.
    """
    depths = [(0, 5), (60_000, 10), (70_000, 0.5), (190_000, 3), (210_000, 8), (230_000, 0.5), (530_000, 0)]
    derived = in_water(depths)
    assert derived is not None
    assert derived.duration == 110
    assert derived.avg_depth == Decimal("6.34")


def test_no_sample_deeper_than_the_threshold_is_nothing() -> None:
    assert in_water([(0, Decimal("0.4")), (60_000, Decimal("1.2")), (120_000, Decimal("0"))]) is None
    assert in_water([]) is None


def test_the_last_sample_starts_no_interval() -> None:
    """A recording that ends at depth counts to its last sample and no further."""
    assert in_water([(0, Decimal("30"))]) is None
    derived = in_water([(0, Decimal("30")), (5_000, Decimal("30"))])
    assert derived is not None
    assert derived.duration == 5


def test_both_figures_round_halves_away_from_zero() -> None:
    """2.5 s is 3 s, and a mean of 2.125 m is 2.13 m — never Python's half-to-even 2 and 2.12."""
    derived = in_water([(0, Decimal("2")), (2_500, Decimal("2.25"))])
    assert derived is not None
    assert (derived.duration, derived.avg_depth) == (3, Decimal("2.13"))


def test_a_float_depth_reads_as_the_decimal_it_prints_as() -> None:
    """An application holding a profile in floats gets the threshold the reader applies."""
    assert in_water([(0, 1.2), (10_000, 5.0)]) is None
    derived = in_water([(0, 10.1), (10_000, 10.2)])
    assert derived is not None
    assert derived.avg_depth == Decimal("10.15")


def test_a_float_subclass_reads_as_the_number_it_prints() -> None:
    """NumPy's `float64` is a `float` whose `repr` names its type."""

    class Float64(float):
        def __repr__(self) -> str:
            return f"np.float64({float(self)})"

    derived = in_water([(0, Float64(10.1)), (10_000, Float64(10.2))])
    assert derived is not None
    assert derived.avg_depth == Decimal("10.15")
    assert in_water([(0, Float64(1.2)), (10_000, Float64(5.0))]) is None


def test_samples_out_of_time_order_are_refused() -> None:
    with pytest.raises(ValueError, match="time order"):
        in_water([(10_000, Decimal("5")), (0, Decimal("5"))])


def test_the_derivation_is_exported_from_the_package_root() -> None:
    """An application applies the same rule to samples it holds, rather than a second copy of it."""
    assert divejson.in_water is in_water
    assert divejson.IN_WATER_DEPTH == IN_WATER_DEPTH
    assert {"in_water", "InWater", "IN_WATER_DEPTH"} <= set(divejson.__all__)
