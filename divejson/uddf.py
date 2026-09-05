"""Reading UDDF into DiveJSON.

UDDF is the nominal incumbent, and the format most of a diver's history is trapped in.
This module reads one and produces a DiveJSON document plus a **report** of what the
source did not carry — which is half the output, not a diagnostic afterthought: a
converter that silently fills gaps produces a conforming document that lies, and §5.4 is
the rule it would be breaking.

`docs/uddf-mapping.md` is the prose companion: every element this module reads, every one
it deliberately does not, and the reasoning behind each heuristic. It is written for a
port in another language as much as for a reader of this file, so the *rules* live there
and only their implementation lives here. What is true of every source format rather than
of UDDF — the note kinds, the identity scope, the way a zero reads, the sample axis, the
`<!DOCTYPE>` refusal — lives in `converter.py`, `series.py` and `xmlsource.py`, and this
module inherits it.

Four decisions shape everything below.

**Tags are matched on their lowercased local name.** UDDF appears under at least four root
shapes — the `…/uddf/3.2/` namespace, `…/uddf/3.1/`, no namespace at all (divelogs.de and
APD DiveSight both emit a bare `<uddf>`), and an uppercase `<UDDF>` for 2.x. Stripping
`{uri}` and lowercasing at lookup time collapses all four into one code path, where the
alternative is the union XPaths Subsurface's own maintainers call unwieldy.

**Schema validity is never a precondition.** Both real third-party exports this converter
was built against fail the 3.2.2 XSD, and they fail structurally: ids carrying parentheses
or a leading space, `<link ref>`s pointing at them, empty `<latitude/>` elements where the
schema wants a float, a document with no namespace at all. Gating on validation would
reject the files the converter exists for. So: whitespace is stripped from ids and refs,
an empty element reads as absent, and children are taken **by name rather than by
position** — `diveType`'s child order changed between 3.2.1 and 3.2.2 without the
namespace moving, so any ordering assumption is wrong for half the corpus.

**Identity is derived, never invented fresh.** UDDF ids are XML Names; DiveJSON requires a
UUID on every record and §5.3 asks that identifiers be stable across exports of the same
data. A UUIDv5 over a fixed namespace and `"{kind}:{source id}"` gives both. The kind is
in the hash because a source id is **not** unique within a file: every `<dive>` in a
Subsurface export reuses its enclosing `<repetitiongroup>`'s id, so hashing the bare id
would hand a dive and its group the same UUID. Where a source already emits real UUIDs —
this format's own reference implementation writes `dive-<uuid>` — they are reused, so a
round trip through UDDF comes back with the identities it left with.

**Absence is reported, never filled.** Every member the source did not record is omitted
and named in the report. The two *unit* ambiguities are the deliberate exceptions and are
not the same case: `<tankvolume>`'s cubic-metres-or-litres and `<o2>`'s
fraction-or-percent are values that **were** recorded, whose scale alone is in doubt, so a
magnitude test there interprets data rather than inventing it. Both fire loudly into the
report, as notes of kind `inferred` — this converter decided them — and neither is listed
under `extensions.divejson.inferred`, because the number is still the source's own and
only its scale was resolved.
"""

from __future__ import annotations

import re
import sys
import uuid as uuid_pkg
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from .converter import (
    PRODUCER_KEY,
    Conversion,
    ConverterError,
    NonConformingOutputError,
    Note,
    NoteKind,
    Scope,
    header,
    record_inferred,
    recorded,
)
from .series import Channel, SampleAxis
from .validate import validate_document
from .xmlsource import local_name, parse_xml, root_name

# The format id this adapter registers under, which is also the name of the directory a
# conformance corpus keeps its pairs in.
FORMAT = "uddf"

# uuid5(NAMESPACE_URL, "https://divejson.org/ns/uddf"). Fixed forever: changing it would
# renumber every document any released version of this converter has ever produced, and
# `docs/uddf-mapping.md` *Identity* records the value as normative for any port.
UDDF_ID_NAMESPACE = uuid_pkg.UUID("1b85a949-d5f7-5d67-9d04-dcc78342f907")

# UDDF is SI throughout and DiveJSON is not. Every one of these is a factor whose silent
# corruption produces a document that validates perfectly and is nonsense, so each is
# spelled once here and carries a hand-computed test in `tests/test_uddf_units.py`.
KELVIN_OFFSET = Decimal("273.15")
PASCAL_PER_BAR = Decimal(100_000)
LITRES_PER_CUBIC_METRE = Decimal(1000)
CENTIMETRES_PER_METRE = Decimal(100)
TENTHS_PER_UNIT = Decimal(10)

# At or above which a `<tankvolume>` is read as litres rather than the cubic metres UDDF
# specifies — see `_volume_litres`.
LITRES_THRESHOLD = Decimal(1)

# The largest magnitude a source number may have. Not a physical bound — the format sets
# none on a depth or a temperature, and inventing one here would be this module deciding
# how deep a dive can be. It is a *representability* bound: JSON numbers are doubles in
# every reader this format expects to meet, and a value past that range stops being a
# number on the way out. `json.dumps` writes an overflowed float as the bare token
# `Infinity`, which no RFC 8259 parser accepts, and a reader on a double-based parser turns
# an integer that large back into infinity — in both directions the document silently stops
# being readable, and this converter's own validation does not catch it, because
# `jsonschema` is happy to call infinity a number greater than zero.
#
# Divided by the largest factor any conversion below applies (litres, ×1000), so that
# checking the value on the way in also covers every value derived from it.
MAX_MAGNITUDE = Decimal(sys.float_info.max) / 1000

MAX_NOTES = 10_000
MAX_NAME = 255
MAX_LOCATION = 255
MAX_DISPLAY_NAME = 512
MIN_PO2_LIMIT = Decimal("0.4")
MAX_PO2_LIMIT = Decimal("2.0")
MIN_SURFACE_PRESSURE = Decimal("0.4")
MAX_SURFACE_PRESSURE = Decimal("1.2")
MAX_CYLINDER_PRESSURE = Decimal(350)
MIN_ALTITUDE = -450
MAX_ALTITUDE = 6500

