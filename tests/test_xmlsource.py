"""The parse target every XML adapter shares, its accessors, and the sniff that never parses.

Three claims worth separating. `parse_xml` refuses a `<!DOCTYPE>` and raises something that
is not any one format's error, so an adapter added later inherits spec §9 rather than
remembering it. `root_name` answers what a bounded head of bytes opens with, and does it
without building a tree — a sniffer that raised on a hostile file would turn "what is
this?" into an error before anything had decided to read it. And the accessors carry the
two leniencies every XML reader needs, which is why they are checked here rather than
through whichever adapter happens to exercise them.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from divejson import ConverterError, DoctypeRefusedError, UddfError
from divejson.uddf import MalformedUddfError
from divejson.xmlsource import attribute, child, children, parse_xml, root_name, text

# -- the parse target -----------------------------------------------------------------


def test_the_root_is_checked_against_the_name_the_adapter_asked_for() -> None:
    assert parse_xml(b"<divelog/>", root="divelog", malformed=MalformedUddfError).tag == "divelog"
    with pytest.raises(MalformedUddfError, match="not <divelog>"):
        parse_xml(b"<uddf/>", root="divelog", malformed=MalformedUddfError)


def test_a_doctype_refusal_is_not_any_one_formats_error() -> None:
    """It moved off `UddfError` deliberately: §9 binds every reader, not the UDDF one.

    A caller catching one format's errors still catches this, because everything a
    converter raises is a `ConverterError` — but a caller asking "was this bad UDDF?" gets
    the honest no.
    """
    with pytest.raises(DoctypeRefusedError) as raised:
        parse_xml(b'<!DOCTYPE uddf><uddf/>', root="uddf", malformed=MalformedUddfError)
    assert isinstance(raised.value, ConverterError)
    assert not isinstance(raised.value, UddfError)


# -- the accessors --------------------------------------------------------------------


def test_an_attribute_is_matched_by_local_name_and_stripped() -> None:
    """Both halves are load-bearing, and each was learned from a real file.

    UDDF 2.x spells its attributes `ID` and `REF`, and a namespaced one arrives as
    `{uri}id`. Subsurface writes the site id `" ff47210"` with a leading space in both the
    formats it exports, and the references to it carry the space too — stripping on one
    side only is worse than not stripping at all.
    """
    element = ET.fromstring("<site ID=' ff47210' xmlns:x='urn:x' x:name='Small Brother' blank='  '/>")
    assert attribute(element, "id") == "ff47210"
    assert attribute(element, "name") == "Small Brother"
    assert attribute(element, "blank") is None
    assert attribute(element, "missing") is None
    assert attribute(None, "id") is None


def test_children_are_taken_by_lowercased_local_name() -> None:
    element = ET.fromstring("<dive><SAMPLE n='1'/><sample n='2'/><notes/></dive>")
    assert [attribute(found, "n") for found in children(element, "sample")] == ["1", "2"]
    assert attribute(child(element, "sample"), "n") == "1"
    assert child(element, "absent") is None
    assert children(None, "sample") == []
    assert child(None, "sample") is None


def test_an_empty_element_is_absent_rather_than_an_empty_value() -> None:
    """`<latitude/>` is Subsurface saying it has no coordinates for a site."""
    element = ET.fromstring("<geography><latitude/><longitude>  </longitude><location>Dahab</location></geography>")
    assert text(child(element, "latitude")) is None
    assert text(child(element, "longitude")) is None
    assert text(child(element, "location")) == "Dahab"
    assert text(None) is None


def test_a_comment_is_not_a_child_with_a_name() -> None:
    """`local_name` answers `""` for a comment, so nothing ever matches one."""
    element = ET.fromstring("<dive><!-- exported by something --><notes>a</notes></dive>")
    assert text(child(element, "notes")) == "a"
    assert children(element, "") == []


# -- the sniff ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (b"<uddf/>", "uddf"),
        (b'<?xml version="1.0" encoding="ISO-8859-1"?>\n<uddf version="3.2.2">', "uddf"),
        (b"<UDDF VERSION=\"2.2.0\">", "uddf"),  # 2.x spelled everything in upper case
        (b'<uddf xmlns="http://www.streit.cc/uddf/3.2/">', "uddf"),
        (b"\xef\xbb\xbf<uddf>", "uddf"),  # a UTF-8 byte-order mark
        (b"<!-- exported by something -->\n<divelog>", "divelog"),
        (b"<!DOCTYPE uddf [<!ENTITY a \"b\">]>\n<uddf>", "uddf"),  # skipped, not refused
        (b'<x:uddf xmlns:x="urn:x">', "uddf"),  # a prefixed root is still that root
        ('<uddf/>'.encode("utf-16"), "uddf"),
        (b"", None),
        (b"{}", None),
        (b"this is not XML at all", None),
        (b"<!-- a comment that never ends", None),
        (b"<!DOCTYPE uddf", None),
        (b"   \n  ", None),
    ],
)
def test_the_root_name_of_a_bounded_head(head: bytes, expected: str | None) -> None:
    assert root_name(head) == expected


def test_a_root_element_past_the_head_is_not_claimed() -> None:
    """A file that opens with kilobytes of comment is a source nothing claims, honestly.

    The sniffer is given a bounded head because a server reads one off an upload before
    deciding anything; answering "I did not reach an element" is the only true answer at
    that point, and `--from` is what someone with such a file uses.
    """
    head = b"<!-- " + b"x" * 200 + b" -->"  # the comment is not closed inside this slice
    assert root_name(head[:100]) is None
