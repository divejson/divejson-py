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
| `profile.ndl` values | seconds | `<waypoint><nodecotime>` | seconds | — |
| `profile.ppo2` values | hundredths of a bar | `<waypoint><calculatedpo2>` | bar or Pascal, and see below | × 100, or ÷ 1000 |
| `profile.cns` values | tenths of a percent | `<waypoint><cns>` | percent | × 10 |
| `profile.gradient_factor` values | whole percent | `<waypoint><gradientfactor>` | percent or fraction, and see below | — , or × 100 |
| `deco_model.gf_low`, `.gf_high` | whole percent | `<gradientfactorlow>`, `<gradientfactorhigh>` | fraction, and see below | × 100, or — |
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
dropped; `manufacturer/name` becomes `brand`, `serialnumber` becomes `serial` and
`notes/para` becomes `notes`. A dive's
`informationbeforedive/equipmentused/link/@ref` becomes `dives[].gear_uuids`.

`<serialnumber>` is on `equipmentPieceType`, so it is read for **every** gear type that
carries one and not only for a computer — which is what §6.12's own member says. On a
computer it is the member that lets a writer recognise the kit item and a recording's
device (§6.4b) as one machine without comparing names, and
[`uddf-writing.md`](uddf-writing.md) is where that test is written down. `<model>` is not
read: §6.12 has no member for it, and on a computer the model is the *device's*
(§6.4b), read from the same element below.

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

**`<divecomputer>` is one element read two ways.** It is a piece of kit the diver owns, and
it is the hardware that recorded a dive, and UDDF has one element for both — alone among
the formats here. So the reader does both: it keeps minting the gear item of type
`computer`, exactly as the table above says, **and** reads a recording's device (§6.4b) from
the same element for every dive whose `<equipmentused><link>` names it. The two are
different records of one object rather than one record written twice: the gear item is a
thing in a kit list and the device is what a recording says about the hardware. They
overlap on this format more than on any other — a `<name>` and a `<serialnumber>` each land
on both — and that overlap is the point, being exactly what lets a writer recognise the two
as one computer again. A dive that links no computer gets no device, which is most files
— see *Device* below.

### Device — the linked `<divecomputer>` elements

| UDDF | DiveJSON (§6.4b) |
| --- | --- |
| `divecomputer/manufacturer/name` | `brand` |
| `divecomputer/model` | `model` |
| `divecomputer/serialnumber` | `serial` |
| `divecomputer/name` | `name` |
| `informationbeforedive/internaldivenumber` | `dive_number`, the **device's** counter |

**`<divecomputer><name>` is read twice, into two members of two records, and that is not a
duplication.** It is the only string in this format that names the computer at all, and the
two members it lands in mean different things: §6.12's `name` is the diver's label for a
thing in their kit list, and §6.4b's `name` is what the device calls itself. UDDF has one
element for both because it has one element for the whole computer. Reading it only as the
gear item's name would leave a computer stated at this format's usual width with a device
that has no string naming it — `opendiving.uddf` below carries a `<name>` and no `<model>`,
where `shearwater-cloud.uddf`'s element states both — and reading
it into `model` instead would put `Ocean` where the same dive's FIT export puts
`Suunto Ocean`, conflating two members §6.4b defines separately.

