"""The measurement table, each conversion against a hand-computed expectation.

This is the highest-risk code in the converter and the only part of it nothing downstream
can catch. `divejson validate` accepts any integer as a profile sample, so a depth channel
scaled by 10 instead of 100 produces a document that passes every check and describes a
dive nobody took. Every expectation below is worked out in a comment rather than read back
off the code, which is the only version of this test that can fail when the code is wrong.

The scales, from spec §5.1: profile depth in **centimetres**, profile temperature in
**tenths of a degree Celsius**, and every scalar outside a profile channel in the base unit
— metres, °C, bar, litres, percent. **The channel conversions carry a scale the scalar ones
do not**, which is the trap this file exists for.

Subsurface's side is easier than UDDF's and harder in one place. Easier because every
measurement carries its unit in the text — metres, litres, bar, Celsius, percent — so
there is no fraction-or-percent and no litres-or-cubic-metres to settle: nothing here is a
`resolved` finding. Harder because a `min` value is a `M:SS` clock rather than a number,
and because a unit the table does not carry has to be **refused** rather than read at the
metric scale, which is what the last group below is about.
"""

from __future__ import annotations

import pytest
from helpers import FIXTURES, one_ssrf_computer, one_ssrf_dive, profile_of

from divejson import convert


def one(attributes: str = "", body: str = "") -> dict:
    return convert(one_ssrf_dive(attributes, body)).document["dives"][0]


def profile(samples: str) -> dict:
    return profile_of(convert(one_ssrf_computer(samples)).document["dives"][0])


def cylinder(attributes: str) -> dict:
    return one(body=f"<cylinder {attributes} />")["cylinders"][0]


def summary(body: str) -> dict:
    return convert(one_ssrf_computer(body)).document["dives"][0]


# -- the channels, which carry a scale ------------------------------------------------


@pytest.mark.parametrize(
    ("metres", "centimetres"),
    [
        ("1.45", 145),  # 1.45 m x 100
        ("2.6", 260),  # 2.6 m x 100 - the float route reaches 260.00000000000003
        ("45.91", 4591),  # 45.91 m x 100
        ("0.0", 0),  # the surface is a reading, not an absence
        ("9.768", 977),  # 976.8 rounds to 977, halves away from zero
        ("0.005", 1),  # 0.5 cm rounds up rather than to even
    ],
)
def test_depth_samples_are_centimetres(metres: str, centimetres: int) -> None:
    found = profile(f"<sample time='0:00 min' depth='{metres} m' />")
    assert found["depth"]["values"] == [centimetres]


@pytest.mark.parametrize(
    ("celsius", "tenths"),
    [
        ("24.4", 244),  # 24.4 C x 10 - Celsius already, unlike UDDF's Kelvin
        ("24.0", 240),
        ("22.0", 220),
        ("0.0", 0),  # freezing
        ("-2.0", -20),  # an under-ice dive is a negative channel value
    ],
)
def test_temperature_samples_are_tenths_of_a_degree(celsius: str, tenths: int) -> None:
    found = profile(f"<sample time='0:00 min' depth='1.0 m' temp='{celsius} C' />")
    assert found["temperature"]["values"] == [tenths]


def test_the_scalar_temperature_takes_no_tenths_scale() -> None:
    """The pair this file exists to keep apart: one channel, one scalar, one factor apart."""
    assert summary("<temperature water='22.4 C' />")["bottom_temperature"] == 22.4


# -- the clock ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("written", "seconds"),
    [
        ("0:00", 0),
        ("0:10", 10),
        ("1:20", 80),  # 1 x 60 + 20
        ("66:50", 4010),  # 66 x 60 + 50 - the first dive of the reference export
        ("71:40", 4300),  # its profile span, which is longer than its logged duration
        ("120:00", 7200),  # the minutes are unbounded: Subsurface writes seconds / 60
    ],
)
def test_a_clock_is_minutes_and_seconds(written: str, seconds: int) -> None:
    assert one(f"duration='{written} min'").get("duration", 0) == seconds


@pytest.mark.parametrize("written", ["66", "66:5", "1:60", "66:50:00", "1.5", "66m50s"])
def test_something_that_is_not_a_clock_is_refused_rather_than_read_as_minutes(written: str) -> None:
    """A bare `66` is the tempting one, and reading it would be a factor-of-60 guess."""
    conversion = convert(one_ssrf_dive(f"duration='{written} min'"))
    assert "duration" not in conversion.document["dives"][0]
    assert any(note.kind == "dropped" and "<dive duration>" in note.message for note in conversion.notes)


