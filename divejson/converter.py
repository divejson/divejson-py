"""The half of a converter that is not any one format's.

Every adapter this package registers reads a different syntax and produces the same two
things: a DiveJSON document, and a report of what the source did not carry. The rules
governing the second — and the errors, the identities and the schema-driven readings
behind the first — are the portable part, so they live here and are inherited rather than
restated once per format. `docs/converting.md` in the specification repository is the
prose companion, and this module is not allowed to disagree with it.

Four things are worth reading before an adapter is written against them.

**One error base.** Everything a converter can raise is a `ConverterError`, so a caller
catches one class and gets a message it can show a diver. The per-format subclasses exist
for a caller that cares which reader refused; nothing in this package needs them.

**A note has a kind, and it is not the same thing as an inferred value.** `kind` says what
the report is telling the diver — `absent` for what the source never recorded, `dropped`
for what was recorded and could not be carried, `inferred` for what this converter decided
rather than read. `extensions.divejson.inferred` is narrower: it lists the members whose
**value** was computed from other readings, which is what §5.4 asks a writer to label as
derived. A `<tankvolume>` read as litres rather than cubic metres is an `inferred` note and
not a listed member — the number is the source's own and only its scale was resolved,
which is a unit conversion like Kelvin to Celsius. A maximum depth taken from the depth
samples of a file that recorded none is both.

**Identity is shared across an archive's members, and position is not.** A `Scope` carries
the member name a conversion sits under and the UUIDs the whole upload has already handed
out. Two members cannot hand out one identity; two members whose records carry no ids of
their own do not collide, because the positional stand-in is prefixed by the member name.

**Which way a zero reads is the schema's decision, not the adapter's.** `recorded` asks the
member's own constraint: `exclusiveMinimum: 0` means a zero was a placeholder the writer
had to put somewhere, `minimum: 0` means it was an answer. An adapter that hard-coded the
comparison would get it right for `max_depth` and wrong for `weight`, and the difference
between those two is a diver's "no lead" turning into "unknown".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from functools import cache
from typing import Any, Literal

from . import SPEC_VERSION, __version__
from .validate import Issue, load_schema

__all__ = [
    "INFERRED",
    "PRODUCER_KEY",
    "Conversion",
    "ConverterError",
    "DoctypeRefusedError",
    "MalformedArchiveError",
    "NonConformingOutputError",
    "Note",
    "NoteGroup",
    "NoteKind",
    "Scope",
    "SourceTooLargeError",
    "UnsupportedSourceError",
    "header",
    "record_inferred",
    "recorded",
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


NoteKind = Literal["absent", "inferred", "dropped"]

# In the order a report reads best: what was never there, what this converter decided,
# what was there and could not be carried.
NOTE_KINDS: tuple[NoteKind, ...] = ("absent", "inferred", "dropped")


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


@dataclass(frozen=True, slots=True)
class Conversion:
    """A converted document and everything the conversion could not carry."""

    document: dict[str, Any]
    notes: tuple[Note, ...]

    def grouped(self) -> list[NoteGroup]:
        """Notes as groups of `(kind, message, wheres)`, in first-seen order.

        One source habit produces one note per record — eight dives with no UTC offset are
        eight notes — and a thousand-dive logbook would bury the interesting ones under
        them. Grouping is a presentation concern, so it lives here rather than in the data.

        Keyed on the kind as well as the message, because a caller renders the two
        differently and a group carrying both would have to pick one.
        """
        groups: dict[tuple[NoteKind, str], NoteGroup] = {}
        for note in self.notes:
            key = (note.kind, note.message)
            if key not in groups:
                groups[key] = NoteGroup(note.kind, note.message, [])
            groups[key].wheres.append(note.where)
        return list(groups.values())


@dataclass(frozen=True, slots=True)
class Scope:
    """Where one conversion sits inside the upload that produced it.

    A bare document is one conversion: no member name, and identities of its own. A member
    of an archive shares `claimed` with its siblings, so two members cannot hand out one
    UUID; and it prefixes both its `where` paths and its **positional** identities with its
    own name, so two members whose records carry no ids do not collide. A record the source
    *did* give an id is deliberately not prefixed — two members claiming one id are
    claiming one record, which is what the collision rule is for.
    """

    member: str | None = None
    claimed: dict[str, str] = field(default_factory=dict)

    def where(self, path: str) -> str:
        return path if self.member is None else f"{self.member}/{path}"

    def positional(self, index: int) -> str:
        """The stand-in source id for a record the source gave none."""
        return f"#{index}" if self.member is None else f"{self.member}#{index}"


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
