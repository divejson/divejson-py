"""Reading the Suunto app's JSON dive-log exports into DiveJSON.

The Suunto app writes one JSON file per activity, and a dive is `DeviceLog.Header` with
`DeviceLog.Samples[]` beside it. `docs/suunto-json-mapping.md` is the prose companion:
every member this module reads, every one it deliberately does not, and why. What is true
of every source format rather than of this one — the note kinds, identity, the way a zero
reads, the number bound, the sample axis — lives in `converter.py` and `series.py`, and
this module inherits it.

Four things shape what is below, and each of them is a place where reading the file the
obvious way gives a wrong dive.

**There are three header shapes, and the newest carries no gas block at all.** A D5-era
export keeps its cylinders under `Header.Diving.Gases`, in SI units — Pascal, cubic metres,
a 0-1 oxygen fraction. A 2026 Suunto Ocean export has no `Header.Diving` at all: what it
records about gas is `Samples[].Cylinders[]` — a `GasNumber` and a `Pressure` in Pascal —
and `Samples[].DiveEvents.GasSwitch`. The third shape is a header with neither, which
carries no gas data because there was none.

**On the Ocean shape a cylinder is listed because the diver switched to it, not because it
transmitted.** `DiveEvents.GasSwitch.GasNumber` is the only record the file keeps of which
cylinders were on the dive. Building the list from telemetry instead emits one cylinder for
a two-tank dive, and one cylinder carrying both pressures is the shape a gas-consumption
figure is derived from — so a stage bottle's pressure drop would be attributed to the whole
dive. Four of the nineteen Ocean files in hand switch to a second gas while only the first
slot ever sends a pressure, so their second cylinder carries no pressures at all, and that
is the honest answer rather than a defect.

**A reading from after the dive ended is not the dive's end pressure.** The transmitter
keeps reporting while the computer is still logging on the surface, so the last reading in
the file is whatever the tank read once the diver purged the regulator to break down their
kit. `Header.DiveTime` is the in-water time and `Header.Duration` the whole logged period;
they differ by minutes. Bounding the cylinder's first and last readings on `DiveTime` moves
the end pressure on every one of the nineteen Ocean files in hand, and on two of them it is
the difference between 53 and 76 bar and **0.14** — a diver who breathed their cylinder
dry, which is not what happened. The profile's own pressure channel is deliberately *not*
bounded: it is the telemetry the device recorded, and truncating it would drop real surface
readings the depth and temperature channels keep.

**A sample's own timestamp is the only thing that orders it.** The union of an Ocean
export's sample timestamps is not monotonic — adjacent entries go backwards by up to 0.7 s,
the separate sensor streams being appended out of order — so the last entry in the file is
not the last reading of the dive. That is `converting.md`'s ordering rule meeting the writer
that makes it obvious, and it is why the cylinder's extremes are taken over the samples'
recorded instants rather than over the profile's whole-second axis, which drops a reading
that shares its second with an earlier sample.
"""

from __future__ import annotations

import json
import math
import re
import uuid as uuid_pkg
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from .converter import (
    CENTIMETRES_PER_METRE,
    PRODUCER_KEY,
    TENTHS_PER_UNIT,
    Conversion,
    ConverterError,
    Identities,
    NonConformingOutputError,
    Note,
    NoteKind,
    Scope,
    decimal_of,
    header,
    position,
    record_inferred,
    recorded,
    rounded,
)
from .series import Channel, SampleAxis
from .validate import validate_document

__all__ = [
    "SUUNTO_JSON",
    "SUUNTO_JSON_ID_NAMESPACE",
    "MalformedSuuntoJsonError",
    "SuuntoJsonAdapter",
    "SuuntoJsonError",
]

# The format id this adapter registers under, which is also the name of the directory a
# conformance corpus keeps its pairs in. `suunto_json` rather than `suunto`, because the
# same vendor's DM5 desktop application exports a different format under the same name.
FORMAT = "suunto_json"

# uuid5(NAMESPACE_URL, "https://divejson.org/ns/suunto_json"). Fixed forever: changing it
# would renumber every document any released version of this converter has produced from a
# Suunto app export, and `docs/suunto-json-mapping.md` *Identity* records the value as
# normative for any port.
SUUNTO_JSON_ID_NAMESPACE = uuid_pkg.UUID("1766426d-4c62-54a6-bc80-264daeab1494")

# What the sniff looks for. JSON has no magic number, so the marker is the one member every
# one of these exports opens with — the whole file is a single `{"DeviceLog": {…}}` object,
# so the name lands within the first few bytes and cannot be missed inside a bounded head.
MARKER = b'"DeviceLog"'

# `Header.ActivityType` for a dive. The app exports every activity the watch records in this
# same shape, so a run and a swim reach this reader looking exactly like a dive until this
# is checked. 51 is what all 35 dive exports in hand carry.
DIVE_ACTIVITY = 51

# How many cylinders one dive may describe. Nothing else bounds it: on the Ocean shape the
# list is built from the distinct `GasNumber`s a file claims, and a file may claim any
# number of them.
MAX_CYLINDERS = 16

# The range §6.3 allows a cylinder pressure, and the bar this format's channel scale is in.
MAX_CYLINDER_PRESSURE = Decimal(350)

# The SI units this export writes, against the ones §6 holds. Every one of these is a place
# where reading the number as it stands gives a document that validates and describes a dive
# nobody took: a 0.21 oxygen fraction is not 0.21 percent, and 20 000 000 Pa is not 20 MPa
# of tank pressure in a member measured in bar.
PASCALS_PER_BAR = Decimal(100_000)
LITRES_PER_CUBIC_METRE = Decimal(1000)
PERCENT_PER_FRACTION = Decimal(100)
KELVIN_OFFSET = Decimal("273.15")

# Sample fixes are in **radians** while `DiveRouteOrigin` is in degrees, in the same file.
# See `_Converter.read_positions`.
DEGREES_PER_RADIAN = Decimal(180) / Decimal(str(math.pi))

