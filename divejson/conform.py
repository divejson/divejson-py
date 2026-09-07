"""The conformance runner: what `divejson conform` walks, and what it decides.

Every implementation of DiveJSON provides this command, and it is what checks the
format's corpus. The specification repository carries no code of its own, so a pinned
release of an implementation is what runs against its fixtures; this repository runs the
same command over its vendored copy of them. One directory shape, walked the same way by
both:

    valid/            documents that must validate
    invalid/          documents that must not, or must not even parse
    <format>/         reader pairs: an input, and the document reading it must produce
    write/<format>/   writer pairs: a document, and the file writing it must produce

A pair directory is named for a **format id** — what an implementation registers an
adapter under — and that is the whole coupling between a corpus and an implementation. A
corpus carrying pairs for a format this implementation does not register is one this
implementation cannot answer for, and saying so is worth more than passing. `zip` is not
one of those ids: an archive is a container the sniffer recognises and no adapter reads, so
a `zip/` directory is a format this implementation does not register, like any other name
nothing answers to.

Three exit statuses, and the distinction between the last two is the point:

* 0 — every case passed.
* 1 — a case **failed**: a valid fixture that does not validate, an invalid one that
  does, a pair whose produced document differs from the expected one.
* 2 — the **corpus's shape** is wrong: an empty directory, an input with no expected
  document beside it, an expected document with no input, a pair directory for a format
  this implementation does not register. Nothing was proved either way, which is not the
  same answer as a failure — and a suite that reads "the runner found nothing to run" as
  success is the failure this status exists for. A glob that silently matches nothing is
  how a conformance corpus stops testing anything.
"""

from __future__ import annotations

import difflib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Any

from .converter import ConverterError
from .registry import WRITTEN, Writer, convert, read_formats, writer_for
from .validate import DuplicateMemberError, parse_document, validate_document

__all__ = [
    "IGNORED",
    "Finding",
    "Group",
    "Result",
    "compared",
    "run",
]

# The two members a converted document asserts about its own *run* rather than about the
# input: when it was converted, and what converted it. A port of this converter would
# write a different `generator` and still be right, and a release moves
# `generator.version` without moving anything the mapping decided — so neither belongs in
# a comparison of what the mapping produced. The rule lives in the package rather than in
# a test helper because a port, an application checking its own determinism and this
# runner all have to drop exactly these two, and a second copy is a second thing to get
# out of step.
IGNORED = ("exported_at", "generator")

# How many lines of a mismatch are printed before the rest are counted instead. A pair
# that differs everywhere would otherwise bury the build log in a document.
DIFF_LINES = 60


def compared(document: dict[str, Any]) -> dict[str, Any]:
    """A converted document reduced to what a fixture comparison is about."""
    return {member: value for member, value in document.items() if member not in IGNORED}


