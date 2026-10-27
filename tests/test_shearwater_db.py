"""Reading Shearwater Cloud's database export: the container, the rows, the log, the map.

The pair in `fixtures/shearwater_db/` is the owner's export reduced to two dives. Everything
it cannot show — a deco stop, an imperial log, a closed-circuit dive, a hostile file — is
built here, from the layout `docs/shearwater-db-mapping.md` states.
"""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile

import pytest
import shearwaterbuild as sw
from helpers import EXPORTED_AT, FIXTURES, device_of, profile_of, recorded_by

from divejson import (
    MalformedShearwaterDbError,
    ShearwaterDbError,
    SourceTooLargeError,
    compared,
    convert,
    shearwater_db_declared_size,
    sniff,
)
from divejson.converter import InflationBudget, Scope
from divejson.shearwater_db import (
    MAX_LOG_SIZE,
    SHEARWATER_DB,
    Tank,
    decoded_sample,
)
from divejson.validate import validate_document

PAIR = FIXTURES / "shearwater_db" / "perdix3-dives-1-and-8.db"
NOT_A_CLOUD_EXPORT = "not a Shearwater Cloud export"


def _conversion(data: bytes, **kwargs):
    return convert(data, exported_at=EXPORTED_AT, **kwargs)


def _dives(data: bytes) -> list[dict]:
    return _conversion(data).document.get("dives", [])


def _codes(conversion, kind: str | None = None) -> list[str]:
    return [note.code for note in conversion.notes if kind is None or note.kind == kind]


def _edited(statements: list[str]) -> bytes:
    """The pair's database with `statements` run against a copy of it."""
    connection = sqlite3.connect(":memory:")
    connection.deserialize(PAIR.read_bytes())
    for statement in statements:
        connection.execute(statement)
    connection.commit()
    data = connection.serialize()
    connection.close()
    return data


# -- the pair ---------------------------------------------------------------------------


def test_the_pair_converts_to_its_expected_document() -> None:
    expected = json.loads(PAIR.with_suffix(".divejson").read_text(encoding="utf-8"))
    assert compared(_conversion(PAIR.read_bytes()).document) == compared(expected)
    assert validate_document(expected) == []


def test_the_summaries_beside_each_log_agree_with_it() -> None:
    """The sidecars are never read into the document; they earn their keep as cross-checks."""
    assert "summary_disagrees" not in _codes(_conversion(PAIR.read_bytes()))


def test_a_summary_that_disagrees_is_reported_and_the_log_is_kept() -> None:
    data = _edited(["UPDATE dive_details SET SerialNumber = 'D0000000'"])
    conversion = _conversion(data)
    assert _codes(conversion).count("summary_disagrees") == 2
    assert {device_of(dive)["serial"] for dive in conversion.document["dives"]} == {"D9772626"}


def test_dive_eight_reads_as_its_xml_export_and_its_header_say() -> None:
    dive = _dives(PAIR.read_bytes())[1]
    recording = recorded_by(dive)
    assert dive["number"] == 8 and dive["started_at"] == "2026-09-21T10:48:17"
    assert (dive["submerged_time"], dive["max_depth"]) == (3718, 21.8)
    assert recording["device"] == {
        "brand": "Shearwater Research, Inc",
        "model": "Perdix 3",
        "serial": "D9772626",
        "firmware": "39",
        "dive_number": 8,
    }
    assert recording["mode"] == "open_circuit" and recording["salinity"] == "salt"
    assert recording["deco_model"] == {"algorithm": "buhlmann", "gf_low": 50, "gf_high": 85}
    assert (recording["surface_pressure"], recording["cns_start"], recording["cns_end"]) == (1.024, 0.0, 2.0)
    profile = profile_of(dive)
    assert len(profile["depth"]["times"]) == 402 and profile["duration"] == 4_010_000
    assert max(profile["depth"]["values"]) == 2180
    assert (max(profile["ndl"]["values"]), min(profile["ndl"]["values"])) == (5940, 1020)
    assert max(profile["tts"]["values"]) == 360 and max(profile["gradient_factor"]["values"]) == 45
    assert "ceiling" not in profile and "pressures" not in profile


def test_the_first_surface_sample_has_no_no_decompression_time() -> None:
    """NDL 0 with no stop at the surface is no reading; Shearwater's UDDF writes none there."""
    profile = profile_of(_dives(PAIR.read_bytes())[1])
    assert profile["depth"]["times"][0] == 0 and profile["ndl"]["times"][0] == 10_000