# Six decimal places is about 11 cm, past what any consumer receiver resolves. The radian
# conversion is this converter's arithmetic on an irrational factor, so it is quantized the
# way `fit.py` quantizes its semicircles; a coordinate the file states in degrees is the
# source's own number and is carried exactly.
COORDINATE_PLACES = Decimal("0.000001")

# ISO 8601 as this exporter writes it, leniently. The seconds, the fraction and the offset
# are each optional because §5.2 allows a date-time without an offset and because a reader
# that insists on the shape one exporter happens to write is a reader that refuses the next
# one. The offset is accepted with or without its colon.
_TIMESTAMP = re.compile(
    r"\A(?P<date>\d{4}-\d{2}-\d{2})[Tt ]"
    r"(?P<hour>\d{2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?"
    r"(?P<fraction>\.\d+)?"
    r"(?P<offset>[Zz]|[+-]\d{2}:?\d{2})?\Z"
)

# The two sample members an event can arrive under, because the export generations
# disagree: the D5 shapes write `Events` and the 2026 Ocean writes `DiveEvents`, and the
# Ocean uses `Events` at the same time for activity bookkeeping (`Lap`, `Pause`,
# `ArrayBegin`) that has nothing to do with a dive. Reading only one of them loses every gas
# switch in one generation or the other.
EVENT_KEYS = ("Events", "DiveEvents")

# `Notify[].Type` onto the two §6.5 stop types, matched case-insensitively. A table rather
# than a cast: this vocabulary is Suunto's, and a value not listed here has to come out as
# nothing rather than be forced into the nearest type this format happens to have.
#
# Only the two the diver is being told to *do*. The corpus also carries "Deep Stop Ahead",
# "Safety Stop Ahead" and "Stop done", which are the prompt before and the confirmation
# after; marking all three would put three markers on one stop.
STOP_TYPES = {"deep stop": "deep_stop", "safety stop": "safety_stop"}

# The event families that become an `other` carrying the device's own wording — the ceiling
# breaks, the ppO2 and ascent-rate alarms. §6.5's `other` exists for exactly this, and
# re-spelling "Ceiling Broken" into a vocabulary of ours would say less.
ALERT_NAMES = ("Alarm", "Warning")

# `Gases[].State` onto §6.3's `role`. Nearly empty on purpose: "Primary" is the only value
# the 18 gases across the D5 exports in hand carry, and every value this table does not name
# has to come out as no role rather than as one this converter invented. Note this is not
# the `State` an event carries, which is the computer narrating its own mode.
GAS_ROLES = {"primary": "bottom"}


class SuuntoJsonError(ConverterError):
    """The input could not be read as a Suunto app export."""


class MalformedSuuntoJsonError(SuuntoJsonError):
    """The bytes are not a readable Suunto app export."""


class SuuntoJsonAdapter:
    """The registry's view of this reader: what it claims, and how it converts.

    An instance of this is what `registry.py` registers; everything else in this module is
    behind it.
    """

    format: str = FORMAT
    suffixes: tuple[str, ...] = (".json",)
    namespace: uuid_pkg.UUID = SUUNTO_JSON_ID_NAMESPACE

    def sniff(self, head: bytes) -> bool:
        """Whether a bounded head of bytes opens a Suunto app export.

        JSON carries no magic number, so the test is the shape: an object whose one
        top-level member is `DeviceLog`. A DiveJSON document is `.json` too and opens with
        `format`, so the suffix decides nothing here and the marker decides everything.
        """
        return head.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"{") and MARKER in head

    def convert(self, data: bytes, *, exported_at: datetime, scope: Scope) -> Conversion:
        """Convert one Suunto app export into DiveJSON.

        Raises `MalformedSuuntoJsonError` or `NonConformingOutputError`.
        """
        return _Converter(_parse(data), exported_at=exported_at, scope=scope).run()


SUUNTO_JSON = SuuntoJsonAdapter()


# -- reading the file -----------------------------------------------------------------


