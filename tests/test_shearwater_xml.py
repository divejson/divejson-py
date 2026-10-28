"""Reading Shearwater Cloud's XML export of a dive — and holding the database reader to it.

The XML export is Shearwater's own decoding of the bytes the database reader decodes, so it
is the oracle for that decoder: for every record the reduced XML pair keeps, the database
pair's sample of the same dive at the same `currentTime` must agree column for column, at
each column's own resolution and through the same sentinel rules. That test is what makes an
independent reading of the binary layout safe to ship.
"""

from __future__ import annotations

import json
import sqlite3
import xml.etree.ElementTree as ET

import pytest
import shearwaterbuild as sw
from helpers import EXPORTED_AT, FIXTURES, device_of, profile_of, recorded_by

from divejson import ShearwaterXmlError, compared, convert, sniff
from divejson.converter import Scope
from divejson.shearwater_db import _Database, _decoded, _inflated, decoded_sample
from divejson.shearwater_xml import SHEARWATER_XML, _Converter
from divejson.validate import validate_document

PAIR = FIXTURES / "shearwater_xml" / "perdix3-dive-8.xml"
DATABASE = FIXTURES / "shearwater_db" / "perdix3-dives-1-and-8.db"
DECO_PAIR = FIXTURES / "shearwater_xml" / "perdix3-deco-dive-11.xml"
DECO_DATABASE = FIXTURES / "shearwater_db" / "perdix3-deco-dive-11.db"

# Each XML pair beside the database pair holding the same dive, and that dive's log's tag.
ORACLES = [
    pytest.param(PAIR, DATABASE, "#8 ", 12, 402, id="dive 8"),
    pytest.param(DECO_PAIR, DECO_DATABASE, "#11 ", 14, 488, id="deco dive 11"),
]

# The columns both exports carry, which the oracle compares.
COMPARED = (
    "depth",
    "tts",
    "ppo2",
    "oxygen",
    "helium",
    "stop_depth",
    "stop_time",
    "ndl",
    "cells",
    "temperature",
    "battery_voltage",
)


def _conversion(data: bytes):
    return convert(data, exported_at=EXPORTED_AT)


def _codes(conversion, kind: str | None = None) -> list[str]:
    return [note.code for note in conversion.notes if kind is None or note.kind == kind]


# -- the oracle ---------------------------------------------------------------------------


def _database_samples(database, tag: str) -> dict[int, object]:
    """One dive's samples out of a database pair, by their millisecond."""
    with _Database(database.read_bytes()) as db:
        [row] = [row for row in db.log_rows() if row.file_name and tag in row.file_name]
        log = _decoded(_inflated(db.log(row)[0], row.declared))
    mask = log.openings[3][6]
    return {
        ordinal * 10_000: decoded_sample(record, cell_mask=mask, first=not ordinal)
        for ordinal, record in enumerate(log.samples)
    }


def _xml_records(pair) -> list[ET.Element]:
    root = ET.fromstring(pair.read_bytes().decode("ascii").replace('encoding="utf-16"', 'encoding="utf-8"'))
    return list(root.find("diveLog").iter("diveLogRecord"))


def _xml_samples(pair) -> dict[int, object]:
    root = ET.fromstring(pair.read_bytes().decode("ascii").replace('encoding="utf-16"', 'encoding="utf-8"'))
    log = root.find("diveLog")
    reader = _Converter(root, log, exported_at=EXPORTED_AT, scope=Scope())
    records = list(log.iter("diveLogRecord"))
    return {reader.time_of(record): reader.sample(record, first=not index) for index, record in enumerate(records)}


def _figure(record: ET.Element, name: str) -> int:
    return int(float(record.findtext(name)))


@pytest.mark.parametrize(("pair", "database", "tag", "kept", "logged"), ORACLES)
def test_every_record_the_xml_keeps_is_the_databases_sample_at_that_time(pair, database, tag, kept, logged) -> None:
    from_database, exported = _database_samples(database, tag), _xml_samples(pair)
    assert len(exported) == kept and len(from_database) == logged
    for at, sample in exported.items():
        assert at in from_database, at
        for column in COMPARED:
            assert getattr(from_database[at], column) == getattr(sample, column), (at, column)


