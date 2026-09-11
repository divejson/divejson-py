"""Reading Subsurface's `.ssrf` into DiveJSON.

Subsurface is the dive log most divers who left a vendor application ended up in, and
`.ssrf` is its own save file rather than an export — so it holds everything Subsurface
knows, where its UDDF export holds what UDDF has room for. Both readers in this package
have been run over the same logbook, and the differences between the two documents are
Subsurface's export losing things rather than either reader disagreeing.

`docs/ssrf-mapping.md` is the prose companion: every attribute this module reads, every one
it deliberately does not, and why. What is true of every source format rather than of this
one — the note kinds, identity, the way a zero reads, the number bound, the sample axis,
the `<!DOCTYPE>` refusal, taking a child by its lowercased local name — lives in
`converter.py`, `series.py` and `xmlsource.py`, and this module inherits it.

Four decisions shape what is below.

**Every measurement carries its unit in the text, and the unit is read rather than
assumed.** `depth max='45.91 m'`, `duration='66:50 min'`, `cns='11%'`, `size='12.0 l'`,
`start='200.0 bar'`, `temp='22.4 C'`, `sac='6.471 l/min'` — one table maps each spelling to
the number in front of it, and a spelling the table does not carry is **refused and
reported** rather than converted by a factor no file has checked. That is the whole reason
this reader has no scale ambiguity of its own: UDDF's `<tankvolume>` and `<o2>` are numbers
whose units the file never states, and there is no such number here. This reader therefore
emits no `resolved` finding, and the only two kinds in its report are `absent` and
`dropped`.

**A dive has no id, so its identity is its position.** Subsurface keys a dive by its
computer's own dive id where there is one and by nothing at all otherwise; `@number` is the
diver's own numbering, which §6.2 says duplicates are legal in, so hashing it would hand
two dives one identity. The positional stand-in `converting.md` defines is what a dive
gets, reported as such. Sites do carry a `@uuid`, and it is read as an opaque string —
Subsurface writes one of them with a leading space.

**Two attributes are read and deliberately not carried**, which is a different thing from
an attribute this reader never looks at (`docs/ssrf-mapping.md` lists those). `@visibility`
is a five-star rating where §6.2's is metres, and `@sac` has no member in §6 at all. Each
is reported as `dropped`, because a diver looking for it in the converted document deserves
to be told where it went. `<divecomputer model>` was a third of these until §6.4b gave a
device a home: it is carried now, and the rationale that refused it is the same one that
keeps it off the gear list.

**A dive may carry one `<divecomputer>` per computer the diver wore, and each is a
recording** (§6.4a) in file order, the first primary — this is the only interchange format
here that carries two computers' records of one dive at all. What stays on the dive is the
first element's, which is the one place the plural recording forces a choice; `read_summary`
and `drop_summary` are where it is made.
"""

from __future__ import annotations

import re
import uuid as uuid_pkg
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

from .converter import (
    CENTIMETRES_PER_METRE,
    MAX_NAME,
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
    device,
    header,
    integer_of,
    recorded,
    recording,
    rounded,
)
from .series import Channel, SampleAxis
from .validate import validate_document
from .xmlsource import attribute, child, children, local_name, parse_xml, root_name, text

# The format id this adapter registers under, which is also the name of the directory a
# conformance corpus keeps its pairs in. The file's own root element is `<divelog>`, which
# is what the sniff matches — the id names the format, not the tag.
FORMAT = "ssrf"
ROOT = "divelog"

# uuid5(NAMESPACE_URL, "https://divejson.org/ns/ssrf"). Fixed forever: changing it would
# renumber every document any released version of this converter has ever produced, and
# `docs/ssrf-mapping.md` *Identity* records the value as normative for any port.
SSRF_ID_NAMESPACE = uuid_pkg.UUID("ef2248bf-087c-53cf-8a79-2f6e01f7c90e")

SECONDS_PER_MINUTE = Decimal(60)

