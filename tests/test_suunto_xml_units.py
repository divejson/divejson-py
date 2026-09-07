"""The unit table, each conversion against a hand-computed expectation.

This is the highest-risk code in the converter and the only part of it nothing downstream
can catch. `divejson validate` accepts any integer as a profile sample, so a depth channel
scaled by 10 instead of 100 produces a document that passes every check and describes a
dive nobody took. Every expectation below is worked out in a comment rather than read back
off the code, which is the only version of this test that can fail when the code is wrong.

The scales, from spec §5.1: profile depth in **centimetres**, profile temperature in
**tenths of a degree Celsius**, profile pressure in **tenths of a bar**, and every scalar
outside a profile channel in the base unit — metres, °C, bar, litres, percent. **The
channel conversions carry a scale the scalar ones do not**, which is the trap this file
exists for, and the tank-pressure channel carries *two* of them: millibar to bar, then bar
to tenths.

This format's own hazard is that the same vendor's two exports disagree about three
readings, and each disagreement is invisible inside one file. CNS is whole percent here and
a 0-1 fraction in the app's JSON. Cylinder pressures are millibar here and Pascal there.
And `<SurfacePressure>` is the one pressure in *this* file that is not millibar: read at the
cylinder scale, a barometer at sea level would report a hundred metres of seawater. The last
group below is the cross-format check that would catch any of the three moving.
"""

from __future__ import annotations

import pytest
from helpers import FIXTURES, suunto_mixtures, suunto_xml, suunto_xml_sample, suunto_xml_samples

from divejson import convert


def one(body: str = "") -> dict:
    return convert(suunto_xml(body)).document["dives"][0]


def profile(*samples: str) -> dict:
    return one(suunto_xml_samples(*samples))["profile"]


def cylinder(body: str) -> dict:
    return one(suunto_mixtures(body))["cylinders"][0]


# -- the channels, which carry a scale ------------------------------------------------


@pytest.mark.parametrize(
    ("metres", "centimetres"),
    [
        ("1.86", 186),  # 1.86 m x 100 - the first sample of the reference export
        ("32.41", 3241),  # its greatest depth
        ("2.6", 260),  # 2.6 m x 100 - the float route reaches 260.00000000000003
        ("0", 0),  # the surface is a reading, not an absence
        ("9.768", 977),  # 976.8 rounds to 977, halves away from zero
        ("0.005", 1),  # 0.5 cm rounds up rather than to even
    ],
)
def test_depth_samples_are_centimetres(metres: str, centimetres: int) -> None:
    assert profile(suunto_xml_sample(10, Depth=metres))["depth"]["values"] == [centimetres]


@pytest.mark.parametrize(
    ("celsius", "tenths"),
    [
        # Celsius already, unlike the app JSON's Kelvin and FIT's own integer degrees.
        ("23.7000065", 237),  # 237.000065 rounds to 237 - the float32 noise this device writes
        ("21.9999943", 220),  # 219.999943 rounds to 220, which is what the sensor meant
        ("22", 220),
        ("0", 0),  # freezing
        ("-2", -20),  # an under-ice dive is a negative channel value
    ],
)
def test_temperature_samples_are_tenths_of_a_degree(celsius: str, tenths: int) -> None:
    assert profile(suunto_xml_sample(10, Temperature=celsius))["temperature"]["values"] == [tenths]


@pytest.mark.parametrize(
    ("metres", "centimetres"),
    [
        ("3", 300),  # the shallowest ceiling in the corpus
        ("15.44", 1544),  # the deepest
    ],
)
def test_ceiling_samples_are_centimetres(metres: str, centimetres: int) -> None:
    assert profile(suunto_xml_sample(10, Ceiling=metres))["ceiling"]["values"] == [centimetres]


@pytest.mark.parametrize(
    ("millibar", "tenths_of_a_bar"),
    [
        # Two scales in one conversion, which is why this is the channel most worth pinning:
        # millibar to bar, then §6.5's tenths.
        ("210000", 2100),  # 210 000 mbar = 210.0 bar = 2 100 tenths
        ("219280", 2193),  # the corpus's highest reading: 219.28 bar, 2 192.8 rounds to 2 193
        ("180", 2),  # its lowest: 0.18 bar, 1.8 rounds to 2
        ("144625", 1446),  # 144.625 bar, 1 446.25 rounds to 1 446
    ],
)
def test_tank_pressure_samples_are_tenths_of_a_bar_from_millibar(
    millibar: str, tenths_of_a_bar: int
) -> None:
    found = convert(
        suunto_xml(
            suunto_mixtures("<TransmitterId>2411100050</TransmitterId>")
            + suunto_xml_samples(suunto_xml_sample(10, Pressure=millibar))
        )
    ).document["dives"][0]["profile"]
    assert found["pressures"][0]["values"] == [tenths_of_a_bar]


def test_the_scalar_temperature_takes_no_tenths_scale() -> None:
    """The pair this file exists to keep apart: one channel, one scalar, one factor apart."""
    assert one("<BottomTemperature>22</BottomTemperature>")["bottom_temperature"] == 22.0


# -- the scalars, which take the base unit --------------------------------------------


def test_depths_are_plain_metres() -> None:
    found = one("<MaxDepth>32.41</MaxDepth><AvgDepth>17.73</AvgDepth>")
    assert found["max_depth"] == 32.41
    assert found["avg_depth"] == 17.73