@pytest.mark.parametrize(("pair", "database", "tag", "kept", "logged"), ORACLES)
def test_the_database_agrees_with_the_xml_under_the_rules_both_sides_share(pair, database, tag, kept, logged) -> None:
    """What the shared sentinel rules would hide on both sides at once, checked against the
    XML's own figures: the stop's time, the no-decompression time wherever no stop is
    recorded past the first record, and a ceiling only where a stop is."""
    from_database = _database_samples(database, tag)
    for index, record in enumerate(_xml_records(pair)):
        sample = from_database[_figure(record, "currentTime")]
        stop = _figure(record, "firstStopDepth")
        if stop:
            assert (sample.stop_depth, sample.stop_time) == (stop, _figure(record, "firstStopTime")), index
            assert sample.ndl is None, index
        else:
            assert sample.ceiling is None, index
            if index:
                assert sample.ndl == _figure(record, "currentNdl"), index


def test_the_two_exports_of_dive_eight_carry_one_profile_where_both_have_it() -> None:
    from_xml = profile_of(_conversion(PAIR.read_bytes()).document["dives"][0])
    from_database = profile_of(_conversion(DATABASE.read_bytes()).document["dives"][1])
    for channel in ("depth", "temperature", "ndl", "tts", "ppo2"):
        kept = dict(zip(from_database[channel]["times"], from_database[channel]["values"]))
        assert all(kept[at] == value for at, value in zip(from_xml[channel]["times"], from_xml[channel]["values"]))
    assert "cns" not in from_xml and "gradient_factor" not in from_xml


def test_the_deco_dives_no_decompression_time_reaches_zero_before_its_first_stop() -> None:
    ndl = profile_of(_conversion(DECO_PAIR.read_bytes()).document["dives"][0])["ndl"]
    assert dict(zip(ndl["times"], ndl["values"])) == {
        520_000: 60,
        530_000: 0,
        600_000: 0,
        4_210_000: 5940,
        4_430_000: 5940,
        4_870_000: 5940,
    }


# -- the pair -----------------------------------------------------------------------------


@pytest.mark.parametrize("pair", [PAIR, DECO_PAIR], ids=lambda path: path.stem)
def test_the_pair_converts_to_its_expected_document(pair) -> None:
    expected = json.loads(pair.with_suffix(".divejson").read_text(encoding="utf-8"))
    assert compared(_conversion(pair.read_bytes()).document) == compared(expected)
    assert validate_document(expected) == []


def test_the_pairs_device_is_the_databases_without_a_model() -> None:
    dive = _conversion(PAIR.read_bytes()).document["dives"][0]
    assert device_of(dive) == {
        "brand": "Shearwater Research, Inc",
        "serial": "D9772626",
        "firmware": "39",
        "dive_number": 8,
    }
    assert "number" not in dive


def test_the_firmware_is_read_as_the_hexadecimal_it_is() -> None:
    conversion = _conversion(PAIR.read_bytes())
    assert "firmware_read_as_hexadecimal" in _codes(conversion, "resolved")


# -- the sniff and the file ---------------------------------------------------------------


def test_the_export_is_claimed_and_the_dm5_root_is_not() -> None:
    assert sniff(PAIR.read_bytes()[:8192]) == "shearwater_xml"
    for dm5 in sorted((FIXTURES / "suunto_xml").glob("*.xml")):
        assert sniff(dm5.read_bytes()[:8192]) == "suunto_xml"


@pytest.mark.parametrize(
    "head",
    [
        b'<dive version="3"><nothing/></dive>',
        b"<dive><diveLog/></dive>",
        b'<dive xmlns="urn:x" version="3"><diveLog/></dive>',
    ],
    ids=["no diveLog", "no version", "a namespace of its own"],
)
def test_a_dive_root_without_the_exports_shape_is_not_claimed(head) -> None:
    assert sniff(head) is None


def test_a_declaration_that_tells_the_truth_reads_too() -> None:
    data = sw.xml([sw.xml_record()], declaration='encoding="utf-8"')
    assert len(_conversion(data).document["dives"]) == 1


def test_a_utf16_file_with_its_byte_order_mark_reads_as_declared() -> None:
    data = sw.xml([sw.xml_record()]).decode("ascii").encode("utf-16")
    assert len(SHEARWATER_XML.convert(data, exported_at=EXPORTED_AT, scope=Scope()).document["dives"]) == 1


def test_a_doctype_is_still_refused() -> None:
    from divejson import DoctypeRefusedError

    data = sw.xml([sw.xml_record()]).replace(b"<dive ", b'<!DOCTYPE dive [<!ENTITY a "b">]><dive ', 1)
    with pytest.raises(DoctypeRefusedError):
        _conversion(data)


