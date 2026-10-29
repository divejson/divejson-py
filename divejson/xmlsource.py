"""Reading an XML source safely, once for every XML format.

Four of the dive-log formats worth reading are XML — UDDF, Subsurface's `.ssrf`, Suunto's
DM5 export, Shearwater Cloud's XML export — and every one of them meets the same two
hazards, so both answers live here and are inherited rather than re-implemented per adapter.

**A `<!DOCTYPE>` is refused outright.** Spec §9 requires a reader not to execute or
dereference anything it finds in a document, and no dive log has a legitimate use for a
document type declaration. `ElementTree` blocks external entities on its own but caps
entity *amplification* only in recent libexpat — a several hundredfold blowup still parses
on older ones, and that is a library-version property rather than a guarantee this
package's `>=3.11` floor can make. Refusing the declaration is the guarantee, and it costs
nothing real. The prolog is scanned for one before expat is handed a byte, so nothing is
expanded at all: a refusal raised from expat's own `doctype` hook surfaces only once the
buffer that carried it has been parsed to its end, entity references included. That hook
stays, behind input fed in `_CHUNK`-sized pieces, for an encoding the scan misreads.

**A tag is matched on its lowercased local name.** UDDF alone appears under four root
shapes — two namespaces, no namespace at all, and an uppercase `<UDDF>` for 2.x — and
stripping `{uri}` and lowercasing at lookup time collapses all of them into one code path.

**The accessors below the parser are how that matching reaches an adapter**, and they are
here rather than in one because both of the leniencies they carry are every XML format's.
An attribute is whitespace-stripped on the way out — Subsurface writes the site id
`" ff47210"` in *both* the formats it exports, and a reference to it carries the space too,
so stripping has to happen on both sides or the lookup stops resolving. An empty element is
absent rather than an empty value, which is how a writer with no coordinates says so.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .converter import ConverterError, DoctypeRefusedError

__all__ = [
    "DoctypeRefusingTarget",
    "attribute",
    "child",
    "children",
    "local_name",
    "parse_xml",
    "root_name",
    "text",
]

_NAME = re.compile(r"[^\s/>]+")
_DOCTYPE = re.compile(r"<!DOCTYPE\s+([^\s\[>]+)")

# What expat is fed at a time, so that a refusal from the `doctype` hook stops the parse
# within one piece of the declaration rather than at the end of the document.
_CHUNK = 64 * 1024


class DoctypeRefusingTarget(ET.TreeBuilder):
    """A parse target that stops the parse the moment a DTD is declared.

    The hook is on the *target* rather than on the parser: `XMLParser.parser`, which the
    equivalent expat handler would need, no longer exists on Python 3.14, while this one
    behaves identically on every interpreter from the package's floor through 3.14.
    """

    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        raise _refused(name)


def _refused(name: str) -> DoctypeRefusedError:
    return DoctypeRefusedError(
        f"the file declares <!DOCTYPE {name}>, and a file that declares a document type is not read, since one "
        "can pull in content from outside the file"
    )


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


def attribute(element: ET.Element | None, name: str) -> str | None:
    """An attribute by lowercased local name, whitespace stripped.

    Matched the same way a tag is, because a UDDF 2.x file spells its attributes `ID` and
    `REF`. Stripped because Subsurface writes one site id as `" ff47210"`, with the leading
    space, in both the formats it exports — and the references pointing at it carry the
    space too, so stripping has to happen on both sides or the reference stops resolving.
    """
    if element is None:
        return None
    for key, value in element.attrib.items():
        _, _, local = key.rpartition("}")
        if local.lower() == name:
            return value.strip() or None
    return None


def children(element: ET.Element | None, name: str) -> list[ET.Element]:
    """Every child with this local name, in document order."""
    if element is None:
        return []
    return [child_element for child_element in element if local_name(child_element) == name]


def child(element: ET.Element | None, name: str) -> ET.Element | None:
    """The first child with this local name, or nothing."""
    if element is None:
        return None
    for child_element in element:
        if local_name(child_element) == name:
            return child_element
    return None


def text(element: ET.Element | None) -> str | None:
    """An element's text, stripped. An empty element is absent, not an empty value.

    Subsurface writes `<latitude/>` for a site it has no coordinates for, so this is the
    single most load-bearing leniency an XML reader has.
    """
    if element is None or element.text is None:
        return None
    return element.text.strip() or None


def parse_xml(data: bytes, *, root: str, malformed: type[ConverterError]) -> ET.Element:
    """Parse `data` as XML and return its root, which must be `<root>`.

    `data` is **bytes**, not text: an XML document declares its own encoding, and a file
    that says `encoding="ISO-8859-1"` has to be decoded by the parser that read that
    declaration. Handing `ElementTree` a `str` carrying one is a `ValueError` anyway.

    `malformed` is the adapter's own error for input it cannot read at all, so a caller
    catching one format's errors still catches this one. A `<!DOCTYPE>` raises
    `DoctypeRefusedError` regardless, which is not any one format's refusal.
    """
    prolog = _decoded(data)
    position = _markup(prolog, 0)
    declaration = _DOCTYPE.match(prolog, position) if position >= 0 else None
    if declaration is not None:
        raise _refused(declaration[1])
    parser = ET.XMLParser(target=DoctypeRefusingTarget())
    try:
        for start in range(0, len(data), _CHUNK):
            parser.feed(data[start : start + _CHUNK])
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

    The bytes are decoded as `_decoded` decodes them.
    """
    text = _decoded(head)
    position = 0
    while True:
        position = _markup(text, position)
        if position < 0:
            return None
        if text.startswith("<!", position):
            position = _past_declaration(text, position)
            if position < 0:
                return None
            continue
        match = _NAME.match(text, position + 1)
        return match.group().rpartition(":")[2].lower() if match else None


def _decoded(data: bytes) -> str:
    """XML bytes as text good enough to walk the markup before the root element.

    UTF-16 is recognised as expat recognises it — from a byte-order mark, which Windows
    tools emit, or from a zero byte in the first two, which is how a `<` opens a UTF-16
    file that has none. Anything else is read as Latin-1, which never fails and is exactly
    right for the ASCII markup that precedes a root element whatever the declared encoding is.
    """
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    if data[:1] == b"\x00":
        return data.decode("utf-16-be", errors="replace")
    if data[1:2] == b"\x00":
        return data.decode("utf-16-le", errors="replace")
    return data.removeprefix(b"\xef\xbb\xbf").decode("latin-1")


def _markup(text: str, position: int) -> int:
    """The offset of the next `<` from `position` that opens neither a processing
    instruction nor a comment — a declaration or an element — or -1."""
    while True:
        position = text.find("<", position)
        if position < 0:
            return -1
        for opening, closing in (("<?", "?>"), ("<!--", "-->")):
            if text.startswith(opening, position):
                end = text.find(closing, position + len(opening))
                if end < 0:
                    return -1
                position = end + len(closing)
                break
        else:
            return position


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