# -- the sniff and the refusal ------------------------------------------------------------


def test_a_sqlite_header_is_claimed() -> None:
    assert sniff(PAIR.read_bytes()[:8192]) == "shearwater_db"


@pytest.mark.parametrize("tables", [sw.SUUNTO_DM4, sw.SUUNTO_DM5, sw.SEAC_SYNC], ids=["dm4", "dm5", "seac"])
def test_another_applications_database_is_claimed_and_refused(tables) -> None:
    data = sw.foreign(tables)
    assert sniff(data[:8192]) == "shearwater_db"
    with pytest.raises(ShearwaterDbError, match=NOT_A_CLOUD_EXPORT):
        _conversion(data)


def test_a_cloud_export_with_no_logs_converts_to_no_dives() -> None:
    conversion = _conversion(_edited(["DELETE FROM log_data", "DELETE FROM dive_details"]))
    assert "dives" not in conversion.document
    assert validate_document(conversion.document) == []


def test_bytes_that_only_begin_like_a_database_are_malformed() -> None:
    with pytest.raises(MalformedShearwaterDbError):
        _conversion(b"SQLite format 3\x00" + b"\xff" * 400)


def test_a_write_ahead_log_header_is_read_as_rollback() -> None:
    data = bytearray(PAIR.read_bytes())
    data[18:20] = b"\x02\x02"
    assert len(_dives(bytes(data))) == 2


@pytest.mark.parametrize(
    "statement",
    [
        'CREATE VIEW "dive_details" AS SELECT 1 AS DiveId',
        'CREATE TABLE "dive_details"("DiveId" varchar, "Twice" varchar GENERATED ALWAYS AS (DiveId || DiveId))',
    ],
    ids=["a view", "a generated column"],
)
def test_a_table_that_would_run_the_files_own_code_is_refused(statement) -> None:
    data = sw.database([sw.row(sw.log([sw.sample()]))], tables=[sw.LOG_DATA, statement], version=None)
    with pytest.raises(ShearwaterDbError, match="not plain tables"):
        _conversion(data)


def test_a_virtual_table_is_refused() -> None:
    try:
        data = sw.database(
            [], tables=[sw.LOG_DATA, 'CREATE VIRTUAL TABLE "dive_details" USING fts5("DiveId")'], version=None
        )
    except sqlite3.OperationalError:
        pytest.skip("this SQLite has no fts5 to build a virtual table with")
    with pytest.raises(ShearwaterDbError, match="not plain tables"):
        _conversion(data)


# -- rows ---------------------------------------------------------------------------------


def test_a_log_stored_another_way_is_reported_and_the_rest_convert() -> None:
    data = sw.database(
        [
            sw.row(sw.log([sw.sample()])),
            sw.row(sw.log([sw.sample()]), log_id="other", number=9, format="sw-clouddb"),
            sw.row(sw.log([sw.sample()]), log_id="later", number=10, format_version=1),
        ]
    )
    conversion = _conversion(data)
    assert len(conversion.document["dives"]) == 1
    assert _codes(conversion, "dropped").count("dive_log_unreadable") == 2


@pytest.mark.parametrize(
    ("declared", "says"),
    [(0, "empty"), (MAX_LOG_SIZE + 32, "more than"), (None, "length it declares")],
    ids=["zero", "past the ceiling", "not what it unpacks to"],
)
def test_a_log_whose_length_is_wrong_is_refused_before_it_inflates(declared, says) -> None:
    body = sw.log([sw.sample()])
    stated = len(body) + 32 if declared is None else declared
    data = sw.database([sw.row(body), sw.row(body, log_id="second", data_bytes_1=sw.blob(body, declared=stated))])
    conversion = _conversion(data)
    assert len(conversion.document["dives"]) == 1
    [note] = [note for note in conversion.notes if note.code == "dive_log_unreadable"]
    assert says in note.message and note.where == "dive/1"


def test_a_log_stored_past_the_value_cap_is_reported_and_the_rest_convert() -> None:
    body = sw.log([sw.sample()])
    oversized = sw.blob(body, trailing=b"\x00" * (MAX_LOG_SIZE + 70_000))
    conversion = _conversion(sw.database([sw.row(body), sw.row(body, log_id="second", data_bytes_1=oversized)]))
    assert len(conversion.document["dives"]) == 1
    [note] = [note for note in conversion.notes if note.code == "dive_log_unreadable"]
    assert "is stored in" in note.message and note.where == "dive/1"


