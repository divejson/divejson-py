"""Reading Suunto's DM5 desktop XML export into DiveJSON.

The Suunto desktop application writes one XML document per dive, rooted at a `<Dive>` in
the `Suunto.Diving.Dal` datacontract namespace — a .NET serializer's rendering of the
application's own dive record. `docs/suunto-xml-mapping.md` is the prose companion: every
element this module reads, every one it deliberately does not, and why. What is true of
every source format rather than of this one — the note kinds, identity, the way a zero
reads, the number bound, the sample axis, the `<!DOCTYPE>` refusal, taking a child by its
lowercased local name — lives in `converter.py`, `series.py` and `xmlsource.py`, and this
module inherits it.

Five things shape what is below.

**Every member is present in every document, and absence is spelled `i:nil="true"`.** The
serializer emits the whole contract whether or not the dive filled it in, so the presence
of an element says nothing at all: `<BatteryLevel i:nil="true" />` appears in all 384 of
the exports this reader was built against and carries a reading in none of them. A nil
element is *not recorded*, never zero — which is `converting.md`'s empty-is-absent rule
meeting the writer that makes it unmissable.

**A `<Mode>3</Mode>` document is a freedive, and it is skipped and reported rather than
converted.** DiveJSON has no member for the kind of a dive, so a converted freedive would
be indistinguishable from a scuba dive with no gas and no decompression algorithm.
Forty-two of the 384 exports in hand are freedives, and they are exactly the 42 that carry
no `<DiveMixture>` at all. This is the answer the Suunto app JSON reader already gives an
entry whose `ActivityType` is not 51.

**The same three readings are in three different units across this vendor's two exports,
and only a dive that exists in both makes it visible.** CNS is whole percent here and a 0-1
fraction in the app's JSON; cylinder pressures are millibar here and Pascal there; and
`<SurfacePressure>` is the one pressure in *this* file that is not millibar but Pascal —
read as millibar its 104 900 would be 104.9 bar, a kilometre of seawater at the surface.
Each factor below is cross-checked against the same dive exported as JSON.

**The sample stream carries one `<Pressure>` per sample with no cylinder on it**, so the
channel's `gas_number` has to come from somewhere else, and `<TransmitterId>` is the only
element that knows: it is nil on exactly the cylinders that had no pod. Where one cylinder
claims the pod, the channel is that cylinder's. Where none does and readings arrive anyway,
the readings are evidence of a tank the mixture list does not name. Where more than one
does, the file cannot say which readings are whose and the channel is dropped.

**A cylinder with no transmitter has both its pressures written as `0`**, and the pair is
the format's absent-marker rather than a tank breathed to nothing. §6.3 settles the start
outright; the end is the same absence, and the corpus is unambiguous that the two arrive
together — of 353 mixtures, 255 write `0` for both and 98 write a real reading for both,
and not one writes a zero on its own.
"""

from __future__ import annotations

import re
import uuid as uuid_pkg
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from .converter import (
    CENTIMETRES_PER_METRE,
    MAX_NOTES,
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
    decimal_of,
    header,
    recorded,
    rounded,
)
from .series import Channel, SampleAxis
from .validate import validate_document
from .xmlsource import attribute, child, children, parse_xml, root_name, text

__all__ = [
    "SUUNTO_XML",
    "SUUNTO_XML_ID_NAMESPACE",
    "MalformedSuuntoXmlError",
    "SuuntoXmlAdapter",
    "SuuntoXmlError",
]

# The format id this adapter registers under, which is also the name of the directory a
# conformance corpus keeps its pairs in. `suunto_xml` rather than `suunto`, because the
# same vendor's mobile application exports a different format that this package also reads.
FORMAT = "suunto_xml"
ROOT = "dive"

# The datacontract namespace every one of these documents declares on its root element.
# Matched as bytes in the sniffed head rather than after a parse, because `<dive>` is a
# generic root name — `<uddf>` and `<divelog>` are each one format's and nothing else's,
# and `<dive>` is a name any dive-log format might reach for — so the sniff asks for the
# namespace too, and the two together are what no other reader can claim. See
# `SuuntoXmlAdapter.sniff` for why `convert` asks for less.
NAMESPACE_MARKER = b"schemas.datacontract.org/2004/07/Suunto.Diving.Dal"

# uuid5(NAMESPACE_URL, "https://divejson.org/ns/suunto_xml"). Fixed forever: changing it
# would renumber every document any released version of this converter has produced from a
# DM5 export, and `docs/suunto-xml-mapping.md` *Identity* records the value as normative for
# any port.
SUUNTO_XML_ID_NAMESPACE = uuid_pkg.UUID("cefc9278-1124-5d83-8af6-7f386f03a061")

# The `<Mode>` this reader refuses. 0 and 1 are air and nitrox — the oxygen fractions of the
# corpus say so, 243 of the 244 `<Mode>0</Mode>` exports carrying a single 21 % mixture —
# and both are scuba. 3 is a freedive: no mixture element at all, a nil `<Algorithm>` and a
# nil `<DiveTime>`, durations of 3 to 60 seconds and depths of 1.39 to 15.48 m.
FREEDIVE_MODE = Decimal(3)

