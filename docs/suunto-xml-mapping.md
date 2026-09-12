# Reading Suunto's DM5 XML into DiveJSON

**Non-normative.** The specification is [`spec/divejson.md`](../spec/divejson.md); nothing
here changes what a conforming document is. The rules every converter follows whatever it
is reading are in [`converting.md`](converting.md) and are **not repeated here** — the note
kinds, identity, the way a zero reads, the representability bound, sample ordering, the
`<!DOCTYPE>` refusal, where a fix belongs. This document carries what is this format's.

Every claim below is checked against **384 one-dive exports** of one diver's logbook,
covering 2021 to 2025 and three firmware versions of a Suunto D5. That corpus is not in
this repository — it is a personal export — so where a figure comes from it, this document
says so in place rather than implying a file you can open. What *is* here is
[`fixtures/suunto_xml/`](../fixtures/suunto_xml), five pairs reduced from or modelled on it.

## Why this format, and what makes it different

Suunto's desktop application — DM5, and the Suunto app's desktop export after it — writes
**one XML document per dive**, rooted at a `<Dive>` in the `Suunto.Diving.Dal` datacontract
namespace. It is a .NET serializer's rendering of the application's own dive record, which
gives the format three habits no other source in this corpus has.

**Every member of the contract is emitted on every document**, filled in or not, so the
presence of an element says nothing at all. `<BatteryLevel i:nil="true" />` appears in all
384 exports and carries a reading in none of them. Absence is spelled `i:nil="true"`, which
is `converting.md`'s empty-is-absent rule with a name on it.

**A logbook is a directory, not a file.** One dive per document means an account's whole
logbook is a folder of them, which reaches a converter as an archive — `converting.md`'s
*A container is one logbook* — with each dive's `where` paths and positional identity
prefixed by its own filename.

**The same vendor exports the same dives in a second format**, the Suunto app's JSON
([`suunto-json-mapping.md`](suunto-json-mapping.md)), and the two disagree about three
units. That is what makes the *Units* section below checkable rather than assumed, and it
is the only reason any of it is visible: each file is internally consistent and plausible
on its own.

## Parsing

### The shape is a namespaced `<Dive>`, and the format id is `suunto_xml`

`suunto_xml` rather than `suunto`, because the same vendor's mobile application exports a
different format this corpus also covers.

The sniff asks for the root element's local name **and** the datacontract namespace, where
the corpus's other XML formats are recognised by name alone. `<uddf>` and `<divelog>` are each
one format's and nothing else's; `<dive>` is a name any dive-log format might reach for, so
claiming it alone would have this reader answering for files it cannot read. The namespace
declaration sits on the root element, so it is inside any bounded head that reached the root.

Reading a document a caller has **named** asks for less — the root name alone — so a file
whose namespace is spelled differently is still read when somebody said what it was.
Deciding what a file *is* wants the tighter test; reading one already named wants the
leniency `converting.md` asks for everywhere else.

### `i:nil="true"` is checked, not inferred from emptiness

A nil element is empty, so `converting.md`'s empty-is-absent rule would cover every document
in hand on its own. The attribute is checked anyway, because it is the format's own word for
*not recorded* and a reader that noticed only the emptiness would read
`<Ceiling i:nil="true">0</Ceiling>` as a ceiling. No file in hand writes that; the check is
what makes it not matter.

### `<Mode>` is the recording's mode, and a `<Mode>3</Mode>` freedive is carried

`<Mode>` is the element this format states a dive's kind in, and §6.4a's `mode` is where it
goes. Every record this format holds is a dive of some kind, so nothing is skipped for its
mode and the reader has no not-a-dive test at all — where the app's JSON export needs one,
because a run and a dive are the same shape there.

| `<Mode>` | `mode` | seen on |
| --- | --- | --- |
| `0` | `open_circuit` | 244 exports |
| `1` | `open_circuit` | 98 exports |
| `3` | `freedive` | 42 exports |

