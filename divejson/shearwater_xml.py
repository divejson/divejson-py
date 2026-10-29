"""Reading Shearwater Cloud's XML export of one dive into DiveJSON.

Shearwater Cloud Desktop exports a dive as XML: a `<dive version="3">` root holding one
`<diveLog>`, whose children are the header the application decoded out of the dive
computer's log and a `<diveLogRecords>` list with one `<diveLogRecord>` per sample. It is
Shearwater's own decoding of the bytes `shearwater_db.py` decodes, so this reader turns each
record into the same `Sample` and builds the profile through the same rules; what differs is
the file. `docs/shearwater-xml-mapping.md` is the prose companion, and it points at the
database document for every column the two share.

Three things about the file shape what is below.

**Its declaration lies.** It says `encoding="utf-16"` over bytes that are ASCII, so a parser
handed the bytes refuses the file. The bytes are decoded as UTF-8 here and re-encoded with
the declaration rewritten to say so, and the result still goes through `xmlsource.parse_xml`,
the package's one XML entry point, which refuses a `<!DOCTYPE>` before anything expands.

**Its dates are culture-formatted.** `startDate` is written the way the exporting machine
writes dates — `9/21/2026 10:48:17 AM` on the files in hand — with no epoch beside it. A date
whose fields are both 12 or below reads either way round; it is read month first and the
report names the other reading, so a diver on a day-first machine can catch the swap.

**Several columns are words.** The tank pressures, the gas time and the SAC are strings —
`AI is off`, `N/A`, `Not Diving` — and are read as readings only where the string is a number.
"""

from __future__ import annotations

import re
import uuid as uuid_pkg
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal
from typing import Any

from .converter import (
    PRODUCER_KEY,
    Conversion,
    ConverterError,
    Identities,
    NonConformingOutputError,
    Note,
    NoteKind,
    Scope,
    decimal_of,
    deco_model,
    device,
    header,
    recorded,
    recording,
    rounded,
)
from .shearwater_db import (
    BRAND,
    DECO_DCIEM,
    DECO_GF,
    DECO_NAMES,
    DECO_VPM,
    DECO_VPM_GFS,
    MAX_SURFACE_PRESSURE,
    METRES_PER_FOOT,
    MILLIBAR_PER_BAR,
    MIN_SURFACE_PRESSURE,
    Gas,
    Sample,
    Tank,
    build_profile,
    computed_ppo2,
    stop_and_ndl,
)
from .validate import validate_document
from .xmlsource import attribute, child, children, parse_xml, root_name, text

__all__ = [
    "SHEARWATER_XML",
    "SHEARWATER_XML_ID_NAMESPACE",
    "MalformedShearwaterXmlError",
    "ShearwaterXmlAdapter",
    "ShearwaterXmlError",
]

FORMAT = "shearwater_xml"

# uuid5(NAMESPACE_URL, "https://divejson.org/ns/shearwater_xml"). Fixed forever:
# `docs/shearwater-xml-mapping.md` *Identity* records the value as normative for any port.
SHEARWATER_XML_ID_NAMESPACE = uuid_pkg.UUID("4ea587e4-cff2-5b16-8bd0-ba264823f5b6")

# One file is one dive written by one computer.
DIVE = "dives/0"
RECORDING = f"{DIVE}/recordings/0"
ROOT = "dive"

# The root's start tag and the first child the sniff asks for. `<dive>` is a name the DM5
# export's root has too, there in a datacontract namespace; this root declares none of its
# own and carries a `version`, and its first element child is `<diveLog>`. The attributes
# stop at a `<` as well as a `>`, which no start tag can hold, so a search tried at every
# `<dive` reads each stretch of the head once rather than once per tag.
_START_TAG = re.compile(r"<(?:[\w.-]+:)?dive(\s[^<>]*)?>", re.IGNORECASE)
_DEFAULT_NAMESPACE = re.compile(r"\sxmlns\s*=")
_VERSION = re.compile(r"\sversion\s*=")
_DIVE_LOG = re.compile(r"<(?:[\w.-]+:)?diveLog[\s/>]")

