# Reading the Suunto app's JSON into DiveJSON

**Non-normative.** The specification is [`spec/divejson.md`](../spec/divejson.md); nothing
here changes what a conforming document is.

**The rules that hold for every source format are in
[`converting.md`](converting.md)** — leniency, identity, units and arithmetic, what a report
is, what a converter refuses to guess. This document carries only what is this format's.

Every claim below was checked against real exports: 35 files off two Suunto computers, 19
from a 2026 Suunto Ocean and 16 from a Suunto D5 across two firmware generations. Where a
rule rests on something none of those exercises, it says so in place.

## Why this format, and what makes it different

This is the file the Suunto app hands a diver who asks for their data, so it is what a
Suunto owner arrives with. It is also the only format in this corpus written by an
*application* about a device rather than by the device itself, which shows: the same reading
appears in different units in different generations of it, the header is a superset of every
activity type the watch records, and the newest generation moved gas out of the header
altogether and never announced it.

Three things follow, and each of them is a place where reading the file the obvious way
gives a wrong dive.

- The units are **SI throughout** — Pascal, cubic metres, Kelvin, a 0-1 gas fraction — and
  §6 holds none of them. Every scalar here is converted.
- A dive's cylinders live in one of two entirely different places depending on the export's
  age, and on the newer one they have to be **reconstructed from the sample stream**.
- A transmitter keeps reporting after the diver has surfaced, so the **last reading in the
  file is not the dive's end pressure**.

## Parsing

### The shape is `DeviceLog.Header`, and the format id is `suunto_json`

JSON carries no magic number, so a reader claims these bytes on their shape: an object whose
one top-level member is `DeviceLog`, carrying a `Header`. The name lands within the first
few bytes of the file, so a bounded head of it is enough.

**Not the `.json` extension.** A DiveJSON document is a `.json` file too, and so is
everything else anybody has ever exported; the suffix is a hint for a message to a human and
never how a source is decided.

The id is `suunto_json` rather than `suunto`, because the same vendor's DM5 desktop
application exports a different format, [`suunto-xml-mapping.md`](suunto-xml-mapping.md),
which a reader registers beside this one.

### One file, one activity, and not every activity is a dive

The app writes one file per activity in this same shape — a run, a swim and a dive differ
only in `Header.ActivityType`, which reads **51** for a dive on all 35 files in hand.
Nothing else in the file distinguishes a run from a dive whose computer recorded no depth,
so an activity that states some other type is skipped and reported — `converting.md`'s rule
for a source record that is not a dive at all, and this is the format that shows it at its
plainest, since a run and a dive here are the same shape. A **freedive** is not one of those:
§6.4a's `mode` is what says which kind of dive it is, and the DM5 XML reader carries one.
No file in hand has a freedive in this shape — every one of the 35 states `51` — so nothing
here says how the app would mark it.

A header that states **no** `ActivityType` is read on. Absence is not a claim, and
[`converting.md`](converting.md)'s first rule is that schema validity is never a
precondition.

### The three header shapes

| shape | how it is recognised | where the gas is |
| --- | --- | --- |
| D5-era, 2021 | `Header.Diving.Gases` present, no `StartPressure` in it | the gas block, mixture only |
| D5-era, 2025 | `Header.Diving.Gases` present, with pressures | the gas block, mixture and pressures |
| Suunto Ocean, 2026 | **no `Header.Diving` at all** | `Samples[].Cylinders[]` and `Samples[].DiveEvents.GasSwitch` |

A fourth arrangement exists and is not a fourth shape: a header with no `Diving` block whose
samples carry no `Cylinders` and no gas switch either. It is a dive with no gas data,
because there was none — a gauge-mode dive, or an export the app trimmed. **No real export
of it is in hand**, so `fixtures/suunto_json/header-only.json` is constructed; what it
proves is that the reader reaches the same answer by carrying nothing rather than by
failing, which is the same code path the two real shapes take when their gas is missing.

### A start time carries its offset and its fraction

`converting.md` has how a date-time is parsed — by pattern, with the calendar checked
afterwards — and this vendor's pair of exports is the reason it says so: the same dive's
DM5 XML writes the `.6` that a standard library's ISO parser is most likely to reject.

This is the format that made `converting.md` state the fraction rule: nothing here asks for
a recorded fraction to be dropped, so `2026-04-17T11:49:23.510+02:00` converts to exactly
that, offset and all.

## Units

Every one of these is a place where carrying the number as written produces a document that
validates perfectly and describes a dive nobody took.

