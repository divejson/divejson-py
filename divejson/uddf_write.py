"""Writing DiveJSON out as UDDF.

The one direction this package writes, and the reason it does: UDDF is what Subsurface,
divelogs.de and MacDive read, so a diver whose logbook is DiveJSON needs a way back out
into the format the rest of the world opens. `docs/uddf-writing.md` is the prose companion
— what this writer carries, what it cannot, and what a reader will make of each — and it is
written for a port in another language as much as for a reader of this file.

**This is checked through the reader, not against a second implementation.** DiveJSON's
reference writer is the application this format came out of; a second one built to match it
byte for byte would be a mirror of a module in another repository, and the first divergence
between them would be a bug in whichever was read last. So the bar here is two things a corpus can hold:
a written pair in `fixtures/write/uddf/`, compared as canonical XML with `<generator>`
ignored, and the **self round trip** — reading a written file back through `uddf.py` returns
the document it was written from, on every member `docs/uddf-mapping.md`'s element map
carries. Everything else is named in the report, and the report is half the output here
exactly as it is on the way in.

Four things shape the module.

**The XSD is the referee.** `informationbeforedive` and `waypoint` are `xs:sequence`, so
their children go in the schema's order and not in one that reads well; `equipment` is a
sequence too, which is why a logbook's gear comes back grouped by type rather than in the
order the document listed it. `informationafterdive` and `geography` are `xs:all` and free.
Every one of those is asserted by validating this writer's output against
`tests/fixtures/uddf_3.2.2.xsd` in the suite, because reading a document back cannot tell
you whether its elements were in a legal order.

**Nothing is invented to satisfy a required element.** UDDF makes `<location>` mandatory
inside `<geography>` and `<greatestdepth>` mandatory on every dive, and the two are not the
same case. A depth has a spelling for "not recorded" that this format's own reader
understands — `0`, which `<greatestdepth>` being mandatory forces on every writer — so that
is written and reported. A place name has none, so a site that has coordinates and no
`location` keeps its coordinates out of the file rather than having its **name** copied
into a member that means something else: a round trip would then hand back a location the
diver never wrote, and §5.4 is about exactly that.

**A cylinder gets a `<mix>` of its own within its dive.** Gases dedupe across the logbook,
so a hundred air dives share one `<mix>` — but two cylinders of *one* dive never do, even
carrying the identical blend. `<tankpressure ref>` and `<switchmix ref>` address a mix
rather than a cylinder, and the reader resolves a shared reference positionally, so a
sidemount pair on one blend would come back with its two pressure channels crossed the
moment one of them missed a waypoint the other had.

**Every reading keeps its own second.** UDDF puts everything recorded at one instant inside
one `<waypoint>`, so the waypoints here are the union of every channel's times and a
waypoint carries only what was actually measured at that second. The reference writer snaps
its other channels onto the depth axis instead and drops what cannot reach one, because two
importers mishandle a depth-less waypoint in opposite ways — a real constraint on a file
written for those two, and the wrong trade for a file written to be read back. What that
costs a consumer is in `docs/uddf-writing.md` under *Known consumer artefacts*.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from . import __version__
from .converter import (
    CENTIMETRES_PER_METRE,
    GENERATOR_NAME,
    TENTHS_PER_UNIT,
    Note,
    NoteKind,
    Written,
)
from .uddf import (
    FORMAT,
    GEAR_TYPE,
    KELVIN_OFFSET,
    LITRES_PER_CUBIC_METRE,
    PASCAL_PER_BAR,
    MalformedUddfError,
)
from .xmlsource import local_name, parse_xml

__all__ = ["SUFFIX", "UDDF_NAMESPACE", "UDDF_VERSION", "UddfWriter", "compared", "write_uddf"]

# The extension a written file takes, and what `write/uddf/` pairs an input with.
SUFFIX = ".uddf"

# The namespace and version this writer emits. 3.2.2 is the newest published schema, and
# the revision `tests/fixtures/uddf_3.2.2.xsd` holds.
UDDF_NAMESPACE = "http://www.streit.cc/uddf/3.2/"
UDDF_VERSION = "3.2.2"

# `generatorType`'s enumeration has three values — `converter`, `divecomputer`, `logbook` —
# and this is the one that is true of this package. The reference writer says `logbook`,
# being one.
GENERATOR_TYPE = "converter"

_INDENT = "  "

# Where each of §6.12's gear types lands in `equipmentType`. Every type UDDF's own
# vocabulary does not name goes to `<variouspieces>`, which is UDDF's catch-all and which
# the reader maps back to `other` — the same translation the reader makes in the other
# direction for `<scooter>` and `<lead>`. The reference writer sends a line cutter and a
# pair of shears to `<knife>` instead, on the grounds that they are cutting tools; this one
# does not, because `<knife>` asserts a knife where `<variouspieces>` asserts nothing, and
# a converter's job is to lose the type honestly rather than to substitute a near one.
_EQUIPMENT_ELEMENT: dict[str, str] = {
    "mask": "mask",
    "snorkel": "variouspieces",
    "fins": "fins",
    "wetsuit": "suit",
    "drysuit": "suit",
    "vest": "variouspieces",
    "hood": "variouspieces",
    "gloves": "gloves",
    "boots": "boots",
    "bcd": "buoyancycontroldevice",
    "regulator": "regulator",
    "computer": "divecomputer",
    "cylinder": "tank",
    "light": "light",
    "smb": "variouspieces",
    "mirror": "variouspieces",
    "whistle": "variouspieces",
    "reel": "variouspieces",
    "knife": "knife",
    "line_cutter": "variouspieces",
    "shears": "variouspieces",
    "compass": "compass",
    # `cameraType` extends `ID_TYPE` rather than `namedType`, so a `<camera>` has no
    # `<name>` at all and could carry only a nameless body-and-lens breakdown. A named
    # camera is a `<variouspieces>`, and the type is reported as not carried.
    "camera": "variouspieces",
    "other": "variouspieces",
}

# `equipmentType`'s own declaration order, filtered to the tags above: it is an
# `xs:sequence`, so a piece emitted out of this order makes the document invalid.
_EQUIPMENT_ORDER = (
    "boots",
    "buoyancycontroldevice",
    "compass",
    "divecomputer",
    "fins",
    "gloves",
    "knife",
    "light",
    "mask",
    "regulator",
    "suit",
    "tank",
    "variouspieces",
)

_SUIT_TYPE = {"wetsuit": "wet-suit", "drysuit": "dry-suit"}

# The gear types whose element reads back as the same type, derived from the reader's own
# map rather than restated. A second table here would be a second thing to get out of step,
# and this one *is* the fidelity claim: a type is carried exactly when reading its element
# returns it.
_GEAR_ROUND_TRIPS = frozenset(
    gear_type
    for gear_type, tag in _EQUIPMENT_ELEMENT.items()
    if GEAR_TYPE.get(tag) == gear_type or (tag == "suit" and gear_type in _SUIT_TYPE)
)

# The event types `<setmarker>` carries as themselves, because the reader reads exactly
# these three back as a type rather than as an `other` labelled with the text.
_MARKER_TYPES = frozenset({"deep_stop", "safety_stop", "bookmark"})

# What one dive's waypoints are collected into before they are written: a second, and the
# readings and annotations that landed on it. UDDF's `<waypoint>` is the one instant every
# channel has to be folded back into, so the fold happens here and the elements come out of
# it in the schema's order.
_Readings = dict[int, dict[str, Any]]

# Everything XML 1.0 forbids outright, even escaped: the C0 controls other than tab, LF and
# CR, plus DEL. `ElementTree` escapes `&`, `<` and `>` and passes these straight through, so
# one of them in a note makes the whole document unparseable — and JSON admits every one of
# them in a string, so a conforming DiveJSON document can carry them. Dropped rather than
# replaced: they carry no meaning a diver put there.
_FORBIDDEN_IN_XML = str.maketrans(
    dict.fromkeys(range(0x20), None) | {0x09: "\t", 0x0A: "\n", 0x0D: "\r", 0x7F: None}
)


def _xml_safe(value: str) -> str:
    return value.translate(_FORBIDDEN_IN_XML)


def _decimal(value: Any) -> Decimal:
    """A JSON number as a `Decimal`, through its own text.

    `Decimal(str(29.6))` is exactly `29.6` where `Decimal(29.6)` is the binary double's
    full expansion, and every factor below is a decimal one — so this is what keeps a
    round trip through Kelvin and Pascal landing back on the number it started from.
    """
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _num(value: Any) -> str:
    """Format a number for an `xs:float` element.

    Fixed notation, trailing zeros trimmed: a tank pressure in Pascal is eight digits and
    the exponent notation `Decimal.__str__` reaches for at that size is legal and unreadable
    — the first thing a human checking an export looks at is whether the numbers look like
    numbers.
    """
    text = format(_decimal(value).normalize(), "f")
    return "0" if text in ("", "-", "-0") else text


def _sub(parent: ET.Element, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
    element = ET.SubElement(parent, tag, {key: _xml_safe(value) for key, value in attrs.items()})
    if text is not None:
        element.text = _xml_safe(text)
    return element


def _optional(parent: ET.Element, tag: str, value: Any) -> None:
    """Emit `<tag>` only where the document has a reading. UDDF has no way to say "not
    recorded" other than by leaving the element out."""
    if value is not None:
        _sub(parent, tag, _num(value))