**0 and 1 are air and nitrox, which are both open circuit.** The oxygen fractions say so:
243 of the 244 `<Mode>0</Mode>` exports carry a single 21 % mixture, and every one of the 98
`<Mode>1</Mode>` exports carries a mixture richer than that, up to 52 %. **3 is a freedive**,
and the 42 that state it are exactly the 42 that carry no `<DiveMixture>` at all:
`<Algorithm>`, `<DiveTime>` and `<BottomTime>` nil, durations of 3 to 60 seconds, depths of
1.39 to 15.48 m. So an archive of the whole directory now converts to **384 dives**, where it
used to produce 342 and a report saying why the other 42 were missing — one member, and the
data loss goes.

A document that states **no** `<Mode>`, or one outside the table, leaves the recording's
`mode` absent and is read on: absence is not a claim, §6.4a forbids assuming open circuit,
and `converting.md`'s first rule is that schema validity is never a precondition.
`fixtures/suunto_xml/unlabelled-pressure.xml` is the nil case.

**`<PersonalMode>` is the deco model's `conservatism`**, on Suunto's own P−2 to P2 scale,
which is exactly what §6.4c's member holds: the device's number, meaningful beside the
device. `0` is the P0 setting rather than an absence, and `-1` is P−1 — the two values in
the corpus — so no floor is applied. **A freedive record states one too and it is not
carried**: §6.4c's object is the model a device ran on *this* dive, a freedive ran none, and
a `deco_model` carrying only a conservatism would say otherwise.

### A start time is a naive .NET timestamp

`<StartTime>` is a naive .NET round-trip timestamp: a date, a `T`, a clock, and usually a
fraction. 380 of the 384 exports write a fraction and four do not.

**No offset, anywhere in the format.** Not on the dive, not at the root, not in a settings
block — there is none. So a converted dive carries the wall clock alone and reports the
absence (§5.2). The same dive's app-JSON export *does* carry one, and taking it from there
would be this converter asserting a zone this file does not.

**The fraction is preserved**, `converting.md`'s rule, so `2021-04-06T11:16:42.6` comes out
as it went in — and this is the format that `.6` is written by, the one a standard library's
ISO parser is most likely to reject and the reason that document says to match a date-time
by pattern.

Two leniencies and one refusal. A time of day with no seconds is read as `:00` and reported,
which is `converting.md`'s again. A date that is not a
real calendar date drops the dive. And a `<StartTime>` carrying an **offset** is refused
rather than read: this serializer has no zone to state, so a file with one is not a document
this reader knows the meaning of.

## Units

The highest-risk part of the mapping, and the only part nothing downstream can catch: a
wrong factor produces a document that validates perfectly and describes a dive nobody took.

**The vendor's two exports disagree about three readings**, and only a dive that exists in
both makes it visible. Each row below is cross-checked that way.

| reading | DM5 XML | Suunto app JSON | the same dive |
| --- | --- | --- | --- |
| CNS | whole percent | 0-1 fraction | `<CnsEnd>4</CnsEnd>` against `EndTissue.CNS: 0.04` |
| OTU | absolute count | absolute count, unrounded | `<OtuEnd>10</OtuEnd>` against `EndTissue.OTU: 9.7428…` |
| cylinder pressure | **millibar** | Pascal | `<StartPressure>205203</StartPressure>` against `20520312` Pa — both 205.2 bar |
| surface pressure | **Pascal** | Pascal | `104900` in both, i.e. 1.049 bar |
| oxygen, helium | whole percent | 0-1 fraction | `<Oxygen>21</Oxygen>` against `Oxygen: 0.21` |
| ppO₂ limit | bar | Pascal | `<PO2>1.4</PO2>` against `PO2: 140000` |
| tank size | litres | cubic metres | `<Size>12</Size>` against `TankSize: 0.012` |

**`<SurfacePressure>` is the one pressure in this file that is not millibar**, and it is the
trap worth stating twice. Read at the cylinder scale, `104900` would be 104.9 bar — a
hundred metres of seawater, at the surface. Two things settle it: every one of the 384
exports lands in 103 100 to 106 700, which is a barometric range only on the Pascal reading,
and §6.2's own 0.4 to 1.2 bar bound refuses the other one outright.

### The factor table