| what | in the file | in DiveJSON | factor |
| --- | --- | --- | --- |
| tank pressure, ppO₂ limit, surface pressure | Pascal | bar | ÷ 100 000 |
| tank size | cubic metres | litres | × 1 000 |
| oxygen, helium | a 0-1 fraction | whole percent | × 100 |
| CNS | a 0-1 fraction | whole percent | × 100 |
| OTU | OTUs | the same number | — |
| depth, ceiling | metres | metres; **centimetres** on a §6.5 channel | — / × 100 |
| sample temperature | **Kelvin** | **tenths of a degree Celsius** on a §6.5 channel | (K − 273.15) × 10 |
| tank pressure on a channel | Pascal | **tenths of a bar** | ÷ 10 000 |
| `Samples[].Latitude` / `Longitude` | **radians** | decimal degrees at six places | × 180/π |
| `DiveRouteOrigin.Latitude` / `Longitude` | **degrees** | decimal degrees, exactly as recorded | — |
| `DiveTime`, `Duration` | seconds, fractional | whole seconds, halves away from zero | — |
| `NoDecTime`, `TimeToSurface` | seconds | **seconds** on a §6.5 channel | — |
| `RtGradientFactors.gf99`, `.gfSurface` | whole percent | **whole percent** on a §6.5 channel | — |

**The channel conversions carry a scale the scalar ones do not**, and the three rows that
say so are the most-executed arithmetic in this reader.

**CNS is a fraction and OTU is not**, which is invisible until the same dive is read out of
two Suunto exports: `EndTissue.CNS: 0.069` is the desktop export's `<CnsEnd>7</CnsEnd>`,
while its OTU `17.89002799987793` is that export's rounded `18`. Converting both would
report the oxygen tolerance units as 1 789 of them.

**Nothing recorded is rounded** (`converting.md`), and this format is where it costs the
most digits: a transmitter reports in steps far finer than a gauge a diver reads, so
21 162 500 Pa is `211.625` bar and not `211.62`. The one quantized value is the
radian-to-degree conversion, which is a converter's own arithmetic on an irrational factor
and is cut at six places, about 11 cm. `DiveRouteOrigin` is already degrees and is carried
untouched.

**Two coordinate units in one file is not an ambiguity.** A sample fix and a route origin
are different members, each with one unit, so there is no magnitude test and no `resolved`
finding — see below.

## Identity

`uuid5(NAMESPACE_URL, "https://divejson.org/ns/suunto_json")` =
**`1766426d-4c62-54a6-bc80-264daeab1494`**, frozen forever. Changing it would renumber every
document any released version of this converter has produced from a Suunto app export, so a
port in another language uses this value and not one of its own.

**This export records no id for its dive.** The only id-shaped members anywhere in the 35
files are `Windows[].Window.ActivityId` and `Samples[].Events[].Activity.CustomModeId`,
both of which read 51 — the activity *type*, not an identifier — and a transmitter's
`TransmitterID`; none of them names the dive. So a dive's identity is the positional stand-in `converting.md`
defines, and every conversion says so in its report. Under an archive that stand-in is
prefixed by the member's name, so an account export of one file per dive gives its dives
their order.

The app names the file after a record id of its own — the 35 files in hand are named
`69e21526bf486d396e2786b5.json` and the like — and that name is **deliberately not read**. A
converter is given bytes, a filename is not part of the document, and a rename would then
change a dive's identity.

## The member map

### Provenance — `Header.Device`

| member | | into |
| --- | --- | --- |
| `Device.Name` | | `extensions.divejson.source_generator.name` |
| `Device.Info.SW` | | `…source_generator.version` |

`source_generator` is the **device**, not the app: this file is the app's rendering of what
the computer on the diver's wrist recorded, and `Device.Name` is what that computer calls
itself. It is user-settable on an Ocean, so a name that is not a product name is the
owner's, carried as recorded. `Device` appears both inside `Header` and beside it in every
file in hand; the header's copy is preferred and the outer one is the fallback.

### Device — the same block, read as hardware

One file is one activity, so a document converted from one has at most one recording
(§6.4a) and its device is read from the block above:

| member | | into (§6.4b) |
| --- | --- | --- |
| — | | `brand`, the literal `Suunto` |
| `Device.SerialNumber` | | `serial` |
| `Device.Info.SW` | | `firmware` |
| `Device.Name` | | `name` |
| `Diving.NumberInSeries` | | `dive_number`, the device's counter |

**The brand is written without being read**, which is the one place this reader
supplies a value the file does not state. It is not §5.4's fabrication: this is a
vendor-proprietary export format, so the vendor is a property of the format rather than a
guess about the file — the same reading that already lets `source_generator` name the device
instead of the application. A format several manufacturers write gets no such line.

`Device.Name` lands on the device's `name` rather than its `model` because it is exactly
that — settable by the owner, and `Porvoo` on one real Ocean. This format states no product
name anywhere, so §6.4b's `model` has no source here; the same dive's FIT export does state
one, which is how two files of one recording come to carry different halves of one device.

A file whose header names a device and holds no samples still produces a recording — a
device-only one, carrying the device and no profile, which is a fact about the dive rather
than an empty record (§6.4a). `fixtures/suunto_json/header-only.json` is that shape.

### The dive — `Header`, and `Header.Diving` where there is one

