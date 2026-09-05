"""Reading UDDF into DiveJSON.

UDDF is the nominal incumbent, and the format most of a diver's history is trapped in.
This module reads one and produces a DiveJSON document plus a **report** of what the
source did not carry — which is half the output, not a diagnostic afterthought: a
converter that silently fills gaps produces a conforming document that lies, and §5.4 is
the rule it would be breaking.

`docs/uddf-mapping.md` is the prose companion: every element this module reads, every one
it deliberately does not, and the reasoning behind each heuristic. It is written for a
port in another language as much as for a reader of this file, so the *rules* live there
and only their implementation lives here.

Five decisions shape everything below.

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

**A `<!DOCTYPE>` is refused outright.** UDDF has no legitimate use for one and spec §9
requires readers not to dereference anything found in a document. `ElementTree` blocks
external entities, but caps entity *amplification* only in recent libexpat — a several
hundredfold blowup still parses on older ones, and that is a library-version property
rather than a guarantee this package's `>=3.10` floor can make. Refusing the declaration
is the guarantee, and it costs nothing real.

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
report when they do.
"""

from __future__ import annotations

import re
import sys
import uuid as uuid_pkg
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from . import SPEC_VERSION, __version__
from .validate import Issue, validate_document

# The producer key this converter writes its own provenance under (spec §5.5). The
# specification's own tools are the established product name here, the way `opendiving`
# is the reference implementation's.
PRODUCER_KEY = "divejson"

# uuid5(NAMESPACE_URL, "https://divejson.org/ns/uddf"). Fixed forever: changing it would
# renumber every document any released version of this converter has ever produced.
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


class UddfError(Exception):
    """The input could not be read as UDDF."""


class DoctypeRefusedError(UddfError):
    """The document carries a `<!DOCTYPE>` declaration, which this reader refuses."""


class MalformedUddfError(UddfError):
    """The input is not well-formed XML, or its root element is not `<uddf>`."""


class NonConformingOutputError(UddfError):
    """The converter produced a document `divejson validate` rejects.

    Always a bug in this module rather than a property of the source: every way a source
    can be wrong is supposed to resolve to an omission and a note. It carries the issues,
    so the failure names itself.
    """

    def __init__(self, issues: list[Issue]) -> None:
        self.issues = issues
        super().__init__("; ".join(str(issue) for issue in issues))


@dataclass(frozen=True, slots=True)
class Note:
    """One thing the source did not carry, or that this converter had to interpret.

    `where` is a path into the **source** document — `dive/0`, `dive/0/tankdata/1`,
    `site/3`, or `$` for the file itself — because that is where a diver looking for the
    missing value has to go. Indices are zero-based and count elements of that kind in
    document order.
    """

    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.where}: {self.message}"


@dataclass(frozen=True, slots=True)
class Conversion:
    """A converted document and everything the conversion could not carry."""

    document: dict[str, Any]
    notes: tuple[Note, ...]

    def grouped(self) -> list[tuple[str, list[str]]]:
        """Notes as `(message, wheres)`, in first-seen order.

        One source habit produces one note per record — eight dives with no UTC offset are
        eight notes — and a thousand-dive logbook would bury the interesting ones under
        them. Grouping is a presentation concern, so it lives here rather than in the data.
        """
        grouped: dict[str, list[str]] = {}
        for note in self.notes:
            grouped.setdefault(note.message, []).append(note.where)
        return list(grouped.items())


def convert_uddf(data: bytes, *, exported_at: datetime | None = None) -> Conversion:
    """Convert one UDDF document into DiveJSON.

    `data` is **bytes**, not text: an XML document declares its own encoding, and a UDDF
    file that says `encoding="ISO-8859-1"` has to be decoded by the parser that read that
    declaration. Handing `ElementTree` a `str` carrying one is a `ValueError` anyway.

    `exported_at` defaults to now in the local zone. It is one of the two members the
    document asserts about its own run rather than about the source (spec §4) — `generator`
    is the other — so a caller producing documents in a fixed context, a test or a batch
    import, should pass its own.

    Raises `DoctypeRefusedError`, `MalformedUddfError` or `NonConformingOutputError`.
    """
    root = _parse(data)
    return _Converter(root, exported_at=exported_at or datetime.now().astimezone()).run()


