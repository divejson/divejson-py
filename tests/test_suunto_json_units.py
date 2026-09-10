"""The unit conversions this reader applies, one factor at a time.

`docs/converting.md` calls this the highest-risk part of a converter: a wrong factor
produces a document that validates perfectly and describes a dive nobody took. Nothing
downstream catches it — a validator accepts any integer as a profile sample — so these are
worth more than the fixtures.

This export is the SI one. Pressure is Pascal where §6.3 holds bar, tank size is cubic
metres where §6.3 holds litres, a gas fraction is 0-1 where §6.3 holds whole percent, and
temperature is Kelvin where §6.5's channel holds tenths of a degree Celsius. Every one of
those is a place where carrying the number as written is a plausible-looking lie.
"""

from __future__ import annotations

from decimal import Decimal

from helpers import EXPORTED_AT, profile_of, suunto_json, suunto_sample, suunto_slots

from divejson import convert


def _dive(header: dict, samples: list[dict] | None = None) -> dict:
    return convert(suunto_json(header, samples), exported_at=EXPORTED_AT).document["dives"][0]


def _gas(**members: object) -> dict:
    return {"Diving": {"Gases": [{"State": "Primary", **members}]}}


# -- pressure -------------------------------------------------------------------------


def test_a_tank_pressure_is_pascal_and_the_member_is_bar() -> None:
    """20 000 000 Pa is 200 bar, and carrying the integer would be 20 MPa of nothing."""
    cylinder = _dive(_gas(StartPressure=20_000_000, EndPressure=5_000_000))["cylinders"][0]
    assert cylinder == {"start_pressure": 200.0, "end_pressure": 50.0, "role": "bottom"}


def test_a_pressure_is_exact_and_never_rounded() -> None:
    """The source's own value, to every digit it recorded.

    This converter quantizes nothing a source recorded, and a dive computer's transmitter
    reports in steps far finer than a gauge a diver reads — 21 162 500 Pa is 211.625 bar and
    not 211.62 or 211.63. Rounding here would write a convention into the conformance
    corpus that this library applies nowhere else.
    """
    cylinder = _dive(_gas(StartPressure=21_162_500, EndPressure=12_715_625))["cylinders"][0]
    assert cylinder["start_pressure"] == 211.625
    assert cylinder["end_pressure"] == 127.15625
    assert Decimal(str(cylinder["end_pressure"])) == 12715625 / Decimal(100_000)


def test_a_ppo2_limit_is_pascal_too() -> None:
    """140 000 Pa is 1.4 bar, which is the number written on every deco planner."""
    assert _dive(_gas(PO2=140_000))["cylinders"][0]["po2_limit"] == 1.4


def test_a_pressure_channel_is_tenths_of_a_bar() -> None:
    """§6.5's scale, and the one a table of the scalar factors above does not contain."""
    dive = _dive(
        {},
        [
            suunto_sample(0, Cylinders=suunto_slots(20_000_000), Depth=1.0),
            suunto_sample(60, Cylinders=suunto_slots(18_385_938), Depth=20.0),
        ],
    )
    # 200 bar and 183.85938 bar, the second rounded half-away-from-zero at the tenth.
    assert profile_of(dive)["pressures"][0]["values"] == [2000, 1839]


# -- gas mixture ----------------------------------------------------------------------


def test_a_gas_fraction_is_zero_to_one_and_the_member_is_whole_percent() -> None:
    """0.21 is air, and carrying it as written reports a 21 % mix as 0.21 %."""
    cylinder = _dive(_gas(Oxygen=0.21, Helium=0))["cylinders"][0]
    assert cylinder["oxygen"] == 21.0 and cylinder["helium"] == 0.0


def test_a_trimix_fraction_converts_without_binary_noise() -> None:
    """`0.18 * 100` is exactly 18; the float route arrives at 18.000000000000004."""
    cylinder = _dive(_gas(Oxygen=0.18, Helium=0.45))["cylinders"][0]
    assert cylinder["oxygen"] == 18.0 and cylinder["helium"] == 45.0


def test_a_tank_size_is_cubic_metres_and_the_member_is_litres() -> None:
    """0.012 m³ is a 12-litre cylinder, which is the number stamped on its neck."""
    assert _dive(_gas(TankSize=0.012))["cylinders"][0]["volume"] == 12.0


def test_a_gas_this_export_never_recorded_is_absent_and_never_air() -> None:
    """§6.3: absent oxygen means not recorded, not 21."""
    conversion = convert(
        suunto_json({}, [suunto_sample(0, Cylinders=suunto_slots(20_000_000), Depth=1.0)]),
        exported_at=EXPORTED_AT,
    )
    cylinder = conversion.document["dives"][0]["cylinders"][0]
    assert "oxygen" not in cylinder and "helium" not in cylinder
    assert any(note.kind == "absent" and "never air" in note.message for note in conversion.notes)


# -- temperature ----------------------------------------------------------------------


