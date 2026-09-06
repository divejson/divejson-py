"""The unit conversions, each against a hand-computed expectation.

This is the highest-risk code in the converter and the only part of it nothing downstream
can catch. `divejson validate` accepts any integer as a profile sample, so a depth channel
scaled by 10 instead of 100 produces a document that passes every check and describes a
dive nobody took — and the most-executed conversion in the whole module is exactly that
one. Every expectation below is worked out in a comment rather than read back off the
code, which is the only version of this test that can fail when the code is wrong.

The scales, from spec §5.1: profile depth and ceiling in **centimetres**, profile
temperature in **tenths of a degree Celsius**, profile pressures in **tenths of a bar**,
and every scalar outside a profile channel in the base unit — metres, °C, bar, litres.
UDDF's side, from its own schema and documentation: metres, Kelvin, Pascal, cubic metres
and gas fractions. The channel conversions therefore carry a scale the scalar ones do not,
which is the trap this file exists for.
"""

from __future__ import annotations

import pytest
from helpers import FIXTURES, STARTED_AT, before, one_dive

from divejson import convert


def cylinder(body: str, *, mix: str = "") -> dict:
    document = convert(one_dive(f"{STARTED_AT}<tankdata>{body}</tankdata>", header=mix)).document
    return document["dives"][0]["cylinders"][0]


def profile(samples: str) -> dict:
    document = convert(one_dive(f"{STARTED_AT}<samples>{samples}</samples>")).document
    return document["dives"][0]["profile"]


@pytest.mark.parametrize(
    ("metres", "centimetres"),
    [
        ("1.45", 145),  # 1.45 m x 100
        ("2.6", 260),  # 2.6 m x 100 - the float route reaches 260.00000000000003
        ("45.91", 4591),  # 45.91 m x 100
        ("0", 0),  # the surface is a reading, not an absence
        ("9.109999999999999", 911),  # 910.9999... rounds to 911, and Subsurface writes this
        ("0.005", 1),  # 0.5 cm rounds up, halves away from zero
    ],
)
def test_depth_samples_are_centimetres(metres: str, centimetres: int) -> None:
    assert profile(f"<waypoint><depth>{metres}</depth><divetime>0</divetime></waypoint>")["depth"]["values"] == [
        centimetres
    ]


@pytest.mark.parametrize(
    ("kelvin", "tenths"),
    [
        ("297.55", 244),  # 297.55 - 273.15 = 24.40 C, x 10
        ("297.15", 240),  # 297.15 - 273.15 = 24.00 C, x 10
        ("295.15", 220),  # 295.15 - 273.15 = 22.00 C, x 10
        ("273.15", 0),  # freezing
        ("271.15", -20),  # -2.00 C: an under-ice dive is a negative channel value
    ],
)
def test_temperature_samples_are_tenths_of_a_degree(kelvin: str, tenths: int) -> None:
    samples = f"<waypoint><depth>1</depth><divetime>0</divetime><temperature>{kelvin}</temperature></waypoint>"
    assert profile(samples)["temperature"]["values"] == [tenths]


def test_bottom_temperature_is_plain_celsius() -> None:
    """The scalar member takes no tenths scale, which is the pair this file exists to keep apart."""
    document = convert(
        one_dive(f"{STARTED_AT}<informationafterdive><lowesttemperature>295.15</lowesttemperature></informationafterdive>")
    ).document
    assert document["dives"][0]["bottom_temperature"] == 22.0  # 295.15 - 273.15


def test_tank_pressure_samples_are_tenths_of_a_bar() -> None:
    """21 000 000 Pa / 100 000 = 210 bar, x 10 = 2100 tenths."""
    samples = (
        "<waypoint><depth>1</depth><divetime>0</divetime>"
        '<tankpressure ref="mix-1">21000000</tankpressure></waypoint>'
    )
    body = f'{STARTED_AT}<tankdata><link ref="mix-1"/></tankdata><samples>{samples}</samples>'
    header = '<gasdefinitions><mix id="mix-1"><name>Air</name><o2>0.21</o2></mix></gasdefinitions>'
    document = convert(one_dive(body, header=header)).document
    assert document["dives"][0]["profile"]["pressures"][0]["values"] == [2100]


def test_cylinder_pressures_are_plain_bar() -> None:
    """20 000 000 Pa / 100 000 = 200 bar - no tenths, unlike the channel above."""
    found = cylinder("<tankpressurebegin>20000000</tankpressurebegin><tankpressureend>8000000</tankpressureend>")
    assert found["start_pressure"] == 200.0
    assert found["end_pressure"] == 80.0