# The one range check this format's own units do not make for us. §6.3 caps a cylinder
# pressure at 350 bar, and a reading past it is a source defect rather than a scale to
# reinterpret — there is no scale in doubt here.
MAX_CYLINDER_PRESSURE = Decimal(350)

# `M:SS`, which is the only shape Subsurface writes a time in: its writer formats
# `seconds / 60` and `seconds % 60` as `%u:%02u`, so the minutes are unbounded and the
# seconds are always two digits below 60. Anything else is not a time this reader will
# guess at.
_CLOCK = re.compile(r"\A(\d+):([0-5]\d)\Z")

# A measurement splits into a number and the unit written after it. The unit is optional
# because Subsurface writes some counts bare — `otu='31'`, `visibility='5'` — and the
# number is whatever is left, checked by `decimal_of` rather than by this pattern.
_MEASUREMENT = re.compile(r"\A(?P<number>\S+?)\s*(?P<unit>%|[A-Za-z][A-Za-z/]*)?\Z")

# A dive's `@date` and `@time`. Subsurface writes both to the digit, and the leniency here
# is for the seconds alone: a hand-edited file missing them costs a dive otherwise, and
# §5.2's grammar requires them. There is no offset in either pattern because there is none
# anywhere in the format — see `read_started_at`.
_DATE = re.compile(r"\A\d{4}-\d{2}-\d{2}\Z")
_TIME = re.compile(r"\A(?P<hour>\d{2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?(?P<fraction>\.\d+)?\Z")


def _clock(raw: str) -> Decimal | None:
    """`'66:50'` as 4010 seconds, or nothing for text that is not a `M:SS` time."""
    match = _CLOCK.match(raw)
    if match is None:
        return None
    minutes = decimal_of(match.group(1))
    if minutes is None:
        return None
    return minutes * SECONDS_PER_MINUTE + Decimal(match.group(2))


# Every unit spelling this reader knows, against the way the number in front of it is read.
# A measurement whose unit is not a key here is **dropped and reported** rather than read at
# a guessed scale, which is `converting.md`'s refuse-rather-than-guess rule applied to the
# one thing Subsurface states outright. Imperial spellings are the case that makes this
# concrete and are deliberately absent: no export in this repository's hand carries one, so
# a factor added for them would be a factor nobody has checked against a file — and a wrong
# factor here produces a document that validates perfectly and describes a dive nobody took.
#
# The empty string is a unit like any other: it is what a bare count is written in, and
# giving it a row means an unexpected suffix on one is refused by the same code path.
UNITS: dict[str, Callable[[str], Decimal | None]] = {
    "": decimal_of,  # `otu='31'`, `visibility='5'`, `number='45'`
    "m": decimal_of,  # `depth max='45.91 m'`
    "l": decimal_of,  # `cylinder size='12.0 l'`
    "bar": decimal_of,  # `cylinder start='200.0 bar'`
    "C": decimal_of,  # `temperature water='22.4 C'`
    "%": decimal_of,  # `cns='11%'`, `cylinder o2='32.0%'`
    "l/min": decimal_of,  # `sac='6.471 l/min'`
    "min": _clock,  # `duration='66:50 min'`, `sample time='0:10 min'`
}


class SsrfError(ConverterError):
    """The input could not be read as a Subsurface logbook."""


class MalformedSsrfError(SsrfError):
    """The input is not well-formed XML, or its root element is not `<divelog>`."""


