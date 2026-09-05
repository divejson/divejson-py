# Reading UDDF into DiveJSON

**Non-normative.** The specification is [`spec/divejson.md`](../spec/divejson.md); nothing
here changes what a conforming document is. This document records what `divejson convert`
decided and why, so that the rules survive being reimplemented: the portable part of a
converter is its rules, not its code, and a port in another language starts here rather
than from `divejson/uddf.py`.

It is also where a future reader learns why a `0.012` and a `12` are the same cylinder.

Every claim about a real writer below was checked against a file that writer produced.
Where a rule rests on a file this repository does not carry — a personal export, a sample
from a public issue tracker — that is said in place rather than implied.

## Why a converter at all

UDDF is the nominal incumbent and the format most of a diver's history is trapped in. It
has been frozen since 2018, its own site's certificate has expired, and the documentation
contradicts its schema in at least one load-bearing place. What actually ships is a family
of dialects: two real third-party exports checked while this converter was written both
fail the 3.2.2 XSD, and they fail structurally rather than at the margins.

So the first rule is the one everything else follows from: **schema validity is never a
precondition**. The single most valuable file a converter is ever handed does not validate.

## Parsing

### Tags are matched on their lowercased local name

UDDF appears under at least four root shapes:

| shape | seen from |
| --- | --- |
| namespace `http://www.streit.cc/uddf/3.2/` | Subsurface, and this format's reference implementation |
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
sorted by their own recorded `<divetime>`, because §6.5 requires strictly increasing sample
times and nothing guarantees a writer emitted them in order.

### Leniency, itemized

- **Whitespace is stripped from ids and refs.** Subsurface writes one site id as
  `" ff47210"`, with the leading space, and the `<link ref>`s pointing at it carry the
  space too — so stripping has to happen on both sides or the reference stops resolving.
- **An empty element is absent, not zero.** `<latitude/>` is Subsurface saying it has no
  coordinates. This is the single most load-bearing leniency in the parser.
- **An id that is not an `NCName` is read anyway.** `mix(21/0)` carries parentheses,
  `2bbb3390` begins with a digit; both are what Subsurface writes, and both are refused by
  the XSD.
- **A number that is not finite, or is too large to carry, is absent.** `NaN` and
  `Infinity` are accepted by decimal parsers and are not readings. Neither is `1e999`: the
  bound is not physical — this format sets none on a depth or a temperature, and inventing
  one here would be a converter deciding how deep a dive can be — but a *representability*
  one. JSON numbers are doubles in every reader this format expects to meet, so a value
  past that range stops being a number on the way out: a serializer writes an overflowed
  float as a bare `Infinity` token no JSON parser accepts, a double-based parser reads an
  integer that large back as infinity, and a schema validator objects to neither. Reject
  it at the point the text is read, before any scale is applied to it, and the members
  derived from it are covered too.
- **Text is compared after stripping.** A `<name>` of pure whitespace is no name.

### A `<!DOCTYPE>` is refused outright

UDDF has no legitimate use for a document type declaration, and spec §9 requires readers
not to dereference anything found in a document. The standard library's XML parser blocks
external entities on its own, but it caps entity *amplification* only in recent libexpat —
a several-hundredfold blowup still parses on older ones, and that is a property of the
library version rather than a guarantee the language makes. Refusing the declaration is the
guarantee, it costs nothing real, and it avoids taking on a second runtime dependency in a
package that has exactly one.

The refusal fires from the parser's doctype hook, which runs before a single entity
reference in the content has been expanded.

## Units

UDDF is SI throughout and DiveJSON is not, and this is the highest-risk part of a
converter: a wrong factor produces a document that validates perfectly and describes a dive
nobody took. Nothing downstream catches it — a validator accepts any integer as a profile
sample, and a count of samples is not a value.

**The channel conversions carry a scale the scalar ones do not.** That is the trap. The
most-executed conversion in the whole converter is depth samples ×100, and a list of the
scalar conversions alone does not contain it.

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