| source | DiveJSON member | factor |
| --- | --- | --- |
| `<MaxDepth>`, `<AvgDepth>` | `max_depth`, `avg_depth` | metres, ×1 |
| `<BottomTemperature>` | `bottom_temperature` | °C, ×1 |
| `<Duration>` | `duration` | seconds, ×1, rounded to a whole one |
| `<SurfacePressure>` | `surface_pressure` | Pascal ÷ 100 000 |
| `<CnsStart>`, `<CnsEnd>` | `cns_start`, `cns_end` | percent, ×1 |
| `<OtuStart>`, `<OtuEnd>` | `otu_start`, `otu_end` | count, ×1 |
| `<Size>` | `cylinders[].volume` | litres, ×1 |
| `<StartPressure>`, `<EndPressure>` | `cylinders[].start_pressure`, `end_pressure` | millibar ÷ 1 000 |
| `<Oxygen>`, `<Helium>` | `cylinders[].oxygen`, `helium` | percent, ×1 |
| `<PO2>` | `cylinders[].po2_limit` | bar, ×1 |
| `<Depth>` | `profile.depth.values` | metres **× 100** — centimetres |
| `<Ceiling>` | `profile.ceiling.values` | metres **× 100** — centimetres |
| `<Temperature>` | `profile.temperature.values` | °C **× 10** — tenths |
| `<Pressure>` | `profile.pressures[].values` | millibar **÷ 1 000 × 10** — tenths of a bar |
| `<Time>`, `<GasChangeTime>` | sample and event times | seconds, ×1 |

**The channel conversions carry a scale the scalar ones do not**, and the tank-pressure
channel carries two: millibar to bar, then §6.5's tenths. `211391` millibar is `211.391` bar
on a cylinder and `2114` tenths in the channel.

Nothing a source recorded is quantized (`converting.md`): `211.391` is the file's own
number, and the only rounding here is §6.5's integer channels, halves away from zero.

## Identity

**Namespace: `cefc9278-1124-5d83-8af6-7f386f03a061`**, which is
`uuid5(NAMESPACE_URL, "https://divejson.org/ns/suunto_xml")`. Frozen forever — changing it
renumbers every document any released version of this converter has produced from a DM5
export.

A DM5 export **records no id for its dive**. `<DiveNumberInSerie>` is the computer's own
counter and not an identifier; `<SerialNumber>` identifies the device rather than the dive.
Both are carried, on the recording's device (§6.4b, *Device* below) — which changes nothing
here, since neither was ever a candidate for the dive's own identity.
So a dive takes `converting.md`'s positional stand-in and the report says so: an identity
that moves if the file's order changes is a fact a diver may need.

Because this format is one dive per file, that stand-in is **prefixed by the archive
member's name** when a directory is converted as one logbook — `converting.md`, *A container
is one logbook* — which is what keeps 384 one-dive documents from collapsing onto one
identity. Converting the owner's whole export directory as a zip yields 384 dives with 384
distinct UUIDs.

The filename itself is deliberately not read. `Dive_2021-04-06-1116.xml` encodes the start
time the document already states, and a reader that took identity from it would hand two
identities to one dive depending on whether the file had been renamed.

## The member map

### Provenance — `<Source>` and `<Software>`

| source | provenance member |
| --- | --- |
| `<Source>` | `extensions.divejson.source_generator.name` — "Suunto D5" |
| `<Software>` | `extensions.divejson.source_generator.version` — the computer's firmware |

`source_generator` is the **dive computer**, not the desktop application: this document is
that application's rendering of what the computer on the diver's wrist recorded.
`extensions.divejson.converted_from` is `suunto_xml`.

An archive whose files came off two firmware versions has no single `source_generator`, so
the merged logbook records none and says so — `converting.md`'s merge rule, and it fires on
the owner's own directory, whose 384 files span three firmware versions.

### Device — `<Source>`, `<SerialNumber>`, `<Software>`, `<DiveNumberInSerie>`

One file is one dive, so a document converted from one has at most one recording (§6.4a),
and its device is where the archive-wide merge above does *not* reach: a device is per
recording, so two firmware versions across a directory are two devices' worth of recordings
rather than one absence.

