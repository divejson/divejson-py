"""The ``divejson`` command line."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .conform import Result
from .conform import run as run_conform
from .converter import NOTE_KINDS, ConverterError, NonConformingOutputError, NoteGroup
from .registry import convert, known_formats, read_formats, write_formats, writer_for
from .validate import DuplicateMemberError, parse_document, validate_document

# How many source locations one grouped finding names before it stops listing them. A
# habit of a whole file - eight dives with no UTC offset - is one finding, and the point
# of the line is the finding rather than the roll call.
_WHERES_SHOWN = 3

# The column the kind is printed in, taken from the kinds themselves so that adding one
# cannot leave the report ragged.
_KIND_WIDTH = max(len(kind) for kind in NOTE_KINDS)

# The collections a converted document can carry, with how to count them.
_COUNTED = (("dives", "dive", "dives"), ("trips", "trip", "trips"), ("sites", "site", "sites"), ("gear", "gear item", "gear items"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="divejson",
        description="Tools for DiveJSON, an open dive-log interchange format.",
    )
    parser.add_argument("--version", action="version", version=f"divejson {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser(
        "validate",
        help="validate documents against the DiveJSON spec",
        description=(
            "Validates each file against the DiveJSON JSON Schema and the semantic "
            "requirements the schema cannot express. Exits non-zero if any file fails."
        ),
    )
    validate.add_argument("files", nargs="+", type=Path, help="DiveJSON documents")

    convert_parser = commands.add_parser(
        "convert",
        help="convert dive logs into DiveJSON, and DiveJSON back out",
        description=(
            "Reads each dive log and writes a DiveJSON document beside it, named after "
            "the input with a .divejson extension. The source format is recognised from "
            "the file's own bytes; a zip of files in one format is read as one logbook. "
            "Nothing the source did not record is "
            "filled in, and everything it did not carry is reported: those lines are the "
            "other half of the output, not a diagnostic. "
            "--to runs the other way, writing each DiveJSON document out in one of the "
            "formats this build writes, and reporting what that format cannot hold. "
            "Exits non-zero if any file could not be converted."
        ),
    )
    convert_parser.add_argument("files", nargs="+", type=Path, help="dive logs, zips of them, or DiveJSON documents")
    convert_parser.add_argument(
        "--from",
        dest="source_format",
        choices=sorted(read_formats()),
        help="read every input as this format instead of recognising it from the bytes",
    )
    convert_parser.add_argument(
        "--to",
        dest="target_format",
        choices=sorted(write_formats()),
        help="write each DiveJSON document out in this format instead of reading one in",
    )
    convert_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="write the document here instead of beside the input; only with one input file",
    )
    convert_parser.add_argument("-f", "--force", action="store_true", help="overwrite an existing output file")
    convert_parser.add_argument(
        "--exported-at",
        type=_offset_aware,
        help="the document's exported_at, as an offset-aware date-time; defaults to now",
    )

    conform = commands.add_parser(
        "conform",
        help="run a conformance corpus against this implementation",
        description=(
            "Walks a conformance corpus: valid/ documents that must validate, invalid/ "
            "ones that must not, one directory of reader pairs per source format named "
            "for the format's id, and write/<format>/ of writer pairs. Exit status 0 if "
            "every case passed, 1 if a case failed, and 2 if the corpus's shape is wrong "
            "— an empty directory, a pair missing one of its halves, or pairs for a "
            "format this implementation does not register, all of which mean cases that "
            "never ran rather than cases that failed."
        ),
    )
    conform.add_argument("corpus", type=Path, help="the corpus directory")
    conform.add_argument(
        "--strict",
        action="store_true",
        help="a format this implementation registers and the corpus has no pairs for is an error, not a warning",
    )
    conform.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="FORMAT",
        help="check only this format's pairs; repeatable. valid/ and invalid/ run regardless",
    )
    conform.add_argument(
        "--skip",
        action="append",
        default=[],
        metavar="FORMAT",
        help="skip this format's pairs; repeatable, and only a format this implementation registers",
    )

    args = parser.parse_args(argv)
    if args.command == "convert":
        if args.target_format:
            if args.source_format:
                print("--from reads a source into DiveJSON and --to writes one out; they are opposite directions")
                return 1
            if args.exported_at:
                print("--exported-at is a member of a document being produced, and --to writes an existing one out")
                return 1
            return _write_command(args.files, args.output, force=args.force, target_format=args.target_format)
        return _convert_command(
            args.files,
            args.output,
            force=args.force,
            exported_at=args.exported_at,
            source_format=args.source_format,
        )
    if args.command == "conform":
        return _conform_command(args.corpus, strict=args.strict, only=args.only, skip=args.skip)
    return _validate_command(args.files)


def _offset_aware(text: str) -> datetime:
    """Parse `--exported-at`, which the format requires to carry a UTC offset (spec §5.2).

    It is one of the two members a converted document asserts about its own run rather than
    about the source — `generator` is the other — and the one of those that moves every
    time. Being able to pin it is what makes two conversions of one input diffable, and
    what lets this repository's own fixture expectations be regenerated without every one
    of them churning a line that carries no information about the change.
    """
    normalized = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        value = datetime.fromisoformat(normalized)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a date and time") from None
    if value.utcoffset() is None:
        raise argparse.ArgumentTypeError(f"{text!r} carries no UTC offset, which exported_at requires (spec §5.2)")
    return value


def _validate_command(files: list[Path]) -> int:
    failed = False
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
            document = parse_document(text)
        except (OSError, UnicodeDecodeError) as error:
            print(f"{path}: unreadable — {error}")
            failed = True
            continue
        except DuplicateMemberError as error:
            print(f"{path}: 1 error")
            print(f"  $: {error}")
            failed = True
            continue
        except json.JSONDecodeError as error:
            print(f"{path}: 1 error")
            print(f"  $: not valid JSON — {error}")
            failed = True
            continue

        issues = validate_document(document, raw=text)
        if issues:
            failed = True
            print(f"{path}: {len(issues)} error{'s' if len(issues) != 1 else ''}")
            for issue in issues:
                print(f"  {issue}")
        else:
            print(f"{path}: OK")
    return 1 if failed else 0


def _convert_command(
    files: list[Path],
    output: Path | None,
    *,
    force: bool,
    exported_at: datetime | None = None,
    source_format: str | None = None,
) -> int:
    """Convert each file, writing the document to a file and the report to stdout.

    **The document goes to a file and never to stdout**, which is why there is no `-`
    destination. The report is the half of this command's output a diver has to read, and
    a converter that streamed the document down the same pipe would either bury it or
    force the report onto stderr, where nobody looks. `--output` names the file instead;
    an existing one is refused rather than silently replaced, since the obvious mistake is
    converting into a hand-written document's name.
    """
    if output is not None and len(files) > 1:
        print(f"--output names one file, but {len(files)} were given")
        return 1

    failed = False
    for path in files:
        destination = output if output is not None else path.with_suffix(".divejson")
        if destination == path:
            print(f"{path}: the output would overwrite the input; pass --output to name another file")
            failed = True
            continue
        try:
            # A file object rather than its bytes: an archive is read through its own
            # directory, which needs to seek rather than to hold the whole upload.
            with open(path, "rb") as handle:
                conversion = convert(handle, format=source_format, exported_at=exported_at)
        except OSError as error:
            print(f"{path}: unreadable — {error}")
            failed = True
            continue
        except NonConformingOutputError as error:
            # Not a property of the file: every way a source can be wrong is meant to
            # resolve to an omission and a note, so reaching here is this converter's bug.
            print(f"{path}: the converter produced a document that does not conform, which is a bug in it")
            for issue in error.issues:
                print(f"  {issue}")
            failed = True
            continue
        except ConverterError as error:
            print(f"{path}: {error}")
            failed = True
            continue

        if destination.exists() and not force:
            print(f"{path}: {destination} already exists — pass --force to replace it, or --output to write elsewhere")
            failed = True
            continue
        try:
            destination.write_text(json.dumps(conversion.document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except OSError as error:
            print(f"{path}: could not write {destination} — {error}")
            failed = True
            continue

        print(f"{path}: {_counted(conversion.document)} → {destination}")
        for group in conversion.grouped():
            print(f"  {_kind(group)} {_listed(group.wheres)}: {group.message}")
    return 1 if failed else 0


def _write_command(
    files: list[Path],
    output: Path | None,
    *,
    force: bool,
    target_format: str,
) -> int:
    """Write each DiveJSON document out in `target_format`, reporting what it cannot hold.

    **Each document is validated first, and a document that does not conform is refused.**
    A writer is given a document to place into another format's shape and is not the thing
    that decides whether it was a document at all — so a member out of range would be
    written out as it stands and the file would carry it, which is a worse answer than
    saying which member and stopping. `divejson validate` is the same check, and this is
    it inline so that nobody has to remember to run it.

    Everything else is `convert`'s, deliberately: the destination is beside the input with
    the format's own extension, an existing file is refused rather than replaced, and the
    report is the other half of the output.
    """
    if output is not None and len(files) > 1:
        print(f"--output names one file, but {len(files)} were given")
        return 1

    writer = writer_for(target_format)
    failed = False
    for path in files:
        destination = output if output is not None else path.with_suffix(writer.suffix)
        if destination == path:
            print(f"{path}: the output would overwrite the input; pass --output to name another file")
            failed = True
            continue
        try:
            text = path.read_text(encoding="utf-8")
            document = parse_document(text)
        except (OSError, UnicodeDecodeError) as error:
            print(f"{path}: unreadable — {error}")
            failed = True
            continue
        except DuplicateMemberError as error:
            print(f"{path}: 1 error")
            print(f"  $: {error}")
            failed = True
            continue
        except json.JSONDecodeError as error:
            print(f"{path}: 1 error")
            print(f"  $: not valid JSON — {error}")
            failed = True
            continue

        issues = validate_document(document, raw=text)
        if issues:
            print(f"{path}: does not conform, so there is nothing to write — {len(issues)} error{'s' if len(issues) != 1 else ''}")
            for issue in issues:
                print(f"  {issue}")
            failed = True
            continue

        written = writer.write(document)
        if destination.exists() and not force:
            print(f"{path}: {destination} already exists — pass --force to replace it, or --output to write elsewhere")
            failed = True
            continue
        try:
            destination.write_bytes(written.data)
        except OSError as error:
            print(f"{path}: could not write {destination} — {error}")
            failed = True
            continue

        print(f"{path}: {_counted(document)} → {destination}")
        for group in written.grouped():
            print(f"  {_kind(group)} {_listed(group.wheres)}: {group.message}")
    return 1 if failed else 0


def _kind(group: NoteGroup) -> str:
    """A report line's kind, padded so the four of them line up down the left.

    Worth the column: `absent` and `dropped` are different news — one is what the diver's
    old application never kept, the other is what it kept and this format cannot hold — and
    a report that reads as one undifferentiated list of complaints gets skimmed. `inferred`
    and `resolved` are the other pair, and the difference between them is whose number is
    in the document: this converter's arithmetic, or the source's own at a scale the
    converter picked.

    The width is the longest kind rather than a number chosen by eye, so a fifth kind lines
    the column up by arriving rather than by anyone remembering to widen it.
    """
    return f"{group.kind:<{_KIND_WIDTH}}"


def _conform_command(corpus: Path, *, strict: bool, only: list[str], skip: list[str]) -> int:
    """Walk a conformance corpus and print what it found.

    A format named to `--only` or `--skip` that this implementation does not register is
    refused rather than ignored. Ignoring it would let `--skip <format>` answer a corpus
    the implementation cannot run, which is the one thing status 2 exists to say out
    loud, and a typo would quietly check nothing.
    """
    registered = known_formats()
    unknown = sorted({*only, *skip} - registered)
    if unknown:
        print(f"no such format: {', '.join(unknown)}")
        print(f"  this implementation registers {', '.join(sorted(registered))}")
        return 2

    result = run_conform(corpus, strict=strict, only=only, skip=skip)
    for group in result.groups:
        failing = "none failing" if not group.failed else f"{group.failed} failing"
        print(f"{group.name}: {_some(group.checked, group.noun)} checked, {failing}")
    for finding in (*result.failures, *result.shape, *result.warnings):
        print(finding)
        for line in finding.detail:
            print(f"  {line}")
    print(_conform_summary(result))
    return result.status


def _conform_summary(result: Result) -> str:
    parts = [f"{_some(result.checked, 'case')} checked"]
    if result.failures:
        parts.append(_some(len(result.failures), "failure"))
    if result.shape:
        parts.append(_some(len(result.shape), "corpus-shape error"))
    if result.warnings:
        parts.append(_some(len(result.warnings), "warning"))
    return ", ".join(parts)


def _some(count: int, thing: str) -> str:
    return f"{count} {thing}{'' if count == 1 else 's'}"


def _counted(document: dict) -> str:
    parts = []
    for member, singular, plural in _COUNTED:
        rows = document.get(member) or []
        if rows:
            parts.append(f"{len(rows)} {singular if len(rows) == 1 else plural}")
    return ", ".join(parts) if parts else "nothing the format carries"


def _listed(wheres: list[str]) -> str:
    if len(wheres) <= _WHERES_SHOWN:
        return ", ".join(wheres)
    return f"{', '.join(wheres[:_WHERES_SHOWN])} and {len(wheres) - _WHERES_SHOWN} more"


if __name__ == "__main__":
    sys.exit(main())
