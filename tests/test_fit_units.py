"""The arithmetic, where a wrong factor validates perfectly and describes a dive nobody took.

FIT states its units in the profile rather than in the file, so this reader has almost no
unit table — and exactly one place where a number can be read at the wrong scale anyway:
the developer field a vendor declares under a profile field's own name. That is the first
section below, and it is the whole reason `_native` exists.

The rest is the scaling §6.5 fixes — centimetres of depth, tenths of a degree, tenths of a
bar — and the one conversion FIT does not scale for you, the semicircle.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fitbuild import DevField, dive_file, fit_file, message
from helpers import device_of, profile_of

from divejson.converter import Scope
from divejson.fit import DEGREES_PER_SEMICIRCLE, FIT, _Converter, _native, _Scan

EXPORTED_AT = datetime(2026, 9, 5, tzinfo=timezone.utc)
STARTED_AT = datetime(2026, 4, 17, 9, 49, 23, tzinfo=timezone.utc)


def _dive(data: bytes) -> dict:
    return FIT.convert(data, exported_at=EXPORTED_AT, scope=Scope()).document["dives"][0]


def _conversion(data: bytes):
    return FIT.convert(data, exported_at=EXPORTED_AT, scope=Scope())


def _records(*samples: tuple[int, dict]) -> tuple:
    """`record` messages at `n` seconds past the start, carrying whatever is passed."""
    return tuple(
        message("record", timestamp=STARTED_AT + timedelta(seconds=offset), **values)
        for offset, values in samples
    )


# -- the developer-field trap ---------------------------------------------------------


def test_a_developer_field_does_not_shadow_the_native_one_beside_it() -> None:
    """Both present: the native `uint32` scaled by 1000 wins, exactly as recorded.

    This is the shape every Suunto file in this project's hand has. Walking `frame.fields`
    into a dict keyed by name keeps whichever came last, and the developer duplicate is
    written last — so the naive reader produces 45.90999984741211 for a dive the device
    recorded at 45.91.
    """
    data = dive_file(session={"max_depth": 45.91})
    # The encoder writes the developer field after the native one, as the exporter does.
    with_duplicate = fit_file(
        message("file_id", type="activity", manufacturer="suunto"),
        message(
            "session",
            DevField("max_depth", 45.90999984741211, units="m"),
            sport="diving",
            start_time=STARTED_AT,
            total_elapsed_time=4301.72,
            max_depth=45.91,
        ),
    )
    assert _dive(data)["max_depth"] == 45.91
    assert _dive(with_duplicate)["max_depth"] == 45.91


def test_a_developer_field_alone_reads_as_not_recorded() -> None:
    """The half the filter exists for, and the half a positional `get_value` gets wrong.

    `fitdecode`'s own `get_value` returns the native field where both are present, but only
    as a side effect of taking the first match by position — and hands back the developer
    field's value where it is the only one, its units and its semantics included. A reader
    leaning on that would pass a vendor's float off as the profile's scaled integer.

    Read as not recorded, the value falls through to the samples and is `inferred`, which is
    a true statement about where the number came from. Passing the float through would have
    been a false one.
    """
    data = fit_file(
        message("file_id", type="activity", manufacturer="suunto"),
        *_records((0, {"depth": 10.0}), (60, {"depth": 30.5})),
        message(
            "session",
            DevField("max_depth", 30.499999, units="m"),
            sport="diving",
            start_time=STARTED_AT,
            total_elapsed_time=60.0,
        ),
    )
    conversion = _conversion(data)
    assert conversion.document["dives"][0]["max_depth"] == 30.5
    assert "dives/0/max_depth" in conversion.document["extensions"]["divejson"]["inferred"]


def test_a_developer_field_under_a_name_no_profile_field_has_is_ignored() -> None:
    """`dive_number_in_series`, `surface_time`, `feeling` — every Suunto file carries some.

    None of them is a member of this format, and none of them may become one by accident:
    a developer field is the vendor's own extension and this reader maps the profile.
    """
    data = fit_file(
        message("file_id", type="activity", manufacturer="suunto"),
        message(
            "session",
            DevField("surface_time", 70676.5, units="s", field_number=1),
            DevField("feeling", 4.0, field_number=2),
            sport="diving",
            start_time=STARTED_AT,
            total_elapsed_time=4301.72,
            max_depth=45.91,
        ),
    )
    dive = _dive(data)
    assert dive["max_depth"] == 45.91
    assert set(dive) <= {"uuid", "started_at", "duration", "max_depth", "avg_depth", "recordings"}
    # `recordings` is there because the `file_id` names a manufacturer, which is §6.4b's
    # `brand`. What it must not carry is a counter off `dive_number_in_series`: that is a
    # developer field, and §6.4b's `dive_number` comes from the native `session.dive_number`.
    assert device_of(dive) == {"brand": "suunto"}


# -- the §6.5 channel scales ----------------------------------------------------------


@pytest.mark.parametrize(
    ("metres", "centimetres"),
    [(2.6, 260), (0.01, 1), (45.91, 4591), (13.365, 1337)],  # halves away from zero
)
def test_depth_samples_are_centimetres(metres: float, centimetres: int) -> None:
    """Through `Decimal`, so `2.6 * 100` is 260 and not 260.00000000000003."""
    data = dive_file(*_records((0, {"depth": metres})), session={"total_elapsed_time": 60.0})
    assert profile_of(_dive(data))["depth"]["values"] == [centimetres]


def test_temperature_samples_are_tenths_of_a_degree() -> None:
    """FIT's `record.temperature` is a whole-degree `sint8`, so the tenths are this scale."""
    data = dive_file(
        *_records((0, {"depth": 5.0, "temperature": 22}), (30, {"depth": 6.0, "temperature": -1})),
        session={"total_elapsed_time": 60.0},
    )
    assert profile_of(_dive(data))["temperature"]["values"] == [220, -10]