| `<Dive>` child | | into (§6.4b) |
| --- | --- | --- |
| — | | `brand`, the literal `Suunto` |
| `<Source>` | | `model` |
| `<SerialNumber>` | | `serial` |
| `<Software>` | | `firmware` |
| `<DiveNumberInSerie>` | | `dive_number`, the device's counter |

The brand is supplied rather than read, which is the format's own property and not a
guess about the file — `suunto-json-mapping.md` states the reasoning in full and it is the
same here.

`<Source>` lands on `model` where the app JSON's `Device.Name` lands on `name`: this
element is the product ("Suunto D5"), the JSON's is settable by the owner, and reading each
as what it is keeps a device converted from either comparable with the other.

Every element here is one this document previously read and refused, and the refusals are
withdrawn together under *Deliberately not mapped* below: they were refused for want of a
place to put them, and §6.4b is that place.

### The dive — `<Dive>`

| source | DiveJSON member | notes |
| --- | --- | --- |
| `<StartTime>` | `started_at` | wall clock, fraction kept, no offset supplied |
| `<Duration>` | `duration` | the whole logged period |
| `<Note>` | `notes` | capped at §6's length, stripped; nil on all 384 in hand |
| `<MaxDepth>` | `max_depth` | a recorded reading, not a summary of the samples |
| `<AvgDepth>` | `avg_depth` | dropped if it is deeper than `max_depth` |
| `<BottomTemperature>` | `bottom_temperature` | recorded directly, unlike the app JSON's |
| `<CnsStart>`, `<CnsEnd>` | `cns_start`, `cns_end` | zero is a reading: §6.2 gives them `minimum: 0` |
| `<OtuStart>`, `<OtuEnd>` | `otu_start`, `otu_end` | as above |
| `<SurfacePressure>` | `surface_pressure` | Pascal; outside 0.4-1.2 bar it is dropped |
| `<DiveMixtures>` | `cylinders` | below |
| `<DiveSamples>` | the recording's `profile` | below; a profile is a member of `recordings[]` (§6.4a), never of the dive |

`<Duration>` is the whole period the computer logged, and it is the only element in this
format observed holding a dive's length. `<BottomTime>` is the time spent at depth and runs
a third shorter — 1 028 s against 2 001 on the dive this reader is measured against — so it
is not `duration`. `<DiveTime>`, which is the member the app-JSON reader prefers, is `i:nil`
on every one of the 384 exports in hand: nothing here has seen a value of it, and building a
preference order on a field nobody has seen would be a guess.

Which way a zero reads is the member's own constraint (`converting.md`), and this format
exercises both sides of it: `<MaxDepth>0</MaxDepth>` is a dive whose depth the computer never
had, while `<CnsStart>0</CnsStart>` is the oxygen clock a diver's first dive of the day
starts on.

### Cylinders — `<DiveMixtures>/<DiveMixture>`

| source | DiveJSON member |
| --- | --- |
| `<Size>` | `cylinders[].volume` |
| `<StartPressure>` | `cylinders[].start_pressure` |
| `<EndPressure>` | `cylinders[].end_pressure` |
| `<Oxygen>` | `cylinders[].oxygen` |
| `<Helium>` | `cylinders[].helium` |
| `<PO2>` | `cylinders[].po2_limit` |

In document order, at most 16 per dive. The corpus has 353 mixtures across 342 exports: 331
dives with one and 11 with two.

**A cylinder with no transmitter has both pressures written as `0`**, and the pair is this
format's absent-marker rather than a tank breathed to nothing. §6.3 settles the start
outright — writers must not emit a zero one — and the end follows it *here*, because the
corpus is unambiguous that the two arrive together: of 353 mixtures, **255 write `0` for
both and 98 write a real reading for both, and not one writes a zero on its own**. Reading
the end alone as `0.0` would put "breathed the cylinder to nothing" on every untransmitted
tank in a logbook. A zero end pressure beside a *recorded* start is left alone, which is the
reading §6.3's `minimum: 0` asks for; no DM5 export in hand writes that shape.

Non-nil `<TransmitterId>` agrees with a non-zero `<StartPressure>` on all 353 mixtures.

