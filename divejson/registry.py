"""What this build reads, how it decides which reader gets a file, and the container.

A **format id** is the whole coupling between this package and everything around it. It
names an adapter here, it names the directory a conformance corpus keeps that format's
pairs in, it is what `divejson convert --from` takes, and it is what an application asking
`sniff()` gets back. Adding a format is registering an adapter; nothing else in the package
learns its name.

**`sniff` answers `zip` and `zip` is not a format.** An archive is a container: it has no
adapter, no identity namespace and no pair directory, and `conform` will call a `zip/`
directory a format it does not read, which is the truth. The marker exists because the
decision "these bytes are an archive" has to come out of the same call as "these bytes are
UDDF" — the caller has one head of bytes and one question.

**An archive is one logbook, or it is refused.** A watch writes one file per dive and an
account export is an archive of them, so the members are converted together and their
records land in one document, in member-name order. Every member has to name the same
registered format: an archive that mixes two, or holds something nothing claims, is a
`UnsupportedSourceError` rather than a partial import, because "we read three of your
seven dives" is not an answer a diver can act on.

**The caps are the caller's, and a member is checked before it is read.** `max_members` and
`max_member_size` default to no cap, which is right for a command line reading a file the
person running it chose; a server passes its own. The declared size in the archive's
directory is consulted first, so an oversized member is refused without being opened; the
read is then bounded as well, so the cap bounds what this process allocates rather than
only what the archive claims about itself.
"""

from __future__ import annotations

import uuid
import zipfile
from datetime import datetime
from io import BytesIO
from typing import Any, BinaryIO, Protocol

from .converter import (
    INFERRED,
    PRODUCER_KEY,
    Claimed,
    Conversion,
    MalformedArchiveError,
    NonConformingOutputError,
    Note,
    Scope,
    SourceTooLargeError,
    UnsupportedSourceError,
    header,
)
from .ssrf import SSRF
from .uddf import UDDF
from .validate import validate_document

__all__ = [
    "SNIFF_BYTES",
    "WRITTEN",
    "ZIP",
    "Adapter",
    "adapter_for",
    "convert",
    "known_formats",
    "read_formats",
    "sniff",
]

# How many bytes of a source `sniff` is given. Enough to reach an XML root element past a
# declaration and a comment or two, and small enough that a server can read it off an
# upload before deciding anything. An application spooling an upload reads exactly this
# many bytes for its own sniff, which is why the number is exported rather than private.
SNIFF_BYTES = 8192

# The container marker `sniff` returns, and the local file header every non-empty archive
# opens with. Not a format id: nothing registers it, and `known_formats()` does not carry
# it.
ZIP = "zip"
ZIP_MAGIC = b"PK\x03\x04"

# The collections a converted document can carry, in the order §4 puts them.
COLLECTIONS = ("dives", "trips", "sites", "gear")


class Adapter(Protocol):
    """What the registry needs of a source format.

    `format` is the id; `suffixes` are the extensions a file of it usually carries, which
    is a hint for a message to a human and never how a source is decided; `namespace` is
    the format's frozen identity namespace, `uuid5(NAMESPACE_URL,
    "https://divejson.org/ns/<format>")`, recorded in that format's own mapping document
    and never changed, because changing it renumbers every document the format's reader has
    ever produced.

    `sniff` is given a bounded head of bytes and says whether this reader claims them.
    `convert` is given the whole source and returns a document and its report; provenance
    is written inside it, under `extensions.divejson`, because what a file says about
    itself is only knowable once it has been parsed. It validates its own output before
    returning it — unless `scope.validates_alone` says the document is an archive member's,
    in which case the merged logbook is what gets validated, here.
    """

    format: str
    suffixes: tuple[str, ...]
    namespace: uuid.UUID

    def sniff(self, head: bytes) -> bool: ...

    def convert(self, data: bytes, *, exported_at: datetime, scope: Scope) -> Conversion: ...


