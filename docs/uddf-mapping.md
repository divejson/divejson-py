# Reading UDDF into DiveJSON

**Non-normative.** The specification is [`spec/divejson.md`](../spec/divejson.md); nothing
here changes what a conforming document is.

**The rules that hold for every source format are in
[`converting.md`](converting.md)** — leniency, identity, units and arithmetic, what a
report is, what a converter refuses to guess. This document carries only what is UDDF's:
its element map, the dialects it has to read, its own identity namespace, its ambiguities,
and what it deliberately leaves unmapped.

It is also where a future reader learns why a `0.012` and a `12` are the same cylinder.

## Why a converter at all

UDDF is the nominal incumbent and the format most of a diver's history is trapped in. It
has been frozen since 2018, its own site's certificate has expired, and the documentation
contradicts its schema in at least one load-bearing place. What actually ships is a family
of dialects: two real third-party exports checked while this converter was written both
fail the 3.2.2 XSD, and they fail structurally rather than at the margins.

That is the file that taught `converting.md`'s first rule, and this is the format it was
written against.

## Parsing

### Tags are matched on their lowercased local name

UDDF appears under at least four root shapes:

| shape | seen from |
| --- | --- |
| namespace `http://www.streit.cc/uddf/3.2/` | Subsurface, and this format's reference writer |
| namespace `http://www.streit.cc/uddf/3.1/` | older 3.1 writers |
| no namespace at all | divelogs.de, APD DiveSight |
| uppercase `<UDDF>` | 2.x writers |

Stripping `{uri}` and lowercasing collapses all four into one code path. Attribute names
are matched the same way, since a 2.x file spells them `ID` and `REF`. The alternative is
the union XPaths Subsurface's own maintainers describe as unwieldy, one per element.

The `@version` attribute is carried into the output's provenance block but selects
nothing: see *element order* below.

### Children are taken by name, never by position

`diveType`'s child sequence changed between 3.2.1 and 3.2.2 — `tankdata` and `samples`
swap, and so do `applicationdata` and `informationbeforedive` — while the namespace stayed
byte-identical. A 3.2.1-correct file is therefore order-invalid under 3.2.2 and the
reverse, and the namespace cannot tell you which you have. divelogs.de writes `<tankdata>`
first; Subsurface writes it after `<informationbeforedive>`. Since the converter does not
gate on validation anyway, the honest reading is to be order-insensitive: take children by
name and let the order be whatever it is.

Waypoints are the one exception, and even there position is not what orders them: they are
sorted by their own recorded `<divetime>`, under `converting.md`'s sample-ordering rule.

### What UDDF's leniencies look like

The rules are in `converting.md`; these are the elements that produced each of them.

- **Whitespace in ids and refs.** Subsurface writes one site id as `" ff47210"`, with the
  leading space, and the `<link ref>`s pointing at it carry the space too — so stripping
  has to happen on both sides or the reference stops resolving.
- **`<latitude/>`.** An empty element is Subsurface saying it has no coordinates.
- **Ids the XSD refuses.** `mix(21/0)` carries parentheses and `2bbb3390` begins with a
  digit, so neither is an `NCName`; both are what Subsurface writes and both are read.
- **`<name>`.** A `<name>` of pure whitespace is no name.

### A `<!DOCTYPE>` is refused outright

UDDF has no legitimate use for a document type declaration. The refusal and its reasoning
are `converting.md`'s, and this reader shares the parse target that carries it.

## Units

The factors. The arithmetic they run on, and why a wrong one is the most expensive mistake
in a converter, are in `converting.md`.