One recorded pair fails §6.3's own ordering: a pod reading 0.188 bar at the start against
61.875 bar at the end. The end pressure is dropped and reported, rather than either being
adjusted to fit.

### Gas switches — `<DiveGasChanges>`, inside each `<DiveMixture>`

`<DiveGasChanges>` is nested **inside** the mixture rather than being a list of its own, so
the cylinder a switch names is the element the time was found in. That makes this the one
gas-switch record among this vendor's three exports that needs no join: a marker on the
profile and the row in the cylinder list name the same cylinder by construction rather than
by agreement.

Each `<GasChangeTime>` becomes a §6.5 `gas_switch` event carrying that cylinder's
`gas_number`. **A `<GasChangeTime>0</GasChangeTime>` is kept, and it is the common case** —
342 of the corpus's 363 switches. On a single-gas dive it is the one marker saying the dive
was breathed on that gas throughout, and §6.5 gives an event time a floor of zero, so it
needs no rebasing even though most of these exports number their samples from 1. A time
before zero is dropped and reported.

**A switch with no profile to sit on is dropped and reported too**, which is a loss only
this format can have: every other source in this corpus keeps its events *in* the sample
stream, so "no samples, no events" is a tautology there. Here the times are on the mixtures
and outlive a dive whose `<DiveSamples>` is empty or whose samples were every one of them
dropped. §6.4 has nowhere to hang a marker without a profile, so the report carries a count
of what was lost — a count and no reason, because the two ways to arrive here are opposite
readings of the file and the file's own half of the story is already in the report: the
dropped-sample path reported each sample as it went, and the no-samples path had nothing to
report. A dive whose samples carry a time and no reading is the one shape that keeps its
markers: something was on the axis, so the profile is emitted for the events alone.

### Numbering — a `gas_number` is a label, and is written only where it is used

`converting.md` numbers a converted dive's cylinders from 0 in document order, and §6.3 calls
`gas_number` a label rather than an index into `cylinders[]`. So it is asserted only where
something in the profile depends on it: a pressure channel, or a gas-switch marker naming the
cylinder it switched to. A single-gas dive with no samples carries no numbering at all.

### Which cylinder the pressure channel belongs to

The sample stream carries **one `<Pressure>` per sample with no cylinder on it**, so the
channel's `gas_number` has to come from somewhere else, and `<TransmitterId>` is the only
element that knows: it is nil on exactly the cylinders that had no pod.

Three shapes, and only one of them is in the corpus.

- **Exactly one cylinder records a `<TransmitterId>`** — the channel is that cylinder's.
  This is every transmitted dive in hand: of 342 exports with mixtures, 98 name exactly one
  and 244 name none, **not one names two**, and pressure samples are present in precisely
  the 98. The pod is on the first mixture throughout that corpus, so a reader that hard-coded
  the first cylinder would agree with this one on every file and be wrong the day a
  transmitted deco bottle sat behind an untransmitted back gas — where the curve would be
  labelled with the back gas, whose own pressures are exactly the ones that would be absent
  to disagree with it.
- **None does, and readings arrive anyway** — evidence of a tank is evidence of a tank
  (`converting.md`, *Cylinders*). The readings become a cylinder of their own, carrying the
  channel and nothing else, because this file records no gas, no size and no start or end
  pressure for a cylinder it never listed. Reported as `absent`. Not in the corpus;
  `fixtures/suunto_xml/unlabelled-pressure.xml` is the pair.
- **More than one does** — the file cannot say which readings are whose: one channel, no
  key. The channel is dropped and reported rather than attached to whichever came first,
  which would put a stage bottle's pressure drop on the back gas — the shape a gas-consumption
  figure is derived from. Not in the corpus;
  `fixtures/suunto_xml/refusals.xml` is the pair.

### The profile — `<DiveSamples>/<Dive.Sample>`

| source | channel |
| --- | --- |
| `<Time>` | the axis, in seconds |
| `<Depth>` | `profile.depth` |
| `<Ceiling>` | `profile.ceiling` |
| `<Temperature>` | `profile.temperature` |
| `<Pressure>` | `profile.pressures[]` |