# Every reader this build carries, in the order `sniff` asks them. A list rather than a
# dict so the order is a property of the file rather than of insertion history. The order
# is free: each adapter claims a root element nothing else claims, so no two of them can
# answer for one file and `sniff` reaches the same verdict whichever it asks first.
ADAPTERS: tuple[Adapter, ...] = (UDDF, SSRF)

# The formats this implementation can *write*. Empty: it reads other formats into DiveJSON
# and writes none of them back out, so a `write/<format>/` directory in a corpus is one
# this implementation does not register.
WRITTEN: frozenset[str] = frozenset()


def read_formats() -> tuple[str, ...]:
    """Every format id this build reads, in the order `sniff` asks them."""
    return tuple(adapter.format for adapter in ADAPTERS)


def known_formats() -> frozenset[str]:
    """Every format id this implementation registers, read or written."""
    return frozenset(read_formats()) | WRITTEN


def adapter_for(fmt: str) -> Adapter:
    """The reader registered under `fmt`, or `UnsupportedSourceError` naming what is."""
    for adapter in ADAPTERS:
        if adapter.format == fmt:
            return adapter
    raise UnsupportedSourceError(f"{fmt!r} is not a format this build reads ({_reads()})")


def sniff(head: bytes) -> str | None:
    """The format a bounded head of bytes claims to be, `zip`, or `None`.

    `None` is a real answer and not a failure: it means nothing registered here claims the
    bytes, which is what an application turns into "this build reads UDDF" rather than into
    a parse error from whichever reader happened to be asked first.
    """
    if head.startswith(ZIP_MAGIC):
        return ZIP
    for adapter in ADAPTERS:
        if adapter.sniff(head):
            return adapter.format
    return None


def convert(
    source: BinaryIO | bytes,
    *,
    format: str | None = None,
    exported_at: datetime | None = None,
    max_members: int | None = None,
    max_member_size: int | None = None,
) -> Conversion:
    """Convert one source — a document or an archive of them — into DiveJSON.

    `source` is a **binary** file object, or bytes for a caller that has them already. A
    file object has to be seekable: the head is read to decide the format and the source is
    then read again from the start, and an archive is read through its own directory.

    `format` names a registered reader and skips the sniff, which is what a corpus walk and
    `divejson convert --from` do. Left `None`, the bytes decide: an archive is walked as one
    logbook, a claimed format is converted, and bytes nothing claims raise
    `UnsupportedSourceError`. `zip` is not accepted here — it is a container the sniff
    recognises, not a reader a caller can ask for.

    `exported_at` defaults to now in the local zone. It is one of the two members the
    document asserts about its own run rather than about the source (spec §4) — `generator`
    is the other — so a caller producing documents in a fixed context, a test or a batch
    import, should pass its own.

    Every failure is a `ConverterError`.
    """
    stream: BinaryIO = BytesIO(source) if isinstance(source, (bytes, bytearray)) else source
    moment = exported_at or datetime.now().astimezone()

    if format is not None:
        reader = adapter_for(format)
        return reader.convert(stream.read(), exported_at=moment, scope=Scope())

    head = stream.read(SNIFF_BYTES)
    stream.seek(0)
    claimed = sniff(head)
    if claimed is None:
        raise UnsupportedSourceError(f"nothing here reads these bytes ({_reads()})")
    if claimed == ZIP:
        return _archive(stream, exported_at=moment, max_members=max_members, max_member_size=max_member_size)
    return adapter_for(claimed).convert(stream.read(), exported_at=moment, scope=Scope())


def _reads() -> str:
    return "this build reads " + ", ".join(
        f"{adapter.format} ({', '.join(adapter.suffixes)})" for adapter in ADAPTERS
    )