def test_the_legacy_predator_layout_is_reported() -> None:
    body = b"\xff\xff" + bytes(30) + sw.log([sw.sample()])
    conversion = _conversion(sw.database([sw.row(body)]))
    assert "dives" not in conversion.document
    assert "Predator" in conversion.notes[0].message


def test_a_log_missing_a_required_record_is_reported() -> None:
    conversion = _conversion(sw.database([sw.row(sw.log([sw.sample()], omit=[0x20]))]))
    assert "dives" not in conversion.document
    assert _codes(conversion) == ["dive_log_unreadable"]


def test_a_deleted_log_is_reported_rather_than_converted() -> None:
    conversion = _conversion(sw.database([sw.row(sw.log([sw.sample()]))], deleted=["1588035071789987697"]))
    assert "dives" not in conversion.document and _codes(conversion) == ["dive_log_deleted"]


def test_logbook_details_with_no_log_are_reported() -> None:
    data = sw.database([sw.row(sw.log([sw.sample()]))], details=[{"DiveId": "nowhere", "DiveNumber": "3"}])
    conversion = _conversion(data)
    assert "dive_without_log" in _codes(conversion, "dropped")


def test_a_log_with_no_logbook_details_converts_from_the_log_alone() -> None:
    [dive] = _dives(sw.one_dive())
    assert "number" not in dive and device_of(dive)["dive_number"] == 8


@pytest.mark.parametrize(
    "sidecar",
    ['{"DiveTimeInSeconds": 3718, "MaxDepth": NaN}', '{"DiveTimeInSeconds": 3718, "MaxDepth": 1e999}'],
    ids=["nan", "overflow"],
)
def test_a_summary_holding_no_finite_number_cannot_fail_the_file(sidecar) -> None:
    conversion = _conversion(sw.database([sw.row(sw.log([sw.sample()]), data_bytes_3=sidecar.encode())]))
    assert len(conversion.document["dives"]) == 1


def test_a_logbook_number_too_long_to_carry_is_reported_not_raised() -> None:
    data = sw.database(
        [sw.row(sw.log([sw.sample()]))], details=[{"DiveId": "1588035071789987697", "DiveNumber": "9" * 5000}]
    )
    conversion = _conversion(data)
    assert "number" not in conversion.document["dives"][0]
    assert "not_a_number" in _codes(conversion, "dropped")


def test_the_logbooks_number_is_the_diver_and_the_counter_is_the_devices() -> None:
    data = sw.database(
        [sw.row(sw.log([sw.sample()], number=3))], details=[{"DiveId": "1588035071789987697", "DiveNumber": "112"}]
    )
    [dive] = _dives(data)
    assert dive["number"] == 112 and device_of(dive)["dive_number"] == 3


def test_a_filled_logbook_column_waits_for_a_file_and_says_so() -> None:
    data = sw.database(
        [sw.row(sw.log([sw.sample()]))],
        details=[{"DiveId": "1588035071789987697", "Location": "Dahab", "Buddy": "", "Notes": None}],
    )
    conversion = _conversion(data)
    [note] = [note for note in conversion.notes if note.code == "logbook_detail_not_read"]
    assert "Location" in note.message


# -- the budget ---------------------------------------------------------------------------


def test_the_declared_size_is_what_the_logs_say_they_unpack_to() -> None:
    assert shearwater_db_declared_size(PAIR.read_bytes()) == 4096 + 16384


def test_a_file_past_the_inflation_budget_is_refused_whole() -> None:
    with pytest.raises(SourceTooLargeError):
        _conversion(PAIR.read_bytes(), max_inflated_size=20479)
    assert len(_conversion(PAIR.read_bytes(), max_inflated_size=20480).document["dives"]) == 2


def test_an_archives_members_share_one_budget() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.db", PAIR.read_bytes())
        archive.writestr("b.db", PAIR.read_bytes())
    with pytest.raises(SourceTooLargeError):
        _conversion(buffer.getvalue(), max_inflated_size=30_000)
    assert len(_conversion(buffer.getvalue(), max_inflated_size=40_960).document["dives"]) == 2


def test_a_budget_cannot_be_negative() -> None:
    with pytest.raises(ValueError):
        InflationBudget(-1)