| member | | into |
| --- | --- | --- |
| `Header.DateTime` | | `started_at`, offset and fraction preserved |
| `Header.DiveTime`, else `Header.Duration` | | `duration` |
| `Header.Depth.Max` | | `max_depth` |
| `Header.DepthAverage`, else `Header.Depth.Avg` | | `avg_depth` |
| `Diving.StartTissue.CNS` × 100 | | `cns_start` |
| `Diving.EndTissue.CNS` × 100 | | `cns_end` |
| `Diving.StartTissue.OTU` | | `otu_start` |
| `Diving.EndTissue.OTU` | | `otu_end` |
| `Diving.SurfacePressure` ÷ 100 000 | | `surface_pressure` |
| `Diving.DiveMode` | | the recording's `mode`, by the table below |
| `Diving.Algorithm` | | `deco_model.name` verbatim, and `deco_model.algorithm` by the table below |
| `Diving.Conservatism` | | `deco_model.conservatism` |

**The mode and the model are the recording's, not the dive's** (§6.4a), and only the D5
header shape states them: the Ocean shape has no `Header.Diving` at all, so an Ocean file
yields the channels below and no `deco_model`, which is correct rather than a gap.

| `Diving.DiveMode` | `mode` | seen on |
| --- | --- | --- |
| `Air` | `open_circuit` | 8 D5 exports |
| `Nitrox` | `open_circuit` | 8 D5 exports |
| `Mixed` | `open_circuit` | `fixtures/suunto_json/suunto-d5.json` |

All three are gas modes of an open-circuit computer; the D5 has no rebreather mode and its
free and gauge modes write no `Header.Diving` this reader has ever seen. A value outside the
table leaves `mode` absent and is reported — §6.4a is explicit that a reader must not assume
open circuit, so guessing at an unseen string would be the one thing the member forbids.

| `Diving.Algorithm` | `algorithm` |
| --- | --- |
| `Suunto Fused2 RGBM` | `rgbm` |
| `Suunto Fused RGBM 2` | `rgbm` |

Both spellings are real — the first is what 16 exports in hand carry and the second is
`fixtures/suunto_json/suunto-d5.json` — and `name` carries whichever the file used, verbatim,
because §6.4c makes it the device's own name for its model. A string outside the table still
fills `name` and leaves `algorithm` absent: a family is a claim about the mathematics and
this reader will not derive one from a product string it has not seen.

`Conservatism` is `≥ 0`-free on purpose: §6.4c puts no floor on it, so a `0` is the P0 setting
and a negative is Suunto's P−1 or P−2 rather than an absent-marker. That is the one member
this reader carries where a negative is a reading.

**`DiveTime` before `Duration`, and the order is the mapping.** They are different
quantities: `DiveTime` is the time in the water and `Duration` is the whole period the
computer logged, which starts before the diver gets in and ends after they are back on the
boat. On one file in hand they are 4 001 s and 4 302 s. §6.2's `duration` is the dive's, so
the in-water figure wins where the export states it — which it does on the Ocean shape and
not on the D5 shapes, where only `Duration` exists.

`DepthAverage` is the Ocean's spelling of the average depth and `Depth.Avg` the D5's; no
file in hand states both. Neither is derived: this export summarises its own dive, so both
depths are readings.

A zero reads by the member's own constraint. `duration`, `max_depth` and `avg_depth` are
`> 0` in the schema, so a zero in any of them is a placeholder and is read as not recorded —
and for `duration` the constraint is asked of the **whole seconds**, so a `DiveTime` of 0.4 s
is read as not recorded too rather than written as a dive of no length;
`cns_*` and `otu_*` are `≥ 0`, so the zero a first dive of the day starts on is an answer.
An average depth deeper than the maximum cannot be, and the average is dropped.

### Cylinders, where the header has a gas block — `Diving.Gases[]`

| member | | into |
| --- | --- | --- |
| `Oxygen` × 100 | | `oxygen` |
| `Helium` × 100 | | `helium` |
| `TankSize` × 1 000 | | `volume` |
| `StartPressure` ÷ 100 000 | | `start_pressure` |
| `EndPressure` ÷ 100 000 | | `end_pressure` |
| `PO2` ÷ 100 000 | | `po2_limit` |
| `State` | | `role`, through the table below |

**The block is authoritative where it exists** and the sample telemetry adds nothing to it.
The block carries a gas fraction, a tank size and a ppO₂ limit that telemetry alone cannot,
and its pressures are the device's own figures — on the 2025 exports they agree with the
first and last telemetry reading to within a kilopascal, which is what says the two describe
the same cylinder.

`State` maps through a table with one row: `Primary` → `bottom`. That vocabulary is Suunto's
and the table is honest about being nearly empty — "Primary" is the only value the 18 gases
across these exports carry, and a value it does not name comes out as **no role** rather
than as one this converter invented.

### Cylinders, where it does not — `Samples[].DiveEvents.GasSwitch` and `Samples[].Cylinders[]`

**A cylinder is listed because the diver switched to it, not because it transmitted.** That
distinction is the whole safety of the mapping. `GasSwitch.GasNumber` is the only record
this shape keeps of *which* cylinders were on the dive; building the list from telemetry
instead emits one cylinder for a two-tank dive, and one cylinder carrying both pressures is
exactly the shape a gas-consumption figure is derived from — so a stage bottle's pressure
drop would be attributed to the whole dive. Four of the nineteen Ocean files switch to a
second gas while only the first slot ever sends a pressure, so on those the second cylinder
carries no pressures at all, which is the honest answer.

