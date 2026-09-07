# Test fixtures

One file, and it is not part of the conformance corpus. `fixtures/` at the repository root
is the specification's corpus, vendored at `SPEC_REF` and not editable here; this directory
holds what the *tests* need and the specification does not own.

## `uddf_3.2.2.xsd` — the schema the UDDF writer is validated against

The official Universal Dive Data Format schema, vendored verbatim, and what
`tests/test_uddf_writing.py` validates every generated document against. The writer's
element order is a property of this revision — `informationbeforedive` and `waypoint` are
`xs:sequence`, `informationafterdive` is `xs:all` — so a document that validates here is a
document whose order is right, which is not something reading it back can tell you.

- Source: <https://www.streit.cc/resources/UDDF/v3.2.3/schema/uddf_3.2.2.xsd> (linked from
  chapter 11 of the UDDF documentation,
  <https://www.streit.cc/resources/UDDF/v3.2.3/en/schema.html>)
- Fetched: 2026-08-14
- Version: 3.2.2 — the newest published schema; the v3.2.3 documentation still links 3.2.2
  as its current XSD.
- License: the UDDF documentation this schema ships with is published under the GNU Free
  Documentation License ("UDDF is freely distributed" — see
  <https://www.streit.cc/resources/UDDF/v3.2.3/en/introduction.html>), which permits
  verbatim redistribution. Copyright © 2005–2018 Kai Schröder, Steffen Reith.

The file is unmodified. If it is ever re-fetched, update the date above and re-run the
tests: the writer is built against exactly this revision, and the schema is the referee for
every ordering decision in it.

Reading it needs `xmlschema`, which is in the `dev` extra and nowhere else — the package
itself never validates XML, so nothing a diver installs depends on this. A test that cannot
import it fails rather than skipping, because a validation nobody runs is the failure the
whole arrangement exists to prevent.
