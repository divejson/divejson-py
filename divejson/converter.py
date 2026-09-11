"""The half of a converter that is not any one format's.

Every adapter this package registers reads a different syntax and produces the same two
things: a DiveJSON document, and a report of what the source did not carry. The rules
governing the second — and the errors, the identities, the number and geometry readings
and the schema-driven ones behind the first — are the portable part, so they live here and
are inherited rather than restated once per format. `docs/converting.md` in the
specification repository is the prose companion, and this module is not allowed to
disagree with it.

The sections below the policy classes carry the rest of what an adapter inherits rather
than rewrites: `decimal_of` and its representability bound, `rounded`'s half-away-from-zero
convention and the two §6.5 channel scales; `capped`; `Identities`; and `position`. An
adapter that reimplements one of these gets it subtly different, which is the failure this
module exists to prevent — the bound and the Null Island rule were each written once for
one format and are true of every format.

These are worth reading before an adapter is written against them.

**One error base.** Everything a converter can raise is a `ConverterError`, so a caller
catches one class and gets a message it can show a diver. The per-format subclasses exist
for a caller that cares which reader refused; nothing in this package needs them.

**A writer's result has the same shape as a reader's.** `Written` is `Conversion` with
bytes where the document was, because the loss a writer has to report is the same kind of
news as the loss a reader reports and a caller should not need two renderers for it. What
the note kinds mean going *out* is each writer's own document to state — `converting.md`
defines them for the way in, and this module holds only the vocabulary.

**A note has a kind, and exactly one of the four means the value was computed.** `kind`
says what the report is telling the diver — `absent` for what the source never recorded,
`inferred` for a value this converter computed from readings the source *did* record,
`resolved` for a recorded value whose scale, units or **meaning** the source left
ambiguous and this converter had to decide, `dropped` for what was recorded and could not
be carried.

`inferred` and `resolved` are the pair worth separating, because a diver reading one
report line has to know whether the number in the document is the converter's arithmetic
or the source's own. That is also what keeps `extensions.divejson.inferred` exact: it
lists the members whose **value** was computed, which is what §5.4 asks a writer to label
as derived, so every member an `inferred` note is about is on that list and every member
on that list has an `inferred` note. A maximum depth taken from the depth samples of a
file that recorded none is `inferred`, and the document lists it. A `<tankvolume>` read as
litres rather than cubic metres is `resolved` and lists nothing — the number is the
source's own and only its scale was decided, which is a unit conversion like Kelvin to
Celsius rather than a derivation. So is a timestamp a generator stamps `Z` while meaning
the wall clock in front of the diver: nothing about the digits says which was meant, so
that one is settled on the writer rather than on the value (`uddf.py`'s generator table),
and the digits written are still the source's.

**Identity is shared across an archive's members, and position is not.** A `Scope` carries
the member name a conversion sits under and the UUIDs the whole upload has already handed
out, each with the member that claimed it. Two members whose records carry no ids of their
own do not collide, because the positional stand-in is prefixed by the member name. And a
record two members *both* define — every per-dive export repeats the site and the gear it
used — is one record: the first member carries its row, the rest resolve their references
to it and add nothing. A repeat inside **one** file is the different thing, and stays what
it always was: a source defect, dropped and reported, because two records in one file
cannot share an identity.

**Which way a zero reads is the schema's decision, not the adapter's.** `recorded` asks the
member's own constraint: `exclusiveMinimum: 0` means a zero was a placeholder the writer
had to put somewhere, `minimum: 0` means it was an answer. An adapter that hard-coded the
comparison would get it right for `max_depth` and wrong for `weight`, and the difference
between those two is a diver's "no lead" turning into "unknown".
"""

from __future__ import annotations

import re
import sys
import uuid as uuid_pkg
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import cache
from typing import Any, Literal

from . import SPEC_VERSION, __version__
from .validate import Issue, load_schema