Switch order is chronological, so the back gas comes first and a deco gas follows — the
order a logbook lists them in. A slot that transmitted without a recorded switch is appended
after those, by `converting.md`'s evidence-of-a-tank rule — whichever way round it arrived.
At most 16 cylinders are read from one dive, since nothing else bounds how many distinct gas
numbers a file may claim.

**Only the pressures are real, and nothing else is invented to fill the gap.** This shape
records no gas fraction, no tank size and no ppO₂ limit anywhere — the string `Oxygen` does
not appear in a single one of the nineteen Ocean files — so every cylinder here carries an
`absent` finding saying so. Reporting air would be indistinguishable from having read it.

A transmitter's readings are range-checked exactly as a gas block's are: they are the same
member with the same 0-350 bar bounds, and a pod reporting outside them is a noisy reading
dropped with a finding rather than a reason to lose the whole conversion.

**A `null` pressure is skipped rather than ending the series.** An Ocean numbers five
cylinder slots on every sample and writes `null` into the four nothing is paired to, and its
final samples null out even the live one; a reader that stopped at the first `null` would
report whatever the tank had reached by then.

### The `DiveTime` bound, which changes an answer on every file

**A reading from after the dive ended is dropped from the cylinder's first and last.** The
transmitter keeps reporting while the computer is still logging on the surface, so the last
reading in the file is whatever the tank read once the diver purged the regulator to break
down their kit. The bound is `Header.DateTime` + `Header.DiveTime`.

It moves the end pressure on **all nineteen** Ocean files in hand. On most of them it is
worth under two bar — the surface breathing before derigging — and on two of them it is the
difference between a real end pressure and **0.14 bar**: a diver who breathed their cylinder
dry, and a respiratory minute volume to match.

| dive | unbounded | bounded |
| --- | --- | --- |
| `fixtures/suunto_json/purged-regulator.json`'s original | 0.14062 bar | **53.34375 bar** |
| a second file with the same habit | 0.14062 bar | **76.125 bar** |
| `fixtures/suunto_json/suunto-ocean.json`'s original | 127.26562 bar | **127.15625 bar** |

A header with no `DiveTime` leaves the readings unbounded, which is weaker and never worse
than not knowing. It deliberately does **not** fall back to `Duration`: bounding a window by
its own full length is not a bound.

**The profile's pressure channel is deliberately not bounded.** It is telemetry the device
really recorded, and truncating it would drop surface readings the depth and temperature
channels keep. So a converted dive's last channel value and its cylinder's `end_pressure`
disagree, on purpose, and every fixture with a `DiveTime` and a pressure channel encodes that.

**The extremes are taken over the samples' own recorded instants, not off the profile.**
The merged axis is not what loses them — it folds an entry into a second another channel's
entry already holds rather than dropping it, which is `converting.md`'s collision rule
read per channel, and *The profile* below is where this exporter's habit of appending its
sensor streams separately makes that rule visible. Two other readings do,
and both are measured on the dive `suunto-ocean.json` is reduced from, whose start pressure
is 211.625 bar: an **unmerged** axis, one entry per second with the first winning it whole,
gives 211.26562, the earlier depth entry taking the second and carrying the cylinder reading
0.1 s later away with it; and the axis's pressure **channel**, which §6.5 stores in tenths
of a bar, gives 211.6, which is a rounding of a value the source recorded and so is
`converting.md`'s rule the other way round.

### Numbering — a source gas number is a label, not a position

§6.3 calls `gas_number` a label rather than an index into `cylinders[]`, and
`converting.md` numbers a converted dive's cylinders from 0 in document order. A source
number is therefore *resolved* to a cylinder's position rather than passed through, and a
numbering is asserted at all only where the profile needs one — a pressure channel, or a gas
switch naming the cylinder it switched to.

The two shapes number differently, and both state it themselves:

- A `Gases[]` block carries no number of its own, and the same dive's
  `Samples[].Cylinders[].GasNumber` reports **1** for its first entry — so a D5's numbering
  is **one-based** over the block's order. The 2025 exports confirm it to within a
  kilopascal: `Gases[0].StartPressure` 20 520 312 Pa against a first telemetry reading of
  20 520 000 Pa on slot 1.
- An Ocean numbers **from 0** and states the number on every reading and every switch.

A switch to a gas the file describes nowhere is still emitted, with no `gas_number`: it is a
switch that happened, and saying so is honest where guessing a position would not be. One
2025 file does this, switching to gas 2 with one entry in its `Gases[]` block.

### The profile — `Samples[]`

| member | | into |
| --- | --- | --- |
| `TimeISO8601` | | the sample's second, elapsed from `Header.DateTime` |
| `Depth` | | the `depth` channel, centimetres |
| `Ceiling` | | the `ceiling` channel, centimetres, **where it is above zero** |
| `Temperature` | | the `temperature` channel, tenths of a degree Celsius |
| `Cylinders[].Pressure` | | a `pressures` channel, tenths of a bar, per cylinder |
| `NoDecTime` | | the `ndl` channel, seconds |
| `TimeToSurface` | | the `tts` channel, seconds, **where it is above zero** |
| `RtGradientFactors.gf99` | | the `gradient_factor` channel, whole percent |
| `RtGradientFactors.gfSurface`, else `.gtSurface` | | the `surface_gradient_factor` channel, whole percent |
| `DiveEvents` / `Events` | | §6.5 events, below |
| `Latitude` / `Longitude`, `DiveRouteOrigin` | | `entry_position` and `exit_position` |