def test_a_ceiling_of_zero_is_not_a_ceiling() -> None:
    """Zero says the diver may surface — the absence of an obligation, not one at 0 m.

    Reading it as a reading draws a flat line along the surface across every no-deco dive
    in a logbook, which is the whole of what a ceiling channel is for.
    """
    data = dive_file(
        *_records(
            (0, {"depth": 5.0, "next_stop_depth": 0.0}),
            (30, {"depth": 30.0, "next_stop_depth": 3.0}),
            (60, {"depth": 6.0, "next_stop_depth": 0.0}),
        ),
        session={"total_elapsed_time": 60.0},
    )
    profile = profile_of(_dive(data))
    assert profile["ceiling"] == {"times": [30], "values": [300]}
    assert len(profile["depth"]["times"]) == 3


# -- semicircles ----------------------------------------------------------------------


def test_a_semicircle_is_180_over_two_to_the_31() -> None:
    """The profile declares no scale for `position_lat`, so the raw count is what arrives."""
    assert DEGREES_PER_SEMICIRCLE == Decimal(180) / Decimal(2**31)
    assert Decimal(2**31) * DEGREES_PER_SEMICIRCLE == Decimal(180)


def test_a_recorded_fix_converts_to_degrees_at_six_places() -> None:
    """Six places is about 11 cm, and is what makes two exports of one dive agree.

    The same fix reaches this package as a semicircle count here and as a radian float from
    the Suunto app's JSON; they agree exactly at six places and disagree below.
    """
    semicircles = round(28.56723 / float(DEGREES_PER_SEMICIRCLE))
    data = dive_file(
        *_records(
            (0, {"depth": 30.0}),
            (60, {"depth": 1.0, "position_lat": semicircles, "position_long": semicircles}),
        ),
        session={"total_elapsed_time": 60.0},
    )
    assert _dive(data)["exit_position"] == {"latitude": 28.56723, "longitude": 28.56723}


