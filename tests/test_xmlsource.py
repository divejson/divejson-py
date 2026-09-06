"""The parse target every XML adapter shares, and the sniff that never parses.

Two claims worth separating. `parse_xml` refuses a `<!DOCTYPE>` and raises something that
is not any one format's error, so an adapter added later inherits spec §9 rather than
remembering it. `root_name` answers what a bounded head of bytes opens with, and does it
without building a tree — a sniffer that raised on a hostile file would turn "what is
this?" into an error before anything had decided to read it.
"""

from __future__ import annotations

import pytest

from divejson import ConverterError, DoctypeRefusedError, UddfError
from divejson.uddf import MalformedUddfError
from divejson.xmlsource import parse_xml, root_name

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
