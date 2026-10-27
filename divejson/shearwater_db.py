"""Reading Shearwater Cloud's database export into DiveJSON.

Shearwater Cloud Desktop's *Export Whole Database* writes the diver's whole account as one
SQLite file. `docs/shearwater-db-mapping.md` is the prose companion: the record layout this
module decodes, every column it reads, every one it deliberately does not, and why. What is
true of every source format rather than of this one — the note kinds, identity, the way a
zero reads, the sample axis — lives in `converter.py` and `series.py`, and this module
inherits it.

Four things shape what is below.

**The dives are blobs, not rows.** The decoded tables a reader would reach for first,
`dive_logs` and `dive_log_records`, are empty in every export examined; each dive is one
`log_data` row whose `data_bytes_1` is the dive computer's own log — the Petrel Native
Format, one 32-byte record per sample — gzip-compressed behind a four-byte length. So this
reader decodes the device's bytes, and the layout it decodes is stated in the mapping
document rather than taken from any decoder's code.

**A database is a container, and it is bounded twice.** The caller's byte cap bounds the
file and not what unpacks out of it, so the declared lengths of every log are summed and
spent against the caller's `InflationBudget` before anything inflates, and each log is held
to a ceiling of its own sized to the device. The SQLite file itself is a stranger's, so it is
opened in memory with what sqlite.org asks of an untrusted database: no schema-defined code
evaluated, every table read checked to be a plain table, values capped, statements bounded.

**The start is the wall clock on the diver's wrist.** The opening record's ticks are seconds
on the device's local clock, not UTC, and a dive's start is written with no offset — the same
reading the UDDF reader keeps for this vendor's `Z`.

**The log is the device's, so the profile keeps every sample it wrote**, including the
minutes of surface logging past the dive time. The XML export of the same dive agrees with it
sample for sample, and `shearwater_xml.py` reads that file through the same column rules.
"""

from __future__ import annotations

import json
import re
import sqlite3
import struct
import uuid as uuid_pkg
import zlib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .converter import (
    CENTIMETRES_PER_METRE,
    HUNDREDTHS_PER_DEGREE,
    PRODUCER_KEY,
    TENTHS_PER_UNIT,
    Conversion,
    ConverterError,
    Identities,
    NonConformingOutputError,
    Note,
    NoteKind,
    Reporter,
    Scope,
    deco_model,
    device,
    header,
    recorded,
    recording,
    rounded,
)
from .series import Channel, SampleAxis
from .validate import validate_document

__all__ = [
    "BRAND",
    "SHEARWATER_DB",
    "SHEARWATER_DB_ID_NAMESPACE",
    "MalformedShearwaterDbError",
    "Sample",
    "ShearwaterDbAdapter",
    "ShearwaterDbError",
    "Tank",
    "declared_size",
]

# The format id, vendor then container, the pattern the two Suunto readers set.
FORMAT = "shearwater_db"

# uuid5(NAMESPACE_URL, "https://divejson.org/ns/shearwater_db"). Fixed forever:
# `docs/shearwater-db-mapping.md` *Identity* records the value as normative for any port.
SHEARWATER_DB_ID_NAMESPACE = uuid_pkg.UUID("6235a5bc-a5d6-5b22-ac68-2da79dd6438b")

# The manufacturer string Shearwater's own UDDF export writes and the UDDF reader carries,
# so that one computer read from either export is one device. A vendor-proprietary export is
# the vendor saying so, as `suunto_json.py` argues for its literal.
BRAND = "Shearwater Research, Inc"

# The sixteen bytes every SQLite file opens with. This build's only SQLite reader, so the
# header is what is claimed — see `ShearwaterDbAdapter.sniff`.
SQLITE_MAGIC = b"SQLite format 3\x00"

# The `log_data.format` of the device's own log, and the one `format_version` seen.
LOG_FORMAT = "sw-pnf"
LOG_FORMAT_VERSION = 0

# The ceiling one log may declare, refused before it inflates: close to two hundred times
# the longest log in hand (22 400 bytes, a Petrel 3 dive), and far above a several-hour
# Perdix log at its 10 s default, which stays under 100 KB.
MAX_LOG_SIZE = 4 * 1024 * 1024

# SQLite's largest page. The connection's value cap is a log's ceiling plus one, so that no
# single value the file holds allocates past what one log may be.
MAX_PAGE_SIZE = 65_536

# How long one statement may run before it is stopped: the progress handler is called every
# `PROGRESS_STEP` virtual-machine instructions, and a statement is stopped after `MIN_CALLS`
# calls or one per `BYTES_PER_CALL` of the file, whichever is more — ten million
# instructions at least. The heaviest statement here, the integrity check, takes under two
# thousand instructions on the owner's 254 KB export, so the bound is thousands of times
# what an honest file of any size needs and still ends a crafted one in well under a second.
PROGRESS_STEP = 1_000
MIN_CALLS = 10_000
BYTES_PER_CALL = 100

RECORD_SIZE = 32
ZERO_RECORD = bytes(RECORD_SIZE)

# The record types, by their first byte.
SAMPLE = 0x01
OPENING = range(0x10, 0x1A)
CLOSING = range(0x20, 0x2A)
INFO_EVENT = 0x30
TISSUES = range(0x70, 0x76)
FINAL = 0xFF
# A closed-circuit Petrel 3's sensor validity maps and periodic records, and the samples of
# two modes no file in hand carries.
VALIDITY = frozenset({*range(0x80, 0x88), 0x90, 0xA0, 0xA1, 0x51})
FREEDIVE_SAMPLE, AVELO_SAMPLE = 0x02, 0x03

# What a log has to carry to be read at all: opening records 0 to 4, closing record 0 and
# the final record.
REQUIRED_OPENINGS = range(5)

# The sample record's status bits.
STATUS_EXTERNAL_PPO2 = 0x02
STATUS_OPEN_CIRCUIT = 0x10

# A tank word at or above this is the transmitter's status rather than a pressure.
TANK_STATUS = 0xFFF0
TANK_STATUSES = {
    0xFFFF: "the air integration is off",
    0xFFFE: "the transmitter is not communicating",
    0xFFFD: "the transmitter is not communicating",
    0xFFFC: "no transmitter is paired",
}
OTHER_TANK_STATUS = "the transmitter reports a status rather than a reading"

# The other sentinels a sample carries, each settled against the files in hand.
NO_GRADIENT_FACTOR = 0xFF
GAS_TIME_STATUS = 0xF0
SAC_STATUS = 0xFFF0
NO_AT_PLUS_FIVE = 0xFFFF
NDL_CAP = 99

# Opening 4's dive mode, read where the log version is at least this.
MODE_FROM_LOG_VERSION = 8
DIVE_MODES = {
    0: "closed_circuit",
    1: "open_circuit",
    2: "gauge",
    4: "semi_closed",
    5: "closed_circuit",
    6: "open_circuit",
    7: "freedive",
}
# Opening 5's sample interval, read where the log version is at least this, else the
# device's fixed 10 s.
INTERVAL_FROM_LOG_VERSION = 9
DEFAULT_INTERVAL = 10_000