Arithmetic runs on decimal values parsed from the source text, not on floating point:
`2.6 × 100` is exactly `260` that way, where the float route arrives at
`260.00000000000003` and has to be rounded back out. Rounding to an integer is
half-away-from-zero — a reading of 2.5 seconds is 3, not the 2 that banker's rounding
gives.

## Identity

UDDF ids are XML Names. DiveJSON requires a UUID on every record, and §5.3 asks that
identifiers be stable across exports of the same data, which a fresh random UUID per run
breaks. So:

1. **If the source id already contains a UUID, reuse it.** `xs:ID` is an `NCName` and
   cannot begin with a digit, which a hex UUID regularly does, so a writer holding real
   UUIDs prefixes them — this format's reference implementation writes
   `dive-019fec36-b9ec-71c6-a03e-64f59b8b92b1`. A short alphabetic prefix followed by a
   canonical UUID is stripped. This is what lets a logbook that went out through UDDF come
   back recognisable.
2. **Otherwise, UUIDv5 over a fixed namespace and `"{kind}:{source id}"`.** The namespace
   is `1b85a949-d5f7-5d67-9d04-dcc78342f907`, itself
   `uuid5(NAMESPACE_URL, "https://divejson.org/ns/uddf")`. It is fixed forever: changing it
   would renumber every document any released converter has produced.
3. **The record kind is in the hash, and that is not decoration.** A source id is not
   unique within a file — every `<dive>` in a Subsurface export reuses its enclosing
   `<repetitiongroup>`'s id — so hashing the bare id would hand a dive and its group one
   UUID.
4. **A record with no id gets its position in the file**, reported as such: its identity is
   stable for an unchanged file and moves if the file's order changes.
5. **Two records of the same kind sharing an id is a source defect.** The second is dropped
   and reported, because two records cannot share one identity.

Two properties worth stating rather than discovering. Derived identities are a function of
the source id alone, so two files from *different* divers that both use the id `owner`
produce the same diver UUID — §5.3 is explicit that UUIDs are not portable identities for
shared realities, so this is within the rules, but it means converted logbooks are not
safe to merge on UUID. And a source id that changes between exports changes the identity
with it; nothing can recover from a writer that does not keep its own ids stable.

## The element map

Everything the converter reads. Anything not here is not read — see *deliberately not
mapped* below.

### Document

| UDDF | DiveJSON |
| --- | --- |
| `/uddf/@version` | `extensions.divejson.uddf_version` |
| `/uddf/generator/name`, `/version` | `extensions.divejson.source_generator` |
| — | `generator` — **the converter**, not the source; §4 defines it as what produced *this* document |
| — | `exported_at` — the moment of conversion, always offset-aware |

The provenance block rides under the `divejson` producer key (§5.5) because the source's
own identity is worth keeping and the core vocabulary has nowhere for it.

### Diver — `/uddf/diver/owner`

| UDDF | DiveJSON |
| --- | --- |
| `personal/firstname` + `middlename` + `lastname` | `diver.name`, joined with spaces |
| `contact/email` | `diver.email` |
| `@id` | `diver.uuid` |

**`@id` is never read as a name or a handle.** It is an XML id, and Subsurface's is the
literal string `owner`. An owner with nothing else recorded produces no `diver` member at
all: §6.1 says a converter whose source records nothing about an owner omits it entirely,
because minting identity for one would be §5.4's fabrication applied to people.

**`email` is the one member whose type constrains the text a source may put in it**, and
the only source string that is checked rather than merely capped. Every other one reaches a
free-text member where the only limit is a length. So a `<contact><email>` holding `n/a`, a
dash or a person's name is read as no email recorded and reported — a member the format
cannot hold is a member the source did not fill in. A converter that passed it through
would emit a document that fails its own validation, and since that is treated as the
converter's bug rather than the file's, one unusable header field would discard an entire
logbook. Any mapping added later that lands a source string on a constrained member owes
the same guard.

### Dive sites — `/uddf/divesite/site`

| UDDF | DiveJSON |
| --- | --- |
| `name` | `sites[].name` — REQUIRED, so a nameless site is dropped |
| `geography/location` | `sites[].location` |
| `geography/latitude` + `longitude` | `sites[].position` |
| `notes/para` | `sites[].notes`, paragraphs joined with blank lines |

