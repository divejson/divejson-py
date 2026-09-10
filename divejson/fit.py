"""Reading ANT/Garmin FIT dive activities into DiveJSON.

FIT is the one binary format a dive computer is likely to hand a diver, and it is shared:
a Garmin Descent and a Suunto Ocean write the same message numbers with the same units,
because the units come from the global FIT profile rather than from the vendor. That is
the opposite of every other source this package reads, where each writer invents its own
spelling — so this module has almost no unit table and one very sharp trap instead.

`docs/fit-mapping.md` is the prose companion: every message and field this module reads,
every one it deliberately does not, and why. What is true of every source format rather
than of this one — the note kinds, identity, the way a zero reads, the number bound, the
sample axis — lives in `converter.py` and `series.py`, and this module inherits it.

**Garmin's SDK is not used, and nothing here is copied out of `Profile.xlsx`.** The
decoding is `fitdecode`'s, which is MIT and carries its own profile; the field names below
are the ones that library exposes, and the numbers in `docs/fit-mapping.md` were read off
real files and the MIT-licensed decoders. See the notice in `README.md`.

Four decisions shape what is below.

**A developer field may shadow a native one, and the native one wins.** This is the whole
of what a FIT reader has to get right for these files. Suunto's exporter declares
developer fields whose names collide with profile fields, so every Suunto `session` in
hand carries **two** `max_depth` values: the native `uint32` scaled by 1000, exact, and a
`float32` duplicate that renders 45.91 as 45.90999984741211. Walking `frame.fields` into a
dict by name keeps whichever came last, which is the lossy one. `_native` filters the
developer fields out by type, so a message carrying **only** the developer duplicate reads
as not recorded and falls through to the next source rather than passing a vendor's float
off as the profile's scaled integer.

**A summary is read from the `session` first for the depths and from Garmin's
`dive_summary` first for the oxygen accounting**, which looks inconsistent and is not. The
depths agree wherever both exist, and the fallback is for a device that summarises a dive
in one message and not the other. The CNS and OTU totals are the *dive's*, and on a
multi-dive Garmin file the session's cover the whole activity while the `dive_summary`
picked by `_summary` describes the dive being read.

**A FIT file records no id for its dive**, so a dive's identity is its position — the
stand-in `converting.md` defines, reported as such. Two copies of one file in one archive
are therefore two dives with two identities, which is right: nothing in either file says
they are the same dive, and a watch that writes one file per dive gives an archive its
order.

**Its timestamps are UTC, and the local offset lives on `activity`.** Every FIT timestamp
counts seconds from 1989-12-31 UTC, and `activity.local_timestamp` is that same instant
written as the wall clock at the dive site, so the difference between the two *is* the
offset §5.2 asks to be preserved. A file with no `activity` message carries no offset to
recover, and its dive is read as the UTC instant it recorded rather than being given a
zone this converter made up.
"""

from __future__ import annotations

import io
import uuid as uuid_pkg
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import fitdecode
from fitdecode.types import DevField, FieldData

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
    SourceTooLargeError,
    decimal_of,
    device,
    header,
    position,
    record_inferred,
    recorded,
    recording,
    rounded,
)
from .series import Channel, SampleAxis
from .validate import validate_document

# The format id this adapter registers under, which is also the name of the directory a
# conformance corpus keeps its pairs in.
FORMAT = "fit"

# uuid5(NAMESPACE_URL, "https://divejson.org/ns/fit"). Fixed forever: changing it would
# renumber every document any released version of this converter has produced from a FIT
# file, and `docs/fit-mapping.md` *Identity* records the value as normative for any port.
FIT_ID_NAMESPACE = uuid_pkg.UUID("1ecddff6-d7c6-58e8-a0ec-337a3338b855")

# The format's only magic number: the ASCII string `.FIT` at offset 8, immediately after
# the header preamble. Unlike the `.fit` extension it is not something a rename can fake,
# and unlike every other format this package reads it is not at offset 0 — which is why a
# container's own head can never decide the format of the files inside it.
MAGIC = b".FIT"
MAGIC_OFFSET = 8
MAGIC_END = MAGIC_OFFSET + len(MAGIC)

# How many frames one file may hold before it stops describing a dive. Decoding is linear
# in frames and is the whole cost of reading a FIT, so a file of bare `record`s — which is
# how a device really encodes a long log, about ten bytes each — is where an unbounded read
# hurts. Frames rather than data messages because that is what the decoder hands back and
# what each one costs: the fullest file in this project's hand decodes to 4,339 of them for
# a 72-minute dive — 4,295 `record`s among 4,311 data messages, plus 26 definitions, the
# header and the CRC — so this is about 23 times that, or roughly 28 hours of continuous
# logging.
#
# A cap this adapter sets rather than one the caller does, unlike the archive's
# `max_members` and `max_member_size`: those bound an upload, and this bounds the work one
# already-bounded file can ask for. It refuses rather than truncating, because a FIT
# file's `session` is written *after* the samples it summarises — stopping early and
# keeping what had arrived would discard the start time, the duration and the depths, and
# convert a confidently empty dive.
MAX_MESSAGES = 100_000

# How many `device_info` messages are kept while looking for the computer's firmware
# version. A device writes one every few minutes — the two Suunto Ocean files in hand
# carry two and three — and only the first that names the file's own manufacturer is read,
# so this bounds a list nothing else does.
MAX_DEVICES = 8

# `message_index` is a bitfield rather than a counter: the low 12 bits are the index and
# the top bits are flags, which the profile spells out as its own enum. Without the mask a
# gas at index 1 with the "selected" bit set arrives as 32769 and sorts after an unflagged
# gas at index 2, reordering the cylinders.
MESSAGE_INDEX_MASK = 0x0FFF

# Real UTC offsets run from -12:00 to +14:00. A wider gap between `activity.timestamp` and
# `activity.local_timestamp` means one of the two is corrupt, and is read as no offset at
# all rather than carried into a document — `timezone()` itself raises past 24 hours,
# which would leave a converter crash where a report line belongs.
MAX_OFFSET_MINUTES = 14 * 60

# §6.3 caps a cylinder pressure at 350 bar. The FIT profile states the unit outright, so a
# reading past the cap is a source defect rather than a scale to reinterpret.
MAX_CYLINDER_PRESSURE = Decimal(350)

# A FIT angle is a *semicircle*: a signed 32-bit count of 180/2^31 degrees, so a full
# circle is 2^32. The profile declares no scale factor for `position_lat`/`position_long`,
# so the raw count is what a decoder hands back.
DEGREES_PER_SEMICIRCLE = Decimal(180) / Decimal(2**31)

# Decimal places kept on a coordinate. Six is about 11 cm at the equator, well inside any
# consumer receiver's error, and it is what makes one dive converted from two of its own
# exports produce one position rather than two: the same fix reaches this package as a
# semicircle count here and as a radian float from the Suunto app's JSON, and the two
# agree exactly at six places and disagree below.
COORDINATE_PLACES = Decimal("0.000001")