_UUID_TEXT = re.compile(r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")

# Deliberately looser than any address grammar, and it is not trying to be one. `email` is
# the only member in this format whose *type* constrains the text a source can put in it,
# so a value here has to clear that bar or be omitted like anything else the source did not
# record. This admits every real address and rejects what writers actually leave in the
# field — `n/a`, `-`, a person's name, a sentence.
_EMAIL = re.compile(r"\A[^@\s]+@[^@\s]+\Z")

# `xs:dateTime`, leniently. The `T`, the seconds and the offset are each optional because
# real writers omit each of them, and the offset is accepted with or without its colon.
_DATE_TIME = re.compile(
    r"\A(?P<date>\d{4}-\d{2}-\d{2})"
    r"(?:T(?:(?P<hour>\d{2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?(?P<fraction>\.\d+)?)?)?"
    r"(?P<offset>[Zz]|[+-]\d{2}:\d{2}|[+-]\d{4}|[+-]\d{2})?\Z"
)

# Where each of UDDF's typed equipment elements lands in §6.12's vocabulary. Everything
# landing on `other` does so because the vocabulary has no value for it, not because the
# source was silent: `<variouspieces>` is UDDF's own catch-all, and a scooter, a
# rebreather, a weight belt, a compressor and a watch are equipment this format does not
# yet name. Read the mapping off the rows rather than off any count of them.
_GEAR_TYPE: dict[str, str] = {
    "boots": "boots",
    "buoyancycontroldevice": "bcd",
    "camera": "camera",
    "compass": "compass",
    "compressor": "other",
    "divecomputer": "computer",
    "fins": "fins",
    "gloves": "gloves",
    "knife": "knife",
    "lead": "other",
    "light": "light",
    "mask": "mask",
    "rebreather": "other",
    "regulator": "regulator",
    "scooter": "other",
    "suit": "wetsuit",
    "tank": "cylinder",
    "variouspieces": "other",
    "videocamera": "camera",
    "watch": "other",
}

# The `<suittype>` values that mean a dry suit. Everything else it can hold — "wet-suit",
# "shorty", "half-suit", "two-piece" and the rest — leaves `type` at `_GEAR_TYPE`'s
# `wetsuit`, which is what every one of them is.
_DRYSUIT_TYPES = {"dry-suit", "drysuit", "hot-water-suit"}

# A `<setmarker>` whose text is exactly one of these is that event, rather than an
# `"other"` labelled with the word. It is what makes this format's own markers survive a
# round trip through UDDF, whose `<setmarker>` is a bare string with no type beside it.
_MARKER_TYPES = {"deep_stop", "safety_stop", "bookmark"}


class UddfError(ConverterError):
    """The input could not be read as UDDF."""


class MalformedUddfError(UddfError):
    """The input is not well-formed XML, or its root element is not `<uddf>`."""


class UddfAdapter:
    """The registry's view of this reader: what it claims, and how it converts.

    An instance of this is what `registry.py` registers; everything else in this module is
    behind it. `provenance` stays inside the conversion rather than being a hook of its
    own, because what a UDDF file says about itself — its declared version, its
    `<generator>` — is only knowable once the tree is parsed.
    """

    format: str = FORMAT
    suffixes: tuple[str, ...] = (".uddf",)
    namespace: uuid_pkg.UUID = UDDF_ID_NAMESPACE

    def sniff(self, head: bytes) -> bool:
        """Whether a bounded head of bytes opens a UDDF document.

        The root element name, and nothing else. The declared version is deliberately not
        consulted: this reader matches element names rather than versions, and a 2.2.0
        file converts as readily as a 3.2.2 one.
        """
        return root_name(head) == FORMAT

    def convert(self, data: bytes, *, exported_at: datetime, scope: Scope) -> Conversion:
        """Convert one UDDF document into DiveJSON.

        `data` is **bytes**, not text: an XML document declares its own encoding, and a
        UDDF file that says `encoding="ISO-8859-1"` has to be decoded by the parser that
        read that declaration. Handing `ElementTree` a `str` carrying one is a `ValueError`
        anyway.

        Raises `DoctypeRefusedError`, `MalformedUddfError` or `NonConformingOutputError`.
        """
        root = parse_xml(data, root=FORMAT, malformed=MalformedUddfError)
        return _Converter(root, exported_at=exported_at, scope=scope).run()


UDDF = UddfAdapter()


def _attr(element: ET.Element | None, name: str) -> str | None:
    """An attribute by lowercased local name, whitespace stripped.

    Subsurface writes one site id as `" ff47210"`, with the leading space, and the
    `<link ref>`s pointing at it carry the space too — so stripping has to happen on both
    sides, or the reference stops resolving.
    """
    if element is None:
        return None
    for key, value in element.attrib.items():
        _, _, local = key.rpartition("}")
        if local.lower() == name:
            return value.strip() or None
    return None


def _kids(element: ET.Element | None, name: str) -> list[ET.Element]:
    if element is None:
        return []
    return [child for child in element if local_name(child) == name]


def _kid(element: ET.Element | None, name: str) -> ET.Element | None:
    if element is None:
        return None
    for child in element:
        if local_name(child) == name:
            return child
    return None


def _dig(element: ET.Element | None, *names: str) -> ET.Element | None:
    for name in names:
        element = _kid(element, name)
    return element


def _text(element: ET.Element | None) -> str | None:
    """An element's text, stripped. An empty element is absent, not an empty value.

    Subsurface writes `<latitude/>` for a site it has no coordinates for, so this is the
    single most load-bearing leniency in the parser.
    """
    if element is None or element.text is None:
        return None
    return element.text.strip() or None


def _text_of(parent: ET.Element | None, *names: str) -> str | None:
    return _text(_dig(parent, *names))


def _decimal(text: str | None) -> Decimal | None:
    """A number from element text, or `None` for anything that is not a usable one.

    `Decimal` rather than `float` throughout: the input is decimal text and every scale
    below is a decimal factor, so `Decimal("2.6") * 100` is exactly `260` where the float
    route arrives at 260.00000000000003 and has to be rounded back out. `Decimal` also
    accepts `"NaN"` and `"Infinity"` without complaint, which is what the finiteness check
    is for.

    **The magnitude bound is the other half of that check and is not optional.** `Decimal`
    parses `1e999` and `1e999999999` happily and calls both finite, and neither survives
    the trip out: the first becomes a float infinity, which this converter would write into
    a document as a bare `Infinity` token no JSON parser accepts and its own validation
    would not object to; the second overflows `Decimal`'s arithmetic on the next
    multiplication, raising something that is not one of this module's errors and so
    abandoning a whole batch mid-migration rather than failing the one file. Text that
    cannot be carried as a number is treated as text that is not a number, which is what
    it is.
    """
    if text is None:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    # `copy_abs`, not `abs`: the builtin is a context operation and raises `Overflow` on
    # exactly the values this line exists to reject, so the guard would be the thing that
    # crashed. `copy_abs` and the comparison below both leave the context alone.
    if not value.is_finite() or value.copy_abs() > MAX_MAGNITUDE:
        return None
    return value


def _rounded(value: Decimal) -> int:
    """The nearest integer, halves away from zero.

    Python's own `round` is half-to-even, which is the right default for statistics and
    the wrong one for a reading: 2.5 seconds of elapsed time is 3, not 2.
    """
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))