def test_a_sample_temperature_is_kelvin_and_the_channel_is_tenths_of_celsius() -> None:
    """293.75 K is 20.6 °C, and the subtraction runs on decimals.

    273.15 has no exact binary representation, so `293.75 - 273.15` on floats lands on
    20.600000000000023 and has to be rounded back out. Doing it in decimal never introduces
    the error.
    """
    dive = _dive({}, [suunto_sample(0, Temperature=293.75, Depth=1.0)])
    assert profile_of(dive)["temperature"]["values"] == [206]


def test_a_temperature_below_freezing_stays_negative() -> None:
    """271.65 K is −1.5 °C, which an ice dive really records."""
    dive = _dive({}, [suunto_sample(0, Temperature=271.65, Depth=1.0)])
    assert profile_of(dive)["temperature"]["values"] == [-15]


# -- depth ----------------------------------------------------------------------------


def test_a_depth_channel_is_centimetres() -> None:
    """§6.5's other channel scale. 45.91 m is 4 591 cm, exactly."""
    dive = _dive({}, [suunto_sample(0, Depth=45.91)])
    assert profile_of(dive)["depth"]["values"] == [4591]


def test_a_ceiling_channel_is_centimetres_and_a_zero_is_no_ceiling() -> None:
    dive = _dive(
        {},
        [
            suunto_sample(0, Depth=30.0, Ceiling=4.5),
            suunto_sample(60, Depth=5.0, Ceiling=0),
        ],
    )
    assert profile_of(dive)["ceiling"] == {"times": [0], "values": [450]}


def test_a_header_depth_is_metres_and_reaches_the_member_unscaled() -> None:
    """The scalar case beside the channel one, which is the whole point of having both."""
    dive = _dive({"Depth": {"Max": 45.91, "Avg": 20.87}})
    assert dive["max_depth"] == 45.91 and dive["avg_depth"] == 20.87


# -- the oxygen clock and the barometer -----------------------------------------------


def test_cns_is_a_fraction_and_otu_is_not() -> None:
    """The pair that looks like one unit and is two.

    `CNS: 0.069` is 6.9 %; `OTU: 17.89` is 17.89 OTU. Converting both would report the
    oxygen tolerance units as 1 789 of them.
    """
    dive = _dive(
        {
            "Diving": {
                "StartTissue": {"CNS": 0, "OTU": 0},
                "EndTissue": {"CNS": 0.069, "OTU": 17.89002799987793},
            }
        }
    )
    assert dive["cns_end"] == 6.9 and dive["otu_end"] == 17.89002799987793
    # Zero is an answer for both: §6.2 gives them `minimum: 0`, so a first dive of the day
    # starting on nothing is a reading rather than a placeholder.
    assert dive["cns_start"] == 0.0 and dive["otu_start"] == 0.0


def test_a_surface_pressure_is_pascal_and_the_member_is_bar() -> None:
    assert _dive({"Diving": {"SurfacePressure": 104_900}})["surface_pressure"] == 1.049


# -- coordinates ----------------------------------------------------------------------


def test_a_sample_fix_is_radians_and_the_member_is_degrees() -> None:
    """0.49859222167449974 rad is 28.56723°, which is a reef in the Gulf of Aqaba.

    Carrying the radians would put the dive 28 degrees of latitude from where it was, in a
    member a validator is perfectly happy with.
    """
    dive = _dive(
        {},
        [
            suunto_sample(0, Depth=30.0),
            suunto_sample(
                60,
                Latitude=0.49859222167449974,
                Longitude=0.6027186224443467,
            ),
        ],
    )
    assert dive["exit_position"] == {"latitude": 28.56723, "longitude": 34.533233}


def test_a_route_origin_is_already_degrees_and_is_carried_exactly() -> None:
    """The same file's other coordinate channel, in the other unit and unrounded.

    Converting it would be this reader applying a factor to a number that already carries
    the member's own unit; quantizing it would be this reader rounding a value the source
    recorded, which it does nowhere else.
    """
    dive = _dive(
        {},
        [
            suunto_sample(
                0,
                Depth=1.0,
                DiveRouteOrigin={
                    "Latitude": 28.567251205444336,
                    "Longitude": 34.53325653076172,
                },
            ),
            suunto_sample(60, Depth=30.0),
        ],
    )
    assert dive["entry_position"] == {"latitude": 28.567251205444336, "longitude": 34.53325653076172}


# -- time -----------------------------------------------------------------------------


def test_a_duration_is_seconds_and_rounds_half_away_from_zero() -> None:
    """4 001.4 s is 4 001, and 2.5 s of anything is 3 rather than banker's 2."""
    assert _dive({"DiveTime": 4001.4})["duration"] == 4001
    assert _dive({"DiveTime": 2.5})["duration"] == 3


def test_a_sample_second_is_elapsed_time_from_the_header_s_own_start() -> None:
    """The axis's origin, which is the instant `started_at` names."""
    dive = _dive({}, [suunto_sample(0, Depth=1.0), suunto_sample(120.49, Depth=2.0)])
    assert profile_of(dive)["depth"]["times"] == [0, 120]