# `dive_gas.status` values for a cylinder this dive did not carry. A device stores its
# whole configured gas list, so a recreational air dive on a computer with two deco gases
# programmed into it would otherwise arrive with three cylinders. `backup_only` is
# deliberately **not** here: it is a pony bottle, carried and not breathed, which is a
# cylinder that was on the dive and belongs in the logbook. (The application this package
# was extracted from drops it, for a reason that is its own: it computes a gas consumption
# that needs exactly one mixture. A logbook has no such need.)
UNCARRIED_GAS_STATUSES = frozenset({"disabled"})

# `dive_settings.water_type`, whose FIT enum is `{0: fresh, 1: salt, 2: en13319,
# 3: custom}`. Three of the four are §6.2 members under the same name. `custom` is
# deliberately absent: it says the diver dialled in a `water_density` number, which is not
# a water type and which §6.2 has nowhere to put — so it is reported rather than rounded
# to the nearest real water.
WATER_TYPES = {"fresh": "fresh", "salt": "salt", "en13319": "en13319"}

# `event.event` values that describe the dive, and the §6.5 event type each becomes. A
# table rather than a cast: this is Garmin's vocabulary, and the other 43 members of its
# 46-member enum — `timer`, `battery`, `off_course`, every cycling and running alert the
# shared enum carries — have to come out as nothing rather than be forced into a type of
# this format's.
#
# `timer` is the deliberate omission. It is the only `event` any file in hand writes, and
# its start/stop pair says where the dive begins and ends, which §6.4's `started_at` and
# the profile's own axis already say twice over.
EVENT_TYPES = {
    "dive_gas_switched": "gas_switch",
    "user_marker": "bookmark",
    "dive_alert": "other",
}


class FitError(ConverterError):
    """The input could not be read as a FIT dive activity."""


class MalformedFitError(FitError):
    """The bytes are not a readable FIT file, or hold no dive."""


class FitAdapter:
    """The registry's view of this reader: what it claims, and how it converts.

    An instance of this is what `registry.py` registers; everything else in this module is
    behind it.
    """

    format: str = FORMAT
    suffixes: tuple[str, ...] = (".fit",)
    namespace: uuid_pkg.UUID = FIT_ID_NAMESPACE

    def sniff(self, head: bytes) -> bool:
        """Whether a bounded head of bytes opens a FIT file.

        The magic and nothing else — not the manufacturer, not the file type. A file this
        reader can read is one it can read whichever vendor wrote it, and whether the
        activity inside turns out to be a dive is a question only decoding answers.
        """
        return head[MAGIC_OFFSET:MAGIC_END] == MAGIC

    def convert(self, data: bytes, *, exported_at: datetime, scope: Scope) -> Conversion:
        """Convert one FIT file into DiveJSON.

        Raises `MalformedFitError`, `SourceTooLargeError` or `NonConformingOutputError`.
        """
        return _Converter(_scan(data), exported_at=exported_at, scope=scope).run()


FIT = FitAdapter()


# -- reading the file -----------------------------------------------------------------


def _native_field(frame: fitdecode.FitDataMessage, name: str) -> FieldData | None:
    """A message's own field by that name, never a developer field wearing it.

    The one filter this reader cannot do without: see the module docstring. `fitdecode`'s
    `get_value` returns the native field where both are present, but only as a side effect
    of taking the first match by position — and hands back the developer field's value
    where it is the only one, units and semantics included.
    """
    for field_data in frame.fields:
        if field_data.is_named(name) and not isinstance(field_data.field, DevField):
            return field_data
    return None


def _native(frame: fitdecode.FitDataMessage | None, name: str) -> Any | None:
    if frame is None:
        return None
    field_data = _native_field(frame, name)
    return field_data.value if field_data is not None else None


def _native_raw(frame: fitdecode.FitDataMessage | None, name: str) -> Any | None:
    """A field's *undecoded* value, for the ones that are identities rather than readings.

    `fitdecode` renders a field whose profile type carries an enum by exact value match,
    and FIT keeps bitfield masks in that same enum slot: `message_index` maps 4095 to
    `mask` and 32768 to `selected`, and `ant_channel_id` — the type behind a tank
    message's `sensor` — maps 65535 to `ant_device_number`. So a gas index or a
    transmitter id that lands on one of those numbers comes back as a **string**. That is
    not a corrupt file: `message_index = 0x8000` is gas 0 with the "selected" bit set,
    which is an ordinary thing for a device to say about the first configured gas.
    """
    if frame is None:
        return None
    field_data = _native_field(frame, name)
    return field_data.raw_value if field_data is not None else None


