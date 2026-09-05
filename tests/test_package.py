"""What an installed copy of this package is, as distinct from this checkout.

The package resolves its own schema, because the applications that install it have no
checkout of anything to fall back on. These are the claims that would otherwise only be
true here, in a tree that happens to have the vendored corpus beside it.
"""

from __future__ import annotations

import json
from importlib.metadata import version
from pathlib import Path

import pytest
from helpers import ROOT
from jsonschema import Draft202012Validator

import divejson
from divejson.validate import load_schema


def test_the_version_is_declared_in_one_place() -> None:
    """The build reads `__version__` out of the package, so a release moves one line."""
    assert version("divejson") == divejson.__version__


def test_every_public_name_resolves() -> None:
    for name in divejson.__all__:
        assert hasattr(divejson, name), name


def test_the_package_declares_its_types() -> None:
    assert (Path(divejson.__file__).resolve().parent / "py.typed").is_file()


def test_the_schema_this_package_resolves_is_the_vendored_one() -> None:
    vendored = ROOT / "schema" / divejson.SPEC_VERSION / "divejson.schema.json"
    assert load_schema() == json.loads(vendored.read_text(encoding="utf-8"))


def test_the_schema_is_itself_valid_2020_12() -> None:
    """A schema that is not a schema fails every document for the wrong reason."""
    Draft202012Validator.check_schema(load_schema())


def test_a_minor_version_with_no_schema_says_so() -> None:
    with pytest.raises(FileNotFoundError):
        load_schema("9.9")


@pytest.mark.parametrize("minor", ["", "1", "../1.0", "1.0/../..", "1.0\n"])
def test_something_that_is_not_a_minor_version_never_becomes_a_path(minor: str) -> None:
    """`load_schema` takes its caller's string and joins it onto a directory."""
    with pytest.raises(ValueError):
        load_schema(minor)


def test_spec_ref_names_exactly_one_thing() -> None:
    """CI checks the specification out at this, so it is one token and nothing else."""
    ref = (ROOT / "SPEC_REF").read_text(encoding="utf-8")
    assert ref.endswith("\n")
    assert ref.split() == [ref.strip()]