def test_the_adapter_spends_the_scopes_budget() -> None:
    budget = InflationBudget(30_000)
    SHEARWATER_DB.convert(PAIR.read_bytes(), exported_at=EXPORTED_AT, scope=Scope(inflation=budget))
    assert budget.remaining == 30_000 - 20480


# -- the sample ---------------------------------------------------------------------------


def test_the_sentinels_read_as_absence() -> None:
    read = decoded_sample(sw.sample(ndl=0, ppo2=0, gf99=0xFF, ceiling=0, gas_time=0xF9, sac=0xFFFA), cell_mask=0)
    assert (read.ndl, read.ppo2, read.gradient_factor, read.ceiling, read.gas_time, read.sac) == (None,) * 6
    assert read.cells == (None, None, None) and read.tanks == (Tank(status="the air integration is off"),) * 2


def test_a_stop_turns_the_no_decompression_byte_into_the_stops_time() -> None:
    read = decoded_sample(sw.sample(stop_depth=6, ndl=3, ceiling=5), cell_mask=0)
    assert (read.stop_depth, read.stop_time, read.ndl, read.ceiling) == (6, 3, None, 5)


def test_a_tank_word_below_the_status_range_is_twelve_bits_of_two_psi() -> None:
    read = decoded_sample(sw.sample(tank0=0x13C4, tank1=0xFFFC), cell_mask=0)
    assert read.tanks == (Tank(psi=0x3C4 * 2), Tank(status="no transmitter is paired"))


def test_a_below_zero_temperature_wraps_as_the_layout_says() -> None:
    assert decoded_sample(sw.sample(temperature=-2), cell_mask=0).temperature == 0
    assert decoded_sample(sw.sample(temperature=-110), cell_mask=0).temperature == -8


def test_the_ppo2_byte_averaging_the_cells_is_not_the_computers_ppo2() -> None:
    read = decoded_sample(sw.sample(ppo2=120, status=0x02, cells=(50, 51, 52)), cell_mask=0b111)
    assert read.ppo2 is None and read.cell_ppo2 and read.cells == (50, 51, 52)


# -- the map ------------------------------------------------------------------------------


def test_a_deco_dive_carries_its_ceiling_and_gradient_factor() -> None:
    [dive] = _dives(sw.one_dive(sw.sample(depth=150, stop_depth=3, ndl=2, ceiling=3, gf99=80), sw.sample(depth=30)))
    profile = profile_of(dive)
    assert profile["ceiling"] == {"times": [0], "values": [300]}
    assert profile["gradient_factor"] == {"times": [0], "values": [80]}
    assert profile["ndl"] == {"times": [10_000], "values": [5940]}


def test_a_transmitter_series_hangs_on_the_cylinder_in_its_place() -> None:
    [dive] = _dives(sw.one_dive(sw.sample(tank0=0x05DC), sw.sample(tank0=0x05D0)))
    assert profile_of(dive)["pressures"] == [{"times": [0, 10_000], "values": [2068, 2052], "gas_number": 0}]


def test_a_tank_with_no_cylinder_in_its_place_is_a_cylinder_of_its_own() -> None:
    conversion = _conversion(sw.one_dive(sw.sample(tank1=0x05DC)))
    dive = conversion.document["dives"][0]
    assert dive["cylinders"][1] == {"gas_number": 1}
    assert profile_of(dive)["pressures"][0]["gas_number"] == 1
    assert "cylinder_from_tank_pressures" in _codes(conversion, "absent")


def test_a_gas_change_between_samples_is_a_switch_and_the_first_gas_is_not() -> None:
    data = sw.one_dive(
        sw.sample(oxygen=21), sw.sample(oxygen=21), sw.sample(oxygen=50), oxygen=(21, 50), helium=(0, 0), mask=0b11
    )
    [dive] = _dives(data)
    assert [cylinder["oxygen"] for cylinder in dive["cylinders"]] == [21.0, 50.0]
    assert profile_of(dive)["events"] == [{"time": 20_000, "type": "gas_switch", "gas_number": 1}]


def test_a_switched_off_gas_is_reported_and_not_carried() -> None:
    conversion = _conversion(sw.one_dive(oxygen=(21, 50), helium=(0, 0), mask=0b01))
    assert len(conversion.document["dives"][0]["cylinders"]) == 1
    assert "disabled_gases" in _codes(conversion, "dropped")