# Opening 2's deco model.
DECO_GF, DECO_VPM, DECO_VPM_GFS, DECO_DCIEM = 0, 1, 2, 3
DECO_NAMES = {DECO_VPM: "VPM-B", DECO_VPM_GFS: "VPM-B/GFS", DECO_DCIEM: "DCIEM"}

# Opening 3's water density, kg/m³, onto §6.4a's three settings — which are Shearwater's three.
SALINITY = {1000: "fresh", 1020: "en13319", 1030: "salt"}

# The surface pressure §6.4a allows, in bar.
MIN_SURFACE_PRESSURE, MAX_SURFACE_PRESSURE = Decimal("0.4"), Decimal("1.2")

# The gas slots: ten, of which the last five are diluents.
GAS_SLOTS = 10
FIRST_DILUENT = 5

# The final record's model codes, only to check them against the name the source wrote; a
# code is never a model (`docs/converting.md`, *The Devices table*). The codes the layout's
# references agree on.
MODEL_CODES = {
    2: "Predator",
    3: "Petrel",
    4: "Nerd",
    5: "Perdix",
    6: "Perdix AI",
    7: "Nerd 2",
    8: "Teric",
    9: "Peregrine",
    10: "Petrel 3",
    11: "Perdix 2",
    12: "Tern",
    14: "Perdix 3",
}

# Unit factors. Depth is tenths of the log's unit; an imperial log's foot is 0.3048 m.
METRES_PER_FOOT = Decimal("0.3048")
BAR_PER_PSI = Decimal("0.0689475729")
TANK_PSI_PER_UNIT = 2
MAX_CYLINDER_PRESSURE = Decimal(350)
MILLIBAR_PER_BAR = Decimal(1000)

# The `dive_details` columns this reader reads or knows to pass over: the join key and the
# housekeeping, the typed columns, and the floats Shearwater Cloud computes. Every other
# column is something the diver typed, and waits for a file that fills one in.
DETAIL_COLUMNS_READ = frozenset({"DiveId", "DiveNumber", "SerialNumber", "TankProfileData"})
DETAIL_COLUMNS_PASSED = frozenset(
    {
        "DataVersion",
        "FileName",
        "LastModified",
        "MainTimestampReliable",
        "DiveDate",
        "Depth",
        "DiveLengthTime",
        "AverageDepth",
        "AverageTemp",
        "MinTemp",
        "MaxTemp",
        "EndGF99",
    }
)


class ShearwaterDbError(ConverterError):
    """The input could not be read as a Shearwater Cloud database."""


class MalformedShearwaterDbError(ShearwaterDbError):
    """The input carries SQLite's header and is not a database SQLite can read."""


def declared_size(data: bytes) -> int:
    """What a Shearwater Cloud database's dive logs declare they unpack to, unpacking nothing.

    The sum the reader spends against the caller's `InflationBudget` before it inflates a
    log, for a caller holding several files that wants to budget the batch before converting
    any of them. Logs the reader would not unpack — another storage format, a declared length
    of zero or past the per-log ceiling — are not counted. Raises what `convert` raises for a
    file that is not one.
    """
    with _Database(data) as db:
        return sum(row.declared for row in _unpacked(db, db.log_rows()))


def _unpacked(db: _Database, rows: list[_LogRow]) -> list[_LogRow]:
    """The rows whose logs the reader inflates: stored as the device's log, not deleted."""
    deleted = db.deleted()
    return [row for row in rows if row.unpacks and row.log_id not in deleted]


class ShearwaterDbAdapter:
    """The registry's view of this reader: what it claims, and how it converts."""

    format: str = FORMAT
    suffixes: tuple[str, ...] = (".db",)
    namespace: uuid_pkg.UUID = SHEARWATER_DB_ID_NAMESPACE

    def sniff(self, head: bytes) -> bool:
        """Whether a bounded head of bytes opens a SQLite database.

        The container rather than the export: the schema text that would tell a Cloud
        database from another SQLite file sits on leaf pages tens of kilobytes in, never in
        the head. This is the registry's only SQLite reader, so claiming the header is the
        way `zip` is claimed by its magic, and `convert` refuses a database that is not a
        Cloud export with a sentence saying so.
        """
        return head.startswith(SQLITE_MAGIC)

    def convert(self, data: bytes, *, exported_at: datetime, scope: Scope) -> Conversion:
        """Convert one Shearwater Cloud database into DiveJSON, one dive per device log.

        Raises `ShearwaterDbError` for a database that is not a Cloud export,
        `MalformedShearwaterDbError` for one SQLite cannot read, `SourceTooLargeError` for
        one whose logs declare more than the budget, or `NonConformingOutputError`.
        """
        with _Database(data) as db:
            return _Converter(db, exported_at=exported_at, scope=scope).run()


SHEARWATER_DB = ShearwaterDbAdapter()


# -- the sample, shared with the XML export ----------------------------------------------


@dataclass(frozen=True, slots=True)
class Tank:
    """One tank word of one sample: a pressure in psi, or the reason there is none."""

    psi: int | None = None
    status: str | None = None


@dataclass(frozen=True, slots=True)
class Sample:
    """One sample as the dive computer recorded it, its sentinels already read as absence.

    In the log's own units — tenths of a metre or of a foot, whole degrees Celsius or
    Fahrenheit, minutes — because the database and the XML export of one dive state the same
    figures and the comparison between them is made here, before any scale is applied. A
    `None` is a field the sample carries no reading for; `docs/shearwater-db-mapping.md`
    lists which value of each field means that.
    """

    depth: int | None = None
    stop_depth: int | None = None
    stop_time: int | None = None
    ndl: int | None = None
    tts: int | None = None
    ppo2: int | None = None
    cell_ppo2: bool = False
    oxygen: int | None = None
    helium: int | None = None
    closed_circuit: bool | None = None
    temperature: int | None = None
    cells: tuple[int | None, int | None, int | None] = (None, None, None)
    battery_voltage: int | None = None
    battery_percent: int | None = None
    setpoint: int | None = None
    tanks: tuple[Tank, ...] = ()
    gas_time: int | None = None
    sac: int | None = None
    cns: int | None = None
    ceiling: int | None = None
    gradient_factor: int | None = None
    at_plus_five: int | None = None
    circuit: str | None = None


def stop_and_ndl(stop_depth: int, minutes: int) -> tuple[int | None, int | None, int | None]:
    """The next stop's depth and time and the no-decompression time, from one sample's pair.

    One byte serves both: the next stop's time where a stop depth is recorded, the
    no-decompression time where it is zero. A zero then is no reading — the first sample of
    seven of the owner's eight logs writes it at the surface, with the device's cap of 99 on
    the next, and Shearwater's own UDDF export writes no no-decompression time there.
    """
    if stop_depth:
        return stop_depth, minutes, None
    return None, None, minutes or None