**Every sample element carries every channel**, nil where the sensor had nothing — a
mid-dive transmitter dropout is a nil `<Pressure>` on a sample whose `<Depth>` is
unaffected — so the channels sit on their own axes and none is padded to another's length.
That is `converting.md`'s no-padding rule; the reference dive carries 201 depths, 201
temperatures and no pressures at all.

**Two samples on one second are a real collision here**, unlike a source that appends each
sensor's stream as its own record. It fires on 37 of the corpus's exports and every one of
them is a freedive, where a 1 s sampling interval meets a `<Time>` that is not quite an
integer; no scuba dive in hand loses a reading to it. Those 37 used to be unreachable — the
reader stopped at `<Mode>3</Mode>` before it read a sample — so carrying freedives is what
first made this rule fire on a real file. `fixtures/suunto_xml/freedive.xml` is one of them,
and its expectation carries three sample seconds from five samples.

**A ceiling of zero is not a ceiling** (`converting.md`, *Profiles*). This export writes
`i:nil` rather than a zero on every no-deco sample — its 1 760 recorded ceilings run 3.0 to
15.44 m and not one is zero — so the guard is the shared rule kept rather than one this
format needs. The app's JSON export of the same dives writes `0`, and the two therefore
produce the same channel.

`<AveragedTemperature>` is deliberately not the temperature channel: it is a smoothed
reading sitting beside the raw `<Temperature>` in the same element, and smoothing is a
chart's decision rather than something to bake into a logbook.

**`profile.duration` is the span of the samples** (§6.4) and is not the dive's `<Duration>`,
though on the reference dive the two happen to coincide at 2 001 s. It carries no finding of
any kind.

### Positions

This format records none. There is no coordinate anywhere in the datacontract, so
`converting.md`'s *Where a fix belongs* has nothing to apply to and a converted dive carries
neither an entry nor an exit position. The app's JSON export of the same dives is where a
diver's positions are, on the models that record them.

## This format settles no ambiguity

Inside one DM5 document every element has exactly one unit, so there is no
fraction-or-percent and no litres-or-cubic-metres for a magnitude test to decide, and this
reader emits **no `resolved` finding**. The vendor's *two* exports disagreeing is not that
case: they are two formats, each internally consistent, and this document's *Units* table is
a fact about each rather than an ambiguity inside either.

The three readings this reader refuses for want of a stated unit are refused rather than
resolved — see *Deliberately not mapped*, `<Visibility>`, `<Weather>` and `<Weight>` — which
is `converting.md`'s refuse-rather-than-guess rule, not its ambiguity rule.

## The kinds this reader's report emits

- **`absent`** — the dive carries no id of its own; a start time with no UTC offset, or with
  no seconds; a cylinder whose start and end pressures are both the zero absent-marker; a
  cylinder the export records no gas for; a zero in a member whose schema makes zero a
  placeholder; a duration below a whole second; tank readings no cylinder claims, arriving as
  a cylinder of their own.
- **`dropped`** — a start time that is not one; text in a numeric element;
  `<Visibility>`, `<Weather>` and `<Weight>`; an average depth deeper
  than the maximum; a surface pressure or ppO₂ limit outside what §6 allows; a mix whose
  halves sum above 100 %; an end pressure above its start; a cylinder pressure past 350 bar;
  cylinders past the cap; a gas change before the dive began, or one left with no profile to
  sit on; a sample with no `<Time>`; two samples on one second; samples that carry a time and
  no reading this format can hold; tank readings two cylinders both claim.
- **`inferred`** — never. This export summarises its own dive, so there is nothing for this
  reader to compute, and `extensions.divejson.inferred` is never written.
- **`resolved`** — never, as above.

## Deliberately not mapped

Read as a list of what was considered, not of what was missed. The datacontract puts 68
elements on `<Dive>`, and this reader maps 18 of them and reads and refuses 3 more.
`<DiveNumberInSerie>` and `<SerialNumber>` are the two that moved: the first out of the
refusals and the second out of the silently unmapped, both into the device map above.