def test_surface_pressure_is_plain_bar() -> None:
    """101 300 Pa / 100 000 = 1.013 bar."""
    document = convert(one_dive(before("<surfacepressure>101300</surfacepressure>"))).document
    assert document["dives"][0]["surface_pressure"] == 1.013


@pytest.mark.parametrize(
    ("written", "litres"),
    [
        ("0.012", 12.0),  # the cubic metres UDDF specifies: 0.012 m3 x 1000
        ("0.024", 24.0),  # a manifolded twinset
        ("0.0003", 0.3),  # a 0.3 l argon bottle, still below the threshold
    ],
)
def test_tank_volume_in_cubic_metres_becomes_litres(written: str, litres: float) -> None:
    assert cylinder(f"<tankvolume>{written}</tankvolume>")["volume"] == litres


@pytest.mark.parametrize("written", ["12", "15", "1"])
def test_tank_volume_at_or_above_one_is_already_litres(written: str) -> None:
    """A cubic metre of water capacity is a thousand-litre cylinder; nobody dives one.

    Subsurface's 6.0.x master builds write litres into the field UDDF specifies in cubic
    metres, so both spellings are live in the installed base and both are schema-valid.
    """
    assert cylinder(f"<tankvolume>{written}</tankvolume>")["volume"] == float(written)


def test_reinterpreting_a_tank_volume_is_reported_as_resolved() -> None:
    """`resolved`, not `inferred`: 12 is the source's own number, at the scale it must mean.

    The kind is what carries that distinction to a diver, and it is also what says the
    document owes no `extensions.divejson.inferred` entry for the cylinder — nothing was
    derived, so there is no derivation to label.
    """
    conversion = convert(one_dive(f"{STARTED_AT}<tankdata><tankvolume>12</tankvolume></tankdata>"))
    resolved = [note for note in conversion.notes if "read as 12 litres" in note.message]
    assert [note.kind for note in resolved] == ["resolved"]


@pytest.mark.parametrize(
    ("written", "percent"),
    [
        ("0.21", 21.0),  # the documented fraction
        ("0.32", 32.0),
        ("1", 100.0),  # pure oxygen: a 1 % mix is not a breathing gas
        ("34", 34.0),  # pre-2017 Subsurface wrote whole percent
        ("100", 100.0),
    ],
)
def test_gas_fractions_become_percentages(written: str, percent: float) -> None:
    header = f'<gasdefinitions><mix id="m"><name>Gas</name><o2>{written}</o2></mix></gasdefinitions>'
    assert cylinder('<link ref="m"/>', mix=header)["oxygen"] == percent


def test_reinterpreting_a_gas_fraction_is_reported_as_resolved() -> None:
    """The other scale resolution, and it carries the same kind as the volume one.

    The two are one decision, so a change that moved only one of them off `resolved` would
    leave the report saying two different things about the same reasoning.
    """
    header = '<gasdefinitions><mix id="m"><name>Gas</name><o2>34</o2></mix></gasdefinitions>'
    conversion = convert(one_dive(f'{STARTED_AT}<tankdata><link ref="m"/></tankdata>', header=header))
    resolved = [note for note in conversion.notes if "read as 34 percent" in note.message]
    assert [note.kind for note in resolved] == ["resolved"]


def test_depths_and_temperatures_match_the_reference_export() -> None:
    """The known answer, on a reduced slice of a real Subsurface export.

    `fixtures/uddf/subsurface.uddf` carries eight of the 431 depth samples and two of the
    29 temperatures that a real Subsurface export of the reference implementation's demo
    account holds for that dive. Against `fixtures/valid/demo-logbook.divejson` — the same
    logbook, exported straight to DiveJSON rather than through Subsurface — **all 431 and
    all 29 are equal after these scales**, sample for sample. The eight and two below are
    the head of that comparison, and they are what makes this a known answer rather than a
    fixture agreeing with the code that produced it.
    """
    found = convert((FIXTURES / "uddf" / "subsurface.uddf").read_bytes()).document["dives"][0]["profile"]
    assert found["depth"]["times"] == [0, 10, 20, 30, 40, 80, 170, 4300]
    assert found["depth"]["values"] == [145, 183, 222, 257, 260, 332, 911, 0]
    assert found["temperature"]["times"] == [30, 80]
    assert found["temperature"]["values"] == [244, 240]