def computed_ppo2(hundredths: int, *, from_cells: bool) -> tuple[int | None, bool]:
    """The ppO₂ the computer worked out, and whether the byte averaged the cells instead.

    A zero is no reading: a ppO₂ the computer computed is never nothing, and the one zero in
    the owner's eight logs sits on a surface sample whose depth and times are zero beside it.
    """
    if from_cells:
        return None, bool(hundredths)
    return hundredths or None, False


def decoded_sample(record: bytes, *, cell_mask: int) -> Sample:
    """One `0x01` record as a `Sample`. Offsets and scales are the mapping document's table."""
    depth, stop_depth, tts = struct.unpack_from(">HHH", record, 1)
    status = record[12]
    stop, stop_time, ndl = stop_and_ndl(stop_depth, record[10])
    ppo2, cell_ppo2 = computed_ppo2(record[7], from_cells=bool(status & STATUS_EXTERNAL_PPO2))
    cells = tuple(
        value if value or cell_mask & (1 << index) else None
        for index, value in enumerate((record[13], record[15], record[16]))
    )
    temperature = struct.unpack_from(">b", record, 14)[0]
    if temperature < 0:
        # The wrap the layout's references give for a below-zero reading; no file in hand
        # is cold enough to check it.
        temperature = min(temperature + 102, 0)
    return Sample(
        depth=depth,
        stop_depth=stop,
        stop_time=stop_time,
        ndl=ndl,
        tts=tts,
        ppo2=ppo2,
        cell_ppo2=cell_ppo2,
        oxygen=record[8],
        helium=record[9],
        closed_circuit=not status & STATUS_OPEN_CIRCUIT,
        temperature=temperature,
        cells=cells,  # type: ignore[arg-type]
        battery_voltage=struct.unpack_from(">H", record, 17)[0] or None,
        battery_percent=record[11],
        setpoint=record[19] or None,
        tanks=(_tank(struct.unpack_from(">H", record, 28)[0]), _tank(struct.unpack_from(">H", record, 20)[0])),
        gas_time=record[22] if record[22] < GAS_TIME_STATUS else None,
        sac=_word(record, 30, below=SAC_STATUS),
        cns=record[23],
        ceiling=record[24] or None,
        gradient_factor=None if record[25] == NO_GRADIENT_FACTOR else record[25],
        at_plus_five=_word(record, 26, below=NO_AT_PLUS_FIVE),
    )


def _word(record: bytes, offset: int, *, below: int) -> int | None:
    value = struct.unpack_from(">H", record, offset)[0]
    return value if value < below else None


def _tank(word: int) -> Tank:
    """A tank word: a status at or above `TANK_STATUS`, else twelve bits of 2 psi units.

    The high four bits below the status range are the transmitter's battery, which has no
    place in a logbook. A pressure of zero is no reading, as the layout's references treat it.
    """
    if word >= TANK_STATUS:
        return Tank(status=TANK_STATUSES.get(word, OTHER_TANK_STATUS))
    psi = (word & 0x0FFF) * TANK_PSI_PER_UNIT
    return Tank(psi=psi or None)


# -- the profile, shared with the XML export ---------------------------------------------


@dataclass(slots=True)
class Gas:
    """One cylinder of a dive, beside the mix the samples name it by."""

    member: dict[str, Any]
    number: int
    oxygen: int
    helium: int
    diluent: bool = False


# The sample fields the format has no member for, each reported once per dive in which it
# carries a reading, under the label a diver knows it by.
NOT_KEPT: tuple[tuple[str, str], ...] = (
    ("stop_depth", "The next decompression stop's depth and time"),
    ("cells", "The oxygen cells' millivolts"),
    ("setpoint", "The setpoint"),
    ("battery_voltage", "The battery voltage"),
    ("battery_percent", "The battery charge"),
    ("gas_time", "The remaining gas time"),
    ("at_plus_five", "The time to surface in five minutes"),
    ("circuit", "The circuit the dive computer shows"),
)


def build_profile(
    timed: Sequence[tuple[int | None, Sample]],
    *,
    gases: list[Gas],
    imperial: bool,
    dciem: bool,
    note: Reporter,
    where: str,
    dive: str,
    noun: str,
    time_member: str,
) -> dict[str, Any] | None:
    """§6.5's Profile out of a dive's samples, reporting what has no place in it.

    `timed` pairs each sample with its millisecond on the axis. `gases` are the dive's
    cylinders, and a tank series no cylinder takes adds one to the list. `dive` is the dive's
    document path, for a finding's `member`.
    """
    recording_path = f"{dive}/recordings/0"
    path = f"{recording_path}/profile"
    axis = SampleAxis(note, where, noun=noun, time_member=time_member, path=path)
    for at, sample in timed:
        axis.offer(at, sample)

    names = ("depth", "ceiling", "temperature", "ndl", "tts", "ppo2", "cns", "gradient_factor")
    channels = {name: Channel(name) for name in names}
    tanks: dict[int, Channel] = {}
    statuses: dict[int, list[str]] = {}
    carried: set[str] = set()
    from_cells = False
    sac = False
    events: list[dict[str, Any]] = []
    unmatched = False
    previous: tuple[int, int] | None = None
    out_of_range = 0

    for at, sample in axis.ordered():
        if sample.depth is not None:
            channels["depth"].record(at, _centimetres(sample.depth, imperial=imperial))
        if sample.temperature is not None:
            channels["temperature"].record(at, _hundredths_of_a_degree(sample.temperature, imperial=imperial))
        if sample.ndl is not None:
            channels["ndl"].record(at, sample.ndl * 60)
        if sample.tts is not None:
            channels["tts"].record(at, sample.tts * 60)
        if sample.ppo2 is not None:
            channels["ppo2"].record(at, sample.ppo2)
        from_cells = from_cells or sample.cell_ppo2
        if sample.cns is not None:
            channels["cns"].record(at, sample.cns * int(TENTHS_PER_UNIT))
        if not dciem:
            if sample.gradient_factor is not None:
                channels["gradient_factor"].record(at, sample.gradient_factor)
            if sample.ceiling is not None:
                channels["ceiling"].record(at, _centimetres(sample.ceiling * 10, imperial=imperial))
        elif sample.ceiling is not None or sample.gradient_factor is not None:
            carried.add("dciem")
        for index, tank in enumerate(sample.tanks):
            if tank.psi is not None:
                bar = Decimal(tank.psi) * BAR_PER_PSI
                if bar <= MAX_CYLINDER_PRESSURE:
                    tanks.setdefault(index, Channel("pressures")).record(at, rounded(bar * TENTHS_PER_UNIT))
                else:
                    out_of_range += 1
            elif tank.status is not None and tank.status not in statuses.setdefault(index, []):
                statuses[index].append(tank.status)
        for attribute, _ in NOT_KEPT:
            value = getattr(sample, attribute)
            if value is not None and value != (None, None, None):
                carried.add(attribute)
        sac = sac or sample.sac is not None

        if sample.oxygen or sample.helium:
            mix = (sample.oxygen or 0, sample.helium or 0)
            if previous is not None and mix != previous:
                target = _gas_for(gases, mix, sample.closed_circuit)
                event: dict[str, Any] = {"time": at, "type": "gas_switch"}
                if target is None:
                    unmatched = True
                else:
                    event["gas_number"] = target.number
                events.append(event)
            previous = mix

    for attribute, label in NOT_KEPT:
        if attribute in carried:
            note(
                where,
                f"{label}, which the dive computer records with each sample, has no place in the logbook; it is "
                "left out.",
                "dropped",
                code="device_reading_not_kept",
                member=path,
            )
    if sac:
        note(
            where,
            "The surface air consumption the dive computer records with each sample has no place in the logbook; "
            "it is left out.",
            "dropped",
            code="air_consumption_not_kept",
            member=path,
        )
    if "dciem" in carried:
        note(
            where,
            "Under the DCIEM model the dive computer records a safe ascent depth rather than a decompression "
            "ceiling and gradient factor; it has no place in the logbook and is left out.",
            "dropped",
            code="device_reading_not_kept",
            member=path,
        )
    if from_cells:
        note(
            where,
            "Where the oxygen cells supplied the ppO₂, the dive computer records their average rather than a ppO₂ "
            "it worked out, so those readings are left out with the cells.",
            "dropped",
            code="device_reading_not_kept",
            member=f"{path}/ppo2",
        )
    if unmatched:
        note(
            where,
            "A gas switch names a mix none of the dive's gases has; it is kept without saying which cylinder.",
            "dropped",
            code="gas_switch_target_unknown",
            member=f"{path}/events",
        )
    for index, found in sorted(statuses.items()):
        if index in tanks:
            continue
        for status in found:
            note(
                where,
                f"Tank {index + 1} records no pressure, {status}.",
                "absent",
                code="tank_pressure_not_recorded",
                member=f"{path}/pressures",
            )
    if out_of_range:
        one = out_of_range == 1
        note(
            where,
            f"{out_of_range} {'sample records' if one else 'samples record'} a tank pressure above the "
            f"{MAX_CYLINDER_PRESSURE} bar a cylinder can hold; {'that reading is' if one else 'those readings are'} "
            "left out.",
            "dropped",
            code="tank_pressure_readings_out_of_range",
            member=f"{path}/pressures",
        )

    pressures = _tank_cylinders(tanks, gases, note=note, where=where, dive=dive)
    return axis.profile(channels, pressures=pressures, events=events)