# The unit factors, each cross-checked against the same dive exported as the Suunto app's
# JSON. See this module's docstring: the vendor's two exports disagree about all three.
#
# Cylinder pressures — `<StartPressure>`, `<EndPressure>` and the samples' `<Pressure>` —
# are millibar. `<CylinderWorkPressure>200000</CylinderWorkPressure>` is the same unit and
# is not mapped.
MILLIBAR_PER_BAR = Decimal(1000)
# `<SurfacePressure>` is the exception and is Pascal: 104 900 is 1.049 bar, and the app's
# JSON export of the same dive writes the identical integer into a member that reader
# already reads as Pascal.
PASCALS_PER_BAR = Decimal(100_000)

# The range §6.3 allows a cylinder pressure. A reading past it is a source defect rather
# than a scale to reinterpret: there is no scale in doubt here, this file writing every
# pressure it puts on a cylinder in one unit.
MAX_CYLINDER_PRESSURE = Decimal(350)

# The bounds §6.2 puts on a surface pressure and §6.3 on a ppO₂ limit, in the units those
# members hold. Both are wide enough that a reading outside one is a device that recorded
# something other than what the element claims, which is reported rather than clamped.
MIN_SURFACE_PRESSURE, MAX_SURFACE_PRESSURE = Decimal("0.4"), Decimal("1.2")
MIN_PO2_LIMIT, MAX_PO2_LIMIT = Decimal("0.4"), Decimal("2.0")

# How many cylinders one dive may describe. The corpus reaches two — 11 of its 342 dives
# with mixtures carry a back gas and a deco bottle — but a `<DiveMixtures>` element may hold
# any number, and each one is a row in a diver's logbook.
MAX_CYLINDERS = 16

# The `i:nil` attribute this serializer marks an unrecorded member with, matched by its
# lowercased local name the way every other name in this package is.
NIL = "nil"

# `<StartTime>` as this serializer writes it, leniently. It writes a naive .NET round-trip
# timestamp — a date, a `T`, a clock and usually a fraction, with no offset and no zone name
# anywhere in the document — so what is admitted here is §5.2's own grammar minus the offset
# rather than the one shape this writer happens to emit. The seconds are optional because a
# stored value missing them costs the whole dive otherwise, while §5.2's grammar requires
# them.
_TIMESTAMP = re.compile(
    r"\A(?P<date>\d{4}-\d{2}-\d{2})[Tt ]"
    r"(?P<hour>\d{2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?"
    r"(?P<fraction>\.\d+)?\Z"
)


class SuuntoXmlError(ConverterError):
    """The input could not be read as a Suunto DM5 export."""


class MalformedSuuntoXmlError(SuuntoXmlError):
    """The input is not well-formed XML, or its root element is not `<Dive>`."""


class SuuntoXmlAdapter:
    """The registry's view of this reader: what it claims, and how it converts.

    An instance of this is what `registry.py` registers; everything else in this module is
    behind it.
    """

    format: str = FORMAT
    suffixes: tuple[str, ...] = (".xml",)
    namespace: uuid_pkg.UUID = SUUNTO_XML_ID_NAMESPACE

    def sniff(self, head: bytes) -> bool:
        """Whether a bounded head of bytes opens a Suunto DM5 dive export.

        The root element name **and** the datacontract namespace, where the other XML
        readers in this package need only the name — see `NAMESPACE_MARKER` for why. The
        namespace declaration sits on the root element, so it is inside any head that
        reached the root at all.

        `convert` deliberately asks for less, the root name alone, so that a caller naming
        this format outright still gets a document whose namespace is spelled differently
        read. Deciding what a file *is* wants the tighter test; reading a file somebody has
        already named wants the leniency `converting.md` asks for everywhere else.
        """
        return root_name(head) == ROOT and NAMESPACE_MARKER in head

    def convert(self, data: bytes, *, exported_at: datetime, scope: Scope) -> Conversion:
        """Convert one DM5 dive export into DiveJSON.

        `data` is **bytes**, not text: an XML document declares its own encoding, and a file
        that says `encoding="ISO-8859-1"` has to be decoded by the parser that read that
        declaration. Handing `ElementTree` a `str` carrying one is a `ValueError` anyway.

        Raises `DoctypeRefusedError`, `MalformedSuuntoXmlError` or
        `NonConformingOutputError`.
        """
        root = parse_xml(data, root=ROOT, malformed=MalformedSuuntoXmlError)
        return _Converter(root, exported_at=exported_at, scope=scope).run()


SUUNTO_XML = SuuntoXmlAdapter()


# -- reading the tree -----------------------------------------------------------------
#
# The accessors below take an element's own spelling — `MaxDepth`, `Dive.Sample` — and
# lower it for the shared `xmlsource` matchers, which compare lowercased local names. The
# call sites therefore read like the file, and the report can quote the element by the name
# a diver will find when they open it.


def _child(element: ET.Element | None, name: str) -> ET.Element | None:
    return child(element, name.lower())


