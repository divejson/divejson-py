# Reading Subsurface `.ssrf` into DiveJSON

**Non-normative.** The specification is [`spec/divejson.md`](../spec/divejson.md); nothing
here changes what a conforming document is.

**The rules that hold for every source format are in
[`converting.md`](converting.md)** — leniency, identity, units and arithmetic, what a
report is, what a converter refuses to guess. This document carries only what is
Subsurface's: its attribute map, the habits it has to read, its own identity namespace, and
what it deliberately leaves unmapped.

It is also where a reader learns why the same logbook converts differently through
Subsurface's two export paths, and which of the two answers is the closer one.

## Why this format and not the UDDF export

`.ssrf` is Subsurface's **save file** rather than an export, so it holds everything
Subsurface knows. Its UDDF export holds what UDDF has room for, and Subsurface fills the
gaps in ways that survive into a converted document —
[`uddf-mapping.md`](uddf-mapping.md) *Known writer artefacts* records several. Both readers
in this repository have been run over the same eight-dive logbook in both of its export
paths, and every difference between the two documents is the UDDF exporter's doing rather
than either reader's. *Where the two readings differ* below lists them.

Subsurface is also where most of a diver's history ends up after leaving a vendor
application, which makes reading its own file the shortest route out of it.

## Parsing

### The root element is `<divelog>`, and the format id is `ssrf`

The id names the format; the tag names the file. Nothing else is consulted — in particular
not `@program`, which Subsurface-mobile spells differently and which older releases have
spelled differently again. A file this reader can read is one it can read whatever the
writer called itself.

Tags and attributes are matched on their lowercased local name, which is
`converting.md`'s rule for every XML source rather than this format's; `.ssrf` carries no
namespace and no uppercase dialect that needs it, and it costs nothing to inherit.

### A `<!DOCTYPE>` is refused outright

Subsurface has no legitimate use for a document type declaration. The refusal and its
reasoning are `converting.md`'s, and this reader shares the parse target that carries it.

### Every measurement carries its unit, and an unknown one is refused

This is the single property that shapes the reader. `depth max='45.91 m'`,
`duration='66:50 min'`, `size='12.0 l'`, `start='200.0 bar'`, `temp='22.4 C'`, `cns='11%'`,
`sac='6.471 l/min'` — the unit is in the text, so one table maps each spelling to the way
the number in front of it is read, and **a spelling the table does not carry is dropped and
reported** rather than converted by a factor no file has checked.

That is `converting.md`'s refuse-rather-than-guess rule applied to the one thing this format
states outright, and the cost of getting it wrong is why: a `'150.6 ft'` read at the metric
scale puts a recreational dive at 150 metres, in a document that validates perfectly.

**Imperial spellings are deliberately not in the table.** Subsurface writes them for a diver
whose units are set that way, and no export in this repository's hand carries one — so
neither the spellings nor the factors could be checked against a file Subsurface produced,
which `converting.md` requires of a claim about a real writer. A reader who has such a file
should add the rows and a hand-computed test for each; until then the refusal is loud, and
the report names the unit it found.

## Units

The factors. The arithmetic they run on, and why a wrong one is the most expensive mistake
in a converter, are in `converting.md`.

| DiveJSON member | DiveJSON unit | `.ssrf` attribute | written as | factor |
| --- | --- | --- | --- | --- |
| `profile.depth` values | centimetres | `<sample @depth>` | `'1.45 m'` | × 100 |
| `profile.temperature` values | tenths of a °C | `<sample @temp>` | `'24.4 C'` | × 10 |
| `profile.duration`, `times` | seconds | `<sample @time>` | `'0:10 min'` | `M × 60 + S` |
| `duration` | seconds | `<dive @duration>` | `'66:50 min'` | `M × 60 + S` |
| `max_depth`, `avg_depth` | metres | `<depth @max>`, `@mean` | `'45.91 m'` | — |
| `bottom_temperature` | °C | `<temperature @water>` | `'22.4 C'` | — |
| `cylinders[].volume` | litres | `<cylinder @size>` | `'12.0 l'` | — |
| `cylinders[].start_pressure`, `.end_pressure` | bar | `<cylinder @start>`, `@end` | `'200.0 bar'` | — |
| `cylinders[].oxygen`, `.helium` | percent | `<cylinder @o2>`, `@he` | `'32.0%'` | — |
| `cns_end` | CNS % | `<dive @cns>` | `'11%'` | — |
| `otu_end` | OTU | `<dive @otu>` | `'31'` | — |
| `dive_number` | — | `<dive @number>` | `'45'` | — |