# -- the date -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("written", "read"),
    [
        ("9/21/2026 10:48:17 AM", "2026-09-21T10:48:17"),
        ("21/9/2026 10:48:17", "2026-09-21T10:48:17"),
        ("9/21/2026 12:05:00 AM", "2026-09-21T00:05:00"),
        ("9/21/2026 12:05:00 PM", "2026-09-21T12:05:00"),
        ("9/21/2026 1:05:00 PM", "2026-09-21T13:05:00"),
        ("21/9/2026 22:48", "2026-09-21T22:48:00"),
    ],
)
def test_a_date_with_a_field_above_twelve_settles_itself(written, read) -> None:
    conversion = _conversion(sw.xml([sw.xml_record()], startDate=written))
    assert conversion.document["dives"][0]["started_at"] == read
    assert "start_date_order_resolved" not in _codes(conversion)


def test_an_ambiguous_date_is_read_month_first_and_the_other_reading_named() -> None:
    conversion = _conversion(sw.xml([sw.xml_record()], startDate="9/8/2026 3:18:10 PM"))
    assert conversion.document["dives"][0]["started_at"] == "2026-09-08T15:18:10"
    [note] = [note for note in conversion.notes if note.code == "start_date_order_resolved"]
    assert note.kind == "resolved" and "2026-08-09" in note.message


def test_a_date_whose_two_readings_are_one_date_is_not_reported() -> None:
    conversion = _conversion(sw.xml([sw.xml_record()], startDate="8/8/2026 3:18:10 PM"))
    assert "start_date_order_resolved" not in _codes(conversion)


@pytest.mark.parametrize("written", ["2026-09-21 10:48:17", "13/21/2026 10:48:17", "9/21/2026 13:00:00 PM", ""])
def test_a_date_that_cannot_be_read_refuses_the_file_and_names_it(written) -> None:
    with pytest.raises(ShearwaterXmlError, match="startDate"):
        _conversion(sw.xml([sw.xml_record()], startDate=written))


# -- the map ------------------------------------------------------------------------------


def test_each_distinct_mix_is_a_cylinder_in_order_of_first_appearance() -> None:
    records = [
        sw.xml_record(currentTime="0", fractionO2="0.21"),
        sw.xml_record(currentTime="10000", fractionO2="0.21"),
        sw.xml_record(currentTime="20000", fractionO2="0.5"),
    ]
    dive = _conversion(sw.xml(records)).document["dives"][0]
    assert dive["cylinders"] == [
        {"oxygen": 21.0, "helium": 0.0, "gas_number": 0},
        {"oxygen": 50.0, "helium": 0.0, "gas_number": 1},
    ]
    assert profile_of(dive)["events"] == [{"time": 20000, "type": "gas_switch", "gas_number": 1}]


def test_a_tank_pressure_that_is_a_number_is_a_reading() -> None:
    records = [
        sw.xml_record(currentTime="0", tank0pressurePSI="3000"),
        sw.xml_record(currentTime="10000", tank0pressurePSI="2900"),
    ]
    dive = _conversion(sw.xml(records)).document["dives"][0]
    assert profile_of(dive)["pressures"] == [{"times": [0, 10000], "values": [2068, 1999], "gas_number": 0}]


def test_the_words_in_a_numeric_column_are_no_reading() -> None:
    conversion = _conversion(sw.xml([sw.xml_record(gasTime="N/A", sac="Waiting for initial data")]))
    assert "air_consumption_not_kept" not in _codes(conversion)
    assert "Tank 1 records no pressure, the air integration is off." in [note.message for note in conversion.notes]


def test_an_imperial_export_is_converted() -> None:
    data = sw.xml([sw.xml_record(currentDepth="33", waterTemp="77")], imperialUnits="true", maxDepth="33")
    dive = _conversion(data).document["dives"][0]
    assert dive["max_depth"] == pytest.approx(10.0584)
    assert profile_of(dive)["depth"]["values"] == [1006] and profile_of(dive)["temperature"]["values"] == [2500]


def test_the_readouts_and_the_deco_model() -> None:
    recording = recorded_by(_conversion(sw.xml([sw.xml_record()], decoModel="1")).document["dives"][0])
    assert recording["deco_model"] == {"name": "VPM-B", "conservatism": 3}
    assert (recording["surface_pressure"], recording["cns_start"], recording["cns_end"]) == (1.024, 0.0, 2.0)
    assert "mode" not in recording and "salinity" not in recording


def test_an_archive_of_exports_gives_each_dive_its_own_identity() -> None:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.xml", PAIR.read_bytes())
        archive.writestr("b.xml", PAIR.read_bytes())
    dives = _conversion(buffer.getvalue()).document["dives"]
    assert len({dive["uuid"] for dive in dives}) == 2


def test_the_database_fixture_is_a_cloud_export_sqlite_can_open() -> None:
    connection = sqlite3.connect(":memory:")
    connection.deserialize(DATABASE.read_bytes())
    assert connection.execute("SELECT count(*) FROM log_data").fetchone() == (2,)
    connection.close()