def _version_text(value: Any) -> str | None:
    """A `software_version` as the text a version is, or nothing where none was recorded.

    The profile scales the field, so `fitdecode` hands back a number rather than a string
    and the digits are the device's own. `bool` is excluded because it is an `int` in
    Python and never a firmware version anywhere else.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


@dataclass(slots=True, eq=False)
class _Point:
    """Every reading one instant of the dive carried, whichever message brought it.

    A FIT device samples its channels independently — a Suunto Ocean writes 4,295 `record`
    messages of which 431 carry a depth and 4,294 a temperature — and a transmitter's
    `tank_update` is a message of its own at its own instant. They are collected onto one
    instant here so that the §6.5 axis is built once, and so that a `record` and a
    `tank_update` at one second are one sample rather than two, the later of which the
    axis would drop.

    `eq=False` so a point stays hashable and compares by identity. A dataclass that
    generates `__eq__` sets `__hash__` to `None`, and these are used as dictionary keys —
    an event finds its second by looking its own instant's point up in the axis. Identity
    is also the comparison that means anything here: two instants that happened to record
    the same depth are still two samples.
    """

    depth: Decimal | None = None
    ceiling: Decimal | None = None
    temperature: Decimal | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    pressures: dict[int, Decimal] = field(default_factory=dict)


@dataclass(slots=True)
class _Scan:
    """Everything one pass over a FIT file keeps.

    A FIT file is a stream whose summary messages come *after* the samples they summarise,
    so there is no cheap way to read a header: the file is decoded once and this is what
    survives it. Summaries are kept as frames, of which there are a handful; the sample
    stream is reduced to `_Point`s as it goes past.
    """

    protocol: str | None = None
    file_id: fitdecode.FitDataMessage | None = None
    session: fitdecode.FitDataMessage | None = None
    sessions: int = 0
    activity: fitdecode.FitDataMessage | None = None
    settings: fitdecode.FitDataMessage | None = None
    summaries: list[fitdecode.FitDataMessage] = field(default_factory=list)
    gases: list[fitdecode.FitDataMessage] = field(default_factory=list)
    tanks: list[fitdecode.FitDataMessage] = field(default_factory=list)
    events: list[fitdecode.FitDataMessage] = field(default_factory=list)
    # Keyed by instant, in first-seen order, which is the order the axis is offered them
    # in. A `dict` rather than a list because two messages at one instant are one sample.
    points: dict[datetime, _Point] = field(default_factory=dict)
    # Sample messages that carried no timestamp, counted so the axis can report each one:
    # nothing can be placed on a channel without an instant to place it at.
    undated: int = 0
    # One entry per reading a later message brought to a channel an earlier one had
    # already filled at that instant, named by the channel it was for.
    collisions: list[str] = field(default_factory=list)
    devices: list[fitdecode.FitDataMessage] = field(default_factory=list)


def _scan(data: bytes) -> _Scan:
    """Decode the file once, keeping the messages a dive is built from.

    `fitdecode`'s defaults are kept: a file whose checksum or trailing bytes are slightly
    off still converts. The CRC guards against transfer corruption rather than tampering,
    nothing downstream trusts it, and refusing an otherwise readable logbook over it would
    lose real dives for no gain.

    **Catches `Exception`, not only `fitdecode`'s own error type.** This is the one place
    in the package where a third-party decoder walks bytes a stranger supplied, and a
    corrupt file does not reliably present as a `FitError`: flipping bytes past the header
    raises `AssertionError` from the reader, `ValueError` from a bad field definition and
    `TypeError` from the processors. Every one of those has to reach a caller as the
    `ConverterError` this package promises, or an import screen shows a stack trace.
    """
    scan = _Scan()
    try:
        with fitdecode.FitReader(io.BytesIO(data)) as reader:
            for count, frame in enumerate(reader, start=1):
                if count > MAX_MESSAGES:
                    raise SourceTooLargeError(
                        f"this FIT file holds more than {MAX_MESSAGES:,} messages, which is far more than "
                        "any dive; it looks like an activity log rather than a dive log"
                    )
                if isinstance(frame, fitdecode.FitHeader):
                    scan.protocol = ".".join(str(part) for part in frame.proto_ver)
                elif isinstance(frame, fitdecode.FitDataMessage):
                    _collect(scan, frame)
    except ConverterError:
        # This module's own refusal, already phrased for a diver — not the decoder's.
        raise
    except Exception as error:
        raise MalformedFitError(
            f"the file is not a readable FIT file — {error or type(error).__name__}"
        ) from error
    return scan


def _collect(scan: _Scan, frame: fitdecode.FitDataMessage) -> None:
    """Route one decoded message into the scan.

    **Samples stop at the first `session`.** A FIT file writes its summary after the
    samples it summarises, so anything following the first `session` belongs to a second
    dive — and a `dive_gas` carries no timestamp to filter on, so the cut is positional.
    The dive that a second `session` describes is reported rather than converted; see
    `_Converter.read_dive`.

    `dive_summary` and `tank_summary` sit **above** that cut, because both are written
    after the session they describe and gating them on it would discard every one. They
    are bounded at the second session instead.
    """
    name = frame.name
    if name == "file_id":
        scan.file_id = scan.file_id or frame
    elif name == "session":
        scan.sessions += 1
        scan.session = scan.session or frame
    elif name == "activity":
        scan.activity = scan.activity or frame
    elif name == "dive_settings":
        scan.settings = scan.settings or frame
    elif name == "device_info":
        if len(scan.devices) < MAX_DEVICES:
            scan.devices.append(frame)
    elif name == "dive_summary":
        if scan.sessions < 2:
            scan.summaries.append(frame)
    elif name == "tank_summary":
        if scan.sessions < 2:
            scan.tanks.append(frame)
    elif scan.session is not None:
        return
    elif name == "dive_gas":
        scan.gases.append(frame)
    elif name == "event":
        scan.events.append(frame)
    elif name == "record":
        _collect_record(scan, frame)
    elif name == "tank_update":
        _collect_tank_update(scan, frame)


def _point(scan: _Scan, frame: fitdecode.FitDataMessage) -> _Point | None:
    at = _native(frame, "timestamp")
    if not isinstance(at, datetime):
        scan.undated += 1
        return None
    return scan.points.setdefault(at, _Point())


def _set(scan: _Scan, point: _Point, name: str, value: Decimal | None) -> None:
    """One channel's reading at one instant, first writer winning and the rest reported.

    Two `record` messages sharing an instant are one sample of the dive, and merging them
    is right for the usual case — a device writing depth on one and temperature on the
    next. Two readings of the *same* channel at one instant is the case that has to be
    reported, because §6.5's times are strictly increasing and only one of them can be
    kept.
    """
    if value is None:
        return
    if getattr(point, name) is None:
        setattr(point, name, value)
    else:
        scan.collisions.append(name)


def _collect_record(scan: _Scan, frame: fitdecode.FitDataMessage) -> None:
    """Depth, deco ceiling, temperature and any satellite fix off one `record`.

    `next_stop_depth` is FIT's deco ceiling — the depth of the next required stop, in
    metres, scaled like `depth` beside it. **Not** `next_stop_time`, `time_to_surface` or
    `ndl_time`, the three neighbouring fields that measure durations rather than a depth.
    """
    point = _point(scan, frame)
    if point is None:
        return
    _set(scan, point, "depth", _number(_native(frame, "depth")))
    _set(scan, point, "ceiling", _number(_native(frame, "next_stop_depth")))
    _set(scan, point, "temperature", _number(_native(frame, "temperature")))
    _set(scan, point, "latitude", _degrees(_native(frame, "position_lat")))
    _set(scan, point, "longitude", _degrees(_native(frame, "position_long")))


def _collect_tank_update(scan: _Scan, frame: fitdecode.FitDataMessage) -> None:
    """One transmitter's pressure reading, grouped by the pod that sent it.

    `pressure` is already bar — the profile scales it — and `sensor` is the pod's ANT id,
    read raw because the profile renders 65535 in that slot as a word rather than a number.

    Two readings from one pod at one instant are the same collision `_set` reports for a
    `record` channel, and are reported the same way: the times on a pressure channel are
    strictly increasing too, so the first is kept and the second named.
    """
    point = _point(scan, frame)
    if point is None:
        return
    bar = _number(_native(frame, "pressure"))
    sensor = _native_raw(frame, "sensor")
    if bar is None or not isinstance(sensor, int) or isinstance(sensor, bool):
        return
    if sensor in point.pressures:
        scan.collisions.append("cylinder pressure")
        return
    point.pressures[sensor] = bar


def _number(value: Any) -> Decimal | None:
    """A reading `fitdecode` handed back, as an exact decimal, or nothing.

    Through `str()` and `decimal_of` rather than `Decimal(value)`: the library divides a
    raw integer by the profile's scale factor, so a depth arrives as a binary float whose
    shortest decimal is the number the device meant — and `decimal_of` is where this
    package's finiteness and magnitude bound live.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return decimal_of(str(value))