**This exporter is what made `converting.md`'s collision rule per channel.** It appends its
sensor streams as separate entries: on the dive `suunto-ocean.json` is reduced from, 7 477
entries carry a depth, a temperature, a satellite fix or a battery reading, almost never two
of those at once, and they collide on the whole seconds §6.5 requires. Offering them to the
axis one at a time leaves it choosing between a depth and a temperature recorded at the same
instant, and keeps 345 of that dive's 431 depths; merging them keeps all 431 — the count the
same dive's FIT reading gives.

**Samples are ordered by their own recorded time.** The union of an Ocean export's sample
timestamps is not monotonic: adjacent entries go backwards by up to a second — 1.05 s is
the worst step across these 35 files — because the separate sensor streams are appended out
of order. The last entry in the file is not the
last reading of the dive.

**`gtSurface` is `gfSurface`, one firmware earlier.** The Ocean's 2.40.56 export writes
`RtGradientFactors: {gf99, gtSurface}` on all 7 194 samples of 19 files in hand; its 2.51.28
export writes `{gf99, gfLeadingTissue, gfSurface}`. The `gt` is a vendor typo fixed in a
firmware update, and both files are real, so both spellings read into
`surface_gradient_factor`. `gfLeadingTissue` is the compartment's *number* rather than a
loading and stays unmapped.

**Three zeros and two negatives, each decided against the file** — `converting.md` says the
decision is per quantity, per format, and this format needs all five:

- **`NoDecTime: 0` is a reading.** It is what a computer shows the moment a dive stops being
  a no-decompression dive, and the files prove it: a D5 export in hand writes it on five
  consecutive samples at 42.6 to 44.5 m, with a time to surface of 256 to 268 s beside it
  and a ceiling appearing a few samples later. Reading it as an absence would delete the one
  reading a decompression dive most needs.
- **`NoDecTime: -1` is the absent-marker**, on 1 031 samples across the 19 Ocean exports in
  hand, 916 of them with a ceiling above zero — the device showing a stop depth in place of
  a no-decompression clock it no longer has. A negative no-decompression time is not a
  quantity, which is why §6.4 floors the channel at zero; `converting.md`'s negative rule
  drops the sample and reports it.
- **`NoDecTime: 6000` is a reading at the display cap**, the Ocean's 100 minutes, and
  `converting.md`'s cap rule carries it through.
- **`TimeToSurface: 0` is the absent-marker**, and this one only the file could settle. The
  Ocean writes it on 199 of the 364 samples of one dive that carry the member, at every
  depth from 0 to 19 m — including two rows from a sample at 14.63 m that says `88`. A time
  to surface that is zero at 14 m and 88 s at 14 m seconds later is not a time; it is the
  space the device writes when it has no figure. The D5 shapes write exactly one per file,
  always on the first sample, where a real ascent from 1.24 m would take the 8 to 12 s the
  next sample states. So a zero is dropped and reported on both shapes.
- **`gf99: -100` is the absent-marker** and `gf99: 0` is a reading: the export writes `-100`
  on 5 531 of 7 194 samples, which is where no compartment leads, and `0` on 585, which is a
  leading tissue at ambient. `gfSurface` is never negative in any file in hand.

**`gf99` runs into four figures on a decompression ascent, and this reader does not explain
it.** On the no-decompression dives in hand the two members behave as a GF99 and a surface
GF must: a tissue is always further from its M-value at the surface than at depth, so `gf99`
is the smaller of the pair on **all 803** samples of those dives that state both, with no
exception. On the decompression dives it stops holding. Over one contiguous stretch of a
5 to 8 m stop `gfSurface` falls from 116 to 90 without once rising, exactly as a surfacing
figure off-gassing should, while `gf99` beside it ranges from 25 to 732 and jumps from 193 to
732 between two adjacent samples 27 cm apart; earlier in the same ascent it reaches 12 575,
and 114 of that dive's samples are above 100.

Two things follow, and the second is the rule. **The member is still `gradient_factor`**: the
field is named `gf99`, it is whole-numbered, it is never negative except for the `-100`
sentinel, it agrees in magnitude with Shearwater's `<gradientfactor>` on comparable dives,
and it meets `gfSurface` where a GF99 and a surface GF must meet. **And the number is carried
as written.** What the large values mean is not something this document can say — Suunto
publishes no definition of the field, and nothing in the file accounts for the size — so the
converter writes the reading and explains nothing, which is §5.4 rather than a gap: deciding
what the device should have written is the one thing a converter may not do, and a cap is
that decision wearing a plausible number. §6.4 puts no ceiling on the channel for the same
reason. `ocean-deco-ppo2.json` keeps two of those samples, `398` at 7.62 m and `192` at
5.70 m, so a reader that clamps fails a pair rather than passing quietly.