A **nameless site is dropped along with every reference to it**, because §6.10 requires a
name and §5.3 forbids a dangling reference. divelogs.de produces this: a site it holds only
a locality for exports as `<name/>` with a `<location>`. Promoting the locality into the
name would be inventing one, so the dive arrives without a site instead.

**An exact `0.000000` / `0.000000` pair is not a position.** Every site in a divelogs.de
export carries it, and Null Island is a place: a reader that trusts it pins a Red Sea wreck
into the Atlantic. Half a pair is not a position either — §6's Position object makes both
members REQUIRED, which is §5.4 enforced by shape.

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

`tripType` records no dates of its own, so a trip's span is the span of its parts. A trip
with no dates at all is dropped: §6.8 makes `starts_on` REQUIRED and there is nothing to
put there.

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

**The offset on `<datetime>` is preserved exactly as recorded and never supplied.** That is
§5.2's whole point, and converting to UTC — or assuming an offset where the source recorded
none — is the failure every tested UDDF consumer produced. A dive with no `<datetime>` at
all is dropped, since §6.2 makes `started_at` REQUIRED.

Two truncations are forgiven, both real writer output rather than hypotheticals:
`2002-06-18T` and a bare `2002-06-18` are read as midnight on that date and reported.
Subsurface emits the first for a midnight dive, its stylesheet building the string with an
unguarded concatenation. Dropping the dive over it would lose a dive to a writer's typo.

Zero is read two different ways, and the difference is the rule rather than an
inconsistency. A `<greatestdepth>` or `<averagedepth>` of `0` is **not recorded**: UDDF
makes `<greatestdepth>` mandatory where DiveJSON leaves `max_depth` optional, so a writer
with nothing to say has to put a zero there. A `<leadquantity>` of `0` **is recorded**:
§6.2 makes `weight: 0` a diver's "no lead", distinct from absence. Which way a zero reads
follows the format's own constraint on the member — `> 0` means the zero was a placeholder,
`≥ 0` means it was an answer. *Known trap:* Subsurface writes `<leadquantity>0</leadquantity>`
for a logbook it holds no weights for, so a converted Subsurface file says "no lead" where
the diver's original log said 6 kg. That is a loss on Subsurface's side, and reading it any
other way would discard a genuine "no lead" from every other writer.

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

**A `<tankdata>` with a gas link and no `<tankvolume>` is a cylinder with its vessel
members absent**, which §6.3 names in as many words. It is not a defect and it is not
skipped.

**A `<tankpressurebegin>` of `0` is a device's absent-marker.** §6.3 says outright that
writers must not emit one, so it is read as not recorded rather than as an empty cylinder.

A dive's cylinders are numbered from 0 in document order — and **only** when the profile
needs the numbering, to tie a pressure channel or a gas switch to its cylinder. §6.3 calls
`gas_number` "a label, not an array index", and UDDF records no numbering at all, so the
converter does not assert one where nothing depends on it.

### Profile — `dive/samples/waypoint`

UDDF puts every reading taken at one instant inside one `<waypoint>`; DiveJSON splits them
into channels sampled on their own axes. **The waypoints set the time axis, and each channel
takes only the waypoints that actually carried a reading for it.** No channel is padded to
another's length. This is why a converted Subsurface dive keeps 431 depth samples beside 29
temperatures rather than inventing 402 readings.

| UDDF | DiveJSON |
| --- | --- |
| `divetime` | the `times` entry for every channel this waypoint feeds |
| `depth` | `profile.depth` |
| `temperature` | `profile.temperature` |
| `tankpressure` (`@ref` → a mix → a cylinder) | `profile.pressures[]` |
| `setmarker` | an event |
| `switchmix` (`@ref` → a mix → a cylinder) | a `gas_switch` event |

- A waypoint with **no `<divetime>`** has no place on the axis and is dropped, reported.
  `<divetime>` is optional in the schema and is the only thing that can place a reading.
