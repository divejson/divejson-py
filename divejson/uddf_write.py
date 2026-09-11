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
carries, with one documented exception below. Everything else is named in the report, and
the report is half the output here exactly as it is on the way in.

The round trip is also **the only thing that checks a scale both directions agree on**.
`divejson conform` compares a written file with a committed one and never reads it back, so
a writer and a reader that disagreed about whether `<gradientfactor>` is percent or a
fraction would produce two green corpora and a value a hundred times wrong. Every member
scaled below owes that test in this suite, and `test_uddf_writing.py` is where they are.

Five things shape the module.

**One `<divecomputer>` per computer.** UDDF's element is a piece of kit *and* the hardware
that recorded a dive, where DiveJSON keeps a §6.12 gear item and a §6.4b device apart — so
this direction has to put both into one element wherever it can, and `plan_computers` is
the fold that decides where it can. Writing the same computer twice would put two kit items
in a reader's gear list where the diver owns one and give one machine two `xs:ID`s. A
device that folds into nothing takes an element of its own with a non-UUID id, which reads
back as a gear item the document never had: the one documented exception to the self round
trip, and the one place a written file returns *more* than it was written from.

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
from dataclasses import dataclass, field
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
# these three back as a type rather than as an unclassified event labelled with the text.
_MARKER_TYPES = frozenset({"deep_stop", "safety_stop", "bookmark"})

# §6.4a's `mode` in UDDF's spelling, written as `<divemode type>` on the first waypoint.
# **`apnoe` rather than `apnea`** for a freedive: `divemodeType` spells it twice, and this is
# the older of the two, which every 3.2.x reader knows — while `uddf.py` reads both. **A
# `gauge` recording has no row**: `divemodeType`'s five values do not include one, and
# writing the nearest would be the guess §5.4 forbids, so it is reported instead.
_DIVE_MODES = {
    "open_circuit": "opencircuit",
    "closed_circuit": "closedcircuit",
    "semi_closed": "semiclosedcircuit",
    "freedive": "apnoe",
}

# The scales §6.4 fixes for the three channels this writer converts back out of, as the
# divisors that undo them: ppO₂ from hundredths of a bar to the bar current writers emit,
# CNS from tenths of a percent to percent, and a gradient factor from whole percent to the
# fraction the documentation's examples show. `ndl` needs none — §6.4 and UDDF both count it
# in seconds.
HUNDREDTHS_PER_UNIT = Decimal(100)
PERCENT_PER_FRACTION = Decimal(100)

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


def _folded(value: Any) -> str | None:
    """A source string reduced to what the fold compares: trimmed and case-folded.

    §6.4b asks readers to case-fold and a writer to trim, and the comparison rules
    elsewhere say *trimmed and case-folded* rather than assuming either was done upstream —
    so this is applied at the comparison rather than to the values that go in the file.
    """
    if not isinstance(value, str):
        return None
    return value.strip().casefold() or None


def _agree(one: Any, other: Any) -> bool:
    """Whether two brands do not disagree — an absent one disagreeing with nothing.

    **Deliberately not the symmetric absent rule the neighbouring comparison uses**, and
    `docs/uddf-writing.md` says so in as many words under *Devices, and the one element
    they share with gear*. Asking whether two *files* are records of one computer, an
    absent member means "this format has no such field"; the fold is not that comparison,
    one side being a user's own record whose `name` is REQUIRED (§6.12) and the other a
    file reading routinely one member wide. The asymmetry lives in the caller's `label`
    leg, which refuses an absent label outright; the brands are the one place where
    absence really is silence.
    """
    left, right = _folded(one), _folded(other)
    return left is None or right is None or left == right