# The XML declaration's encoding, rewritten to the encoding the bytes are read in.
_DECLARED_ENCODING = re.compile(rb"\A(<\?xml[^>]*?encoding\s*=\s*)([\"'])[^\"']*\2")

# `startDate` as Shearwater writes it in any culture that orders a date's three numbers
# with slashes: two fields in either order and a four-digit year, a time, and a meridiem
# where the culture writes one.
_DATE = re.compile(
    r"\A(?P<first>\d{1,2})/(?P<second>\d{1,2})/(?P<year>\d{4})\s+"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second_of>\d{2}))?"
    r"(?:\s*(?P<meridiem>[AaPp])\.?\s*[Mm]\.?)?\Z"
)

# The tank strings that are a status rather than a reading, in the database's words.
TANK_WORDS = {"ai is off": "the air integration is off"}


class ShearwaterXmlError(ConverterError):
    """The input could not be read as a Shearwater Cloud XML export."""


class MalformedShearwaterXmlError(ShearwaterXmlError):
    """The input is not well-formed XML, or not a `<dive>` holding a `<diveLog>`."""


class ShearwaterXmlAdapter:
    """The registry's view of this reader: what it claims, and how it converts."""

    format: str = FORMAT
    suffixes: tuple[str, ...] = (".xml",)
    namespace: uuid_pkg.UUID = SHEARWATER_XML_ID_NAMESPACE

    def sniff(self, head: bytes) -> bool:
        """Whether a bounded head of bytes opens a Shearwater Cloud XML export.

        A `<dive>` root in no namespace of its own, carrying a `version`, with a `<diveLog>`
        after it. The DM5 export's `<Dive>` declares its datacontract namespace on the root,
        so the two never answer for one file.
        """
        if root_name(head) != ROOT:
            return False
        decoded = head.decode("utf-16", errors="replace") if head[:2] in (b"\xff\xfe", b"\xfe\xff") else head.decode(
            "latin-1"
        )
        tag = _START_TAG.search(decoded)
        if tag is None:
            return False
        attributes = tag.group(1) or ""
        return (
            not _DEFAULT_NAMESPACE.search(attributes)
            and bool(_VERSION.search(attributes))
            and bool(_DIVE_LOG.search(decoded, tag.end()))
        )

    def convert(self, data: bytes, *, exported_at: datetime, scope: Scope) -> Conversion:
        """Convert one Shearwater Cloud XML export into DiveJSON.

        Raises `DoctypeRefusedError`, `MalformedShearwaterXmlError`, `ShearwaterXmlError` for
        a start date that cannot be read, or `NonConformingOutputError`.
        """
        root = parse_xml(_as_utf8(data), root=ROOT, malformed=MalformedShearwaterXmlError)
        log = _child(root, "diveLog")
        if log is None:
            raise MalformedShearwaterXmlError("the file holds no <diveLog>, which is the dive")
        return _Converter(root, log, exported_at=exported_at, scope=scope).run()


SHEARWATER_XML = ShearwaterXmlAdapter()


def _as_utf8(data: bytes) -> bytes:
    """The bytes as UTF-8 with a declaration saying so, whatever the declaration claimed.

    A file that opens on a UTF-16 byte-order mark is UTF-16 and its declaration is true, so
    it goes to the parser as it is. Anything else is decoded as UTF-8, a byte-order mark
    allowed — the files in hand are ASCII under a declaration naming UTF-16.
    """
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data
    try:
        decoded = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise MalformedShearwaterXmlError("the file is not text in the encoding it can be read in") from error
    return _DECLARED_ENCODING.sub(rb'\1"utf-8"', decoded.encode("utf-8"), count=1)


def _child(element: ET.Element | None, name: str) -> ET.Element | None:
    return child(element, name.lower())


def _text(element: ET.Element | None, name: str) -> str | None:
    return text(_child(element, name))


def _number(element: ET.Element | None, name: str) -> Decimal | None:
    return decimal_of(_text(element, name))


def _whole(element: ET.Element | None, name: str, *, scale: int = 1) -> int | None:
    value = _number(element, name)
    return None if value is None else rounded(value * scale)