def _integer(value: Decimal | None) -> int | None:
    return None if value is None else _rounded(value)


def _is_uuid(text: str) -> bool:
    return bool(_UUID_TEXT.match(text))


def _embedded_uuid(source_id: str) -> str | None:
    """The UUID a source id already carries, if it carries one.

    `xs:ID` is an `NCName` and cannot begin with a digit, which a hex UUID regularly does,
    so a writer holding real UUIDs prefixes them — this format's reference implementation
    writes `dive-019fec36-…`. Stripping a short alphabetic prefix recovers it, which is
    what lets a logbook survive a round trip through UDDF with its identities intact.
    """
    head, dash, tail = source_id.partition("-")
    if dash and head.isalpha() and len(head) <= 12 and _is_uuid(tail):
        return tail.lower()
    return source_id.lower() if _is_uuid(source_id) else None


def _volume_litres(raw: Decimal) -> tuple[Decimal, bool]:
    """A `<tankvolume>` as litres, and whether it had to be reinterpreted.

    UDDF specifies cubic metres — "not in litres, as UDDF uses SI units!" — and Subsurface
    wrote litres into the field until 2025-09-30, which is in no tagged release but is in
    the 6.0.x master builds people actually run. Both spellings are live in the installed
    base and both are schema-valid, so no validator settles it and a fixture cannot either.

    The magnitude does: a cubic metre of water capacity is a thousand-litre cylinder, and a
    litre-valued 0.012 would be twelve millilitres. The same `< 1` threshold is what
    Bubbletrail's UDDF importer uses.
    """
    if raw < LITRES_THRESHOLD:
        return raw * LITRES_PER_CUBIC_METRE, False
    return raw, True


def _gas_percent(raw: Decimal) -> tuple[Decimal, bool]:
    """An `<o2>`/`<he>` as a percentage, and whether it had to be reinterpreted.

    The UDDF documentation calls it "a real number less or equal 1.0 in percent", which
    contradicts itself, and writers took both readings: pre-2017 Subsurface wrote
    `<o2>34</o2>` where current writers write `0.34`. A value at or below 1 is the
    documented fraction — 1.0 being pure oxygen, since a 1 % mix is not a breathing gas —
    and anything above it was already a percentage.
    """
    if raw <= 1:
        return raw * 100, False
    return raw, True


def _date_time(text: str) -> tuple[str | None, str | None]:
    """A DiveJSON date-time from `xs:dateTime` text, plus what had to be forgiven.

    Returns `(value, note)`; `value` is `None` when the text is not a date and time at all.

    **The offset is preserved exactly as recorded, and never supplied.** That is §5.2's
    whole point, and converting to UTC — or assuming an offset where the source recorded
    none — is the failure every tested UDDF consumer produced.

    The forgiven shapes are real writer output rather than hypotheticals: Subsurface emits
    a midnight dive as `<datetime>2002-06-18T</datetime>`, its XSLT building the string
    with an unguarded `concat`, and a bare date is what the same bug produces one character
    earlier.
    """
    match = _DATE_TIME.match(text.strip())
    if match is None:
        return None, None

    parts = match.groupdict()
    note = None
    if parts["hour"] is None:
        note = f"{text.strip()!r} records a date with no time of day; read as midnight"
        hour, minute, second = "00", "00", "00"
    else:
        hour, minute, second = parts["hour"], parts["minute"], parts["second"]
        if second is None:
            note = f"{text.strip()!r} records no seconds; read as :00"
            second = "00"

    try:
        datetime.fromisoformat(f"{parts['date']}T{hour}:{minute}:{second}")
    except ValueError:
        return None, None

    value = f"{parts['date']}T{hour}:{minute}:{second}{parts['fraction'] or ''}"
    offset = parts["offset"]
    if offset is None:
        return value, note
    if offset in ("Z", "z"):
        return value + offset, note
    if len(offset) == 3:  # +02
        return f"{value}{offset}:00", note
    if len(offset) == 5:  # +0200
        return f"{value}{offset[:3]}:{offset[3:]}", note
    return value + offset, note


def _has_offset(value: str) -> bool:
    _, _, time_part = value.partition("T")
    return time_part.endswith(("Z", "z")) or "+" in time_part or "-" in time_part


