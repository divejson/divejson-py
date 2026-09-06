"""Tools for DiveJSON, an open dive-log interchange format.

The format itself — the normative specification, the JSON Schema and the conformance
corpus — lives at <https://github.com/divejson/divejson>. This package implements it:
``validate`` — the schema pass plus the requirements the spec lists as beyond-schema —
``registry``, which decides what a source is and converts it through the adapter that
reads it, and ``conform``, the conformance runner every implementation of the format
provides. All three are importable functions; ``cli`` is a thin wrapper over them.

An application reads a source in two calls. ``sniff(head)`` takes a bounded head of bytes
— ``SNIFF_BYTES`` of them — and answers a format id, ``"zip"``, or ``None`` for bytes
nothing here claims; ``convert(source)`` takes the whole thing and returns a
``Conversion``: the document, and the notes that are the other half of the output.

The schema, the fixtures and the mapping documents are **vendored** here, from the
specification commit named in the repository's ``SPEC_REF``, so that an installed package
resolves its own schema and needs no checkout of anything.
"""

__version__ = "0.2.0"

SPEC_VERSION = "1.0"

# Imported below the two constants rather than at the top of the file: `validate` reads
# `SPEC_VERSION` from this module as it imports, so the names it needs have to exist by
# the time the import runs.
from .conform import compared  # noqa: E402
from .converter import (  # noqa: E402
    PRODUCER_KEY,
    Conversion,
    ConverterError,
    DoctypeRefusedError,
    MalformedArchiveError,
    NonConformingOutputError,
    Note,
    NoteGroup,
    NoteKind,
    SourceTooLargeError,
    UnsupportedSourceError,
)
from .fit import FIT_ID_NAMESPACE, FitError, MalformedFitError  # noqa: E402
from .registry import SNIFF_BYTES, convert, read_formats, sniff  # noqa: E402
from .ssrf import SSRF_ID_NAMESPACE, MalformedSsrfError, SsrfError  # noqa: E402
from .uddf import UDDF_ID_NAMESPACE, MalformedUddfError, UddfError  # noqa: E402
from .validate import (  # noqa: E402
    DuplicateMemberError,
    Issue,
    load_document,
    load_schema,
    parse_document,
    validate_document,
)

__all__ = [
    "SNIFF_BYTES",
    "SPEC_VERSION",
    "PRODUCER_KEY",
    "FIT_ID_NAMESPACE",
    "SSRF_ID_NAMESPACE",
    "UDDF_ID_NAMESPACE",
    "Conversion",
    "ConverterError",
    "DoctypeRefusedError",
    "DuplicateMemberError",
    "FitError",
    "Issue",
    "MalformedArchiveError",
    "MalformedFitError",
    "MalformedSsrfError",
    "MalformedUddfError",
    "NonConformingOutputError",
    "Note",
    "NoteGroup",
    "NoteKind",
    "SourceTooLargeError",
    "SsrfError",
    "UddfError",
    "UnsupportedSourceError",
    "__version__",
    "compared",
    "convert",
    "load_document",
    "load_schema",
    "parse_document",
    "read_formats",
    "sniff",
    "validate_document",
]