class _Converter:
    def __init__(self, root: ET.Element, log: ET.Element, *, exported_at: datetime, scope: Scope) -> None:
        self.root = root
        self.log = log
        self.exported_at = exported_at
        self.scope = scope
        self.notes: list[Note] = []
        self.identities = Identities(SHEARWATER_XML_ID_NAMESPACE, scope, self.note)

    def note(self, where: str, message: str, kind: NoteKind, *, code: str, member: str | None = None) -> None:
        """One line of the report, at a path into the source this conversion read."""
        self.notes.append(Note(self.scope.where(where), message, kind, code=code, member=member))

    def run(self) -> Conversion:
        dive = self.read_dive()
        document: dict[str, Any] = header(self.exported_at)
        if dive is not None:
            document["dives"] = [dive]
        provenance: dict[str, Any] = {"converted_from": FORMAT}
        version = attribute(self.root, "version")
        if version is not None:
            provenance["shearwater_xml_version"] = version
        document["extensions"] = {PRODUCER_KEY: provenance}

        if self.scope.validates_alone:
            issues = validate_document(document)
            if issues:
                raise NonConformingOutputError(issues)
        return Conversion(document, tuple(self.notes), self.scope.sources(document))

    def read_dive(self) -> dict[str, Any] | None:
        where = "dive/0"
        started_at = self.read_started_at(where)
        # The export records no id for its dive — `<number>` is the computer's counter — so
        # the dive takes the positional stand-in, prefixed by its archive member's name.
        claimed, carried = self.identities.for_record("dive", None, where, 0)
        if claimed is None or not carried:
            return None
        imperial = (_text(self.log, "imperialUnits") or "").lower() == "true"
        dive: dict[str, Any] = {"uuid": claimed, "started_at": started_at}

        seconds = _whole(self.log, "maxTime")
        if seconds is not None and recorded(seconds, record="dive", member="submerged_time"):
            dive["submerged_time"] = seconds
        elif seconds is not None:
            self.note(
                where, f"<maxTime> is {seconds} s, which is no dive time; read as not recorded.", "absent",
                code="dive_time_zero", member=f"{DIVE}/submerged_time",
            )
        depth = _number(self.log, "maxDepth")
        if depth is not None and recorded(depth, record="dive", member="max_depth"):
            dive["max_depth"] = float(depth * METRES_PER_FOOT if imperial else depth)
        elif depth is not None:
            self.note(
                where, f"<maxDepth> is {depth}, which is no depth; read as not recorded.", "absent",
                code="depth_zero", member=f"{DIVE}/max_depth",
            )

        records = children(_child(self.log, "diveLogRecords"), "divelogrecord")
        samples = [(self.time_of(record), self.sample(record, first=not index)) for index, record in enumerate(records)]
        gases = self.read_gases([sample for _, sample in samples])
        model_code = _whole(self.log, "decoModel")
        profile = build_profile(
            samples,
            gases=gases,
            imperial=imperial,
            dciem=model_code == DECO_DCIEM,
            note=self.note,
            where=where,
            dive=DIVE,
            noun="record",
            time_member="<currentTime>",
        )
        if gases:
            dive["cylinders"] = [gas.member for gas in gases]

        built = recording(
            device=self.read_device(where),
            deco_model=self.read_deco_model(model_code, where),
            readouts=self.read_readouts(where),
            profile=profile,
        )
        if built is not None:
            dive["recordings"] = [built]
        return dive

    def read_started_at(self, where: str) -> str:
        """`<startDate>` as a wall clock with no offset; a date it cannot read refuses the file."""
        raw = _text(self.log, "startDate")
        if raw is None:
            raise ShearwaterXmlError("the file records no <startDate>, and a dive cannot be logged without one")
        match = _DATE.match(raw)
        read = None if match is None else _wall_clock(match)
        if read is None:
            raise ShearwaterXmlError(f"<startDate> {raw!r} is not a date and time that can be read")
        started, other = read
        if other is not None:
            self.note(
                where,
                f"<startDate> {raw!r} reads either way round; it is read month first, as {started[:10]}, rather than "
                f"day first, as {other}.",
                "resolved",
                code="start_date_order_resolved",
                member=f"{DIVE}/started_at",
            )
        if match is not None and match["second_of"] is None:
            self.note(
                where, "<startDate> records no seconds; read as :00.", "absent",
                code="start_time_seconds_missing", member=f"{DIVE}/started_at",
            )
        self.note(
            where,
            "The file gives the start time with no time zone, so it is kept as a local time.",
            "absent",
            code="start_time_zone_missing",
            member=f"{DIVE}/started_at",
        )
        return started

    def time_of(self, record: ET.Element) -> int | None:
        """`<currentTime>`, already milliseconds."""
        return _whole(record, "currentTime")

    def sample(self, record: ET.Element, *, first: bool) -> Sample:
        """One `<diveLogRecord>` as the database's `Sample`, in the log's own units, `first`
        where it is the export's first record."""
        stop_depth = _whole(record, "firstStopDepth") or 0
        stop, stop_time, ndl = stop_and_ndl(stop_depth, _whole(record, "currentNdl"), first=first)
        if stop is not None:
            stop_time = _whole(record, "firstStopTime")
        external = (_text(record, "externalPPO2") or "0").lower() not in ("0", "false")
        ppo2, cell_ppo2 = computed_ppo2(_whole(record, "averagePPO2", scale=100) or 0, from_cells=external)
        return Sample(
            depth=_whole(record, "currentDepth", scale=10),
            stop_depth=stop,
            stop_time=stop_time,
            ndl=ndl,
            tts=_whole(record, "ttsMins"),
            ppo2=ppo2,
            cell_ppo2=cell_ppo2,
            oxygen=_whole(record, "fractionO2", scale=100),
            helium=_whole(record, "fractionHe", scale=100),
            temperature=_whole(record, "waterTemp"),
            cells=tuple(  # type: ignore[arg-type]
                _whole(record, f"sensor{index}Millivolts") or None for index in (1, 2, 3)
            ),
            battery_voltage=_whole(record, "batteryVoltage", scale=100) or None,
            tanks=tuple(_tank(_text(record, f"tank{index}pressurePSI")) for index in range(4)),
            gas_time=_whole(record, "gasTime"),
            sac=_whole(record, "sac"),
            circuit=_text(record, "currentCircuitSetting"),
        )

    def read_gases(self, samples: list[Sample]) -> list[Gas]:
        """One cylinder per distinct mix the records breathe, in the order each first appears.

        The export carries no header gas list, so the samples' own fractions are the only
        place a gas is named.
        """
        gases: list[Gas] = []
        for sample in samples:
            oxygen, helium = sample.oxygen or 0, sample.helium or 0
            if not (oxygen or helium) or any((gas.oxygen, gas.helium) == (oxygen, helium) for gas in gases):
                continue
            if not (0 <= oxygen <= 100 and 0 <= helium <= 100 and oxygen + helium <= 100):
                continue
            number = len(gases)
            gases.append(
                Gas({"oxygen": float(oxygen), "helium": float(helium), "gas_number": number}, number, oxygen, helium)
            )
        return gases

    def read_device(self, where: str) -> dict[str, Any] | None:
        """`<computerSerial>`, `<computerFirmware>` read as hexadecimal, `<number>`, the brand.

        No model: `<product>` is a code, and the export names the product nowhere.
        """
        firmware = None
        raw = _text(self.log, "computerFirmware")
        if raw is not None:
            if re.fullmatch(r"[0-9A-Fa-f]+", raw) and len(raw) <= 8:
                firmware = str(int(raw, 16))
                if firmware != raw:
                    self.note(
                        where,
                        f"<computerFirmware> is written in hexadecimal, so {raw!r} is read as firmware {firmware}.",
                        "resolved",
                        code="firmware_read_as_hexadecimal",
                        member=f"{RECORDING}/device/firmware",
                    )
            else:
                self.note(
                    where, "<computerFirmware> is not a number, and is left out.", "dropped",
                    code="not_a_number", member=f"{RECORDING}/device/firmware",
                )
        counter = _whole(self.log, "number")
        return device(
            {"brand": BRAND, "serial": _text(self.log, "computerSerial"), "firmware": firmware, "dive_number": counter},
            note=self.note,
            where=where,
            labels={"serial": "<computerSerial>", "dive_number": "<number>"},
            path=RECORDING,
        )

    def read_deco_model(self, code: int | None, where: str) -> dict[str, Any] | None:
        """`<decoModel>` as the database's opening 2 is read, with `<gfMin>`/`<gfMax>` beside it."""
        if code is None:
            return None
        if code == DECO_GF:
            members: dict[str, Any] = {"algorithm": "buhlmann", "gf_low": _whole(self.log, "gfMin"), "gf_high": _whole(
                self.log, "gfMax"
            )}
        elif code in (DECO_VPM, DECO_VPM_GFS):
            members = {"name": DECO_NAMES[code], "conservatism": _whole(self.log, "vpmbConservatism")}
        elif code == DECO_DCIEM:
            members = {"name": DECO_NAMES[code]}
        else:
            self.note(
                where,
                f"<decoModel> is {code}, a decompression model that cannot be named; it is left out.",
                "dropped",
                code="deco_model_unknown",
                member=f"{RECORDING}/deco_model",
            )
            return None
        return deco_model(
            members,
            note=self.note,
            where=where,
            labels={"gf_low": "<gfMin>", "gf_high": "<gfMax>"},
            path=RECORDING,
        )

    def read_readouts(self, where: str) -> dict[str, float]:
        """`<startSurfacePressure>` in millibar, and `<startCns>` and `<endCns>` in whole percent."""
        readouts: dict[str, float] = {}
        millibar = _number(self.log, "startSurfacePressure")
        if millibar is not None:
            bar = millibar / MILLIBAR_PER_BAR
            if MIN_SURFACE_PRESSURE <= bar <= MAX_SURFACE_PRESSURE:
                readouts["surface_pressure"] = float(bar)
            else:
                self.note(
                    where,
                    f"<startSurfacePressure> is {bar} bar, outside the {MIN_SURFACE_PRESSURE} to "
                    f"{MAX_SURFACE_PRESSURE} bar air pressure can be at a dive site; it is left out.",
                    "dropped",
                    code="surface_pressure_out_of_range",
                    member=f"{RECORDING}/surface_pressure",
                )
        for member, name in (("cns_start", "startCns"), ("cns_end", "endCns")):
            value = _number(self.log, name)
            if value is None:
                continue
            if recorded(value, record="recording", member=member):
                readouts[member] = float(value)
            else:
                self.note(
                    where, f"<{name}> is {value}, below zero, which it cannot be; it is left out.", "dropped",
                    code="oxygen_figure_negative", member=f"{RECORDING}/{member}",
                )
        return readouts