class SsrfAdapter:
    """The registry's view of this reader: what it claims, and how it converts.

    An instance of this is what `registry.py` registers; everything else in this module is
    behind it.
    """

    format: str = FORMAT
    suffixes: tuple[str, ...] = (".ssrf",)
    namespace: uuid_pkg.UUID = SSRF_ID_NAMESPACE

    def sniff(self, head: bytes) -> bool:
        """Whether a bounded head of bytes opens a Subsurface logbook.

        The root element name, and nothing else. `@program` is deliberately not consulted:
        Subsurface-mobile writes its own name there, and a file this reader can read is one
        it can read whatever the writer called itself.
        """
        return root_name(head) == ROOT

    def convert(self, data: bytes, *, exported_at: datetime, scope: Scope) -> Conversion:
        """Convert one `.ssrf` document into DiveJSON.

        `data` is **bytes**, not text: an XML document declares its own encoding, and a
        file that says `encoding="ISO-8859-1"` has to be decoded by the parser that read
        that declaration. Handing `ElementTree` a `str` carrying one is a `ValueError`
        anyway.

        Raises `DoctypeRefusedError`, `MalformedSsrfError` or `NonConformingOutputError`.
        """
        root = parse_xml(data, root=ROOT, malformed=MalformedSsrfError)
        return _Converter(root, exported_at=exported_at, scope=scope).run()


SSRF = SsrfAdapter()