__all__ = [
    "CENTIMETRES_PER_METRE",
    "DEVICE_CAPS",
    "INFERRED",
    "MAX_MAGNITUDE",
    "MAX_MODEL_NAME",
    "MAX_NAME",
    "MAX_NOTES",
    "PRODUCER_KEY",
    "TENTHS_PER_UNIT",
    "Claimed",
    "Conversion",
    "ConverterError",
    "DoctypeRefusedError",
    "Identities",
    "MalformedArchiveError",
    "NonConformingOutputError",
    "Note",
    "NoteGroup",
    "NoteKind",
    "Reporter",
    "Scope",
    "SourceTooLargeError",
    "UnsupportedSourceError",
    "Written",
    "capped",
    "channel_floor",
    "deco_model",
    "decimal_of",
    "device",
    "grouped",
    "header",
    "integer_of",
    "position",
    "profile_members",
    "record_inferred",
    "recorded",
    "recording",
    "rounded",
    "zero_is_an_answer",
]

# The producer key this converter writes its own provenance under (spec §5.5). The
# specification's own tools are the established product name here, the way `opendiving`
# is the reference implementation's.
PRODUCER_KEY = "divejson"

# What `generator.name` says of every document this package produces, whichever adapter
# read the source. §4 defines `generator` as what produced *this* document, and that is
# the converter rather than the file it read.
GENERATOR_NAME = "divejson convert"

# The member of the provenance block that lists derived values (spec §5.4). Written only
# when it is non-empty, so a conversion that computed nothing produces exactly the
# document it produced before this member existed.
INFERRED = "inferred"


class ConverterError(Exception):
    """A source could not be converted.

    The one class a caller catches. Everything below is a refinement for a caller that
    wants to know which reader gave up and why; `str()` of any of them is a sentence a
    diver can act on, because the message reaches an import screen.
    """


class UnsupportedSourceError(ConverterError):
    """No registered reader claims these bytes, or an archive's members disagree."""


class SourceTooLargeError(ConverterError):
    """The source is past a cap the caller set, and was refused rather than read."""


class MalformedArchiveError(ConverterError):
    """The bytes carry the archive magic and are not a readable archive."""


class DoctypeRefusedError(ConverterError):
    """The document carries a `<!DOCTYPE>` declaration, which this reader refuses.

    Not any one format's: spec §9 has readers dereference nothing they find in a document,
    and every XML source this package reads goes through the same parse target, so the
    refusal is inherited rather than re-implemented per format.
    """


class NonConformingOutputError(ConverterError):
    """The converter produced a document `divejson validate` rejects.

    Always a bug in this package rather than a property of the source: every way a source
    can be wrong is supposed to resolve to an omission and a note. It carries the issues,
    so the failure names itself.
    """

    def __init__(self, issues: list[Issue]) -> None:
        self.issues = issues
        super().__init__("; ".join(str(issue) for issue in issues))


NoteKind = Literal["absent", "inferred", "resolved", "dropped"]

# In the order a report reads best, which is also how much of the value the source itself
# supplied: nothing at all, the readings it was computed from, the value with its reading
# left open, and the whole thing, uncarriable.
NOTE_KINDS: tuple[NoteKind, ...] = ("absent", "inferred", "resolved", "dropped")

# How the helpers below add a line to the report they were called from. An adapter's own
# `note` method, which knows the archive member the path belongs under.
Reporter = Callable[[str, str, NoteKind], None]


@dataclass(frozen=True, slots=True)
class Note:
    """One thing the source did not carry, or that this converter had to decide.

    `where` is a path into the **source** document — `dive/0`, `dive/0/tankdata/1`,
    `site/3`, or `$` for the file itself — because that is where a diver looking for the
    missing value has to go. Indices are zero-based and count records of that kind in
    document order. Under an archive the path is prefixed by the member it came from.

    `kind` is required rather than defaulted: a note whose kind nobody chose would be
    filed under whichever value looked harmless, and the kinds are what a reader groups
    the report by.
    """

    where: str
    message: str
    kind: NoteKind

    def __str__(self) -> str:
        return f"{self.where}: {self.message}"