def _tank(raw: str | None) -> Tank:
    """A tank string: a number of psi, the air integration being off, or nothing at all."""
    if raw is None:
        return Tank()
    value = decimal_of(raw)
    if value is not None:
        psi = rounded(value)
        return Tank(psi=psi if psi > 0 else None)
    return Tank(status=TANK_WORDS.get(raw.strip().lower()))


def _wall_clock(match: re.Match[str]) -> tuple[str, str | None] | None:
    """A matched `startDate` as an ISO wall clock, and the day-first date where both read.

    A field above 12 settles which is the day. Where both are 12 or below the date is read
    month first, the shape of the files in hand, and the day-first reading is returned beside
    it — unless the two fields are equal, when both readings are one date.
    """
    first, second, year = int(match["first"]), int(match["second"]), int(match["year"])
    hour, minute = int(match["hour"]), int(match["minute"])
    seconds = int(match["second_of"] or 0)
    meridiem = (match["meridiem"] or "").lower()
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem == "p" else 0)
    if first > 12 and second > 12:
        return None
    month, day = (second, first) if first > 12 else (first, second)
    try:
        moment = datetime(year, month, day, hour, minute, seconds)
    except ValueError:
        return None
    other = None
    if first <= 12 and second <= 12 and first != second:
        try:
            other = datetime(year, second, first).date().isoformat()
        except ValueError:
            other = None
    return moment.isoformat(), other