def _notes(parent: ET.Element, text: Any) -> bool:
    """A record's `notes` as one `<para>`, and whether it was written.

    One paragraph rather than a split on blank lines, because the reader joins the paragraphs
    it finds with a blank line between them: splitting here and joining there would survive
    a round trip only for text that had no other blank line in it.

    An **empty** note is not written and the caller reports it. `<para></para>` and no
    `<notes>` at all read back identically — every XML reader here takes an empty element as
    absent, which is what a writer with no coordinates forced on it — so an empty string is a
    value UDDF has no spelling for rather than one this writer chose to drop.
    """
    if not text:
        return False
    _sub(_sub(parent, "notes"), "para", str(text))
    return True


def _uddf_id(prefix: str, uuid: str) -> str:
    """An `xs:ID` from a DiveJSON uuid.

    Prefixed because `xs:ID` is an `NCName` and cannot begin with a digit, which a hex uuid
    regularly does. `converting.md`'s identity rule reads the prefix back off — a short
    alphabetic head before a uuid is stripped — so an id written this way comes back as the
    uuid it was, which is what makes identities survive the trip out and in.
    """
    return f"{prefix}-{uuid}"


def _person_names(name: str) -> tuple[str, str]:
    """One free-text name split into `personalType`'s mandatory first/last pair.

    The first whitespace-separated token is the given name and the remainder the family
    name, which is right for "Ada Lovelace" and harmless for the rest — the reader joins
    them back with a space, so any split round-trips. A one-token name leaves `<lastname>`
    **empty**, which is a valid `xs:string` and which the reader reads as no surname rather
    than as one.
    """
    tokens = name.split()
    if not tokens:
        return "", ""
    return tokens[0], " ".join(tokens[1:])