@dataclass(frozen=True, slots=True)
class NoteGroup:
    """Notes sharing a kind and a message, with every place they were raised."""

    kind: NoteKind
    message: str
    wheres: list[str]


def grouped(notes: Sequence[Note]) -> list[NoteGroup]:
    """Notes as groups of `(kind, message, wheres)`, in first-seen order.

    One habit produces one note per record — eight dives with no UTC offset are eight
    notes — and a thousand-dive logbook would bury the interesting ones under them.
    Grouping is a presentation concern, so it lives here rather than in the data.

    Keyed on the kind as well as the message, because a caller renders the two differently
    and a group carrying both would have to pick one.
    """
    groups: dict[tuple[NoteKind, str], NoteGroup] = {}
    for note in notes:
        key = (note.kind, note.message)
        if key not in groups:
            groups[key] = NoteGroup(note.kind, note.message, [])
        groups[key].wheres.append(note.where)
    return list(groups.values())


@dataclass(frozen=True, slots=True)
class Conversion:
    """A converted document and everything the conversion could not carry."""

    document: dict[str, Any]
    notes: tuple[Note, ...]

    def grouped(self) -> list[NoteGroup]:
        return grouped(self.notes)


@dataclass(frozen=True, slots=True)
class Written:
    """A document written out in another format, and what that format could not hold.

    The mirror of `Conversion`, and deliberately the same shape: a caller that renders one
    report renders the other with the same code, and the CLI does. What differs is which
    way the loss runs — a `Conversion`'s notes are about a source file this package read,
    a `Written`'s are about the DiveJSON document it was handed — so a `Written` note's
    `where` is a path into that **document**: `dives/0`, `dives/0/cylinders/1`, `$`.
    Each writer's own document says what its kinds mean on this side.
    """

    data: bytes
    notes: tuple[Note, ...]

    def grouped(self) -> list[NoteGroup]:
        return grouped(self.notes)


# Every UUID an upload has handed out, against the archive member that claimed it and the
# path it was claimed at. The member is what separates "another file in this archive
# already carries this record" from "this file names two records the same", which are
# opposite answers: the first resolves references to the record that is carried, the second
# drops a record that cannot be.
Claimed = dict[str, tuple[str | None, str]]


@dataclass(frozen=True, slots=True)
class Scope:
    """Where one conversion sits inside the upload that produced it.

    A bare document is one conversion: no member name, and identities of its own. A member
    of an archive shares `claimed` with its siblings, and prefixes both its `where` paths
    and its **positional** identities with its own name, so two members whose records carry
    no ids do not collide. A record the source *did* give an id is deliberately not
    prefixed — two members naming one id are naming one record, and that is the point.
    """

    member: str | None = None
    claimed: Claimed = field(default_factory=dict)

    def where(self, path: str) -> str:
        return path if self.member is None else f"{self.member}/{path}"

    def positional(self, index: int) -> str:
        """The stand-in source id for a record the source gave none."""
        return f"#{index}" if self.member is None else f"{self.member}#{index}"

    @property
    def validates_alone(self) -> bool:
        """Whether this conversion's own document is the one that gets written.

        The converter validates its own output before writing it, and for a member of an
        archive that output is an intermediate: the merged logbook is what is written, and
        a member whose dive refers to a site *another* member carries cannot validate on
        its own — its references close only after the merge. The registry validates there
        instead, so the rule is kept once over the document that leaves the library.
        """
        return self.member is None


def header(exported_at: datetime) -> dict[str, Any]:
    """The four members every converted document opens with, in §4's order.

    `format` and `version` are the two §4 requires first and second. `exported_at` and
    `generator` are the two a converted document asserts about its own *run* rather than
    about the source, which is why `divejson.compared` drops exactly those before a
    fixture comparison.
    """
    return {
        "format": "divejson",
        "version": SPEC_VERSION,
        "exported_at": exported_at.isoformat(timespec="seconds"),
        "generator": {"name": GENERATOR_NAME, "version": __version__},
    }