| DiveJSON member | DiveJSON unit | UDDF element | UDDF unit | factor |
| --- | --- | --- | --- | --- |
| `profile.depth` values | centimetres | `<waypoint><depth>` | metres | × 100 |
| `profile.temperature` values | tenths of a °C | `<waypoint><temperature>` | Kelvin | (K − 273.15) × 10 |
| `profile.pressures[]` values | tenths of a bar | `<waypoint><tankpressure>` | Pascal | ÷ 100 000 × 10 |
| `profile.duration`, `times` | seconds | `<waypoint><divetime>` | seconds | — |
| `max_depth`, `avg_depth` | metres | `<greatestdepth>`, `<averagedepth>` | metres | — |
| `bottom_temperature` | °C | `<lowesttemperature>` | Kelvin | K − 273.15 |
| `cylinders[].start_pressure`, `.end_pressure` | bar | `<tankpressurebegin>`, `<tankpressureend>` | Pascal | ÷ 100 000 |
| `surface_pressure` | bar | `<surfacepressure>` | Pascal | ÷ 100 000 |
| `cylinders[].volume` | litres | `<tankvolume>` | cubic metres | × 1000, and see below |
| `cylinders[].oxygen`, `.helium` | percent | `<o2>`, `<he>` | fraction | × 100, and see below |
| `cylinders[].po2_limit` | bar | `<maximumpo2>` | bar | — the one pressure UDDF does not express in Pascal |
| `weight` | kilograms | `<leadquantity>` | kilograms | — |
| `visibility`, `altitude` | metres | `<visibility>`, `<altitude>` | metres | — |

## Identity

The rules are `converting.md`'s. UDDF's namespace, fixed forever, is
`1b85a949-d5f7-5d67-9d04-dcc78342f907`, itself
`uuid5(NAMESPACE_URL, "https://divejson.org/ns/uddf")`.

The two UDDF facts those rules answer to: `xs:ID` is an `NCName` and cannot begin with a
digit, which a hex UUID regularly does, so a writer holding real UUIDs prefixes them — this
format's reference writer writes `dive-019fec36-b9ec-71c6-a03e-64f59b8b92b1`, and
the prefix is stripped back off. And a source id is not unique within a file: every
`<dive>` in a Subsurface export reuses its enclosing `<repetitiongroup>`'s id, which is why
the record kind is part of the hash.

## The element map

Everything the converter reads. Anything not here is not read — see *deliberately not
mapped* below.

### Document

| UDDF | DiveJSON |
| --- | --- |
| `/uddf/@version` | `extensions.divejson.uddf_version` |
| `/uddf/generator/name`, `/version` | `extensions.divejson.source_generator` |
| — | `generator` — **the converter**, not the source |
| — | `exported_at` — the moment of conversion, always offset-aware |

### Diver — `/uddf/diver/owner`

| UDDF | DiveJSON |
| --- | --- |
| `personal/firstname` + `middlename` + `lastname` | `diver.name`, joined with spaces |
| `contact/email` | `diver.email` |
| `@id` | `diver.uuid` |

**`@id` is never read as a name or a handle.** It is an XML id, and Subsurface's is the
literal string `owner`.

`<contact><email>` is UDDF's instance of `converting.md`'s constrained-member guard: one
holding `n/a`, a dash or a person's name is read as no email recorded, and reported.

### Dive sites — `/uddf/divesite/site`

| UDDF | DiveJSON |
| --- | --- |
| `name` | `sites[].name` — REQUIRED, so a nameless site is dropped |
| `geography/location` | `sites[].location` |
| `geography/latitude` + `longitude` | `sites[].position` |
| `notes/para` | `sites[].notes`, paragraphs joined with blank lines |

divelogs.de produces the nameless site: one it holds only a locality for exports as
`<name/>` with a `<location>`. Promoting the locality into the name would be inventing one,
so the dive arrives without a site instead — and every reference to that site goes with it.

Every site in a divelogs.de export also carries an exact `0.000000` / `0.000000` pair,
which is the file that taught `converting.md`'s rule that such a pair is not a position.

### Trips — `/uddf/divetrip/trip`