def _is_member(name: str) -> bool:
    """Whether an archive entry is a file to convert rather than packaging.

    Directory entries are not files. Neither are the dot-files and the `__MACOSX/` shadow
    tree an archive made on a Mac carries beside the real ones — the same rule the
    conformance runner applies to a corpus directory, and for the same reason: refusing an
    archive because the Finder put a `.DS_Store` in it would refuse most real uploads.
    """
    parts = name.split("/")
    return bool(parts[-1]) and "__MACOSX" not in parts and not parts[-1].startswith(".")


def _archive(
    stream: BinaryIO,
    *,
    exported_at: datetime,
    max_members: int | None,
    max_member_size: int | None,
) -> Conversion:
    try:
        archive = zipfile.ZipFile(stream)
        names = sorted(name for name in archive.namelist() if _is_member(name))
    except (zipfile.BadZipFile, OSError) as error:
        raise MalformedArchiveError(f"the archive could not be read — {error}") from error

    if not names:
        raise UnsupportedSourceError("the archive holds no files to convert")
    if max_members is not None and len(names) > max_members:
        raise SourceTooLargeError(
            f"the archive holds {len(names)} files, and at most {max_members} are read at once"
        )

    claimed = {name: sniff(_member_bytes(archive, name, max_member_size, head=True)) for name in names}
    unread = [name for name, fmt in claimed.items() if fmt is None or fmt == ZIP]
    if unread:
        raise UnsupportedSourceError(
            f"{unread[0]} is not a format this build reads, and every file in an archive has to "
            f"be one ({_reads()})"
        )
    formats = sorted({fmt for fmt in claimed.values() if fmt is not None})
    if len(formats) > 1:
        raise UnsupportedSourceError(
            f"the archive mixes {' and '.join(formats)}; an archive is one logbook, "
            "so its files have to be one format"
        )

    reader = adapter_for(formats[0])
    shared: Claimed = {}
    converted: list[tuple[str, Conversion]] = []
    for name in names:
        data = _member_bytes(archive, name, max_member_size)
        converted.append(
            (name, reader.convert(data, exported_at=exported_at, scope=Scope(member=name, claimed=shared)))
        )
    return _merged(converted, exported_at=exported_at)


def _member_bytes(
    archive: zipfile.ZipFile, name: str, max_member_size: int | None, *, head: bool = False
) -> bytes:
    """One member's bytes, or its head, never read before its size has been checked.

    Two bounds, because they answer different questions. The directory's declared size is
    the archive author's claim, and refuses an oversized member without opening it. The
    read itself is then bounded as well, so what reaches memory is capped by the caller's
    number rather than by the archive's — `zipfile` stops at the declared size today, and
    this does not depend on it continuing to.
    """
    info = archive.getinfo(name)
    if max_member_size is not None and info.file_size > max_member_size:
        raise SourceTooLargeError(
            f"{name} says it is {info.file_size} bytes, and at most {max_member_size} are read"
        )
    limit = SNIFF_BYTES if head else max_member_size
    try:
        with archive.open(info) as member:
            data = member.read(limit + 1) if limit is not None else member.read()
    except (zipfile.BadZipFile, OSError) as error:
        raise MalformedArchiveError(f"{name} could not be read out of the archive — {error}") from error
    if not head and max_member_size is not None and len(data) > max_member_size:
        raise SourceTooLargeError(f"{name} is longer than the {max_member_size} bytes that are read")
    return data