A `<name>` of pure whitespace is no name (*What UDDF's leniencies look like* above), so it
yields neither, and an **empty** `<name>` is the same answer — which is what makes a written
file round-trip: [`uddf-writing.md`](uddf-writing.md) emits an empty one for a device that
has no name, `<name>` being mandatory on the element.

`<internaldivenumber>` is on the dive rather than on the element, which is where UDDF puts
it, and it is why a device read from a shared element still differs between two dives that
link it. It is the counter §6.2's `dive_number` is explicitly not — `<divenumber>` is the
diver's and stays there.

**A device whose every member is absent is not written at all** (§6.4b). With `<name>` on
the list above, that now takes an element carrying **nothing** this reader maps — no
`<name>`, no `<manufacturer>`, no `<model>`, no `<serialnumber>` — on a dive that also
states no `<internaldivenumber>` the element is entitled to, which the multi-computer rule
below makes the dive's first link alone.
An element carrying only a `<name>` yields both records: a gear item named by it and a
device named by it.

`fixtures/uddf/opendiving.uddf`, the reference writer's own export, is the shape to expect.
It has one `<divecomputer>` with a `<name>` and a `<manufacturer><name>` and no `<model>`,
`<serialnumber>` or `<internaldivenumber>` — so its dive's recording carries a device of
exactly two members, `{"brand": "Suunto", "name": "Ocean"}`, and the gear item carries the
same name beside its own uuid and notes. UDDF records what the diver owns far more often
than it records what recorded the dive, and on this format the two overlap almost entirely:
a device read here is usually the kit item read again, which is why the fold going the other
way ([`uddf-writing.md`](uddf-writing.md)) matters as much as it does.

UDDF has no equipment element for a firmware version, so §6.4b's `firmware` has no source
here.

**A dive may link more than one computer, and each linked `<divecomputer>` is a recording**
(§6.4a), in the order the dive's `<equipmentused>` links them. That is `converting.md`'s
general rule rather than anything of UDDF's, and it is the same shape `.ssrf` reaches from
its own repeated element — the carve-out included, and in `converting.md`'s own words: a
link that yields **neither a device nor a profile** yields no recording, §6.4a forbidding
one that carries nothing.

**The first link is where the dive's own once-per-dive facts land**, both of them, because
UDDF states each once per **dive** and never once per computer:

- **Its `<samples>` become that first recording's profile.** A dive has one `<samples>`
  element, so there is nothing to give the others and nothing to divide, and every recording
  after the first is device-only. That profile is also the only thing that carries a link
  past the carve-out without a device, and it reaches exactly one link — so **where the dive
  has a profile**, the first link is a recording whether or not its element names a device,
  exactly as a dive linking no computer at all is one recording made of its samples alone,
  and the carve-out bites only on the *later* links whose elements name none. **Where the
  dive has no profile** — an absent `<samples>`, or samples `converting.md` found wholly
  unusable — there is nothing to protect any link, so every one of them is judged on its
  device alone, the first included, and a dive whose elements all name none has no recordings
  at all.
- **Its `<internaldivenumber>` joins that first link's device**, and only that one's. It is a
  child of the dive (above), so a dive linking two computers has one counter and no way to
  say whose it is; giving it to the first is the only reading that does not put one machine's
  count on another's device. Confining it there is also what keeps the rule above from
  circling: every **later** link's device is made of that element's own four members and
  nothing the dive supplies, so whether it yields a device never depends on which link came
  first — while the first link's device may still be a counter and nothing else, which is why
  the emptiness test above names the dive's `<internaldivenumber>` beside the element's own.

**No file in this corpus links two**, so this has no pair of its own and is written down
here rather than discovered by the first reader to meet one.

**It is not a round trip, and the writing direction says so first.** A document whose dive
carries more than one recording cannot be written whole — one `<samples>` per dive — so
[`uddf-writing.md`](uddf-writing.md) writes the primary's samples and **reports every other
recording as dropped**, keeping its device in `<equipment>` so that what was worn is not
lost with what it sampled. Nothing here recovers a dropped recording, and **the order does
not survive either**: a dive's links come from its `gear_uuids`, in the diver's own order,
with a link appended after them for every device that took an element of its own, so which
computer reads back first is a fact about the kit list rather than about the recordings. A
reader must not take the first link as evidence that its computer was the document's
primary — there is no such evidence in the file. This rule exists to read somebody else's
two-computer file; on output from the writer above it returns what that writer's report
already said would come back.

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
| `nodecotime` | `profile.ndl` |
| `calculatedpo2` | `profile.ppo2` |
| `cns` | `profile.cns` |
| `gradientfactor` | `profile.gradient_factor` |
| `divemode` `@type` | the recording's `mode`, from the first waypoint that states one |

- `<divetime>` is optional in the schema and is the only thing that can place a reading, so
  a waypoint without one is the timeless sample `converting.md` drops and reports.
- `<divetime>` is `xs:float` while §6.5's `times` are integers, which is what makes the
  same-second collision rule fire on real files.
- A `<setmarker>` whose text is exactly `deep_stop`, `safety_stop` or `bookmark` becomes
  that event type; anything else becomes an event with **no `type`** and the text as its
  `label`, which §6.6 makes the spelling of an unclassified event. `<setmarker>` is a bare
  string with no type beside it, so this is the only thing a round trip through UDDF has to
  go on.
- **`<divemode>` states the recording's mode.** `divemodeType` enumerates five values and
  DiveJSON's `mode` five, and they are not the same five:

  | `<divemode @type>` | `mode` |
  | --- | --- |
  | `opencircuit` | `open_circuit` |
  | `closedcircuit` | `closed_circuit` |
  | `semiclosedcircuit` | `semi_closed` |
  | `apnoe` | `freedive` |
  | `apnea` | `freedive` |

  **Two spellings of one mode**, because UDDF has two: `apnoe` is the original and `apnea`
  was added beside it in 2017 as the English word, both are current in 3.2.x, and a reader
  that knew only one would drop every freedive from whichever half of the installed base
  wrote the other. **`gauge` is the value UDDF does not have** — §6.4a's fifth mode has no
  counterpart here at all, which is a fact about UDDF rather than about this reader, and
  `uddf-writing.md` is where it costs something. A `@type` outside the table, or a
  `<divemode>` with no `@type`, leaves the recording's `mode` absent and is reported: §6.4a
  forbids assuming open circuit, and the element is not schema-valid without one anyway.
- **Only a change of value is an event.** The
  first waypoint that carries one gives the recording its `mode`; a later waypoint stating a
  *different* value is reported `dropped`, because the only place §6.6 could carry a switch
  is an event and an event with no type needs a label the file does not supply — writing one
  would be inventing the device's wording (§5.4). A later waypoint that simply stops stating
  the mode is not a change: an absence is not a claim. No file in hand changes mode
  mid-dive; `fixtures/uddf/shearwater-cloud.uddf` states `opencircuit` on its first eight
  waypoints and nothing on its last four.
- **A `<nodecotime>` of 5940 is a reading**, not a missing one: 99 minutes is the Shearwater
  display maximum and it means *at least this*. `converting.md`'s display-cap rule is what
  carries it through.
- **Two cylinders on one blend link the same `<mix>`**, so a `@ref` resolves to a *list* of
  cylinders and repeated references on one waypoint take them in order. Collapsing them
  would put two readings on one second, which §6.5 forbids, and would say the diver carried
  one bottle. A `<tankpressure>` with no `@ref` at all — which the documentation permits for
  a linked double measured at one pressure — is taken as the dive's cylinder when there is
  exactly one, and dropped when there is a choice to get wrong.

### The decompression model — `<decomodel>`

`<decomodel>` is a top-level container of `<buehlmann>`, `<vpm>` and `<rgbm>`, each with an
`@id`, and a dive reaches its own through a `<link>` under `<informationbeforedive>`. That
link is how the model becomes *this dive's* rather than the file's: a logbook holds one
`<decomodel>` block and many dives, and nothing says every dive ran the same model.

| UDDF | DiveJSON |
| --- | --- |
| `<decomodel><buehlmann @id>` a dive links | `recordings[].deco_model.algorithm: "buhlmann"` |
| `<buehlmann><gradientfactorlow>`, `<gradientfactorhigh>` | `deco_model.gf_low`, `.gf_high` |

- **`<vpm>` and `<rgbm>` are not mapped**, and they are on the not-mapped list below with
  that reason: no file in hand carries either, and a mapping no pair exercises is one
  nothing checks. §6.4c's `algorithm` is OPTIONAL, so each arrives in a minor version with
  the file that first needs it.
- **A `<link>` that resolves to a `<decomodel>` child is no longer a dangling reference.**
  Until this mapping existed it fell through to the "not a site this converter carries"
  note, which is what `fixtures/uddf/shearwater-cloud.uddf`'s `<link ref="zhl16c" />`
  produced. It resolves now and is reported nowhere.
- **The `<tablegeneration><calculateprofile><profile><decomodel>` route is not read.** That
  element names the model a *recalculation* used, which is the application's arithmetic
  rather than the device's, and §6.4c's object is what the device ran.

Two of this format's unit ambiguities are the deco model's, and both are below under
*Where UDDF does not hand over the answer*: `<calculatedpo2>`'s bar-or-Pascal, settled on
the value, and the gradient factors' percent-or-fraction, settled on the generator.

## Generators this reader knows

`converting.md` settles a *meaning* on the writer, not on the value, and this is the table
that does it: an entry here changes how one generator's files are read and no others'. It is
the sharpest tool in this document — a wrong entry misreads every file that generator ever
produced — so it gains a row only against real files, and each row says which.

| `<generator><name>` | what is read differently |
| --- | --- |
| `Shearwater Cloud Desktop` | a `Z` on a dive's `<datetime>` means **no offset**; the gradient factors are **whole percent**, not fractions |

**The Shearwater `Z` is the local wall clock, not UTC.** Shearwater Cloud Desktop writes the
time the diver read off their wrist and suffixes it `Z`, so the instant the file appears to
state is wrong by the diver's own offset — three hours, for the Red Sea export this rule was
written against, where a Perdix 3 stamped `15:18:10Z` for the same moment a Suunto beside it
on the same wrist stamped `15:17:38+03:00`. So under this generator the `Z` is read as
absent: the value becomes a **local date-time** (§5.2's third state, the wall clock
with the instant unknown) and the report carries a `resolved` finding — "the generator writes
the local wall clock with a `Z` suffix; read as a wall clock with no offset (spec §5.2)". It
is `resolved` rather than `inferred` because the digits written are the ones the source
recorded and only their meaning was in doubt, so nothing goes under
`extensions.divejson.inferred`.

The alternatives are both worse. Trusting the `Z` puts the dive three hours from where it
happened and can never pair it with the same dive off another computer. Correcting it with
an offset would be the fabrication §5.2 forbids outright — a wall clock with no offset is a
state this format has precisely so a converter never has to invent one.

Matching is on the exact `<generator><name>` string, with the manufacturer id
`Shearwater_Research_Inc` checked beside it: a name alone is a string anything may claim,
and two agreeing beats one. `<generator><datetime>` is left alone under this rule and every
other — it is the export instant, a fact about the run rather than logbook data.

**No first-party statement of this exists either way**, which is why the rule is keyed on
the generator rather than asserted as the format's. What is on record: the owner of the two
files confirms the wall clock; a third-party reader states in its own source that
Shearwater's exports carry "a wall-clock reading stored as if it were a UTC epoch";
Subsurface's import is consistent with it, copying the time part verbatim and ignoring a
trailing `Z`; and one issue asserts the opposite with no evidence behind it. Three further
Shearwater Cloud Desktop exports in public repositories carry the same shape. **The rule now
has a pair**, `fixtures/uddf/shearwater-cloud.uddf` being a reduction of the Perdix 3 export
quoted above, so what it is held to is a file this repository carries — while the evidence that
the reading is the right one is still the record above rather than anything in the corpus,
which is `converting.md`'s rule about a claim resting on a file that is not here.

## Where UDDF does not hand over the answer

Most of what follows is `converting.md`'s ambiguity of *scale*: a value the source did
record, whose scale the file cannot settle, so a heuristic reads it at the scale it must have
meant and reports a finding of kind `resolved` when it fires. The generator table above is
the same kind of finding settled the other way, on the writer rather than on the value — and
the gradient factors below are the case that has to be settled that way. The first item is
the opposite shape and no ambiguity at all — a required DiveJSON member with no UDDF source —
and §6.4 settles it outright, so it guesses nothing and reports nothing.

The heading carries no count on purpose: it had one, and one more ambiguity is exactly the
sort of thing that arrives without the heading being corrected.

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

### `<calculatedpo2>`: bar or Pascal

The documentation states Pascal, and Shearwater Cloud Desktop writes `0.399999976` for a
ppO₂ of 0.4 bar. The two spellings are three orders of magnitude apart and a breathable ppO₂
lives between about 0.1 and 2 bar, so nothing overlaps.

**Heuristic: at or below 10, bar; above it, Pascal.** Reported as `resolved` when the
second branch fires. §6.4's `ppo2` is hundredths of a bar, so the first branch multiplies by
100 and the second divides by 1 000.

### `<gradientfactor>` and the GF pair: percent or fraction, on the generator

`<gradientfactorlow>` and `<gradientfactorhigh>` are documented as fractions with
`0.0 ≤ GF Low ≤ GF High ≤ 1.0`; the per-waypoint `<gradientfactor>` is documented as "a
percentage as a real number" with no range at all, its one example `0.8` glossed as 80 %.
Shearwater Cloud Desktop writes whole percent in all three — `50` and `85` for the pair,
`0`, `1` and `3` to `17` per waypoint.

**A magnitude test cannot settle the per-waypoint one**, which is what makes this the
generator table's case rather than a heuristic's. Nearly every value in hand is `0` or `1`,
and the `<o2>` shape would read a `1` as 100 %: a leading tissue sitting at its M-value on a
15 m no-decompression dive, which is not what the file says. So the rule is the writer's: a
generator the table names writes whole percent, read verbatim and reported `resolved`; any
other is read as the documented fraction and multiplied by 100. Both the pair and the
per-waypoint channel follow the one rule, because a file that writes one of them in percent
writes all three that way.

## Deliberately not mapped

| UDDF | why not |
| --- | --- |
| `<waypoint><decostop>` | a stop *schedule*, not a ceiling sample: `@duration` is required on it and a ceiling has none, several may appear on one waypoint, and no writer in the corpus emits any. `profile.ceiling` waits for a real file to map from. |
| `<waypoint><otu>` | §6.4 has a `cns` channel and no OTU one, and no file in hand carries this element. Deriving the dive's `otu_start`/`otu_end` scalars from the last sample would present a derivation as recorded data, which §5.7 forbids. |
| `<waypoint><measuredpo2>` | per cell by construction: its `@ref` to an O₂ sensor is mandatory and the element may repeat inside one waypoint, once per sensor. §6.4's `ppo2` is the one figure the computer calculated, which is `<calculatedpo2>`; a per-cell reading has no member yet. |
| `<waypoint><alarm>`, `<setpo2>`, `<heading>`, `<pulserate>` | no core member, and no fixture to map against. `<setpo2>` is a *maximum tolerated* ppO₂ rather than a rebreather setpoint, which is the member it would otherwise look like. |
| `<decomodel><vpm>`, `<rgbm>` | no file in hand carries either, so §6.4c's `algorithm` has no value seeded for them yet. |
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
