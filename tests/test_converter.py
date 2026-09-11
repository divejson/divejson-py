"""The converter policy every adapter inherits: notes, identities, and how a zero reads.

Nothing here is UDDF's. These are the rules a second reader would otherwise re-implement
slightly differently, which is the failure the shared module exists to prevent — a report
whose kinds mean one thing for one format and another for the next, or a `weight` of zero
read as absence because the adapter that wrote it remembered the `max_depth` rule.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from divejson import Conversion, Note
from divejson.converter import (
    INFERRED,
    MAX_MODEL_NAME,
    NOTE_KINDS,
    Claimed,
    Scope,
    channel_floor,
    deco_model,
    header,
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


def test_the_header_opens_with_the_two_members_section_4_requires_first() -> None:
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
        # A floor above zero: a zero is below it, so it is no more an answer than a
        # placeholder is.
        ("dive", "surface_pressure", False),
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


def test_a_mode_or_a_model_alone_does_not_make_a_recording() -> None:
    """§3's rule 4 names `device`, `profile` and `source_files`, and neither of the two
    members §6.4a gained is one of them — a recording built from a mode alone would emit
    `recordings: [{}]`'s conforming twin and describe no record of a dive at all."""
    assert recording(mode="gauge", deco_model={"conservatism": 0}) is None
    assert recording(mode="gauge", device={"model": "Perdix 3"}) == {
        "device": {"model": "Perdix 3"},
        "mode": "gauge",
    }


def test_the_recordings_members_come_out_in_the_sections_order() -> None:
    built = recording(
        profile={"duration": 60},
        source_files=[{"uuid": "0198a6f0-2222-7120-8000-000000000120"}],
        started_at="2026-04-17T11:49:23+02:00",
        deco_model={"conservatism": 0},
        mode="open_circuit",
        device={"model": "Perdix 3"},
    )
    assert list(built) == ["device", "mode", "deco_model", "started_at", "source_files", "profile"]