**A zero ceiling is `converting.md`'s rule, and this is the export that showed it.** It
writes `"Ceiling": 0` on every no-deco sample where the same vendor's desktop export writes
`xsi:nil` — 10 992 of the 12 643 ceiling readings across these 35 files, every one of which
would have drawn a flat line along the surface.

**`DeviceInternalAbsPressure` is not a tank pressure.** It sits in the same sample object as
`Cylinders` and reads about 96 400 Pa at the surface: it is the computer's own ambient
pressure sensor. Labelling it tank pressure on a chart divers plan gas from would be
actively wrong, and it is close enough in shape to be picked up by mistake.

### Events — `Samples[].DiveEvents` and `Samples[].Events`

**Both member names, because the generations disagree.** The D5 shapes write `Events` and
the 2026 Ocean writes `DiveEvents`, and the Ocean uses `Events` at the same time for
activity bookkeeping — `Lap`, `Pause`, `ArrayBegin` — that has nothing to do with a dive.
Reading only one of them loses every gas switch in one generation or the other.

| in the file | | into |
| --- | --- | --- |
| `GasSwitch.GasNumber` | | `gas_switch`, with the cylinder's position |
| `Notify` `Deep Stop`, `Active: true` | | `deep_stop` |
| `Notify` `Safety Stop`, `Active: true` | | `safety_stop` |
| `Alarm` / `Warning`, `Active: true` | | the §6.6 type its `Type` names, **and** that `Type` as the `label` |
| `Notify` `Deco`, `Active: true` | | `ndl_reached` |
| `Notify` `Safety Stop Broken`, `Active: true` | | `safety_stop_violation` |

**An alert carries a type and the device's wording, and it is the wording that earns the
type.** §6.6's vocabulary was seeded from this list, one value per distinct meaning, so each
alert this format names maps onto a value rather than arriving unclassified — and the label
travels beside it because "Ceiling Broken" is what the diver was shown and no type can say
it as well. The whole table, matched case-insensitively:

| `Type` | §6.6 `type` | exercised by |
| --- | --- | --- |
| `Ascent Speed` | `ascent_rate` | `suunto-d5.json`, `d5-stop-alarms.json`, and two more |
| `Mandatory Safety Stop` | `safety_stop_mandatory` | `d5-stop-alarms.json`, and two more |
| `Safety Stop Broken` | `safety_stop_violation` | `d5-stop-alarms.json`, `d5-deep-stop-broken.json` |
| `Mandatory Safety Stop Broken` | `safety_stop_violation` | `d5-stop-alarms.json` |
| `Deep Stop Broken` | `deep_stop_violation` | `d5-deep-stop-broken.json` |
| `Violated Deep Stop` | `deep_stop_violation` | `d5-deep-stop-broken.json` |
| `Ceiling Broken` | `ceiling_violation` | `suunto-ocean.json`, `d5-deco-max-depth.json` |
| `NoDecoTime` | `ndl_reached` | `ocean-deco-ppo2.json` |
| `PO2 High` | `ppo2_high` | `ocean-deco-ppo2.json` |
| `Tank Pressure` | `pressure_low` | `ocean-tank-pressure.json` |
| `Max.Depth` | `depth_alarm` | `d5-deco-max-depth.json` |

Every row has a pair behind it, which is the bar this corpus holds a mapping to; five of the
pairs exist for no other reason. **A `Type` outside the table is an event with no `type` and
that wording as its `label`**, which §6.6 makes the spelling of an unclassified event — the
vocabulary grows in a minor version when a file names something it has no value for, and
nothing has to be forced into the nearest one in the meantime. Across the 35 dives in hand
these are 79 markers once the inactive edges are dropped, rare enough to render.

**Only the `Active: true` edge is emitted.** These arrive in pairs — a `Deep Stop` true at
1 424 s and false at 1 454 s is one 30-second stop — and a marker has no way to show which
half of a pair it is, so the marker is the start and the other half would only double it. A
`GasSwitch` has no `Active` and is not a pair.

**Four `Notify` values are carried and the other eight are not.** `Deep Stop` and
`Safety Stop` are the stops themselves; `Deco` is the moment the dive became a
decompression dive, which is `ndl_reached`, and `Safety Stop Broken` is
`safety_stop_violation`. Neither of the last two carries a `label`: a `Notify`'s `Type` is
the device's own name for its *state*, and writing it as the wording of an occurrence would
put "Deco" on a marker the diver never read.

The eight that stay dropped each have a reason of its own. "The format has no value for
this" is not among them any more: §6.6 makes an event with a label and no type conforming,
so nothing is dropped for want of somewhere to put it.

- `Deep Stop Ahead`, `Safety Stop Ahead` and `Stop done` are the prompt before and the
  confirmation after a stop this reader already marks; carrying all three would put three
  ticks on one stop.
- `Gas Switch` duplicates the `GasSwitch` event in the same sample.
- `Deco Window`, `NoFly Time`, `Dive Time` and `Gas Available` are the computer narrating
  its own state rather than something that happened on the dive — the reason everything
  under `State` is dropped, applied to the four `Notify` values that are the same thing.

