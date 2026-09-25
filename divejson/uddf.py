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
of UDDF — the note kinds, identity, the way a zero reads, the number bound, the coordinate
pair, the sample axis, the `<!DOCTYPE>` refusal — lives in `converter.py`, `series.py` and
`xmlsource.py`, and this module inherits it. What is below is UDDF's alone.

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
and named in the report. The *unit* ambiguities are the deliberate exceptions and are not
the same case: `<tankvolume>`'s cubic-metres-or-litres, `<o2>`'s fraction-or-percent and
`<calculatedpo2>`'s bar-or-Pascal are values that **were** recorded, whose scale alone is in
doubt, so a magnitude test there interprets data rather than inventing it. The gradient
factors are the ambiguity a magnitude test cannot settle, and `PERCENT_GRADIENT_FACTORS`
settles them on the generator instead. Every one of them fires loudly into the report, as a
note of kind `resolved` — the kind that exists for exactly this, a number the source
supplied and the converter only had to read at the scale it must have meant. They are
deliberately not `inferred`, which is reserved for a value computed from other readings and
carries the obligation to list its member under `extensions.divejson.inferred`; a resolution
lists nothing, because there is no derivation for a reader to be told about. So this reader
infers nothing and that list is absent from every document it produces, while its report
still says out loud where it chose a scale. One `resolved` finding settles a meaning rather
than a scale: a `<surfacepressure>` on a dive linking more than one computer, which the
file states once for the dive and this reader gives to the first (`read_recordings`).

That paragraph carried a count until this reader gained a third scale. It does not carry one
now, for the reason `docs/uddf-mapping.md`'s own heading gives: one more ambiguity is exactly
the sort of thing that arrives without the number in front of it being corrected.
"""

from __future__ import annotations

import re
import uuid as uuid_pkg
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .converter import (
    CENTIMETRES_PER_METRE,
    MAX_NAME,
    PRODUCER_KEY,
    TENTHS_PER_UNIT,
    Conversion,
    ConverterError,
    Identities,
    NonConformingOutputError,
    Note,
    NoteKind,
    Scope,
    capped,
    center_members,
    deco_model,
    decimal_of,
    device,
    header,
    integer_of,
    milliseconds,
    position,
    record_inferred,
    recorded,
    recording,
    roles_in_order,
    rounded,
    in_seconds,
    shared_readout,
)
from .series import Channel, SampleAxis
from .validate import validate_document

# Aliased on import rather than renamed at every call site. These four moved into
# `xmlsource.py` when a second XML format arrived — taking a child by lowercased local
# name and stripping an attribute are every XML reader's, not UDDF's — and the alias keeps
# a hundred-odd mechanical renames out of a diff that is about the other format.
from .xmlsource import attribute as _attr
from .xmlsource import child as _kid
from .xmlsource import children as _kids
from .xmlsource import local_name, parse_xml, root_name
from .xmlsource import text as _text

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

# At or above which a `<tankvolume>` is read as litres rather than the cubic metres UDDF
# specifies — see `_volume_litres`.
LITRES_THRESHOLD = Decimal(1)

# `MAX_NAME` is `converter.py`'s: every adapter meets it, and §6.9's `name` is one of the
# names it caps — a place's name shares the 255 every name in the format has, and so does
# an insurer's. The caps below are UDDF's own, being the only reader that
# fills the members they cap. §6.12's `serial` is 1-64 rather than the 255 its neighbours
# share, and the reason is worth knowing: a gear serial longer than a device's (§6.4b) could
# never equal one, and equality between the two is what says a kit item and a device are
# one machine. A phone, an email and a website — §6.1's and §6.18's — are never cut to theirs,
# only dropped past them: a number or an address with its end missing is a wrong one rather
# than a short one. An address's parts are cut like a name, the postcode to a code's length.
MAX_FULL_NAME = 512
MAX_SERIAL = 64
MAX_PHONE = 32
MAX_EMAIL = 255
MAX_WEBSITE = 512
MAX_POSTCODE = 32
MIN_PO2_LIMIT = Decimal("0.4")
MAX_PO2_LIMIT = Decimal("2.0")
MIN_SURFACE_PRESSURE = Decimal("0.4")
MAX_SURFACE_PRESSURE = Decimal("1.2")
MAX_CYLINDER_PRESSURE = Decimal(350)
MIN_ALTITUDE = -450
MAX_ALTITUDE = 6500

# Deliberately looser than any address grammar, and it is not trying to be one. `email` is
# the only member in this format whose *type* constrains the text a source can put in it,
# so a value here has to clear that bar or be omitted like anything else the source did not
# record. This admits every real address and rejects what writers actually leave in the
# field — `n/a`, `-`, a person's name, a sentence.
_EMAIL = re.compile(r"\A[^@\s]+@[^@\s]+\Z")

# An absolute URI: a scheme, a colon, and something after it. `xs:anyURI` admits a bare
# `www.example.com`, which §6.18's `website` does not mean and an application cannot open,
# and prepending a scheme would be stating one the file never did.
_ABSOLUTE_URI = re.compile(r"\A[A-Za-z][A-Za-z0-9+.\-]*:\S+\Z")

# `addressType`'s children onto §6.19's members, in §6.19's order, with each one's cap.
_ADDRESS = (
    ("street", "street", MAX_NAME),
    ("city", "city", MAX_NAME),
    ("postcode", "postcode", MAX_POSTCODE),
    ("province", "region", MAX_NAME),
    ("country", "country", MAX_NAME),
)

# The children of a center's shape that §6.18 reads; every other one is reported by name, so
# that what UDDF records about a base, a shop or a hotel and this format does not — a price,
# a rating, a guide, a hotel's category — is never dropped in silence.
_CENTER_READ = {"name", "address", "contact", "notes"}
_CONTACT_READ = {"phone", "mobilephone", "email", "homepage"}

# Where a part's diver slept, and the role each shape gives its center. The accommodation is
# read under both spellings — the XSD declares `<accomodation>`, the documentation writes
# `<accommodation>` — and an `<operator>` is the liveaboard, `trippartType` pairing it with a
# `<vessel>` in place of the accommodation.
_STAYS = {"accomodation": "accommodation", "accommodation": "accommodation", "operator": "liveaboard"}

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
#
# Public because the writer derives its own fidelity claim from it: a gear type survives a
# round trip through UDDF exactly when reading the element it was written as returns that
# type, and a second table stating which those are would be a second thing to get wrong.
GEAR_TYPE: dict[str, str] = {
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
# "shorty", "half-suit", "two-piece" and the rest — leaves `type` at `GEAR_TYPE`'s
# `wetsuit`, which is what every one of them is.
_DRYSUIT_TYPES = {"dry-suit", "drysuit", "hot-water-suit"}

# A `<setmarker>` whose text is exactly one of these is that event, rather than an event
# with no type at all labelled with the word. It is what makes this format's own markers
# survive a round trip through UDDF, whose `<setmarker>` is a bare string with no type
# beside it.
_MARKER_TYPES = {"deep_stop", "safety_stop", "bookmark"}

# `<divemode @type>` onto §6.4a's `mode`. `divemodeType` enumerates five values and
# DiveJSON five, and they are not the same five: **UDDF spells a freedive twice** — `apnoe`
# is the original and `apnea` was added beside it in 2017 as the English word, both current
# in 3.2.x — and it has no value for a computer run as a **gauge**. A reader that knew one
# freedive spelling would drop every freedive from whichever half of the installed base
# wrote the other, which is why both are here.
_DIVE_MODES = {
    "opencircuit": "open_circuit",
    "closedcircuit": "closed_circuit",
    "semiclosedcircuit": "semi_closed",
    "apnoe": "freedive",
    "apnea": "freedive",
}

# `<calculatedpo2>` at or below this is bar; above it is the Pascal the documentation
# states. Three orders of magnitude separate the two spellings and a breathable ppO₂ lives
# between about 0.1 and 2 bar, so nothing overlaps — Shearwater Cloud Desktop writes
# `0.399999976` for 0.4 bar where the documentation asks for 40 000.
PO2_BAR_THRESHOLD = Decimal(10)

# The scales §6.4 fixes for the two channels only this reader lands on, as factors off the
# units UDDF states. ppO₂ is hundredths of a bar, so a reading already in bar multiplies by
# 100 and one in Pascal divides by 1 000 — a hundredth of a bar being a kilopascal. A
# gradient factor is whole percent, which is what the documented fraction multiplies by.
HUNDREDTHS_PER_UNIT = Decimal(100)
PASCAL_PER_PO2_HUNDREDTH = Decimal(1000)
PERCENT_PER_FRACTION = Decimal(100)

# The generators this reader knows, and what each one is read differently for. An entry
# here changes how one application's files are read and no others', which makes it the
# sharpest tool in this module: `converting.md` settles a *scale* on the value with a
# magnitude test and a *meaning* on the writer with a table like this one, and the
# difference is what a wrong answer costs — a magnitude test misreads one value, a row
# here misreads every file that generator ever produced. So a row is added only against
# real files, and `docs/uddf-mapping.md` says which.
#
# Matched on the exact `<generator><name>`, with the `<manufacturer>` id checked beside
# it: a name alone is a string anything may claim, and two agreeing beats one.
#
# **The Shearwater `Z` is the local wall clock, not UTC.** Shearwater Cloud Desktop writes
# the time the diver read off their wrist and suffixes it `Z`, so the instant the file
# appears to state is wrong by the diver's own offset — three hours, for the Red Sea export
# this rule was written against, where a Perdix 3 stamped `15:18:10Z` for the same moment a
# Suunto on the same wrist stamped `15:17:38+03:00`. Trusting it puts the dive three hours
# from where it happened and can never pair it with the same dive off another computer;
# correcting it with an offset would be the fabrication §5.2 forbids outright.
LOCAL_CLOCK_WITH_Z: dict[str, str] = {
    "Shearwater Cloud Desktop": "Shearwater_Research_Inc",
}

# **The gradient factors are whole percent, not the documented fraction.** The same table
# and the same two-keys rule, for a second thing that generator writes differently.
# `<gradientfactorlow>` and `<gradientfactorhigh>` are documented as fractions and the
# per-waypoint `<gradientfactor>` as "a percentage as a real number" with no range at all,
# its one example `0.8` glossed as 80 %; Shearwater Cloud Desktop writes `50` and `85` for
# the pair and `0`, `1` and `3` to `17` per waypoint.
#
# **A magnitude test cannot settle this one**, which is what makes it the table's case
# rather than a heuristic's. Nearly every per-waypoint value in hand is `0` or `1`, and the
# `<o2>` shape would read that `1` as 100 % — a leading tissue at its M-value on a 15 m
# no-decompression dive, which is not what the file says. The pair follows the same row: a
# file that writes one of the three in percent writes all three that way.
PERCENT_GRADIENT_FACTORS: dict[str, str] = {
    "Shearwater Cloud Desktop": "Shearwater_Research_Inc",
}

# What the report says when *that* rule fires, once per file rather than once per reading:
# the scale is a property of the generator, so a line per waypoint would be one fact about
# the file written out a hundred and twenty-eight times.
PERCENT_GRADIENT_NOTE = (
    "the generator writes gradient factors in whole percent where the documentation's "
    "examples show a fraction; read as the percent §6.4 records (spec §6.4c)"
)

# What the report says when that rule fires. `resolved` rather than `inferred` because the
# digits written are the ones the source recorded and only their meaning was in doubt, so
# nothing goes under `extensions.divejson.inferred`.
LOCAL_CLOCK_NOTE = (
    "the generator writes the local wall clock with a `Z` suffix; read as a wall clock "
    "with no offset (spec §5.2)"
)


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


def _dig(element: ET.Element | None, *names: str) -> ET.Element | None:
    for name in names:
        element = _kid(element, name)
    return element


def _text_of(parent: ET.Element | None, *names: str) -> str | None:
    return _text(_dig(parent, *names))


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

    Returns `(value, note)`; `value` is `None` when the text is not a date at all. **A text
    carrying no time of day comes back as the bare date**, `2002-06-18`, and a caller that
    wants a date-time checks for that: a dive's start may be a date alone (§5.2), and every
    other caller takes the date off the front anyway.

    **The offset is preserved exactly as recorded, and never supplied.** That is §5.2's
    whole point, and converting to UTC — or assuming an offset where the source recorded
    none — is the failure every tested UDDF consumer produced.

    The forgiven shapes are real writer output rather than hypotheticals: Subsurface emits
    a midnight dive as `<datetime>2002-06-18T</datetime>`, its XSLT building the string
    with an unguarded `concat`, and a bare date is what the same bug produces one character
    earlier — and what UDDF's own documentation calls a legal omission of the lower-order
    elements.
    """
    match = _DATE_TIME.match(text.strip())
    if match is None:
        return None, None

    parts = match.groupdict()
    if parts["hour"] is None:
        try:
            date.fromisoformat(parts["date"])
        except ValueError:
            return None, None
        return parts["date"], f"{text.strip()!r} records a date with no time of day"

    note = None
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