def test_the_invalid_position_sentinel_is_not_a_hundred_and_eighty_degrees() -> None:
    """`position_lat` is a `sint32` whose absent-marker is 0x7FFFFFFF.

    The arithmetic on that count gives 179.99999991618097, which rounds to 180.000000 — a
    real, in-range longitude that no range check catches and that would pin every
    position-less dive to the antimeridian. `fitdecode` resolves the sentinel before this
    reader sees it, and this is what says so.
    """
    data = dive_file(
        *_records(
            (0, {"depth": 30.0}),
            (60, {"depth": 1.0, "position_lat": 0x7FFFFFFF, "position_long": 0x7FFFFFFF}),
        ),
        session={"total_elapsed_time": 60.0},
    )
    conversion = _conversion(data)
    dive = conversion.document["dives"][0]
    assert "entry_position" not in dive and "exit_position" not in dive
    # And for the right reason: nothing reached `position` at all. A sentinel that survived
    # decoding would arrive as 180.000000 and be reported out of range for a latitude, which
    # would leave the two assertions above passing on a reader that had got this wrong.
    assert [note for note in conversion.notes if "WGS 84" in note.message] == []
    assert [note for note in conversion.notes if note.kind == "dropped"] == []


# -- the scalars ----------------------------------------------------------------------


@pytest.mark.parametrize(("seconds", "duration"), [(4301.72, 4302), (2.5, 3), (0.4, None), (1.0, 1)])
def test_the_duration_rounds_halves_away_from_zero(seconds: float, duration: int | None) -> None:
    """2.5 seconds of elapsed time is 3, not the 2 Python's own half-to-even `round` gives.

    A value that rounds to zero is not a duration: §6.2's floor is exclusive, so it reads as
    not recorded rather than as a dive of no length.
    """
    data = dive_file(session={"total_elapsed_time": seconds})
    assert _dive(data).get("duration") == duration


def test_the_computed_mean_depth_keeps_two_places() -> None:
    """The last resort, and the only figure in this reader that is its own arithmetic.

    Two places is what a device's own `avg_depth` carries, so a computed one that carried
    twelve would be visibly a different kind of number in the same member.
    """
    data = dive_file(
        *_records((0, {"depth": 10.0}), (30, {"depth": 20.0}), (60, {"depth": 25.0})),
        session={"total_elapsed_time": 60.0, "max_depth": None, "avg_depth": None},
    )
    conversion = _conversion(data)
    assert conversion.document["dives"][0]["avg_depth"] == 18.33
    assert conversion.document["extensions"]["divejson"]["inferred"] == [
        "dives/0/max_depth",
        "dives/0/avg_depth",
    ]


# -- the sub-second fraction ----------------------------------------------------------


class _Field:
    """One decoded field, as `_native_field` needs to see it.

    A stand-in rather than a real message, because the property below cannot be built from
    bytes: FIT's `date_time` is a count of whole seconds, so no encoder can hand this reader
    a fraction to preserve. What can be pinned is that the reader does not *truncate* one,
    and this is the smallest thing that pins it.
    """

    def __init__(self, name: str, value: object) -> None:
        self._name = name
        self.value = value
        self.field = object()  # anything that is not a `DevField`

    def is_named(self, name: str) -> bool:
        return name == self._name


class _Frame:
    def __init__(self, **values: object) -> None:
        self.fields = [_Field(name, value) for name, value in values.items()]


def test_a_recorded_sub_second_fraction_is_carried_rather_than_truncated() -> None:
    """§5.2 makes the fraction OPTIONAL, and dropping one would discard a recorded value.

    No FIT file can carry one today, so this is a guard rather than a mapping: it fails the
    day someone reformats the start time through anything that writes whole seconds.
    """
    converter = _Converter(_Scan(), exported_at=EXPORTED_AT, scope=Scope())
    session = _Frame(start_time=datetime(2026, 4, 17, 9, 49, 23, 510000, tzinfo=timezone.utc))
    assert converter.read_started_at(session, "dive/0") == "2026-04-17T09:49:23.510000+00:00"


def test_a_whole_second_start_time_carries_no_fraction() -> None:
    """The other half: `isoformat` writes the fraction only where there is one to write."""
    converter = _Converter(_Scan(), exported_at=EXPORTED_AT, scope=Scope())
    assert converter.read_started_at(_Frame(start_time=STARTED_AT), "dive/0") == "2026-04-17T09:49:23+00:00"


def test_the_native_reader_takes_the_value_off_a_stand_in_frame() -> None:
    """The two tests above lean on `_native`, so this says what they are leaning on."""
    assert _native(_Frame(max_depth=45.91), "max_depth") == 45.91
    assert _native(_Frame(max_depth=45.91), "avg_depth") is None
    assert _native(None, "max_depth") is None