@dataclass(frozen=True, slots=True)
class _MixKey:
    """What makes two cylinders the same `<mix>`: the blend and the limit planned for it.

    `po2_limit` is part of the key rather than of the payload alone, because it maps onto
    `<mix><maximumpo2>` — a diver carrying the same EAN32 planned to 1.4 on the bottom and
    1.6 on the ascent has defined two mixes as far as UDDF is concerned, and collapsing them
    would mean picking one limit and dropping the other. An unrecorded fraction is a key
    value of its own rather than a zero: "no mix was recorded" and "air" are different
    gases.
    """

    oxygen: Decimal | None
    helium: Decimal | None
    po2_limit: Decimal | None

    @property
    def sort_key(self) -> tuple[Decimal, Decimal, Decimal]:
        # `-1` sorts an unrecorded value ahead of every real one — the fractions floor at 0
        # and the ppO2 constraint at 0.4 bar — so the mix list is stable without
        # special-casing `None`.
        absent = Decimal(-1)
        return (
            absent if self.oxygen is None else self.oxygen,
            absent if self.helium is None else self.helium,
            absent if self.po2_limit is None else self.po2_limit,
        )


def _mix_key(cylinder: dict[str, Any]) -> _MixKey:
    return _MixKey(
        oxygen=None if cylinder.get("oxygen") is None else _decimal(cylinder["oxygen"]),
        helium=None if cylinder.get("helium") is None else _decimal(cylinder["helium"]),
        po2_limit=None if cylinder.get("po2_limit") is None else _decimal(cylinder["po2_limit"]),
    )


def _gas_name(key: _MixKey) -> str:
    """A name for a blend, `<mix>` requiring one of every gas it defines.

    It says what the fractions say and nothing more: a mix whose oxygen nobody recorded is
    called "unrecorded" rather than "air", which is the same refusal §6.3 makes of a reader
    that fills an absent gas in.
    """
    oxygen, helium = key.oxygen, key.helium
    if oxygen is None and helium is None:
        return "unrecorded"
    if helium:
        return f"Trimix {_num(oxygen or 0)}/{_num(helium)}"
    if oxygen is None:
        return "unrecorded"
    if oxygen == 21:
        return "Air"
    if oxygen == 100:
        return "Oxygen"
    return f"EAN{_num(oxygen)}"