def record_inferred(provenance: dict[str, Any], members: list[str]) -> None:
    """List the members whose value this converter computed, if it computed any.

    Written only when the list is non-empty (spec §5.4 asks a writer to label a derived
    value, and says nothing about announcing that there are none), so a conversion that
    infers nothing produces exactly the document it would have produced without this
    member — which is what lets a reader land without regenerating a single expectation.
    """
    if members:
        provenance[INFERRED] = members


@cache
def _floor(record: str, member: str) -> tuple[Decimal, bool] | None:
    """One member's lower bound from the schema, and whether the bound is exclusive.

    Read out of the schema rather than restated here: the constraint is normative, it
    already differs between members that look alike — `start_pressure` excludes zero and
    `end_pressure` does not — and a copy in this file would be a second place for it to
    be wrong.
    """
    definition = load_schema()["$defs"][record]["properties"][member]
    if "exclusiveMinimum" in definition:
        return Decimal(str(definition["exclusiveMinimum"])), True
    if "minimum" in definition:
        return Decimal(str(definition["minimum"])), False
    return None


@cache
def _cap(record: str, member: str) -> Decimal | None:
    """One member's upper bound from the schema, or nothing where it has none.

    The mirror of `_floor`, and here for the same reason: §6.4c's gradient factors are a
    whole percent from 0 to 100, and a converter carrying that 100 as a constant of its own
    would be a second place for the range to be wrong. `recorded` deliberately asks only
    about the floor — a value above a ceiling is a different report line from one below a
    floor — so a member with both keeps its own range check and reads the ceiling here.
    """
    definition = load_schema()["$defs"][record]["properties"][member]
    if "maximum" in definition:
        return Decimal(str(definition["maximum"]))
    return None


@cache
def profile_members() -> tuple[str, ...]:
    """§6.4's Profile members in the section's own order, off the schema.

    A converted profile reads down the section the way a converted dive does, and which
    member comes where is the schema's to say. The alternative is each adapter listing its
    channels in the order it happens to build them, which was harmless while there were
    three of them and is a reordered corpus the day a fourth lands between two of them.
    """
    return tuple(load_schema()["$defs"]["profile"]["properties"])


@cache
def channel_floor(member: str) -> int | None:
    """The floor §6.5 puts on one profile channel's values, or nothing where it puts none.

    A channel is a `$ref` to a series definition rather than a member carrying constraints
    of its own, so `recorded` is not the question to ask about one — every call site it has
    passes `record="dive"` or `record="cylinder"`, and `_floor` finds nothing under a
    reference. This resolves the reference instead: the six decompression readouts share a
    definition whose `values` floor at zero, because no-decompression time, time to surface,
    ppO₂, CNS and a gradient factor have no negative reading and a source that writes one is
    spelling absence in the only space it had. Depth, ceiling and temperature share the
    signed definition and floor at nothing.

    `series.Channel` asks this once per channel, which is what keeps the rule one place in
    this package rather than one per format.
    """
    schema = load_schema()
    reference = schema["$defs"]["profile"]["properties"][member].get("$ref")
    if reference is None:
        # `pressures` is an array of series rather than one, and carries no floor either:
        # §6.3 records a cylinder pressure from zero up, and the readers bound that
        # themselves against §6.3's own maximum.
        return None
    values = schema["$defs"][reference.rsplit("/", 1)[-1]]["properties"]["values"]
    minimum = values.get("items", {}).get("minimum")
    return None if minimum is None else int(minimum)


def zero_is_an_answer(record: str, member: str) -> bool:
    """Whether a source zero is a reading for this member, or a placeholder.

    Which way a zero reads follows the member's own constraint. `exclusiveMinimum: 0` —
    `max_depth`, `duration`, a cylinder's `volume` and `start_pressure` — means the format
    records the member only when it is positive, so a writer with nothing to say had to
    put a zero somewhere and the zero is that nothing. `minimum: 0` — `weight`,
    `visibility`, `end_pressure` — means zero is a value the diver can have recorded, and
    reading it as absence would discard a real "no lead".

    An unknown record or member raises `KeyError`, which is the point: no adapter can put
    a source zero into a member the schema has no such rule for.
    """
    return recorded(Decimal(0), record=record, member=member)