| UDDF | DiveJSON |
| --- | --- |
| `name` | `trips[].name` |
| `trippart/name` | `trips[].locations[].name` |
| `trippart/geography/location` | `trips[].locations[].display_name`, when it differs from the name |
| `trippart/geography/latitude` + `longitude` | `trips[].locations[].position` |
| `trippart/dateoftrip/@startdate`, earliest | `trips[].starts_on` |
| `trippart/dateoftrip/@enddate`, latest | `trips[].ends_on` |
| `trippart/notes/para` | `trips[].notes` |
| dive's `informationbeforedive/tripmembership/@ref` | `dives[].trip_uuid` |

`tripType` records no dates of its own, so a trip's span is the span of its parts — and a
trip whose parts carry none has nothing to put in `starts_on`.

UDDF also allows the opposite direction — `trippart/relateddives/link` pointing from the
trip at its dives. It is not read, because no writer in the corpus emits it.

### Gear — `/uddf/diver/owner/equipment`

The element's own name is the type. `name` is REQUIRED by §6.12, so a nameless piece is
dropped; `manufacturer/name` becomes `brand` and `notes/para` becomes `notes`. A dive's
`informationbeforedive/equipmentused/link/@ref` becomes `dives[].gear_uuids`.

| UDDF element | `gear.type` |
| --- | --- |
| `mask`, `fins`, `gloves`, `boots`, `compass`, `knife`, `light`, `camera`, `regulator` | the same value |
| `buoyancycontroldevice` | `bcd` |
| `divecomputer` | `computer` |
| `tank` | `cylinder` |
| `videocamera` | `camera` |
| `suit` | `drysuit` when `<suittype>` says so, else `wetsuit` |
| `variouspieces`, `lead`, `scooter`, `rebreather`, `compressor`, `watch` | `other` |

The last row is translation rather than invention: `<variouspieces>` is UDDF's own
catch-all, and a scooter and a weight belt are equipment this format's vocabulary does not
yet name. `<equipmentconfiguration>` is skipped — it describes how the pieces are rigged
together, not a piece.

### Dives — `/uddf/profiledata/repetitiongroup/dive`

Repetition groups are walked *through*: a group is a surface interval's worth of dives and
carries nothing this format records.

| UDDF | DiveJSON |
| --- | --- |
| `informationbeforedive/datetime` | `started_at` |
| `informationbeforedive/divenumber` | `dive_number` |
| `informationbeforedive/link/@ref` | `site_uuids`, in source order, the first being the primary site |
| `informationbeforedive/altitude` | `altitude` |
| `informationbeforedive/surfacepressure` | `surface_pressure` |
| `informationbeforedive/equipmentused/leadquantity` | `weight` |
| `informationafterdive/diveduration` | `duration` |
| `informationafterdive/greatestdepth` | `max_depth` |
| `informationafterdive/averagedepth` | `avg_depth` |
| `informationafterdive/lowesttemperature` | `bottom_temperature` |
| `informationafterdive/visibility` | `visibility` |
| `informationafterdive/notes/para` | `notes` |

`<datetime>` is the source of the UTC offset `converting.md` requires be carried through
untouched; a dive with no `<datetime>` at all is dropped, since §6.2 makes `started_at`
REQUIRED.

Two truncations are forgiven, both real writer output rather than hypotheticals:
`2002-06-18T` and a bare `2002-06-18` are read as midnight on that date and reported.
Subsurface emits the first for a midnight dive, its stylesheet building the string with an
unguarded concatenation. Dropping the dive over it would lose a dive to a writer's typo.

Zero is read two different ways here, which is `converting.md`'s zero rule meeting two
members with different constraints. A `<greatestdepth>` or `<averagedepth>` of `0` is **not
recorded**: UDDF makes `<greatestdepth>` mandatory where DiveJSON leaves `max_depth`
optional, so a writer with nothing to say has to put a zero there. A `<leadquantity>` of `0`
**is recorded**, since §6.2 makes `weight: 0` a diver's "no lead". *Known trap:* Subsurface
writes `<leadquantity>0</leadquantity>` for a logbook it holds no weights for, so a
converted Subsurface file says "no lead" where the diver's original log said 6 kg. That is a
loss on Subsurface's side, and reading it any other way would discard a genuine "no lead"
from every other writer.