def _parse(data: bytes) -> dict[str, Any]:
    """The file's `DeviceLog`, or a refusal naming what is wrong with it.

    `parse_float=Decimal` is what makes the arithmetic below decimal from the source text
    on, which `converting.md` *Units and arithmetic* requires: `Decimal("0.21") * 100` is
    exactly `21` where the float route arrives at 21.000000000000004. `parse_constant`
    covers the same ground for the bare `Infinity`, `-Infinity` and `NaN` tokens Python's
    JSON reader accepts and RFC 8259 does not — as `Decimal`s they reach `_number`, which
    rejects them, rather than becoming float infinities that survive into a document.
    """
    try:
        document = json.loads(data, parse_float=Decimal, parse_constant=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MalformedSuuntoJsonError(f"this is not readable JSON — {error}") from error
    if not isinstance(document, dict):
        raise MalformedSuuntoJsonError("a Suunto app export is a JSON object, and this is not one")
    log = document.get("DeviceLog")
    if not isinstance(log, dict) or not isinstance(log.get("Header"), dict):
        raise MalformedSuuntoJsonError(
            "this JSON carries no DeviceLog.Header, so it is not a Suunto app export"
        )
    return log


def _number(value: Any) -> Decimal | None:
    """A source reading as a decimal, or `None` for anything that is not a usable one.

    `bool` is excluded before `int`, since Python makes `True` an integer and a `true` in a
    numeric member is a writer saying something other than 1.
    """
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return None
    return decimal_of(str(value))


def _text(value: Any) -> str | None:
    """A source string with its whitespace stripped, or `None` where there is no text.

    `converting.md`: text is compared after stripping, and a name of pure whitespace is no
    name. This export writes `""` into `Header.Notes` on every Ocean file, which is the
    empty-is-absent rule rather than a note the diver wrote.
    """
    return value.strip() or None if isinstance(value, str) else None


@dataclass(frozen=True, slots=True)
class _Moment:
    """One recorded instant: the text as written, and the instant it names.

    Both halves are needed and neither derives from the other. §5.2 asks that the recorded
    offset — and, under owner-settled policy, the recorded sub-second fraction — be
    preserved exactly, which is a fact about the *text*; placing a sample on the profile's
    axis is a fact about the instant.
    """

    text: str
    at: datetime


def _moment(value: Any) -> _Moment | None:
    """A recorded timestamp, or `None` for text that is not one.

    The text is rebuilt rather than passed through, so that a shape §5.2 does not admit —
    a `+0200` offset with no colon, a space where the `T` belongs, a lowercase `z` — comes
    out in the one spelling the format takes. The **fraction is carried through untouched**:
    the source recorded it, nothing in the format asks for it to be dropped, and §5.2 makes
    it OPTIONAL rather than forbidden, so truncating would discard a value the file states.

    `datetime.fromisoformat` is deliberately not the parser. On the Python floor this
    package supports it accepts only a two- or six-digit fraction and rejects `.5`, which is
    a property of one interpreter version rather than of the data.
    """
    if not isinstance(value, str):
        return None
    match = _TIMESTAMP.match(value.strip())
    if match is None:
        return None
    parts = match.groupdict()
    second = parts["second"] or "00"
    fraction = parts["fraction"] or ""
    offset = _offset(parts["offset"])
    if offset is None and parts["offset"] is not None:
        return None
    try:
        at = datetime(
            int(parts["date"][:4]),
            int(parts["date"][5:7]),
            int(parts["date"][8:]),
            int(parts["hour"]),
            int(parts["minute"]),
            int(second),
            # Padded to microseconds and truncated past them: a datetime cannot hold more,
            # and the `text` above is what preserves whatever the source actually wrote.
            int(f"{fraction[1:]:0<6}"[:6]) if fraction else 0,
            tzinfo=_zone(offset),
        )
    except ValueError:
        return None
    return _Moment(f"{parts['date']}T{parts['hour']}:{parts['minute']}:{second}{fraction}{offset or ''}", at)


def _offset(recorded_offset: str | None) -> str | None:
    """A recorded UTC offset in the one spelling §5.2 takes, or `None` for none."""
    if recorded_offset is None:
        return None
    if recorded_offset in ("Z", "z"):
        return "Z"
    body = recorded_offset.replace(":", "")
    return f"{body[:3]}:{body[3:]}" if len(body) == 5 else None


def _zone(offset: str | None) -> timezone | None:
    """The `tzinfo` an offset names, or `None` where the source recorded none."""
    if offset is None:
        return None
    if offset == "Z":
        return timezone.utc
    minutes = int(offset[1:3]) * 60 + int(offset[4:])
    return timezone(timedelta(minutes=-minutes if offset[0] == "-" else minutes))


def _elapsed(moment: datetime, origin: datetime) -> Decimal:
    """Seconds from `origin` to `moment`, on the wall clock where the two disagree.

    A file whose header records an offset and whose samples do not — or the reverse — is
    malformed in a way that has nothing to do with its profile, and subtracting the two
    raises. Falling back to the wall clock is exact whenever both sides share an offset,
    which is every file this exporter writes, and is defined rather than fatal when they do
    not.
    """
    if (moment.tzinfo is None) != (origin.tzinfo is None):
        moment, origin = moment.replace(tzinfo=None), origin.replace(tzinfo=None)
    return Decimal(str((moment - origin).total_seconds()))


@dataclass(slots=True)
class _Sample:
    """One `Samples[]` entry, read once and held for the second pass over the axis."""

    depth: Decimal | None = None
    ceiling: Decimal | None = None
    kelvin: Decimal | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    # Source gas number to Pascal, for the slots this sample carried a reading on.
    pressures: dict[int, Decimal] = field(default_factory=dict)
    # `(name, payload)` for every event member this sample carried, in file order.
    events: list[tuple[str, Any]] = field(default_factory=list)


# -- converting it --------------------------------------------------------------------


class _Converter:
    def __init__(self, log: dict[str, Any], *, exported_at: datetime, scope: Scope) -> None:
        self.log = log
        self.head: dict[str, Any] = log["Header"]
        self.samples: list[dict[str, Any]] = [
            sample for sample in (log.get("Samples") or []) if isinstance(sample, dict)
        ]
        self.exported_at = exported_at
        self.scope = scope
        self.notes: list[Note] = []
        self.identities = Identities(SUUNTO_JSON_ID_NAMESPACE, scope, self.note)
        self.inferred: list[str] = []

    # -- reporting ---------------------------------------------------------------

    def note(self, where: str, message: str, kind: NoteKind) -> None:
        self.notes.append(Note(self.scope.where(where), message, kind))

    def absent(self, member: str, source: str, where: str) -> None:
        self.note(where, f"the export records no {source}, so the dive has no {member}", "absent")

    # -- the run -----------------------------------------------------------------

    def run(self) -> Conversion:
        dive = self.read_dive()

        document: dict[str, Any] = header(self.exported_at)
        if dive is not None:
            document["dives"] = [dive]
        document["extensions"] = {PRODUCER_KEY: self.provenance()}

        if self.scope.validates_alone:
            issues = validate_document(document)
            if issues:
                raise NonConformingOutputError(issues)
        return Conversion(document, tuple(self.notes))

    def provenance(self) -> dict[str, Any]:
        """What the file said about itself, and which of its members were computed.

        `source_generator` is the **device**, not the app: this file is the app's rendering
        of what the computer on the diver's wrist recorded, and `Device.Name` is what that
        computer calls itself — "Suunto D5", or the name its owner gave an Ocean. The
        firmware under `Device.Info.SW` is that device's own version.
        """
        provenance: dict[str, Any] = {"converted_from": FORMAT}
        device = self.head.get("Device")
        device = device if isinstance(device, dict) else self.log.get("Device")
        if isinstance(device, dict):
            name = _text(device.get("Name"))
            if name is not None:
                generator: dict[str, Any] = {"name": name}
                info = device.get("Info")
                version = _text(info.get("SW")) if isinstance(info, dict) else None
                if version is not None:
                    generator["version"] = version
                provenance["source_generator"] = generator
        record_inferred(provenance, self.inferred)
        return provenance

    # -- the dive ----------------------------------------------------------------

    def read_dive(self) -> dict[str, Any] | None:
        where = "dive/0"
        if not self.is_a_dive(where):
            return None

        started = _moment(self.head.get("DateTime"))
        if started is None:
            self.note(
                where,
                "the header records no start time this converter can read, and a dive without one cannot "
                "be carried (spec §6.2); the dive is dropped",
                "dropped",
            )
            return None
        if started.at.tzinfo is None:
            self.note(
                where,
                "the header's start time records no UTC offset; the wall clock travels alone (spec §5.2)",
                "absent",
            )

        # This export records no id for its dive, so a dive takes the positional stand-in
        # `converting.md` defines — and the note that says so, since an identity that moves
        # when a file's order changes is a fact a diver may need (spec §5.3).
        claimed, carried = self.identities.for_record("dive", None, where, 0)
        if claimed is None or not carried:
            return None

        dive: dict[str, Any] = {"uuid": claimed, "started_at": started.text}
        diving = self.head.get("Diving")
        diving = diving if isinstance(diving, dict) else {}
        samples = self.axis(started.at, where)

        # In §6.2's own member order, so a converted dive reads down the schema.
        self.read_duration(dive, where)
        self.read_depths(dive, where)
        self.read_oxygen(diving, dive, where)
        self.read_surface_pressure(diving, dive, where)
        self.read_positions(samples, dive)

        cylinders, numbering = self.read_cylinders(diving, started.at, where)
        if cylinders:
            dive["cylinders"] = cylinders
        profile = self.read_profile(samples, cylinders, numbering, where)
        if profile is not None:
            dive["profile"] = profile
        return dive

    def is_a_dive(self, where: str) -> bool:
        """Whether this activity is one, on the header's own say-so.

        The app exports a run and a swim in this same shape, and nothing else in the file
        distinguishes them from a dive whose computer happened to record no depth. An
        activity that says it is something else is reported and skipped rather than
        converted into a dive with no readings.

        A header that states **no** `ActivityType` is read on: absence is not a claim, and
        `converting.md`'s first rule is that schema validity is never a precondition. Every
        one of the 35 dive exports in hand states 51.
        """
        recorded_type = self.head.get("ActivityType")
        if recorded_type is None or _number(recorded_type) == DIVE_ACTIVITY:
            return True
        self.note(
            where,
            f"the export records activity type {recorded_type!r}, and this reader carries dives "
            f"(type {DIVE_ACTIVITY}); the activity is dropped",
            "dropped",
        )
        return False

    def read_duration(self, dive: dict[str, Any], where: str) -> None:
        """`DiveTime` if the header states one, else `Duration`.

        The two are different quantities and the order matters. `DiveTime` is the time in
        the water; `Duration` is the whole period the computer logged, which begins before
        the diver gets in and ends after they are back on the boat — 4 001 s against 4 302 s
        on one file in hand. §6.2's `duration` is the dive's, so the in-water figure wins
        where the export states it, and the D5 shapes state only `Duration`.
        """
        for member, source in (("DiveTime", "DiveTime"), ("Duration", "Duration")):
            value = _number(self.head.get(member))
            if value is None:
                continue
            if not recorded(value, record="dive", member="duration"):
                self.note(
                    where,
                    f"the header records {source} as {value}, which is not a length of time a dive can "
                    "have; read as not recorded",
                    "absent",
                )
                continue
            dive["duration"] = rounded(value)
            return
        self.absent("duration", "DiveTime or Duration", where)

    def read_depths(self, dive: dict[str, Any], where: str) -> None:
        """`Depth.Max`, and the average under whichever name this generation writes.

        `DepthAverage` is the Ocean's spelling and `Depth.Avg` the D5's; they are the same
        quantity and no file in hand states both. Neither is derived here — this export
        summarises its own dive, so §6.2's depths are readings rather than something this
        converter computes off the profile.
        """
        depth = self.head.get("Depth")
        depth = depth if isinstance(depth, dict) else {}
        for member, source, value in (
            ("max_depth", "Depth.Max", _number(depth.get("Max"))),
            (
                "avg_depth",
                "DepthAverage or Depth.Avg",
                _first(_number(self.head.get("DepthAverage")), _number(depth.get("Avg"))),
            ),
        ):
            if value is None:
                self.absent(member.replace("_", " "), source, where)
                continue
            if not recorded(value, record="dive", member=member):
                self.note(
                    where,
                    f"the header records a {member.replace('_', ' ')} of {value} m, which devices write to "
                    "mean 'not recorded'; read as not recorded",
                    "absent",
                )
                continue
            dive[member] = float(value)
        maximum, average = dive.get("max_depth"), dive.get("avg_depth")
        if maximum is not None and average is not None and average > maximum:
            self.note(
                where,
                f"the header's average depth {average} m is deeper than its maximum {maximum} m, which "
                "cannot be; the average is dropped (spec §6.2)",
                "dropped",
            )
            del dive["avg_depth"]

    def read_oxygen(self, diving: dict[str, Any], dive: dict[str, Any], where: str) -> None:
        """The oxygen clock, out of `Diving.StartTissue` and `Diving.EndTissue`.

        **CNS is a 0-1 fraction here and §6.2 holds whole percent**, which is invisible
        until the same dive is read out of two Suunto exports: `EndTissue.CNS: 0.069` is the
        DM5 export's `<CnsEnd>7</CnsEnd>`. Carrying it unconverted would report a 69 %
        oxygen clock as 0.069 %. OTU needs no conversion — it is the same absolute count in
        both — and is carried at the precision the file states it, this export writing a
        full float32 where the desktop one rounds.

        Both members read a zero as an answer, which is the schema's decision rather than
        this reader's: §6.2 gives them `minimum: 0`, so the 0 a diver's first dive of the
        day starts on is a reading and not a placeholder.
        """
        for block, half in (("StartTissue", "start"), ("EndTissue", "end")):
            tissue = diving.get(block)
            tissue = tissue if isinstance(tissue, dict) else {}
            for member, value in (
                (f"cns_{half}", self.scaled(_number(tissue.get("CNS")), PERCENT_PER_FRACTION)),
                (f"otu_{half}", _number(tissue.get("OTU"))),
            ):
                if value is None:
                    continue
                if not recorded(value, record="dive", member=member):
                    self.note(
                        where,
                        f"the export records {member.replace('_', ' ')} as {value}, which is below what the "
                        "member can hold; dropped",
                        "dropped",
                    )
                    continue
                dive[member] = float(value)

    def read_surface_pressure(self, diving: dict[str, Any], dive: dict[str, Any], where: str) -> None:
        """`Diving.SurfacePressure`, in Pascal where §6.2 holds bar.

        §6.2 bounds the member at 0.4 to 1.2 bar, which is the range a barometer at a dive
        site can read; a value outside it is a device that recorded something other than a
        surface pressure, and is reported rather than clamped.
        """
        pascal = _number(diving.get("SurfacePressure"))
        if pascal is None:
            return
        bar = pascal / PASCALS_PER_BAR
        if not Decimal("0.4") <= bar <= Decimal("1.2"):
            self.note(
                where,
                f"the export records a surface pressure of {bar} bar, outside the 0.4 to 1.2 the format "
                "allows; dropped",
                "dropped",
            )
            return
        dive["surface_pressure"] = float(bar)

    def read_positions(self, samples: SampleAxis, dive: dict[str, Any]) -> None:
        """The fix on the way in and the fix on the way out, split at the deepest sample.

        The same rule every reader in this package applies — no fix is taken underwater, so
        the only question worth asking of one is which surface interval it belongs to, and
        the deepest sample is the split.

        **This export writes its coordinates in two units, in one file.** A sample's own
        `Latitude`/`Longitude` are radians; the `DiveRouteOrigin` block on the first sample
        is degrees. That is the exporter's doing and not a dialect to resolve: the two are
        different members, each with one unit, so neither is ambiguous and neither is a
        `resolved` finding. The origin matters because every satellite fix in the sample
        stream lands after the diver surfaced — a receiver has nothing to talk to until
        then — so without it these files yield an exit and no entry, while the app draws
        both pins from the same export.
        """
        fixed = [(second, sample) for second, sample in samples.ordered() if sample.latitude is not None]
        depths = [(second, sample.depth) for second, sample in samples.ordered() if sample.depth is not None]
        if not fixed or not depths:
            return

        # `max` keeps the first of equal values, so a flat profile pivots on its earliest
        # sample and every fix on it reads as an exit — except one landing exactly there,
        # which the `<=` below keeps as the entry.
        pivot = max(depths, key=lambda pair: pair[1])[0]
        before = [pair for pair in fixed if pair[0] <= pivot]
        after = [pair for pair in fixed if pair[0] > pivot]
        for member, chosen in (("entry_position", before[-1:]), ("exit_position", after[:1])):
            for second, sample in chosen:
                found = position(
                    sample.latitude,
                    sample.longitude,
                    note=self.note,
                    where=f"dive/0/sample/{second}",
                )
                if found is not None:
                    dive[member] = found

    # -- cylinders ---------------------------------------------------------------

    def read_cylinders(
        self, diving: dict[str, Any], origin: datetime, where: str
    ) -> tuple[list[dict[str, Any]], dict[int, int]]:
        """This dive's §6.3 Cylinders, and what each source gas number names.

        Returns the cylinders and a map from the **source's** gas number to the cylinder's
        position, which is what a pressure channel and a gas-switch marker are numbered by:
        §6.3 calls `gas_number` a label rather than an index into `cylinders[]`, and
        `converting.md` numbers a converted dive's cylinders from 0 in document order, so a
        source number is resolved through this map rather than passed through.

        The two shapes number differently and both are stated by the files themselves. A
        `Gases[]` block carries no number of its own, and the same dive's
        `Samples[].Cylinders[].GasNumber` reports **1** for its first entry — so a D5's
        numbering is one-based over the block's order, which the 2025 exports confirm to
        within a kilopascal: `Gases[0].StartPressure` 20 520 312 Pa against a first
        telemetry reading of 20 520 000 Pa on slot 1. An Ocean numbers from 0 and states the
        number on every reading and every switch.
        """
        gases = [gas for gas in (diving.get("Gases") or []) if isinstance(gas, dict)]
        if gases:
            capped = self.cap(gases, where)
            return (
                [self.declared_cylinder(gas, where) for gas in capped],
                {position + 1: position for position in range(len(capped))},
            )
        return self.reconstructed_cylinders(origin, where)

    def cap(self, values: list[Any], where: str) -> list[Any]:
        """The first `MAX_CYLINDERS` of a list of them, reporting anything past it."""
        if len(values) <= MAX_CYLINDERS:
            return values
        self.note(
            where,
            f"the export describes {len(values)} cylinders, and at most {MAX_CYLINDERS} are read from one "
            "dive; the rest are dropped",
            "dropped",
        )
        return values[:MAX_CYLINDERS]

    def declared_cylinder(self, gas: dict[str, Any], where: str) -> dict[str, Any]:
        """One `Diving.Gases[]` entry as a §6.3 Cylinder, converting SI units as it goes.

        Everything here is in the file's own units and none of them is the format's: Pascal
        against bar, cubic metres against litres, a 0-1 fraction against whole percent. A
        member the entry omits stays absent — an untransmitted backup cylinder really has no
        start pressure, and an export that never recorded a gas fraction has not recorded a
        0 % one.
        """
        cylinder: dict[str, Any] = {}
        volume = self.scaled(_number(gas.get("TankSize")), LITRES_PER_CUBIC_METRE)
        if volume is not None and recorded(volume, record="cylinder", member="volume"):
            cylinder["volume"] = float(volume)

        pressures = (
            self.bar(_number(gas.get("StartPressure")), where),
            self.bar(_number(gas.get("EndPressure")), where),
        )
        self.set_pressures(cylinder, pressures, where)

        blend: dict[str, float] = {}
        for member, source in (("oxygen", "Oxygen"), ("helium", "Helium")):
            percent = self.scaled(_number(gas.get(source)), PERCENT_PER_FRACTION)
            if percent is None:
                continue
            if not 0 <= percent <= 100:
                self.note(
                    where,
                    f"a gas records {member} at {percent} percent, outside the 0 to 100 a mix can be; dropped",
                    "dropped",
                )
                continue
            blend[member] = float(percent)
        if blend.get("oxygen", 0.0) + blend.get("helium", 0.0) > 100:
            self.note(
                where,
                "a gas's oxygen and helium sum above 100 percent, which no mix can; both are dropped "
                "(spec §6.3)",
                "dropped",
            )
            blend = {}
        elif not blend:
            self.note(
                where,
                "the export records no gas mixture for one of this dive's cylinders; absent means not "
                "recorded, never air (spec §6.3)",
                "absent",
            )
        cylinder.update(blend)

        po2 = _number(gas.get("PO2"))
        limit = None if po2 is None else po2 / PASCALS_PER_BAR
        if limit is not None:
            if Decimal("0.4") <= limit <= Decimal(2):
                cylinder["po2_limit"] = float(limit)
            else:
                self.note(
                    where,
                    f"a gas records a ppO₂ limit of {limit} bar, outside the 0.4 to 2.0 the format allows; "
                    "dropped",
                    "dropped",
                )
        role = GAS_ROLES.get((_text(gas.get("State")) or "").lower())
        if role is not None:
            cylinder["role"] = role
        return cylinder

    def reconstructed_cylinders(
        self, origin: datetime, where: str
    ) -> tuple[list[dict[str, Any]], dict[int, int]]:
        """The Ocean shape's cylinders, from its gas switches and its transmitter.

        **A cylinder is listed because the diver switched to it, not because it
        transmitted** — see this module's own docstring for why that distinction is the
        whole safety of a multi-gas dive. Switch order is chronological, so the back gas
        comes first and a deco gas follows, which is the order a logbook lists them in. A
        slot that transmitted without a recorded switch is appended after those: evidence
        of a tank is evidence of a tank, whichever way round it arrived.

        Only the pressures are real and nothing else is invented to fill the gap. This shape
        records no gas fraction, no tank size and no ppO₂ limit anywhere — the string
        `Oxygen` does not appear in any of the nineteen Ocean files in hand — so every
        cylinder here carries an `absent` finding saying so, because reporting air would be
        indistinguishable from having read it.
        """
        pressures = self.transmitted(origin)
        switched = self.switched()
        numbers = self.cap(switched + [number for number in sorted(pressures) if number not in switched], where)
        if not numbers:
            return [], {}

        cylinders: list[dict[str, Any]] = []
        for number in numbers:
            cylinder: dict[str, Any] = {}
            self.set_pressures(cylinder, pressures.get(number, (None, None)), where)
            self.note(
                where,
                "the export records no gas mixture for one of this dive's cylinders; absent means not "
                "recorded, never air (spec §6.3)",
                "absent",
            )
            cylinders.append(cylinder)
        return cylinders, {number: position for position, number in enumerate(numbers)}

    def switched(self) -> list[int]:
        """The gas numbers this dive switched to, in the order they were first used."""
        order: list[int] = []
        seen: set[int] = set()
        for sample in self.samples:
            for name, payload in _events(sample):
                if name != "GasSwitch" or not isinstance(payload, dict):
                    continue
                number = payload.get("GasNumber")
                if isinstance(number, bool) or not isinstance(number, int) or number in seen:
                    continue
                seen.add(number)
                order.append(number)
        return order

    def transmitted(self, origin: datetime) -> dict[int, tuple[Decimal | None, Decimal | None]]:
        """Each slot's first and last transmitter reading, bounded by `Header.DiveTime`.

        **Over the samples' own recorded instants, not over the profile's axis.** The axis
        rounds to whole seconds and keeps the first sample on each of them, and a cylinder
        reading that shares its second with an earlier sample of another channel is dropped
        by that rule — which is right for a channel and wrong for the extremes: on the file
        this reader is measured against it moves the start pressure from 211.625 bar to
        211.26562. So this walks every sample, and `None` readings are skipped rather than
        ending the series: an Ocean reports five slots on every sample with `Pressure: null`
        in the four nothing is paired to, and its final samples null out even the live one.

        **A reading from after the dive ended is dropped** — see this module's docstring.
        `DiveTime` is the bound; a header with none leaves the readings unbounded, which is
        weaker and never worse than not knowing. Deliberately not falling back to
        `Duration`: bounding a window by its own full length is not a bound.
        """
        end = self.dive_ended(origin)
        extremes: dict[int, tuple[tuple[datetime, Decimal], tuple[datetime, Decimal]]] = {}
        for sample in self.samples:
            moment = _moment(sample.get("TimeISO8601"))
            if moment is None:
                continue
            if end is not None and _elapsed(moment.at, end) > 0:
                continue
            for slot in sample.get("Cylinders") or []:
                if not isinstance(slot, dict):
                    continue
                number = slot.get("GasNumber")
                pascal = _number(slot.get("Pressure"))
                # A reading with no slot to attach it to is dropped rather than guessed at:
                # every real Ocean sample numbers all five of its slots.
                if pascal is None or isinstance(number, bool) or not isinstance(number, int):
                    continue
                reading = (moment.at, pascal)
                first, last = extremes.get(number, (reading, reading))
                extremes[number] = (
                    reading if reading[0] < first[0] else first,
                    reading if reading[0] >= last[0] else last,
                )
        return {
            number: (first[1] / PASCALS_PER_BAR, last[1] / PASCALS_PER_BAR)
            for number, (first, last) in extremes.items()
        }

    def dive_ended(self, origin: datetime) -> datetime | None:
        """When the dive itself ended, as opposed to when the device stopped logging."""
        dive_time = _number(self.head.get("DiveTime"))
        if dive_time is None:
            return None
        return origin + timedelta(seconds=float(dive_time))

    def set_pressures(
        self, cylinder: dict[str, Any], pressures: tuple[Decimal | None, Decimal | None], where: str
    ) -> None:
        """A cylinder's two pressures, with the two checks §6.3 asks for."""
        start, end = pressures
        if start is not None and not recorded(start, record="cylinder", member="start_pressure"):
            # §6.3 is explicit: a recorded zero start pressure is a device's absent-marker
            # rather than a measurement, and writers must not emit one.
            self.note(
                where,
                "a cylinder's start pressure is 0 bar, which devices write to mean 'not recorded'; read as "
                "not recorded (spec §6.3)",
                "absent",
            )
            start = None
        if start is not None and end is not None and end > start:
            self.note(
                where,
                f"a cylinder's end pressure {end} bar is above its start pressure {start} bar, which cannot "
                "be; the end pressure is dropped",
                "dropped",
            )
            end = None
        if start is not None:
            cylinder["start_pressure"] = float(start)
        if end is not None:
            cylinder["end_pressure"] = float(end)

    def bar(self, pascal: Decimal | None, where: str) -> Decimal | None:
        """A tank pressure in bar from the Pascal this export writes, range checked."""
        if pascal is None:
            return None
        return self.pressure(pascal / PASCALS_PER_BAR, where)

    def pressure(self, bar: Decimal | None, where: str) -> Decimal | None:
        if bar is None:
            return None
        if not 0 <= bar <= MAX_CYLINDER_PRESSURE:
            self.note(
                where,
                f"a cylinder pressure of {bar} bar is outside the 0 to 350 the format allows; dropped",
                "dropped",
            )
            return None
        return bar

    # -- the profile -------------------------------------------------------------

    def axis(self, origin: datetime, where: str) -> SampleAxis:
        """The dive's time axis, offered one merged sample per second the file recorded at.

        The origin is `Header.DateTime`, so the profile's seconds are elapsed time from the
        instant `started_at` names — and a sample before it is dropped and reported by the
        axis, which is the right answer for a stream whose entries arrive out of order.

        **Entries that land on one second are merged rather than one of them being
        dropped**, and that is the whole difference between a faithful profile and a
        quarter of one. This exporter appends its sensor streams as separate entries: on the
        file this reader is measured against, 7 477 entries carry a depth, a temperature, a
        satellite fix or a battery reading, almost never two of those at once, and they
        collide on the whole seconds §6.5 requires. Offering them one at a time leaves the
        axis choosing between a depth and a temperature recorded at the same instant, and it
        keeps 345 of the dive's 431 depths. Merging keeps all 431 — the count the same
        dive's FIT reading gives — because each of §6.5's channels carries its own times and
        two different channels at one second were never in competition.

        What is still a collision is one **channel** twice on a second, and that is reported
        per channel rather than per sample: a file whose streams overlap throughout would
        otherwise write thousands of identical lines saying one thing about the file.
        """
        axis = SampleAxis(self.note, where, noun="sample", time_member="TimeISO8601")
        merged: dict[int, _Sample] = {}
        collisions: dict[str, int] = {}
        undated = 0
        for raw in self.samples:
            moment = _moment(raw.get("TimeISO8601"))
            if moment is None:
                undated += 1
                continue
            second = rounded(_elapsed(moment.at, origin))
            standing = merged.get(second)
            if standing is None:
                merged[second] = self.sample(raw)
            else:
                _merge(standing, self.sample(raw), collisions)

        for _ in range(undated):
            axis.offer(None, None)
        for channel, count in sorted(collisions.items()):
            self.note(
                where,
                f"{count} {channel} readings land on a second the dive already has one at; the later reading "
                "is dropped, because the format's sample times are strictly increasing (spec §6.5)",
                "dropped",
            )
        for second, sample in merged.items():
            axis.offer(second, sample)
        return axis

    def sample(self, raw: dict[str, Any]) -> _Sample:
        """One `Samples[]` entry's readings, in the units the file states them in."""
        sample = _Sample(
            depth=_number(raw.get("Depth")),
            ceiling=_number(raw.get("Ceiling")),
            kelvin=_number(raw.get("Temperature")),
        )
        latitude, longitude = _number(raw.get("Latitude")), _number(raw.get("Longitude"))
        if latitude is not None or longitude is not None:
            sample.latitude = self.degrees(latitude)
            sample.longitude = self.degrees(longitude)
        else:
            origin = raw.get("DiveRouteOrigin")
            if isinstance(origin, dict):
                # Degrees here, radians two lines up, in the same file. See `read_positions`.
                sample.latitude = _number(origin.get("Latitude"))
                sample.longitude = _number(origin.get("Longitude"))
        for slot in raw.get("Cylinders") or []:
            if not isinstance(slot, dict):
                continue
            number, pascal = slot.get("GasNumber"), _number(slot.get("Pressure"))
            if pascal is not None and not isinstance(number, bool) and isinstance(number, int):
                sample.pressures.setdefault(number, pascal)
        sample.events = _events(raw)
        return sample

    def degrees(self, radians: Decimal | None) -> Decimal | None:
        """A sample fix's radians as degrees, quantized where the arithmetic ends."""
        if radians is None:
            return None
        return (radians * DEGREES_PER_RADIAN).quantize(COORDINATE_PLACES)

    def read_profile(
        self,
        samples: SampleAxis,
        cylinders: list[dict[str, Any]],
        numbering: dict[int, int],
        where: str,
    ) -> dict[str, Any] | None:
        """The sample stream as a §6.5 Profile.

        The channels sit on their own axes rather than one shared one: an Ocean dive writes
        7 477 samples of which 431 carry a depth and 4 294 a temperature, and padding either
        to the other's length would invent four thousand depths. That is `converting.md`'s
        no-padding rule meeting the writer that makes it obvious.

        **A ceiling of zero is not a ceiling.** This export writes `"Ceiling": 0` on every
        no-deco sample where the same vendor's desktop export writes `xsi:nil`; the ceiling
        is the depth a diver may not ascend above, and zero says they may surface. Reading
        it as a reading would draw a flat line along the surface across every no-deco dive
        in a logbook.

        **`Cylinders[].Pressure` is the transmitter and `DeviceInternalAbsPressure` is
        not.** The second sits in the same sample object and reads about 96 400 Pa at the
        surface — it is the computer's own ambient-pressure sensor, and labelling it tank
        pressure on a chart divers plan gas from would be actively wrong.
        """
        depth = Channel()
        ceiling = Channel()
        temperature = Channel()
        pressures = {number: Channel() for number in numbering.values()}
        for second, sample in samples.ordered():
            if sample.depth is not None:
                depth.record(second, rounded(sample.depth * CENTIMETRES_PER_METRE))
            if sample.ceiling is not None and sample.ceiling > 0:
                ceiling.record(second, rounded(sample.ceiling * CENTIMETRES_PER_METRE))
            if sample.kelvin is not None:
                temperature.record(second, rounded((sample.kelvin - KELVIN_OFFSET) * TENTHS_PER_UNIT))
            for source_number, pascal in sample.pressures.items():
                bar = pascal / PASCALS_PER_BAR
                number = numbering.get(source_number)
                if number in pressures and 0 <= bar <= MAX_CYLINDER_PRESSURE:
                    pressures[number].record(second, rounded(bar * TENTHS_PER_UNIT))

        events = self.read_events(samples, numbering, where)
        profile = samples.profile(
            {"depth": depth, "ceiling": ceiling, "temperature": temperature},
            pressures=tuple(pressures.items()),
            events=events,
        )
        # §6.3 calls `gas_number` a label rather than an array index, so a numbering is
        # asserted only where something in the profile depends on it — a pressure channel,
        # or a gas switch naming the cylinder it switched to.
        if profile is not None and (profile.get("pressures") or any("gas_number" in event for event in events)):
            for number, cylinder in enumerate(cylinders):
                cylinder["gas_number"] = number
        return profile

    def read_events(
        self, samples: SampleAxis, numbering: dict[int, int], where: str
    ) -> list[dict[str, Any]]:
        """The markers a sample carries, as §6.5 events.

        Three families are read and the rest are dropped, which is a decision about noise
        rather than about trust. `GasSwitch` is the dive's gas history; `Notify` is the
        device prompting the diver, two of whose values are stops; `Alarm` and `Warning` are
        the things that went wrong, which are the events a diver most wants marked.

        **Everything under `State` is dropped**: it is the computer narrating its own mode —
        "Below Surface", "Wet Outside", "Dive Active" — which is not an event on a dive, and
        five of them land on t=0 of every file in the corpus. `DiveState`, `DiveStatus`,
        `Lap`, `Pause`, `ArrayBegin` and `Activity` go the same way and for the same reason.

        **Only the `Active: true` edge is emitted.** These arrive in pairs — a `Deep Stop`
        true at 1 424 s and false at 1 454 s is one 30-second stop — and a marker has no way
        to show which half of a pair it is, so the marker is the start and the other half
        would only double it. A `GasSwitch` has no `Active` and is not a pair.
        """
        events: list[dict[str, Any]] = []
        for second, sample in samples.ordered():
            for name, payload in sample.events:
                event = self.event(name, payload, second, numbering, where)
                if event is not None:
                    events.append(event)
        return events

    def event(
        self, name: str, payload: Any, second: int, numbering: dict[int, int], where: str
    ) -> dict[str, Any] | None:
        """One `{name: payload}` pair as a §6.5 event, or nothing worth a marker."""
        if name == "GasSwitch" and isinstance(payload, dict):
            event: dict[str, Any] = {"time": second, "type": "gas_switch"}
            number = payload.get("GasNumber")
            if not isinstance(number, bool) and isinstance(number, int):
                # The source's own number resolved to the cylinder's position, so a marker
                # names the cylinder the logbook shows. A switch to a gas this file
                # describes nowhere is still emitted, with no number: it is a switch that
                # happened, and saying so is honest where guessing a position would not be.
                resolved = numbering.get(number)
                if resolved is not None:
                    event["gas_number"] = resolved
            return event

        if not isinstance(payload, dict) or payload.get("Active") is not True:
            return None
        reported = _text(payload.get("Type"))
        if reported is None:
            return None
        if name == "Notify":
            stop = STOP_TYPES.get(reported.lower())
            return None if stop is None else {"time": second, "type": stop}
        if name in ALERT_NAMES:
            # The device's wording verbatim, which is what §6.5's `other` is for: "Ceiling
            # Broken" says more than any type this format could map it onto, and §6.6
            # requires the label.
            return {"time": second, "type": "other", "label": reported}
        return None

    # -- numbers -----------------------------------------------------------------

    def scaled(self, value: Decimal | None, factor: Decimal) -> Decimal | None:
        return None if value is None else value * factor


def _merge(standing: _Sample, arriving: _Sample, collisions: dict[str, int]) -> None:
    """Fold a later entry's readings into the one already holding that second.

    The first reading of a channel wins and the later one is counted, which is
    `series.py`'s rule applied one level down: the channels are what §6.5 makes strictly
    increasing, and two entries at one second are only in competition where they carry the
    same channel. Events are not a channel and are all kept — a gas switch and an alarm on
    one second are two things that happened.
    """
    for member, channel in (("depth", "depth"), ("ceiling", "ceiling"), ("kelvin", "temperature")):
        arrived = getattr(arriving, member)
        if arrived is None:
            continue
        if getattr(standing, member) is None:
            setattr(standing, member, arrived)
        else:
            collisions[channel] = collisions.get(channel, 0) + 1
    if arriving.latitude is not None or arriving.longitude is not None:
        if standing.latitude is None and standing.longitude is None:
            standing.latitude, standing.longitude = arriving.latitude, arriving.longitude
        else:
            collisions["position"] = collisions.get("position", 0) + 1
    for number, pascal in arriving.pressures.items():
        if number in standing.pressures:
            collisions["cylinder pressure"] = collisions.get("cylinder pressure", 0) + 1
        else:
            standing.pressures[number] = pascal
    standing.events.extend(arriving.events)


def _first(*values: Decimal | None) -> Decimal | None:
    """The first value that was actually recorded.

    Not `a or b`: these are physical readings and a legitimately zero one must not fall
    through to the next candidate — which is then read by the member's own constraint, one
    step later and for a stated reason.
    """
    return next((value for value in values if value is not None), None)


def _events(sample: dict[str, Any]) -> list[tuple[str, Any]]:
    """Every `{name: payload}` an entry carries, under either member name."""
    found: list[tuple[str, Any]] = []
    for key in EVENT_KEYS:
        raw = sample.get(key)
        if raw is None:
            continue
        for entry in raw if isinstance(raw, list) else [raw]:
            if isinstance(entry, dict):
                found.extend(entry.items())
    return found