class _Writer:
    """One document being written out, and the report of what UDDF could not hold."""

    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document
        self.notes: list[Note] = []
        # `(mix key, its occurrence within a dive)` to the `xs:ID` of the `<mix>` that pair
        # gets. The occurrence is what keeps two cylinders of one dive off one mix.
        self.mix_ids: dict[tuple[_MixKey, int], str] = {}

    # -- reporting ---------------------------------------------------------------

    def note(self, where: str, message: str, kind: NoteKind) -> None:
        self.notes.append(Note(where, message, kind))

    def notes_of(self, parent: ET.Element, where: str, record: dict[str, Any]) -> None:
        """A record's `notes`, with the empty string reported rather than written."""
        if not _notes(parent, record.get("notes")) and record.get("notes") is not None:
            self.note(
                where,
                "the note is empty, and an empty <para> reads back as no note at all; nothing is written "
                "for it",
                "dropped",
            )

    def unmapped(self, where: str, record: dict[str, Any], carried: frozenset[str]) -> None:
        """Report every member of a record this writer did not put anywhere.

        Derived from the record rather than listed, so a member the format gains reports
        itself instead of being silently dropped by a writer nobody updated. `carried` is
        the set this writer knows how to place — a member in it may still be reported
        separately when the *value* could not be placed.
        """
        for member in sorted(set(record) - carried):
            self.note(
                where,
                f"UDDF has no slot for {member}; it is not written",
                "dropped",
            )

    # -- the run -----------------------------------------------------------------

    def run(self) -> Written:
        # `xmlns` as a plain attribute rather than through `ET.register_namespace`: this
        # tree is serialized and never traversed, and a hand-written declaration puts every
        # element in the default namespace exactly as UDDF's own writers spell it, where
        # `ElementTree`'s qualified names would arrive as `ns0:` prefixes.
        root = ET.Element("uddf", {"xmlns": UDDF_NAMESPACE, "version": UDDF_VERSION})
        root.append(self.generator_element())

        self.plan_mixes()
        self.unmapped(
            "$",
            self.document,
            frozenset(
                {
                    "format",
                    "version",
                    "exported_at",
                    "generator",
                    "extensions",
                    "diver",
                    "dives",
                    "trips",
                    "sites",
                    "gear",
                }
            ),
        )
        if self.document.get("extensions"):
            # Named on its own rather than through `unmapped`, because "no slot" is not
            # quite what happens to it. A converted document keeps the *source* file's
            # generator and declared version under `extensions.divejson` (spec §5.5), and
            # UDDF's slots for those two say what wrote **this** file — which, after this
            # writer has run, is this package. So the block is not carried across, and a
            # round trip comes back describing the rewrite rather than the original import.
            self.note(
                "$",
                "the document's extensions are producer-defined members, and UDDF's <generator> and version "
                "describe the file in front of a reader; they are written as this converter's own",
                "dropped",
            )

        for element in (
            self.diver_element(),
            self.divesite_element(),
            self.divetrip_element(),
            self.gasdefinitions_element(),
            self.profiledata_element(),
        ):
            if element is not None:
                root.append(element)

        ET.indent(root, space=_INDENT)
        body = ET.tostring(root, encoding="unicode")
        return Written(f'<?xml version="1.0" encoding="utf-8"?>\n{body}\n'.encode(), tuple(self.notes))

    def generator_element(self) -> ET.Element:
        """`<generator>` — this package, and when the document it read was exported.

        `<datetime>` is the document's own `exported_at` rather than the clock, which makes
        the whole file a function of its input: two runs over one document produce one set
        of bytes, and a writer pair in a corpus does not churn a line every time it is
        regenerated. It is also why the pair comparison can ignore `<generator>` and lose
        nothing — the only thing in it that moves is this package's version.
        """
        generator = ET.Element("generator")
        _sub(generator, "name", GENERATOR_NAME)
        _sub(generator, "type", GENERATOR_TYPE)
        _sub(generator, "version", __version__)
        exported_at = self.document.get("exported_at")
        if isinstance(exported_at, str):
            _sub(generator, "datetime", exported_at)
        return generator

    # -- diver -------------------------------------------------------------------

    def diver_element(self) -> ET.Element | None:
        """`<diver><owner>`, which is also the only place a logbook's gear can live.

        So the element is written whenever there is either an owner to describe or a piece
        of kit to hang on one, and the two are independent: `<equipment>` sits inside
        `<owner>`, and a logbook with gear and no diver would otherwise lose the whole list
        to a member it has nothing to do with. An owner with nothing recorded about the
        person gets empty names — a valid `xs:string`, which the reader reads back as no
        diver at all rather than as a nameless one — and a note saying so.
        """
        diver = self.document.get("diver") or {}
        name, email = diver.get("name"), diver.get("email")
        gear = self.equipment_element()
        if not (name or email or gear is not None):
            if diver:
                self.note(
                    "diver",
                    "the document records no name and no email for the logbook's owner, and UDDF's <owner> "
                    "carries nothing else about a person; no diver is written",
                    "dropped",
                )
            return None
        if diver:
            self.unmapped("diver", diver, frozenset({"uuid", "name", "email"}))

        element = ET.Element("diver")
        uuid = diver.get("uuid")
        # A plain `owner` where the document names nobody, which is what every UDDF writer
        # in the corpus emits and what the reader is careful never to read as an identity.
        owner = _sub(element, "owner", id=_uddf_id("diver", uuid) if uuid and (name or email) else "owner")
        personal = _sub(owner, "personal")
        first, last = _person_names(str(name or ""))
        _sub(personal, "firstname", first)
        _sub(personal, "lastname", last)
        if email:
            _sub(_sub(owner, "contact"), "email", str(email))
        if gear is not None:
            owner.append(gear)
        return element

    def equipment_element(self) -> ET.Element | None:
        """The whole gear list, grouped into `equipmentType`'s declaration order.

        That order is the schema's and not the document's, so the list comes back from a
        round trip grouped by type. Nothing is lost by it — every piece keeps its uuid — and
        it is not reported, a reordering being a fact about the format rather than a member
        the file could not hold.
        """
        items = self.document.get("gear")
        if not items:
            return None

        by_tag: dict[str, list[tuple[int, dict[str, Any]]]] = {}
        for index, item in enumerate(items):
            where = f"gear/{index}"
            self.unmapped(where, item, frozenset({"uuid", "name", "brand", "type", "notes"}))
            gear_type = item.get("type")
            tag = _EQUIPMENT_ELEMENT.get(gear_type, "variouspieces")
            if not gear_type:
                # Every element in `equipmentType` *is* a type, so there is no way to write
                # a piece without asserting one. The catch-all is the least of them, and it
                # still reads back as `other` where the document said nothing.
                self.note(
                    where,
                    "the piece records no type, and every UDDF equipment element is one; it is written as "
                    "<variouspieces>, which reads back as 'other'",
                    "absent",
                )
            elif gear_type not in _GEAR_ROUND_TRIPS:
                self.note(
                    where,
                    f"UDDF's equipment vocabulary does not name {gear_type!r}; the piece is written as "
                    f"<{tag}>, which reads back as {GEAR_TYPE[tag]!r}",
                    "dropped",
                )
            by_tag.setdefault(tag, []).append((index, item))

        equipment = ET.Element("equipment")
        for tag in _EQUIPMENT_ORDER:
            for index, item in by_tag.get(tag, []):
                uuid = item.get("uuid")
                piece = _sub(equipment, tag, id=_uddf_id("gear", uuid) if uuid else f"gear-{index}")
                _sub(piece, "name", str(item.get("name") or ""))
                if item.get("brand"):
                    # `manufacturerType` extends `namedType`, so its id is mandatory and it
                    # is an inline child rather than a shared definition anything links to.
                    # Derived from the owning piece so that a diver who owns two Apeks
                    # regulators does not emit one `xs:ID` twice, which would make the whole
                    # document invalid.
                    manufacturer = _sub(piece, "manufacturer", id=_uddf_id("mfr", uuid) if uuid else f"mfr-{index}")
                    _sub(manufacturer, "name", str(item["brand"]))
                self.notes_of(piece, where, item)
                if tag == "suit" and item.get("type") in _SUIT_TYPE:
                    # After `<notes>`: `suitType` extends `equipmentPieceType` and its own
                    # sequence follows the base type's whole one.
                    _sub(piece, "suittype", _SUIT_TYPE[item["type"]])
        return equipment

    # -- sites -------------------------------------------------------------------

    def divesite_element(self) -> ET.Element | None:
        sites = self.document.get("sites")
        if not sites:
            return None
        divesite = ET.Element("divesite")
        for index, site in enumerate(sites):
            where = f"sites/{index}"
            self.unmapped(where, site, frozenset({"uuid", "name", "location", "position", "notes"}))
            element = _sub(divesite, "site", id=_uddf_id("site", site["uuid"]))
            _sub(element, "name", str(site.get("name") or ""))
            self.geography(element, where, site.get("location"), site.get("position"), noun="site")
            self.notes_of(element, where, site)
        return divesite

    def geography(
        self,
        parent: ET.Element,
        where: str,
        location: Any,
        position: Any,
        *,
        noun: str,
    ) -> None:
        """`<geography>`, which UDDF will not let carry coordinates without a place name.

        `<location>` is mandatory in `geographyType`, and there is nothing honest to put
        there for a record that has none: copying the record's own **name** in — which is
        what the reference writer does, having one to spare and an app's own export to
        produce — would hand a round trip a location the diver never wrote. So the
        coordinates are dropped and reported, which is the loss this format actually
        imposes.
        """
        if not location:
            if position:
                self.note(
                    where,
                    f"UDDF records a coordinate only inside a <geography>, which must name a place, and the "
                    f"{noun} records none; the position is dropped rather than the name being copied into it",
                    "dropped",
                )
            return
        geography = _sub(parent, "geography")
        _sub(geography, "location", str(location))
        if position:
            _sub(geography, "latitude", _num(position["latitude"]))
            _sub(geography, "longitude", _num(position["longitude"]))

    # -- trips -------------------------------------------------------------------

    def divetrip_element(self) -> ET.Element | None:
        """`<divetrip>`, one `<trippart>` per §6.9 location.

        UDDF models a trip as a sequence of parts, each with its own place, and that is the
        only shape a list of locations fits: the reader takes a trip's span as the span of
        its parts and its locations from their names, so a part per location comes back as
        the list it was written from. A trip with no locations still needs one part —
        `tripType` requires at least one — and it gets a nameless one, an empty `<name>`
        being a valid `xs:string` that reads back as no location rather than as one.
        """
        trips = self.document.get("trips")
        if not trips:
            return None
        divetrip = ET.Element("divetrip")
        for index, trip in enumerate(trips):
            where = f"trips/{index}"
            self.unmapped(
                where, trip, frozenset({"uuid", "name", "locations", "starts_on", "ends_on", "notes"})
            )
            element = _sub(divetrip, "trip", id=_uddf_id("trip", trip["uuid"]))
            _sub(element, "name", str(trip.get("name") or ""))
            locations = trip.get("locations") or [None]
            for part_index, location in enumerate(locations):
                part_where = f"{where}/locations/{part_index}"
                # `trippartType` is an `xs:sequence`: name, dateoftrip, geography, notes.
                part = _sub(element, "trippart")
                if location is None:
                    _sub(part, "name", "")
                else:
                    self.unmapped(part_where, location, frozenset({"name", "display_name", "position"}))
                    _sub(part, "name", str(location.get("name") or ""))
                # The dates and the note belong to the trip and not to any one part, so they
                # go on the first: the reader takes the span of every part's dates and joins
                # every part's notes, both of which return what one part carried.
                if part_index == 0:
                    self.date_of_trip(part, where, trip)
                if location is not None:
                    # `display_name` and nothing else: the reader takes a part's
                    # `<geography><location>` as the display name and only where it differs
                    # from the part's own name, so writing the name here would round-trip as
                    # no display name at all.
                    self.geography(
                        part, part_where, location.get("display_name"), location.get("position"), noun="location"
                    )
                if part_index == 0:
                    self.notes_of(part, where, trip)
        return divetrip

    def date_of_trip(self, part: ET.Element, where: str, trip: dict[str, Any]) -> None:
        """`<dateoftrip>`, whose two attributes are both `use="required"`.

        A trip with no end date has nothing to put in `enddate`, and UDDF has no spelling
        for an open one — so the start date is repeated and the report says what a reader
        will make of it, which is a trip that ended the day it began.
        """
        starts = trip.get("starts_on")
        if not starts:
            return
        ends = trip.get("ends_on")
        if not ends:
            self.note(
                where,
                "the trip records no end date, and UDDF's <dateoftrip> requires one; the start date is "
                "written there, so a reader sees a trip that ended the day it began",
                "absent",
            )
            ends = starts
        # `xs:dateTime` where DiveJSON holds a plain date, so each is widened to midnight —
        # and the reader takes the date back off the front, which is what makes it exact.
        _sub(part, "dateoftrip", startdate=f"{starts}T00:00:00", enddate=f"{ends}T00:00:00")

    # -- gases -------------------------------------------------------------------

    def plan_mixes(self) -> None:
        """Allocate an `xs:ID` to every `<mix>` the document needs, before anything is written.

        Sorted by the fractions rather than by encounter, so an id is a function of the set
        of gases and of nothing else: two writes of one logbook agree, and logging another
        dive on a gas already in the file renumbers nothing. The occurrence in the key is
        what keeps two cylinders of one dive apart — see the module docstring.
        """
        keys: set[tuple[_MixKey, int]] = set()
        for dive in self.document.get("dives") or []:
            seen: dict[_MixKey, int] = {}
            for cylinder in dive.get("cylinders") or []:
                key = _mix_key(cylinder)
                occurrence = seen.get(key, 0)
                seen[key] = occurrence + 1
                keys.add((key, occurrence))
        ordered = sorted(keys, key=lambda pair: (pair[0].sort_key, pair[1]))
        self.mix_ids = {pair: f"mix-{number}" for number, pair in enumerate(ordered, start=1)}

    def gasdefinitions_element(self) -> ET.Element | None:
        if not self.mix_ids:
            return None
        gasdefinitions = ET.Element("gasdefinitions")
        for (key, _), mix_id in self.mix_ids.items():
            mix = _sub(gasdefinitions, "mix", id=mix_id)
            _sub(mix, "name", _gas_name(key))
            # Fractions, not percentages: UDDF's `<o2>` and `<he>` are 0-1, and both are
            # optional, so a fraction the document never recorded is left out rather than
            # written as a zero — which would say "no oxygen here" instead of "no reading".
            if key.oxygen is not None:
                _sub(mix, "o2", _num(key.oxygen / 100))
            if key.helium is not None:
                _sub(mix, "he", _num(key.helium / 100))
            if key.po2_limit is not None:
                # Bar in both formats — the one pressure UDDF does *not* express in Pascal.
                _sub(mix, "maximumpo2", _num(key.po2_limit))
        return gasdefinitions

    # -- dives -------------------------------------------------------------------

    def profiledata_element(self) -> ET.Element | None:
        """`<profiledata>`, or nothing at all for a logbook with no dives.

        One `<repetitiongroup>` holding every dive: a group is a surface interval's worth of
        dives and DiveJSON records no such grouping, so inventing several would be asserting
        surface intervals nobody logged. `profiledata` requires at least one group and a
        group at least one dive, which is why an empty logbook omits the section rather than
        emitting a hollow one that would not validate.
        """
        dives = self.document.get("dives")
        if not dives:
            return None
        profiledata = ET.Element("profiledata")
        group = _sub(profiledata, "repetitiongroup", id="rg-1")
        for index, dive in enumerate(dives):
            group.append(self.dive_element(dive, index))
        return profiledata

    def dive_element(self, dive: dict[str, Any], index: int) -> ET.Element:
        where = f"dives/{index}"
        self.unmapped(
            where,
            dive,
            frozenset(
                {
                    "uuid",
                    "dive_number",
                    "started_at",
                    "duration",
                    "notes",
                    "max_depth",
                    "avg_depth",
                    "bottom_temperature",
                    "visibility",
                    "weight",
                    "altitude",
                    "surface_pressure",
                    "trip_uuid",
                    "site_uuids",
                    "gear_uuids",
                    "cylinders",
                    "profile",
                }
            ),
        )
        element = ET.Element("dive", {"id": _uddf_id("dive", dive["uuid"])})

        # `informationbeforediveType` is an `xs:sequence`: link, divenumber, datetime,
        # altitude, equipmentused, tripmembership, surfacepressure, in exactly this order.
        before = _sub(element, "informationbeforedive")
        for site_uuid in dive.get("site_uuids") or []:
            _sub(before, "link", ref=_uddf_id("site", site_uuid))
        number = dive.get("dive_number")
        if number is not None:
            if number > 0:
                _sub(before, "divenumber", str(number))
            else:
                # `xs:positiveInteger`. A zero would make the whole document invalid rather
                # than one element wrong, and §6.2 leaves `dive_number` unbounded below.
                self.note(
                    where,
                    f"the dive is numbered {number}, and UDDF's <divenumber> is a positive integer; the "
                    "number is not written",
                    "dropped",
                )
        _sub(before, "datetime", str(dive["started_at"]))
        _optional(before, "altitude", dive.get("altitude"))
        weight, gear_uuids = dive.get("weight"), dive.get("gear_uuids") or []
        if weight is not None or gear_uuids:
            used = _sub(before, "equipmentused")
            _optional(used, "leadquantity", weight)
            for gear_uuid in gear_uuids:
                _sub(used, "link", ref=_uddf_id("gear", gear_uuid))
        if dive.get("trip_uuid"):
            _sub(before, "tripmembership", ref=_uddf_id("trip", dive["trip_uuid"]))
        if dive.get("surface_pressure") is not None:
            _sub(before, "surfacepressure", _num(_decimal(dive["surface_pressure"]) * PASCAL_PER_BAR))

        mix_by_gas_number, declared = self.tankdata_elements(element, dive, where)
        numbered = self.samples_element(element, dive, where, mix_by_gas_number)
        self.check_numbering(where, declared, numbered=numbered)

        # `informationafterdiveType` is an `xs:all`, so this order is a reader's convenience
        # rather than a requirement.
        after = _sub(element, "informationafterdive")
        if dive.get("bottom_temperature") is not None:
            _sub(after, "lowesttemperature", _num(_decimal(dive["bottom_temperature"]) + KELVIN_OFFSET))
        depth = dive.get("max_depth")
        if depth is None:
            # `<greatestdepth>` is mandatory where `max_depth` is optional, and zero is the
            # spelling every UDDF writer is forced into for a depth it does not have — which
            # this format's own reader reads back as not recorded, so the round trip holds.
            self.note(
                where,
                "the dive records no maximum depth, and UDDF's <greatestdepth> is mandatory; 0 is written, "
                "which readers of this format take as not recorded",
                "absent",
            )
        _sub(after, "greatestdepth", _num(depth if depth is not None else 0))
        _optional(after, "visibility", dive.get("visibility"))
        self.notes_of(after, where, dive)
        duration = dive.get("duration")
        if duration is None:
            # `<diveduration>` is mandatory too, and takes the same zero for the same
            # reason: §6.2 records a duration only when it is positive, so a reader takes
            # this one back as not recorded rather than as a dive of no length.
            self.note(
                where,
                "the dive records no duration, and UDDF's <diveduration> is mandatory; 0 is written, which "
                "readers of this format take as not recorded",
                "absent",
            )
        _sub(after, "diveduration", _num(duration if duration is not None else 0))
        _optional(after, "averagedepth", dive.get("avg_depth"))
        return element

    def tankdata_elements(
        self, element: ET.Element, dive: dict[str, Any], where: str
    ) -> tuple[dict[int, str], list[int | None]]:
        """A `<tankdata>` per cylinder, its mix id for the profile, and the numbers declared.

        UDDF records no cylinder numbering at all, so the second return value is what
        `check_numbering` needs: the `gas_number` each cylinder carried on the way in,
        against which what a reader recovers can be compared once the profile is written.
        """
        cylinders = dive.get("cylinders") or []
        seen: dict[_MixKey, int] = {}
        mix_by_gas_number: dict[int, str] = {}
        for index, cylinder in enumerate(cylinders):
            cylinder_where = f"{where}/cylinders/{index}"
            self.unmapped(
                cylinder_where,
                cylinder,
                frozenset({"volume", "start_pressure", "end_pressure", "oxygen", "helium", "po2_limit", "gas_number"}),
            )
            key = _mix_key(cylinder)
            occurrence = seen.get(key, 0)
            seen[key] = occurrence + 1
            mix_id = self.mix_ids[(key, occurrence)]
            # Keyed on the cylinder's own `gas_number` where it has one, because that is
            # what §6.5's channels and §6.6's switches address — a label the document chose,
            # not a position. Where it has none, the position is the number, which is the
            # numbering `converting.md` gives a converted dive.
            number = cylinder.get("gas_number")
            mix_by_gas_number.setdefault(index if number is None else number, mix_id)

            # `tankdataType` is an `xs:sequence`: link, tankvolume, tankpressurebegin,
            # tankpressureend.
            tank = _sub(element, "tankdata")
            _sub(tank, "link", ref=mix_id)
            if cylinder.get("volume") is not None:
                _sub(tank, "tankvolume", _num(_decimal(cylinder["volume"]) / LITRES_PER_CUBIC_METRE))
            start = cylinder.get("start_pressure")
            if start is None:
                # `<tankpressurebegin>` is mandatory, and 0 is the absent-marker §6.3 names
                # and the reader reads back as not recorded — so the cylinder keeps its gas
                # and its size instead of being dropped for want of a pressure.
                self.note(
                    cylinder_where,
                    "the cylinder records no start pressure, and UDDF's <tankpressurebegin> is mandatory; 0 is "
                    "written, which readers of this format take as not recorded (spec §6.3)",
                    "absent",
                )
            _sub(tank, "tankpressurebegin", _num(_decimal(start) * PASCAL_PER_BAR if start is not None else 0))
            if cylinder.get("end_pressure") is not None:
                _sub(tank, "tankpressureend", _num(_decimal(cylinder["end_pressure"]) * PASCAL_PER_BAR))

        return mix_by_gas_number, [cylinder.get("gas_number") for cylinder in cylinders]

    def check_numbering(self, where: str, declared: list[int | None], *, numbered: bool) -> None:
        """Report the dive's cylinder numbering where UDDF will not give it back.

        §6.3 calls `gas_number` a **label**, and UDDF carries no numbering at all: a reader
        recovers one by counting `<tankdata>` elements in file order, and only where the
        profile needs one — a pressure channel, or a gas switch naming the cylinder it
        switched to. So the labels survive in exactly one case, which is the one this
        converter's own reader produces: numbered from 0 by position, on a dive whose
        profile asks for a numbering. Anything else is reported here rather than in the
        file, there being nowhere in the file to put it.
        """
        recovered: list[int | None] = list(range(len(declared))) if numbered else [None] * len(declared)
        if declared != recovered and any(number is not None for number in declared):
            self.note(
                where,
                "UDDF records no cylinder numbering, so a reader recovers one by counting this dive's "
                "<tankdata> elements, and only where its profile needs one; the gas numbers the document "
                "carries are not preserved (spec §6.3)",
                "dropped",
            )

    # -- profile -----------------------------------------------------------------

    def samples_element(
        self, element: ET.Element, dive: dict[str, Any], where: str, mix_by_gas_number: dict[int, str]
    ) -> bool:
        """`<samples>`, and whether what was written asks a reader for a gas numbering.

        One waypoint per second any channel or event landed on — the union rather than the
        depth channel's axis, see the module docstring — so a temperature taken between two
        depth samples becomes its own waypoint, carrying a `<divetime>` and a
        `<temperature>` and no depth.

        The return value is what `check_numbering` needs: a `<tankpressure ref>` or a
        `<switchmix ref>` is the only thing that makes a reader number the cylinders at all.
        """
        profile = dive.get("profile")
        if not profile:
            return False
        profile_where = f"{where}/profile"
        self.unmapped(
            profile_where, profile, frozenset({"duration", "depth", "temperature", "pressures", "events"})
        )

        readings: _Readings = {}

        def at(second: int) -> dict[str, Any]:
            return readings.setdefault(second, {"pressures": []})

        for second, centimetres in _series(profile.get("depth")):
            at(second)["depth"] = _decimal(centimetres) / CENTIMETRES_PER_METRE
        for second, tenths in _series(profile.get("temperature")):
            at(second)["temperature"] = _decimal(tenths) / TENTHS_PER_UNIT + KELVIN_OFFSET

        for channel in profile.get("pressures") or []:
            mix_id = mix_by_gas_number.get(channel["gas_number"])
            if mix_id is None:
                # `<tankpressure ref>` is an `xs:IDREF`, and without a cylinder on this dive
                # to have written a `<mix>` for, there is nothing valid to point it at.
                self.note(
                    profile_where,
                    f"the profile carries a pressure channel for gas number {channel['gas_number']}, which no "
                    "cylinder of this dive is numbered; the channel is dropped",
                    "dropped",
                )
                continue
            for second, tenths in _series(channel):
                at(second)["pressures"].append((mix_id, _decimal(tenths) / TENTHS_PER_UNIT * PASCAL_PER_BAR))

        self.events(profile.get("events") or [], profile_where, mix_by_gas_number, readings)

        if not readings:
            return False
        samples = _sub(element, "samples")
        for second in sorted(readings):
            reading = readings[second]
            # `waypointType` is an `xs:sequence`, so these go in exactly this order.
            waypoint = _sub(samples, "waypoint")
            if "depth" in reading:
                _sub(waypoint, "depth", _num(reading["depth"]))
            _sub(waypoint, "divetime", _num(second))
            if "setmarker" in reading:
                _sub(waypoint, "setmarker", reading["setmarker"])
            if "switchmix" in reading:
                _sub(waypoint, "switchmix", ref=reading["switchmix"])
            for mix_id, pascal in reading["pressures"]:
                _sub(waypoint, "tankpressure", _num(pascal), ref=mix_id)
            if "temperature" in reading:
                _sub(waypoint, "temperature", _num(reading["temperature"]))

        span = max(
            (
                channel["times"][-1]
                for channel in (profile.get("depth"), profile.get("temperature"), *(profile.get("pressures") or []))
                if channel and channel["times"]
            ),
            default=0,
        )
        if profile.get("duration") != span:
            # §6.4 defines `profile.duration` as the span of the samples, and a reader takes
            # it off them — so a document whose recorded duration is not that span comes
            # back with the span instead. Nothing is written anywhere to say otherwise:
            # UDDF has no profile-level duration at all.
            self.note(
                profile_where,
                f"the profile's duration is {profile.get('duration')} s where its samples span {span} s, and "
                "UDDF records no duration for a profile; a reader takes the span (spec §6.4)",
                "dropped",
            )
        return any(reading["pressures"] or "switchmix" in reading for reading in readings.values())

    def events(
        self,
        events: list[dict[str, Any]],
        where: str,
        mix_by_gas_number: dict[int, str],
        readings: _Readings,
    ) -> None:
        """§6.6's events onto waypoints, one `<setmarker>` and one `<switchmix>` each.

        Both are `maxOccurs="1"` on `waypointType`, so a second of either on one waypoint
        has nowhere to go and is dropped rather than joined into the first — the reference
        writer joins simultaneous markers with a separator, which is right for a file being
        read by a human and wrong for one being read back, where the join would return as a
        single `other` event whose label is two labels.
        """

        def at(second: int) -> dict[str, Any]:
            return readings.setdefault(second, {"pressures": []})

        for index, event in enumerate(events):
            event_where = f"{where}/events/{index}"
            self.unmapped(event_where, event, frozenset({"time", "type", "gas_number", "label"}))
            second = event["time"]
            kind = event["type"]
            if kind == "gas_switch":
                # A switch the document recorded without saying what to, and one naming a
                # cylinder this dive has none of, are the same answer here: `<switchmix>`
                # has an `xs:IDREF` and nothing to put in it.
                number = event.get("gas_number")
                mix_id = None if number is None else mix_by_gas_number.get(number)
                if mix_id is None:
                    self.note(
                        event_where,
                        "UDDF's <switchmix> must name the gas switched to, and the event names none this dive "
                        "has a cylinder for; the switch is dropped",
                        "dropped",
                    )
                    continue
                if "switchmix" in readings.get(second, {}):
                    self.note(
                        event_where,
                        f"a gas switch at {second} s shares its second with an earlier one, and a waypoint "
                        "carries one <switchmix>; the later switch is dropped",
                        "dropped",
                    )
                    continue
                at(second)["switchmix"] = mix_id
                continue

            marker = kind if kind in _MARKER_TYPES else event.get("label")
            if kind in _MARKER_TYPES and event.get("label"):
                # `<setmarker>` is one string, and the type has to have it: the three named
                # types are the only thing this format's own round trip has to go on, so a
                # labelled safety stop keeps its type and loses its label rather than
                # arriving as an `other` that nothing recognises.
                self.note(
                    event_where,
                    f"UDDF's <setmarker> carries one string, and the event is a {kind} with a label; the type "
                    "is written and the label is dropped",
                    "dropped",
                )
            if not marker:
                # `<setmarker>` is a bare string with no type beside it, so an unlabelled
                # `other` has nothing to carry: writing the word "other" would come back as
                # an event labelled "other", which is a label the document did not have.
                self.note(
                    event_where,
                    "UDDF's <setmarker> carries only text, and the event is an unlabelled 'other'; it is dropped "
                    "rather than written as the word",
                    "dropped",
                )
                continue
            if "setmarker" in readings.get(second, {}):
                self.note(
                    event_where,
                    f"an event at {second} s shares its second with an earlier one, and a waypoint carries one "
                    "<setmarker>; the later event is dropped",
                    "dropped",
                )
                continue
            at(second)["setmarker"] = str(marker)