A `<link>` under `informationbeforedive` addresses a site here; the schema also lets it
address a buddy or a shop, so one that resolves to something else is dropped with a note,
and one that resolves to nothing at all is reported as a source defect.

### Cylinders — `dive/tankdata`

| UDDF | DiveJSON |
| --- | --- |
| `tankvolume` | `cylinders[].volume` |
| `tankpressurebegin` | `cylinders[].start_pressure` |
| `tankpressureend` | `cylinders[].end_pressure` |
| `link/@ref` → `gasdefinitions/mix/o2` | `cylinders[].oxygen` |
| `link/@ref` → `gasdefinitions/mix/he` | `cylinders[].helium` |
| `link/@ref` → `gasdefinitions/mix/maximumpo2` | `cylinders[].po2_limit` |

`<gasdefinitions>` is not a collection of its own: DiveJSON carries the blend on the
cylinder that held it, so a mix nothing links to travels nowhere.

`converting.md`'s vessel-less cylinder is UDDF's `<tankdata>` with a gas link and no
`<tankvolume>`; the absent start pressure it describes is `<tankpressurebegin>` of `0`. And
UDDF records no cylinder numbering at all, which is why the converter has none to carry and
numbers only where a pressure channel or a gas switch needs it.

### Profile — `dive/samples/waypoint`

UDDF puts every reading taken at one instant inside one `<waypoint>`; DiveJSON splits them
into channels sampled on their own axes, and `converting.md`'s no-padding rule is why a
converted Subsurface dive keeps 431 depth samples beside 29 temperatures rather than
inventing 402 readings.

| UDDF | DiveJSON |
| --- | --- |
| `divetime` | the `times` entry for every channel this waypoint feeds |
| `depth` | `profile.depth` |
| `temperature` | `profile.temperature` |
| `tankpressure` (`@ref` → a mix → a cylinder) | `profile.pressures[]` |
| `setmarker` | an event |
| `switchmix` (`@ref` → a mix → a cylinder) | a `gas_switch` event |

- `<divetime>` is optional in the schema and is the only thing that can place a reading, so
  a waypoint without one is the timeless sample `converting.md` drops and reports.
- `<divetime>` is `xs:float` while §6.5's `times` are integers, which is what makes the
  same-second collision rule fire on real files.
- A `<setmarker>` whose text is exactly `deep_stop`, `safety_stop` or `bookmark` becomes
  that event type; anything else becomes an `"other"` event labelled with the text.
  `<setmarker>` is a bare string with no type beside it, so this is the only thing a round
  trip through UDDF has to go on.
- **Two cylinders on one blend link the same `<mix>`**, so a `@ref` resolves to a *list* of
  cylinders and repeated references on one waypoint take them in order. Collapsing them
  would put two readings on one second, which §6.5 forbids, and would say the diver carried
  one bottle. A `<tankpressure>` with no `@ref` at all — which the documentation permits for
  a linked double measured at one pressure — is taken as the dive's cylinder when there is
  exactly one, and dropped when there is a choice to get wrong.

## Three places UDDF does not hand over the answer

The last two are `converting.md`'s ambiguities: a value the source did record, whose scale
the file cannot settle, so a heuristic reads it at the scale it must have meant and reports
a finding of kind `resolved` when it fires. The first is the opposite shape and no ambiguity
at all — a required DiveJSON member with no UDDF source — and §6.4 settles it outright, so
it guesses nothing and reports nothing.

### `profile.duration` has no UDDF source

`<diveduration>` is the dive's *logged* duration and maps to `dive.duration`. §6.4 makes
`profile.duration` REQUIRED and at least the largest sample time in any channel — a
different quantity, and in practice a larger one: `<diveduration>` is **shorter** than the
sample span in both real exports on record, 4010 against 4300 and 4001 against 4288.