**The two channel rows are the trap**, exactly as `converting.md` says: they carry a scale
the scalar rows beside them do not, and the most-executed conversion in this module is the
first of them. The bare-count rows are a unit too — a `%` on an `@otu` is as much a refusal
as a `ft` on a depth.

`M × 60 + S` is a clock rather than a number: Subsurface formats a time as `seconds / 60`
and `seconds % 60`, so the minutes are unbounded and the seconds are always two digits below
60. A bare `'66 min'` is **not** read as 66 minutes — reading it would be a factor-of-60
guess about a shape Subsurface does not write.

## Identity

The rules are `converting.md`'s. This format's namespace, fixed forever, is
`ef2248bf-087c-53cf-8a79-2f6e01f7c90e`, itself
`uuid5(NAMESPACE_URL, "https://divejson.org/ns/ssrf")`.

Two facts those rules answer to.

**A `<dive>` carries no id at all.** Not a UUID, not a key of Subsurface's own — `@number`
is the diver's own numbering, which §6.2 says duplicates are legal in, so hashing it would
hand two dives one identity. Every dive therefore takes the positional stand-in
`converting.md` defines, and every conversion reports it: an identity that is stable for an
unchanged file and moves when the file's order does.

That makes the archive case ordinary rather than exotic. Two `.ssrf` files in one zip would
hand their first dives one UUID if the stand-in were not prefixed by the member name, and
the merged document would fail its own validation on a duplicate uuid. `converting.md`'s
prefixing rule is what stops it, and `tests/test_ssrf_parsing.py` holds it there.

**A `<site @uuid>` is eight hex digits rather than a UUID**, so it is hashed like any other
source id. It is read as an opaque string: Subsurface writes one of them in the reference
logbook as `' ff47210'`, with a leading space, and every `@divesiteid` pointing at it
carries the space too — stripping on one side only is worse than not stripping at all.

## The attribute map

Everything the converter reads. Anything not here is not read — see *deliberately not
mapped* below.

### Document — `/divelog`

| `.ssrf` | DiveJSON |
| --- | --- |
| `@version` | `extensions.divejson.ssrf_version` — the save format's version, not the application's |
| `@program` | `extensions.divejson.source_generator.name` |
| — | `generator` — **the converter**, not the source |
| — | `exported_at` — the moment of conversion, always offset-aware |

Subsurface records its own release nowhere in the file, so `source_generator` carries a name
and no version.

### Dive sites — `/divelog/divesites/site`

| `.ssrf` | DiveJSON |
| --- | --- |
| `@name` | `sites[].name` — REQUIRED, so a nameless site is dropped along with the references to it |
| `@uuid` | the source id `sites[].uuid` is derived from |

### Dives — `/divelog/dives/dive`, and `/divelog/dives/trip/dive`

| `.ssrf` | DiveJSON |
| --- | --- |
| `@date` + `@time` | `started_at` |
| `@number` | `dive_number` |
| `@duration` | `duration` |
| `@divesiteid` | `site_uuids`, one entry |
| `@cns` | `cns_end` |
| `@otu` | `otu_end` |
| `<notes>` | `notes` |
| `<cylinder>` | `cylinders[]` |
| `<divecomputer><depth @max>`, `@mean` | `max_depth`, `avg_depth` |
| `<divecomputer><temperature @water>` | `bottom_temperature` |
| `<divecomputer><sample>` | `profile` |