def recorded(value: Decimal | int | None, *, record: str, member: str) -> bool:
    """Whether a source reading is one this member can hold, sign and zero considered.

    Only the member's lower bound: a member with an upper bound as well — `altitude`,
    `surface_pressure` — keeps its own range check, because a value above the ceiling is a
    different report line from a value below the floor.
    """
    if value is None:
        return False
    bound = _floor(record, member)
    if bound is None:
        return True
    floor, exclusive = bound
    return value > floor if exclusive else value >= floor


# -- numbers -------------------------------------------------------------------------

# The two §6.5 channel scales, here rather than in any one adapter because every format's
# samples land on them: depth in centimetres, temperature in tenths of a degree, pressure
# in tenths of a bar. `docs/converting.md` *Units and arithmetic* names these as the trap —
# "the channel conversions carry a scale the scalar ones do not" — and a table of an
# adapter's scalar factors does not contain them.
CENTIMETRES_PER_METRE = Decimal(100)
TENTHS_PER_UNIT = Decimal(10)

# The largest magnitude a source number may have. Not a physical bound — this format sets
# none on a depth or a temperature, and inventing one here would be a converter deciding
# how deep a dive can be. It is a *representability* bound, and `docs/converting.md`
# *Reading the source* states it: JSON numbers are doubles in every reader this format
# expects to meet, so a value past that range stops being a number on the way out.
# `json.dumps` writes an overflowed float as the bare token `Infinity`, which no RFC 8259
# parser accepts, and a reader on a double-based parser turns an integer that large back
# into infinity — in both directions the document silently stops being readable, and
# validation catches neither, `jsonschema` being happy to call infinity a number greater
# than zero.
#
# Divided by 1000, the largest factor any adapter applies to a number it has read — UDDF's
# cubic metres to litres, and above any channel scale — so that checking the value as the
# text is read also covers every value derived from it.
MAX_MAGNITUDE = Decimal(sys.float_info.max) / 1000