@dataclass(slots=True)
class _DeviceRecord:
    """One computer, and every recording of the document that is its record of a dive.

    The devices fold with each other **before** any gear item is considered, which is the
    ordering `docs/uddf-writing.md` states and the silence that used to be load-bearing:
    read gear-first, one computer recording ten dives offers a gear item ten candidates and
    the cardinality rule reads nine of them as ties, contradicting "one computer recording
    ten dives is ten recordings and one element".

    `merged` is what the gear legs test, and it fills member by member in document order —
    the first device to carry a member supplies it. The fold has already made the two agree
    wherever both carry a serial or a label, so what merging really buys is an element that
    keeps a serial one recording's device carried and another's did not.
    """

    merged: dict[str, Any]
    # `(dive index, recording index)` for every recording this computer recorded, in
    # document order. The link leg is asked per recording, so the record has to keep them
    # rather than a count.
    recordings: list[tuple[int, int]]
    # The gear item this record folded into, once `plan_computers` has decided.
    gear_index: int | None = None
    # The `<divecomputer id="device-<n>">` this record's *unfolded* recordings share, and
    # the recordings that use it. Both stay unset where every recording folded.
    element_id: str | None = None
    unfolded: list[tuple[int, int]] = field(default_factory=list)

    @property
    def label(self) -> str | None:
        """What the fold compares a name against: `name` else `model` (`docs/uddf-writing.md`).

        Absent means no fold, whatever else matches — a device carrying only a brand
        matches nothing, and folding it into "any Suunto computer the diver owns" is a
        guess.
        """
        return _folded(self.merged.get("name")) or _folded(self.merged.get("model"))

    def absorbs(self, other: dict[str, Any]) -> bool:
        """Whether one more device is this same computer.

        The same predicate the gear legs use, with `label` being `name` else `model` on
        both sides — which is where the serial leg does its real work: one computer
        recording ten dives is ten recordings and one device record.
        """
        mine, theirs = _folded(self.merged.get("serial")), _folded(other.get("serial"))
        if mine and theirs:
            return mine == theirs
        label = _folded(other.get("name")) or _folded(other.get("model"))
        return bool(self.label) and self.label == label and _agree(
            self.merged.get("brand"), other.get("brand")
        )

    def absorb(self, other: dict[str, Any], at: tuple[int, int]) -> None:
        for member, value in other.items():
            self.merged.setdefault(member, value)
        self.recordings.append(at)

    def matches(self, item: dict[str, Any]) -> bool:
        """Whether a `computer` gear item and this record are one machine.

        The legs are `docs/uddf-writing.md`'s, minus the link — which is asked per
        recording by the caller, because a folded element reaches a dive only through the
        `<equipmentused><link>` that dive's own `gear_uuids` produced.

        **Serials that differ mean different computers, and there is no fall-through to the
        label.** Without that leg two Suunto Oceans, each plausibly named `Suunto Ocean`
        with brand `Suunto`, fold on the label and one machine's serial goes out on the
        other's element.
        """
        mine, theirs = _folded(self.merged.get("serial")), _folded(item.get("serial"))
        if mine and theirs:
            return mine == theirs
        return (
            self.label is not None
            and _folded(item.get("name")) == self.label
            and _agree(item.get("brand"), self.merged.get("brand"))
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
        # The document's computers, one record each, filled by `plan_computers` before the
        # equipment loop runs — a folded element is built from a gear item *and* a device,
        # so neither half can be written until the fold has been decided.
        self.computers: list[_DeviceRecord] = []
        # `(dive index, recording index)` to the `xs:ID` of the element that recording's
        # device took, for the dives that have to link it.
        self.device_links: dict[tuple[int, int], str] = {}

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
        self.plan_computers()
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

    # -- computers ---------------------------------------------------------------

    def plan_computers(self) -> None:
        """Decide, before anything is written, which `<divecomputer>` each computer gets.

        **One element per computer**, not one per gear item plus one per recording: writing
        the same computer twice would put two kit items in a reader's gear list where the
        diver owns one, and give one machine two `xs:ID`s. Three passes, in this order and
        for `docs/uddf-writing.md`'s reasons.

        1. **The devices fold with each other**, so that one computer's every recording is
           a single record. Greedy in document order and against the *merged* record rather
           than pairwise, which is what the gear legs are stated against and what keeps the
           answer independent of which member of a record a later device is compared to.
        2. **Each record takes at most one gear item, and each gear item at most one
           record.** A tie is two different computers claiming one kit item; the first in
           document order wins it and the rest are reported, because a fold is not a merge
           and silently picking one of three would put a serial on an element the diver
           never meant.
        3. **The link leg, asked per recording.** A folded element reaches a dive only
           through the `<equipmentused><link>` that dive's own `gear_uuids` produced, so a
           recording whose dive does not list the gear item does not fold and its device
           takes an element of its own — which needs no gear link, so both facts survive:
           which computer recorded the dive, and which gear the diver recorded using. That
           costs a computer two elements where some dives link the item and others do not,
           and where **no** dive links any gear at all, which is an ordinary logbook shape.
           A duplicate in a kit list is visible to the diver and correctable in a moment; a
           device that never arrived is neither.
        """
        for dive_index, dive in enumerate(self.document.get("dives") or []):
            for rec_index, entry in enumerate(dive.get("recordings") or []):
                found = entry.get("device")
                if not found:
                    continue
                at = (dive_index, rec_index)
                for record in self.computers:
                    if record.absorbs(found):
                        record.absorb(found, at)
                        break
                else:
                    self.computers.append(_DeviceRecord(merged=dict(found), recordings=[at]))

        items = self.document.get("gear") or []
        claimed: set[int] = set()
        for record in self.computers:
            candidates = [
                gear_index
                for gear_index, item in enumerate(items)
                if item.get("type") == "computer" and record.matches(item)
            ]
            free = [gear_index for gear_index in candidates if gear_index not in claimed]
            if free:
                record.gear_index = free[0]
                claimed.add(free[0])
            # One candidate this record took is the ordinary case and says nothing.
            # Anything else is a tie, and there are two shapes of one: a kit item an
            # earlier computer already holds, and several kit items this one computer
            # answers to at once. They lose different things, so they say different things.
            if record.gear_index is None and candidates:
                self.note(
                    self.device_where(record),
                    "the kit item this computer answers to is already another computer's, and a "
                    "<divecomputer> carries one of each; the first in document order keeps it and this "
                    "one takes an element of its own",
                    "dropped",
                )
            elif len(candidates) > 1:
                self.note(
                    self.device_where(record),
                    "this computer answers to more than one item in the kit list, and a <divecomputer> "
                    "carries one of each; the first in document order takes it and the rest are written "
                    "as the kit entries they are, with nothing about the hardware on them",
                    "dropped",
                )

        # The `device-<n>` numbering runs over the document's recordings in order, so it is
        # assigned here rather than inside the equipment loop, which walks the schema's
        # order instead. Deliberately **not** a uuid: an id built from a dive's uuid would
        # read back as a gear item wearing that dive's identity, which §5.3 forbids.
        unfolded = 0
        for record in self.computers:
            linked = self.folded_recordings(record)
            record.unfolded = [at for at in record.recordings if at not in linked]
            if not record.unfolded:
                continue
            record.element_id = f"device-{unfolded}"
            unfolded += 1
            for at in record.unfolded:
                self.device_links[at] = record.element_id

    def folded_recordings(self, record: _DeviceRecord) -> set[tuple[int, int]]:
        """The recordings of one record whose dive links its gear item, so they fold."""
        if record.gear_index is None:
            return set()
        uuid = (self.document.get("gear") or [])[record.gear_index].get("uuid")
        dives = self.document.get("dives") or []
        return {
            at
            for at in record.recordings
            if uuid is not None and uuid in (dives[at[0]].get("gear_uuids") or [])
        }

    def device_where(self, record: _DeviceRecord) -> str:
        """A path into the document for a finding about a whole computer.

        Its first recording's, that being the one place in the document the record can be
        pointed at: `writing.md` makes a `where` a path into the document being written,
        and a device record is a thing this writer computed rather than a record the
        document holds.
        """
        dive_index, rec_index = record.recordings[0]
        return f"dives/{dive_index}/recordings/{rec_index}/device"

    def computer_element(
        self,
        equipment: ET.Element,
        record: _DeviceRecord,
        item: dict[str, Any] | None = None,
        index: int = 0,
    ) -> None:
        """One `<divecomputer>`, built from a gear item and a device or from one of them.

        `equipmentPieceType` is an `xs:sequence` — `<name>`, `<manufacturer>`, `<model>`,
        `<serialnumber>`, `<notes>` — and not a free order, so this is the only order the
        children may go in.

        **The `<name>` row stops at the device's `name` and does not fall through to its
        `model`**, which is what makes an unfolded element round-trip. `<name>` is mandatory
        on the element, so a device with no name of its own and no gear item beside it to
        supply one gets an **empty** one: a valid `xs:string` the reading direction takes as
        no name at all, so it comes back as no `device.name` and, §6.12 making a gear item's
        name REQUIRED, as no gear item either. Writing the model there instead would hand a
        reader back two things the document never had.
        """
        item = item or {}
        uuid = item.get("uuid")
        if item:
            element_id = _uddf_id("gear", uuid) if uuid else f"gear-{index}"
            manufacturer_id = _uddf_id("mfr", uuid) if uuid else f"mfr-{index}"
        else:
            element_id = record.element_id or f"device-{index}"
            manufacturer_id = f"mfr-{element_id}"
        piece = _sub(equipment, "divecomputer", id=element_id)

        placed: set[str] = set()
        name = item.get("name") or record.merged.get("name")
        if name == record.merged.get("name"):
            placed.add("name")
        _sub(piece, "name", str(name or ""))
        brand = item.get("brand") or record.merged.get("brand")
        if brand:
            manufacturer = _sub(piece, "manufacturer", id=manufacturer_id)
            _sub(manufacturer, "name", str(brand))
        if brand == record.merged.get("brand"):
            placed.add("brand")
        if record.merged.get("model"):
            _sub(piece, "model", str(record.merged["model"]))
            placed.add("model")
        serial = record.merged.get("serial") or item.get("serial")
        if serial:
            # Where both carry one the fold has already made them equal, so which side it
            # is taken from cannot change the file.
            _sub(piece, "serialnumber", str(serial))
        if serial == record.merged.get("serial"):
            placed.add("serial")
        if item:
            self.notes_of(piece, f"gear/{index}", item)

        if not record.merged:
            return
        # `<internaldivenumber>` is the dive's child rather than the element's, so it is
        # written by `dive_element` and a *value* it could not place is reported there —
        # a counter of 0, which `xs:positiveInteger` refuses, and a counter riding on a
        # recording UDDF had to drop. The member has a slot either way, which is what this
        # set says.
        placed.add("dive_number")
        # Reported from the record rather than from a list, which is `writing.md`'s rule:
        # `firmware` falls out of this without being named, and so does a name or a brand
        # the gear item's own overrode — a real loss the round trip would otherwise show
        # without the report having said so.
        self.unmapped(self.device_where(record), record.merged, frozenset(placed))

    def equipment_element(self) -> ET.Element | None:
        """The whole gear list, grouped into `equipmentType`'s declaration order.

        That order is the schema's and not the document's, so the list comes back from a
        round trip grouped by type. Nothing is lost by it — every piece keeps its uuid — and
        it is not reported, a reordering being a fact about the format rather than a member
        the file could not hold.
        """
        items = self.document.get("gear") or []
        loose = [record for record in self.computers if record.element_id is not None]
        if not (items or loose):
            return None
        folded = {
            record.gear_index: record
            for record in self.computers
            if record.gear_index is not None and len(record.unfolded) < len(record.recordings)
        }

        by_tag: dict[str, list[tuple[int, dict[str, Any]]]] = {}
        for index, item in enumerate(items):
            where = f"gear/{index}"
            self.unmapped(where, item, frozenset({"uuid", "name", "brand", "serial", "type", "notes"}))
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
                if tag == "divecomputer":
                    # A `computer` gear item and the device that folded into it are one
                    # element, built from both records — which is why `plan_computers` runs
                    # before this loop. A gear item nothing folded into is written the same
                    # way, from an empty record.
                    self.computer_element(
                        equipment, folded.get(index) or _DeviceRecord(merged={}, recordings=[]), item, index
                    )
                    continue
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
                if item.get("serial"):
                    # `<serialnumber>` is on `equipmentPieceType`, which is the same breadth
                    # §6.12 gives the member: a serialled regulator keeps its serial through
                    # a round trip like any other piece. On a computer it does more, and
                    # `computer_element` is where.
                    _sub(piece, "serialnumber", str(item["serial"]))
                # Recomputed rather than carried down from the pass above: this loop walks
                # the pieces in the schema's order, not the document's, so the two indices
                # are different numbers and a path taken from the wrong loop names whichever
                # piece the first one happened to end on.
                self.notes_of(piece, f"gear/{index}", item)
                if tag == "suit" and item.get("type") in _SUIT_TYPE:
                    # After `<notes>`: `suitType` extends `equipmentPieceType` and its own
                    # sequence follows the base type's whole one.
                    _sub(piece, "suittype", _SUIT_TYPE[item["type"]])
            if tag == "divecomputer":
                # After the kit list's own computers and inside their group, `equipmentType`
                # being an `xs:sequence`: a device that folded into no gear item, or into one
                # its dive does not link, still gets an element — the device is why §6.4b
                # exists, and a logbook whose owner never listed the computer in their kit is
                # the ordinary case rather than the exotic one.
                for record in loose:
                    self.computer_element(equipment, record)
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
                    "recordings",
                }
            ),
        )
        recordings = dive.get("recordings") or []
        element = ET.Element("dive", {"id": _uddf_id("dive", dive["uuid"])})

        # `informationbeforediveType` is an `xs:sequence`, and this is the whole of what
        # this writer puts in it, in the schema's order: link, divenumber,
        # internaldivenumber, datetime, altitude, equipmentused, tripmembership,
        # surfacepressure.
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
        self.internal_dive_number(before, recordings, where)
        _sub(before, "datetime", str(dive["started_at"]))
        _optional(before, "altitude", dive.get("altitude"))
        weight, gear_uuids = dive.get("weight"), dive.get("gear_uuids") or []
        devices = [
            self.device_links[(index, rec_index)]
            for rec_index in range(len(recordings))
            if (index, rec_index) in self.device_links
        ]
        if weight is not None or gear_uuids or devices:
            used = _sub(before, "equipmentused")
            _optional(used, "leadquantity", weight)
            for gear_uuid in gear_uuids:
                _sub(used, "link", ref=_uddf_id("gear", gear_uuid))
            # After the links `gear_uuids` produced. `<equipmentused>` is what the diver
            # wore, so nothing is added to it for a *folded* computer — that element is the
            # gear item's and the gear item's own link already reaches it. These are the
            # `device-<n>` elements, which no gear link reaches.
            for element_id in dict.fromkeys(devices):
                _sub(used, "link", ref=element_id)
        self.check_link_order(index, recordings, gear_uuids, devices, where)
        if dive.get("trip_uuid"):
            _sub(before, "tripmembership", ref=_uddf_id("trip", dive["trip_uuid"]))
        if dive.get("surface_pressure") is not None:
            _sub(before, "surfacepressure", _num(_decimal(dive["surface_pressure"]) * PASCAL_PER_BAR))

        mix_by_gas_number, declared = self.tankdata_elements(element, dive, where)
        numbered = self.recording_elements(element, recordings, where, mix_by_gas_number)
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

    def check_link_order(
        self,
        index: int,
        recordings: list[dict[str, Any]],
        gear_uuids: list[str],
        devices: list[str],
        where: str,
    ) -> None:
        """Report a dive whose computers do not come back in the order they went out.

        UDDF has no per-recording anything: a dive gets one `<datetime>`, one `<samples>`
        and one `<internaldivenumber>`, and a reader recovers a dive's recordings from its
        `<equipmentused>` links, giving the two dive-level facts to the **first** linked
        `<divecomputer>` because there is nothing else in the file to give them to
        (`docs/uddf-mapping.md` says so, and says a reader must not read primacy into that
        order). The links themselves come from the kit list in the diver's own order, with
        an element appended for every device that folded into nothing — so the sequence is
        a fact about the gear list rather than about the recordings, and the two agree only
        by construction.

        Where they do not, something real is lost and `writing.md`'s rule is that it is
        named: a `computer` gear item the dive links but no recording's device matches
        takes the first link, so **the profile and the counter come back on that computer**
        and not on the one that recorded the dive; and where the links merely run through
        this dive's own computers in another order, the recordings come back reordered,
        which §6.4a makes a fact about the document — a recording has no uuid, so its
        position is the only thing that says it is the primary. Reordering the links
        instead is not open: `<equipmentused>` is the diver's own list and its order is a
        member of the document.

        **What is *gained* is not reported here, and that is the same rule read the other
        way.** A linked computer no recording answers to comes back as a recording the
        document never had, exactly as it comes back as a gear item the document never had
        — the documented exception in `docs/uddf-writing.md`, where nothing is lost and so
        nothing is said. That covers a dive with no recordings at all, which is every
        hand-logged dive in a logbook whose owner listed their computer in their kit.
        """
        computers = {
            item.get("uuid")
            for item in self.document.get("gear") or []
            if item.get("type") == "computer"
        }
        linked = list(
            dict.fromkeys(
                [_uddf_id("gear", uuid) for uuid in gear_uuids if uuid in computers] + devices
            )
        )
        carried = list(
            dict.fromkeys(
                self.recording_element(index, rec_index)
                for rec_index, entry in enumerate(recordings)
                if entry.get("device")
            )
        )
        if not (linked and carried):
            return
        if linked[0] != carried[0]:
            self.note(
                where,
                "UDDF gives a dive one <samples> and one <internaldivenumber>, and a reader takes both off "
                "the first <divecomputer> the dive links — which is not the element this dive's primary "
                "recording was written into; the profile and the device counter come back on that computer "
                "instead",
                "dropped",
            )
            return
        # Only the elements this dive's own recordings went out on, in the order a reader
        # will meet them. Every one of them is linked — a folded element only exists
        # because the dive links its gear item, and an unfolded one gets a link of its own
        # — so this is a permutation of `carried` and differs from it exactly when the
        # recordings come back in another order.
        met = [element for element in linked if element in set(carried)]
        if met != carried:
            self.note(
                where,
                "a reader recovers this dive's recordings from its <equipmentused> links, which run in the "
                "kit list's order rather than the recordings' own; this dive's come back in a different "
                "order, and §6.4a makes the first of them the primary",
                "dropped",
            )

    def recording_element(self, index: int, rec_index: int) -> str | None:
        """The `xs:ID` of the `<divecomputer>` one recording's device was written into."""
        at = (index, rec_index)
        if at in self.device_links:
            return self.device_links[at]
        for record in self.computers:
            if at in record.recordings and record.gear_index is not None:
                uuid = (self.document.get("gear") or [])[record.gear_index].get("uuid")
                return _uddf_id("gear", uuid) if uuid else f"gear-{record.gear_index}"
        return None

    def internal_dive_number(
        self, before: ET.Element, recordings: list[dict[str, Any]], where: str
    ) -> None:
        """`<internaldivenumber>`, from the **primary** recording's device and no other.

        It sits between `<divenumber>` and `<datetime>` in `informationbeforediveType`'s
        sequence, and it is a child of the *dive* rather than of the computer — so a dive
        carrying two recordings has one slot and no way to say whose counter is in it. The
        reader gives it to the first linked computer, so the writer takes it off the first
        recording; a later recording's counter goes with the recording UDDF had to drop.

        `xs:positiveInteger` where §6.4b puts a floor of 0 under the counter, so a counter
        of **0** is not written and is reported — the same trade `<divenumber>` already
        makes: a zero there invalidates the whole document rather than one element.
        """
        for index, entry in enumerate(recordings):
            counter = (entry.get("device") or {}).get("dive_number")
            if counter is None:
                continue
            here = f"{where}/recordings/{index}/device"
            if index > 0:
                self.note(
                    here,
                    "UDDF records one dive counter per dive, under <informationbeforedive>, and it is the "
                    "first recording's; this device's counter has nowhere to go",
                    "dropped",
                )
            elif counter > 0:
                _sub(before, "internaldivenumber", str(counter))
            else:
                self.note(
                    here,
                    f"the device's counter is {counter}, and UDDF's <internaldivenumber> is a positive "
                    "integer; the counter is not written",
                    "dropped",
                )

    def recording_elements(
        self,
        element: ET.Element,
        recordings: list[dict[str, Any]],
        where: str,
        mix_by_gas_number: dict[int, str],
    ) -> bool:
        """The dive's `<samples>`, from the primary recording, and the report for the rest.

        UDDF gives a dive one `<datetime>` and one `<samples>`, so a document whose dive
        carries more than one recording cannot be written whole: the **primary** recording
        — the first, §6.4a — supplies the samples, and every other recording is reported as
        dropped, with its device still folded into `<equipment>` so that what was worn is
        not lost along with what it sampled.

        A recording's own `started_at` has no slot either, and `<datetime>` stays the
        **dive's** even where the primary recording states one: §6.2's `started_at` is the
        logbook's and is what every reader of a UDDF file expects to find there.
        """
        numbered = False
        for index, entry in enumerate(recordings):
            here = f"{where}/recordings/{index}"
            # `device`, `mode` and `profile` are the three this writer places, and
            # `deco_model` is in the set because it has a report of its own below rather than
            # the generic one; `started_at`, `source_files` and anything §6.4a gains report
            # themselves from the record.
            self.unmapped(here, entry, frozenset({"device", "mode", "deco_model", "profile"}))
            if entry.get("deco_model"):
                self.note(
                    f"{here}/deco_model",
                    "UDDF's <decomodel> requires a tissue table — a half-time and its coefficients for every "
                    "compartment — which §6.4c has no member for, and nothing is invented to satisfy a "
                    "required element; the decompression model is not written",
                    "dropped",
                )
            if index == 0:
                numbered = self.samples_element(
                    element, entry.get("profile"), entry.get("mode"), here, mix_by_gas_number
                )
            else:
                self.note(
                    here,
                    "UDDF holds one profile per dive, and this dive's is the primary recording's; this "
                    "recording is dropped, its device kept under <equipment> (spec §6.4a)",
                    "dropped",
                )
        return numbered

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
        self,
        element: ET.Element,
        profile: dict[str, Any] | None,
        mode: Any,
        where: str,
        mix_by_gas_number: dict[int, str],
    ) -> bool:
        """`<samples>`, and whether what was written asks a reader for a gas numbering.

        One waypoint per second any channel or event landed on — the union rather than the
        depth channel's axis, see the module docstring — so a temperature taken between two
        depth samples becomes its own waypoint, carrying a `<divetime>` and a
        `<temperature>` and no depth.

        The recording's `mode` is written here because UDDF states it on a waypoint: it goes
        on the **first**, which is what a reader takes it off.

        The return value is what `check_numbering` needs: a `<tankpressure ref>` or a
        `<switchmix ref>` is the only thing that makes a reader number the cylinders at all.
        """
        divemode = self.divemode(mode, where)
        if not profile:
            self.mode_unplaced(divemode, where)
            return False
        profile_where = f"{where}/profile"
        self.unmapped(
            profile_where,
            profile,
            # `tts` and `surface_gradient_factor` are deliberately outside this set: UDDF has
            # no time-to-surface element and no surface gradient factor, so both take the
            # report a member the format cannot hold gets.
            frozenset(
                {
                    "duration",
                    "depth",
                    "temperature",
                    "pressures",
                    "ndl",
                    "ppo2",
                    "cns",
                    "gradient_factor",
                    "events",
                }
            ),
        )

        readings: _Readings = {}

        def at(second: int) -> dict[str, Any]:
            return readings.setdefault(second, {"pressures": []})

        for second, centimetres in _series(profile.get("depth")):
            at(second)["depth"] = _decimal(centimetres) / CENTIMETRES_PER_METRE
        for second, tenths in _series(profile.get("temperature")):
            at(second)["temperature"] = _decimal(tenths) / TENTHS_PER_UNIT + KELVIN_OFFSET
        for second, seconds in _series(profile.get("ndl")):
            at(second)["nodecotime"] = _decimal(seconds)
        for second, hundredths in _series(profile.get("ppo2")):
            at(second)["calculatedpo2"] = _decimal(hundredths) / HUNDREDTHS_PER_UNIT
        for second, tenths in _series(profile.get("cns")):
            at(second)["cns"] = _decimal(tenths) / TENTHS_PER_UNIT
        # **The documented fraction, not the whole percent §6.4 records.** `uddf-mapping.md`
        # keys the percent-or-fraction question on the generator, and this writer is not a
        # generator that table names — it stamps `divejson convert` — so a file it produces
        # is read back by the fraction branch, and a written `0.67` comes back as `67`.
        # Writing whole percent would come back as `6700`.
        for second, percent in _series(profile.get("gradient_factor")):
            at(second)["gradientfactor"] = _decimal(percent) / PERCENT_PER_FRACTION

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
            self.mode_unplaced(divemode, where)
            return False
        first = min(readings)
        samples = _sub(element, "samples")
        for second in sorted(readings):
            reading = readings[second]
            # `waypointType` is an `xs:sequence`, so these go in exactly this order. It is
            # the XSD's and not a preference: `<cns>` comes third in the type and therefore
            # first in a waypoint carrying no alarm or battery reading, and `<nodecotime>` is
            # last of all.
            waypoint = _sub(samples, "waypoint")
            if "cns" in reading:
                _sub(waypoint, "cns", _num(reading["cns"]))
            if "calculatedpo2" in reading:
                _sub(waypoint, "calculatedpo2", _num(reading["calculatedpo2"]))
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
            if divemode is not None and second == first:
                _sub(waypoint, "divemode", type=divemode)
            if "gradientfactor" in reading:
                _sub(waypoint, "gradientfactor", _num(reading["gradientfactor"]))
            if "nodecotime" in reading:
                _sub(waypoint, "nodecotime", _num(reading["nodecotime"]))

        # Every channel this writer **wrote**, and no others: the span is what a reader takes
        # off the file produced here, so a `tts` reaching past the last depth sample does not
        # belong in it — that channel is dropped, and the note above says so.
        span = max(
            (
                channel["times"][-1]
                for channel in (
                    profile.get("depth"),
                    profile.get("temperature"),
                    profile.get("ndl"),
                    profile.get("ppo2"),
                    profile.get("cns"),
                    profile.get("gradient_factor"),
                    *(profile.get("pressures") or []),
                )
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

    def divemode(self, mode: Any, where: str) -> str | None:
        """§6.4a's `mode` in UDDF's spelling, or nothing where UDDF has no spelling for it.

        `gauge` is the one value with no counterpart: `divemodeType`'s five do not include a
        computer run as a bottom timer, and writing the nearest would be §5.4's guess. It is
        reported rather than approximated.
        """
        if not isinstance(mode, str):
            return None
        written = _DIVE_MODES.get(mode)
        if written is None:
            self.note(
                f"{where}/mode",
                f"UDDF's <divemode> has no value for a {mode} recording, its five being open, closed and "
                "semi-closed circuit and two spellings of a freedive; the mode is not written",
                "dropped",
            )
        return written

    def mode_unplaced(self, divemode: str | None, where: str) -> None:
        """A mode UDDF *can* spell, on a recording with no waypoint to write it on.

        `<divemode>` is a `<waypoint>` child and nothing else, so a recording that kept no
        sample has nowhere to put one — and that is a loss the report has to name, `mode`
        being inside `recording_elements`'s carried set and so silent without this.
        """
        if divemode is not None:
            self.note(
                f"{where}/mode",
                "UDDF states a dive's mode on a waypoint, and this recording has no samples to carry one; "
                "the mode is not written",
                "dropped",
            )

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
        single unclassified event whose label is two labels.

        **`type` is OPTIONAL (§6.6), so `kind` may be nothing at all**, which is the ordinary
        shape of an alarm: an event with no type and a label goes out as a `<setmarker>`
        carrying that label, exactly as it arrived. What UDDF can carry is the wording.
        """

        def at(second: int) -> dict[str, Any]:
            return readings.setdefault(second, {"pressures": []})

        for index, event in enumerate(events):
            event_where = f"{where}/events/{index}"
            self.unmapped(event_where, event, frozenset({"time", "type", "gas_number", "label"}))
            second = event["time"]
            kind = event.get("type")
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

            label = event.get("label")
            marker = kind if kind in _MARKER_TYPES else label
            if kind in _MARKER_TYPES and label:
                # `<setmarker>` is one string, and the type has to have it: the three named
                # types are the only thing this format's own round trip has to go on, so a
                # labelled safety stop keeps its type and loses its label rather than
                # arriving as an event nothing recognises.
                self.note(
                    event_where,
                    f"UDDF's <setmarker> carries one string, and the event is a {kind} with a label; the type "
                    "is written and the label is dropped",
                    "dropped",
                )
            elif kind is not None and kind not in _MARKER_TYPES and label:
                # The mirror of it, and that way round because the **label** is the half UDDF
                # can carry back: `<setmarker>ppo2_high</setmarker>` would return as an
                # unclassified event labelled `ppo2_high`, where
                # `<setmarker>PO2 High</setmarker>` returns as the marker the diver saw.
                self.note(
                    event_where,
                    f"UDDF's <setmarker> carries one string and has no keyword for a {kind}; the label is "
                    "written and the type is dropped",
                    "dropped",
                )
            if not marker:
                # `<setmarker>` is a bare string with no type beside it, so a typed event
                # with no label has nothing UDDF can carry: writing the word its type spells
                # would come back as an event labelled `ppo2_high`, which is a label the
                # document did not have and not the device's wording §6.6's `label` holds.
                self.note(
                    event_where,
                    f"UDDF's <setmarker> carries only text, and the event is "
                    f"{'an unlabelled ' + kind if kind else 'neither typed nor labelled'}; it is dropped "
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