def test_a_99_percent_gas_stays_99() -> None:
    [dive] = _dives(sw.one_dive(sw.sample(oxygen=99), oxygen=(99,)))
    assert dive["cylinders"] == [{"oxygen": 99.0, "helium": 0.0, "gas_number": 0}]


def test_an_imperial_log_is_converted_to_metres_and_celsius() -> None:
    [dive] = _dives(sw.one_dive(sw.sample(depth=330, temperature=77), imperial=True, max_depth=330))
    assert dive["max_depth"] == pytest.approx(10.0584)
    profile = profile_of(dive)
    assert profile["depth"]["values"] == [1006] and profile["temperature"]["values"] == [2500]


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (0, "closed_circuit"),
        (1, "open_circuit"),
        (2, "gauge"),
        (4, "semi_closed"),
        (5, "closed_circuit"),
        (7, "freedive"),
    ],
)
def test_the_dive_mode_reads_by_the_table(mode, expected) -> None:
    [dive] = _dives(sw.one_dive(mode=mode))
    assert recorded_by(dive)["mode"] == expected


@pytest.mark.parametrize("mode", [3, 12])
def test_a_mode_with_no_value_is_reported(mode) -> None:
    conversion = _conversion(sw.one_dive(mode=mode))
    assert "mode" not in recorded_by(conversion.document["dives"][0])
    assert "dive_mode_unknown" in _codes(conversion, "dropped")


@pytest.mark.parametrize(
    ("model", "expected"),
    [(1, {"name": "VPM-B", "conservatism": 3}), (2, {"name": "VPM-B/GFS", "conservatism": 3}), (3, {"name": "DCIEM"})],
)
def test_the_named_deco_models(model, expected) -> None:
    [dive] = _dives(sw.one_dive(deco_model=model))
    assert recorded_by(dive)["deco_model"] == expected


def test_under_dciem_the_ceiling_byte_is_not_a_ceiling() -> None:
    conversion = _conversion(sw.one_dive(sw.sample(stop_depth=3, ceiling=3, gf99=40), deco_model=3))
    profile = profile_of(conversion.document["dives"][0])
    assert "ceiling" not in profile and "gradient_factor" not in profile


@pytest.mark.parametrize(("density", "expected"), [(1000, "fresh"), (1020, "en13319"), (1030, "salt")])
def test_the_three_water_settings(density, expected) -> None:
    [dive] = _dives(sw.one_dive(density=density))
    assert recorded_by(dive)["salinity"] == expected


def test_another_water_density_is_reported() -> None:
    conversion = _conversion(sw.one_dive(density=1025))
    assert "salinity" not in recorded_by(conversion.document["dives"][0])
    assert "water_setting_unknown" in _codes(conversion, "dropped")


def test_a_time_zone_the_computer_holds_is_said_and_not_applied() -> None:
    conversion = _conversion(sw.one_dive(zone=120))
    assert conversion.document["dives"][0]["started_at"] == "2026-09-21T10:48:17"
    assert "time_zone_not_read" in _codes(conversion, "dropped")
    assert "start_time_zone_missing" not in _codes(conversion)


def test_a_model_code_that_disagrees_with_the_name_is_reported() -> None:
    conversion = _conversion(sw.one_dive(model=10))
    assert device_of(conversion.document["dives"][0])["model"] == "Perdix 3"
    assert "model_code_disagrees" in _codes(conversion, "dropped")


def test_samples_are_timed_by_their_ordinal_not_their_place_among_records() -> None:
    info = sw.record(0x30, {1: 0x0B})
    body = sw.log([sw.sample(depth=10), sw.sample(depth=20)], interval=2000)
    first = body.index(sw.sample(depth=20))
    data = sw.database([sw.row(body[:first] + info + body[first:])])
    conversion = _conversion(data)
    assert profile_of(conversion.document["dives"][0])["depth"]["times"] == [0, 2000]


def test_a_record_of_an_unknown_kind_is_counted_once() -> None:
    conversion = _conversion(sw.one_dive(extra=[sw.record(0x42), sw.record(0x43)]))
    [note] = [note for note in conversion.notes if note.code == "record_types_unknown"]
    assert "2 records" in note.message


def test_the_reader_writes_no_derived_value() -> None:
    conversion = _conversion(PAIR.read_bytes())
    assert "inferred" not in conversion.document["extensions"]["divejson"]
    for dive in conversion.document["dives"]:
        assert "avg_depth" not in dive