- Waypoints whose readings are all unusable produce **no profile at all**, rather than one
  carrying a bare `duration: 0`. A zero-length sampled record is a claim the source did not
  make. This is reported, unlike a dive that simply has no `<samples>`: the source did
  record a profile, and this is the converter unable to carry it — the same class as a
  dropped waypoint rather than an absence.
- `<divetime>` is `xs:float` while §6.5's `times` are strictly increasing integers, so two
  waypoints that round to the same second keep the first and report the second.
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

## The three ambiguities

None of these has a clean answer. Each is handled explicitly and reported when its
heuristic fires, because a silent guess is the failure this converter exists to avoid.

### `profile.duration` has no UDDF source

`<diveduration>` is the dive's *logged* duration and maps to `dive.duration`. §6.4 makes
`profile.duration` REQUIRED and at least the largest sample time in any channel — a
different quantity, and in practice a larger one: `<diveduration>` is **shorter** than the
sample span in both real exports on record, 4010 against 4300 and 4001 against 4288.

**Take the largest sample time across every channel. Never trim samples to make
`<diveduration>` fit** — that would delete recorded readings to satisfy a number, which is
exactly §5.4's violation. DiveJSON's own reference writer produces the same shape, a
`profile.duration` of 4301 over a logged `duration` of 4001.

### `<tankvolume>`: cubic metres or litres

The documentation is unambiguous — "not in litres, as UDDF uses SI units!" — and Subsurface
wrote litres into the field until 2025-09-30. That fix is in no tagged release but is in the
6.0.x master builds people actually run, so **both spellings are live in the installed
base**, both are schema-valid, and no validator catches either. A fixture cannot settle it
because whichever build produced the fixture only demonstrates one of them.

**Heuristic: below 1, cubic metres; at 1 or above, already litres.** A cubic metre of water
capacity is a thousand-litre cylinder, and a litre-valued `0.012` would be twelve
millilitres. Bubbletrail's UDDF importer uses the same threshold. Reported when the second
branch fires.

Dropping the member instead would lose every cylinder's size on those Subsurface builds,
which is why this is not the "refuse rather than guess" case: the value **was** recorded and
only its scale is in doubt, so the magnitude test interprets data rather than inventing it.

### `<o2>` and `<he>`: fraction or percent

The documentation calls `<o2>` "a real number less or equal 1.0 in percent", which
contradicts itself, and writers took both readings: pre-2017 Subsurface wrote `<o2>34</o2>`
where current writers write `0.34`. Both are schema-valid.

**Heuristic: at or below 1, the documented fraction; above 1, already a percentage.** `1.0`
is pure oxygen rather than a 1 % mix, because a 1 % mix is not a breathing gas. Reported
when the second branch fires.

## Refuse rather than guess, and say which

Every case where the source **did not record** a member resolves to omitting it and
reporting that, never to a plausible default. §5.4 is the rule; the report is how a diver
learns what their file did not carry. In particular:

- No implied air for a cylinder with no gas — §6.3 says absent oxygen means not recorded,
  not 21.
- No `(0, 0)` for a missing position, and no half a position.
- No synthesized samples, and no offset supplied where the source recorded none.
- No name invented for a record whose format-required name is missing; the record and the
  references to it go instead.

The two unit ambiguities above are deliberately *not* this case, for the reason given
there.

## The report

Every conversion returns a list of findings alongside the document. Each names a location
in the **source** file — `dive/0`, `dive/0/tankdata/1`, `site/3`, `$` — because that is
where a diver looking for a missing value has to go. Indices are zero-based and count
elements of that kind in document order.

The command line groups findings by message, since one habit of a whole file produces one
finding per record and a thousand-dive logbook would otherwise bury the interesting ones.

**The converter validates its own output before it is written.** A converter that can emit
a non-conforming document is a bug factory, and every way a source can be wrong is supposed
to resolve to an omission and a note — so reaching a validation failure is a bug in the
converter rather than a property of the file, and it is reported as one.

## Deliberately not mapped

Listed rather than left silent, because a port needs to know these were considered.

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