def _series(channel: Any) -> list[tuple[int, int]]:
    """A §6.5 channel as `(second, value)` pairs, in the order it stores them.

    `strict`, because §6.5 makes the two arrays the same length and the validator enforces
    it: a mismatch here is a caller handing this writer a document it never validated, and
    a loud `ValueError` is a better answer than a channel silently truncated to the shorter
    of the two.
    """
    if not channel:
        return []
    return list(zip(channel["times"], channel["values"], strict=True))


def write_uddf(document: dict[str, Any]) -> Written:
    """Write one DiveJSON document as a UDDF 3.2.2 document.

    `document` is expected to conform — this is the writing half of a conversion, not a
    validator, and a member out of range is written out as it stands. `validate_document`
    is what answers that question, and the command line asks it before calling this.

    The bytes are a function of the document alone: no clock is read, so two writes of one
    document are one file.
    """
    return _Writer(document).run()


def compared(data: bytes) -> str:
    """A written document reduced to what a writer-pair comparison is about.

    Canonical XML — `ET.canonicalize`, which fixes attribute order, namespace prefixes and
    whitespace — with `<generator>` removed, that being the one element whose contents move
    without the mapping moving: it carries this package's version. The rule is here rather
    than in the conformance runner because a port checking its own writer against the same
    corpus needs exactly this comparison, which is the same reason `conform.compared` lives
    in the package.
    """
    root = parse_xml(data, root=FORMAT, malformed=MalformedUddfError)
    for child in [element for element in root if local_name(element) == "generator"]:
        root.remove(child)
    return ET.canonicalize(ET.tostring(root, encoding="unicode"), strip_text=True)


class UddfWriter:
    """The registry's view of this writer: what it produces, and how two are compared."""

    format: str = FORMAT
    suffix: str = SUFFIX

    def write(self, document: dict[str, Any]) -> Written:
        return write_uddf(document)

    def compared(self, data: bytes) -> str:
        return compared(data)


UDDF_WRITER = UddfWriter()