def _children(element: ET.Element | None, name: str) -> list[ET.Element]:
    return children(element, name.lower())


def _recorded_text(element: ET.Element | None, name: str) -> str | None:
    """A child element's text, or `None` where the source recorded nothing.

    Two spellings of nothing, and both are this serializer's: an `i:nil="true"` element, and
    an empty one. The nil test is explicit rather than left to `xmlsource.text`'s
    empty-is-absent rule — which would cover every document in hand, this writer never
    putting content inside a nil element — because `i:nil` is the format's own word for
    *not recorded*, and a reader that noticed only the emptiness would read
    `<Ceiling i:nil="true">0</Ceiling>` as a ceiling.
    """
    found = _child(element, name)
    if found is None or attribute(found, NIL) == "true":
        return None
    return text(found)


@dataclass(slots=True)
class _Cylinder:
    """One converted cylinder beside the source facts the profile needs from it.

    `transmitted` and `switches` both live on the `<DiveMixture>` element rather than on the
    samples, which is what makes this format's gas switches the one kind among the three
    Suunto exports that needs no join: the cylinder a switch names is the element the time
    was found in.
    """

    member: dict[str, Any]
    number: int
    transmitted: bool = False
    switches: list[int] = field(default_factory=list)


# -- converting it --------------------------------------------------------------------