**Subsurface writes no UTC offset into a `.ssrf`** — there is none on a dive, none in
`<settings>` and none at the root of the reference logbook — so every converted dive carries
a bare wall clock and says so in the report. The temptation here is specific and worth
naming: the same logbook's other exports do carry a time zone, and taking one from there
would be this converter asserting a zone the file does not. `converting.md` forbids
supplying one, and §5.2 is what it is protecting.

A dive with no `@date` is dropped, since §6.2 makes `started_at` REQUIRED. A `@time` missing
its seconds is read as `:00` and reported — §5.2's grammar requires them, so a hand-edited
file would otherwise produce a document that fails this converter's own validation and cost
the whole logbook.

`@cns` and `@otu` are the dive's *end* figures. Subsurface records no starting pair, so
`cns_start` and `otu_start` have no source here.

Zero is read two different ways, which is `converting.md`'s zero rule meeting two members
with different constraints. A `<depth @max>` or `@mean` of `0.0 m` is **not recorded** —
Subsurface writes it for a dive whose depth it never had — while a `<cylinder @end>` of
`0.0 bar` **is** a reading, a cylinder breathed dry. The schema's own bound on each member
is what decides, and no adapter gets to hard-code the comparison.

### Cylinders — `dive/cylinder`

| `.ssrf` | DiveJSON |
| --- | --- |
| `@size` | `cylinders[].volume` |
| `@start`, `@end` | `cylinders[].start_pressure`, `.end_pressure` |
| `@o2`, `@he` | `cylinders[].oxygen`, `.helium` |

`converting.md`'s vessel-less cylinder is a `<cylinder>` with a gas and no `@size`; its
absent start pressure is `@start='0.0 bar'`. **No `gas_number` is written on any dive**: this
reader maps no pressure channel and no gas switch, so nothing in the document depends on the
numbering, and §6.3 calls `gas_number` a label rather than an array index.

### Profile — `dive/divecomputer/sample`

| `.ssrf` | DiveJSON |
| --- | --- |
| `@time` | the `times` entry for every channel this sample feeds |
| `@depth` | `profile.depth` |
| `@temp` | `profile.temperature` |

**Subsurface writes a sample's depth every time and its other readings only when they
change.** That is what makes a dive with 431 depth readings carry 29 temperatures, and it is
the writer that taught `converting.md`'s no-padding rule: the two channels sit on their own
axes rather than one gaining 402 invented readings.

A dive may carry **more than one `<divecomputer>`**, one per computer the diver wore. §6.4
gives a dive one profile, so the first is read and the rest are reported.

## This format settles no ambiguity

`converting.md` defines a `resolved` finding for a value the source recorded whose *scale*
is genuinely in doubt. **This reader emits none**, and that is a property of the format
rather than an omission: every measurement states its unit, so there is no
fraction-or-percent and no litres-or-cubic-metres for a magnitude test to settle. Where
UDDF's `<o2>0.32</o2>` and `<o2>34</o2>` are both schema-valid and mean the same gas, `.ssrf`
writes `o2='32.0%'` and there is nothing left to decide.

`profile.duration` is the other thing that is not a finding, for `converting.md`'s reason:
§6.4 defines it as the span of the profile's own samples, so taking the largest sample time
is structural rather than derived. It is regularly longer than the dive's own `@duration` —
4300 against 4010 on the reference logbook's first dive — and samples are never trimmed to
make the two agree.

**So the report this reader produces carries two kinds and only two: `absent` and
`dropped`.** `tests/test_ssrf_fixtures.py` asserts that over the whole corpus, and a
`resolved` finding appearing there would mean this reader had started guessing at a scale.

## Three things read and deliberately not carried

Different from an attribute this reader never looks at, which is what the next section
lists. These three are read, refused, and named in the report, because a diver looking for
them in the converted document deserves to be told where they went.

- **`<dive @visibility>` is a five-star rating**, and §6.2's `visibility` is metres. A `5`
  would validate perfectly and claim five metres of visibility on a dive the diver rated
  five stars. Subsurface's own UDDF export of the same logbook writes `<visibility>15</visibility>`
  for that `5` — its exporter converts the rating to a distance — so the two readers of one
  logbook differ here **by design**, and this reader's silence is the truthful half.