- **`<DiveNumberInSerie>`** — **no longer refused.** It is the *computer's* counter rather
  than the diver's lifetime dive number: it starts at 1 on a new or factory-reset device and
  starts again on the next one, so carrying it as §6.2's `dive_number` would stamp a dive #1
  onto somebody's three-hundredth dive. That reasoning is unchanged and is now the reason it
  has a member of its own — §6.4b's `dive_number`, defined as the device's counter — so it is
  carried under *Device* above and the finding is gone. §6.2's `dive_number` is still the
  diver's, and this reader still writes nothing into it.
- **`<Visibility>`, `<Weather>`, `<Weight>`** — read and refused, each with a finding. They
  are the desktop application's dive-conditions panel and arrive as a block: 25 of the 384
  exports carry all three, 359 carry none, and every recorded value is `0`. None can be read
  at a scale the file states. `<Weather>` is a code with no member in §6 at all.
  `<Visibility>` is the application's own rating where §6.2's is metres — the same refusal
  Subsurface's five-star `@visibility` gets. `<Weight>` reaches a §6.2 member measured in
  kilograms, but the export writes a bare number and neither it nor any companion file in
  hand states which unit the application wrote it in; every recorded value here is zero in
  either unit, so refusing costs nothing and reading would be a guess.
- **`<Marks>`** — the only other event-shaped block in the format, and the obvious candidate
  for bookmarks and stops. Its `<Type>` is an undocumented numeric code and the corpus says
  plainly that it cannot be guessed: **29 distinct values across 4 095 marks**, and the one
  that appears in **every single export** — `257`, 503 of them, about 1.3 per dive — is not
  the shape of a bookmark a diver pressed. `<Heading>` is nil on 4 068 of the 4 095. Mapping
  any of these onto §6.5's vocabulary would be a confident label over a number nobody has
  decoded. The same dives' app-JSON export spells its events out in words, so a diver who
  wants them has a file that says so.
- **`<DiveMixture>`'s `<Type>`** — the only candidate this format has for §6.3's `role`, and
  it is `1` on all 353 mixtures in hand, including both cylinders of a dive where a 21/0 back
  gas and a 52/0 deco bottle carry it alike. Whatever it encodes, it is not what the cylinder
  was carried for. `<PO2>` is the element that actually separates those two rows, 1.4 against
  1.6, and it is mapped.
- **`<DiveMixture>`'s `<Name>`** — nil on all 353, and §6.3 has no member for a cylinder's
  name either way.
- **`<DiveMixture>`'s `<TransmitterId>`** — read, and carried nowhere. It is a pod's device
  serial (`2411100050`), not a property of the cylinder; its *presence* is what labels the
  pressure channel, above.
- **`<DiveGasChange>`'s `<SetPointType>` and `<PO2>`** — a closed-circuit setpoint. §6.5's
  event has no member for one, and both are nil or `0` on every switch in hand.
- **`<DiveTime>`** — nil on all 384; nothing here has seen a value of it. Above, under *The
  dive*.
- **`<BottomTime>`** — the time spent at depth, which §6.2 has no member for. Above.
- **`<CylinderVolume>` and `<CylinderWorkPressure>`** — a dive-level cylinder size and
  working pressure, `12` and `200000` on every one of the 384 exports. The size duplicates
  the mixture's own `<Size>`, which is where §6.3 puts it; a *working* pressure is what the
  tank is rated to rather than what was in it, and §6.3 carries no such member.
- **`<Dive>`'s own `<StartPressure>` and `<EndPressure>`** — a second pressure pair at the
  dive level, non-nil on the 98 transmitted exports. §6.3 puts pressures on the cylinder and
  this file already states the cylinder's pair; these are not a copy of it — they differ from
  the mixture's by up to 4.5 bar — so carrying them would need a dive-level member the format
  does not have.
- **`<DeltaPressure>`** — nil on all 384; a pressure difference has no member either way.
- **`<MaxGf>`, `<MinGf>`, `<SetPoint>`, `<HighSwitchPoint>`, `<LowSwitchPoint>` and
  `<LowSetPoint>`** — nil on every file in hand, all five fixtures and all 384 exports. §6.4c
  has members two of them would fill, but a mapping no file exercises is a mapping nothing
  checks, so they wait for an export that states one.