def decimal_of(text: str | None) -> Decimal | None:
    """A number from source text, or `None` for anything that is not a usable one.

    `Decimal` rather than `float` throughout: the input is decimal text and every scale an
    adapter applies is a decimal factor, so `Decimal("2.6") * 100` is exactly `260` where
    the float route arrives at 260.00000000000003 and has to be rounded back out. `Decimal`
    also accepts `"NaN"` and `"Infinity"` without complaint, which is what the finiteness
    check is for.

    **The magnitude bound is the other half of that check and is not optional.** `Decimal`
    parses `1e999` and `1e999999999` happily and calls both finite, and neither survives
    the trip out: the first becomes a float infinity, which a converter would write into a
    document as a bare `Infinity` token no JSON parser accepts and its own validation would
    not object to; the second overflows `Decimal`'s arithmetic on the next multiplication,
    raising something that is not a `ConverterError` and so abandoning a whole batch
    mid-migration rather than failing the one file. Text that cannot be carried as a number
    is treated as text that is not a number, which is what it is.
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


def rounded(value: Decimal) -> int:
    """The nearest integer, halves away from zero.

    Python's own `round` is half-to-even, which is the right default for statistics and the
    wrong one for a reading: 2.5 seconds of elapsed time is 3, not 2.
    """
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))


def integer_of(value: Decimal | None) -> int | None:
    return None if value is None else rounded(value)


# -- text ----------------------------------------------------------------------------

# The two length caps every adapter meets, whatever it is reading: §6's `notes` on any
# record, and the 255 that every REQUIRED name in §6 shares — a site's, a trip's, a gear
# item's, a diver's. A format whose own members reach further caps them here too, and the
# caps only that format meets stay with it.
MAX_NOTES = 10_000
MAX_NAME = 255


def capped(value: str, limit: int, *, note: Reporter, where: str, member: str) -> str:
    """A source string cut to the length the format allows, reporting what was cut."""
    if len(value) <= limit:
        return value
    note(
        where,
        f"{member} is {len(value)} characters; the format caps it at {limit} and the rest is dropped",
        "dropped",
    )
    return value[:limit]


# -- recordings and devices ----------------------------------------------------------

# §6.4b's own caps, and its own member order: a device written through `device` below
# reads down the section. The lengths are the section's rather than `MAX_NAME`'s, because
# §6.4b is narrower than §6's REQUIRED names by design — a serial longer than 64
# characters is not a serial any hardware here writes, and a firmware version is narrower
# still. Read the numbers off the rows rather than off any count of them.
DEVICE_CAPS: dict[str, int] = {
    "brand": 64,
    "model": 64,
    "serial": 64,
    "firmware": 32,
    "name": 64,
}


def device(
    members: dict[str, Any],
    *,
    note: Reporter,
    where: str,
    labels: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """§6.4b's Device from whatever one reader read about the hardware, or nothing at all.

    Every adapter reaches this section from a different set of fields and all of them meet
    the same three rules, so the rules live here rather than five times over: **strings are
    trimmed and an empty one is absence** (§5.4 — a device that records nothing about
    itself is not written at all, rather than written as an object with no members),
    every string is capped at §6.4b's own length, and `dive_number` floors at 0.

    `labels` names the source's own spelling of a member for the report — `<serialnumber>`,
    `Device.Info.SW` — so a note keeps speaking the format's language the way
    `SampleAxis`'s `noun` does.
    """
    labels = labels or {}
    built: dict[str, Any] = {}
    for member, limit in DEVICE_CAPS.items():
        value = members.get(member)
        if not isinstance(value, str):
            continue
        trimmed = value.strip()
        if not trimmed:
            continue
        built[member] = capped(
            trimmed, limit, note=note, where=where, member=labels.get(member, f"the device's {member}")
        )
    counter = members.get("dive_number")
    if isinstance(counter, int) and not isinstance(counter, bool):
        if counter >= 0:
            built["dive_number"] = counter
        else:
            note(
                where,
                f"{labels.get('dive_number', 'the device counter')} is {counter}, and the format records "
                "a device's counter from zero up; dropped",
                "dropped",
            )
    return built or None


# §6.4c's own cap on the device's name for its model — `"Suunto Fused RGBM 2"`,
# `"ZHL-16C"`. Narrower than §6's REQUIRED names for the reason §6.4b's device strings
# are: this is a product string a manufacturer chose, not free text a diver typed.
MAX_MODEL_NAME = 64


def deco_model(
    members: dict[str, Any],
    *,
    note: Reporter,
    where: str,
    labels: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """§6.4c's Deco Model from what a reader read about the algorithm, or nothing at all.

    Four sources reach this section from four different subsets — UDDF's
    `<decomodel><buehlmann>`, FIT's `dive_settings`, the Suunto app's `Header.Diving` and
    the DM5 XML's `<PersonalMode>` — so the rules that hold across all four are here rather
    than four times over: §6.4c's member order, the name trimmed and capped with an empty
    one read as absence (§5.4), the schema's range on a gradient factor, **both or neither**
    on the pair, **§3 rule 7's ordering**, and §6.4b's rule that an object with no members is
    not written at all.

    Every one of those is the same argument: a reading the format cannot hold resolves to an
    omission and a note here, because the alternative is `validate_document` refusing this
    converter's own output and taking the whole file — or, under an archive, every dive in
    it — with it. `docs/converting.md` states the rule and calls a converter that reaches a
    validation failure a bug rather than a file that was unusual.

    `conservatism` passes through whatever integer it was handed, and that is deliberate:
    §6.4c puts no floor on the member because Suunto's scale runs P−2 to P2, so a `-1` is a
    setting and a `0` is the P0 setting. It is the one member here where a negative is a
    reading, and the schema is what says so.

    `labels` names the source's own spelling of a member for the report —
    `<gradientfactorlow>`, `dive_settings.gf_high` — the way `device`'s does.
    """
    labels = labels or {}
    built: dict[str, Any] = {}

    algorithm = members.get("algorithm")
    if isinstance(algorithm, str):
        built["algorithm"] = algorithm

    name = members.get("name")
    if isinstance(name, str) and name.strip():
        built["name"] = capped(
            name.strip(), MAX_MODEL_NAME, note=note, where=where,
            member=labels.get("name", "the model's name"),
        )

    # §6.4c writes the pair both or neither, which `dependentRequired` enforces in the
    # schema, so a reading that fails its own range takes the other half with it rather than
    # producing a document this package's own validation would reject.
    pair: dict[str, int] = {}
    for member in ("gf_low", "gf_high"):
        value = members.get(member)
        if not isinstance(value, int) or isinstance(value, bool):
            continue
        ceiling = _cap("deco_model", member)
        if recorded(value, record="deco_model", member=member) and (ceiling is None or value <= ceiling):
            pair[member] = value
        else:
            note(
                where,
                f"{labels.get(member, member)} is {value}, and §6.4c records a gradient factor as a whole "
                f"percent from 0 to {ceiling}; dropped",
                "dropped",
            )
    if len(pair) == 2 and pair["gf_low"] > pair["gf_high"]:
        # §3's rule 7, which the schema cannot express and this converter's own output is
        # held to: a low above a high is a model nothing ran. **Both go**, because the file
        # does not say which of the two is the wrong one and choosing would be §5.4's guess.
        # It is dropped here rather than left to `validate_document`, which raises and takes
        # the whole file — or the whole archive — with it: every way a source can be wrong
        # is supposed to resolve to an omission and a note (`docs/converting.md`).
        note(
            where,
            f"{labels.get('gf_low', 'gf_low')} is {pair['gf_low']} and "
            f"{labels.get('gf_high', 'gf_high')} is {pair['gf_high']}, and §3 rule 7 records a low no higher "
            "than its high; both are dropped, the source not saying which of the two is wrong",
            "dropped",
        )
    elif len(pair) == 2:
        built.update(pair)
    elif pair:
        member, value = next(iter(pair.items()))
        missing = "gf_high" if member == "gf_low" else "gf_low"
        note(
            where,
            f"{labels.get(member, member)} is {value} and {labels.get(missing, missing)} is not recorded; "
            "§6.4c carries the two gradient factors both or neither, so the one is dropped",
            "dropped",
        )

    conservatism = members.get("conservatism")
    if isinstance(conservatism, int) and not isinstance(conservatism, bool):
        built["conservatism"] = conservatism

    return built or None


def recording(
    *,
    device: dict[str, Any] | None = None,
    mode: str | None = None,
    deco_model: dict[str, Any] | None = None,
    started_at: str | None = None,
    source_files: list[dict[str, Any]] | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """§6.4a's Recording in the section's member order, or nothing at all.

    Nothing at all is the point of the function. §3's rule 4 says a recording carries at
    least one of `device`, `profile` and `source_files`, so a source that describes a
    record of a dive without naming a device and without keeping a sample produces no
    recording — and every adapter that built one anyway would emit `recordings: [{}]` and
    fail its own output validation. `started_at` alone does not qualify: §6.4a reads an
    absent one as the dive's, so a recording carrying only a start describes nothing the
    dive does not already say. **`mode` and `deco_model` do not qualify either**, for the
    same reason and for one of their own: both describe how a computer was running rather
    than anything it recorded, and §3's rule 4 names the three members it names.
    """
    if not (device or profile or source_files):
        return None
    built: dict[str, Any] = {}
    if device:
        built["device"] = device
    if mode:
        built["mode"] = mode
    if deco_model:
        built["deco_model"] = deco_model
    if started_at:
        built["started_at"] = started_at
    if source_files:
        built["source_files"] = source_files
    if profile:
        built["profile"] = profile
    return built


# -- identity ------------------------------------------------------------------------

_UUID_TEXT = re.compile(r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")


def _is_uuid(text: str) -> bool:
    return bool(_UUID_TEXT.match(text))


def _embedded_uuid(source_id: str) -> str | None:
    """The UUID a source id already carries, if it carries one.

    A writer holding real UUIDs often has to prefix them, because its own id type forbids a
    leading digit and a hex UUID regularly has one — UDDF's reference implementation writes
    `dive-019fec36-…`. Stripping a short alphabetic prefix recovers it, which is what lets a
    logbook that went out through another format come back recognisable.
    """
    head, dash, tail = source_id.partition("-")
    if dash and head.isalpha() and len(head) <= 12 and _is_uuid(tail):
        return tail.lower()
    return source_id.lower() if _is_uuid(source_id) else None


class Identities:
    """The stable UUIDs one conversion hands out, under one format's namespace.

    `docs/converting.md` *Identity* is the prose: reuse an embedded UUID, else UUIDv5 over
    the format's own frozen namespace and `"{kind}:{source id}"`, with the record kind in
    the hash because a source id is not unique within a file — a dive and its enclosing
    group commonly share one, and hashing the bare id would hand them one UUID.

    Held by the adapter's converter rather than free-standing, because it is stateful in two
    ways that matter: it knows the `Scope`, so a record with no id of its own is identified
    within its archive member, and it shares the claimed-UUID table with the archive's other
    members, so a record two files both define is written once.
    """

    __slots__ = ("_namespace", "_scope", "_note")

    def __init__(self, namespace: uuid_pkg.UUID, scope: Scope, note: Reporter) -> None:
        self._namespace = namespace
        self._scope = scope
        self._note = note

    def for_record(self, kind: str, source_id: str | None, where: str, index: int) -> tuple[str | None, bool]:
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
        claimed = self._scope.claimed
        if source_id is None:
            self._note(
                where,
                f"the source gives this {kind} no id, so its identity is derived from its position in the "
                "file and will move if the file's order changes (spec §5.3)",
                "absent",
            )
            # Prefixed by the archive member this file is, so that two members whose
            # records carry no ids do not derive one identity from one position.
            source_id = self._scope.positional(index)

        derived = str(uuid_pkg.uuid5(self._namespace, f"{kind}:{source_id}"))
        for candidate in dict.fromkeys((_embedded_uuid(source_id) or derived, derived)):
            holder = claimed.get(candidate)
            if holder is None:
                claimed[candidate] = (self._scope.member, self._scope.where(where))
                return candidate, True
            if holder[0] != self._scope.member:
                return candidate, False

        self._note(
            where,
            f"a second {kind} carries the id {source_id!r}, already used by {claimed[derived][1]}; the "
            "record is dropped, because two records cannot share one identity (spec §5.3)",
            "dropped",
        )
        return None, False


# -- geometry ------------------------------------------------------------------------


def position(
    latitude: Decimal | None, longitude: Decimal | None, *, note: Reporter, where: str
) -> dict[str, float] | None:
    """A §6 Position from a recorded coordinate pair, or `None` where there is not one.

    Three sources of nothing, and the second is the interesting one. Half a pair is not a
    position — §6's Position makes both members REQUIRED, which is §5.4 enforced by shape.
    An exact `0.000000` pair is a writer saying "unknown" in the one spelling that looks
    like an answer: divelogs.de puts it on every site in its export, and a reader that
    trusts Null Island pins a Red Sea wreck into the Atlantic. And a pair outside WGS 84's
    own range is not a place at all.
    """
    if latitude is None or longitude is None:
        if latitude is not None or longitude is not None:
            note(where, "only one half of a coordinate pair was recorded, and a position needs both; dropped (spec §6)", "dropped")
        return None
    if latitude == 0 and longitude == 0:
        note(
            where,
            "the coordinates are exactly 0.000000 / 0.000000, which writers emit to mean 'unknown'; read as "
            "no position rather than as Null Island",
            "absent",
        )
        return None
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        note(where, f"the coordinates {latitude} / {longitude} are outside the WGS 84 range; dropped", "dropped")
        return None
    return {"latitude": float(latitude), "longitude": float(longitude)}