- **`<dive @sac>`** is a surface air consumption, which §6 has no member for at all.
- **`<divecomputer @model>`** names the source of one dive's telemetry rather than an item
  the diver owns. §6.12 has a `computer` gear type, and a gear item is a thing in a kit list;
  minting one per dive from a model string would fill a logbook's gear with duplicates of the
  same computer. Subsurface's own UDDF export carries no `<divecomputer>` equipment element
  for it either.

## Deliberately not mapped

Listed rather than left silent, because a port needs to know these were considered. Several
say the same thing, and it is `converting.md`'s: no file in this repository's hand carries
one, so nothing about it could be checked against output Subsurface actually produced.

| `.ssrf` | why not |
| --- | --- |
| `<trip>`'s own attributes | §6.8 makes a trip's `starts_on` REQUIRED, and no file in hand carries a `<trip>` to read its dates and place from — so the dives inside one are carried and the grouping is reported as dropped. The element is still walked *through*, or a trip's dives would disappear with it. This is the first thing to map when such a file arrives. |
| `<site @gps>` | site coordinates, and the highest-value entry in this table. No file in hand carries one, so neither the separator nor the coordinate order can be checked; `converting.md`'s Null Island and half-a-pair rules are already shared and waiting for it. |
| `<site><geo>` | Subsurface's country/region taxonomy, whose `@cat` codes are not documented in any file here. `sites[].location` is where it would land. |
| `<weightsystem>` | `dive.weight` is the member, and the unit spelling and the multiple-system summing rule are both unchecked against a real file. |
| `<sample @pressure>`, `@sensor` | `profile.pressures[]` and the cylinder numbering it needs. No file in hand carries a sample pressure, and a channel tied to the wrong cylinder is worse than no channel. |
| `<divecomputer><event>` | gas switches and markers, whose `@name` vocabulary no file here exercises. |
| `<divecomputer @deviceid>`, `@diveid`, `@last-manual-time` | the computer's serial, its own dive key, and a marker saying the duration was typed by hand. No core member. |
| `<temperature @air>` | surface air temperature; no core member. |
| `<cylinder @description>`, `@workpressure`, `@use`, `@depth` | the cylinder's model name, its working pressure, its role and its maximum operating depth. `@use` would land on §6.3's `role`, whose value spellings no file here shows. |
| `<dive @tags>`, `@rating` | no core member. |
| `<settings>` | Subsurface's per-computer device records; no core member, and the reference logbook's is empty. |
| the logbook's owner | the format records nothing about one, so no `diver` member is written (§6.1). Minting an identity for one would be §5.4's fabrication applied to people. |
| `courses`, `certifications`, `gear`, `gear_sets`, `species` | `.ssrf` has no slot for any of them. |

## Where the two readings differ

Both readers in this repository have been run over one Subsurface logbook exported both
ways — eight dives and five sites, converted whole and compared member by member, ignoring
only the three things neither reader is claiming anything about: `uuid`, `site_uuids` and
`extensions`. The depth and temperature channels come out **equal, sample for sample**,
through two entirely different unit paths — metres and Celsius in attributes here, metres
and Kelvin in elements there.

**Compare the whole document, not `dives`, and set nothing aside without checking it.**
This count was arrived at three times by walking the dives alone, and was three times too
low: a difference that lives on a site is invisible from there, and so is one on a member
every dive happens to share. A fourth attempt then excluded `cns_end` and `otu_end` as a
pair the two exports record to different precisions, which they are not — the UDDF export
carries no CNS or OTU at all, so they differ exactly the way `bottom_temperature` does and
belong in the list for the same reason. Eight members differ, and every one is the UDDF
exporter's doing. Everything else in both documents is equal.

- **`visibility`** — `15` metres there, absent here, on all eight dives. The star rating,
  converted by the exporter and refused by this reader.