# -- the scalars, which take the base unit --------------------------------------------


def test_depths_are_plain_metres() -> None:
    found = summary("<depth max='45.91 m' mean='20.841 m' />")
    assert found["max_depth"] == 45.91
    assert found["avg_depth"] == 20.841


def test_cylinder_pressures_are_plain_bar() -> None:
    """No tenths, unlike a pressure channel would carry."""
    found = cylinder("start='200.0 bar' end='80.0 bar'")
    assert found["start_pressure"] == 200.0
    assert found["end_pressure"] == 80.0


def test_a_cylinder_size_is_plain_litres() -> None:
    """`size='12.0 l'` is twelve litres and says so, which is UDDF's whole ambiguity gone."""
    assert cylinder("size='12.0 l'")["volume"] == 12.0


def test_a_gas_is_a_percentage_and_says_so() -> None:
    """`o2='32.0%'` cannot be the fraction UDDF's `<o2>` might be: the `%` is in the text."""
    found = cylinder("o2='32.0%' he='15.0%'")
    assert found["oxygen"] == 32.0
    assert found["helium"] == 15.0


def test_the_oxygen_clock_is_a_percentage_and_the_oxygen_dose_is_a_count() -> None:
    """`cns='11%'` and `otu='31'`: the two members whose units differ from each other."""
    found = one("cns='11%' otu='31'")
    assert found["cns_end"] == 11.0
    assert found["otu_end"] == 31.0


# -- a unit the table does not carry --------------------------------------------------


@pytest.mark.parametrize(
    ("attribute", "written"),
    [
        ("duration", "41:20 s"),
        ("cns", "11"),  # the percent sign is not optional
        ("otu", "31 units"),
    ],
)
def test_a_unit_the_table_does_not_carry_is_dropped_rather_than_guessed(attribute: str, written: str) -> None:
    """Refusing costs one member; guessing costs the dive.

    An imperial export is the case that makes this concrete — a `'150.6 ft'` read at the
    metric scale puts a recreational dive at 150 metres — and no file in this repository's
    hand carries one, so there is no factor here that anything has checked.
    """
    conversion = convert(one_ssrf_dive(f"{attribute}='{written}'"))
    assert set(conversion.document["dives"][0]) == {"uuid", "started_at"}
    assert any("where this reader reads it in" in note.message for note in conversion.notes)


def test_a_whole_file_in_one_unrecognised_unit_reports_one_line_per_member() -> None:
    """The report groups on the message, and a message carrying the value would not group.

    A file written in feet produces one finding per depth sample — 431 of them on the
    export this reader was built against — and a diver looking for the interesting ones
    should not have to read past four hundred that say the same thing.
    """
    samples = "".join(f"<sample time='0:{second:02d} min' depth='{second}.0 ft' />" for second in range(10))
    conversion = convert(one_ssrf_computer(samples))
    groups = [group for group in conversion.grouped() if "<sample depth>" in group.message]
    assert len(groups) == 1
    assert len(groups[0].wheres) == 10


def test_the_reference_export_converts_to_the_same_channels_the_uddf_reader_produces() -> None:
    """The known answer, and the strongest one this repository can state.

    `fixtures/ssrf/subsurface.ssrf` and `fixtures/uddf/subsurface.uddf` are the same two
    dives of one Subsurface logbook, reduced the same way from the two files Subsurface
    exported. The depth and temperature channels below are **equal, sample for sample**,
    through two entirely different unit paths: metres and Celsius written in the text here,
    metres and Kelvin in elements there. A wrong factor on either side moves one of them.
    """
    ssrf = profile_of(convert((FIXTURES / "ssrf" / "subsurface.ssrf").read_bytes()).document["dives"][0])
    uddf = profile_of(convert((FIXTURES / "uddf" / "subsurface.uddf").read_bytes()).document["dives"][0])
    assert ssrf["depth"]["times"] == uddf["depth"]["times"] == [0, 10, 20, 30, 40, 80, 170, 4300]
    assert ssrf["depth"]["values"] == uddf["depth"]["values"] == [145, 183, 222, 257, 260, 332, 911, 0]
    assert ssrf["temperature"]["times"] == uddf["temperature"]["times"] == [30, 80]
    assert ssrf["temperature"]["values"] == uddf["temperature"]["values"] == [244, 240]
    assert ssrf["duration"] == uddf["duration"] == 4300
