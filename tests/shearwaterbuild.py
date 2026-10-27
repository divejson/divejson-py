"""Building Shearwater Cloud databases and XML exports to convert, one behaviour at a time.

The pairs in `fixtures/shearwater_db/` and `fixtures/shearwater_xml/` are real exports. These
build the smallest log, database or XML file that exhibits one rule — a deco stop, an
imperial log, a view where a table belongs — with the record layout
`docs/shearwater-db-mapping.md` states, so a test reads as the rule it checks.

The DM4, DM5 and Seac Sync databases the sniff refuses are built here too, from those
formats' table names alone: Subsurface carries real ones, under a licence this repository
does not take files from.
"""

from __future__ import annotations

import gzip
import sqlite3
import struct
from collections.abc import Iterable, Sequence

RECORD = 32

# The Perdix 3 the fixtures come from, as its log's final record states it.
SERIAL = 0xD9772626

LOG_DATA = (
    'CREATE TABLE "log_data"("log_id" varchar primary key not null, "table_version" bigint, '
    '"created_unixtime" bigint, "modified_unixtime" bigint, "file_name" varchar, "format" varchar, '
    '"format_version" INTEGER, "calculated_values_from_samples" varchar, "data_bytes_1" BLOB, '
    '"data_bytes_2" BLOB, "data_bytes_3" BLOB, "data_bytes_4" BLOB)'
)
DIVE_DETAILS = (
    'CREATE TABLE "dive_details"("DiveId" varchar primary key not null, "DiveNumber" varchar, '
    '"SerialNumber" varchar, "TankProfileData" varchar, "Location" varchar, "Buddy" varchar, '
    '"Notes" varchar)'
)
DELETED_LOGS = 'CREATE TABLE "DeletedLogs"("DiveID" varchar primary key not null, "DeletedAsOf" datetime)'
TABLE_VERSION = 'CREATE TABLE "SWC_TableVersion"("Id" integer primary key autoincrement not null, "DbVersion" integer)'


def record(kind: int, fields: dict[int, int | bytes] | None = None) -> bytes:
    """One 32-byte record: `fields` maps an offset to a byte, or to the bytes starting there."""
    built = bytearray(RECORD)
    built[0] = kind
    for offset, value in (fields or {}).items():
        if isinstance(value, bytes):
            built[offset : offset + len(value)] = value
        else:
            built[offset] = value
    return bytes(built)


def sample(
    *,
    depth: int = 100,
    stop_depth: int = 0,
    tts: int = 1,
    ppo2: int = 30,
    oxygen: int = 21,
    helium: int = 0,
    ndl: int = 99,
    battery: int = 80,
    status: int = 0x10,
    cells: tuple[int, int, int] = (0, 0, 0),
    temperature: int = 25,
    voltage: int = 0,
    setpoint: int = 0,
    tank0: int = 0xFFFF,
    tank1: int = 0xFFFF,
    gas_time: int = 0xF9,
    cns: int = 0,
    ceiling: int = 0,
    gf99: int = 0xFF,
    at_plus_five: int = 0xFFFF,
    sac: int = 0xFFF9,
) -> bytes:
    """One `0x01` sample record, in the layout's units: tenths of a metre, minutes, percent."""
    return record(
        0x01,
        {
            1: struct.pack(">HHH", depth, stop_depth, tts),
            7: ppo2,
            8: oxygen,
            9: helium,
            10: ndl,
            11: battery,
            12: status,
            13: cells[0],
            14: struct.pack(">b", temperature),
            15: cells[1],
            16: cells[2],
            17: struct.pack(">H", voltage),
            19: setpoint,
            20: struct.pack(">H", tank1),
            22: gas_time,
            23: cns,
            24: ceiling,
            25: gf99,
            26: struct.pack(">H", at_plus_five),
            28: struct.pack(">H", tank0),
            30: struct.pack(">H", sac),
        },
    )


