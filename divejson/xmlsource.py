"""Reading an XML source safely, once for every XML format.

Three of the dive-log formats worth reading are XML — UDDF, Subsurface's `.ssrf`, Suunto's
DM5 export — and every one of them meets the same two hazards, so both answers live here
and are inherited rather than re-implemented per adapter.

**A `<!DOCTYPE>` is refused outright.** Spec §9 requires a reader not to execute or
dereference anything it finds in a document, and no dive log has a legitimate use for a
document type declaration. `ElementTree` blocks external entities on its own but caps
entity *amplification* only in recent libexpat — a several hundredfold blowup still parses
on older ones, and that is a library-version property rather than a guarantee this
package's `>=3.10` floor can make. Refusing the declaration is the guarantee, and it costs
nothing real. Raising from the target's `doctype` hook aborts before expat has expanded a
single entity reference in the content, which is what makes this a bound on amplification
rather than a check performed after the damage.

**A tag is matched on its lowercased local name.** UDDF alone appears under four root
shapes — two namespaces, no namespace at all, and an uppercase `<UDDF>` for 2.x — and
stripping `{uri}` and lowercasing at lookup time collapses all of them into one code path.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .converter import ConverterError, DoctypeRefusedError

__all__ = ["DoctypeRefusingTarget", "local_name", "parse_xml", "root_name"]

_NAME = re.compile(r"[^\s/>]+")


class DoctypeRefusingTarget(ET.TreeBuilder):
    """A parse target that stops the parse the moment a DTD is declared.

    The hook is on the *target* rather than on the parser: `XMLParser.parser`, which the
    equivalent expat handler would need, no longer exists on Python 3.14, while this one
    behaves identically from 3.10 through 3.14.
    """

    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        raise DoctypeRefusedError(f"the document declares <!DOCTYPE {name}>, which this reader refuses (spec §9)")


def local_name(element: ET.Element) -> str:
    """An element's local name, lowercased.

    Both halves earn their place: the namespace is one of several, or absent, and UDDF 2.x
    spelled its elements in upper case.
    """
    tag = element.tag
    if not isinstance(tag, str):  # a comment or a processing instruction
        return ""
    _, _, local = tag.rpartition("}")
    return local.lower()


def parse_xml(data: bytes, *, root: str, malformed: type[ConverterError]) -> ET.Element:
    """Parse `data` as XML and return its root, which must be `<root>`.

    `data` is **bytes**, not text: an XML document declares its own encoding, and a file
    that says `encoding="ISO-8859-1"` has to be decoded by the parser that read that
    declaration. Handing `ElementTree` a `str` carrying one is a `ValueError` anyway.

    `malformed` is the adapter's own error for input it cannot read at all, so a caller
    catching one format's errors still catches this one. A `<!DOCTYPE>` raises
    `DoctypeRefusedError` regardless, which is not any one format's refusal.
    """
    parser = ET.XMLParser(target=DoctypeRefusingTarget())
    try:
        parser.feed(data)
        found = parser.close()
    except DoctypeRefusedError:
        raise
    except ET.ParseError as error:
        raise malformed(f"not well-formed XML — {error}") from error
    if found is None or local_name(found) != root:
        named = "nothing" if found is None else f"<{local_name(found)}>"
        raise malformed(f"the root element is {named}, not <{root}>")
    return found


def root_name(head: bytes) -> str | None:
    """The lowercased local name of the first element in a bounded head of XML.

    What a sniffer needs and nothing more: it never builds a tree, never expands anything,
    and answers `None` for a head that does not reach an element — which for a truncated
    file is the honest answer rather than a guess. A document type declaration is skipped
    rather than refused here; refusing it is the *reader's* job, and a sniffer that raised
    would turn a hostile file into an error before anything had decided to read it.

    UTF-16 is decoded from its byte-order mark because Windows tools emit it; anything else
    is read as Latin-1, which never fails and is exactly right for the ASCII markup that
    precedes a root element whatever the declared encoding is.
    """
    if head[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = head.decode("utf-16", errors="replace")
    else:
        text = head.removeprefix(b"\xef\xbb\xbf").decode("latin-1")

    position = 0
    while True:
        position = text.find("<", position)
        if position < 0:
            return None
        rest = text[position:]
        for opening, closing in (("<?", "?>"), ("<!--", "-->")):
            if rest.startswith(opening):
                end = text.find(closing, position + len(opening))
                if end < 0:
                    return None
                position = end + len(closing)
                break
        else:
            if rest.startswith("<!"):
                position = _past_declaration(text, position)
                if position < 0:
                    return None
                continue
            match = _NAME.match(text, position + 1)
            return match.group().rpartition(":")[2].lower() if match else None


def _past_declaration(text: str, position: int) -> int:
    """The offset just after a `<!DOCTYPE …>`, internal subset and all, or -1.

    The subset matters: `<!DOCTYPE uddf [<!ENTITY a "b">]>` carries a `>` inside it, and
    stopping at that one would leave the scan pointing into the middle of a declaration.
    """
    depth = 0
    for index in range(position + 2, len(text)):
        character = text[index]
        if character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
        elif character == ">" and depth <= 0:
            return index + 1
    return -1