class _Converter:
    def __init__(self, root: ET.Element, *, exported_at: datetime, scope: Scope) -> None:
        self.root = root
        self.exported_at = exported_at
        self.scope = scope
        self.notes: list[Note] = []
        # The claimed-UUID table inside is shared with the rest of the archive this file
        # came from, if it came from one, so that a record two members both define is
        # written once and referred to by both.
        self.identities = Identities(SSRF_ID_NAMESPACE, scope, self.note)
        self.site_ids: set[str] = set()
        self.site_uuids: dict[str, str] = {}

    # -- reporting ---------------------------------------------------------------

    def note(self, where: str, message: str, kind: NoteKind) -> None:
        """One line of the report, at a path into the source this conversion read."""
        self.notes.append(Note(self.scope.where(where), message, kind))

    def capped(self, value: str, limit: int, where: str, member: str) -> str:
        return capped(value, limit, note=self.note, where=where, member=member)

    # -- measurements ------------------------------------------------------------

    def measure(self, raw: str | None, unit: str, where: str, member: str) -> Decimal | None:
        """One attribute as a number in `unit`, or nothing at all, reporting either way.

        The unit written in the file has to be the one this member is read in. A different
        one is a real reading — an imperial export's `'150.6 ft'` — that this reader will
        not convert by a factor no file in hand has checked, so it is dropped and named.
        Reading it at the metric scale would put a dive at 150 metres.
        """
        if raw is None:
            return None
        # None of the three messages carries the recorded value, deliberately. A file
        # written in one unit produces one of these per record — 431 of them for a sample
        # channel — and `Conversion.grouped()` groups on the message, so a value in the
        # text would turn one habit of the file into hundreds of separate report lines.
        match = _MEASUREMENT.match(raw)
        if match is None:
            self.note(where, f"{member} is not a number with a unit after it; dropped", "dropped")
            return None
        found = match.group("unit") or ""
        if found != unit:
            self.note(
                where,
                f"{member} is recorded in {found or 'no unit'}, where this reader reads it in "
                f"{unit or 'no unit'}; dropped rather than read at a scale nothing states",
                "dropped",
            )
            return None
        # `UNITS[unit]` on a unit this module never registered is a `KeyError`, which is the
        # point: it is this adapter asking for a scale nothing defines, not a source defect.
        value = UNITS[unit](match.group("number"))
        if value is None:
            self.note(
                where,
                f"{member} is recorded in {unit or 'no unit'} with something that is not a number in front of "
                "it; dropped",
                "dropped",
            )
        return value

    def positive(self, value: Decimal | None, where: str, member: str, source: str) -> Decimal | None:
        """A measurement the format records only when it is above zero.

        Subsurface writes `max='0.0 m'` for a dive whose depth it never had, so reading the
        zero back as a measurement would turn "not recorded" into "the surface". Which way
        a zero reads is the schema's decision rather than this module's, so `member` names
        the DiveJSON member the value is headed for and `recorded` asks it.
        """
        if value is None or recorded(value, record="dive", member=member):
            return value
        self.note(where, f"{source} is {value}, which the format records only when positive; read as not recorded", "absent")
        return None

    # -- the run -----------------------------------------------------------------

    def run(self) -> Conversion:
        sites = self.read_sites()
        dives = self.read_dives()

        document: dict[str, Any] = header(self.exported_at)
        for member, rows in (("dives", dives), ("sites", sites)):
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
        *this* document — and that is the converter. `@version` is the save format's own
        version, `@program` the application that wrote it; Subsurface records its own
        release nowhere in the file, so `source_generator` carries a name and no version.

        There is no `extensions.divejson.inferred` here, and its absence is a statement
        rather than an omission: this reader computes no value from other readings, so it
        raises no `inferred` finding and has no derived member to label (spec §5.4). The
        two kinds its report can carry are `absent` and `dropped`.
        """
        provenance: dict[str, Any] = {"converted_from": FORMAT}
        version = attribute(self.root, "version")
        if version:
            provenance["ssrf_version"] = version
        program = attribute(self.root, "program")
        if program:
            provenance["source_generator"] = {"name": program}
        return provenance

    # -- sites -------------------------------------------------------------------

    def read_sites(self) -> list[dict[str, Any]]:
        """`<divesites><site>` as Dive Site records.

        A site's `@uuid` is Subsurface's own eight-hex-digit key rather than a real UUID,
        so it is hashed like any other source id — and read as an opaque string, since one
        of them in the file this reader was built against is `" ff47210"`, with a leading
        space that the dives' `@divesiteid` carries too.
        """
        sites: list[dict[str, Any]] = []
        for index, element in enumerate(children(child(self.root, "divesites"), "site")):
            where = f"site/{index}"
            source_id = attribute(element, "uuid")
            if source_id:
                self.site_ids.add(source_id)
            name = attribute(element, "name")
            if not name:
                self.note(
                    where,
                    "the site has no name, which the format requires of one; it is dropped along with the "
                    "references to it, because a name cannot be invented (spec §6.10)",
                    "dropped",
                )
                continue
            claimed, carried = self.identities.for_record("site", source_id, where, index)
            if claimed is None:
                continue
            if source_id:
                # Recorded before the row is written, and whether or not it is: a repeat of
                # another archive member's record is not carried again, and this file's
                # references to it still have to resolve to the one that is.
                self.site_uuids[source_id] = claimed
            if not carried:
                continue
            sites.append({"uuid": claimed, "name": self.capped(name, MAX_NAME, where, "the site name")})
        return sites

    # -- dives -------------------------------------------------------------------

    def dive_elements(self) -> list[ET.Element]:
        """Every `<dive>` under `<dives>`, in document order, trips walked through.

        A `<trip>` groups dives that share a journey, and this reader carries the dives and
        not the grouping — see `docs/ssrf-mapping.md`. Walking through it rather than past
        it is what keeps a trip's dives from disappearing along with the record.
        """
        found: list[ET.Element] = []
        trips = 0
        # `is not None`, never a truth test: an `Element` with no children is falsy today
        # and `ElementTree` warns that it will not be.
        dives = child(self.root, "dives")
        if dives is None:
            return found
        for element in dives:
            name = local_name(element)
            if name == "dive":
                found.append(element)
            elif name == "trip":
                self.note(
                    f"trip/{trips}",
                    "the source groups these dives into a trip; the dives are carried and the grouping is not, "
                    "because no Subsurface file in hand carries a <trip> to read its dates and place from "
                    "(spec §6.8)",
                    "dropped",
                )
                trips += 1
                found.extend(children(element, "dive"))
        return found

    def read_dives(self) -> list[dict[str, Any]]:
        dives = []
        for index, element in enumerate(self.dive_elements()):
            dive = self.read_dive(element, index)
            if dive is not None:
                dives.append(dive)
        return dives

    def read_dive(self, element: ET.Element, index: int) -> dict[str, Any] | None:
        where = f"dive/{index}"

        started_at = self.read_started_at(element, where)
        if started_at is None:
            return None
        # `<dive>` carries no id of any kind, so every dive takes the positional stand-in
        # `converting.md` defines — and the note that says so, since an identity that moves
        # when the file's order changes is a fact a diver may need (spec §5.3).
        claimed, carried = self.identities.for_record("dive", None, where, index)
        if claimed is None or not carried:
            return None

        dive: dict[str, Any] = {"uuid": claimed}
        number = integer_of(self.measure(attribute(element, "number"), "", where, "<dive number>"))
        if number is not None:
            dive["dive_number"] = number
        dive["started_at"] = started_at

        duration = integer_of(self.measure(attribute(element, "duration"), "min", where, "<dive duration>"))
        if duration is not None:
            if recorded(duration, record="dive", member="duration"):
                dive["duration"] = duration
            else:
                self.note(where, f"<dive duration> is {duration} seconds; the format records a duration only when it is positive", "absent")

        notes = text(child(element, "notes"))
        if notes:
            dive["notes"] = self.capped(notes, MAX_NOTES, where, "the note")

        # `cns='11%'` and `otu='31'`: a percentage and a bare count, both of them the dive's
        # *end* figure. Subsurface records no starting pair, which is why `cns_start` and
        # `otu_start` have no source here.
        for member, source, unit in (("cns_end", "cns", "%"), ("otu_end", "otu", "")):
            value = self.measure(attribute(element, source), unit, where, f"<dive {source}>")
            if value is None:
                continue
            if recorded(value, record="dive", member=member):
                dive[member] = float(value)
            else:
                self.note(where, f"<dive {source}> is {value}, which the format records only from zero up; dropped", "dropped")

        if attribute(element, "visibility") is not None:
            self.note(
                where,
                "visibility is a five-star rating here and metres in this format, so the rating is dropped "
                "rather than read as a distance — Subsurface's own UDDF export of the same logbook writes it "
                "as metres, and the two documents differ there by design (spec §6.2)",
                "dropped",
            )
        if self.measure(attribute(element, "sac"), "l/min", where, "<dive sac>") is not None:
            self.note(
                where,
                "the dive records a surface air consumption, which this format has no member for; dropped",
                "dropped",
            )

        site_uuid = self.site_reference(attribute(element, "divesiteid"), where)
        if site_uuid:
            dive["site_uuids"] = [site_uuid]

        cylinders = self.read_cylinders(element, where)
        if cylinders:
            dive["cylinders"] = cylinders

        recordings = self.read_recordings(element, dive, where)
        if recordings:
            dive["recordings"] = recordings
        return dive

    def read_started_at(self, element: ET.Element, where: str) -> str | None:
        """`@date` and `@time` as a §5.2 local wall clock, with no offset supplied.

        **`.ssrf` records no time zone anywhere** — not on a dive, not in `<settings>`, not
        at the root — so every conversion of one carries the wall clock alone and says so.
        Supplying an offset is the failure §5.2 exists to prevent, and it would be the
        easiest thing in the world to do wrongly here: the same logbook's other exports
        carry one, and taking it from there would be this converter asserting a zone the
        file does not.

        The two halves are composed rather than concatenated, because §5.2's grammar
        requires the seconds and a hand-edited `time='11:49'` would otherwise reach the
        document and fail its own validation.
        """
        date = attribute(element, "date")
        clock = attribute(element, "time")
        if date is None:
            self.note(
                where,
                "the dive records no date, and the format requires a start time; the dive is dropped (spec §6.2)",
                "dropped",
            )
            return None
        if clock is None:
            self.note(where, "the dive records a date with no time of day; read as midnight", "absent")
            clock = "00:00:00"

        parts = _TIME.match(clock)
        if _DATE.match(date) is None or parts is None:
            self.note(where, "the dive's date and time are not a date and time; the dive is dropped (spec §6.2)", "dropped")
            return None
        if parts["second"] is None:
            self.note(where, "the dive's time of day records no seconds; read as :00", "absent")
        started_at = f"{date}T{parts['hour']}:{parts['minute']}:{parts['second'] or '00'}"
        try:
            # Calendar validity, which no pattern can check: Subsurface will not write a
            # 30th of February and a file someone edited by hand might.
            datetime.fromisoformat(started_at)
        except ValueError:
            self.note(where, "the dive's date and time are not a date and time; the dive is dropped (spec §6.2)", "dropped")
            return None

        self.note(
            where,
            "the source records no UTC offset on the dive's start time; the wall clock travels alone (spec §5.2)",
            "absent",
        )
        return started_at + (parts["fraction"] or "")

    def site_reference(self, ref: str | None, where: str) -> str | None:
        """`@divesiteid` against the sites this file defined.

        Three answers, and the middle one is why the ids are collected separately from the
        rows: a site the converter had to drop for having no name *is* defined in the file,
        and saying it is not would send a diver looking for a typo that is not there.
        """
        if ref is None:
            return None
        if ref in self.site_uuids:
            return self.site_uuids[ref]
        if ref in self.site_ids:
            self.note(where, f"the dive names the site {ref!r}, which this converter could not carry; the reference is dropped", "dropped")
        else:
            self.note(where, f"the dive names the site {ref!r}, which <divesites> does not define; the reference is dropped", "dropped")
        return None

    # -- cylinders ---------------------------------------------------------------

    def read_cylinders(self, element: ET.Element, where: str) -> list[dict[str, Any]]:
        """A dive's `<cylinder>` elements as §6.3 Cylinders.

        Nothing on a `.ssrf` dive refers to a cylinder by number — this reader maps no
        pressure channel and no gas switch — so no `gas_number` is written. §6.3 calls it
        "a label, not an array index", and asserting a numbering nothing depends on would
        be this converter inventing one.
        """
        cylinders: list[dict[str, Any]] = []
        for index, tank in enumerate(children(element, "cylinder")):
            tank_where = f"{where}/cylinder/{index}"
            cylinder: dict[str, Any] = {}

            litres = self.measure(attribute(tank, "size"), "l", tank_where, "<cylinder size>")
            if litres is None:
                self.note(
                    tank_where,
                    "the source records no cylinder size; the cylinder carries its gas and pressures without "
                    "one (spec §6.3)",
                    "absent",
                )
            elif recorded(litres, record="cylinder", member="volume"):
                cylinder["volume"] = float(litres)
            else:
                self.note(tank_where, f"<cylinder size> is {litres} litres; the format records a size only when positive", "absent")

            start = self.pressure(attribute(tank, "start"), tank_where, "<cylinder start>")
            end = self.pressure(attribute(tank, "end"), tank_where, "<cylinder end>")
            if start is not None and not recorded(start, record="cylinder", member="start_pressure"):
                # §6.3 is explicit: a recorded zero start pressure is a device's
                # absent-marker rather than a measurement, and writers must not emit one.
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

            self.read_mix(tank, cylinder, tank_where)
            cylinders.append(cylinder)
        return cylinders

    def pressure(self, raw: str | None, where: str, member: str) -> Decimal | None:
        bar = self.measure(raw, "bar", where, member)
        if bar is None:
            return None
        if not 0 <= bar <= MAX_CYLINDER_PRESSURE:
            self.note(where, f"{member} is {bar} bar, outside the 0 to 350 the format allows; dropped", "dropped")
            return None
        return bar

    def read_mix(self, tank: ET.Element, cylinder: dict[str, Any], where: str) -> None:
        """`@o2` and `@he` as percentages, which is how Subsurface writes them.

        Both carry their `%` in the text, so neither is UDDF's fraction-or-percent
        ambiguity: there is nothing here for a magnitude test to settle.
        """
        blend: dict[str, float] = {}
        for member, source in (("oxygen", "o2"), ("helium", "he")):
            percent = self.measure(attribute(tank, source), "%", where, f"<cylinder {source}>")
            if percent is None:
                continue
            if not 0 <= percent <= 100:
                self.note(where, f"<cylinder {source}> is {percent} percent, outside the 0 to 100 a mix can be; dropped", "dropped")
                continue
            blend[member] = float(percent)
        if blend.get("oxygen", 0.0) + blend.get("helium", 0.0) > 100:
            self.note(where, "oxygen and helium sum above 100 percent, which no mix can; both are dropped (spec §6.3)", "dropped")
            return
        if not blend:
            self.note(where, "the source records no gas for this cylinder; absent means not recorded, never air (spec §6.3)", "absent")
        cylinder.update(blend)

    # -- the dive computer -------------------------------------------------------

    def read_recordings(
        self, element: ET.Element, dive: dict[str, Any], where: str
    ) -> list[dict[str, Any]]:
        """Every `<divecomputer>` on the dive as a §6.4a Recording, in file order.

        This is the format that made the case for the member: it is the only interchange
        format here that carries two computers' records of one dive at all. One element
        with samples becomes a recording with a profile; one without becomes a device-only
        recording; **one that yields neither yields no recording**, §6.4a forbidding a
        recording that carries nothing — which the corpus reaches in
        `fixtures/ssrf/trip-grouping.ssrf`, whose dives 43 and 44 carry a
        `<divecomputer last-manual-time='…'>` with a `<depth>` on it and nothing this
        section can hold. Its dive 42 is the ordinary shape beside them, samples and all.

        The dive's own `max_depth`, `avg_depth` and `bottom_temperature` come from the
        **first element in file order** and never from a later one — see `read_summary`.
        That rule is keyed on the element rather than on the primary recording, and the
        two are not always the same one: those two elements become nothing while still
        supplying their dive's three figures.
        """
        recordings: list[dict[str, Any]] = []
        for index, computer in enumerate(children(element, "divecomputer")):
            here = f"{where}/divecomputer/{index}"
            if index == 0:
                self.read_summary(computer, dive, where)
            else:
                self.drop_summary(computer, here)
            built = recording(
                device=self.read_device(computer, here),
                started_at=self.read_recording_start(computer, dive["started_at"], here),
                profile=self.read_profile(computer, here),
            )
            if built is not None:
                recordings.append(built)
        return recordings

    def read_device(self, computer: ET.Element, where: str) -> dict[str, Any] | None:
        """`@model` and the two `<extradata>` keys as a §6.4b Device.

        `@model` was read and refused until this section existed, and the reason it was
        refused still holds: it names the source of one dive's telemetry rather than an
        item the diver owns, so it is a device and never a gear item. Subsurface's own
        UDDF export of the same logbook carries no `<divecomputer>` equipment element for
        it either.

        `Serial` and `FW Version` are the keys libdivecomputer's download writes what it
        read off the hardware under, and they are the only route to either member in this
        format — neither has an attribute of its own. `@deviceid` is Subsurface's own key
        for the computer rather than the serial §6.4b asks for, and `@diveid` keys a dive
        rather than counting one, so neither is the device counter. This format carries no
        device name at all: nothing in a `.ssrf` records what the diver called their
        computer.
        """
        extradata = {
            attribute(extra, "key"): attribute(extra, "value")
            for extra in children(computer, "extradata")
        }
        return device(
            {
                "model": attribute(computer, "model"),
                "serial": extradata.get("Serial"),
                "firmware": extradata.get("FW Version"),
            },
            note=self.note,
            where=where,
            labels={
                "model": "<divecomputer model>",
                "serial": "the Serial <extradata>",
                "firmware": "the FW Version <extradata>",
            },
        )

    def read_recording_start(
        self, computer: ET.Element, dive_start: str, where: str
    ) -> str | None:
        """`<divecomputer @date @time>` where it states a start of its own.

        §6.4a reads an absent `started_at` as the dive's, so an element agreeing with its
        dive writes nothing — which is every file in hand. Where it differs, writing it is
        what keeps a second computer's samples on their own axis: that computer started
        when its diver's wrist went under, not when the first one's did.

        Composed rather than concatenated for `read_started_at`'s reason, and silent for a
        different one: an element with no `@date` is not a defect here, it is the ordinary
        case, and the dive's own start has already been read and reported on.
        """
        date, clock = attribute(computer, "date"), attribute(computer, "time")
        if date is None or clock is None:
            return None
        parts = _TIME.match(clock)
        if _DATE.match(date) is None or parts is None:
            self.note(where, "the computer's own date and time are not a date and time; dropped", "dropped")
            return None
        started_at = f"{date}T{parts['hour']}:{parts['minute']}:{parts['second'] or '00'}"
        try:
            datetime.fromisoformat(started_at)
        except ValueError:
            self.note(where, "the computer's own date and time are not a date and time; dropped", "dropped")
            return None
        started_at += parts["fraction"] or ""
        return None if started_at == dive_start else started_at

    def drop_summary(self, computer: ET.Element, where: str) -> None:
        """A later element's `<depth>` and `<temperature>`, reported rather than read.

        One finding per element that carries either. Two computers routinely differ on a
        maximum depth — `fixtures/ssrf/refusals.ssrf`'s second element states a deeper one,
        its own mean and a warmer temperature than the first — and §6.2 gives the dive one
        of each, so silently preferring one of them would put an unmarked choice in a
        logbook. Nothing is averaged and nothing is reached past.
        """
        if child(computer, "depth") is None and child(computer, "temperature") is None:
            return
        self.note(
            where,
            "the dive's greatest depth, mean depth and water temperature are the first <divecomputer>'s, "
            "and this one records its own; they are dropped rather than averaged or preferred (spec §6.2)",
            "dropped",
        )

    def read_summary(self, computer: ET.Element, dive: dict[str, Any], where: str) -> None:
        """`<depth>` and `<temperature>` as the dive's own scalars.

        These are readings the computer reported rather than summaries of the samples, and
        the difference is visible in the file this reader was built against: its first dive
        records `max='45.91 m'` where the deepest sample is 45.82 m. Nothing here is
        computed, so nothing here is `inferred`.

        The **first** element in file order supplies all three, and a value it does not
        carry is not taken from a later one: an absence on the first element is what that
        computer recorded, and reaching past it would be the same silent choice
        `drop_summary` refuses in the other direction.
        """
        depth = child(computer, "depth")
        max_depth = self.positive(self.measure(attribute(depth, "max"), "m", where, "<depth max>"), where, "max_depth", "<depth max>")
        avg_depth = self.positive(self.measure(attribute(depth, "mean"), "m", where, "<depth mean>"), where, "avg_depth", "<depth mean>")
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

        water = self.measure(attribute(child(computer, "temperature"), "water"), "C", where, "<temperature water>")
        if water is not None:
            dive["bottom_temperature"] = float(water)

    # -- profile -----------------------------------------------------------------

    def read_profile(self, computer: ET.Element, where: str) -> dict[str, Any] | None:
        """`<sample>` as a Profile.

        Subsurface writes a sample's depth every time and its other readings **only when
        they change**, so a dive with 431 depths carries 29 temperatures and the two sit on
        their own axes — which is `converting.md`'s no-padding rule meeting the writer that
        taught it. The axis itself is `series.SampleAxis`, shared with every other format;
        what is below is which attribute carries which channel.
        """
        samples = children(computer, "sample")
        if not samples:
            return None

        axis = SampleAxis(self.note, where, noun="sample", time_member="time")
        for sample in samples:
            axis.offer(integer_of(self.measure(attribute(sample, "time"), "min", where, "<sample time>")), sample)

        depth = Channel("depth")
        temperature = Channel("temperature")
        for second, sample in axis.ordered():
            metres = self.measure(attribute(sample, "depth"), "m", where, "<sample depth>")
            if metres is not None:
                depth.record(second, rounded(metres * CENTIMETRES_PER_METRE))
            celsius = self.measure(attribute(sample, "temp"), "C", where, "<sample temp>")
            if celsius is not None:
                temperature.record(second, rounded(celsius * TENTHS_PER_UNIT))

        return axis.profile({"depth": depth, "temperature": temperature})