def _merged(converted: list[tuple[str, Conversion]], *, exported_at: datetime) -> Conversion:
    """One document out of an archive's members, in member-name order.

    Identity was reconciled as the members converted, not here: they shared a `Scope`, so
    no two of them handed out one UUID, every positional identity carried its member's
    name, and a record two members both defined was written by the first and referred to by
    the rest. What is left is concatenation, plus the one member that is the *document's*
    rather than a record's — the diver, of which a logbook has one — and the provenance
    block, which is this converter's own statement about the whole archive.
    """
    documents = [conversion.document for _, conversion in converted]
    notes: list[Note] = [note for _, conversion in converted for note in conversion.notes]

    document = header(exported_at)
    owners = [(name, conversion.document["diver"]) for name, conversion in converted if "diver" in conversion.document]
    if owners:
        document["diver"] = owners[0][1]
        for name, other in owners[1:]:
            if _recorded_owner(other) != _recorded_owner(owners[0][1]):
                notes.append(
                    Note(
                        name,
                        f"the logbook's owner is the one {owners[0][0]} records, and a logbook has one; "
                        "what this file records about the owner is dropped (spec §6.1)",
                        "dropped",
                    )
                )

    for member in COLLECTIONS:
        rows = [row for doc in documents for row in doc.get(member, ())]
        if rows:
            document[member] = rows

    document["extensions"] = {PRODUCER_KEY: _merged_provenance(converted, notes)}

    issues = validate_document(document)
    if issues:
        raise NonConformingOutputError(issues)
    return Conversion(document, tuple(notes))


def _recorded_owner(diver: dict[str, Any]) -> dict[str, Any]:
    """A diver reduced to what the source actually recorded about the person.

    Without the UUID, which for this one record says nothing: it is derived from an
    `<owner id>` that every UDDF writer spells `owner`, so it is the same for two different
    people and different for one person whose two exports spell it differently. What is
    left is the name and the email — and comparing those is what separates an archive of
    one diver's dives, where every file repeats the same owner and nothing is lost, from
    one where something is.

    Any difference is a drop, and the report says only that. Two people's exports in one
    zip and one person's two files where the later adds an email the first omitted are both
    "this member recorded something about the owner that the merged logbook does not
    carry", which is true of each; deciding *which* of the two it was would mean deciding
    when two names are one person, and this converter does not know that.
    """
    return {member: value for member, value in diver.items() if member != "uuid"}


def _merged_provenance(converted: list[tuple[str, Conversion]], notes: list[Note]) -> dict[str, Any]:
    """The provenance every member agrees on, plus the derived members they named.

    A key the members disagree about is dropped and reported rather than resolved to one of
    them: an archive whose files came out of two versions of one application has no single
    `source_generator`, and picking the first file's would be this converter asserting
    something no file said.
    """
    blocks = [conversion.document["extensions"][PRODUCER_KEY] for _, conversion in converted]
    merged: dict[str, Any] = {}
    for key in dict.fromkeys(name for block in blocks for name in block if name != INFERRED):
        values = [block.get(key) for block in blocks]
        if all(value == values[0] for value in values):
            merged[key] = values[0]
        else:
            notes.append(
                Note(
                    "$",
                    f"the archive's files disagree about {key}, so the merged logbook records none",
                    "dropped",
                )
            )

    inferred = [path for block, shift in _shifts(converted) for path in _shifted(block, shift)]
    if inferred:
        merged[INFERRED] = inferred
    return merged


def _shifts(converted: list[tuple[str, Conversion]]) -> list[tuple[dict[str, Any], dict[str, int]]]:
    """Each member's provenance block beside the offset its rows moved by on merging."""
    running = dict.fromkeys(COLLECTIONS, 0)
    pairs = []
    for _, conversion in converted:
        document = conversion.document
        pairs.append((document["extensions"][PRODUCER_KEY], dict(running)))
        for member in COLLECTIONS:
            running[member] += len(document.get(member, ()))
    return pairs


def _shifted(block: dict[str, Any], shift: dict[str, int]) -> list[str]:
    """One member's inferred paths, re-indexed for their place in the merged document.

    `dives/0/max_depth` in the second file of an archive is `dives/7/max_depth` once the
    first file's seven dives sit in front of it, and a path that still named 0 would label
    the wrong dive as derived — which is worse than not labelling it, because §5.4's point
    is that a reader can tell the two apart.
    """
    moved = []
    for path in block.get(INFERRED, ()):
        collection, _, rest = path.partition("/")
        index, _, tail = rest.partition("/")
        if collection in shift and index.isdigit():
            moved.append(f"{collection}/{int(index) + shift[collection]}/{tail}")
        else:
            moved.append(path)
    return moved