def convert_uddf_file(path: Path, *, exported_at: datetime | None = None) -> Conversion:
    """`convert_uddf` on a file's bytes. `OSError` propagates."""
    return convert_uddf(path.read_bytes(), exported_at=exported_at)


class _DoctypeRefusingTarget(ET.TreeBuilder):
    """A parse target that stops the parse the moment a DTD is declared.

    Raising from `doctype` aborts before expat has expanded a single entity reference in
    the content, which is what makes this a bound on amplification rather than a check
    performed after the damage. The hook is on the *target* rather than on the parser:
    `XMLParser.parser`, which the equivalent expat handler would need, no longer exists on
    Python 3.14, while this one behaves identically from 3.10 through 3.14.
    """

    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        raise DoctypeRefusedError(f"the document declares <!DOCTYPE {name}>, which this reader refuses (spec §9)")


def _parse(data: bytes) -> ET.Element:
    parser = ET.XMLParser(target=_DoctypeRefusingTarget())
    try:
        parser.feed(data)
        root = parser.close()
    except DoctypeRefusedError:
        raise
    except ET.ParseError as error:
        raise MalformedUddfError(f"not well-formed XML — {error}") from error
    if root is None or _name(root) != "uddf":
        found = "nothing" if root is None else f"<{_name(root)}>"
        raise MalformedUddfError(f"the root element is {found}, not <uddf>")
    return root


def _name(element: ET.Element) -> str:
    """An element's local name, lowercased.

    Both halves earn their place: the namespace is one of four, or absent, and UDDF 2.x
    spelled its elements in upper case.
    """
    tag = element.tag
    if not isinstance(tag, str):  # a comment or a processing instruction
        return ""
    _, _, local = tag.rpartition("}")
    return local.lower()


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
    return [child for child in element if _name(child) == name]