- **`cylinders[].oxygen`** — `21.0` there, absent here, on the four dives whose save-file
  `<cylinder>` carries no `@o2`. Subsurface's UDDF export writes an explicit `mix(21/0)` for
  a cylinder it records no gas for, and §6.3 is explicit that absent oxygen is not recorded
  rather than air. On the other four the save file does record `@o2` and the two readings
  agree.
- **`cylinders[].helium`** — `0.0` there, absent here, on **all eight** dives, including the
  four where `oxygen` agrees. Every `<mix>` the exporter writes carries `<he>0.00</he>`,
  which §6.3 makes a recorded zero because `helium`'s floor is inclusive; no `<cylinder>` in
  the save file carries an `@he` at all. So this one fires on a dive whose gas the two
  readings otherwise match on, which is why walking `oxygen` and stopping missed it.
- **`weight`** — `0.0` there, absent here, on all eight dives. Subsurface writes
  `<leadquantity>0</leadquantity>` for a logbook it holds no weights for, which §6.2 makes a
  recorded "no lead"; `uddf-mapping.md` records that as a known trap. The save file writes
  no `<weightsystem>` at all, which is the honest absence.
- **`bottom_temperature`** — a temperature here (`22.4` on the first dive; each dive has its
  own), absent there, on all eight dives. The UDDF export carries no `<lowesttemperature>`
  for the water temperature the save file keeps.
- **`cns_end`** — a CNS figure here (`11` on the first dive), absent there, on the seven
  dives whose `<dive>` carries an `@cns`. The UDDF export writes no CNS anywhere in the
  document — not on a dive, not on a waypoint — for the figure the save file keeps. The
  eighth dive carries neither `@cns` nor `@otu`, and there the two readings agree.
- **`otu_end`** — an OTU figure here (`31` on the first dive), absent there, on the same
  seven dives, and absent from the UDDF export for the same reason. Where a UDDF document
  does carry these two they are per-waypoint series rather than the dive's end scalar, and
  `uddf-mapping.md` records this reader declining to derive a scalar from them; that policy
  never comes into play here, because there is nothing in the export to derive from.
- **`sites[].location`** — the site's own name there, absent here, on all five sites. The
  exporter writes a `<geography><location>` holding exactly what `<name>` holds, and the
  UDDF reader carries it because §6.10's `location` is a real member and a reader cannot
  know that a writer filled it by copying. The save file's `<site>` has one name and no
  second field to copy it into. This is the one difference `dives` cannot see: the other
  seven all live on a dive.

The record UUIDs differ too, and always will: each format has its own frozen identity
namespace, so the same site converted through both paths is two records. `converting.md`
says why converted logbooks are not safe to merge on UUID.

## The pairs

`fixtures/ssrf/` holds the conformance pairs for this reader — an input, and the document a
correct reader produces from it. The specification adopts them after a release, and
`fixtures/README.md` gains its rows then.

| file | modelled on | what it covers |
| --- | --- | --- |
| `subsurface.ssrf` | Subsurface 6.0.x | The same two dives of the same logbook as `fixtures/uddf/subsurface.uddf`, reduced from the `.ssrf` the way that file is reduced from the UDDF export: the first dive keeps eight of its 431 depth samples and two of its 29 temperatures, sample for sample the same eight and two, which is what makes the two documents comparable. The second keeps all six of the profile Subsurface fabricates for a dive that has none, carried through as recorded — the UDDF fixture reduced that one to three. Also the site id `' ff47210'` with its leading space, a cylinder with a gas and one without, and the three read-and-dropped attributes. |
| `trip-grouping.ssrf` | hand-built | A `<trip>` wrapping two dives with a third beside it, so the walk-through and the dropped grouping are both visible, and the positional identities run across the flattening. |
| `refusals.ssrf` | hand-built | Everything this reader refuses: a `'150.6 ft'` maximum depth and a `'67.1 F'` sample temperature, a nameless site and the dive whose reference to it therefore resolves to nothing, a reference to a site nothing defines, a dive with no date, a `@time` with no seconds, two `<divecomputer>` elements on one dive, a sample with no time, two samples on one second, a zero start pressure beside a zero end pressure, a pressure past 350 bar, and a mix summing past 100 %. |