*Rejected:* carrying all eight as unclassified labelled events. It is a marker cloud on
every dive — `Deco Window` alone fires seven times on each of three files in hand — for
narration that is not an occurrence.

### Positions

Which surface interval a fix belongs to is `converting.md`'s question, and the deepest
sample is its split. What is this format's is below.

**This export writes its coordinates in two units, in one file.** A sample's own
`Latitude`/`Longitude` are radians; the `DiveRouteOrigin` on the first sample is degrees.
The origin matters because **every satellite fix in the sample stream lands after the diver
surfaced** — a receiver has nothing to talk to through seawater — so on the strength of
those alone this shape yields an exit and no entry, while the app draws both pins from the
same export. The origin is fed in as an ordinary fix rather than assigned to the entry
directly, so a file that does log a pre-descent fix gets the rule the rest of the mapping
promises; none in hand does.

`DiveRouteQuality` is deliberately not read as a validity signal: it reads 1 on good origins
and on `0, 0` ones alike across the corpus, so treating it as one would drop real positions
and keep junk. The `0.000000` pair is already rejected by the rule in
[`converting.md`](converting.md), which is where two of these files' origins go.

Every Ocean dive in `fixtures/suunto_json/` that a FIT input here also carries comes back
with the exit position that file gives it — `suunto-ocean.divejson` against
`fixtures/fit/suunto-ocean.divejson`, `suunto-ocean-2026.divejson` against
`fixtures/fit/suunto-ocean-2026.divejson` — two files, two readers, two coordinate
encodings, one answer at six decimal places on each dive.

## This format settles no ambiguity

Every member this export states carries one unit, so there is no scale for a reader to
decide and this reader emits **no `resolved` finding**. That is what separates it from UDDF,
whose `<o2>` and `<tankvolume>` are numbers with no stated unit.

The two coordinate units are not this case: they are two different members each with one
unit, and neither is in doubt.

## The kinds this reader's report emits

- **`absent`** — the dive carries no id of its own; the export records no gas mixture for a
  cylinder; a start time with no UTC offset; a zero in a member whose schema makes zero a
  placeholder; a member the header does not state.
- **`dropped`** — an activity that is not a dive; a start time that is not one; a sample
  with no `TimeISO8601`; a sample before the dive began; one channel twice on a second; an
  end pressure above its start; a pressure, ppO₂ limit or surface pressure outside what §6
  allows; a mix whose halves sum above 100 %; cylinders past the cap; samples that carry a
  time and no reading this format can hold.
- **`inferred`** — never. This export summarises its own dive, so there is nothing for this
  reader to compute, and `extensions.divejson.inferred` is never written.
- **`resolved`** — never, as above.

## Deliberately not mapped

Read as a list of what was considered, not of what was missed.

- **`Header.Temperature.Max` / `Min`** — whole-activity summaries, and on **all nineteen**
  Ocean files the "Max" is *lower* than the "Min", which is not a reading of anything a dive
  log has a member for. §6.2's `bottom_temperature` would have to come from the sample
  channel instead, which would make it this converter's arithmetic and therefore `inferred`;
  the temperature channel is already in the profile, where a reader can see all of it rather
  than one summary of it. The same reasoning the FIT reader applies to
  `session.avg_temperature`.
- **`Header.Notes`** — the app writes `""` on every Ocean file and omits it on every D5 one,
  so there is nothing to carry; an empty value is absence, not a note the diver wrote. A
  file that does carry text here would map onto §6.2's `notes`, and none in hand does.
- **`Header.Altitude` and `Diving.Altitude`** — the first is a `{Max, Min}` pair of readings
  taken during the activity and the second reads `0` on all 16 files that have it, being the
  computer's altitude *adjustment mode* rather than a place. §6.2's `altitude` is the
  altitude of the site, which neither is.
- **`Header.DiveTimeMax`, `Ascent`, `AscentTime`, `Descent`, `DescentTime`,
  `MaxDepthAverage`, `Distance`, `VerticalSpeed`, `PauseDuration`, `SampleInterval`,
  `Ventilation`** — a device's own derived figures, none of which §6.2 has a member for.
  `MaxDepthAverage` equals `Depth.Max` on all 19 files that state it — the D5 shapes state
  none of these — and is not documented as a maximum depth.
- **`Header.Feeling`, `IsSupervised`, `DiveInWorkout`, `DeviceLocation`, `MoveType`,
  `Activity`, `Personal`, `Settings`, `Targets`** — the diver's rating of the dive and the
  watch's configuration. §6.2 has nowhere for a 1-5 feeling, and inventing an `extensions`
  member for it would be this reader defining vocabulary.