def log(
    samples: Sequence[bytes] = (),
    *,
    number: int = 8,
    gf: tuple[int, int] = (50, 85),
    imperial: bool = False,
    ticks: int = 1789987697,
    oxygen: Sequence[int] = (21,),
    helium: Sequence[int] = (0,),
    mask: int = 0x0001,
    cns: tuple[int, int] = (0, 2),
    millibar: int = 1024,
    deco_model: int = 0,
    conservatism: int = 3,
    density: int = 1030,
    cell_mask: int = 0,
    mode: int = 6,
    log_version: int = 17,
    interval: int = 10_000,
    zone: int = 0,
    max_depth: int = 218,
    dive_time: int = 3718,
    serial: int = SERIAL,
    firmware: int = 0x39,
    model: int = 14,
    extra: Iterable[bytes] = (),
    omit: Iterable[int] = (),
) -> bytes:
    """A whole device log: the opening records, `extra`, the samples, the closing and final records.

    `omit` names record types to leave out, for a log missing one of the required set.
    """
    o2 = list(oxygen) + [0] * (10 - len(oxygen))
    he = list(helium) + [0] * (10 - len(helium))
    openings = [
        record(
            0x10,
            {
                2: struct.pack(">H", number),
                4: gf[0],
                5: gf[1],
                8: int(imperial),
                10: struct.pack(">H", cns[0]),
                12: struct.pack(">I", ticks),
                20: bytes(o2),
                30: bytes(he[:2]),
            },
        ),
        record(0x11, {1: bytes(he[2:]), 16: struct.pack(">H", millibar)}),
        record(0x12, {18: deco_model, 19: conservatism}),
        record(0x13, {3: struct.pack(">H", density), 6: cell_mask}),
        record(0x14, {1: mode, 16: log_version, 17: struct.pack(">H", mask)}),
        record(0x15, {23: struct.pack(">H", interval), 26: struct.pack(">i", zone)}),
    ]
    closing = record(
        0x20, {4: struct.pack(">H", max_depth), 6: struct.pack(">I", dive_time)[1:], 10: struct.pack(">H", cns[1])}
    )
    final = record(0xFF, {1: 0xFD, 2: struct.pack(">I", serial), 10: firmware, 13: model})
    records = [*openings, *extra, *samples, closing, bytes(RECORD) * 2, final, bytes(RECORD)]
    omitted = set(omit)
    return b"".join(chunk for chunk in records if chunk[0] not in omitted)


def blob(body: bytes, *, declared: int | None = None, trailing: bytes = b"\x00" * 16) -> bytes:
    """A `data_bytes_1`: the four-byte little-endian length, one gzip member, and junk after it."""
    length = len(body) if declared is None else declared
    return struct.pack("<I", length) + gzip.compress(body) + trailing


