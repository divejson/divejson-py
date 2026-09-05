"""Tools for DiveJSON, an open dive-log interchange format.

The format itself — the normative specification, the JSON Schema and the conformance
corpus — lives at <https://github.com/divejson/divejson>. This package implements it:
``validate`` — the schema pass plus the requirements the spec lists as beyond-schema —
``uddf``, which reads UDDF logbooks into conforming documents, and ``conform``, the
conformance runner every implementation of the format provides. All three are importable
functions; ``cli`` is a thin wrapper over them.

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
from .uddf import (  # noqa: E402
    PRODUCER_KEY,
    UDDF_ID_NAMESPACE,
    Conversion,
    DoctypeRefusedError,
    MalformedUddfError,
    NonConformingOutputError,
    Note,
    UddfError,
    convert_uddf,
    convert_uddf_file,
)
from .validate import (  # noqa: E402
    DuplicateMemberError,
    Issue,
    load_document,
    load_schema,
    parse_document,
    validate_document,
)

__all__ = [
    "SPEC_VERSION",
    "PRODUCER_KEY",
    "UDDF_ID_NAMESPACE",
    "Conversion",
    "DoctypeRefusedError",
    "DuplicateMemberError",
    "Issue",
    "MalformedUddfError",
    "NonConformingOutputError",
    "Note",
    "UddfError",
    "__version__",
    "compared",
    "convert_uddf",
    "convert_uddf_file",
    "load_document",
    "load_schema",
    "parse_document",
    "validate_document",
]