@pytest.mark.parametrize(
    ("millibar", "bar"),
    [
        ("211391", 211.391),  # 211 391 mbar / 1000 - the two-gas dive's back gas
        ("144625", 144.625),  # its end pressure
        ("200000", 200.0),
        ("188", 0.188),  # the corpus's lowest recorded start pressure, carried as it stands
    ],
)
def test_cylinder_pressures_are_millibar(millibar: str, bar: float) -> None:
    """The trap the api's parser was fixed for: read as bar, `211391` is 211 391 bar.

    Carried exactly rather than quantized. `211.391` is the source's own number, and this
    package rounds nothing it did not compute itself.
    """
    found = cylinder(f"<StartPressure>{millibar}</StartPressure><EndPressure>{millibar}</EndPressure>")
    assert found["start_pressure"] == bar
    assert found["end_pressure"] == bar


@pytest.mark.parametrize(
    ("pascal", "bar"),
    [
        ("104900", 1.049),  # the reference dive - the app's JSON writes the same integer
        ("103100", 1.031),  # the corpus's lowest
        ("106700", 1.067),  # its highest
    ],
)
def test_the_surface_pressure_is_pascal_and_nothing_else_here_is(pascal: str, bar: float) -> None:
    """The one pressure in this file that is not millibar.

    Read at the cylinder scale, `104900` would be 104.9 bar — a kilometre of seawater at
    the surface — and §6.2's own 0.4 to 1.2 bound is what catches it either way.
    """
    assert one(f"<SurfacePressure>{pascal}</SurfacePressure>")["surface_pressure"] == bar


def test_a_cylinder_size_is_plain_litres() -> None:
    """`<Size>12</Size>` is twelve litres, where the app's JSON writes 0.012 cubic metres."""
    assert cylinder("<Size>12</Size>")["volume"] == 12.0


def test_a_gas_is_already_whole_percent() -> None:
    """The app's JSON writes `0.21` for the same mixture; carried unconverted it is 0.21 %."""
    found = cylinder("<Oxygen>21</Oxygen><Helium>0</Helium>")
    assert found["oxygen"] == 21.0
    assert found["helium"] == 0.0


def test_the_oxygen_clock_is_whole_percent_where_the_app_json_writes_a_fraction() -> None:
    """`<CnsEnd>4</CnsEnd>` is the same reading as that export's `EndTissue.CNS: 0.04`.

    OTU needs no conversion in either format — it is the same absolute count — and this
    export rounds it where the app's writes a full float.
    """
    found = one("<CnsStart>0</CnsStart><CnsEnd>4</CnsEnd><OtuStart>0</OtuStart><OtuEnd>10</OtuEnd>")
    assert found["cns_start"] == 0.0
    assert found["cns_end"] == 4.0
    assert found["otu_start"] == 0.0
    assert found["otu_end"] == 10.0


def test_a_ppo2_limit_is_already_bar() -> None:
    """`<PO2>1.4</PO2>`, where the app's JSON writes the same limit as `140000` Pascal."""
    assert cylinder("<PO2>1.4</PO2>")["po2_limit"] == 1.4


def test_a_duration_is_already_seconds() -> None:
    """No clock to parse, unlike Subsurface's `M:SS`."""
    assert one("<Duration>2001</Duration>")["duration"] == 2001


# -- the cross-format known answer ----------------------------------------------------


def test_the_same_dive_read_from_fit_and_from_this_export_agrees() -> None:
    """The strongest check this repository can state about these factors.

    `fixtures/suunto_xml/suunto-d5.xml` and `fixtures/fit/suunto-d5.fit` are the **same
    dive** — 2021-04-06, a Suunto D5 — recorded once and exported twice, and the two reach
    the depth channel through entirely different unit paths: decimal metres in element text
    here, scaled integers in a binary message there. A wrong factor on either side moves one
    of them.

    Three things deliberately do not agree, and each is the source's rather than a reader's.
    The FIT carries the `+02:00` this export records nowhere, and this export carries the
    sub-second fraction the FIT does not. The temperature channels differ because the FIT
    writes whole degrees where this file writes the sensor's float. And this export's own
    scalars — `surface_pressure`, `bottom_temperature`, the cylinder's size and ppO₂ limit —
    have no FIT counterpart at all.
    """
    xml = convert((FIXTURES / "suunto_xml" / "suunto-d5.xml").read_bytes()).document["dives"][0]
    fit = convert((FIXTURES / "fit" / "suunto-d5.fit").read_bytes()).document["dives"][0]

    assert xml["started_at"] == "2021-04-06T11:16:42.6"
    assert fit["started_at"] == "2021-04-06T11:16:42+02:00"
    for member in ("duration", "max_depth", "avg_depth", "cns_end", "otu_end"):
        assert xml[member] == fit[member], member
    assert xml["cylinders"][0]["oxygen"] == fit["cylinders"][0]["oxygen"] == 21.0

    # Sample for sample on the three seconds the reduction and the whole recording share.
    shared = [0, 1, 2]
    assert [xml["profile"]["depth"]["times"][index] for index in shared] == [1, 11, 21]
    assert [fit["profile"]["depth"]["times"][index] for index in shared] == [1, 11, 21]
    assert [xml["profile"]["depth"]["values"][index] for index in shared] == [186, 588, 756]
    assert [fit["profile"]["depth"]["values"][index] for index in shared] == [186, 588, 756]