class _Converter:
    def __init__(self, root: ET.Element, *, exported_at: datetime, scope: Scope) -> None:
        self.root = root
        self.exported_at = exported_at
        self.scope = scope
        self.notes: list[Note] = []
        # Shared with the rest of the archive this file came from, if it came from one, so
        # that a record two members both define is written once and referred to by both —
        # see `uuid_for`, which is where that is decided and where the *other* case, two
        # records in one file naming one id, is still refused.
        self.claimed = scope.claimed
        self.inferred: list[str] = []
        self.source_ids: set[str] = set()
        self.site_uuids: dict[str, str] = {}
        self.trip_uuids: dict[str, str] = {}
        self.gear_uuids: dict[str, str] = {}
        self.mixes: dict[str, dict[str, Any]] = {}

    # -- reporting ---------------------------------------------------------------

    def note(self, where: str, message: str, kind: NoteKind) -> None:
        """One line of the report, at a path into the source this conversion read."""
        self.notes.append(Note(self.scope.where(where), message, kind))

    # -- identity ----------------------------------------------------------------

    def uuid_for(self, kind: str, source_id: str | None, where: str, index: int) -> tuple[str | None, bool]:
        """A record's stable UUID, and whether **this** file is the one that carries it.

        Three outcomes, and the middle one is the whole of what an archive needs.

        The identity is nobody's yet: `(uuid, True)`, and the record is written here.

        The identity belongs to a record in **another member of the same archive**:
        `(uuid, False)`. That is one record defined twice, which is the ordinary shape of a
        per-dive export — each file repeats the site it was at and the gear it was dived
        with — so its row is written once and this file's references resolve to it. Not a
        note: nothing was lost, and a note per repeat would be one line for every file in
        the archive saying that the archive is shaped the way archives are.

        The identity belongs to another record in **this same file**: `(None, False)`,
        reported. Two records in one file cannot share one identity (spec §5.3), and there
        is no other record to resolve to.
        """
        if source_id is None:
            self.note(
                where,
                f"the source gives this {kind} no id, so its identity is derived from its position in the "
                "file and will move if the file's order changes (spec §5.3)",
                "absent",
            )
            # Prefixed by the archive member this file is, so that two members whose
            # records carry no ids do not derive one identity from one position.
            source_id = self.scope.positional(index)

        derived = str(uuid_pkg.uuid5(UDDF_ID_NAMESPACE, f"{kind}:{source_id}"))
        for candidate in dict.fromkeys((_embedded_uuid(source_id) or derived, derived)):
            holder = self.claimed.get(candidate)
            if holder is None:
                self.claimed[candidate] = (self.scope.member, self.scope.where(where))
                return candidate, True
            if holder[0] != self.scope.member:
                return candidate, False

        self.note(
            where,
            f"a second {kind} carries the id {source_id!r}, already used by {self.claimed[derived][1]}; the "
            "record is dropped, because two records cannot share one identity (spec §5.3)",
            "dropped",
        )
        return None, False

    # -- text --------------------------------------------------------------------

    def capped(self, value: str, limit: int, where: str, member: str) -> str:
        if len(value) <= limit:
            return value
        self.note(
            where,
            f"{member} is {len(value)} characters; the format caps it at {limit} and the rest is dropped",
            "dropped",
        )
        return value[:limit]

    def email(self, value: str | None, where: str) -> str | None:
        """`<contact><email>` when it is an address, and nothing when it is not.

        Every other source string reaches a member the format types as free text, where the
        only limit is a length this converter caps. `email` is the exception — the schema
        types it as an email address, so a `-` or an `n/a` is a value the member cannot
        hold. Without this the whole conversion fails on it: the output would not validate,
        which this module treats as its own bug, so one unusable header field would discard
        an entire logbook instead of costing it one member and a line in the report.
        """
        if value is None or _EMAIL.match(value):
            return value
        self.note(where, f"the recorded email {value!r} is not an address; read as no email recorded", "dropped")
        return None

    def notes_text(self, parent: ET.Element | None, where: str) -> str | None:
        """A `<notes>` block as one string. Its `<link>` children carry no note text."""
        notes = _kid(parent, "notes")
        if notes is None:
            return None
        paragraphs = [text for text in (_text(para) for para in _kids(notes, "para")) if text]
        if not paragraphs:
            return None
        return self.capped("\n\n".join(paragraphs), MAX_NOTES, where, "the note")

    # -- geometry ----------------------------------------------------------------

    def position(self, geography: ET.Element | None, where: str) -> dict[str, float] | None:
        """A Position from `<geography>`, or `None` where there is not an honest one.

        Two sources of nothing, and the second is the interesting one. An empty
        `<latitude/>` is Subsurface saying it has no coordinates. An exact `0.000000` pair
        is divelogs.de saying the same thing in the one spelling that looks like an answer:
        every site in its export carries it, and a reader that trusts Null Island pins a
        Red Sea wreck into the Atlantic.
        """
        latitude = _decimal(_text_of(geography, "latitude"))
        longitude = _decimal(_text_of(geography, "longitude"))
        if latitude is None or longitude is None:
            if latitude is not None or longitude is not None:
                self.note(where, "only one half of a coordinate pair was recorded, and a position needs both; dropped (spec §6)", "dropped")
            return None
        if latitude == 0 and longitude == 0:
            self.note(
                where,
                "the coordinates are exactly 0.000000 / 0.000000, which writers emit to mean 'unknown'; read as "
                "no position rather than as Null Island",
                "absent",
            )
            return None
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            self.note(where, f"the coordinates {latitude} / {longitude} are outside the WGS 84 range; dropped", "dropped")
            return None
        return {"latitude": float(latitude), "longitude": float(longitude)}

    # -- the run -----------------------------------------------------------------

    def run(self) -> Conversion:
        for element in self.root.iter():
            source_id = _attr(element, "id")
            if source_id is not None:
                self.source_ids.add(source_id)

        # Order matters: the dives resolve links into the tables the four calls above it
        # fill, and the diver is last only so its identity yields to a real record's on
        # the vanishingly rare id collision.
        self.read_mixes()
        sites = self.read_sites()
        trips = self.read_trips()
        gear = self.read_gear()
        dives = self.read_dives()
        diver = self.read_diver()

        document: dict[str, Any] = header(self.exported_at)
        if diver:
            document["diver"] = diver
        for member, rows in (("dives", dives), ("trips", trips), ("sites", sites), ("gear", gear)):
            if rows:
                document[member] = rows
        document["extensions"] = {PRODUCER_KEY: self.provenance()}

        if self.scope.validates_alone:
            issues = validate_document(document)
            if issues:
                raise NonConformingOutputError(issues)
        return Conversion(document, tuple(self.notes))

    def provenance(self) -> dict[str, Any]:
        """What the source file said about itself.

        Under a producer key rather than in `generator`, which §4 defines as what produced
        *this* document — and that is the converter. The source's own identity is worth
        keeping and has nowhere in the core vocabulary to go.

        The provenance block is also where a converter labels what it derived, so the
        inferred list lands here (spec §5.4) — empty for every UDDF conversion, since a
        `<tankvolume>` read as litres is a recorded number at a resolved scale rather than
        a value computed from other readings.
        """
        provenance: dict[str, Any] = {"converted_from": FORMAT}
        version = _attr(self.root, "version")
        if version:
            provenance["uddf_version"] = version
        generator = _kid(self.root, "generator")
        name = _text_of(generator, "name")
        if name:
            source: dict[str, str] = {"name": name}
            source_version = _text_of(generator, "version")
            if source_version:
                source["version"] = source_version
            provenance["source_generator"] = source
        record_inferred(provenance, self.inferred)
        return provenance

    # -- diver -------------------------------------------------------------------

    def read_diver(self) -> dict[str, Any] | None:
        """The logbook's owner, or nothing at all.

        §6.1 is explicit that a converter whose source records nothing about an owner omits
        the member entirely — minting identity for one would be §5.4's fabrication applied
        to people. `<owner id>` is deliberately not read as a name or a handle: it is an
        XML id, and Subsurface's is the literal string "owner".

        **That is also why the diver is the one record that does not take `uuid_for`'s
        shared-record path.** Two archive members naming one site id are naming one site;
        two naming one *owner* id are naming nothing, because the id is a convention rather
        than an identity and every UDDF writer in the corpus spells it `owner`. So a second
        member's diver is written out — without the identity that is not its own — and the
        merge is where a logbook's one owner is chosen and the rest reported. Collapsing it
        here would discard a second person's name and email in silence.
        """
        owner = _dig(self.root, "diver", "owner")
        if owner is None:
            return None
        where = "diver"
        names = [
            text
            for text in (_text_of(owner, "personal", part) for part in ("firstname", "middlename", "lastname"))
            if text
        ]
        email = self.email(_text_of(owner, "contact", "email"), where)
        if not names and not email:
            self.note(where, "the source records nothing about the logbook's owner; no diver is written (spec §6.1)", "absent")
            return None

        claimed, carried = self.uuid_for("diver", _attr(owner, "id"), where, 0)
        diver: dict[str, Any] = {}
        if claimed is not None and carried:
            diver["uuid"] = claimed
        if names:
            diver["name"] = self.capped(" ".join(names), MAX_NAME, where, "the diver's name")
        if email:
            diver["email"] = email
        return diver

    # -- sites -------------------------------------------------------------------

    def read_sites(self) -> list[dict[str, Any]]:
        sites: list[dict[str, Any]] = []
        for index, element in enumerate(_kids(_kid(self.root, "divesite"), "site")):
            where = f"site/{index}"
            name = _text_of(element, "name")
            if not name:
                self.note(
                    where,
                    "the site has no name, which the format requires of one; it is dropped along with the "
                    "references to it, because a name cannot be invented (spec §6.10)",
                    "dropped",
                )
                continue
            claimed, carried = self.uuid_for("site", _attr(element, "id"), where, index)
            if claimed is None:
                continue
            source_id = _attr(element, "id")
            if source_id:
                # Recorded before the row is written, and whether or not it is: a repeat of
                # another archive member's record is not carried again, and this file's
                # references to it still have to resolve to the one that is.
                self.site_uuids[source_id] = claimed
            if not carried:
                continue

            site: dict[str, Any] = {"uuid": claimed, "name": self.capped(name, MAX_NAME, where, "the site name")}
            geography = _kid(element, "geography")
            location = _text_of(geography, "location")
            if location:
                site["location"] = self.capped(location, MAX_LOCATION, where, "the site location")
            position = self.position(geography, where)
            if position:
                site["position"] = position
            notes = self.notes_text(element, where)
            if notes:
                site["notes"] = notes

            sites.append(site)
        return sites

    # -- trips -------------------------------------------------------------------

    def read_trips(self) -> list[dict[str, Any]]:
        """`<divetrip><trip>` as Trip records.

        A `<trippart>` becomes a Trip Location: UDDF models a trip as a sequence of parts,
        each with its own place and dates, and §6.9's location list is the nearest thing
        this format has. The trip's own dates are the span of its parts, because `tripType`
        records none of its own.
        """
        trips: list[dict[str, Any]] = []
        for index, element in enumerate(_kids(_kid(self.root, "divetrip"), "trip")):
            where = f"trip/{index}"
            name = _text_of(element, "name")
            if not name:
                self.note(where, "the trip has no name, which the format requires of one; it is dropped (spec §6.8)", "dropped")
                continue

            starts, ends, locations, notes = self.read_trip_parts(element, where)
            if starts is None:
                self.note(
                    where,
                    "the trip records no dates, and the format requires a start date; it is dropped along with "
                    "the dives' membership of it (spec §6.8)",
                    "dropped",
                )
                continue
            claimed, carried = self.uuid_for("trip", _attr(element, "id"), where, index)
            if claimed is None:
                continue
            source_id = _attr(element, "id")
            if source_id:
                # Recorded before the row is written, and whether or not it is: a repeat of
                # another archive member's record is not carried again, and this file's
                # references to it still have to resolve to the one that is.
                self.trip_uuids[source_id] = claimed
            if not carried:
                continue

            trip: dict[str, Any] = {"uuid": claimed, "name": self.capped(name, MAX_NAME, where, "the trip name")}
            if locations:
                trip["locations"] = locations
            trip["starts_on"] = starts
            if ends is not None and ends >= starts:
                trip["ends_on"] = ends
            elif ends is not None:
                self.note(where, f"the trip ends on {ends}, before it starts on {starts}; the end date is dropped", "dropped")
            if notes:
                trip["notes"] = notes

            trips.append(trip)
        return trips

    def read_trip_parts(
        self, element: ET.Element, where: str
    ) -> tuple[str | None, str | None, list[dict[str, Any]], str | None]:
        starts: list[str] = []
        ends: list[str] = []
        locations: list[dict[str, Any]] = []
        paragraphs: list[str] = []

        for part_index, part in enumerate(_kids(element, "trippart")):
            part_where = f"{where}/trippart/{part_index}"
            date_of_trip = _kid(part, "dateoftrip")
            for attribute, collected in (("startdate", starts), ("enddate", ends)):
                raw = _attr(date_of_trip, attribute)
                if raw is None:
                    continue
                value, _ = _date_time(raw)
                if value is None:
                    self.note(part_where, f"{attribute} is {raw!r}, which is not a date; dropped", "dropped")
                else:
                    collected.append(value[:10])

            geography = _kid(part, "geography")
            part_name = _text_of(part, "name")
            display_name = _text_of(geography, "location")
            if part_name:
                location: dict[str, Any] = {"name": self.capped(part_name, MAX_NAME, part_where, "the trip part's name")}
                if display_name and display_name != part_name:
                    location["display_name"] = self.capped(display_name, MAX_DISPLAY_NAME, part_where, "the location")
                position = self.position(geography, part_where)
                if position:
                    location["position"] = position
                locations.append(location)
            elif geography is not None:
                self.note(
                    part_where,
                    "the trip part has no name, which the format requires of a location; the place is dropped "
                    "(spec §6.9)",
                    "dropped",
                )

            part_notes = self.notes_text(part, part_where)
            if part_notes:
                paragraphs.append(part_notes)

        joined = self.capped("\n\n".join(paragraphs), MAX_NOTES, where, "the trip note") if paragraphs else None
        return (min(starts) if starts else None), (max(ends) if ends else None), locations, joined

    # -- gear --------------------------------------------------------------------

    def read_gear(self) -> list[dict[str, Any]]:
        """The owner's kit list, from `<diver><owner><equipment>`.

        `<equipmentconfiguration>` is skipped along with anything else unrecognized: it
        describes how the pieces are rigged together, not a piece.
        """
        equipment = _dig(self.root, "diver", "owner", "equipment")
        # `is not None`, never a truth test: an `Element` with no children is falsy today
        # and `ElementTree` warns that it will not be.
        pieces = [child for child in equipment if local_name(child) in _GEAR_TYPE] if equipment is not None else []
        gear: list[dict[str, Any]] = []
        for index, element in enumerate(pieces):
            kind = local_name(element)
            where = f"gear/{index}"
            name = _text_of(element, "name")
            if not name:
                self.note(
                    where,
                    f"the <{kind}> has no name, which the format requires of a gear item; it is dropped "
                    "(spec §6.12)",
                    "dropped",
                )
                continue
            claimed, carried = self.uuid_for("gear", _attr(element, "id"), where, index)
            if claimed is None:
                continue
            source_id = _attr(element, "id")
            if source_id:
                # Recorded before the row is written, and whether or not it is: a repeat of
                # another archive member's record is not carried again, and this file's
                # references to it still have to resolve to the one that is.
                self.gear_uuids[source_id] = claimed
            if not carried:
                continue

            item: dict[str, Any] = {"uuid": claimed, "name": self.capped(name, MAX_NAME, where, "the gear name")}
            brand = _text_of(element, "manufacturer", "name")
            if brand:
                item["brand"] = self.capped(brand, MAX_NAME, where, "the brand")
            gear_type = _GEAR_TYPE[kind]
            if kind == "suit" and (_text_of(element, "suittype") or "").lower() in _DRYSUIT_TYPES:
                gear_type = "drysuit"
            item["type"] = gear_type
            notes = self.notes_text(element, where)
            if notes:
                item["notes"] = notes

            gear.append(item)
        return gear

    # -- gases -------------------------------------------------------------------

    def read_mixes(self) -> None:
        """`<gasdefinitions><mix>` into a table the dives' `<tankdata>` link into.

        Gas mixes are not a DiveJSON collection: the format carries the blend on the
        cylinder that held it, so a mix nothing links to travels nowhere. A gas list with
        no cylinder using it is a plan rather than a dive, which is why that is not
        reported here.
        """
        for index, element in enumerate(_kids(_kid(self.root, "gasdefinitions"), "mix")):
            source_id = _attr(element, "id")
            if source_id is None:
                continue
            where = f"mix/{index}"
            mix: dict[str, Any] = {}
            for member, tag in (("oxygen", "o2"), ("helium", "he")):
                raw = _decimal(_text_of(element, tag))
                if raw is None:
                    continue
                percent, reinterpreted = _gas_percent(raw)
                if reinterpreted:
                    self.note(
                        where,
                        f"<{tag}> is {raw}, above the 1.0 the documentation describes; read as {percent} percent "
                        "rather than as a fraction",
                        "inferred",
                    )
                if not 0 <= percent <= 100:
                    self.note(where, f"<{tag}> reads as {percent} percent, outside the 0 to 100 a fraction can be; dropped", "dropped")
                    continue
                mix[member] = float(percent)
            if mix.get("oxygen", 0.0) + mix.get("helium", 0.0) > 100:
                self.note(where, "oxygen and helium sum above 100 percent, which no mix can; both are dropped (spec §6.3)", "dropped")
                mix.pop("oxygen", None)
                mix.pop("helium", None)

            po2_limit = _decimal(_text_of(element, "maximumpo2"))
            if po2_limit is not None:
                if MIN_PO2_LIMIT <= po2_limit <= MAX_PO2_LIMIT:
                    mix["po2_limit"] = float(po2_limit)
                else:
                    self.note(where, f"<maximumpo2> is {po2_limit} bar, outside the 0.4 to 2.0 the format allows; dropped", "dropped")
            self.mixes[source_id] = mix

    # -- dives -------------------------------------------------------------------

    def dive_elements(self) -> list[ET.Element]:
        """Every `<dive>` under `<profiledata>`, in document order.

        Taken *through* the repetition groups rather than from them: a group is a surface
        interval's worth of dives and carries nothing this format records, so it is a
        container the converter walks past.
        """
        profiledata = _kid(self.root, "profiledata")
        return [dive for group in _kids(profiledata, "repetitiongroup") for dive in _kids(group, "dive")]

    def read_dives(self) -> list[dict[str, Any]]:
        dives = []
        for index, element in enumerate(self.dive_elements()):
            dive = self.read_dive(element, index)
            if dive is not None:
                dives.append(dive)
        return dives

    def read_dive(self, element: ET.Element, index: int) -> dict[str, Any] | None:
        where = f"dive/{index}"
        before = _kid(element, "informationbeforedive")
        after = _kid(element, "informationafterdive")

        started_at = self.read_started_at(before, where)
        if started_at is None:
            return None
        claimed, carried = self.uuid_for("dive", _attr(element, "id"), where, index)
        if claimed is None or not carried:
            # `not carried`: an archive holding one dive twice carries it once.
            return None

        dive: dict[str, Any] = {"uuid": claimed}
        number = _integer(_decimal(_text_of(before, "divenumber")))
        if number is not None:
            dive["dive_number"] = number
        dive["started_at"] = started_at

        duration = _integer(_decimal(_text_of(after, "diveduration")))
        if duration is not None:
            if recorded(duration, record="dive", member="duration"):
                dive["duration"] = duration
            else:
                self.note(where, f"<diveduration> is {duration} seconds; the format records a duration only when it is positive", "absent")

        notes = self.notes_text(after, where)
        if notes:
            dive["notes"] = notes

        max_depth = self.positive(_decimal(_text_of(after, "greatestdepth")), where, "<greatestdepth>", "max_depth")
        avg_depth = self.positive(_decimal(_text_of(after, "averagedepth")), where, "<averagedepth>", "avg_depth")
        if max_depth is not None and avg_depth is not None and avg_depth > max_depth:
            self.note(
                where,
                f"the mean depth {avg_depth} m is deeper than the greatest depth {max_depth} m, which cannot be; "
                "the mean is dropped rather than either being adjusted to fit (spec §6.2)",
                "dropped",
            )
            avg_depth = None
        if max_depth is not None:
            dive["max_depth"] = float(max_depth)
        if avg_depth is not None:
            dive["avg_depth"] = float(avg_depth)

        lowest = _decimal(_text_of(after, "lowesttemperature"))
        if lowest is not None:
            dive["bottom_temperature"] = float(lowest - KELVIN_OFFSET)

        visibility = _decimal(_text_of(after, "visibility"))
        if visibility is not None:
            if recorded(visibility, record="dive", member="visibility"):
                dive["visibility"] = float(visibility)
            else:
                self.note(where, f"<visibility> is {visibility} m; dropped", "dropped")

        used = _kid(before, "equipmentused")
        weight = _decimal(_text_of(used, "leadquantity"))
        if weight is not None:
            # A zero is kept here and read as absence for `max_depth`, and neither is this
            # module's choice: `weight`'s floor in the schema is inclusive and
            # `max_depth`'s is not. A recorded "no lead" is a fact about the dive, and
            # absence is how "we do not know" is spelled here (spec §6.2).
            if recorded(weight, record="dive", member="weight"):
                dive["weight"] = float(weight)
            else:
                self.note(where, f"<leadquantity> is {weight} kg; dropped", "dropped")

        altitude = _integer(_decimal(_text_of(before, "altitude")))
        if altitude is not None:
            if MIN_ALTITUDE <= altitude <= MAX_ALTITUDE:
                dive["altitude"] = altitude
            else:
                self.note(where, f"<altitude> is {altitude} m, outside the -450 to 6500 the format allows; dropped", "dropped")

        surface_pressure = _decimal(_text_of(before, "surfacepressure"))
        if surface_pressure is not None:
            bar = surface_pressure / PASCAL_PER_BAR
            if MIN_SURFACE_PRESSURE <= bar <= MAX_SURFACE_PRESSURE:
                dive["surface_pressure"] = float(bar)
            else:
                self.note(where, f"<surfacepressure> reads as {bar} bar, outside the 0.4 to 1.2 the format allows; dropped", "dropped")

        trip_uuid = self.reference(_attr(_kid(before, "tripmembership"), "ref"), self.trip_uuids, where, "trip")
        if trip_uuid:
            dive["trip_uuid"] = trip_uuid
        site_uuids = self.references([_attr(link, "ref") for link in _kids(before, "link")], self.site_uuids, where, "dive site")
        if site_uuids:
            dive["site_uuids"] = site_uuids
        gear_uuids = self.references([_attr(link, "ref") for link in _kids(used, "link")], self.gear_uuids, where, "gear item")
        if gear_uuids:
            dive["gear_uuids"] = gear_uuids

        cylinders, mix_refs = self.read_cylinders(element, where)
        profile, needs_gas_numbers = self.read_profile(element, where, mix_refs)
        if needs_gas_numbers:
            for gas_number, cylinder in enumerate(cylinders):
                cylinder["gas_number"] = gas_number
            self.note(
                where,
                "UDDF records no gas numbering, so the dive's cylinders are numbered from 0 in the order the "
                "file lists them, to tie each pressure channel and gas switch to its cylinder (spec §6.5)",
                "absent",
            )
        if cylinders:
            dive["cylinders"] = cylinders
        if profile:
            dive["profile"] = profile
        return dive

    def read_started_at(self, before: ET.Element | None, where: str) -> str | None:
        raw = _text_of(before, "datetime")
        if raw is None:
            self.note(
                where,
                "the dive records no <datetime>, and the format requires a start time; the dive is dropped "
                "(spec §6.2)",
                "dropped",
            )
            return None
        started_at, forgiven = _date_time(raw)
        if started_at is None:
            self.note(where, f"<datetime> is {raw!r}, which is not a date and time; the dive is dropped (spec §6.2)", "dropped")
            return None
        if forgiven:
            self.note(where, forgiven, "absent")
        if not _has_offset(started_at):
            self.note(where, "the source recorded no UTC offset on the dive's start time; the wall clock travels alone (spec §5.2)", "absent")
        return started_at

    def positive(self, value: Decimal | None, where: str, source: str, member: str) -> Decimal | None:
        """A measurement the format records only when it is above zero.

        Zero is what this format's own UDDF writer emits for a depth it never had —
        `<greatestdepth>` is mandatory in UDDF and optional here — so reading it back as a
        measurement would turn "not recorded" into "the surface". Which way the zero reads
        is the schema's decision rather than this module's, so `member` names the DiveJSON
        member the value is headed for and `recorded` asks it.
        """
        if value is None or recorded(value, record="dive", member=member):
            return value
        self.note(where, f"{source} is {value}, which the format records only when positive; read as not recorded", "absent")
        return None

    def reference(self, ref: str | None, table: dict[str, str], where: str, kind: str) -> str | None:
        resolved = self.references([ref], table, where, kind)
        return resolved[0] if resolved else None

    def references(self, refs: list[str | None], table: dict[str, str], where: str, kind: str) -> list[str]:
        """Resolved references, in source order, without repeats.

        Order is meaningful — §5.3 makes the first `site_uuids` entry the primary site —
        and the schema forbids the same uuid twice in one list.
        """
        resolved: list[str] = []
        for ref in refs:
            if ref is None:
                continue
            if ref in table:
                if table[ref] not in resolved:
                    resolved.append(table[ref])
            elif ref not in self.source_ids:
                self.note(where, f"a link points at {ref!r}, which nothing in the file defines; the reference is dropped", "dropped")
            elif ref not in self.mixes:
                # A `<link>` under `informationbeforedive` addresses a site here, but the
                # schema lets it address a buddy or a shop too, and one under
                # `<equipmentused>` addresses a piece of kit. A reference to a record this
                # converter carries nowhere is worth a note; a gas reference is not.
                self.note(where, f"a link points at {ref!r}, which is not a {kind} this converter carries; the reference is dropped", "dropped")
        return resolved

    # -- cylinders ---------------------------------------------------------------

    def read_cylinders(self, element: ET.Element, where: str) -> tuple[list[dict[str, Any]], list[str | None]]:
        """A dive's `<tankdata>` as Cylinders, plus the mix each one links to.

        The second return value is what the profile needs: `<tankpressure ref>` and
        `<switchmix ref>` both address a *mix*, so tying a channel to its cylinder means
        going back through the link that cylinder made.
        """
        cylinders: list[dict[str, Any]] = []
        mix_refs: list[str | None] = []
        for index, tank in enumerate(_kids(element, "tankdata")):
            tank_where = f"{where}/tankdata/{index}"
            cylinder: dict[str, Any] = {}

            raw_volume = _decimal(_text_of(tank, "tankvolume"))
            if raw_volume is None:
                self.note(
                    tank_where,
                    "the source records no cylinder size; the cylinder carries its gas and pressures without "
                    "one (spec §6.3)",
                    "absent",
                )
            else:
                litres, reinterpreted = _volume_litres(raw_volume)
                if reinterpreted:
                    self.note(
                        tank_where,
                        f"<tankvolume> is {raw_volume}, too large to be the cubic metres UDDF specifies; read as "
                        f"{litres} litres, which is how some builds of Subsurface write it",
                        "inferred",
                    )
                if recorded(litres, record="cylinder", member="volume"):
                    cylinder["volume"] = float(litres)
                else:
                    self.note(tank_where, f"<tankvolume> reads as {litres} litres; the format records a size only when positive", "absent")

            start = self.pressure_bar(_text_of(tank, "tankpressurebegin"), tank_where, "<tankpressurebegin>")
            end = self.pressure_bar(_text_of(tank, "tankpressureend"), tank_where, "<tankpressureend>")
            if start is not None and not recorded(start, record="cylinder", member="start_pressure"):
                # §6.3 is explicit: a recorded zero start pressure is a device's
                # absent-marker rather than a measurement, and writers must not emit it.
                self.note(
                    tank_where,
                    "the start pressure is 0 bar, which devices write to mean 'not recorded'; read as not "
                    "recorded (spec §6.3)",
                    "absent",
                )
                start = None
            if start is not None and end is not None and end > start:
                self.note(
                    tank_where,
                    f"the end pressure {end} bar is above the start pressure {start} bar, which cannot be; the "
                    "end pressure is dropped",
                    "dropped",
                )
                end = None
            if start is not None:
                cylinder["start_pressure"] = float(start)
            if end is not None:
                cylinder["end_pressure"] = float(end)

            mix_ref = _attr(_kid(tank, "link"), "ref")
            if mix_ref is None:
                self.note(tank_where, "the source records no gas for this cylinder; absent means not recorded, never air (spec §6.3)", "absent")
            elif mix_ref in self.mixes:
                cylinder.update(self.mixes[mix_ref])
            else:
                self.note(
                    tank_where,
                    f"the cylinder links to the gas {mix_ref!r}, which <gasdefinitions> does not define; its mix "
                    "is not recorded",
                    "absent",
                )

            cylinders.append(cylinder)
            mix_refs.append(mix_ref)
        return cylinders, mix_refs

    def pressure_bar(self, text: str | None, where: str, member: str) -> Decimal | None:
        value = _decimal(text)
        if value is None:
            return None
        bar = value / PASCAL_PER_BAR
        if not 0 <= bar <= MAX_CYLINDER_PRESSURE:
            self.note(where, f"{member} reads as {bar} bar, outside the 0 to 350 the format allows; dropped", "dropped")
            return None
        return bar

    # -- profile -----------------------------------------------------------------

    def read_profile(
        self, element: ET.Element, where: str, mix_refs: list[str | None]
    ) -> tuple[dict[str, Any] | None, bool]:
        """`<samples><waypoint>` as a Profile, and whether the dive needs gas numbers.

        UDDF puts every reading taken at one instant inside one `<waypoint>`; DiveJSON
        splits them into channels sampled on their own axes. So the waypoints set the time
        axis and each channel takes only the waypoints that actually carried a reading for
        it — which is why a converted Subsurface dive keeps 431 depth samples and 29
        temperatures rather than padding the second to match the first.

        The axis itself — the ordering, the dropped and reported waypoints, the two on one
        second, the profile that is not written at all — is `series.SampleAxis`, shared
        with every other format, and only what is UDDF's is below: which element carries
        which channel, and what a `<tankpressure ref>` resolves to.
        """
        samples = _kid(element, "samples")
        if samples is None:
            return None, False

        axis = SampleAxis(self.note, where, noun="waypoint", time_member="<divetime>")
        for waypoint in _kids(samples, "waypoint"):
            axis.offer(_integer(_decimal(_text_of(waypoint, "divetime"))), waypoint)

        cylinders_of_mix: dict[str, list[int]] = {}
        for index, ref in enumerate(mix_refs):
            if ref is not None:
                cylinders_of_mix.setdefault(ref, []).append(index)

        depth = Channel()
        temperature = Channel()
        pressures: dict[int, Channel] = {}
        events: list[dict[str, Any]] = []
        needs_gas_numbers = False

        for second, waypoint in axis.ordered():
            metres = _decimal(_text_of(waypoint, "depth"))
            if metres is not None:
                depth.record(second, _rounded(metres * CENTIMETRES_PER_METRE))

            kelvin = _decimal(_text_of(waypoint, "temperature"))
            if kelvin is not None:
                temperature.record(second, _rounded((kelvin - KELVIN_OFFSET) * TENTHS_PER_UNIT))

            for cylinder_index, tenths in self.waypoint_pressures(waypoint, where, second, cylinders_of_mix, len(mix_refs)):
                channel = pressures.setdefault(cylinder_index, Channel())
                if not channel.record(second, tenths):
                    self.note(where, f"two tank pressures at {second} s resolve to the same cylinder; the later one is dropped", "dropped")
                    continue
                needs_gas_numbers = True

            marker = _text_of(waypoint, "setmarker")
            if marker is not None:
                if marker in _MARKER_TYPES:
                    events.append({"time": second, "type": marker})
                else:
                    events.append({"time": second, "type": "other", "label": marker})

            switch = _kid(waypoint, "switchmix")
            if switch is not None:
                event: dict[str, Any] = {"time": second, "type": "gas_switch"}
                ref = _attr(switch, "ref")
                if ref is not None and ref in cylinders_of_mix:
                    event["gas_number"] = cylinders_of_mix[ref][0]
                    needs_gas_numbers = True
                elif ref is not None:
                    self.note(
                        where,
                        f"a gas switch at {second} s names the gas {ref!r}, which no cylinder on this dive links "
                        "to; the switch is kept without saying what it was to (spec §6.6)",
                        "absent",
                    )
                events.append(event)

        profile = axis.profile(
            {"depth": depth, "temperature": temperature},
            pressures=tuple(pressures.items()),
            events=events,
        )
        return profile, (needs_gas_numbers if profile else False)

    def waypoint_pressures(
        self,
        waypoint: ET.Element,
        where: str,
        second: int,
        cylinders_of_mix: dict[str, list[int]],
        tank_count: int,
    ) -> Iterator[tuple[int, int]]:
        """Each `<tankpressure>` on one waypoint, as `(cylinder index, tenths of a bar)`.

        Two cylinders on one blend link the same `<mix>`, so a reference resolves to a
        *list* of cylinders and repeated references on one waypoint take them in order —
        the sidemount pair the format's own §6.3 describes, whose two channels would
        otherwise collapse onto one cylinder.

        `@ref` is optional, the UDDF documentation noting that a linked double measured at
        one pressure may omit it, so a reference-less reading is taken as the dive's
        cylinder when there is exactly one and dropped when there is a choice to get wrong.
        """
        seen: dict[str, int] = {}
        for element in _kids(waypoint, "tankpressure"):
            pascal = _decimal(_text(element))
            if pascal is None:
                continue
            ref = _attr(element, "ref")
            if ref is None:
                if tank_count != 1:
                    self.note(
                        where,
                        f"a tank pressure at {second} s names no cylinder, and the dive has {tank_count}; the "
                        "reading is dropped rather than guessed onto one",
                        "dropped",
                    )
                    continue
                index = 0
            else:
                candidates = cylinders_of_mix.get(ref, [])
                position = seen.get(ref, 0)
                seen[ref] = position + 1
                if position >= len(candidates):
                    self.note(
                        where,
                        f"a tank pressure at {second} s names the gas {ref!r}, which no further cylinder on this "
                        "dive links to; the reading is dropped",
                        "dropped",
                    )
                    continue
                index = candidates[position]
            yield index, _rounded(pascal / PASCAL_PER_BAR * TENTHS_PER_UNIT)