class _Converter:
    def __init__(self, root: ET.Element, *, exported_at: datetime, scope: Scope) -> None:
        self.root = root
        self.exported_at = exported_at
        self.scope = scope
        self.notes: list[Note] = []
        # The claimed-UUID table inside is shared with the rest of the archive this file
        # came from, if it came from one, so that a record two members both define is
        # written once and referred to by both. A DM5 export is one dive per file and a
        # logbook is a directory of them, so that archive is the ordinary case here.
        self.identities = Identities(SUUNTO_XML_ID_NAMESPACE, scope, self.note)

    # -- reporting ---------------------------------------------------------------

    def note(self, where: str, message: str, kind: NoteKind) -> None:
        """One line of the report, at a path into the source this conversion read."""
        self.notes.append(Note(self.scope.where(where), message, kind))

    # -- readings ----------------------------------------------------------------

    def number(self, element: ET.Element | None, name: str, where: str) -> Decimal | None:
        """One element's text as a number, reporting text that is not one.

        A nil or empty element is silent: the source recorded nothing, and this format says
        so on every member of every document, so a line per unfilled member would be the
        whole datacontract restated once per dive. Text that is *present* and unreadable is
        a different thing and is reported.
        """
        raw = _recorded_text(element, name)
        if raw is None:
            return None
        value = decimal_of(raw)
        if value is None:
            self.note(where, f"<{name}> is not a number this reader can carry; dropped", "dropped")
        return value

    def scalar(self, dive: dict[str, Any], member: str, name: str, where: str) -> None:
        """One recorded number onto one dive member, by the member's own constraint.

        Which way a zero reads is the schema's decision rather than this module's, so
        `recorded` is asked with the member the value is headed for: a `<MaxDepth>0</MaxDepth>`
        is a dive whose depth the computer never had, while a zero `<CnsEnd>` is the oxygen
        clock a diver's first dive of the day starts on.

        Every member written through here is one §6.2 types as a `number`, so the reading
        is carried as a float. `duration`, the one whole-number member this reader maps, has
        `read_duration` of its own — an integer member has to be rounded *before* its
        constraint is asked about, and that ordering is the point of separating them.
        """
        value = self.number(self.root, name, where)
        if value is None:
            return
        if recorded(value, record="dive", member=member):
            dive[member] = float(value)
        else:
            self.note(
                where,
                f"<{name}> is {value}, which is not a value the format's `{member}` can hold; read as "
                "not recorded",
                "absent",
            )

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
        """What the file said about itself.

        `source_generator` is the **dive computer**, not the desktop application: this
        document is that application's rendering of what the computer on the diver's wrist
        recorded, and `<Source>` is what the computer calls itself while `<Software>` is its
        firmware. `<SerialNumber>` is deliberately not carried — it identifies one physical
        device rather than the software that produced the readings, which is what §5.5's
        provenance block is about.

        There is no `extensions.divejson.inferred` here, and its absence is a statement
        rather than an omission: this reader computes no value from other readings, so it
        raises no `inferred` finding and has no derived member to label (spec §5.4). The two
        kinds its report can carry are `absent` and `dropped`.
        """
        provenance: dict[str, Any] = {"converted_from": FORMAT}
        name = _recorded_text(self.root, "Source")
        if name is not None:
            generator: dict[str, Any] = {"name": name}
            version = _recorded_text(self.root, "Software")
            if version is not None:
                generator["version"] = version
            provenance["source_generator"] = generator
        return provenance

    # -- the dive ----------------------------------------------------------------

    def read_dive(self) -> dict[str, Any] | None:
        where = "dive/0"
        if not self.is_a_scuba_dive(where):
            return None

        started_at = self.read_started_at(where)
        if started_at is None:
            return None
        # This export records no id for its dive — `<DiveNumberInSerie>` is the computer's
        # own counter and not an identifier — so a dive takes the positional stand-in
        # `converting.md` defines, and the note that says so, since an identity that moves
        # when a file's order changes is a fact a diver may need (spec §5.3).
        claimed, carried = self.identities.for_record("dive", None, where, 0)
        if claimed is None or not carried:
            return None

        dive: dict[str, Any] = {"uuid": claimed, "started_at": started_at}
        self.read_dive_number(where)

        # In §6.2's own member order, so a converted dive reads down the schema.
        self.read_duration(dive, where)
        notes = _recorded_text(self.root, "Note")
        if notes is not None:
            dive["notes"] = capped(notes, MAX_NOTES, note=self.note, where=where, member="<Note>")
        self.read_depths(dive, where)
        self.scalar(dive, "bottom_temperature", "BottomTemperature", where)
        self.read_conditions(where)
        for member, name in (
            ("cns_start", "CnsStart"),
            ("cns_end", "CnsEnd"),
            ("otu_start", "OtuStart"),
            ("otu_end", "OtuEnd"),
        ):
            self.scalar(dive, member, name, where)
        self.read_surface_pressure(dive, where)

        cylinders = self.read_cylinders(where)
        profile = self.read_profile(cylinders, where)
        if cylinders:
            dive["cylinders"] = [cylinder.member for cylinder in cylinders]
        if profile is not None:
            dive["profile"] = profile
        return dive

    def is_a_scuba_dive(self, where: str) -> bool:
        """Whether this document is a dive this reader carries, on `<Mode>`'s say-so.

        A `<Mode>3</Mode>` document is a freedive, and DiveJSON has no member for the kind
        of a dive — so a converted one would arrive indistinguishable from a scuba dive with
        no gas and no algorithm, mislabelled by omission in a logbook it shares with real
        scuba dives. It is skipped and reported instead, which is the answer the Suunto app
        JSON reader gives an activity that is not a dive.

        A document that states **no** `<Mode>` is read on: absence is not a claim, and
        `converting.md`'s first rule is that schema validity is never a precondition.
        """
        if self.number(self.root, "Mode", where) != FREEDIVE_MODE:
            return True
        self.note(
            where,
            "the export records dive mode 3, which is a freedive, and this format has no member for the "
            "kind of a dive; the dive is dropped rather than carried as a scuba dive it could not be told "
            "apart from",
            "dropped",
        )
        return False

    def read_started_at(self, where: str) -> str | None:
        """`<StartTime>` as a §5.2 date-time, fraction kept and no offset supplied.

        **A DM5 export records no time zone anywhere** — not on the dive, not at the root —
        so every conversion of one carries the wall clock alone and says so. Supplying an
        offset is the failure §5.2 exists to prevent, and it would be the easiest thing in
        the world to do wrongly here: the same dive's app-JSON export carries one, and
        taking it from there would be this converter asserting a zone this file does not.

        The **sub-second fraction is preserved**: 380 of the 384 exports in hand write one,
        `<StartTime>2021-04-06T11:16:42.6</StartTime>` against the same dive's
        `2021-04-06T11:16:42.600+02:00` in the app's JSON, and §5.2 makes the fraction
        OPTIONAL rather than forbidden, so truncating would discard a reading the file
        states.
        """
        raw = _recorded_text(self.root, "StartTime")
        if raw is None:
            self.note(
                where,
                "the export records no <StartTime>, and the format requires a start time; the dive is "
                "dropped (spec §6.2)",
                "dropped",
            )
            return None
        read = _date_time(raw)
        if read is None:
            self.note(
                where,
                "<StartTime> is not a date and time this reader can carry; the dive is dropped (spec §6.2)",
                "dropped",
            )
            return None
        started_at, seconds = read
        if not seconds:
            self.note(where, "<StartTime> records no seconds; read as :00", "absent")
        self.note(
            where,
            "the source records no UTC offset on the dive's start time; the wall clock travels alone "
            "(spec §5.2)",
            "absent",
        )
        return started_at

    def read_dive_number(self, where: str) -> None:
        """`<DiveNumberInSerie>`, read and deliberately not carried.

        It is the *computer's* counter rather than the diver's lifetime dive number: it
        starts at 1 on a new or factory-reset device and starts again on the next one, so
        carrying it would stamp a dive #1 onto somebody's three-hundredth dive. §6.2's
        `dive_number` is the diver's, and this file does not have it.
        """
        if _recorded_text(self.root, "DiveNumberInSerie") is not None:
            self.note(
                where,
                "<DiveNumberInSerie> counts this dive within the computer's own series and starts again on "
                "a new or reset device, so it is not carried as the diver's dive number; dropped "
                "(spec §6.2)",
                "dropped",
            )

    def read_duration(self, dive: dict[str, Any], where: str) -> None:
        """`<Duration>`, the whole period the computer logged.

        Deliberately not `<BottomTime>`, which is the time spent at depth and runs a third
        shorter — 1 028 s against 2 001 on the dive this reader is measured against — and
        not `<DiveTime>`, which is `i:nil` on every one of the 384 exports in hand, so
        nothing here has ever seen a value of it. §6.2's `duration` is the dive's own
        length, and `<Duration>` is the only element in this format that has been observed
        holding it.

        **The whole seconds are what the member's own rule is asked about**, not the value
        the element states. §6.2 makes `duration` a positive integer, so a `<Duration>` of
        0.4 s is above the schema's floor while the number written from it — zero — is not;
        checking before rounding lets that one through and fails the converter's own
        validation. The other readers in this package round first for the same reason.
        """
        value = self.number(self.root, "Duration", where)
        if value is None:
            return
        seconds = rounded(value)
        if recorded(seconds, record="dive", member="duration"):
            dive["duration"] = seconds
        else:
            self.note(
                where,
                f"<Duration> is {value} s, which the format cannot hold as a duration (§6.2 admits a "
                "positive whole number of seconds); read as not recorded",
                "absent",
            )

    def read_depths(self, dive: dict[str, Any], where: str) -> None:
        """`<MaxDepth>` and `<AvgDepth>`, both readings rather than derivations.

        This export summarises its own dive, so neither depth is computed from the sample
        stream and neither is `inferred`. The deepest sample is regularly a little shallower
        than `<MaxDepth>`, the computer having seen a peak between the samples it stored.
        """
        self.scalar(dive, "max_depth", "MaxDepth", where)
        self.scalar(dive, "avg_depth", "AvgDepth", where)
        maximum, average = dive.get("max_depth"), dive.get("avg_depth")
        if maximum is not None and average is not None and average > maximum:
            self.note(
                where,
                f"the average depth {average} m is deeper than the greatest depth {maximum} m, which cannot "
                "be; the average is dropped rather than either being adjusted to fit (spec §6.2)",
                "dropped",
            )
            del dive["avg_depth"]

    def read_conditions(self, where: str) -> None:
        """`<Visibility>`, `<Weather>` and `<Weight>`, read and refused as a block.

        These three are the desktop application's dive-conditions panel, and they arrive
        together or not at all: 25 of the 384 exports in hand carry all three and 359 carry
        none, and every recorded value is `0`.

        None of them can be read at a scale the file states. `<Weather>` is a code with no
        member in §6 at all. `<Visibility>` is the application's own rating where §6.2's is
        metres, which is the refusal Subsurface's five-star `@visibility` gets for the same
        reason. And `<Weight>` reaches a §6.2 member measured in kilograms, but the export
        writes a bare number and neither it nor any companion file in hand states the unit
        the application wrote it in — so reading it would break `converting.md`'s
        refuse-rather-than-guess rule for a member whose every recorded value here is zero
        in either unit.
        """
        for name in ("Visibility", "Weather", "Weight"):
            if _recorded_text(self.root, name) is not None:
                self.note(
                    where,
                    f"<{name}> comes from the desktop application's dive-conditions panel in a unit the "
                    "export does not state, so it is dropped rather than read at a scale nothing states",
                    "dropped",
                )

    def read_surface_pressure(self, dive: dict[str, Any], where: str) -> None:
        """`<SurfacePressure>`, in Pascal where §6.2 holds bar.

        §6.2 bounds the member at 0.4 to 1.2 bar, which is the range a barometer at a dive
        site can read. That bound is also the backstop for the unit: every one of the 384
        exports in hand lands in 103 100 to 106 700, which is barometric on the Pascal
        reading and a hundred metres of seawater on the millibar one.
        """
        pascal = self.number(self.root, "SurfacePressure", where)
        if pascal is None:
            return
        bar = pascal / PASCALS_PER_BAR
        if not MIN_SURFACE_PRESSURE <= bar <= MAX_SURFACE_PRESSURE:
            self.note(
                where,
                f"the export records a surface pressure of {bar} bar, outside the "
                f"{MIN_SURFACE_PRESSURE} to {MAX_SURFACE_PRESSURE} the format allows; dropped",
                "dropped",
            )
            return
        dive["surface_pressure"] = float(bar)

    # -- cylinders ---------------------------------------------------------------

    def read_cylinders(self, where: str) -> list[_Cylinder]:
        """`<DiveMixtures>/<DiveMixture>` as §6.3 Cylinders, in document order."""
        mixtures = _children(_child(self.root, "DiveMixtures"), "DiveMixture")
        if len(mixtures) > MAX_CYLINDERS:
            self.note(
                where,
                f"the export describes {len(mixtures)} cylinders, and at most {MAX_CYLINDERS} are read from "
                "one dive; the rest are dropped",
                "dropped",
            )
            mixtures = mixtures[:MAX_CYLINDERS]
        return [
            self.read_cylinder(mixture, number, f"{where}/cylinder/{number}")
            for number, mixture in enumerate(mixtures)
        ]

    def read_cylinder(self, mixture: ET.Element, number: int, where: str) -> _Cylinder:
        """One `<DiveMixture>` as a §6.3 Cylinder, converting units as it goes.

        `<Type>` is deliberately not read as §6.3's `role`, though it is the only candidate
        element this format has: it is `1` on all 353 mixtures in hand, including both
        cylinders of a dive where a 21/0 back gas and a 52/0 deco bottle carry it alike.
        Whatever it encodes, it is not what the cylinder was carried for, and mapping it
        would confidently label a deco bottle "bottom". `<Name>` is nil on every mixture and
        §6.3 has no member for it either way.
        """
        cylinder: dict[str, Any] = {}

        litres = self.number(mixture, "Size", where)
        if litres is not None and recorded(litres, record="cylinder", member="volume"):
            cylinder["volume"] = float(litres)

        self.read_pressures(mixture, cylinder, where)
        self.read_mix(mixture, cylinder, where)

        po2 = self.number(mixture, "PO2", where)
        if po2 is not None:
            if MIN_PO2_LIMIT <= po2 <= MAX_PO2_LIMIT:
                # Already bar here (`<PO2>1.4</PO2>`), where the app's JSON export of the
                # same dive writes the same limit in Pascal.
                cylinder["po2_limit"] = float(po2)
            else:
                self.note(
                    where,
                    f"the cylinder records a ppO₂ limit of {po2} bar, outside the {MIN_PO2_LIMIT} to "
                    f"{MAX_PO2_LIMIT} the format allows; dropped",
                    "dropped",
                )

        return _Cylinder(
            member=cylinder,
            number=number,
            transmitted=_recorded_text(mixture, "TransmitterId") is not None,
            switches=self.read_switch_times(mixture, where),
        )

    def read_pressures(self, mixture: ET.Element, cylinder: dict[str, Any], where: str) -> None:
        """`<StartPressure>` and `<EndPressure>`, in millibar, as one absent-marker pair.

        A cylinder with no transmitter has **both** written as `0`, and that pair is this
        format's way of saying it measured neither. §6.3 settles the start on its own —
        writers must not emit a zero one, so it is read as not recorded — and the end
        follows it here rather than being read as an answer, because the corpus is
        unambiguous that the two arrive together: of 353 mixtures, 255 write zero for both
        and 98 write a real reading for both, and not one writes a zero on its own. Reading
        the end alone as `0.0` would put "breathed the cylinder to nothing" on every
        untransmitted tank in a logbook. A zero end pressure beside a *recorded* start is
        left alone, which is the reading §6.3's `minimum: 0` asks for.
        """
        start = self.pressure(mixture, "StartPressure", where)
        end = self.pressure(mixture, "EndPressure", where)
        if start is not None and not recorded(start, record="cylinder", member="start_pressure"):
            paired = end == 0
            self.note(
                where,
                "the cylinder's start and end pressures are both 0 bar, which this export writes for a "
                "cylinder that had no transmitter; read as not recorded (spec §6.3)"
                if paired
                else "the cylinder's start pressure is 0 bar, which devices write to mean 'not recorded'; "
                "read as not recorded (spec §6.3)",
                "absent",
            )
            start = None
            if paired:
                end = None
        if start is not None and end is not None and end > start:
            self.note(
                where,
                f"the end pressure {end} bar is above the start pressure {start} bar, which cannot be; the "
                "end pressure is dropped",
                "dropped",
            )
            end = None
        if start is not None:
            cylinder["start_pressure"] = float(start)
        if end is not None:
            cylinder["end_pressure"] = float(end)

    def pressure(self, mixture: ET.Element, name: str, where: str) -> Decimal | None:
        """One cylinder pressure in bar from the millibar this export writes."""
        millibar = self.number(mixture, name, where)
        if millibar is None:
            return None
        bar = millibar / MILLIBAR_PER_BAR
        if not 0 <= bar <= MAX_CYLINDER_PRESSURE:
            self.note(
                where,
                f"<{name}> is {bar} bar, outside the 0 to {MAX_CYLINDER_PRESSURE} the format allows; dropped",
                "dropped",
            )
            return None
        return bar

    def read_mix(self, mixture: ET.Element, cylinder: dict[str, Any], where: str) -> None:
        """`<Oxygen>` and `<Helium>`, already whole percent.

        The app's JSON export of the same dive writes both as 0-1 fractions, which is the
        easiest thing to get wrong across this vendor's two formats: an unconverted `0.21`
        there is a 0.21 % mix. Here `<Oxygen>21</Oxygen>` is the percentage §6.3 holds, and
        nothing is scaled.
        """
        blend: dict[str, float] = {}
        for member, name in (("oxygen", "Oxygen"), ("helium", "Helium")):
            percent = self.number(mixture, name, where)
            if percent is None:
                continue
            if not 0 <= percent <= 100:
                self.note(
                    where,
                    f"<{name}> is {percent} percent, outside the 0 to 100 a mix can be; dropped",
                    "dropped",
                )
                continue
            blend[member] = float(percent)
        if blend.get("oxygen", 0.0) + blend.get("helium", 0.0) > 100:
            self.note(
                where,
                "oxygen and helium sum above 100 percent, which no mix can; both are dropped (spec §6.3)",
                "dropped",
            )
            return
        if not blend:
            self.note(
                where,
                "the export records no gas for this cylinder; absent means not recorded, never air "
                "(spec §6.3)",
                "absent",
            )
        cylinder.update(blend)

    def read_switch_times(self, mixture: ET.Element, where: str) -> list[int]:
        """When the diver switched onto this cylinder, from its own `<DiveGasChanges>`.

        `<DiveGasChanges>` is nested **inside** each `<DiveMixture>` rather than being a list
        of its own, so the cylinder a switch names is the element the time was found in.
        That is what makes this the one gas-switch record among the three Suunto exports
        that needs no join, and why a marker on the profile and the row in the cylinder list
        name the same cylinder by construction rather than by agreement.

        **A `<GasChangeTime>0</GasChangeTime>` is kept, and it is the common case** — 342 of
        the corpus's 363 switches. On a single-gas dive it is the one marker saying the dive
        was breathed on that gas throughout, and §6.5 gives an event time a floor of zero, so
        it needs no rebasing even though most of these exports number their samples from 1.

        `<SetPointType>` and a switch's own `<PO2>` describe a closed-circuit setpoint, which
        §6.5's event has no member for and which is nil on every switch in hand.
        """
        times: list[int] = []
        for index, change in enumerate(_children(_child(mixture, "DiveGasChanges"), "DiveGasChange")):
            at = f"{where}/gas change/{index}"
            seconds = self.number(change, "GasChangeTime", at)
            if seconds is None:
                continue
            second = rounded(seconds)
            if second < 0:
                self.note(at, f"the gas change is at {second} s, before the dive began; dropped", "dropped")
                continue
            times.append(second)
        return times

    # -- the profile -------------------------------------------------------------

    def read_profile(self, cylinders: list[_Cylinder], where: str) -> dict[str, Any] | None:
        """`<DiveSamples>/<Dive.Sample>` as a §6.5 Profile.

        Every sample element carries every channel, nil where the sensor had nothing — a
        mid-dive transmitter dropout is a nil `<Pressure>` on a sample whose `<Depth>` is
        unaffected — so the channels sit on their own axes and none is padded to another's
        length. Two samples on one second are therefore a real collision here rather than
        two sensor streams that were never in competition, and the axis settles them per
        channel; it fires on 37 of the corpus's exports, every one of them a freedive this
        reader has already skipped before reaching this method.

        `<AveragedTemperature>` is deliberately not the temperature channel: it is a
        smoothed reading sitting beside the raw `<Temperature>` in the same element, and
        smoothing is a chart's decision rather than something to bake into a logbook.
        `<SacRate>` and `<GasTime>` are the computer's own gas arithmetic, which §6.5 has no
        channel for; `<Heading>` is a compass bearing, likewise, and is nil on all 115 602
        samples in hand.
        """
        samples = _children(_child(self.root, "DiveSamples"), "Dive.Sample")
        if not samples:
            return None

        axis = SampleAxis(self.note, where, noun="sample", time_member="<Time>")
        for index, sample in enumerate(samples):
            seconds = self.number(sample, "Time", f"{where}/sample/{index}")
            axis.offer(None if seconds is None else rounded(seconds), sample)

        depth = Channel()
        ceiling = Channel()
        temperature = Channel()
        pressure = Channel()
        # Counted rather than reported one at a time: a pod that reports outside §6.3's
        # range usually does it for a run of samples, and one line per reading would be a
        # transmitter fault written out several hundred times.
        out_of_range = 0
        for second, sample in axis.ordered():
            metres = self.number(sample, "Depth", where)
            if metres is not None:
                depth.record(second, rounded(metres * CENTIMETRES_PER_METRE))
            # A ceiling of zero is not a ceiling — zero says the diver may surface, and a
            # gap in the channel's times is how §6.5 spells no obligation. This export
            # writes `i:nil` rather than a zero on every no-deco sample, its 1 760 recorded
            # ceilings running 3.0 to 15.44 m, so the guard is `converting.md`'s rule kept
            # rather than one this format needs.
            above = self.number(sample, "Ceiling", where)
            if above is not None and above > 0:
                ceiling.record(second, rounded(above * CENTIMETRES_PER_METRE))
            celsius = self.number(sample, "Temperature", where)
            if celsius is not None:
                temperature.record(second, rounded(celsius * TENTHS_PER_UNIT))
            millibar = self.number(sample, "Pressure", where)
            if millibar is not None:
                bar = millibar / MILLIBAR_PER_BAR
                if 0 <= bar <= MAX_CYLINDER_PRESSURE:
                    pressure.record(second, rounded(bar * TENTHS_PER_UNIT))
                else:
                    out_of_range += 1
        if out_of_range:
            self.note(
                where,
                f"{out_of_range} {'sample records' if out_of_range == 1 else 'samples record'} a tank "
                f"pressure outside the 0 to {MAX_CYLINDER_PRESSURE} bar the format allows; "
                f"{'that reading is' if out_of_range == 1 else 'those readings are'} dropped",
                "dropped",
            )

        pressures = self.label_pressures(pressure, cylinders, where)
        events = self.read_events(cylinders)
        profile = axis.profile(
            {"depth": depth, "ceiling": ceiling, "temperature": temperature},
            pressures=pressures,
            events=events,
        )
        # §6.3 calls `gas_number` a label rather than an array index, so a numbering is
        # asserted only where something in the profile depends on it — a pressure channel,
        # or a gas switch naming the cylinder it switched to.
        if profile is not None and (profile.get("pressures") or any("gas_number" in event for event in events)):
            for cylinder in cylinders:
                cylinder.member["gas_number"] = cylinder.number
        return profile

    def label_pressures(
        self, pressure: Channel, cylinders: list[_Cylinder], where: str
    ) -> tuple[tuple[int, Channel], ...]:
        """Which cylinder the sample stream's one `<Pressure>` channel belongs to.

        The channel carries no cylinder of its own — one `<Pressure>` per sample, with no
        key on it — so the label has to come from somewhere else, and `<TransmitterId>` is
        the only element that knows: it is nil on exactly the cylinders that had no pod.
        Across the corpus's 342 exports with mixtures, 98 name exactly one transmitted
        cylinder and 244 name none, **not one names two**, non-nil `<TransmitterId>` agrees
        with a non-zero `<StartPressure>` on all 353 mixtures, and readings are present in
        precisely the 98.

        The other two shapes are not in that corpus and both have an answer that involves no
        guessing. Readings that no cylinder claims are evidence of a tank the mixture list
        does not name, which `converting.md` says becomes a cylinder carrying its pressures
        and nothing else. Readings that **two** cylinders claim are a question this format
        cannot answer — one channel, no key — so the channel is dropped and said to be,
        rather than attached to whichever came first.
        """
        if not len(pressure):
            return ()
        claiming = [cylinder for cylinder in cylinders if cylinder.transmitted]
        if len(claiming) == 1:
            return ((claiming[0].number, pressure),)
        if len(claiming) > 1:
            self.note(
                where,
                f"{len(claiming)} cylinders record a <TransmitterId> and the sample stream carries one "
                "unlabelled <Pressure>, so which readings are whose is not in the file; the pressure "
                "channel is dropped rather than attached to one of them",
                "dropped",
            )
            return ()
        if len(cylinders) >= MAX_CYLINDERS:
            self.note(
                where,
                "the samples carry tank pressures for a cylinder the export lists nowhere, and this dive "
                f"already describes the {MAX_CYLINDERS} cylinders that are read from one; the pressure "
                "channel is dropped",
                "dropped",
            )
            return ()
        # Evidence of a tank is evidence of a tank: the readings become a cylinder of their
        # own, carrying the channel and nothing else, rather than being attached to a
        # cylinder the file never said had a pod on it.
        added = _Cylinder(member={}, number=len(cylinders), transmitted=True)
        cylinders.append(added)
        self.note(
            where,
            "the samples carry tank pressures and no cylinder records a <TransmitterId>, so the readings "
            "arrive as a cylinder of their own with no gas, no size and no start or end pressure — the "
            "export records none of those for it",
            "absent",
        )
        return ((added.number, pressure),)

    def read_events(self, cylinders: list[_Cylinder]) -> list[dict[str, Any]]:
        """The dive's gas switches, off the cylinders that recorded them.

        `<Marks>` is deliberately not read, and it is the only other event-shaped block in
        the format. Its `<Type>` is an undocumented numeric code the corpus says plainly
        cannot be guessed at: 29 distinct values across 4 095 marks, and the one that
        appears in **every single export** — `257`, 503 of them, about 1.3 per dive — is
        not the shape of a bookmark a diver pressed. Mapping one onto §6.5's vocabulary
        would be a confident label over a number nobody has decoded — the same mistake as
        reading `<Type>1</Type>` on a `<DiveMixture>` as the cylinder's role. The same
        dives' app JSON spells its events out in words, so a diver who wants them has a
        file that says so.
        """
        return [
            {"time": second, "type": "gas_switch", "gas_number": cylinder.number}
            for cylinder in cylinders
            for second in cylinder.switches
        ]


def _date_time(raw: str) -> tuple[str, bool] | None:
    """`<StartTime>` as a §5.2 date-time and whether it stated its seconds, or `None`.

    The second half of the pair is what lets the caller report the one leniency this
    grammar carries — §5.2 requires the seconds and a stored value missing them would cost
    the whole dive — without asking the caller to re-parse the text to find out.

    `datetime.fromisoformat` is deliberately not the parser. On the Python floor this
    package supports, it accepts only a two- or six-digit fraction and rejects `.6` — which
    is a property of one interpreter version rather than of the data, and `.6` is exactly
    what this format writes. It is still used for the calendar check no pattern can make.
    """
    match = _TIMESTAMP.match(raw)
    if match is None:
        return None
    parts = match.groupdict()
    second = parts["second"] or "00"
    try:
        datetime(
            int(parts["date"][:4]),
            int(parts["date"][5:7]),
            int(parts["date"][8:]),
            int(parts["hour"]),
            int(parts["minute"]),
            int(second),
        )
    except ValueError:
        return None
    written = f"{parts['date']}T{parts['hour']}:{parts['minute']}:{second}{parts['fraction'] or ''}"
    return written, parts["second"] is not None