def _centimetres(tenths: int, *, imperial: bool) -> int:
    if imperial:
        return rounded(Decimal(tenths) / 10 * METRES_PER_FOOT * CENTIMETRES_PER_METRE)
    return tenths * 10


def _hundredths_of_a_degree(degrees: int, *, imperial: bool) -> int:
    if imperial:
        return rounded((Decimal(degrees) - 32) * 5 / 9 * HUNDREDTHS_PER_DEGREE)
    return degrees * int(HUNDREDTHS_PER_DEGREE)


def _gas_for(gases: Sequence[Gas], mix: tuple[int, int], closed_circuit: bool | None) -> Gas | None:
    """The cylinder a switch to `mix` lands on, a diluent preferred on closed circuit."""
    matching = [gas for gas in gases if (gas.oxygen, gas.helium) == mix]
    for gas in matching:
        if closed_circuit is None or gas.diluent == closed_circuit:
            return gas
    return matching[0] if matching else None


def _tank_cylinders(
    tanks: dict[int, Channel], gases: list[Gas], *, note: Reporter, where: str, dive: str
) -> tuple[tuple[int, Channel], ...]:
    """Which cylinder each tank's series hangs on: **untested**, no file in hand transmits.

    The rule is positional — the first tank on the first cylinder, the second on the second —
    and a tank past the last cylinder makes a cylinder of its own, carrying the series and
    nothing else, rather than being given to a cylinder the file never tied it to.
    """
    pressures = []
    for index, channel in sorted(tanks.items()):
        if index < len(gases):
            pressures.append((gases[index].number, channel))
            continue
        number = max((gas.number for gas in gases), default=-1) + 1
        added = Gas(member={"gas_number": number}, number=number, oxygen=0, helium=0)
        gases.append(added)
        note(
            where,
            "The samples carry tank pressures for a tank no gas of the dive is tied to, so the readings make a "
            "cylinder of their own, with no gas, size or start and end pressure.",
            "absent",
            code="cylinder_from_tank_pressures",
            member=f"{dive}/cylinders/{len(gases) - 1}",
        )
        pressures.append((number, channel))
    return tuple(pressures)


# -- the database -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _LogRow:
    """One `log_data` row before its log is fetched: what it says about itself."""

    rowid: int
    index: int
    log_id: str
    file_name: str | None
    format: Any
    format_version: Any
    stored: int | None
    declared: int
    is_blob: bool

    @property
    def unpacks(self) -> bool:
        """Whether the reader would inflate this row's log."""
        return self.refusal is None

    @property
    def refusal(self) -> str | None:
        """Why the reader leaves this row's log packed, as a clause for the report."""
        if self.format != LOG_FORMAT:
            return "is stored in a form other than the dive computer's own log"
        if self.format_version != LOG_FORMAT_VERSION:
            return "is stored in a version of the dive computer's log that is not read"
        if not self.is_blob or self.stored is None or self.stored < 4:
            return "holds no dive computer log"
        if self.declared == 0:
            return "declares that it is empty"
        if self.declared > MAX_LOG_SIZE or self.stored > MAX_LOG_SIZE + MAX_PAGE_SIZE:
            return f"declares {self.declared} bytes, more than the {MAX_LOG_SIZE} a dive computer's log is read to"
        return None


# The tables this reader queries, and the columns each must have to be one it can read.
LOG_COLUMNS = ("log_id", "file_name", "format", "format_version", "data_bytes_1")


