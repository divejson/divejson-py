# Conformance fixtures

`divejson validate` must pass every document in `valid/` and reject every document in
`invalid/`. CI enforces exactly that, so a change to the spec or schema that alters what
conforms shows up here as a failing fixture — update the three together
([CONTRIBUTING.md](../CONTRIBUTING.md)).

Beside those two sit the **pair** corpora, which are for *converters* rather than
validators: each input is paired with the document a correct reader produces from it.
`uddf/` is the first.

An implementation walks all of it with one command — `divejson conform fixtures/ --strict`
([CONTRIBUTING.md](../CONTRIBUTING.md)) — and the directory layout is that command's
contract:

| path | what |
| --- | --- |
| `valid/` | documents that must validate |
| `invalid/` | documents that must not, one defect each |
| `<format>/` | reader pairs, the directory named for the source format's registry id: every file whose suffix is not `.divejson` is an input, and `<stem>.divejson` beside it is what a reader must produce from it |
| `write/<format>/` | writer pairs, the other direction: `<name>.divejson` in, `<name>.<ext>` beside it the file a writer must produce, compared as canonical XML with `<generator>` ignored. There are none yet — the first writer brings the first ones |

A pair directory for a format the implementation running the suite does not read is a
corpus-shape error rather than a failure, because it means cases that never ran. That is
what makes a fixture dropped into this tree impossible to ignore.

## valid/