def database(
    rows: Sequence[dict] = (),
    *,
    details: Sequence[dict] = (),
    deleted: Sequence[str] = (),
    tables: Sequence[str] = (LOG_DATA, DIVE_DETAILS, DELETED_LOGS, TABLE_VERSION),
    version: int | None = 12,
) -> bytes:
    """A Cloud database's bytes: `rows` into `log_data`, `details` into `dive_details`.

    A row is a dict of `log_data` columns; `data_bytes_1` defaults to nothing.
    """
    connection = sqlite3.connect(":memory:")
    for statement in tables:
        connection.execute(statement)
    for row in rows:
        columns = ", ".join(f'"{name}"' for name in row)
        connection.execute(f"INSERT INTO log_data ({columns}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
    for row in details:
        columns = ", ".join(f'"{name}"' for name in row)
        connection.execute(
            f"INSERT INTO dive_details ({columns}) VALUES ({', '.join('?' * len(row))})", list(row.values())
        )
    for log_id in deleted:
        connection.execute('INSERT INTO "DeletedLogs" ("DiveID") VALUES (?)', (log_id,))
    if version is not None:
        connection.execute('INSERT INTO "SWC_TableVersion" ("DbVersion") VALUES (?)', (version,))
    connection.commit()
    data = connection.serialize()
    connection.close()
    return data


def row(body: bytes, *, log_id: str = "1588035071789987697", number: int = 8, **columns) -> dict:
    """One `log_data` row holding `body` as the device's log."""
    built = {
        "log_id": log_id,
        "file_name": f"Perdix 3[D9772626]#{number} 2026-9-21 10-48-17.swlogzp",
        "format": "sw-pnf",
        "format_version": 0,
        "data_bytes_1": blob(body),
    }
    built.update(columns)
    return built


def one_dive(*samples: bytes, **header) -> bytes:
    """A database holding one log of `samples`, with `header` passed to `log`."""
    return database([row(log(samples or (sample(),), **header))])


def foreign(tables: Iterable[str]) -> bytes:
    """Another application's SQLite logbook, made of its own table names and nothing else."""
    connection = sqlite3.connect(":memory:")
    for statement in tables:
        connection.execute(statement)
    connection.commit()
    data = connection.serialize()
    connection.close()
    return data


# The table names Subsurface's sniff tells three other SQLite dive logs apart by.
SUUNTO_DM4 = (
    "CREATE TABLE Dive (DiveId INTEGER PRIMARY KEY, StartTime INTEGER, ProfileBlob BLOB)",
    "CREATE TABLE Mix (MixId INTEGER)",
)
SUUNTO_DM5 = (
    "CREATE TABLE Dive (DiveId INTEGER PRIMARY KEY, StartTime INTEGER, SampleBlob BLOB)",
    "CREATE TABLE DiveMixture (DiveMixtureId INTEGER)",
)
SEAC_SYNC = ("CREATE TABLE headers_dive (dive_number INTEGER)", "CREATE TABLE dive_data (dive_number INTEGER)")


def xml(records: Sequence[dict[str, str]] = (), *, declaration: str = 'encoding="utf-16"', **log: str) -> bytes:
    """A Cloud XML export: `log` overrides the `<diveLog>` header, each record a `<diveLogRecord>`."""
    fields = {
        "number": "8",
        "gfMin": "50",
        "gfMax": "85",
        "imperialUnits": "false",
        "startCns": "0",
        "startDate": "9/21/2026 10:48:17 AM",
        "computerFirmware": "27",
        "maxDepth": "21.8000011",
        "maxTime": "3718",
        "endCns": "2",
        "computerSerial": "D9772626",
        "logVersion": "17",
        "decoModel": "0",
        "vpmbConservatism": "3",
        "startSurfacePressure": "1024",
        "product": "14",
    }
    fields.update(log)
    head = "".join(f"<{name}>{value}</{name}>" for name, value in fields.items())
    body = "".join(
        "<diveLogRecord>" + "".join(f"<{name}>{value}</{name}>" for name, value in xml_record(**given).items())
        + "</diveLogRecord>"
        for given in records
    )
    return (
        f'<?xml version="1.0" {declaration}?>\n<dive xmlns:xsd="http://www.w3.org/2001/XMLSchema" version="3">'
        f"<diveLog>{head}<diveLogRecords>{body}</diveLogRecords></diveLog></dive>"
    ).encode("ascii")


def xml_record(**overrides: str) -> dict[str, str]:
    """One `<diveLogRecord>`'s children, the owner's surface record by default."""
    fields = {
        "currentTime": "0",
        "currentDepth": "1.2",
        "firstStopDepth": "0",
        "ttsMins": "1",
        "averagePPO2": "0.24",
        "fractionO2": "0.21",
        "fractionHe": "0",
        "firstStopTime": "0",
        "currentNdl": "99",
        "currentCircuitSetting": "OC/BO",
        "waterTemp": "31",
        "externalPPO2": "0",
        "sensor1Millivolts": "0",
        "sensor2Millivolts": "0",
        "sensor3Millivolts": "0",
        "batteryVoltage": "1.51",
        "tank0pressurePSI": "AI is off",
        "tank1pressurePSI": "AI is off",
        "tank2pressurePSI": "N/A",
        "tank3pressurePSI": "N/A",
        "gasTime": "N/A",
        "sac": "Not Diving",
        "sad": "0",
    }
    fields.update(overrides)
    return fields