class _Database:
    """A Cloud database opened in memory, with sqlite.org's precautions for an untrusted file.

    Used as a context manager so that the connection — and the copy of the file it holds — is
    released whatever the conversion does.
    """

    def __init__(self, data: bytes) -> None:
        if not data.startswith(SQLITE_MAGIC):
            raise MalformedShearwaterDbError("the file does not begin as a SQLite database does")
        if 2 in data[18:20]:
            # A write-ahead-log header, which an in-memory copy cannot open: it has no `-wal`
            # beside it, so the file is read as the rollback database it then is.
            data = data[:18] + b"\x01\x01" + data[20:]
        self.connection = sqlite3.connect(":memory:")
        self._steps = 0
        self._budget = max(MIN_CALLS, len(data) // BYTES_PER_CALL)
        # Closed on every refusal below, not left to the collector: a connection that is
        # garbage-collected open is a `ResourceWarning` on Python 3.13 and later.
        try:
            try:
                self.connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_LOG_SIZE + MAX_PAGE_SIZE)
                self.connection.set_progress_handler(self._progress, PROGRESS_STEP)
                self.connection.deserialize(data)
                for pragma in ("trusted_schema=OFF", "query_only=ON", "cell_size_check=ON", "mmap_size=0"):
                    self.connection.execute(f"PRAGMA {pragma}")
            except sqlite3.Error as error:
                raise _malformed(error) from error
            if self.rows("PRAGMA quick_check") != [("ok",)]:
                raise MalformedShearwaterDbError("the file is a damaged SQLite database, and is not read")
            self.tables = self._tables()
        except BaseException:
            self.connection.close()
            raise

    def __enter__(self) -> _Database:
        return self

    def __exit__(self, *_: object) -> None:
        self.connection.close()

    def _progress(self) -> int:
        self._steps += 1
        return 1 if self._steps > self._budget else 0

    def rows(self, sql: str, parameters: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
        """Every row of one statement, under the statement budget."""
        self._steps = 0
        try:
            return self.connection.execute(sql, parameters).fetchall()
        except sqlite3.OperationalError as error:
            if "interrupted" in str(error):
                raise MalformedShearwaterDbError(
                    "reading the file took far longer than a dive log database of its size can, and was stopped"
                ) from error
            raise _malformed(error) from error
        except sqlite3.Error as error:
            raise _malformed(error) from error

    def _tables(self) -> dict[str, list[str]]:
        """The tables this reader may query, each with its columns, checked to be plain.

        A view, a virtual table or a table with a generated column would run code the file
        wrote inside the reader's own `SELECT`, so a database declaring one where a Cloud
        export has a table is refused outright. `log_data` is the one a Cloud export cannot
        lack.
        """
        wanted = ("log_data", "dive_details", "DeletedLogs", "SWC_TableVersion")
        found: dict[str, list[str]] = {}
        for kind, name, rootpage, sql in self.rows("SELECT type, name, rootpage, sql FROM sqlite_master"):
            if name not in wanted:
                continue
            plain = (
                kind == "table"
                and isinstance(rootpage, int)
                and rootpage > 0
                and not str(sql or "").lstrip().upper().startswith("CREATE VIRTUAL")
            )
            columns = self.rows(f'PRAGMA table_xinfo("{name}")') if plain else []
            if not plain or any(column[6] in (2, 3) for column in columns):
                raise ShearwaterDbError(
                    "the file is a SQLite database whose dive tables are not plain tables, so it is not read"
                )
            found[name] = [column[1] for column in columns]
        if "log_data" not in found or any(column not in found["log_data"] for column in LOG_COLUMNS):
            raise ShearwaterDbError(
                "the file is a SQLite database but not a Shearwater Cloud export: it holds no table of dive "
                "computer logs"
            )
        return found

    def log_rows(self) -> list[_LogRow]:
        rows = self.rows(
            "SELECT rowid, log_id, file_name, format, format_version, length(data_bytes_1), "
            "substr(data_bytes_1, 1, 4), typeof(data_bytes_1) FROM log_data ORDER BY rowid"
        )
        read = []
        for index, (rowid, log_id, file_name, fmt, version, stored, head, kind) in enumerate(rows):
            declared = struct.unpack("<I", head)[0] if isinstance(head, bytes) and len(head) == 4 else 0
            read.append(
                _LogRow(
                    rowid=rowid,
                    index=index,
                    log_id=str(log_id),
                    file_name=file_name if isinstance(file_name, str) else None,
                    format=fmt,
                    format_version=version,
                    stored=stored if isinstance(stored, int) else None,
                    declared=declared,
                    is_blob=kind == "blob",
                )
            )
        return read

    def log(self, row: _LogRow) -> tuple[bytes, Any, Any]:
        """One row's compressed log and its two sidecars."""
        sidecars = [column for column in ("data_bytes_2", "data_bytes_3") if column in self.tables["log_data"]]
        selected = ", ".join(["data_bytes_1", *sidecars]) or "data_bytes_1"
        found = self.rows(f"SELECT {selected} FROM log_data WHERE rowid = ?", (row.rowid,))[0]
        values = dict(zip(["data_bytes_1", *sidecars], found))
        return values["data_bytes_1"], _json(values.get("data_bytes_2")), _json(values.get("data_bytes_3"))

    def details(self) -> list[dict[str, Any]]:
        if "dive_details" not in self.tables:
            return []
        columns = self.tables["dive_details"]
        listed = ", ".join(f'"{column}"' for column in columns)
        return [dict(zip(columns, row)) for row in self.rows(f"SELECT {listed} FROM dive_details ORDER BY rowid")]

    def deleted(self) -> set[str]:
        if "DiveID" not in self.tables.get("DeletedLogs", []):
            return set()
        return {str(value) for (value,) in self.rows('SELECT "DiveID" FROM "DeletedLogs"') if value is not None}

    def version(self) -> str | None:
        if "DbVersion" not in self.tables.get("SWC_TableVersion", []):
            return None
        (value,) = self.rows('SELECT max("DbVersion") FROM "SWC_TableVersion"')[0]
        return str(value) if isinstance(value, int) else None


def _malformed(error: sqlite3.Error) -> MalformedShearwaterDbError:
    return MalformedShearwaterDbError(f"the file begins as a SQLite database does and cannot be read as one — {error}")


def _json(value: Any) -> Any:
    """A sidecar column's JSON, or nothing: a sidecar is only ever a cross-check."""
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(value, str) or len(value) > MAX_PAGE_SIZE:
        return None
    try:
        return json.loads(value)
    except ValueError:
        return None


# -- the log ------------------------------------------------------------------------------


class _Unreadable(Exception):
    """A log that cannot be read, with the clause the report says why in."""


@dataclass(slots=True)
class _Log:
    """One device log, decoded: its header fields and its samples, in record order."""

    openings: dict[int, bytes] = field(default_factory=dict)
    closings: dict[int, bytes] = field(default_factory=dict)
    final: bytes | None = None
    samples: list[bytes] = field(default_factory=list)
    info_events: int = 0
    tissues: int = 0
    validity: int = 0
    unread_samples: int = 0
    unknown: int = 0

    @property
    def log_version(self) -> int:
        return self.openings[4][16]

    @property
    def imperial(self) -> bool:
        return self.openings[0][8] == 1


def _inflated(blob: bytes, declared: int) -> bytes:
    """A log's records, out of its four-byte length and its one gzip member.

    Bounded to the declared length plus one, which is how the decompressor is made to notice
    a member that runs over, and required to end exactly there with its checksum read. Bytes
    after the member exist in most logs and carry nothing.
    """
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        body = inflater.decompress(blob[4:], declared + 1)
    except zlib.error as error:
        raise _Unreadable("is not a log that unpacks") from error
    if len(body) != declared or not inflater.eof:
        raise _Unreadable("does not unpack to the length it declares")
    if declared % RECORD_SIZE:
        raise _Unreadable("does not unpack to whole records")
    return body


def _decoded(body: bytes) -> _Log:
    """A log's records, walked by their type byte. The required set is checked here."""
    if body[:2] == b"\xff\xff":
        raise _Unreadable("is in the older Predator layout, which is not read")
    log = _Log()
    for offset in range(0, len(body), RECORD_SIZE):
        record = body[offset : offset + RECORD_SIZE]
        kind = record[0]
        if record == ZERO_RECORD:
            continue
        if kind == SAMPLE:
            log.samples.append(record)
        elif kind in OPENING:
            log.openings.setdefault(kind - OPENING.start, record)
        elif kind in CLOSING:
            log.closings.setdefault(kind - CLOSING.start, record)
        elif kind == FINAL:
            log.final = log.final or record
        elif kind == INFO_EVENT:
            log.info_events += 1
        elif kind in TISSUES:
            log.tissues += 1
        elif kind in VALIDITY:
            log.validity += 1
        elif kind in (FREEDIVE_SAMPLE, AVELO_SAMPLE):
            log.unread_samples += 1
        else:
            log.unknown += 1
    if any(index not in log.openings for index in REQUIRED_OPENINGS) or 0 not in log.closings or log.final is None:
        raise _Unreadable("is missing records every dive computer log carries")
    if log.openings[0][8] not in (0, 1):
        raise _Unreadable("records units that are neither metric nor imperial")
    return log


# -- converting it ------------------------------------------------------------------------


class _Converter:
    def __init__(self, db: _Database, *, exported_at: datetime, scope: Scope) -> None:
        self.db = db
        self.exported_at = exported_at
        self.scope = scope
        self.notes: list[Note] = []
        self.identities = Identities(SHEARWATER_DB_ID_NAMESPACE, scope, self.note)

    def note(self, where: str, message: str, kind: NoteKind, *, code: str, member: str | None = None) -> None:
        """One line of the report, at a path into the source this conversion read."""
        self.notes.append(Note(self.scope.where(where), message, kind, code=code, member=member))

    def run(self) -> Conversion:
        rows = self.db.log_rows()
        # Spent before any log inflates, on what the logs that would inflate declare: a file
        # past the budget is refused whole, rather than read until it runs out.
        self.scope.inflation.spend(sum(row.declared for row in _unpacked(self.db, rows)), what="the file's dive logs")

        details = self.db.details()
        by_id = {str(row.get("DiveId")): row for row in details}
        deleted = self.db.deleted()

        dives: list[dict[str, Any]] = []
        for row in rows:
            dive = self.read_dive(row, by_id.get(row.log_id), deleted, len(dives))
            if dive is not None:
                dives.append(dive)

        logged = {row.log_id for row in rows}
        for index, row in enumerate(details):
            if str(row.get("DiveId")) not in logged:
                self.note(
                    f"dive_details/{index}",
                    "Shearwater Cloud lists a dive with no dive computer log beside it; it is left out.",
                    "dropped",
                    code="dive_without_log",
                )

        document: dict[str, Any] = header(self.exported_at)
        if dives:
            document["dives"] = dives
        provenance: dict[str, Any] = {"converted_from": FORMAT}
        version = self.db.version()
        if version is not None:
            provenance["shearwater_db_version"] = version
        document["extensions"] = {PRODUCER_KEY: provenance}

        if self.scope.validates_alone:
            issues = validate_document(document)
            if issues:
                raise NonConformingOutputError(issues)
        return Conversion(document, tuple(self.notes), self.scope.sources(document))

    # -- one dive ----------------------------------------------------------------

    def read_dive(
        self, row: _LogRow, details: dict[str, Any] | None, deleted: set[str], position: int
    ) -> dict[str, Any] | None:
        where = f"dive/{row.index}"
        named = repr(row.file_name or row.log_id)
        if row.log_id in deleted:
            self.note(
                where,
                f"Shearwater Cloud marks the log {named} as deleted, so the dive is left out.",
                "dropped",
                code="dive_log_deleted",
            )
            return None
        refusal = row.refusal
        try:
            if refusal is not None:
                raise _Unreadable(refusal)
            blob, started, summary = self.db.log(row)
            log = _decoded(_inflated(blob, row.declared))
        except _Unreadable as reason:
            self.note(
                where,
                f"The log {named} {reason}, so the dive is left out.",
                "dropped",
                code="dive_log_unreadable",
            )
            return None

        claimed, carried = self.identities.for_record("dive", row.log_id, where, row.index)
        if claimed is None or not carried:
            return None
        path = f"dives/{position}"
        opening0, closing0 = log.openings[0], log.closings[0]
        ticks = struct.unpack_from(">I", opening0, 12)[0]
        dive: dict[str, Any] = {"uuid": claimed}

        number = self.read_number(details, where, path)
        if number is not None:
            dive["number"] = number
        dive["started_at"] = self.read_started_at(log, ticks, where, path)

        seconds = struct.unpack(">I", b"\x00" + closing0[6:9])[0]
        if recorded(seconds, record="dive", member="submerged_time"):
            dive["submerged_time"] = seconds
        else:
            self.note(
                where, "The log records a dive time of 0 s, which is no dive time; read as not recorded.",
                "absent", code="dive_time_zero", member=f"{path}/submerged_time",
            )
        tenths = struct.unpack_from(">H", closing0, 4)[0]
        if recorded(tenths, record="dive", member="max_depth"):
            depth = Decimal(tenths) / 10
            dive["max_depth"] = float(depth * METRES_PER_FOOT if log.imperial else depth)
        else:
            self.note(
                where, "The log records a maximum depth of 0, which is no depth; read as not recorded.",
                "absent", code="depth_zero", member=f"{path}/max_depth",
            )

        gases = self.read_gases(log, where, path)
        model = self.read_deco_model(log, where, path)
        profile = build_profile(
            self.timed(log),
            gases=gases,
            imperial=log.imperial,
            dciem=log.openings[2][18] == DECO_DCIEM,
            note=self.note,
            where=where,
            dive=path,
            noun="sample",
            time_member="time",
        )
        self.report_records(log, where, path)
        self.report_logbook(details, where, path)
        if gases:
            dive["cylinders"] = [gas.member for gas in gases]

        built = recording(
            device=self.read_device(row, log, details, where, path),
            mode=self.read_mode(log, where, path),
            deco_model=model,
            salinity=self.read_salinity(log, where, path),
            readouts=self.read_readouts(log, where, path),
            profile=profile,
        )
        if built is not None:
            dive["recordings"] = [built]
        self.cross_check(log, ticks, started, summary, details, where)
        return dive

    def timed(self, log: _Log) -> Iterator[tuple[int, Sample]]:
        """Each sample at its ordinal times the interval, the first at 0.

        By ordinal among the samples, so the info event between the first two does not move
        the second; the interval is opening 5's where the log version states one.
        """
        interval = DEFAULT_INTERVAL
        if log.log_version >= INTERVAL_FROM_LOG_VERSION and 5 in log.openings:
            interval = struct.unpack_from(">H", log.openings[5], 23)[0] or DEFAULT_INTERVAL
        mask = log.openings[3][6]
        for ordinal, record in enumerate(log.samples):
            yield ordinal * interval, decoded_sample(record, cell_mask=mask)

    def read_number(self, details: dict[str, Any] | None, where: str, path: str) -> int | None:
        """`dive_details.DiveNumber`, the logbook's number for the dive — the diver's, not the device's."""
        raw = None if details is None else details.get("DiveNumber")
        if raw is None or str(raw).strip() == "":
            return None
        text = str(raw).strip()
        if re.fullmatch(r"-?\d+", text):
            return int(text)
        self.note(
            where, "Shearwater Cloud's dive number is not a number, and is left out.", "dropped",
            code="not_a_number", member=f"{path}/number",
        )
        return None

    def read_started_at(self, log: _Log, ticks: int, where: str, path: str) -> str:
        """The opening ticks as a wall clock with no offset.

        A log of version 9 or later carries the time zone the computer was set to in opening
        5. It is not applied: what its daylight-saving flag adds is not settled by any file
        in hand, so the start stays the wall clock and the report says the zone was there.
        """
        started = datetime.fromtimestamp(ticks, tz=timezone.utc).replace(tzinfo=None).isoformat()
        opening5 = log.openings.get(5)
        zone = (
            struct.unpack_from(">i", opening5, 26)[0]
            if opening5 is not None and log.log_version >= INTERVAL_FROM_LOG_VERSION
            else 0
        )
        if zone:
            self.note(
                where,
                "The dive computer records the time zone it was set to, which is not read yet; the start is kept as "
                "the local time it showed.",
                "dropped",
                code="time_zone_not_read",
                member=f"{path}/started_at",
            )
        else:
            self.note(
                where,
                "The file gives the start time with no time zone, so it is kept as a local time.",
                "absent",
                code="start_time_zone_missing",
                member=f"{path}/started_at",
            )
        return started

    def read_gases(self, log: _Log, where: str, path: str) -> list[Gas]:
        """One cylinder per enabled gas slot, in slot order; the slot is the gas number.

        Oxygen is opening 0's bytes 20 to 29; helium is opening 0's 30 and 31 for the first
        two slots and opening 1's 1 to 8 for the rest; opening 4's bytes 17 and 18 are the
        enabled mask. A 99/0 is 99, never promoted to 100.
        """
        opening0, opening1 = log.openings[0], log.openings[1]
        mask = struct.unpack_from(">H", log.openings[4], 17)[0]
        oxygen = list(opening0[20:30])
        helium = list(opening0[30:32]) + list(opening1[1:9])
        gases: list[Gas] = []
        disabled = 0
        for slot in range(GAS_SLOTS):
            if not (oxygen[slot] or helium[slot]):
                continue
            if not mask & (1 << slot):
                disabled += 1
                continue
            index = len(gases)
            member: dict[str, Any] = {}
            for key, value in (("oxygen", oxygen[slot]), ("helium", helium[slot])):
                if value <= 100:
                    member[key] = float(value)
                else:
                    self.note(
                        where, f"A gas records {value} percent, outside the 0 to 100 a mix can be; it is left out.",
                        "dropped", code="gas_percent_out_of_range", member=f"{path}/cylinders/{index}/{key}",
                    )
            if member.get("oxygen", 0.0) + member.get("helium", 0.0) > 100:
                self.note(
                    where, "Oxygen and helium add up to more than 100 percent, which no mix can; both are left out.",
                    "dropped", code="gas_over_100_percent", member=f"{path}/cylinders/{index}",
                )
                member = {}
            member["gas_number"] = slot
            gases.append(Gas(member, slot, oxygen[slot], helium[slot], diluent=slot >= FIRST_DILUENT))
        if disabled:
            self.note(
                where,
                f"{disabled} {'gas is' if disabled == 1 else 'gases are'} set on the dive computer and switched off; "
                "left out.",
                "dropped",
                code="disabled_gases",
                member=f"{path}/cylinders",
            )
        return gases

    def read_device(
        self, row: _LogRow, log: _Log, details: dict[str, Any] | None, where: str, path: str
    ) -> dict[str, Any] | None:
        """The final record's serial and firmware, the model the log is named for, the brand.

        The model comes from `log_data.file_name`, the name the device gave the log —
        `Perdix 3[D9772626]#8 …` — and never from the final record's model code, which is
        only checked against it.
        """
        final = log.final
        assert final is not None
        recording_path = f"{path}/recordings/0"
        serial = f"{struct.unpack_from('>I', final, 2)[0]:08X}"
        bcd = final[10]
        firmware = None
        if bcd >> 4 < 10 and bcd & 0x0F < 10:
            firmware = str((bcd >> 4) * 10 + (bcd & 0x0F))
        else:
            self.note(
                where, "The dive computer's firmware version is not written as a decimal number; it is left out.",
                "dropped", code="not_a_number", member=f"{recording_path}/device/firmware",
            )
        model = None
        if row.file_name and "[" in row.file_name:
            model = row.file_name.split("[", 1)[0].strip() or None
        coded = MODEL_CODES.get(final[13])
        if model and coded and not (model.casefold().startswith(coded.casefold()) or coded.casefold().startswith(
            model.casefold()
        )):
            self.note(
                where,
                f"The dive computer's model code names a {coded!r}, where the log is named for a {model!r}; the "
                "name is kept.",
                "dropped",
                code="model_code_disagrees",
                member=f"{recording_path}/device/model",
            )
        return device(
            {
                "brand": BRAND,
                "model": model,
                "serial": serial,
                "firmware": firmware,
                "dive_number": struct.unpack_from(">H", log.openings[0], 2)[0],
            },
            note=self.note,
            where=where,
            path=recording_path,
        )

    def read_mode(self, log: _Log, where: str, path: str) -> str | None:
        if log.log_version < MODE_FROM_LOG_VERSION:
            return None
        stated = log.openings[4][1]
        mode = DIVE_MODES.get(stated)
        if mode is None:
            self.note(
                where,
                f"The dive computer records the dive mode {stated}, which is not one that can be read; the mode is "
                "left empty rather than assumed.",
                "dropped",
                code="dive_mode_unknown",
                member=f"{path}/recordings/0/mode",
            )
        return mode

    def read_deco_model(self, log: _Log, where: str, path: str) -> dict[str, Any] | None:
        """Opening 2's model: Bühlmann with opening 0's pair, or the named VPM-B and DCIEM."""
        opening2 = log.openings[2]
        stated, conservatism = opening2[18], opening2[19]
        recording_path = f"{path}/recordings/0"
        if stated == DECO_GF:
            members: dict[str, Any] = {
                "algorithm": "buhlmann",
                "gf_low": log.openings[0][4],
                "gf_high": log.openings[0][5],
            }
        elif stated in (DECO_VPM, DECO_VPM_GFS):
            members = {"name": DECO_NAMES[stated], "conservatism": conservatism}
        elif stated == DECO_DCIEM:
            members = {"name": DECO_NAMES[stated]}
        else:
            self.note(
                where,
                f"The dive computer records the decompression model {stated}, which cannot be named; it is left out.",
                "dropped",
                code="deco_model_unknown",
                member=f"{recording_path}/deco_model",
            )
            return None
        return deco_model(members, note=self.note, where=where, path=recording_path)

    def read_salinity(self, log: _Log, where: str, path: str) -> str | None:
        density = struct.unpack_from(">H", log.openings[3], 3)[0]
        salinity = SALINITY.get(density)
        if salinity is None:
            self.note(
                where,
                f"The dive computer's water density setting is {density}, which is none of fresh, EN 13319 or salt "
                "water; it is left out.",
                "dropped",
                code="water_setting_unknown",
                member=f"{path}/recordings/0/salinity",
            )
        return salinity

    def read_readouts(self, log: _Log, where: str, path: str) -> dict[str, float]:
        """The surface pressure and the CNS the dive began and ended on, the computer's own."""
        readouts: dict[str, float] = {}
        millibar = struct.unpack_from(">H", log.openings[1], 16)[0]
        bar = Decimal(millibar) / MILLIBAR_PER_BAR
        if MIN_SURFACE_PRESSURE <= bar <= MAX_SURFACE_PRESSURE:
            readouts["surface_pressure"] = float(bar)
        else:
            self.note(
                where,
                f"The log records a surface pressure of {bar} bar, outside the {MIN_SURFACE_PRESSURE} to "
                f"{MAX_SURFACE_PRESSURE} bar air pressure can be at a dive site; it is left out.",
                "dropped",
                code="surface_pressure_out_of_range",
                member=f"{path}/recordings/0/surface_pressure",
            )
        # Unsigned on the device, so the schema's floor is asked only to keep the rule one place.
        for member, record in (("cns_start", log.openings[0]), ("cns_end", log.closings[0])):
            percent = struct.unpack_from(">H", record, 10)[0]
            if recorded(percent, record="recording", member=member):
                readouts[member] = float(percent)
        return readouts

    def report_records(self, log: _Log, where: str, path: str) -> None:
        """The records with no place in the logbook, one line each for the dive that has them."""
        recording_path = f"{path}/recordings/0"
        for count, label in (
            (log.tissues, "The tissue loadings the dive computer records"),
            (log.info_events, "The events the dive computer records beside its samples"),
            (log.validity, "The oxygen cells' validity records"),
            (log.unread_samples, "The freedive and Avelo samples the dive computer records"),
        ):
            if count:
                self.note(
                    where,
                    f"{label} have no place in the logbook; they are left out.",
                    "dropped",
                    code="device_reading_not_kept",
                    member=recording_path,
                )
        if log.unknown:
            self.note(
                where,
                f"The log carries {log.unknown} {'record' if log.unknown == 1 else 'records'} of a kind that is not "
                "read; left out.",
                "dropped",
                code="record_types_unknown",
                member=recording_path,
            )
        for records, side in ((log.openings, "entry"), (log.closings, "exit")):
            fix = records.get(9)
            if fix is not None and log.log_version >= 17 and fix[16] in (2, 3):
                self.note(
                    where,
                    f"The dive computer records an {side} position, which is not read yet; it is left out.",
                    "dropped",
                    code="device_reading_not_kept",
                    member=f"{path}/{side}_position",
                )

    def report_logbook(self, details: dict[str, Any] | None, where: str, path: str) -> None:
        """Every filled `dive_details` column this reader does not read yet, by its own name.

        What Shearwater Cloud puts in Location, Buddy, the GNSS fixes and the rest is unknown
        until a file that fills them in exists, so none is mapped on a guess.
        """
        if details is None:
            return
        for column, value in details.items():
            if column in DETAIL_COLUMNS_READ or column in DETAIL_COLUMNS_PASSED:
                continue
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            self.note(
                where,
                f"What Shearwater Cloud's {column} field records is not read yet, and is left out.",
                "dropped",
                code="logbook_detail_not_read",
                member=path,
            )

    def cross_check(
        self, log: _Log, ticks: int, started: Any, summary: Any, details: dict[str, Any] | None, where: str
    ) -> None:
        """Shearwater Cloud's own summaries of the dive, against the log they summarise.

        None of them is read into the document: the log is what the device wrote. A summary
        that disagrees with it is reported, so the disagreement is not lost.
        """
        closing0 = log.closings[0]
        final = log.final
        assert final is not None
        checks: list[tuple[str, Any, Any]] = []
        if isinstance(started, dict):
            checks.append(("the start time", started.get("DIVE_START_TIME"), ticks))
        if isinstance(summary, dict):
            seconds = struct.unpack(">I", b"\x00" + closing0[6:9])[0]
            checks.append(("the dive time", summary.get("DiveTimeInSeconds"), seconds))
            if not log.imperial:
                tenths = struct.unpack_from(">H", closing0, 4)[0]
                stated = summary.get("MaxDepth")
                checks.append(
                    ("the maximum depth", None if not isinstance(stated, (int, float)) else round(stated * 10), tenths)
                )
        if details is not None:
            checks.append(
                ("the serial number", details.get("SerialNumber"), f"{struct.unpack_from('>I', final, 2)[0]:08X}")
            )
            tank = _first_tank(details.get("TankProfileData"))
            if tank is not None:
                surface = tank.get("SurfacePressureMBar")
                checks.append(
                    (
                        "the surface pressure",
                        None if not isinstance(surface, (int, float)) else round(surface),
                        struct.unpack_from(">H", log.openings[1], 16)[0],
                    )
                )
                density = struct.unpack_from(">H", log.openings[3], 3)[0]
                checks.append(("the water density", tank.get("Salinity"), density))
        for label, theirs, ours in checks:
            if theirs is None or theirs == "" or theirs == ours:
                continue
            self.note(
                where,
                f"Shearwater Cloud's own summary of this dive gives {label} as {theirs!r}, where the dive computer's "
                f"log gives {ours!r}; the log is what is kept.",
                "dropped",
                code="summary_disagrees",
            )


def _first_tank(raw: Any) -> dict[str, Any] | None:
    found = _json(raw)
    if not isinstance(found, dict):
        return None
    tanks = found.get("TankData")
    if isinstance(tanks, list) and tanks and isinstance(tanks[0], dict):
        return tanks[0]
    return None