| file | what it covers |
| --- | --- |
| `minimal.divejson` | The smallest conforming document: `format`, `version`, `exported_at` — no diver (a source that records nothing about its owner omits the member), no collections (absent ≡ empty). |
| `demo-logbook.divejson` | A real export of the reference writer's demo account (all names are seeded demo data; pulled 2026-09-02, courses-era writer): 8 dives, one with a full sampled profile from a dive computer, sites, trips, an empty `courses` collection, gear with a service history, certifications, and producer extensions carrying application-specific values. Regenerate from a fresh export when the writer changes. |
| `technical-dive.divejson` | Hand-built coverage of what the demo corpus lacks: trimix, a sidemount pair (two cylinders, one blend, `usage: "parallel"`), staged deco cylinders, gas-switch events, a ceiling channel with a gap, per-cylinder pressure channels, a `+12:45` UTC offset **and** an offset-less local `started_at` (§5.2's third state), a dive with no recorded duration, a common-name-only species, an antimeridian-crossing bounding box, an `agency: "other"` certification with `front_file`/`back_file`, a dive-count service interval, two courses — a completed `"other"`-agency course linked from a dive and its certification, and an unreferenced `"planned"` one with no dates — a lowercase-`z`, one-digit-fraction `created_at` (both spellings the grammar allows and naive parsers reject), and a `bookmark` 85 s **past** `profile.duration` — the surface-marker case §6.4 blesses, which the pre-2026-09-04 validator rejected, and which also completes the event-type vocabulary (`bookmark` was the one `type` no fixture exercised) and carries a `label` on a non-`other` event. |

## invalid/

One defect per file. Every rule the schema alone cannot express (spec §3) has a fixture
here; several schema-level defects are included so the validator's schema pass and the
format's structural guarantees (Position objects, the null ban) are exercised too.

| file | defect | spec |
| --- | --- | --- |
| `bad-version.divejson` | `version` is `"0.9"` | §4, §7 |
| `missing-format.divejson` | no `format` member | §4 |
| `version-before-format.divejson` | `version` serialized before `format` | §4 |
| `version-not-second.divejson` | `format` first but another member before `version` | §4 |
| `null-member.divejson` | a member carried as `null` | §5.4 |
| `undefined-member.divejson` | an undefined member outside `extensions` | §5.5 |
| `duplicate-json-member.divejson` | the same JSON member name twice in one object | §9 |
| `duplicate-uuid.divejson` | two records share a uuid | §5.3 |
| `dangling-reference.divejson` | a `site_uuids` entry resolves to nothing | §5.3 |
| `naive-exported-at.divejson` | `exported_at` without a UTC offset | §5.2 |
| `trailing-newline-datetime.divejson` | a date-time with a trailing newline inside the string | §5.2 |
| `position-incomplete.divejson` | a Position missing `longitude` | §6 |
| `oxygen-helium-sum.divejson` | `oxygen + helium > 100` on a cylinder | §6.3 |
| `pressure-order.divejson` | `end_pressure > start_pressure` on a cylinder | §6.3 |
| `avg-depth-exceeds-max.divejson` | `avg_depth > max_depth` on a dive | §6.2 |
| `profile-duration-short.divejson` | `profile.duration` below the latest sample | §6.4 |
| `channel-length-mismatch.divejson` | a series' `times` and `values` differ in length | §6.5 |
| `non-increasing-samples.divejson` | a series' `times` is not strictly increasing | §6.5 |
| `event-other-without-label.divejson` | an `"other"` event with no label | §6.6 |
| `trip-dates-reversed.divejson` | `ends_on` before `starts_on` on a trip | §6.8 |
| `course-dates-reversed.divejson` | `ends_on` before `starts_on` on a course | §6.17 |
| `bbox-missing-corner.divejson` | a Bounding Box missing one corner | §6.9 |
| `bbox-south-exceeds-north.divejson` | `south > north` in a Bounding Box | §6.9 |
| `bbox-without-position.divejson` | a `bbox` on a location with no `position` | §6.9 |
| `species-no-identity.divejson` | a species with no AphiaID and no name at all | §6.11 |
| `agency-other-missing.divejson` | `agency: "other"` without `agency_other` | §6.16 |

## uddf/

Paired conversion fixtures: `<name>.uddf` is the input and `<name>.divejson` beside it is
what `divejson convert` must produce from it. The pair is the unit — a conformance case for
converters the way the two directories above are one for validators, and one any port in
another language can be run against. No implementation lives here; the first to read UDDF,
[divejson-py](https://github.com/divejson/divejson-py), is measured against this directory
like any that follows it.

**The comparison ignores exactly two members, `exported_at` and `generator`.** Both are
facts about the *run* rather than about the input: the first is the moment of conversion
and the second is whatever software did it, which for a port is not this one. Everything
else is compared, `extensions` included — the provenance block under the `divejson`
producer key records the source's own version and generator, which are properties of the
input like any other.

**Every text input here is hand-built**, reduced from a real export of that writer rather
than being one. That is the licensing-safe answer, and it is why each file is small enough to
read: a fixture whose point is one element is easier to check when it is not surrounded by
four hundred waypoints. The corpus mixes the two habits the directories above already have
— `demo-logbook.divejson` is a real export while `minimal.divejson` and
`technical-dive.divejson` are hand-built — and this is where that choice is recorded for
`uddf/`. Naming a file after a writer says which output it is modelled on, not that the
bytes came from it.

**A binary format is the exception**, and it has to be: a `.fit` file cannot be reduced by
hand, and a hand-built one would prove that an encoder and a decoder agree rather than that
a device's file reads. Such an input is committed **as recorded**, from a dive whose
position the recorder is content to publish.

The mapping rules every expectation below follows are in
[`docs/uddf-mapping.md`](../docs/uddf-mapping.md).

| file | modelled on | what it covers |
| --- | --- | --- |
| `subsurface.uddf` | Subsurface 6.0.x | The known answer for the channel arithmetic: eight of the 431 depth samples and two of the 29 temperatures of a real Subsurface export of the demo account, whose full 431 and 29 equal `valid/demo-logbook.divejson`'s after scaling, sample for sample. It also fails the 3.2.2 XSD, in the kinds the real export does — `mix(21/0)` ids carrying parentheses, a site id beginning with a digit, `<link ref>`s pointing at both, dive ids duplicating their own `<repetitiongroup>`'s, empty `<latitude/>`/`<longitude/>` and an empty `<divetrip/>` — plus the site id `" ff47210"`, whose leading space passes the schema and breaks a reference lookup that does not strip. Its second dive carries the six-point profile Subsurface fabricates for a dive that has none, and its first has a `profile.duration` of 4300 over a logged `duration` of 4010. |
| `divelogs.uddf` | divelogs.de | **No namespace at all**, so it fails the XSD at the root. `<tankdata>` sits before `<informationbeforedive>`, which no version of `diveType` permits — the converter takes children by name for the reason above it, that 3.2.1 and 3.2.2 disagree about the order and the namespace does not say which you have. Also `0.000000` coordinates on every site, a site with an empty `<name/>` and the dive whose link to it therefore resolves to nothing, zero-depth waypoints on every other sample, and its `<inifinity/>` misspelling of `<infinity>`. |
| `mix-only-cylinder.uddf` | hand-built | The shape §6.3 blesses and no real file on disk carried: a `<tankdata>` with a gas link, both pressures and a real drop between them, and **no `<tankvolume>`** — so a cylinder's size is the only input a gas-consumption figure lacks. Its second dive has no `<tankdata>` at all, which is what APD DiveSight exports. |
| `opendiving.uddf` | this format's reference writer | The richest mapping, and the round trip that matters most: `dive-<uuid>` style ids coming back as those uuids, trips with `<trippart>` dates and places, a kit list under `<equipment>` with per-dive `<equipmentused>` links, two cylinders on two gases, `<tankpressure>` channels, a `<switchmix>` gas switch and a `<setmarker>`. |
| `legacy-writer.uddf` | 2.x writers | Four habits in one dive: an uppercase `<UDDF>` root with uppercase element *and attribute* names, `<o2>34</o2>` as whole percent, `<tankvolume>12</tankvolume>` as litres where UDDF specifies cubic metres, and the `<datetime>2002-06-18T</datetime>` a midnight dive gets from an unguarded string concatenation. Its waypoints are out of order, two of them round to the same second, and one has no `<divetime>` at all. |