def _kid(element: ET.Element | None, name: str) -> ET.Element | None:
    if element is None:
        return None
    for child in element:
        if _name(child) == name:
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
    def __init__(self, root: ET.Element, *, exported_at: datetime) -> None:
        self.root = root
        self.exported_at = exported_at
        self.notes: list[Note] = []
        self.claimed: dict[str, str] = {}
        self.source_ids: set[str] = set()
        self.site_uuids: dict[str, str] = {}
        self.trip_uuids: dict[str, str] = {}
        self.gear_uuids: dict[str, str] = {}
        self.mixes: dict[str, dict[str, Any]] = {}

    # -- reporting ---------------------------------------------------------------

    def note(self, where: str, message: str) -> None:
        self.notes.append(Note(where, message))

    # -- identity ----------------------------------------------------------------

    def uuid_for(self, kind: str, source_id: str | None, where: str, index: int) -> str | None:
        """A stable UUID for one source record, or `None` when it collides irreparably."""
        if source_id is None:
            self.note(
                where,
                f"the source gives this {kind} no id, so its identity is derived from its position in the "
                "file and will move if the file's order changes (spec §5.3)",
            )
            source_id = f"#{index}"

        derived = str(uuid_pkg.uuid5(UDDF_ID_NAMESPACE, f"{kind}:{source_id}"))
        for candidate in dict.fromkeys((_embedded_uuid(source_id) or derived, derived)):
            if candidate not in self.claimed:
                self.claimed[candidate] = where
                return candidate

        self.note(
            where,
            f"a second {kind} carries the id {source_id!r}, already used by {self.claimed[derived]}; the "
            "record is dropped, because two records cannot share one identity (spec §5.3)",
        )
        return None

    # -- text --------------------------------------------------------------------

    def capped(self, value: str, limit: int, where: str, member: str) -> str:
        if len(value) <= limit:
            return value
        self.note(where, f"{member} is {len(value)} characters; the format caps it at {limit} and the rest is dropped")
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
        self.note(where, f"the recorded email {value!r} is not an address; read as no email recorded")
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
                self.note(where, "only one half of a coordinate pair was recorded, and a position needs both; dropped (spec §6)")
            return None
        if latitude == 0 and longitude == 0:
            self.note(
                where,
                "the coordinates are exactly 0.000000 / 0.000000, which writers emit to mean 'unknown'; read as "
                "no position rather than as Null Island",
            )
            return None
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            self.note(where, f"the coordinates {latitude} / {longitude} are outside the WGS 84 range; dropped")
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

        document: dict[str, Any] = {
            "format": "divejson",
            "version": SPEC_VERSION,
            "exported_at": self.exported_at.isoformat(timespec="seconds"),
            "generator": {"name": "divejson convert", "version": __version__},
        }
        if diver:
            document["diver"] = diver
        for member, rows in (("dives", dives), ("trips", trips), ("sites", sites), ("gear", gear)):
            if rows:
                document[member] = rows
        document["extensions"] = {PRODUCER_KEY: self.provenance()}

        issues = validate_document(document)
        if issues:
            raise NonConformingOutputError(issues)
        return Conversion(document, tuple(self.notes))

    def provenance(self) -> dict[str, Any]:
        """What the source file said about itself.

        Under a producer key rather than in `generator`, which §4 defines as what produced
        *this* document — and that is the converter. The source's own identity is worth
        keeping and has nowhere in the core vocabulary to go.
        """
        provenance: dict[str, Any] = {"converted_from": "uddf"}
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
        return provenance

    # -- diver -------------------------------------------------------------------

    def read_diver(self) -> dict[str, Any] | None:
        """The logbook's owner, or nothing at all.

        §6.1 is explicit that a converter whose source records nothing about an owner omits
        the member entirely — minting identity for one would be §5.4's fabrication applied
        to people. `<owner id>` is deliberately not read as a name or a handle: it is an
        XML id, and Subsurface's is the literal string "owner".
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
            self.note(where, "the source records nothing about the logbook's owner; no diver is written (spec §6.1)")
            return None

        diver: dict[str, Any] = {}
        claimed = self.uuid_for("diver", _attr(owner, "id"), where, 0)
        if claimed is not None:
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
                )
                continue
            claimed = self.uuid_for("site", _attr(element, "id"), where, index)
            if claimed is None:
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

            source_id = _attr(element, "id")
            if source_id:
                self.site_uuids[source_id] = claimed
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
                self.note(where, "the trip has no name, which the format requires of one; it is dropped (spec §6.8)")
                continue

            starts, ends, locations, notes = self.read_trip_parts(element, where)
            if starts is None:
                self.note(
                    where,
                    "the trip records no dates, and the format requires a start date; it is dropped along with "
                    "the dives' membership of it (spec §6.8)",
                )
                continue
            claimed = self.uuid_for("trip", _attr(element, "id"), where, index)
            if claimed is None:
                continue

            trip: dict[str, Any] = {"uuid": claimed, "name": self.capped(name, MAX_NAME, where, "the trip name")}
            if locations:
                trip["locations"] = locations
            trip["starts_on"] = starts
            if ends is not None and ends >= starts:
                trip["ends_on"] = ends
            elif ends is not None:
                self.note(where, f"the trip ends on {ends}, before it starts on {starts}; the end date is dropped")
            if notes:
                trip["notes"] = notes

            source_id = _attr(element, "id")
            if source_id:
                self.trip_uuids[source_id] = claimed
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
                    self.note(part_where, f"{attribute} is {raw!r}, which is not a date; dropped")
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
        pieces = [child for child in equipment if _name(child) in _GEAR_TYPE] if equipment is not None else []
        gear: list[dict[str, Any]] = []
        for index, element in enumerate(pieces):
            kind = _name(element)
            where = f"gear/{index}"
            name = _text_of(element, "name")
            if not name:
                self.note(
                    where,
                    f"the <{kind}> has no name, which the format requires of a gear item; it is dropped "
                    "(spec §6.12)",
                )
                continue
            claimed = self.uuid_for("gear", _attr(element, "id"), where, index)
            if claimed is None:
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

            source_id = _attr(element, "id")
            if source_id:
                self.gear_uuids[source_id] = claimed
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
                    )
                if not 0 <= percent <= 100:
                    self.note(where, f"<{tag}> reads as {percent} percent, outside the 0 to 100 a fraction can be; dropped")
                    continue
                mix[member] = float(percent)
            if mix.get("oxygen", 0.0) + mix.get("helium", 0.0) > 100:
                self.note(where, "oxygen and helium sum above 100 percent, which no mix can; both are dropped (spec §6.3)")
                mix.pop("oxygen", None)
                mix.pop("helium", None)

            po2_limit = _decimal(_text_of(element, "maximumpo2"))
            if po2_limit is not None:
                if MIN_PO2_LIMIT <= po2_limit <= MAX_PO2_LIMIT:
                    mix["po2_limit"] = float(po2_limit)
                else:
                    self.note(where, f"<maximumpo2> is {po2_limit} bar, outside the 0.4 to 2.0 the format allows; dropped")
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
        claimed = self.uuid_for("dive", _attr(element, "id"), where, index)
        if claimed is None:
            return None

        dive: dict[str, Any] = {"uuid": claimed}
        number = _integer(_decimal(_text_of(before, "divenumber")))
        if number is not None:
            dive["dive_number"] = number
        dive["started_at"] = started_at

        duration = _integer(_decimal(_text_of(after, "diveduration")))
        if duration is not None:
            if duration > 0:
                dive["duration"] = duration
            else:
                self.note(where, f"<diveduration> is {duration} seconds; the format records a duration only when it is positive")

        notes = self.notes_text(after, where)
        if notes:
            dive["notes"] = notes

        max_depth = self.positive(_decimal(_text_of(after, "greatestdepth")), where, "<greatestdepth>")
        avg_depth = self.positive(_decimal(_text_of(after, "averagedepth")), where, "<averagedepth>")
        if max_depth is not None and avg_depth is not None and avg_depth > max_depth:
            self.note(
                where,
                f"the mean depth {avg_depth} m is deeper than the greatest depth {max_depth} m, which cannot be; "
                "the mean is dropped rather than either being adjusted to fit (spec §6.2)",
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
            if visibility >= 0:
                dive["visibility"] = float(visibility)
            else:
                self.note(where, f"<visibility> is {visibility} m; dropped")

        used = _kid(before, "equipmentused")
        weight = _decimal(_text_of(used, "leadquantity"))
        if weight is not None:
            if weight >= 0:
                # Kept when it is zero: a recorded "no lead" is a fact about the dive, and
                # absence is how "we do not know" is spelled here (spec §6.2).
                dive["weight"] = float(weight)
            else:
                self.note(where, f"<leadquantity> is {weight} kg; dropped")

        altitude = _integer(_decimal(_text_of(before, "altitude")))
        if altitude is not None:
            if MIN_ALTITUDE <= altitude <= MAX_ALTITUDE:
                dive["altitude"] = altitude
            else:
                self.note(where, f"<altitude> is {altitude} m, outside the -450 to 6500 the format allows; dropped")

        surface_pressure = _decimal(_text_of(before, "surfacepressure"))
        if surface_pressure is not None:
            bar = surface_pressure / PASCAL_PER_BAR
            if MIN_SURFACE_PRESSURE <= bar <= MAX_SURFACE_PRESSURE:
                dive["surface_pressure"] = float(bar)
            else:
                self.note(where, f"<surfacepressure> reads as {bar} bar, outside the 0.4 to 1.2 the format allows; dropped")

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
            )
            return None
        started_at, forgiven = _date_time(raw)
        if started_at is None:
            self.note(where, f"<datetime> is {raw!r}, which is not a date and time; the dive is dropped (spec §6.2)")
            return None
        if forgiven:
            self.note(where, forgiven)
        if not _has_offset(started_at):
            self.note(where, "the source recorded no UTC offset on the dive's start time; the wall clock travels alone (spec §5.2)")
        return started_at

    def positive(self, value: Decimal | None, where: str, member: str) -> Decimal | None:
        """A measurement the format records only when it is above zero.

        Zero is what this format's own UDDF writer emits for a depth it never had —
        `<greatestdepth>` is mandatory in UDDF and optional here — so reading it back as a
        measurement would turn "not recorded" into "the surface".
        """
        if value is None or value > 0:
            return value
        self.note(where, f"{member} is {value}, which the format records only when positive; read as not recorded")
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
                self.note(where, f"a link points at {ref!r}, which nothing in the file defines; the reference is dropped")
            elif ref not in self.mixes:
                # A `<link>` under `informationbeforedive` addresses a site here, but the
                # schema lets it address a buddy or a shop too, and one under
                # `<equipmentused>` addresses a piece of kit. A reference to a record this
                # converter carries nowhere is worth a note; a gas reference is not.
                self.note(where, f"a link points at {ref!r}, which is not a {kind} this converter carries; the reference is dropped")
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
                )
            else:
                litres, reinterpreted = _volume_litres(raw_volume)
                if reinterpreted:
                    self.note(
                        tank_where,
                        f"<tankvolume> is {raw_volume}, too large to be the cubic metres UDDF specifies; read as "
                        f"{litres} litres, which is how some builds of Subsurface write it",
                    )
                if litres > 0:
                    cylinder["volume"] = float(litres)
                else:
                    self.note(tank_where, f"<tankvolume> reads as {litres} litres; the format records a size only when positive")

            start = self.pressure_bar(_text_of(tank, "tankpressurebegin"), tank_where, "<tankpressurebegin>")
            end = self.pressure_bar(_text_of(tank, "tankpressureend"), tank_where, "<tankpressureend>")
            if start is not None and start == 0:
                # §6.3 is explicit: a recorded zero start pressure is a device's
                # absent-marker rather than a measurement, and writers must not emit it.
                self.note(
                    tank_where,
                    "the start pressure is 0 bar, which devices write to mean 'not recorded'; read as not "
                    "recorded (spec §6.3)",
                )
                start = None
            if start is not None and end is not None and end > start:
                self.note(
                    tank_where,
                    f"the end pressure {end} bar is above the start pressure {start} bar, which cannot be; the "
                    "end pressure is dropped",
                )
                end = None
            if start is not None:
                cylinder["start_pressure"] = float(start)
            if end is not None:
                cylinder["end_pressure"] = float(end)

            mix_ref = _attr(_kid(tank, "link"), "ref")
            if mix_ref is None:
                self.note(tank_where, "the source records no gas for this cylinder; absent means not recorded, never air (spec §6.3)")
            elif mix_ref in self.mixes:
                cylinder.update(self.mixes[mix_ref])
            else:
                self.note(
                    tank_where,
                    f"the cylinder links to the gas {mix_ref!r}, which <gasdefinitions> does not define; its mix "
                    "is not recorded",
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
            self.note(where, f"{member} reads as {bar} bar, outside the 0 to 350 the format allows; dropped")
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
        """
        samples = _kid(element, "samples")
        if samples is None:
            return None, False

        timed: list[tuple[int, ET.Element]] = []
        for index, waypoint in enumerate(_kids(samples, "waypoint")):
            second = _integer(_decimal(_text_of(waypoint, "divetime")))
            if second is None:
                self.note(
                    f"{where}/waypoint/{index}",
                    "the waypoint records no <divetime>, so it has no place on the profile's time axis; dropped",
                )
            elif second < 0:
                self.note(f"{where}/waypoint/{index}", f"the waypoint is at {second} s, before the dive began; dropped")
            else:
                timed.append((second, waypoint))

        timed.sort(key=lambda pair: pair[0])
        ordered: list[tuple[int, ET.Element]] = []
        for second, waypoint in timed:
            if ordered and ordered[-1][0] == second:
                self.note(
                    where,
                    f"two waypoints share the second {second}; the later one is dropped, because the format's "
                    "sample times are strictly increasing (spec §6.5)",
                )
                continue
            ordered.append((second, waypoint))
        if not ordered:
            return None, False

        cylinders_of_mix: dict[str, list[int]] = {}
        for index, ref in enumerate(mix_refs):
            if ref is not None:
                cylinders_of_mix.setdefault(ref, []).append(index)

        depth: dict[str, list[int]] = {"times": [], "values": []}
        temperature: dict[str, list[int]] = {"times": [], "values": []}
        pressures: dict[int, dict[str, list[int]]] = {}
        events: list[dict[str, Any]] = []
        needs_gas_numbers = False

        for second, waypoint in ordered:
            metres = _decimal(_text_of(waypoint, "depth"))
            if metres is not None:
                depth["times"].append(second)
                depth["values"].append(_rounded(metres * CENTIMETRES_PER_METRE))

            kelvin = _decimal(_text_of(waypoint, "temperature"))
            if kelvin is not None:
                temperature["times"].append(second)
                temperature["values"].append(_rounded((kelvin - KELVIN_OFFSET) * TENTHS_PER_UNIT))

            for cylinder_index, tenths in self.waypoint_pressures(waypoint, where, second, cylinders_of_mix, len(mix_refs)):
                channel = pressures.setdefault(cylinder_index, {"times": [], "values": []})
                if channel["times"] and channel["times"][-1] == second:
                    self.note(where, f"two tank pressures at {second} s resolve to the same cylinder; the later one is dropped")
                    continue
                needs_gas_numbers = True
                channel["times"].append(second)
                channel["values"].append(tenths)

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
                    )
                events.append(event)

        if not (depth["times"] or temperature["times"] or pressures or events):
            # Waypoints whose every reading was unusable are not a profile. Emitting the
            # bare `duration: 0` the members below would leave behind asserts a sampled
            # record of zero length, which is a thing the source did not say.
            #
            # Reported, unlike a dive that simply has no `<samples>`: the source *did*
            # record a profile here, and this is the converter unable to carry it. That is
            # the same class as a dropped waypoint or a dropped coordinate pair, and every
            # one of those says so.
            subject = "waypoint carries" if len(ordered) == 1 else "waypoints carry"
            self.note(
                where,
                f"the dive's {len(ordered)} {subject} a time but no reading this format can hold, so it "
                "arrives with no profile at all rather than one of zero length",
            )
            return None, False

        latest = max(
            (channel["times"][-1] for channel in (depth, temperature, *pressures.values()) if channel["times"]),
            default=0,
        )
        profile: dict[str, Any] = {"duration": latest}
        if depth["times"]:
            profile["depth"] = depth
        if temperature["times"]:
            profile["temperature"] = temperature
        if pressures:
            profile["pressures"] = [
                {"times": channel["times"], "values": channel["values"], "gas_number": cylinder_index}
                for cylinder_index, channel in sorted(pressures.items())
            ]
        if events:
            events.sort(key=lambda event: event["time"])
            profile["events"] = events
        return profile, needs_gas_numbers

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
                    )
                    continue
                index = candidates[position]
            yield index, _rounded(pascal / PASCAL_PER_BAR * TENTHS_PER_UNIT)