**Take the largest sample time across every channel. Never trim samples to make
`<diveduration>` fit** — that would delete recorded readings to satisfy a number, which is
exactly §5.4's violation. DiveJSON's own reference writer produces the same shape, a
`profile.duration` of 4301 over a logged `duration` of 4001. §6.4 defines the member as the
span of the samples themselves, so reading it off them is structural rather than derived,
and it carries no finding of any kind.

### `<tankvolume>`: cubic metres or litres

The documentation is unambiguous — "not in litres, as UDDF uses SI units!" — and Subsurface
wrote litres into the field until 2025-09-30. That fix is in no tagged release but is in the
6.0.x master builds people actually run, so **both spellings are live in the installed
base**, both are schema-valid, and no validator catches either. A fixture cannot settle it
because whichever build produced the fixture only demonstrates one of them.

**Heuristic: below 1, cubic metres; at 1 or above, already litres.** A cubic metre of water
capacity is a thousand-litre cylinder, and a litre-valued `0.012` would be twelve
millilitres. Bubbletrail's UDDF importer uses the same threshold. Reported as `resolved`
when the second branch fires.

### `<o2>` and `<he>`: fraction or percent

The documentation calls `<o2>` "a real number less or equal 1.0 in percent", which
contradicts itself, and writers took both readings: pre-2017 Subsurface wrote `<o2>34</o2>`
where current writers write `0.34`. Both are schema-valid.

**Heuristic: at or below 1, the documented fraction; above 1, already a percentage.** `1.0`
is pure oxygen rather than a 1 % mix, because a 1 % mix is not a breathing gas. Reported as
`resolved` when the second branch fires.

## Deliberately not mapped

| UDDF | why not |
| --- | --- |
| `<waypoint><decostop>` | a stop *schedule*, not a ceiling sample: `@duration` is required on it and a ceiling has none, several may appear on one waypoint, and no writer in the corpus emits any. `profile.ceiling` waits for a real file to map from. |
| `<waypoint><cns>`, `<otu>` | per-sample series where DiveJSON holds start/end scalars. Deriving the scalars from the last sample would present a derivation as recorded data, which §5.7 forbids. |
| `<waypoint><alarm>`, `<divemode>`, `<setpo2>`, `<nodecotime>`, `<heading>`, `<pulserate>` | no core member, and no fixture to map against. |
| `<informationbeforedive><surfaceintervalbeforedive>` | no core member. |
| `<informationafterdive><rating>`, `<current>`, `<problems>` | no core member. |
| `<site><ecology>` | site-level flora and fauna, where §6.11's species are per-dive sightings. |
| `<trippart><relateddives>` | the reverse of `<tripmembership>`; no writer in the corpus emits it. |
| `<mix><n2>`, `<ar>`, `<h2>` | §6.3 models the remainder as nitrogen and does not model argon or trace gases. |
| `courses`, `certifications`, `gear_sets`, `gear service` | UDDF has no slot for any of them. |

## Known writer artefacts

Things a converted file will show that are the source writer's doing rather than the
converter's. Recording them here saves the next reader the round trip.

- **Subsurface discards every depth-less waypoint** on import, so a temperature curve
  sampled on its own axis arrives decimated — 706 readings became 29 in one measured case.
  It also stores a temperature only where the value changes.
- **Subsurface fabricates a six-point depth profile** for a dive that has none, built from
  max depth and duration. It is in its stored data, not merely in its export, so nothing
  downstream can tell those samples from recorded ones. The converter carries them through
  as recorded rather than trimming them away: they are what the file says.
- **Subsurface recomputes duration and mean depth** from its own samples, so both can differ
  from the values the original log held.
- **Subsurface loses trips, gear and weights entirely** — its UDDF import reads no trip
  element at all, and the only equipment it reads is the dive computer's model.
- **divelogs.de reads a missing depth as zero**, producing a profile that saws between the
  seabed and the surface on every other sample, and writes `0.000000` coordinates for every
  site.
- **divelogs.de empties every string containing an ampersand** on export, while holding the
  correct value in its own database.