def _degrees(value: Any) -> Decimal | None:
    """A semicircle count as decimal degrees, rounded to the place a coordinate keeps.

    `fitdecode` resolves the format's own absent-marker before this: `position_lat` is a
    `sint32` whose invalid sentinel is 0x7FFFFFFF, and the base type's parser returns
    nothing for it rather than the 180.000000 degrees the arithmetic would give. That
    matters most for longitude, where 180 is a real, in-range value no range check catches.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return (Decimal(value) * DEGREES_PER_SEMICIRCLE).quantize(COORDINATE_PLACES)


# -- converting it --------------------------------------------------------------------


class _Converter:
    def __init__(self, scan: _Scan, *, exported_at: datetime, scope: Scope) -> None:
        self.scan = scan
        self.exported_at = exported_at
        self.scope = scope
        self.notes: list[Note] = []
        self.identities = Identities(FIT_ID_NAMESPACE, scope, self.note)
        self.inferred: list[str] = []
        # The `dive_gas` entries this dive actually carried, in the order the cylinders are
        # written in. Read once, by `read_dive`, because two things need the same list and
        # the report may only be written once: the cylinders are built from it and a gas
        # switch resolves the `message_index` it names against it.
        self.gases: list[fitdecode.FitDataMessage] = []

    # -- reporting ---------------------------------------------------------------

    def note(self, where: str, message: str, kind: NoteKind) -> None:
        self.notes.append(Note(self.scope.where(where), message, kind))

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

        `source_generator` is the device rather than an application: a FIT file is written
        by the computer on the diver's wrist, and `file_id.product_name` is what that
        computer calls itself. `fit_protocol_version` is the wire format's own version out
        of the file header, which is the closest thing FIT has to `.ssrf`'s `@version`.
        """
        provenance: dict[str, Any] = {"converted_from": FORMAT}
        if self.scan.protocol:
            provenance["fit_protocol_version"] = self.scan.protocol
        name = _native(self.scan.file_id, "product_name") or _native(self.scan.file_id, "manufacturer")
        if isinstance(name, str) and name.strip():
            generator: dict[str, Any] = {"name": name.strip()}
            version = self.device_version()
            if version is not None:
                generator["version"] = version
            provenance["source_generator"] = generator
        record_inferred(provenance, self.inferred)
        return provenance

    def read_device(self, session: fitdecode.FitDataMessage, where: str) -> dict[str, Any] | None:
        """The same two messages read as hardware rather than as provenance (§6.4b).

        `source_generator` and the device are one fact seen twice for a format a wrist
        writes: the provenance block records what produced the *file*, and here that is the
        computer. Both are written, and `converting.md` says why.

        **`product` (2) is not a model and there is no fall-through to it.** It is a numeric
        vendor id — the Ocean's is `62` — where §6.4b's `model` is the product string as the
        source names it, and a decoder that resolves one resolves it to a profile constant,
        which is the profile's vocabulary rather than what the vendor calls the computer.
        Falling back to `file_id.manufacturer` is not open either: `brand` is a member of
        its own, and copying it into `model` would say `suunto` is the product. A file
        stating no `product_name` therefore carries **no model**, which is the ordinary
        absence rather than a gap to fill. The same reading governs `brand`: a manufacturer
        id the profile does not resolve to a name arrives as an integer, and an integer is
        not what the maker is called.
        """
        computer = self.computer_info()
        serial = _native(computer, "serial_number")
        if serial is None:
            # The file's own claim about what wrote it, which is the fallback rather than
            # the first answer: `device_index` 0 is the computer and every other
            # `device_info` in the chain is a transmitter or a strap.
            serial = _native(self.scan.file_id, "serial_number")
        firmware = _native(computer, "software_version")
        return device(
            {
                # `brand` and `model` are handed over as the decoder rendered them: a
                # profile enum that resolved gives a string, and one that did not gives the
                # bare number, which `device` drops for not being a string at all.
                "brand": _native(self.scan.file_id, "manufacturer"),
                "model": _native(self.scan.file_id, "product_name"),
                "serial": None if serial is None else str(serial),
                "firmware": _version_text(firmware),
                "dive_number": _native(session, "dive_number"),
            },
            note=self.note,
            where=where,
            labels={
                "brand": "file_id.manufacturer",
                "model": "file_id.product_name",
                "serial": "the device's serial_number",
                "firmware": "device_info.software_version",
                "dive_number": "session.dive_number",
            },
        )

    def computer_info(self) -> fitdecode.FitDataMessage | None:
        """The `device_info` for the computer itself: `device_index` 0, and no other.

        libdivecomputer's rule, and the right one. A dive computer writes a `device_info`
        for every device in the chain — the computer, a pressure transmitter, a heart-rate
        strap — and only index 0 is the computer; `garmin_parser.c` copies a serial, a
        product and a firmware from that message and no other. Reading a transmitter's
        serial as the computer's would pair two dives that were never on one wrist.

        Raw rather than decoded, for `_native_raw`'s reason: `device_index` is a profile
        enum whose 0 renders as `creator`, so a decoded comparison against 0 never fires.
        Neither fixture in `fixtures/fit/` carries a `device_index` at all, so this returns
        nothing for both of them and the two members it feeds wait on a file that has one.
        """
        for message in self.scan.devices:
            if _native_raw(message, "device_index") == 0:
                return message
        return None

    def device_version(self) -> str | None:
        """The firmware the computer was running, where a `device_info` message says.

        The *device's* own record, not the pod's or the phone's: a `device_info` naming a
        `manufacturer` the `file_id` does not is another thing in the chain, and reading
        its firmware as the computer's would put a transmitter's version on the dive.
        """
        maker = _native(self.scan.file_id, "manufacturer")
        for message in self.scan.devices:
            if _native(message, "manufacturer") != maker:
                continue
            version = _version_text(_native(message, "software_version"))
            if version is not None:
                return version
        return None

    # -- the dive ----------------------------------------------------------------

    def read_dive(self) -> dict[str, Any] | None:
        where = "dive/0"
        session = self.session(where)

        started_at = self.read_started_at(session, where)
        if started_at is None:
            return None
        # A FIT file records no id for its dive, so every dive takes the positional
        # stand-in `converting.md` defines — and the note that says so, since an identity
        # that moves when a file's order changes is a fact a diver may need (spec §5.3).
        claimed, carried = self.identities.for_record("dive", None, where, 0)
        if claimed is None or not carried:
            return None

        dive: dict[str, Any] = {"uuid": claimed, "started_at": started_at}
        summary = self.summary()
        samples = self.axis(where)
        self.gases = self.carried_gases(where)

        # In §6.2's own member order, so a converted dive reads down the schema.
        self.read_duration(session, summary, dive, where)
        self.read_depths(session, summary, samples, dive, where)
        self.read_water_type(dive, where)
        self.read_oxygen(session, summary, dive, where)
        self.read_positions(samples, dive)

        cylinders, sensors = self.read_cylinders(where)
        if cylinders:
            dive["cylinders"] = cylinders
        # After the list is in the document, because it numbers the cylinders in place:
        # a `gas_number` is asserted only where a pressure channel or a gas switch needs
        # one to point at.
        profile = self.read_profile(samples, cylinders, sensors, where)
        # A FIT file is one dive written by one computer, so a converted document has
        # exactly one recording (§6.4a) — and none at all where the file names no computer
        # and kept no usable sample, §6.4a forbidding a recording that carries nothing.
        built = recording(device=self.read_device(session, where), profile=profile)
        if built is not None:
            dive["recordings"] = [built]
        return dive

    def session(self, where: str) -> fitdecode.FitDataMessage:
        """The `session` this file's dive is described by.

        `sport == "diving"` covers every dive sub-sport — single and multi-gas, gauge,
        apnea — and is what both vendors write. A session that does not say so is accepted
        where the file carried depth samples, since that is the stronger evidence and
        costs nothing to check.
        """
        session = self.scan.session
        if session is None:
            raise MalformedFitError("this FIT file carries no session, so it describes no dive")

        sport = _native(session, "sport")
        if sport != "diving" and not any(point.depth is not None for point in self.scan.points.values()):
            raise MalformedFitError(f"this FIT file records a {sport or 'non-diving'} activity, not a dive")
        if self.scan.sessions > 1:
            self.note(
                where,
                f"the file describes {self.scan.sessions} sessions and this format's reader takes one dive "
                "from a file; the first is converted and the rest are dropped",
                "dropped",
            )
        return session

    def summary(self) -> fitdecode.FitDataMessage | None:
        """The `dive_summary` describing the whole activity, not one dive inside it.

        A Garmin freediving activity writes a `dive_summary` per descent *plus* a
        session-level one, and `reference_mesg` names which message type a summary refers
        to. Taking the first would read one descent's depth and bottom time as the whole
        dive's. Falls back to the first of any kind, a single-dive export commonly writing
        one with no `reference_mesg` at all.
        """
        for summary in self.scan.summaries:
            if _native(summary, "reference_mesg") == "session":
                return summary
        return self.scan.summaries[0] if self.scan.summaries else None

    def read_started_at(self, session: fitdecode.FitDataMessage, where: str) -> str | None:
        """`session.start_time` in the zone `activity` says the dive was logged in.

        The offset is `local_timestamp - timestamp` on the `activity` message: two
        renderings of one instant, whose difference is the wall-clock offset at the dive
        site. §5.2 asks that a recorded offset be preserved and never supplied, and this
        is a recorded one — recovered rather than assumed.

        With no `activity` message there is nothing to recover from, and the dive is
        carried as the UTC instant the file recorded, reported. That is not an invented
        offset: FIT states outright that its timestamps are UTC, so `+00:00` is the zone
        the field is in. What is lost is the wall clock the diver read, and the report
        says so.
        """
        start = _native(session, "start_time")
        if not isinstance(start, datetime):
            self.note(
                where,
                "the session records no start time, and the format requires one; the dive is dropped "
                "(spec §6.2)",
                "dropped",
            )
            return None

        offset = self.local_offset()
        if offset is None:
            self.note(
                where,
                "the file carries no activity message, so the local time zone cannot be recovered; the "
                "start time is carried as the UTC instant the device recorded (spec §5.2)",
                "absent",
            )
        else:
            start = start.astimezone(offset)
        # `isoformat` writes the fraction only where the source recorded one. FIT counts
        # whole seconds, so this is `…:23+02:00` on every file in hand — but a source that
        # did record a fraction keeps it rather than being truncated.
        return start.isoformat()

    def local_offset(self) -> timezone | None:
        activity = self.scan.activity
        utc = _native(activity, "timestamp")
        local = _native(activity, "local_timestamp")
        if not isinstance(utc, datetime) or not isinstance(local, datetime):
            return None
        # Rounded to whole minutes: no real zone has sub-minute resolution, and the two
        # timestamps are second-resolution readings that may be a second apart.
        minutes = round((local - utc).total_seconds() / 60)
        if abs(minutes) > MAX_OFFSET_MINUTES:
            return None
        return timezone(timedelta(minutes=minutes))

    # -- the summary scalars -----------------------------------------------------

    def absent(self, member: str, source: str, where: str) -> None:
        self.note(where, f"the file records no {source}, so the dive's {member} is not carried", "absent")

    def read_duration(
        self,
        session: fitdecode.FitDataMessage,
        summary: fitdecode.FitDataMessage | None,
        dive: dict[str, Any],
        where: str,
    ) -> None:
        """`total_elapsed_time`, else `total_timer_time`, else `dive_summary.bottom_time`.

        In that order and for that reason. `total_elapsed_time` is the wall clock from the
        moment the dive started to the moment it ended, which is what a diver means by a
        dive's duration; `total_timer_time` excludes pauses, a distinction that barely
        exists underwater, and stands in where a device omits the first. Garmin's
        `bottom_time` is deliberately last: it measures time *at depth*, not the dive.
        """
        seconds = _first(
            _number(_native(session, "total_elapsed_time")),
            _number(_native(session, "total_timer_time")),
            _number(_native(summary, "bottom_time")),
        )
        if seconds is None:
            self.absent("duration", "elapsed time for the session", where)
            return
        duration = rounded(seconds)
        if recorded(duration, record="dive", member="duration"):
            dive["duration"] = duration
        else:
            self.note(
                where,
                f"the session's elapsed time is {duration} seconds; the format records a duration only when "
                "it is positive",
                "absent",
            )

    def read_depths(
        self,
        session: fitdecode.FitDataMessage,
        summary: fitdecode.FitDataMessage | None,
        samples: SampleAxis,
        dive: dict[str, Any],
        where: str,
    ) -> None:
        """`max_depth` and `avg_depth`: the session, then Garmin's summary, then the samples.

        The two messages agree wherever both carry a depth, and the summary is the
        fallback for a device that summarises a dive in one message and not the other. The
        samples are the third source and the only one that is *this converter's
        arithmetic*, so a depth taken from them is `inferred` and the document lists it
        under `extensions.divejson.inferred` (spec §5.4).

        **The `inferred` findings are raised last, after the two depths have been checked
        against each other**, because a computed mean deeper than a recorded maximum is
        dropped and a note saying where a dropped value came from is a note about a value
        the document does not carry. Raising them as the values were found and unlisting
        the dropped mean afterwards left the report and `extensions.divejson.inferred`
        disagreeing — an `inferred` line with no member on the list, which is the one thing
        `converter.py` says can never happen.
        """
        depths = [point.depth for _, point in samples.ordered() if point.depth is not None]
        computed = {
            "max_depth": max(depths) if depths else None,
            # The arithmetic mean of the depth readings, which is the mean *depth of the
            # dive* only where the device sampled at a constant rate. Every file in hand
            # does; a device that samples faster on descent would weight it towards the
            # descent, which is why this is the last resort and is labelled as computed.
            "avg_depth": (sum(depths, Decimal(0)) / len(depths)).quantize(Decimal("0.01")) if depths else None,
        }

        found: dict[str, Decimal] = {}
        derived: list[str] = []
        for member, source in (("max_depth", "max_depth"), ("avg_depth", "avg_depth")):
            native = _first(_number(_native(session, source)), _number(_native(summary, source)))
            if native is not None and recorded(native, record="dive", member=member):
                found[member] = native
                continue
            if native is not None:
                self.note(
                    where,
                    f"the session's {source} is {native} m; the format records a depth only when it is "
                    "positive",
                    "absent",
                )
            value = computed[member]
            if value is None or not recorded(value, record="dive", member=member):
                self.absent(member, f"{source} on its session or on a dive summary", where)
                continue
            found[member] = value
            derived.append(member)

        if "max_depth" in found and "avg_depth" in found and found["avg_depth"] > found["max_depth"]:
            self.note(
                where,
                f"the mean depth {found['avg_depth']} m is deeper than the greatest depth "
                f"{found['max_depth']} m, which cannot be; the mean is dropped rather than either being "
                "adjusted to fit (spec §6.2)",
                "dropped",
            )
            found.pop("avg_depth")

        for member in ("max_depth", "avg_depth"):
            if member not in found:
                continue
            dive[member] = float(found[member])
            if member in derived:
                self.note(
                    where,
                    f"the file records no {member} on its session or on a dive summary, so the dive's "
                    f"{member} is computed from its own depth samples (spec §5.4)",
                    "inferred",
                )
                self.inferred.append(f"dives/0/{member}")

    def read_oxygen(
        self,
        session: fitdecode.FitDataMessage,
        summary: fitdecode.FitDataMessage | None,
        dive: dict[str, Any],
        where: str,
    ) -> None:
        """The dive's CNS and OTU totals — the summary first, then the session.

        The mirror of `read_depths`, which prefers the session. The order is the other way
        round because these are the *dive's* oxygen accounting: on a multi-dive Garmin
        file the session totals cover the whole activity, while `summary` has already
        picked out the summary that describes the dive being read.

        **`o2_toxicity` is the dive's ending OTU total rather than the OTUs it added**,
        which the profile's bare "OTUs" unit does not settle. The corpus does: one dive
        exists as both a FIT and a Suunto DM5 XML export, and where the XML records an OTU
        start of 22 against an end of 23, the FIT writes `o2_toxicity = 23`. Read as a
        delta it would have been 1. There is no `start_otu` anywhere in the FIT profile,
        so `otu_start` has no source at all — see `docs/fit-mapping.md`.
        """
        for member, source in (("cns_start", "start_cns"), ("cns_end", "end_cns"), ("otu_end", "o2_toxicity")):
            value = _first(_number(_native(summary, source)), _number(_native(session, source)))
            if value is None:
                self.absent(member, f"{source} on its session or on a dive summary", where)
            elif recorded(value, record="dive", member=member):
                dive[member] = float(value)
            else:
                self.note(
                    where,
                    f"the session's {source} is {value}, which the format records only from zero up; dropped",
                    "dropped",
                )

    def read_water_type(self, dive: dict[str, Any], where: str) -> None:
        """`dive_settings.water_type`, which is the only salinity evidence a FIT carries.

        `en13319` stays `en13319` rather than being folded into `salt`: it is the
        calibration a computer ships set to, and rewriting it as the nearest real water
        would be inventing a reading. `custom` says the diver dialled in a density number,
        which §6.2 has no member for, and is reported rather than rounded off.

        **A device that wrote no `dive_settings`, or wrote one with no `water_type`,
        raises nothing.** The message is the computer's configuration rather than a record
        of the dive, so a setting it did not write is not a reading the dive failed to
        take — unlike the session summaries above, every one of which the device was
        describing this dive when it left empty.
        """
        value = _native(self.scan.settings, "water_type")
        if not isinstance(value, str):
            return
        if value in WATER_TYPES:
            dive["water_type"] = WATER_TYPES[value]
        else:
            self.note(
                where,
                f"the device's water type is {value!r}, which this format has no value for; dropped",
                "dropped",
            )

    def read_positions(self, samples: SampleAxis, dive: dict[str, Any]) -> None:
        """The fix on the way in and the fix on the way out, split at the deepest sample.

        **No fix is taken underwater** — a receiver does not reach a wrist through
        seawater — so every position in a dive log was recorded at the surface, and the
        only question worth asking of one is which surface interval it belongs to. The
        deepest sample is the split: what is at or before it is on the way in, what is
        after it is on the way out, and the last before and the first after are the two
        kept, because the fix that says where a diver got in is the one taken just before
        they descended rather than the one from when the boat left the jetty.

        The deepest sample is the pivot in preference to an in-water *window*, which would
        need a depth threshold this reader would have to invent. With no depth channel
        there is no pivot and so no answer, and nothing is written: a file that recorded
        positions and never a depth cannot say which of them is the entry.
        """
        fixed = [(second, point) for second, point in samples.ordered() if point.latitude is not None]
        depths = [(second, point.depth) for second, point in samples.ordered() if point.depth is not None]
        if not fixed or not depths:
            return

        # `max` keeps the first of equal values, so a flat profile pivots on its earliest
        # sample and every fix on it reads as an exit — except one landing exactly there,
        # which the `<=` below keeps as the entry.
        pivot = max(depths, key=lambda pair: pair[1])[0]
        before = [pair for pair in fixed if pair[0] <= pivot]
        after = [pair for pair in fixed if pair[0] > pivot]
        for member, chosen in (("entry_position", before[-1:]), ("exit_position", after[:1])):
            for second, point in chosen:
                where = f"dive/0/record/{second}"
                found = position(point.latitude, point.longitude, note=self.note, where=where)
                if found is not None:
                    dive[member] = found

    # -- cylinders ---------------------------------------------------------------

    def read_cylinders(self, where: str) -> tuple[list[dict[str, Any]], list[int]]:
        """`dive_gas` as §6.3 Cylinders, with a transmitter's pressures where they pair.

        Returns the cylinders and the pod ids their pressure channels are numbered by, in
        the same order — one ordering for both places a cylinder gets a position, so that
        a pod sits at the same `gas_number` on a profile channel as on the cylinder.

        **Nothing in a FIT file links a gas to a pod.** Tank telemetry is keyed by the
        transmitter's ANT id and a `dive_gas` by its `message_index`, and no message maps
        one onto the other. Position is the only signal there is, so the two are paired in
        order and **only when the counts match exactly**; anything else — two gases and
        one pod — leaves the pressures out and says so, rather than attaching a start
        pressure to a cylinder that may not be the one it was measured in.

        A file with tank telemetry and no gas list at all is the other way round: evidence
        of a tank is evidence of a tank, so each pod becomes a cylinder carrying its
        pressures and nothing else.

        **Untested against a real file.** No FIT file in this project's hand carries any
        tank telemetry, so `tank_summary` and `tank_update` are exercised only by
        encoder-built messages — see `docs/fit-mapping.md`.
        """
        gases = self.gases
        tanks = self.tank_pressures()

        if not gases:
            cylinders = [self.cylinder(None, pressures, where) for _, pressures in tanks]
            return cylinders, [sensor for sensor, _ in tanks]

        if tanks and len(tanks) != len(gases):
            self.note(
                where,
                f"the file records {len(gases)} gases and {len(tanks)} cylinder pressure sources, and nothing "
                "in it says which belongs to which; the pressures are dropped rather than attached to a "
                "cylinder they may not have been measured in",
                "dropped",
            )
            tanks = []

        # `strict`, because the two lists are equal in length by construction — the
        # mismatched case was dropped above — and a zip that silently truncated would lose a
        # cylinder rather than fail.
        paired = [pressures for _, pressures in tanks] or [(None, None)] * len(gases)
        cylinders = [
            self.cylinder(gas, pressures, where) for gas, pressures in zip(gases, paired, strict=True)
        ]
        return cylinders, [sensor for sensor, _ in tanks]

    def carried_gases(self, where: str) -> list[fitdecode.FitDataMessage]:
        """The `dive_gas` entries for cylinders that were on the dive, in device order.

        A device stores its whole configured gas list, so a `disabled` entry is a gas the
        computer was programmed with and this dive did not carry; it is dropped and
        reported rather than arriving as a cylinder nobody dived.

        Deduped on `message_index`, keeping the first, since a device may re-announce its
        list mid-file. Entries with no index at all are kept in a space of their own and
        appended, rather than being keyed by position: keying a position into the same
        table as a real `message_index` makes a gas at position 0 collide with a gas
        declaring index 0, and one of the two vanishes.

        Called **once**, by `read_dive`, and the answer kept on `self.gases`: the cylinders
        and the gas-switch events both resolve against this list, and a second call would
        write the disabled-gas line into the report twice.
        """
        indexed: dict[int, fitdecode.FitDataMessage] = {}
        unindexed: list[fitdecode.FitDataMessage] = []
        dropped = 0
        for gas in self.scan.gases:
            if _native(gas, "status") in UNCARRIED_GAS_STATUSES:
                dropped += 1
                continue
            index = _native_raw(gas, "message_index")
            if isinstance(index, int) and not isinstance(index, bool):
                indexed.setdefault(index & MESSAGE_INDEX_MASK, gas)
            else:
                unindexed.append(gas)
        if dropped:
            self.note(
                where,
                f"the device's gas list holds {dropped} gases it records as disabled, which are configured "
                "and were not carried on this dive; dropped",
                "dropped",
            )
        return [indexed[index] for index in sorted(indexed)] + unindexed

    def tank_pressures(self) -> list[tuple[int, tuple[Decimal | None, Decimal | None]]]:
        """What each cylinder started and finished the dive on, per pod.

        Two sources, joined **per pod and per field** rather than one taking over from the
        other. `tank_summary` is the device's own figure and wins where it has one;
        anything it leaves empty falls through to the ends of that pod's `tank_update`
        telemetry — the first and last reading it sent — since a transmitter streams
        throughout the dive whether or not a summary is also written. The realistic case
        is the partial one: a pod that drops out near the end writes a summary with a
        start pressure and no end, and the last real reading is the one a gas calculation
        turns on.

        Summaries are deduped by `sensor` and merged per field, so a device that writes
        one twice for a pod does not count as two cylinders. A summary with no `sensor`
        cannot be joined to anything and stands as its own cylinder — unless it carries no
        pressures either, in which case it describes nothing at all.
        """
        # In **recorded-time** order, not the order the file listed them in. `series.py`'s
        # first rule is that no writer guarantees it emitted its samples in order, and this
        # pod's own pressure channel is built off the axis, which sorts — so taking the ends
        # off the file order would put one pair of readings on the cylinder and a different
        # pair at the ends of its channel, in one document. Where the two invert it is worse
        # than untidy: the `end > start` guard below drops a real end pressure and keeps a
        # later reading as the start.
        in_order = [point for _, point in sorted(self.scan.points.items())]

        telemetry: dict[int, tuple[Decimal | None, Decimal | None]] = {}
        for sensor in dict.fromkeys(key for point in in_order for key in point.pressures):
            readings = [point.pressures[sensor] for point in in_order if sensor in point.pressures]
            telemetry[sensor] = (readings[0], readings[-1])

        summaries: dict[int, tuple[Decimal | None, Decimal | None]] = {}
        loose: list[tuple[Decimal | None, Decimal | None]] = []
        for summary in self.scan.tanks:
            pressures = (
                _number(_native(summary, "start_pressure")),
                _number(_native(summary, "end_pressure")),
            )
            sensor = _native_raw(summary, "sensor")
            if isinstance(sensor, int) and not isinstance(sensor, bool):
                summaries[sensor] = _merge(summaries.get(sensor, (None, None)), pressures)
            elif pressures != (None, None):
                loose.append(pressures)

        # A pod named by a summary that carries no pressures earns a place only if it also
        # streamed telemetry; otherwise a bare `tank_summary(sensor=…, volume_used=…)`
        # would grow an entirely empty cylinder.
        sensors = [
            sensor
            for sensor in summaries
            if summaries[sensor] != (None, None) or sensor in telemetry
        ]
        sensors += [sensor for sensor in telemetry if sensor not in summaries]
        joined: list[tuple[int, tuple[Decimal | None, Decimal | None]]] = [
            (sensor, _merge(summaries.get(sensor, (None, None)), telemetry.get(sensor, (None, None))))
            for sensor in sensors
        ]
        # A pod with no id of its own is a cylinder without a channel: it is numbered
        # after the identified ones and contributes nothing to the profile.
        return joined + [(-1 - index, pressures) for index, pressures in enumerate(loose)]

    def cylinder(
        self,
        gas: fitdecode.FitDataMessage | None,
        pressures: tuple[Decimal | None, Decimal | None],
        where: str,
    ) -> dict[str, Any]:
        """One `dive_gas`, plus its pod's pressures where the two paired.

        No unit conversion: the profile defines `oxygen_content` and `helium_content` as
        whole percent and a tank pressure as bar, which is what §6.3 holds. `gas` is
        `None` for a cylinder known only from its transmitter.

        **FIT has nowhere to record a cylinder's size** — not on `dive_gas`, and
        `tank_summary` carries only the volume *used* — so `volume` is absent on every
        cylinder this reader produces. That is a property of the format rather than of the
        file, so it is in `docs/fit-mapping.md` and not in the report: a line per cylinder
        saying the format cannot hold something would say it of every FIT ever written.
        """
        cylinder: dict[str, Any] = {}
        blend: dict[str, float] = {}
        for member, source in (("oxygen", "oxygen_content"), ("helium", "helium_content")):
            percent = _number(_native(gas, source))
            if percent is None:
                continue
            if not 0 <= percent <= 100:
                self.note(
                    where,
                    f"a gas records {source.replace('_content', '')} at {percent} percent, outside the 0 to "
                    "100 a mix can be; dropped",
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
        elif gas is not None and not blend:
            self.note(
                where,
                "the device records no gas for one of this dive's cylinders; absent means not recorded, "
                "never air (spec §6.3)",
                "absent",
            )

        start, end = (self.pressure(value, where) for value in pressures)
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
        cylinder.update(blend)

        if _native(gas, "mode") == "closed_circuit_diluent":
            # `mode` is the only field on `dive_gas` that speaks to a cylinder's role, and
            # it answers half the question: its enum is open circuit or closed-circuit
            # diluent, so a diluent identifies itself while `open_circuit` covers a back
            # gas and a stage bottle alike and maps to nothing. Pointedly not derived from
            # `status`, which says whether a gas was carried rather than what for.
            cylinder["role"] = "diluent"
        return cylinder

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

    def axis(self, where: str) -> SampleAxis:
        """The dive's time axis, offered every instant the file recorded a reading at.

        The origin is the session's own start time, so the profile's seconds are elapsed
        time from the moment the dive began — the same instant `started_at` names. A
        reading before it is dropped and reported by the axis, and a file whose session
        recorded no start time falls back to its earliest reading, which is the only other
        thing that can put a sample at zero.
        """
        axis = SampleAxis(self.note, where, noun="record", time_member="timestamp")
        for _ in range(self.scan.undated):
            axis.offer(None, None)
        for channel in self.scan.collisions:
            self.note(
                where,
                f"two messages record a {channel} at one instant; the later reading is dropped, because the "
                "format's sample times are strictly increasing (spec §6.5)",
                "dropped",
            )
        if not self.scan.points:
            return axis

        start = _native(self.scan.session, "start_time")
        origin = start if isinstance(start, datetime) else min(self.scan.points)
        for at, point in self.scan.points.items():
            axis.offer(rounded(Decimal(str((at - origin).total_seconds()))), point)
        return axis

    def read_profile(
        self,
        samples: SampleAxis,
        cylinders: list[dict[str, Any]],
        sensors: list[int],
        where: str,
    ) -> dict[str, Any] | None:
        """The `record` stream, and a transmitter's telemetry, as a §6.5 Profile.

        The channels sit on their own axes rather than one shared one: a Suunto Ocean
        writes 4,295 records of which 431 carry a depth and 4,294 a temperature, and
        padding either to the other's length would invent four thousand depths. That is
        `converting.md`'s no-padding rule meeting the writer that makes it obvious.

        **A ceiling of zero is not a ceiling.** The ceiling is the depth a diver may not
        ascend above, and zero says they may surface — the absence of an obligation rather
        than an obligation at 0 m. Reading it as a reading would draw a flat line along
        the surface across every no-deco dive in a logbook.
        """
        depth = Channel()
        ceiling = Channel()
        temperature = Channel()
        pressures = {sensor: Channel() for sensor in sensors}
        for second, point in samples.ordered():
            if point.depth is not None:
                depth.record(second, rounded(point.depth * CENTIMETRES_PER_METRE))
            if point.ceiling is not None and point.ceiling > 0:
                ceiling.record(second, rounded(point.ceiling * CENTIMETRES_PER_METRE))
            if point.temperature is not None:
                temperature.record(second, rounded(point.temperature * TENTHS_PER_UNIT))
            for sensor, bar in point.pressures.items():
                if sensor in pressures and 0 <= bar <= MAX_CYLINDER_PRESSURE:
                    pressures[sensor].record(second, rounded(bar * TENTHS_PER_UNIT))

        events = self.read_events(samples, where)
        numbered = [(number, pressures[sensor]) for number, sensor in enumerate(sensors) if sensor in pressures]
        profile = samples.profile(
            {"depth": depth, "ceiling": ceiling, "temperature": temperature},
            pressures=numbered,
            events=events,
        )
        # §6.3 calls `gas_number` a label rather than an array index, so a numbering is
        # asserted only where something in the profile depends on it — a pressure channel,
        # or a gas switch naming the cylinder it switched to.
        if profile is not None and (profile.get("pressures") or any("gas_number" in event for event in events)):
            for number, cylinder in enumerate(cylinders):
                cylinder["gas_number"] = number
        return profile

    def read_events(self, samples: SampleAxis, where: str) -> list[dict[str, Any]]:
        """The `event` messages that describe the dive, as §6.5 events.

        The one that needs work is `dive_gas_switched`, whose `data` field holds the
        switched-to gas's **`message_index`** — the device's own key for a `dive_gas`
        entry, which is not the position §6.3 numbers a cylinder by. It is resolved
        through the same list the cylinders were built from, so a marker names the
        cylinder the logbook shows.

        Where it cannot be resolved the marker is still emitted with no `gas_number`. A
        switch to a gas whose `dive_gas` was disabled or absent is a real switch that
        happened, and saying "a gas switch, to something this file does not describe" is
        honest where guessing a position would not be.

        **Untested against a real file**: the only `event` any file in hand writes is
        `timer`, which this table deliberately does not carry.
        """
        if not self.scan.events:
            return []
        positions = {
            index & MESSAGE_INDEX_MASK: number
            for number, index in enumerate(_native_raw(gas, "message_index") for gas in self.gases)
            if isinstance(index, int) and not isinstance(index, bool)
        }
        # The axis is what decided which instants have a place and what second each landed
        # on, so an event asks it rather than recomputing from the session's start time:
        # a sample the axis dropped for sharing a second with an earlier one is an instant
        # the profile does not reach, and an event there has nowhere to go either.
        seconds = {point: second for second, point in samples.ordered()}

        events: list[dict[str, Any]] = []
        for frame in self.scan.events:
            name = _native(frame, "event")
            kind = EVENT_TYPES.get(name) if isinstance(name, str) else None
            at = _native(frame, "timestamp")
            if kind is None or not isinstance(at, datetime):
                continue
            point = self.scan.points.get(at)
            second = seconds.get(point) if point is not None else None
            if second is None:
                self.note(
                    where,
                    "an event is recorded at an instant the dive's samples do not reach, so it has no place "
                    "on the profile's time axis; dropped",
                    "dropped",
                )
                continue
            event: dict[str, Any] = {"time": second, "type": kind}
            data = _native(frame, "data")
            if kind == "gas_switch" and isinstance(data, int) and not isinstance(data, bool):
                number = positions.get(data & MESSAGE_INDEX_MASK)
                if number is not None:
                    event["gas_number"] = number
            if kind == "other":
                # `data` renders through the profile's own `dive_alert` enum, so this is
                # the device's wording — `deco_ceiling_broken`, not a number — and an
                # alert outside the enum decodes to the bare integer, which is still more
                # than "something happened". §6.5 requires a label on `other`, so an event
                # with nothing to say is dropped rather than failing the whole conversion.
                if data is None:
                    self.note(where, "the device records an alert it gives no code for; dropped", "dropped")
                    continue
                event["label"] = str(data)
            events.append(event)
        return events


def _first(*values: Decimal | None) -> Decimal | None:
    """The first value that was actually recorded.

    Not `a or b`: these are physical readings and a legitimately zero one must not fall
    through to the next candidate — which for a depth is then read as not recorded by the
    member's own constraint, one step later and for a stated reason.
    """
    return next((value for value in values if value is not None), None)


def _merge(
    summary: tuple[Decimal | None, Decimal | None], telemetry: tuple[Decimal | None, Decimal | None]
) -> tuple[Decimal | None, Decimal | None]:
    """One pod's figures, preferring its summary and filling gaps from its telemetry."""
    return (_first(summary[0], telemetry[0]), _first(summary[1], telemetry[1]))


__all__ = ["FIT", "FIT_ID_NAMESPACE", "FitAdapter", "FitError", "MalformedFitError"]