- **`<AltitudeMode>`, `<AscentMode>` and `<LastDecoStopDepth>`** — these do carry values, `0`,
  `0` and `3` on every file, and they are refused for a reason of their own: §6.4c carries a
  model's family, its name, its gradient factors and its conservatism, and has no member for
  an altitude band, an ascent rule or a last-stop depth.
- **`<Algorithm>`** — an undocumented enum that reads `0` on every scuba export in hand and
  nil on every freedive, so nothing in the corpus says what any other value would mean.
  §6.4c's `algorithm` takes a family this reader can name, and a bare `0` is not one. The
  app's JSON export of the same dives states the model as a string and *is* mapped
  (`suunto-json-mapping.md`), which is where a D5's model comes from.
- **`<PersonalMode>`** — **no longer refused**; it is `deco_model.conservatism`, above.
- **`<AscentTime>`, `<DesaturationTime>`, `<SurfaceTime>`, `<DivingDaysInRow>`,
  `<PreviousMaxDepth>`, `<TimeFromReset>`** — the device's own derived figures, several of
  them about a *series* of dives rather than this one. §6.2 has no member for any.
- **`<OlfEnd>`** — Suunto's oxygen limit fraction, a percentage of whichever of the CNS and
  OTU clocks is higher. §6.2 carries the two clocks themselves, which are mapped, and a
  derived maximum of them is not a third reading.
- **`<StartTemperature>` and `<EndTemperature>`** — §6.2 carries one water temperature,
  `bottom_temperature`, and `<BottomTemperature>` is the element that states it. The full
  range is in the temperature channel, where a reader can see all of it.
- **`<TissuePressuresNitrogenStart>` / `End`, `<TissuePressuresHeliumStart>` / `End` and the
  four `*Blob` elements beside them** — a tissue model's loading state, fifteen compartments
  each. §6.2 has no member for one, and a loading figure is only meaningful beside the
  algorithm that produced it.
- **`<SampleBlob>`, `<ProfileBlob>`, `<PressureBlob>`, `<TemperatureBlob>`** — base64
  duplicates of readings the elements above already carry in text. `<SampleBlob>` is non-nil
  on all 384 and the other three are nil on all 384; decoding a private binary encoding to
  reach numbers the same file states in decimal would be a second reader with no second
  source.
- **`<SampleInterval>`** — the device's sampling period, 1 or 10 s across the corpus. §6.5's
  channels carry their own times, so the interval is already in them.
- **`<Dive.Sample>`'s `<AveragedTemperature>`** — above, under *The profile*.
- **`<Dive.Sample>`'s `<SacRate>` and `<GasTime>`** — the computer's own gas arithmetic:
  a surface-air-consumption rate and a remaining-gas time. §6.5 fixes the channels a profile
  carries and neither is one of them.
- **`<Dive.Sample>`'s `<Heading>`** — a compass bearing, nil on all 115 602 samples in hand
  and with no §6.5 channel either way.
- **`<SerialNumber>`** — **no longer refused.** It was left out on the grounds that it
  identifies a piece of hardware and nothing in a logbook needs it. That is no longer true:
  a logbook holding two records of one dive needs to tell one wrist's computer from the
  other's, and the serial is the only thing that does it reliably. It is carried under
  *Device* above; §9 covers what publishing a document with one in it means.
- **`<Boat>`, `<Master>`, `<Partner>`, `<DiveTags>`, `<Deleted>`, `<BatteryLevel>`** — a
  boat name, a dive master, a buddy, a tag list, a deletion flag and a battery reading. Nil
  or empty on all 384, so there is nothing to carry from this corpus. `<Boat>`, `<Master>`
  and `<Partner>` have no §6.2 member; a file that filled `<DiveTags>` in would be worth
  revisiting against §6.2's `notes`, and none in hand does.
- **The filename** — above, under *Identity*.

## The pairs

[`fixtures/suunto_xml/`](../fixtures/suunto_xml) holds the conformance pairs for this format
— an input, and the document a correct reader produces from it. What each one covers, and
how it was built, is one row per pair in
[`fixtures/README.md`](../fixtures/README.md#suunto_xml); the rules those expectations
follow are this document and [`converting.md`](converting.md).