def _carries(element: ET.Element | None) -> bool:
    """Whether an element holds any text at all, anywhere beneath it."""
    return element is not None and any(text.strip() for text in element.itertext())


def _has_offset(value: str) -> bool:
    _, _, time_part = value.partition("T")
    return time_part.endswith(("Z", "z")) or "+" in time_part or "-" in time_part


class _Converter:
    def __init__(self, root: ET.Element, *, exported_at: datetime, scope: Scope) -> None:
        self.root = root
        self.exported_at = exported_at
        self.scope = scope
        self.notes: list[Note] = []
        # The claimed-UUID table inside is shared with the rest of the archive this file
        # came from, if it came from one, so that a record two members both define is
        # written once and referred to by both — while the *other* case, two records in one
        # file naming one id, is still refused.
        self.identities = Identities(UDDF_ID_NAMESPACE, scope, self.note)
        self.inferred: list[str] = []
        self.source_ids: set[str] = set()
        self.site_uuids: dict[str, str] = {}
        self.trip_uuids: dict[str, str] = {}
        self.gear_uuids: dict[str, str] = {}
        # `<divecomputer>` elements by their own `@id`, filled by `read_gear` and read by
        # `read_recordings`. Keyed on the element rather than on the gear uuid because the
        # two do not always both exist: §6.12 makes a gear item's `name` REQUIRED, so a
        # nameless `<divecomputer>` mints no gear item and never reaches `gear_uuids` —
        # while §6.4b is happy with a `<model>` alone, so that same element may still be a
        # device. Resolving a dive's link through the gear table would lose exactly those.
        self.computers: dict[str, ET.Element] = {}
        self.mixes: dict[str, dict[str, Any]] = {}
        # `<decomodel>`'s children by their own `@id`, filled by `read_deco_models` and
        # reached by a dive's `<link ref>` under `<informationbeforedive>`. Every child is
        # listed and only `<buehlmann>` carries a §6.4c object, so a `<vpm>` or an `<rgbm>`
        # a dive links resolves — it is not a dangling reference — and is reported as a
        # model this reader cannot name.
        self.deco_models: dict[str, ET.Element] = {}
        # A `<divebase>`'s or a `<shop>`'s `@id` to its center, for a dive's link to one —
        # recorded whether or not this file carries the row, like the tables above. An inline
        # shape's id is not here: nothing in UDDF links one.
        self.center_uuids: dict[str, str] = {}
        # The ids of a base or a shop that was not read, so a link to one says why it went.
        self.unread_centers: set[str] = set()
        # The centers this file carries, in the order `docs/uddf-mapping.md` fixes; and every
        # center read, carried or not, by its trimmed case-folded name, which is all an
        # inline shape has to be folded by.
        self.centers: list[dict[str, Any]] = []
        self.center_names: dict[str, dict[str, Any]] = {}
        self.local_clock_with_z = self.generator_writes_a_local_z()
        self.percent_gradient_factors = self.generator_writes_percent_gradient_factors()
        self.reported_gradient_scale = False

    # -- reporting ---------------------------------------------------------------

    def note(self, where: str, message: str, kind: NoteKind) -> None:
        """One line of the report, at a path into the source this conversion read."""
        self.notes.append(Note(self.scope.where(where), message, kind))

    # -- the generator table -----------------------------------------------------

    def generator_writes_a_local_z(self) -> bool:
        """Whether `LOCAL_CLOCK_WITH_Z` claims the application that wrote this file."""
        return self.generator_is_in(LOCAL_CLOCK_WITH_Z)

    def generator_writes_percent_gradient_factors(self) -> bool:
        """Whether `PERCENT_GRADIENT_FACTORS` claims the application that wrote this file."""
        return self.generator_is_in(PERCENT_GRADIENT_FACTORS)

    def generator_is_in(self, table: dict[str, str]) -> bool:
        """Whether one generator table names this document's writer.

        Read once per file rather than per dive: `<generator>` is a property of the
        document, and asking it again for every dive would make a logbook's cost depend on
        how many dives it has for an answer that cannot change.
        """
        generator = _kid(self.root, "generator")
        name = _text_of(generator, "name")
        if name is None:
            return False
        maker = table.get(name.strip())
        return maker is not None and _attr(_kid(generator, "manufacturer"), "id") == maker

    # -- identity ----------------------------------------------------------------

    def uuid_for(self, kind: str, source_id: str | None, where: str, index: int | str) -> tuple[str | None, bool]:
        """A record's stable UUID, and whether this file is the one that carries it."""
        return self.identities.for_record(kind, source_id, where, index)

    # -- text --------------------------------------------------------------------

    def capped(self, value: str, limit: int, where: str, member: str) -> str:
        return capped(value, limit, note=self.note, where=where, member=member)

    def email(self, value: str | None, where: str) -> str | None:
        """`<contact><email>` when it is an address §6.1 can hold, and nothing when it is not.

        Every other source string reaches a member the format types as free text, where the
        only limit is a length. `email` is the exception — the schema types it as an email
        address, so a `-` or an `n/a` is a value the member cannot hold. Without this the
        whole conversion fails on it: the output would not validate, which this module
        treats as its own bug, so one unusable header field would discard an entire logbook
        instead of costing it one member and a line in the report. An address past §6.1's
        bound is the same case.
        """
        if value is None:
            return None
        if not _EMAIL.match(value):
            self.note(where, f"the recorded email {value!r} is not an address; read as no email recorded", "dropped")
            return None
        if len(value) > MAX_EMAIL:
            self.note(
                where,
                f"the recorded email is {len(value)} characters and the format allows {MAX_EMAIL}; an address "
                "cut short is a wrong one, so it is dropped",
                "dropped",
            )
            return None
        return value

    def phone(
        self, contact: ET.Element | None, where: str, *, section: str = "§6.1", whose: str = "the owner"
    ) -> str | None:
        """The first `<phone>`, else the first `<mobilephone>`, reporting every other one.

        §6.1 and §6.18 each carry one number the way they carry one address, and
        `contactType` holds any number of each. A first number past the bound is dropped
        rather than cut, for the reason `MAX_PHONE` gives, and the next is not read in its
        place.
        """
        recorded = [
            (tag, value)
            for tag in ("phone", "mobilephone")
            for value in (_text(kid) for kid in _kids(contact, tag))
            if value
        ]
        if not recorded:
            return None
        for tag, value in recorded[1:]:
            self.note(
                where,
                f"{section} carries one phone, the first {whose} records; <{tag}> {value!r} is not read",
                "dropped",
            )
        _, value = recorded[0]
        if len(value) > MAX_PHONE:
            self.note(
                where,
                f"the recorded phone is {len(value)} characters and the format allows {MAX_PHONE}; a number cut "
                "short is a wrong one, so it is dropped",
                "dropped",
            )
            return None
        return value

    def date_of(self, element: ET.Element | None, where: str, member: str) -> str | None:
        """The date an `encapsulatedDateTimeType` holds, taken off the front of its `<datetime>`.

        The slot is an `xs:dateTime` and §6.1's members are dates, so a time of day is one a
        writer supplied only because the slot demands one and is discarded without a finding;
        a bare date, which UDDF's own examples write, reads the same way.
        """
        raw = _text_of(element, "datetime")
        if raw is None:
            return None
        value, _ = _date_time(raw)
        if value is None:
            self.note(where, f"{member} is {raw!r}, which is not a date; dropped", "dropped")
            return None
        return value[:10]

    def notes_text(self, parent: ET.Element | None) -> str | None:
        """A `<notes>` block as one string. Its `<link>` children carry no note text."""
        notes = _kid(parent, "notes")
        if notes is None:
            return None
        paragraphs = [text for text in (_text(para) for para in _kids(notes, "para")) if text]
        if not paragraphs:
            return None
        return "\n\n".join(paragraphs)

    # -- geometry ----------------------------------------------------------------

    def position(self, geography: ET.Element | None, where: str) -> dict[str, float] | None:
        """A Position from `<geography>`, reading an empty element as no coordinate.

        Subsurface writes `<latitude/>` for a site it has no coordinates for; everything
        past that — half a pair, the exact-zero pair, the WGS 84 range — is the shared rule.
        """
        return position(
            decimal_of(_text_of(geography, "latitude")),
            decimal_of(_text_of(geography, "longitude")),
            note=self.note,
            where=where,
        )

    # -- the run -----------------------------------------------------------------

    def run(self) -> Conversion:
        for element in self.root.iter():
            source_id = _attr(element, "id")
            if source_id is not None:
                self.source_ids.add(source_id)

        # Order matters: the dives resolve links into the tables the calls above them fill,
        # the centers come before the trip parts and the kit list whose inline shapes fold
        # into them, and the diver is last only so its identity yields to a real record's on
        # the vanishingly rare id collision.
        self.read_mixes()
        self.read_deco_models()
        sites = self.read_sites()
        self.read_centers()
        trips = self.read_trips()
        gear = self.read_gear()
        dives = self.read_dives()
        diver = self.read_diver()
        centers = [{member: row[member] for member in center_members() if member in row} for row in self.centers]

        document: dict[str, Any] = header(self.exported_at)
        if diver:
            document["diver"] = diver
        collections = (("dives", dives), ("trips", trips), ("sites", sites), ("gear", gear), ("centers", centers))
        for member, rows in collections:
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
        `<tankvolume>` read as litres is a recorded number at a scale this reader resolved
        rather than a value it computed, and reports itself as `resolved` for that reason.
        The list is kept rather than dropped because it is the shared policy every adapter
        inherits. The Subsurface reader that followed this one puts nothing in it either —
        its measurements state their units, so it has no scale to settle and nothing to
        compute — and a reader that takes a maximum depth off the samples of a file that
        recorded none is the one that will.
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
        here would discard what a second person's file records about them in silence.

        **An owner is a diver when it records anything this maps**, the id aside: an
        `<owner>` with empty names and a `<birthdate>` is a nameless diver with a date of
        birth, and one whose only content is a kit list is no diver at all.
        """
        owner = _dig(self.root, "diver", "owner")
        if owner is None:
            return None
        where = "diver"
        personal = _kid(owner, "personal")
        contact = _kid(owner, "contact")
        names = [
            text for text in (_text_of(personal, part) for part in ("firstname", "middlename", "lastname")) if text
        ]
        recorded: dict[str, Any] = {
            "email": self.email(_text_of(contact, "email"), where),
            "phone": self.phone(contact, where),
            "born_on": self.date_of(_kid(personal, "birthdate"), where, "the date of birth"),
            "insurances": self.read_insurances(owner, where),
        }
        if not names and not any(recorded.values()):
            self.note(where, "the source records nothing about the logbook's owner; no diver is written (spec §6.1)", "absent")
            return None

        claimed, carried = self.uuid_for("diver", _attr(owner, "id"), where, 0)
        diver: dict[str, Any] = {}
        if claimed is not None and carried:
            diver["uuid"] = claimed
        if names:
            diver["name"] = self.capped(" ".join(names), MAX_NAME, where, "the diver's name")
        diver.update((member, value) for member, value in recorded.items() if value)
        return diver

    def read_insurances(self, owner: ET.Element, where: str) -> list[dict[str, Any]]:
        """`<diveinsurances><insurance>` as §6.1's Insurance objects, in file order.

        `<name>` is the insurer and REQUIRED of one, so an insurance whose `<name>` holds no
        text is dropped rather than carried without it. The XSD requires the element and
        types it as nothing, so `<name/>` is valid UDDF; mapped without a `provider` it would
        fail the schema, and the whole file with it. What §6.1's Insurance has no member for
        is reported, and nothing fills its `number`: `insuranceType` has no element for one.
        """
        insurances: list[dict[str, Any]] = []
        for index, element in enumerate(_kids(_kid(owner, "diveinsurances"), "insurance")):
            here = f"{where}/insurance/{index}"
            provider = _text_of(element, "name")
            if not provider:
                self.note(
                    here,
                    "the insurance has no name, which the format requires of one as its insurer; it is dropped "
                    "(spec §6.1)",
                    "dropped",
                )
                continue
            insurance: dict[str, Any] = {"provider": self.capped(provider, MAX_NAME, here, "the insurer's name")}
            expires_on = self.date_of(_kid(element, "validdate"), here, "the insurance's validdate")
            if expires_on:
                insurance["expires_on"] = expires_on
            aliases = (_text(kid) for kid in _kids(element, "aliasname"))
            unread = [f"<aliasname> {alias!r}" for alias in aliases if alias]
            if _text_of(element, "issuedate", "datetime"):
                unread.append("<issuedate>")
            if any(_text(para) for para in _kids(_kid(element, "notes"), "para")):
                unread.append("<notes>")
            for what in unread:
                self.note(here, f"§6.1's Insurance has no member for {what}; it is not read", "dropped")
            insurances.append(insurance)
        return insurances

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
                # The place's name and nothing else. `<geography>`'s own coordinates are the
                # **site's** pin, not the locality's centre (§6.10), and UDDF has no element
                # for a fuller form of the place or for its extent — so a site read from
                # here never arrives with a `full_name`, a `location.position` or a `bbox`.
                site["location"] = {"name": self.capped(location, MAX_NAME, where, "the site's locality")}
            position = self.position(geography, where)
            if position:
                site["position"] = position
            notes = self.notes_text(element)
            if notes:
                site["notes"] = notes

            sites.append(site)
        return sites

    # -- centers -------------------------------------------------------------------

    def read_centers(self) -> None:
        """`<divesite><divebase>` and `<business><shop>` as §6.18 centers, the bases first.

        Each carries the role its slot implies, and a trip part's or a purchase's inline
        shape folds into one of these by name later (`read_inline`), which is why they are
        read before either.

        **A name-only base nothing points at is skipped**, and reported: Subsurface writes
        one into every export for want of the concept, and read as a center it would give
        every such logbook a dive center nobody dived with. The test is on shape rather than
        on that string — `docs/uddf-mapping.md` *Centers* has it — and a shop is never
        skipped, nothing writing one as a placeholder.
        """
        linked, named = self.center_anchors()
        for index, element in enumerate(_kids(_kid(self.root, "divesite"), "divebase")):
            where = f"divebase/{index}"
            name = _text_of(element, "name")
            source_id = _attr(element, "id")
            carries = any(_carries(_kid(element, tag)) for tag in ("address", "contact", "notes"))
            if name and not carries and source_id not in linked and name.casefold() not in named:
                self.note(
                    where,
                    f"the <divebase> {name!r} records nothing but its name and nothing in the file links it, "
                    "which is the placeholder a writer emits for want of a dive base; it is not read as a "
                    "center (spec §6.18)",
                    "dropped",
                )
                if source_id:
                    self.unread_centers.add(source_id)
                continue
            self.read_center(element, "dive_center", where)
        for index, element in enumerate(_kids(_kid(self.root, "business"), "shop")):
            self.read_center(element, "shop", f"shop/{index}")

    def center_anchors(self) -> tuple[set[str], set[str]]:
        """What keeps a name-only `<divebase>` from being a placeholder.

        The ids every dive and every `<trippart>` links, and the case-folded names every
        part's `<accomodation>` and `<operator>` carries — the second because an inline
        shape folds into the base it names, which is how a writer's copy of a center finds
        the base it came from.
        """
        linked: set[str] = set()
        for dive in self.dive_elements():
            for link in _kids(_kid(dive, "informationbeforedive"), "link"):
                ref = _attr(link, "ref")
                if ref:
                    linked.add(ref)
        named: set[str] = set()
        for trip in _kids(_kid(self.root, "divetrip"), "trip"):
            for part in _kids(trip, "trippart"):
                for link in _kids(part, "link"):
                    ref = _attr(link, "ref")
                    if ref:
                        linked.add(ref)
                for tag in _STAYS:
                    name = _text_of(_kid(part, tag), "name")
                    if name:
                        named.add(name.casefold())
        return linked, named

    def read_center(self, element: ET.Element, role: str, where: str) -> None:
        """One `<divebase>` or `<shop>` as a center, which a dive may link by its `@id`."""
        source_id = _attr(element, "id")
        name = _text_of(element, "name")
        if not name:
            self.note(
                where,
                f"the <{local_name(element)}> has no name, which the format requires of a center; it is "
                "dropped, and every link to it with it (spec §6.18)",
                "dropped",
            )
            if source_id:
                self.unread_centers.add(source_id)
            return
        # A path rather than an index for a shape with no id: centers come from four places,
        # and one count would give two of them one identity.
        claimed, carried = self.uuid_for("center", source_id, where, where)
        if claimed is None:
            if source_id:
                self.unread_centers.add(source_id)
            return
        if source_id:
            # Recorded before the row is written, and whether or not it is: a repeat of
            # another archive member's record is not carried again, and this file's
            # references to it still have to resolve to the one that is.
            self.center_uuids[source_id] = claimed
        self.add_center(element, claimed, carried, name, role, where)

    def read_inline(self, element: ET.Element, role: str, where: str, position: int | str) -> str | None:
        """A shape held inside another element — a part's stay, a purchase's shop — as a center.

        **Folded by name into a center already read**, trimmed and case-insensitively, where
        one has it: the shape adds its slot's role to that center, fills the members it
        lacks, and reports each one it states differently, the center's own standing. Two
        parts at one hotel are two elements and one center, and a resort's `<divebase>` and
        the `<accomodation>` a part slept at are one center with both roles. A shape that
        matches nothing is a center of its own, under its `@id` — or `position` where it has
        none, which is how an `<operator>` is identified (`operatorType` carries no id).

        Returns the center's uuid, or nothing where the shape names no center.
        """
        tag = local_name(element)
        name = _text_of(element, "name")
        if not name:
            self.note(
                where,
                f"the <{tag}> has no name, which the format requires of a center; it is dropped (spec §6.18)",
                "dropped",
            )
            return None
        standing = self.center_names.get(name.casefold())
        if standing is not None:
            standing["roles"] = roles_in_order([*standing["roles"], role])
            for member, value in self.center_contents(element, where).items():
                if member not in standing:
                    standing[member] = value
                elif standing[member] != value:
                    self.note(
                        where,
                        f"the <{tag}> folds into the center {standing['name']!r} by name, and states its {member} "
                        f"as {value!r} where the center has {standing[member]!r}; the center's stands",
                        "dropped",
                    )
            return str(standing["uuid"])
        claimed, carried = self.uuid_for("center", _attr(element, "id"), where, position)
        if claimed is None:
            return None
        self.add_center(element, claimed, carried, name, role, where)
        return claimed

    def add_center(self, element: ET.Element, uuid: str, carried: bool, name: str, role: str, where: str) -> None:
        """A center read from `element`, or — where another archive member carries it — only
        what a later shape needs to fold into it by name, that member reporting the rest."""
        center: dict[str, Any] = {"uuid": uuid, "name": name, "roles": [role]}
        if carried:
            center["name"] = self.capped(name, MAX_NAME, where, "the center's name")
            center.update(self.center_contents(element, where))
            self.centers.append(center)
        # The first of two centers of one name is the one a later shape folds into — a file
        # gives a reader nothing else to tell them apart by.
        self.center_names.setdefault(name.casefold(), center)

    def center_contents(self, element: ET.Element, where: str) -> dict[str, Any]:
        """What a center's shape says beyond its name, reporting what §6.18 does not carry.

        The same four children on every shape that reads into a center — `<address>`,
        `<contact>`, `<notes>` and the name — and every other child is reported by its tag,
        derived from the element rather than listed, so a price, a rating or a hotel's
        category is named in the report whichever shape carried it.
        """
        contact = _kid(element, "contact")
        contents = {
            "phone": self.phone(contact, where, section="§6.18", whose="the center"),
            "email": self.center_email(contact, where),
            "website": self.website(contact, where),
            "address": self.address(_kid(element, "address"), where),
            "notes": self.notes_text(element),
        }
        for child in element:
            tag = local_name(child)
            if tag in _CENTER_READ:
                continue
            said = f"<{tag}> {_text(child)!r}" if tag == "aliasname" and _text(child) else f"<{tag}>"
            self.note(where, f"§6.18 has no member for {said}; it is not read", "dropped")
        for child in contact if contact is not None else ():
            tag = local_name(child)
            if tag not in _CONTACT_READ and _carries(child):
                self.note(where, f"§6.18 has no member for <contact><{tag}>; it is not read", "dropped")
        return {member: value for member, value in contents.items() if value}

    def center_email(self, contact: ET.Element | None, where: str) -> str | None:
        """The first `<email>`, on the owner's terms (`email`), reporting every other one."""
        recorded = [value for value in (_text(kid) for kid in _kids(contact, "email")) if value]
        for value in recorded[1:]:
            self.note(
                where, f"§6.18 carries one email, the first the center records; {value!r} is not read", "dropped"
            )
        return self.email(recorded[0], where) if recorded else None

    def website(self, contact: ET.Element | None, where: str) -> str | None:
        """The first `<homepage>`, where it is an absolute URL, reporting every other one.

        Checked rather than merely capped: `xs:anyURI` admits a bare host, which §6.18's
        `website` does not mean and nothing can open, and a scheme prepended here would be
        one the file never stated. Past §6.18's bound it is dropped, not cut, for the reason
        `MAX_PHONE` gives.
        """
        recorded = [value for value in (_text(kid) for kid in _kids(contact, "homepage")) if value]
        if not recorded:
            return None
        for value in recorded[1:]:
            self.note(
                where, f"§6.18 carries one website, the first the center records; {value!r} is not read", "dropped"
            )
        value = recorded[0]
        if not _ABSOLUTE_URI.match(value):
            self.note(
                where,
                f"the recorded homepage {value!r} is not an absolute URL with its scheme; read as no website "
                "recorded rather than given a scheme the file never stated",
                "dropped",
            )
            return None
        if len(value) > MAX_WEBSITE:
            self.note(
                where,
                f"the recorded homepage is {len(value)} characters and the format allows {MAX_WEBSITE}; an address "
                "cut short is a wrong one, so it is dropped",
                "dropped",
            )
            return None
        return value

    def address(self, element: ET.Element | None, where: str) -> dict[str, str] | None:
        """An `<address>` as §6.19's Address, or nothing where it names no country.

        `<country>` is the anchor — `addressType` requires it and so does §6.19 — and the
        rest of an address without one has nowhere valid to go. It is dropped and reported
        rather than failing the whole conversion at the output validation, real files being
        routinely schema-invalid.
        """
        if not _carries(element):
            return None
        if not _text_of(element, "country"):
            self.note(
                where,
                "the address records no <country>, which §6.19 requires as the anchor every other part is "
                "read inside; the address is dropped",
                "dropped",
            )
            return None
        address: dict[str, str] = {}
        for tag, member, cap in _ADDRESS:
            value = _text_of(element, tag)
            if value:
                address[member] = self.capped(value, cap, where, f"the address's {member}")
        return address

    # -- trips -------------------------------------------------------------------

    def read_trips(self) -> list[dict[str, Any]]:
        """`<divetrip><trip>` as Trip records.

        A `<trippart>` is a §6.9a part, which is as close to an identity as this mapping
        gets: both formats model a trip as a sequence of stretches each carrying its own
        dates and its own place. Neither records dates on the trip itself, so a trip whose
        parts carry none has no span in either and is still a trip.
        """
        trips: list[dict[str, Any]] = []
        # Every `<trippart>` in the file before this trip's, counted whether or not its trip
        # is read: an `<operator>`'s identity is its part's position in the whole file.
        first_part = 0
        for index, element in enumerate(_kids(_kid(self.root, "divetrip"), "trip")):
            where = f"trip/{index}"
            name = _text_of(element, "name")
            first, first_part = first_part, first_part + len(_kids(element, "trippart"))
            if not name:
                self.note(where, "the trip has no name, which the format requires of one; it is dropped (spec §6.8)", "dropped")
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
                # The parts are read only for a trip this file carries: a part's stay mints a
                # center, and an `<operator>` has no id, so a repeat's would be a second
                # center for one boat.
                continue

            parts, notes = self.read_trip_parts(element, where, first)
            trip: dict[str, Any] = {"uuid": claimed, "name": self.capped(name, MAX_NAME, where, "the trip name")}
            if parts:
                trip["parts"] = parts
            if notes:
                trip["notes"] = notes

            trips.append(trip)
        return trips

    def read_trip_parts(
        self, element: ET.Element, where: str, first: int
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Every `<trippart>` as a part, in file order, and the trip's note.

        A part with neither a name, a date nor a place to stay is **no part at all**, which
        is what lets a trip with no parts survive a round trip: `tripType` requires at least
        one `<trippart>`, so a writer with nothing to put in one emits exactly this element
        (`docs/uddf-writing.md`), and reading it back as nothing is what closes the circle.
        Its note is still collected — a note belongs to the trip (§6.9a gives a part none).

        `first` is this trip's first part's position among every `<trippart>` in the file.
        """
        parts: list[dict[str, Any]] = []
        paragraphs: list[str] = []

        for part_index, part in enumerate(_kids(element, "trippart")):
            part_where = f"{where}/trippart/{part_index}"
            stay = self.read_stay(part, part_where, first + part_index)
            self.report_part_residue(part, part_where)
            date_of_trip = _kid(part, "dateoftrip")
            dates: dict[str, str] = {}
            for attribute, member in (("startdate", "starts_on"), ("enddate", "ends_on")):
                raw = _attr(date_of_trip, attribute)
                if raw is None:
                    continue
                value, _ = _date_time(raw)
                if value is None:
                    self.note(part_where, f"{attribute} is {raw!r}, which is not a date; dropped", "dropped")
                else:
                    dates[member] = value[:10]
            starts, ends = dates.get("starts_on"), dates.get("ends_on")
            if starts is not None and ends is not None and ends < starts:
                self.note(
                    part_where,
                    f"the part ends on {ends}, before it starts on {starts}; the end date is dropped",
                    "dropped",
                )
                ends = None

            geography = _kid(part, "geography")
            part_name = _text_of(part, "name")
            full_name = _text_of(geography, "location")
            location: dict[str, Any] | None = None
            if part_name:
                location = {"name": self.capped(part_name, MAX_NAME, part_where, "the trip part's name")}
                if full_name and full_name != part_name:
                    location["full_name"] = self.capped(full_name, MAX_FULL_NAME, part_where, "the location")
                position = self.position(geography, part_where)
                if position:
                    location["position"] = position
            elif geography is not None:
                # What the finding has to say is what survives, and that turns on the rest of
                # the part — a nameless part with no dates and nowhere to stay is nothing at
                # all once the place goes, which is the same split `uddf_write.nameless_part`
                # makes from the other side.
                kept = (
                    "the rest of the part is kept"
                    if starts is not None or ends is not None or stay is not None
                    else "the part records no dates and no stay either, so nothing of it is carried"
                )
                self.note(
                    part_where,
                    f"the trip part has no name, which the format requires of a location; the place is "
                    f"dropped and {kept} (spec §6.9)",
                    "dropped",
                )

            part_notes = self.notes_text(part)
            if part_notes:
                paragraphs.append(part_notes)

            record: dict[str, Any] = {}
            if starts is not None:
                record["starts_on"] = starts
            if ends is not None:
                record["ends_on"] = ends
            if location is not None:
                record["location"] = location
            if stay is not None:
                record["accommodation_uuid"] = stay
            if record:
                parts.append(record)

        joined = "\n\n".join(paragraphs) if paragraphs else None
        return parts, joined

    def read_stay(self, part: ET.Element, where: str, position: int) -> str | None:
        """Where the diver slept during one part: its `<accomodation>`, or its `<operator>`.

        `trippartType` holds one or the other, and the second comes with a `<vessel>`: UDDF's
        liveaboard, the boat being the bed. The operator is the center, with
        `roles: ["liveaboard"]`, and the vessel — a boat, which §6.18 does not model — is
        reported. An `<operator>` carries no id, so its identity is `position`, its part's
        place among every `<trippart>` in the file.
        """
        vessel = _kid(part, "vessel")
        if vessel is not None:
            named = _text_of(vessel, "name")
            self.note(
                f"{where}/vessel",
                f"the <vessel>{f' {named!r}' if named else ''} is a boat, which §6.18 does not model; it and "
                "everything it records about the boat are not read, the operator being the center",
                "dropped",
            )
        for tag, role in _STAYS.items():
            shape = _kid(part, tag)
            if shape is not None:
                return self.read_inline(shape, role, f"{where}/{tag}", position)
        return None

    def report_part_residue(self, part: ET.Element, where: str) -> None:
        """A part's `@type` and its `<link>`s, which §6.9a has no member for.

        The type says whether the diver slept aboard or ashore, which the center a part's
        `accommodation_uuid` names says for itself, and how the trip was booked, which
        nothing stores. A link to a base says the diver dived with it during the part, where
        §6.9a records where the diver stayed — who they dived with is each dive's (§6.2).
        The base itself is read, the link being what keeps a name-only one from being taken
        for a placeholder.
        """
        stated = _attr(part, "type")
        if stated is not None:
            self.note(where, f"§6.9a has no member for the part's type {stated!r}; it is not read", "dropped")
        for link in _kids(part, "link"):
            ref = _attr(link, "ref")
            if ref is None:
                continue
            if ref in self.center_uuids:
                self.note(
                    where,
                    f"the part links the center {ref!r}, saying the diver dived with it during the part; §6.9a "
                    "records where the diver stayed, and whom a dive was with is the dive's, so the link is "
                    "dropped (spec §6.9a)",
                    "dropped",
                )
            else:
                self.note(
                    where, f"the part links {ref!r}, and §6.9a has no member a link fills; it is dropped", "dropped"
                )

    # -- gear --------------------------------------------------------------------

    def read_gear(self) -> list[dict[str, Any]]:
        """The owner's kit list, from `<diver><owner><equipment>`.

        `<equipmentconfiguration>` is skipped along with anything else unrecognized: it
        describes how the pieces are rigged together, not a piece.
        """
        equipment = _dig(self.root, "diver", "owner", "equipment")
        # `is not None`, never a truth test: an `Element` with no children is falsy today
        # and `ElementTree` warns that it will not be.
        pieces = [child for child in equipment if local_name(child) in GEAR_TYPE] if equipment is not None else []
        gear: list[dict[str, Any]] = []
        for index, element in enumerate(pieces):
            kind = local_name(element)
            where = f"gear/{index}"
            if kind == "divecomputer":
                # Before the name test, and deliberately: `read_recordings` resolves a
                # dive's `<equipmentused><link>` through this table, and an element that
                # mints no gear item may still name a device (spec §6.4b).
                computer_id = _attr(element, "id")
                if computer_id:
                    self.computers[computer_id] = element
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
            # Only for a piece this file carries: a dropped piece's purchase goes with it, and
            # a repeat of another archive member's is that member's to read — a shop with no
            # id would be a second center for one shop.
            self.read_purchases(element, where)

            item: dict[str, Any] = {"uuid": claimed, "name": self.capped(name, MAX_NAME, where, "the gear name")}
            brand = _text_of(element, "manufacturer", "name")
            if brand:
                item["brand"] = self.capped(brand, MAX_NAME, where, "the brand")
            serial = _text_of(element, "serialnumber")
            if serial:
                # `<serialnumber>` is on `equipmentPieceType`, so it is read for **every**
                # gear type that carries one and not only for a computer — which is the
                # breadth §6.12 gives the member. On a computer it is also what lets a
                # writer recognise the kit item and a recording's device as one machine
                # without comparing names (`docs/uddf-writing.md`).
                item["serial"] = self.capped(serial, MAX_SERIAL, where, "the serial number")
            gear_type = GEAR_TYPE[kind]
            if kind == "suit" and (_text_of(element, "suittype") or "").lower() in _DRYSUIT_TYPES:
                gear_type = "drysuit"
            item["type"] = gear_type
            notes = self.notes_text(element)
            if notes:
                item["notes"] = notes

            gear.append(item)
        return gear

    def read_purchases(self, element: ET.Element, where: str) -> None:
        """A piece's `<purchase>`, numbered: the XSD allows one, and a file holding two keeps
        each shop apart by where it sits."""
        for number, purchase in enumerate(_kids(element, "purchase")):
            self.read_purchase(purchase, f"{where}/purchase/{number}")

    def read_purchase(self, purchase: ET.Element, where: str) -> None:
        """A `<purchase>`'s own `<shop>` as a center, and the purchase reported.

        The shop folds into a center already read by name, as a part's stay does
        (`read_inline`); the purchase itself — what the piece cost, when, and where — has no
        member until §6.12 records one.
        """
        shop = _kid(purchase, "shop")
        if shop is not None:
            self.read_inline(shop, "shop", f"{where}/shop", f"{where}/shop")
        self.note(
            where,
            "§6.12 has no member for the piece's <purchase>, its price and its date; it is not read"
            + (", and the shop it names is read as a center" if shop is not None else ""),
            "dropped",
        )

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
                raw = decimal_of(_text_of(element, tag))
                if raw is None:
                    continue
                percent, reinterpreted = _gas_percent(raw)
                if reinterpreted:
                    self.note(
                        where,
                        f"<{tag}> is {raw}, above the 1.0 the documentation describes; read as {percent} percent "
                        "rather than as a fraction",
                        "resolved",
                    )
                if not 0 <= percent <= 100:
                    self.note(where, f"<{tag}> reads as {percent} percent, outside the 0 to 100 a fraction can be; dropped", "dropped")
                    continue
                mix[member] = float(percent)
            if mix.get("oxygen", 0.0) + mix.get("helium", 0.0) > 100:
                self.note(where, "oxygen and helium sum above 100 percent, which no mix can; both are dropped (spec §6.3)", "dropped")
                mix.pop("oxygen", None)
                mix.pop("helium", None)

            ppo2_limit = decimal_of(_text_of(element, "maximumpo2"))
            if ppo2_limit is not None:
                if MIN_PO2_LIMIT <= ppo2_limit <= MAX_PO2_LIMIT:
                    mix["ppo2_limit"] = float(ppo2_limit)
                else:
                    self.note(where, f"<maximumpo2> is {ppo2_limit} bar, outside the 0.4 to 2.0 the format allows; dropped", "dropped")
            self.mixes[source_id] = mix

    def read_deco_models(self) -> None:
        """`<decomodel>`'s children into a table the dives' `<link ref>` resolves into.

        A logbook holds one `<decomodel>` block and many dives, and nothing says every dive
        ran the same model — so the model becomes *this dive's* through the `<link>` under
        its `<informationbeforedive>` rather than by being the only one in the file. The
        elements are kept rather than the objects they read as, because which of the three a
        link landed on is what the dive has to report.

        **`<tablegeneration><calculateprofile><profile><decomodel>` is deliberately not
        read**: that element names the model a *recalculation* used, which is an
        application's arithmetic rather than the device's, and §6.4c's object is what the
        device ran. `_kids` takes only the root's own `<decomodel>` children, so the nested
        one is out of reach by construction.
        """
        for element in _kids(self.root, "decomodel"):
            for model in element:
                source_id = _attr(model, "id")
                if source_id is not None:
                    self.deco_models[source_id] = model

    def read_deco_model(self, before: ET.Element | None, where: str) -> dict[str, Any] | None:
        """The §6.4c model a dive links, or nothing where it links none.

        Only `<buehlmann>` is mapped. `<vpm>` and `<rgbm>` are on the not-mapped list with
        the reason this reader gives everywhere: no file in hand carries either, and §6.4c's
        `algorithm` has no value seeded for them, so a link to one is reported rather than
        turned into a family this reader would be guessing at.
        """
        for link in _kids(before, "link"):
            ref = _attr(link, "ref")
            model = self.deco_models.get(ref) if ref is not None else None
            if model is None:
                continue
            if local_name(model) != "buehlmann":
                self.note(
                    where,
                    f"the dive's decompression model is a <{local_name(model)}>, and §6.4c names the two "
                    "families a file in hand states; the model is dropped rather than read as one of them",
                    "dropped",
                )
                return None
            return deco_model(
                {
                    "algorithm": "buhlmann",
                    "gf_low": self.gradient_factor(model, "gradientfactorlow"),
                    "gf_high": self.gradient_factor(model, "gradientfactorhigh"),
                },
                note=self.note,
                where=where,
                labels={"gf_low": "<gradientfactorlow>", "gf_high": "<gradientfactorhigh>"},
            )
        return None

    def gradient_factor(self, parent: ET.Element | None, tag: str = "gradientfactor") -> int | None:
        """One gradient factor as §6.4's whole percent, at the scale its generator writes.

        The same rule for all three elements, and it is the generator's rather than the
        value's — `PERCENT_GRADIENT_FACTORS` says why a magnitude test cannot settle it.
        Reported once per file, the first time it fires, because a scale is a property of
        the writer and not of any one reading.
        """
        value = decimal_of(_text_of(parent, tag))
        if value is None:
            return None
        if not self.percent_gradient_factors:
            return rounded(value * PERCENT_PER_FRACTION)
        if not self.reported_gradient_scale:
            self.note("$", PERCENT_GRADIENT_NOTE, "resolved")
            self.reported_gradient_scale = True
        return rounded(value)

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
        number = integer_of(decimal_of(_text_of(before, "divenumber")))
        if number is not None:
            dive["number"] = number
        dive["started_at"] = started_at

        duration = integer_of(decimal_of(_text_of(after, "diveduration")))
        if duration is not None:
            if recorded(duration, record="dive", member="duration"):
                dive["duration"] = duration
            else:
                self.note(where, f"<diveduration> is {duration} seconds; the format records a duration only when it is positive", "absent")

        notes = self.notes_text(after)
        if notes:
            dive["notes"] = notes

        max_depth = self.positive(decimal_of(_text_of(after, "greatestdepth")), where, "<greatestdepth>", "max_depth")
        avg_depth = self.positive(decimal_of(_text_of(after, "averagedepth")), where, "<averagedepth>", "avg_depth")
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

        lowest = decimal_of(_text_of(after, "lowesttemperature"))
        if lowest is not None:
            dive["bottom_temperature"] = float(lowest - KELVIN_OFFSET)

        visibility = decimal_of(_text_of(after, "visibility"))
        if visibility is not None:
            if recorded(visibility, record="dive", member="visibility"):
                dive["visibility"] = float(visibility)
            else:
                self.note(where, f"<visibility> is {visibility} m; dropped", "dropped")

        used = _kid(before, "equipmentused")
        weight = decimal_of(_text_of(used, "leadquantity"))
        if weight is not None:
            # A zero is kept here and read as absence for `max_depth`, and neither is this
            # module's choice: `weight`'s floor in the schema is inclusive and
            # `max_depth`'s is not. A recorded "no lead" is a fact about the dive, and
            # absence is how "we do not know" is spelled here (spec §6.2).
            if recorded(weight, record="dive", member="weight"):
                dive["weight"] = float(weight)
            else:
                self.note(where, f"<leadquantity> is {weight} kg; dropped", "dropped")

        altitude = integer_of(decimal_of(_text_of(before, "altitude")))
        if altitude is not None:
            if MIN_ALTITUDE <= altitude <= MAX_ALTITUDE:
                dive["altitude"] = altitude
            else:
                self.note(where, f"<altitude> is {altitude} m, outside the -450 to 6500 the format allows; dropped", "dropped")

        # §6.4a's readout rather than a member of the dive, and it is read here only
        # because it is a child of the dive: `read_recordings` is where it goes.
        readouts: dict[str, float] = {}
        surface_pressure = decimal_of(_text_of(before, "surfacepressure"))
        if surface_pressure is not None:
            bar = surface_pressure / PASCAL_PER_BAR
            if MIN_SURFACE_PRESSURE <= bar <= MAX_SURFACE_PRESSURE:
                readouts["surface_pressure"] = float(bar)
            else:
                self.note(where, f"<surfacepressure> reads as {bar} bar, outside the 0.4 to 1.2 the format allows; dropped", "dropped")

        trip_uuid = self.reference(_attr(_kid(before, "tripmembership"), "ref"), self.trip_uuids, where, "trip")
        if trip_uuid:
            dive["trip_uuid"] = trip_uuid
        # A site first where an id names both a site and a center — a source id is not unique
        # within a file (the module docstring).
        links = [_attr(link, "ref") for link in _kids(before, "link")]
        to_centers = [
            ref
            for ref in links
            if ref is not None
            and ref not in self.site_uuids
            and (ref in self.center_uuids or ref in self.unread_centers)
        ]
        center_uuid = self.dive_center(to_centers, where)
        if center_uuid:
            dive["center_uuid"] = center_uuid
        site_uuids = self.references(
            [ref for ref in links if ref not in to_centers], self.site_uuids, where, "dive site"
        )
        if site_uuids:
            dive["site_uuids"] = site_uuids
        gear_uuids = self.references([_attr(link, "ref") for link in _kids(used, "link")], self.gear_uuids, where, "gear item")
        if gear_uuids:
            dive["gear_uuids"] = gear_uuids

        cylinders, mix_refs = self.read_cylinders(element, where)
        profile, needs_gas_numbers, mode = self.read_profile(element, where, mix_refs)
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
        recordings = self.read_recordings(
            before, used, profile, mode, self.read_deco_model(before, where), readouts, where
        )
        if recordings:
            dive["recordings"] = recordings
        return dive

    def read_recordings(
        self,
        before: ET.Element | None,
        used: ET.Element | None,
        profile: dict[str, Any] | None,
        mode: str | None,
        model: dict[str, Any] | None,
        readouts: dict[str, float],
        where: str,
    ) -> list[dict[str, Any]]:
        """A §6.4a Recording per `<divecomputer>` the dive links, in link order.

        UDDF states a dive's `<samples>`, its `<internaldivenumber>` and its
        `<surfacepressure>` **once per dive** and never once per computer, so all three go
        to the first linked computer and to no other: a dive linking two has one profile, one
        counter and one surface pressure, and there is no way in the file to say whose the
        counter or the pressure is. Confining the counter there is also what keeps the
        emptiness test below from circling — every later link's device is made of that
        element's own four members and nothing the dive supplies.

        The carve-out follows from those facts rather than adding to them. The profile and
        the surface pressure are the only things that carry a link past it without a device,
        and they reach exactly one link, so **where the dive has either** the first link is a
        recording whether or not its element names a device — exactly as a dive linking no
        computer at all is one recording made of what the dive states — and a link yielding
        neither a device nor one of those yields nothing, §6.4a forbidding a recording that
        carries nothing. Where the dive has neither, every link is judged on its device
        alone, the first included.

        **The surface pressure on a dive with two recordings is `resolved`** rather than
        silent: it is one computer's figure (§6.4a) and the file does not say whose, so
        giving it to the first is a reading of its meaning, which `docs/converting.md` asks a
        reader to report.
        """
        counter = integer_of(decimal_of(_text_of(before, "internaldivenumber")))
        linked = [
            self.computers[ref]
            for ref in (_attr(link, "ref") for link in _kids(used, "link"))
            if ref is not None and ref in self.computers
        ]
        if not linked:
            if counter is not None:
                self.note(
                    where,
                    "the dive records the computer's own dive counter and links no <divecomputer> for it "
                    "to belong to, so there is no device to carry it; dropped (spec §6.4b)",
                    "dropped",
                )
            built = recording(profile=profile, mode=mode, deco_model=model, readouts=readouts)
            return [built] if built is not None else []

        recordings: list[dict[str, Any]] = []
        for index, element in enumerate(linked):
            built = recording(
                device=self.read_device(element, counter if index == 0 else None, where),
                # The mode and the model go where the profile goes, and for the same
                # reason: UDDF states `<divemode>` on the dive's one sample stream and links
                # its `<decomodel>` from the dive, never from a computer, so a dive linking
                # two computers has no way in the file to say whose either is.
                mode=mode if index == 0 else None,
                deco_model=model if index == 0 else None,
                readouts=readouts if index == 0 else None,
                profile=profile if index == 0 else None,
            )
            if built is not None:
                recordings.append(built)
        if readouts and len(recordings) > 1:
            self.note(where, shared_readout("<surfacepressure>", len(recordings)), "resolved")
        return recordings

    def read_device(
        self, element: ET.Element, counter: int | None, where: str
    ) -> dict[str, Any] | None:
        """One linked `<divecomputer>` as a §6.4b Device.

        **`<name>` is read twice, into two members of two records, and that is not a
        duplication.** It is the only string in this format that names the computer at all,
        and the two members mean different things: §6.12's `name` is the diver's label for
        a thing in their kit list, and §6.4b's is what the device calls itself. Reading it
        only as the gear item's would leave the corpus's own UDDF computer with a device
        that has no string naming it — `fixtures/uddf/opendiving.uddf` carries a `<name>`
        and no `<model>` — and reading it into `model` instead would put `Ocean` where the
        same dive's FIT export puts `Suunto Ocean`.

        An **empty** `<name>` is no name, which is what makes a written file round-trip:
        `docs/uddf-writing.md` emits an empty one for a device that has none, `<name>`
        being mandatory on the element, and it comes back as no `device.name`.

        UDDF has no equipment element for a firmware version, so §6.4b's `firmware` has no
        source here.
        """
        return device(
            {
                "brand": _text_of(element, "manufacturer", "name"),
                "model": _text_of(element, "model"),
                "serial": _text_of(element, "serialnumber"),
                "name": _text_of(element, "name"),
                "dive_number": counter,
            },
            note=self.note,
            where=where,
            labels={
                "brand": "<manufacturer><name>",
                "model": "<model>",
                "serial": "<serialnumber>",
                "name": "<name>",
                "dive_number": "<internaldivenumber>",
            },
        )

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
        if "T" not in started_at:
            # A date-only start (§5.2): the day was recorded and the time of day was not,
            # and midnight would be a time the file never stated. An offset beside a bare
            # date has nowhere to go, a date carrying none.
            self.note(where, f"{forgiven}; read as a date-only start rather than as midnight (spec §5.2)", "absent")
            stated = _DATE_TIME.match(raw.strip())
            if stated is not None and stated["offset"] is not None:
                self.note(where, f"{raw.strip()!r} records a UTC offset beside a date alone, which a date cannot carry; dropped", "dropped")
            return started_at
        if forgiven:
            self.note(where, forgiven, "absent")
        if self.local_clock_with_z and started_at.endswith(("Z", "z")):
            # The generator table firing. `<generator><datetime>` is left alone under this
            # rule and every other — it is the export instant, a fact about the run rather
            # than logbook data.
            self.note(where, LOCAL_CLOCK_NOTE, "resolved")
            # And the no-offset note below is not also emitted, because it would be false:
            # the source *did* record something in that position and this reader decided
            # what it meant. Saying "the source recorded no UTC offset" beside a `resolved`
            # finding that says otherwise is two report lines disagreeing about one value.
            return started_at[:-1]
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

    def dive_center(self, refs: list[str], where: str) -> str | None:
        """The center a dive's `<link>`s name — a `<divebase>` or a `<shop>` — or nothing.

        UDDF's prose names buddies and sites as this link's targets, and its XSD lets it name
        any id, so resolving a base or a shop loses nothing. §6.2 carries one center, so a
        dive linking two keeps the first and reports the rest; a link to a base or a shop
        that was not read goes with it, and says so.
        """
        found: list[str] = []
        for ref in refs:
            if ref not in self.center_uuids:
                self.note(
                    where,
                    f"a link points at the center {ref!r}, which is not read; the reference goes with it",
                    "dropped",
                )
            elif self.center_uuids[ref] not in found:
                found.append(self.center_uuids[ref])
        if len(found) > 1:
            self.note(
                where,
                f"the dive links {len(found)} centers and §6.2 carries one; the first is kept and the rest are "
                "dropped",
                "dropped",
            )
        return found[0] if found else None

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
            elif ref not in self.mixes and ref not in self.deco_models:
                # A `<link>` under `informationbeforedive` addresses a site here — a center's
                # was taken off before (`dive_center`) — but the schema lets it address a
                # buddy too, and one under `<equipmentused>` addresses a piece of kit. A
                # reference to a record this converter carries nowhere is worth a note; a gas
                # reference is not, and neither is a decompression model — that one resolves
                # in `read_deco_model` and was reported there if it went nowhere.
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

            raw_volume = decimal_of(_text_of(tank, "tankvolume"))
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
                        "resolved",
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
        value = decimal_of(text)
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
    ) -> tuple[dict[str, Any] | None, bool, str | None]:
        """`<samples><waypoint>` as a Profile, whether gas numbers are needed, and the mode.

        UDDF puts every reading taken at one instant inside one `<waypoint>`; DiveJSON
        splits them into channels sampled on their own axes. So the waypoints set the time
        axis and each channel takes only the waypoints that actually carried a reading for
        it — which is why a converted Subsurface dive keeps 431 depth samples and 29
        temperatures rather than padding the second to match the first.

        The axis itself — the ordering, the dropped and reported waypoints, the two on one
        millisecond, the profile that is not written at all — is `series.SampleAxis`, shared
        with every other format, and only what is UDDF's is below: which element carries
        which channel, and what a `<tankpressure ref>` resolves to.

        **The recording's `mode` comes back from here** because `<divemode>` is a waypoint
        child rather than a dive-level element: the first waypoint that states one gives the
        recording its mode, and the third return value is that. §6.4a is where it lands, not
        §6.5, so it leaves this method rather than joining the profile.
        """
        samples = _kid(element, "samples")
        if samples is None:
            return None, False, None

        # `<divetime>` is `xs:float` seconds, and a fraction it states keeps its place on the
        # millisecond axis: `legacy-writer.uddf`'s `30` and `30.4` share a second and are
        # two instants here, both kept.
        axis = SampleAxis(self.note, where, noun="waypoint", time_member="<divetime>")
        for waypoint in _kids(samples, "waypoint"):
            axis.offer(milliseconds(decimal_of(_text_of(waypoint, "divetime"))), waypoint)

        cylinders_of_mix: dict[str, list[int]] = {}
        for index, ref in enumerate(mix_refs):
            if ref is not None:
                cylinders_of_mix.setdefault(ref, []).append(index)

        depth = Channel("depth")
        temperature = Channel("temperature")
        ndl = Channel("ndl")
        ppo2 = Channel("ppo2")
        cns = Channel("cns")
        gradient_factor = Channel("gradient_factor")
        pressures: dict[int, Channel] = {}
        events: list[dict[str, Any]] = []
        needs_gas_numbers = False
        mode: str | None = None
        reported_pascal_po2 = False

        for at, waypoint in axis.ordered():
            metres = decimal_of(_text_of(waypoint, "depth"))
            if metres is not None:
                depth.record(at, rounded(metres * CENTIMETRES_PER_METRE))

            kelvin = decimal_of(_text_of(waypoint, "temperature"))
            if kelvin is not None:
                temperature.record(at, rounded((kelvin - KELVIN_OFFSET) * TENTHS_PER_UNIT))

            # Seconds already, which is §6.4's unit for the reading — the axis it sits on
            # is milliseconds, the value is not. A value at the device's display cap —
            # 5 940, the Shearwater's 99 minutes — is a reading rather than a missing one:
            # it means *at least this*, which is the number the diver read off their wrist.
            seconds = decimal_of(_text_of(waypoint, "nodecotime"))
            if seconds is not None:
                ndl.record(at, rounded(seconds))

            po2, in_pascal = self.po2_hundredths(_text_of(waypoint, "calculatedpo2"))
            if po2 is not None:
                if in_pascal and not reported_pascal_po2:
                    self.note(
                        where,
                        "<calculatedpo2> is above 10, so it is the Pascal the documentation states rather than "
                        "the bar current writers emit; read as Pascal",
                        "resolved",
                    )
                    reported_pascal_po2 = True
                ppo2.record(at, po2)

            percent = decimal_of(_text_of(waypoint, "cns"))
            if percent is not None:
                cns.record(at, rounded(percent * TENTHS_PER_UNIT))

            factor = self.gradient_factor(waypoint)
            if factor is not None:
                gradient_factor.record(at, factor)

            mode = self.waypoint_mode(waypoint, mode, at, where)

            for cylinder_index, tenths in self.waypoint_pressures(waypoint, where, at, cylinders_of_mix, len(mix_refs)):
                channel = pressures.setdefault(cylinder_index, Channel("pressures"))
                # Asked before the reading is offered rather than read off `record`'s
                # answer, which has two refusals in it: a pressure channel carries no floor
                # and so can only be refused for the second, but a caller that reported a
                # floor refusal as a collision would be saying the wrong thing quietly.
                if channel.taken(at):
                    self.note(where, f"two tank pressures at {in_seconds(at)} s resolve to the same cylinder; the later one is dropped", "dropped")
                    continue
                channel.record(at, tenths)
                needs_gas_numbers = True

            marker = _text_of(waypoint, "setmarker")
            if marker is not None:
                if marker in _MARKER_TYPES:
                    events.append({"time": at, "type": marker})
                else:
                    # No `type` at all, which §6.6 makes the spelling of an unclassified
                    # event: `<setmarker>` is a bare string with no type beside it, and the
                    # device's own wording is all this one has.
                    events.append({"time": at, "label": marker})

            switch = _kid(waypoint, "switchmix")
            if switch is not None:
                event: dict[str, Any] = {"time": at, "type": "gas_switch"}
                ref = _attr(switch, "ref")
                if ref is not None and ref in cylinders_of_mix:
                    event["gas_number"] = cylinders_of_mix[ref][0]
                    needs_gas_numbers = True
                elif ref is not None:
                    self.note(
                        where,
                        f"a gas switch at {in_seconds(at)} s names the gas {ref!r}, which no cylinder on this dive links "
                        "to; the switch is kept without saying what it was to (spec §6.6)",
                        "absent",
                    )
                events.append(event)

        profile = axis.profile(
            # §6.4's own member order, so a converted profile reads down the section.
            {
                "depth": depth,
                "temperature": temperature,
                "ndl": ndl,
                "ppo2": ppo2,
                "cns": cns,
                "gradient_factor": gradient_factor,
            },
            pressures=tuple(pressures.items()),
            events=events,
        )
        return profile, (needs_gas_numbers if profile else False), mode

    def po2_hundredths(self, text: str | None) -> tuple[int | None, bool]:
        """A `<calculatedpo2>` as §6.4's hundredths of a bar, and whether it was Pascal.

        The documentation states Pascal and Shearwater Cloud Desktop writes `0.399999976`
        for a ppO₂ of 0.4 bar. The two spellings are three orders of magnitude apart and a
        breathable ppO₂ lives between about 0.1 and 2 bar, so nothing overlaps and the value
        settles it: at or below 10 it is bar, above it Pascal. `converting.md`'s ambiguity of
        scale, reported `resolved` when the second branch fires.
        """
        value = decimal_of(text)
        if value is None:
            return None, False
        if value <= PO2_BAR_THRESHOLD:
            return rounded(value * HUNDREDTHS_PER_UNIT), False
        return rounded(value / PASCAL_PER_PO2_HUNDREDTH), True

    def waypoint_mode(
        self, waypoint: ET.Element, standing: str | None, at: int, where: str
    ) -> str | None:
        """One waypoint's `<divemode @type>` against the mode the recording already has.

        **The first waypoint that states a value wins**, in recorded-time order, and a later
        waypoint stating a *different* one is reported and dropped: the only place §6.6 could
        carry a mid-dive switch is an event, an event with no type needs a `label`, and a
        label a converter invents is §5.4's fabrication. A later waypoint that simply stops
        stating the mode is not a change — an absence is not a claim — and no file in hand
        changes mode mid-dive.

        A `@type` outside the table, or a `<divemode>` with no `@type`, leaves the mode
        absent and is reported: §6.4a forbids assuming open circuit, and there is nothing
        else in the element to read.
        """
        element = _kid(waypoint, "divemode")
        if element is None:
            return standing
        stated = _attr(element, "type")
        mode = _DIVE_MODES.get(stated) if stated is not None else None
        if mode is None:
            # **A bare `<divemode/>` is schema-valid**, whatever the 3.2.1 documentation's
            # "compulsory" says: `tests/fixtures/uddf_3.2.2.xsd` declares `@type` with no
            # `use`, which XSD reads as optional. So it is not a malformed file being
            # reported here — it is an element that states nothing, read and reported like
            # any other value this reader cannot place, schema validity having never been a
            # precondition in either direction.
            said = "no dive mode" if stated is None else f"the dive mode {stated!r}"
            self.note(
                where,
                f"a waypoint records {said}, which is not one of the five UDDF spells; the recording's mode "
                "is left unrecorded rather than assumed (spec §6.4a)",
                "dropped",
            )
            return standing
        if standing is None:
            return mode
        if mode != standing:
            self.note(
                where,
                f"the dive mode changes to {stated!r} at {in_seconds(at)} s, and §6.4a records one mode for a "
                "recording; the change is dropped, there being no event a converter could label without "
                "inventing the device's wording (spec §5.4)",
                "dropped",
            )
        return standing

    def waypoint_pressures(
        self,
        waypoint: ET.Element,
        where: str,
        at: int,
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
            pascal = decimal_of(_text(element))
            if pascal is None:
                continue
            ref = _attr(element, "ref")
            if ref is None:
                if tank_count != 1:
                    self.note(
                        where,
                        f"a tank pressure at {in_seconds(at)} s names no cylinder, and the dive has {tank_count}; the "
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
                        f"a tank pressure at {in_seconds(at)} s names the gas {ref!r}, which no further cylinder on this "
                        "dive links to; the reading is dropped",
                        "dropped",
                    )
                    continue
                index = candidates[position]
            yield index, rounded(pascal / PASCAL_PER_BAR * TENTHS_PER_UNIT)