def _converted(fmt: str, data: bytes) -> dict[str, Any]:
    """One pair's input through the registered reader for the directory's own format.

    Named rather than sniffed: a corpus directory *is* the claim about what its inputs are,
    and a pair whose input a sniffer would not recognise is still a case this
    implementation owes an answer for. `exported_at` is left to default, which is the
    clock: it is one of the two members `compared` drops, so nothing this runner decides
    can see it.
    """
    return convert(data, format=fmt).document


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing the runner has to say, with anything long carried in `detail`."""

    where: str
    message: str
    detail: tuple[str, ...] = ()

    def __str__(self) -> str:
        return f"{self.where}: {self.message}"


@dataclass(frozen=True, slots=True)
class Group:
    """One part of the corpus, and how it went: `valid`, `invalid`, or a format id."""

    name: str
    noun: str  # singular: the caller pluralizes
    checked: int
    failed: int


@dataclass(frozen=True, slots=True)
class Result:
    groups: tuple[Group, ...]
    failures: tuple[Finding, ...]
    shape: tuple[Finding, ...]
    warnings: tuple[Finding, ...]

    @property
    def checked(self) -> int:
        return sum(group.checked for group in self.groups)

    @property
    def status(self) -> int:
        """The exit status, with a shape error outranking a failure.

        Both are non-zero, so the order only matters to somebody reading the number: a
        corpus whose shape is wrong has cases that never ran, and that is the more
        useful thing to be told first.
        """
        if self.shape:
            return 2
        if self.failures:
            return 1
        return 0


class _Outcome(Enum):
    PASSED = auto()
    FAILED = auto()
    UNCHECKED = auto()  # the corpus's shape stopped the case from running at all


def run(
    corpus: Path,
    *,
    strict: bool = False,
    only: Iterable[str] = (),
    skip: Iterable[str] = (),
) -> Result:
    """Walk a conformance corpus and report what it says about this implementation.

    `only` and `skip` name **formats**, and neither reaches `valid/` or `invalid/`: those
    are what the validator owes the format whichever adapters are registered. Under
    `strict`, a format this implementation registers and the corpus has no pairs for
    stops being a warning and becomes a corpus-shape error.
    """
    return _Walk(corpus, strict=strict, only=only, skip=skip).result()


class _Walk:
    def __init__(
        self, corpus: Path, *, strict: bool, only: Iterable[str], skip: Iterable[str]
    ) -> None:
        self._corpus = corpus
        self._strict = strict
        self._only = frozenset(only)
        self._skip = frozenset(skip)
        self._groups: list[Group] = []
        self._failures: list[Finding] = []
        self._shape: list[Finding] = []
        self._warnings: list[Finding] = []

    def result(self) -> Result:
        self._walk()
        return Result(
            groups=tuple(self._groups),
            failures=tuple(self._failures),
            shape=tuple(self._shape),
            warnings=tuple(self._warnings),
        )

    def _walk(self) -> None:
        if not self._corpus.is_dir():
            self._shape.append(Finding(str(self._corpus), "is not a directory"))
            return

        self._documents("valid", conforming=True)
        self._documents("invalid", conforming=False)

        for directory in sorted(
            path
            for path in self._corpus.iterdir()
            if path.is_dir()
            and not path.name.startswith(".")
            and path.name not in ("valid", "invalid")
        ):
            if directory.name == "write":
                self._writer_directories(directory)
            else:
                self._reader_directory(directory)

        self._formats_with_no_pairs()

        if not self._groups and not self._shape:
            self._shape.append(
                Finding(str(self._corpus), "holds no cases: no documents and no pairs")
            )

    # Documents: `valid/` and `invalid/`.

    def _documents(self, name: str, *, conforming: bool) -> None:
        directory = self._corpus / name
        if not directory.is_dir():
            return
        paths = sorted(path for path in directory.glob("*.divejson") if path.is_file())
        if not paths:
            self._shape.append(Finding(name, "holds no documents"))
            return
        outcomes = [self._document(path, conforming=conforming) for path in paths]
        self._record(name, "document", outcomes)

    def _document(self, path: Path, *, conforming: bool) -> _Outcome:
        where = self._where(path)
        try:
            document = parse_document(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, DuplicateMemberError, json.JSONDecodeError) as error:
            if conforming:
                self._failures.append(Finding(where, f"is not readable as JSON — {error}"))
                return _Outcome.FAILED
            # Refusing at the parse is a refusal: a duplicate member name is one of the
            # things §9 has readers reject, and the document never reaches the schema.
            return _Outcome.PASSED
        except OSError as error:
            self._failures.append(Finding(where, f"is unreadable — {error}"))
            return _Outcome.FAILED

        issues = validate_document(document)
        if conforming and issues:
            self._failures.append(
                Finding(
                    where,
                    f"does not conform, in {len(issues)} way{'s' if len(issues) != 1 else ''}",
                    tuple(str(issue) for issue in issues),
                )
            )
            return _Outcome.FAILED
        if not conforming and not issues:
            self._failures.append(Finding(where, "conforms, and every document here must not"))
            return _Outcome.FAILED
        return _Outcome.PASSED

    # Reader pairs: `<format>/`.

    def _reader_directory(self, directory: Path) -> None:
        fmt = directory.name
        if fmt not in read_formats():
            # Asked before `--only`/`--skip` are applied, deliberately. A corpus carrying
            # pairs this implementation cannot run is a fact about the corpus, and no
            # filter may turn it into silence — `--skip <that format>` is refused by the
            # command for the same reason.
            self._shape.append(
                Finding(fmt, f"is a format this implementation does not read ({self._reads()})")
            )
            return
        if not self._selected(fmt):
            return

        # Everything that is not a `.divejson` is an input to convert, which is what lets
        # a format bring whatever extension it has. Hidden files are not: a corpus copied
        # off a Mac carries `.DS_Store`, and that rule would make it a source file.
        contents = sorted(
            path
            for path in directory.iterdir()
            if path.is_file() and not path.name.startswith(".")
        )
        inputs = [path for path in contents if path.suffix != ".divejson"]
        expectations = {path for path in contents if path.suffix == ".divejson"}
        if not inputs:
            self._shape.append(Finding(fmt, "holds no inputs to convert"))
            return

        outcomes = []
        claimed = set()
        for source in inputs:
            expected = source.with_suffix(".divejson")
            claimed.add(expected)
            outcomes.append(self._reader_pair(fmt, source, expected))
        for orphan in sorted(expectations - claimed):
            self._shape.append(
                Finding(self._where(orphan), "is an expected document with no input beside it")
            )
        self._record(fmt, "reader pair", outcomes)

    def _reader_pair(self, fmt: str, source: Path, expected_path: Path) -> _Outcome:
        where = self._where(source)
        if not expected_path.is_file():
            self._shape.append(
                Finding(where, f"has no {expected_path.name} beside it to be checked against")
            )
            return _Outcome.UNCHECKED

        try:
            expected = parse_document(expected_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, DuplicateMemberError, json.JSONDecodeError) as error:
            self._failures.append(
                Finding(self._where(expected_path), f"is not readable as JSON — {error}")
            )
            return _Outcome.FAILED
        if not isinstance(expected, dict):
            self._failures.append(
                Finding(self._where(expected_path), "is not a JSON object, so it is not a document")
            )
            return _Outcome.FAILED

        try:
            produced = _converted(fmt, source.read_bytes())
        except ConverterError as error:
            self._failures.append(Finding(where, f"could not be converted — {error}"))
            return _Outcome.FAILED
        except OSError as error:
            self._failures.append(Finding(where, f"is unreadable — {error}"))
            return _Outcome.FAILED

        outcome = _Outcome.PASSED
        if compared(produced) != compared(expected):
            self._failures.append(
                Finding(
                    where,
                    f"converts to a document that differs from {expected_path.name}",
                    _diff(expected, produced),
                )
            )
            outcome = _Outcome.FAILED
        issues = validate_document(expected)
        if issues:
            self._failures.append(
                Finding(
                    self._where(expected_path),
                    f"is expected of a converter and does not itself conform, "
                    f"in {len(issues)} way{'s' if len(issues) != 1 else ''}",
                    tuple(str(issue) for issue in issues),
                )
            )
            outcome = _Outcome.FAILED
        return outcome

    # Writer pairs: `write/<format>/`.

    def _writer_directories(self, directory: Path) -> None:
        """`write/` holds one directory of writer pairs per format an implementation writes."""
        children = sorted(
            path
            for path in directory.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        )
        if not children:
            self._shape.append(Finding("write", "holds no writer-pair directories"))
            return
        for child in children:
            if child.name not in WRITTEN:
                self._shape.append(
                    Finding(
                        f"write/{child.name}",
                        f"is a format this implementation does not write ({self._writes()})",
                    )
                )
            elif self._selected(child.name):
                self._writer_directory(child)

    def _writer_directory(self, directory: Path) -> None:
        """One format's writer pairs: a document, and the file writing it must produce.

        The reader pair inverted, and the inversion is the whole of it — the `.divejson` is
        the **input** here and the file beside it the expectation, where in a `<format>/`
        directory it is the other way round. So the pairing runs off the documents rather
        than off everything that is not one, and a file with no document beside it is the
        orphan.

        **How two files are compared is the writer's own answer, not this runner's.** A
        writer registers `compared`, and for an XML format that is canonical XML with
        `<generator>` dropped: two runs of one writer differ in the version it stamps there
        and in nothing else, and a corpus that failed on a release would be a corpus nobody
        could keep green.
        """
        fmt = directory.name
        writer = writer_for(fmt)
        contents = sorted(
            path for path in directory.iterdir() if path.is_file() and not path.name.startswith(".")
        )
        inputs = [path for path in contents if path.suffix == ".divejson"]
        if not inputs:
            self._shape.append(Finding(f"write/{fmt}", "holds no documents to write"))
            return

        expected = {path for path in contents if path.suffix != ".divejson"}
        outcomes = []
        claimed = set()
        for source in inputs:
            produced = source.with_suffix(writer.suffix)
            claimed.add(produced)
            outcomes.append(self._writer_pair(writer, source, produced))
        for orphan in sorted(expected - claimed):
            self._shape.append(
                Finding(self._where(orphan), "is an expected file with no document beside it to write")
            )
        self._record(f"write/{fmt}", "writer pair", outcomes)

    def _writer_pair(self, writer: Writer, source: Path, expected_path: Path) -> _Outcome:
        where = self._where(source)
        if not expected_path.is_file():
            self._shape.append(
                Finding(where, f"has no {expected_path.name} beside it to be checked against")
            )
            return _Outcome.UNCHECKED

        try:
            document = parse_document(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, DuplicateMemberError, json.JSONDecodeError) as error:
            self._failures.append(Finding(where, f"is not readable as JSON — {error}"))
            return _Outcome.FAILED
        if not isinstance(document, dict):
            self._failures.append(Finding(where, "is not a JSON object, so it is not a document"))
            return _Outcome.FAILED

        outcome = _Outcome.PASSED
        # The input is checked the way a reader pair's *expectation* is: a corpus whose
        # writer pair starts from a document that does not itself conform proves nothing
        # about the writer, whatever the two files then agree on.
        issues = validate_document(document)
        if issues:
            self._failures.append(
                Finding(
                    where,
                    f"is given to a writer and does not itself conform, "
                    f"in {len(issues)} way{'s' if len(issues) != 1 else ''}",
                    tuple(str(issue) for issue in issues),
                )
            )
            outcome = _Outcome.FAILED

        try:
            produced = writer.write(document).data
            comparable = writer.compared(produced)
            against = writer.compared(expected_path.read_bytes())
        except ConverterError as error:
            self._failures.append(Finding(where, f"could not be written — {error}"))
            return _Outcome.FAILED
        except OSError as error:
            self._failures.append(Finding(self._where(expected_path), f"is unreadable — {error}"))
            return _Outcome.FAILED

        if comparable != against:
            self._failures.append(
                Finding(
                    where,
                    f"is written as a document that differs from {expected_path.name}",
                    _text_diff(against, comparable),
                )
            )
            outcome = _Outcome.FAILED
        return outcome

    # Formats with no directory at all.

    def _formats_with_no_pairs(self) -> None:
        for fmt in sorted(read_formats()):
            if self._selected(fmt) and not (self._corpus / fmt).is_dir():
                self._missing(
                    fmt,
                    "is a format this implementation reads, and the corpus has no pairs for it",
                )
        for fmt in sorted(WRITTEN):
            if self._selected(fmt) and not (self._corpus / "write" / fmt).is_dir():
                self._missing(
                    f"write/{fmt}",
                    "is a format this implementation writes, and the corpus has no pairs for it",
                )

    def _missing(self, where: str, message: str) -> None:
        if self._strict:
            self._shape.append(Finding(where, message))
        else:
            self._warnings.append(Finding(where, f"{message} (an error under --strict)"))

    # Bookkeeping.

    def _record(self, name: str, noun: str, outcomes: list[_Outcome]) -> None:
        checked = sum(outcome is not _Outcome.UNCHECKED for outcome in outcomes)
        if checked:
            failed = sum(outcome is _Outcome.FAILED for outcome in outcomes)
            self._groups.append(Group(name, noun, checked, failed))

    def _selected(self, fmt: str) -> bool:
        return (not self._only or fmt in self._only) and fmt not in self._skip

    def _where(self, path: Path) -> str:
        return path.relative_to(self._corpus).as_posix()

    def _reads(self) -> str:
        return f"it reads {', '.join(sorted(read_formats()))}"

    def _writes(self) -> str:
        return f"it writes {', '.join(sorted(WRITTEN))}" if WRITTEN else "it writes nothing"


def _diff(expected: dict[str, Any], produced: dict[str, Any]) -> tuple[str, ...]:
    return _lines(_rendered(expected), _rendered(produced))


def _text_diff(expected: str, produced: str) -> tuple[str, ...]:
    """A writer pair's mismatch, over the comparable form of each file.

    Split on `>` rather than on newlines: the comparable form is canonical XML, which
    carries no indentation at all, so a whole document would otherwise be one line and the
    diff would say only that it differs.
    """
    return _lines(_by_element(expected), _by_element(produced))


def _by_element(canonical: str) -> list[str]:
    return [f"{part}>" for part in canonical.split(">")[:-1]]


def _lines(expected: list[str], produced: list[str]) -> tuple[str, ...]:
    lines = list(
        difflib.unified_diff(expected, produced, fromfile="expected", tofile="produced", lineterm="", n=2)
    )
    if len(lines) > DIFF_LINES:
        return (*lines[:DIFF_LINES], f"... and {len(lines) - DIFF_LINES} more lines of difference")
    return tuple(lines)


def _rendered(document: dict[str, Any]) -> list[str]:
    """The lines a mismatch is diffed over: the document, minus what is not compared."""
    return json.dumps(compared(document), indent=2, ensure_ascii=False).splitlines()