- **Every fitness member — `EPOC`, `Energy`, `MAXVO2`, `FitnessAge`,
  `FitnessAgeClassification`, `HrZones`, `PowerZones`, `SpeedZones`, `PeakTrainingEffect`,
  `RecoveryTime`, `TraingingLoadPeak` (the vendor's spelling), `StepCount`,
  `StepCountSupervised`, `PoolLength`, `PoolLengths`, and the eight `Downhill*` members** —
  an activity tracker's, in a header shared with every sport the watch records.
- **`Diving.AlgorithmAscentTime`, `AlgorithmBottomMixture`, `AlgorithmBottomTime`,
  `AscentMode`, `DeepStopEnabled`, `LastDecoStopDepth`, `MiniLock`, `SafetyStopTime`** —
  §6.4c carries a model's family, its name, its gradient factors and its conservatism, and
  has no member for an ascent rule, a last-stop depth, a deep-stop switch or a stop length.
  `Algorithm`, `Conservatism` and `DiveMode` left this list when it did; they are carried
  under *The dive* above.
- **`Diving.DaysInSeries`, `DesaturationTime`, `NoFlyTime`, `PreviousDiveDepth`,
  `SurfaceTime`** — properties of a *series* of dives rather than of this one.
  `NumberInSeries` was refused alongside them until §6.4b gave a device's counter a home;
  it is carried now, under *Device* above. §6.2's `dive_number` is still the diver's own
  numbering and a device's counter is still not reliably it — which is why the two are
  different members rather than one. **Untested**: no file in hand carries a
  `Header.Diving.NumberInSeries`. Only the D5 shapes have a `Header.Diving` at all — the
  Ocean shape has none, per *The three header shapes* above — and the one D5 file in
  `fixtures/suunto_json/` states no number inside it.
- **`Diving.StartTissue` / `EndTissue`'s `Helium`, `Nitrogen`, `OLF`, `RgbmHelium` and
  `RgbmNitrogen`** — everything under those blocks but `CNS` and `OTU`. §6.2 has no member
  for a tissue model's state, and a loading figure is only meaningful beside the algorithm
  that produced it.
- **`Gases[].TankFillPressure`** — the pressure the cylinder was filled to, which §6.3 does
  not carry; `start_pressure` is the pressure at the start of the dive and the two differ.
- **`Gases[].TransmitterID`, `TransmitterStartBatteryCharge`, `TransmitterEndBatteryCharge`**
  — a pod's serial and its battery. Neither is a property of the cylinder.
- **`Device.Info.HW` / `BSL` / `BatteryAtStart` / `BatteryAtEnd` /
  `BatteryDesignCapacity` / `BatteryFullCapacity`** — a hardware revision, a bootloader
  version and battery telemetry, none of which §6.4b models. `Device.SerialNumber` was
  refused here too, on the grounds that it identifies a piece of hardware and nothing in a
  logbook needs it. That is no longer true and the sentence is withdrawn: a logbook holding
  two records of one dive needs to tell one wrist's computer from the other's, and the
  serial is the only thing that does it reliably. It is carried under *Device* above, and
  §9 covers what publishing a document with one in it means.
- **`Samples[].AbsPressure`, `SeaLevelPressure`, `SurfacePressure`, `MinSurfacePressure`,
  `MaxSurfacePressure`, `DeviceInternalAbsPressure`, `DeviceInternalTemperature`,
  `Altitude`, `VerticalSpeed`, `Speed`, `Distance`, `Cadence`, `Power`,
  `AmbientIlluminance`, `BrightnessDisplayIntensity`, `GasTime`, `Ventilation`** — §6.5
  fixes the channels a profile carries and none of these is one of them. `NoDecTime`,
  `TimeToSurface` and `RtGradientFactors.gf99`/`.gfSurface` were on this list until §6.4
  gained channels for them; they are carried under *The profile* above.
  `RtGradientFactors.gfLeadingTissue` stays here: it is which compartment is leading, not
  how loaded it is, and no member holds a compartment number.
- **`Samples[].BatteryCharge`, `BatteryCurrent`, `BatteryVoltage`** — the watch's battery,
  logged every few seconds.
- **`Samples[].DiveRoute`, `DiveRouteDistance`, `EHPE`, `EVPE`, `NumberOfSatellites`,
  `Satellite5BestSNR`, `GPSAltitude`, `QualityFeatures`, `UTC`** — the rest of the GPS
  track and its quality metrics. §6 carries an entry and an exit position, not a track.
- **`Samples[].Events` / `DiveEvents` under `State`, `DiveState`, `DiveStatus`, `Lap`,
  `Pause`, `ArrayBegin`, `Activity`** — the computer narrating its own mode: "Below
  Surface", "Wet Outside", "Dive Active", "Tank pressure available". Five of them land on
  t = 0 of every file in the corpus, and none is an event on a dive.
- **`DeviceLog.Windows[]`** — per-window activity summaries the app uses for its own
  charts; empty on all 16 D5 files and a repeat of the header's figures on the 19 Ocean
  ones.
- **The filename** — above, under *Identity*.

## The pairs

[`fixtures/suunto_json/`](../fixtures/suunto_json) holds the conformance pairs for this format — an
input, and the document a correct reader produces from it. What each one covers, and how it
was built, is one row per pair in [`fixtures/README.md`](../fixtures/README.md#suunto_json); the
rules those expectations follow are this document and [`converting.md`](converting.md).
