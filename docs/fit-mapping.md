# Reading ANT/Garmin FIT into DiveJSON

**Non-normative.** The specification is [`spec/divejson.md`](../spec/divejson.md); nothing
here changes what a conforming document is.

**Several of the messages below have never been read from a device that wrote them.**
`dive_summary`, `tank_summary`, `tank_update` and every `dive_gas` field except
`oxygen_content`, `helium_content`, `status` and `message_index` are Garmin's, and no
Garmin file exists in this project: every FIT file this reader was built against is a
Suunto one, and Suunto's exporter writes none of those four messages. They are unit-tested
over messages built with an encoder driven by the same profile the reader decodes through,
which proves the mapping and does not prove the device. Their rows are marked **untested**
below, and they stay marked until a real Descent export has been converted.

**The rules that hold for every source format are in
[`converting.md`](converting.md)** — leniency, identity, units and arithmetic, what a
report is, what a converter refuses to guess. This document carries only what is FIT's.

## Why this format, and what makes it different

FIT is the one binary format a dive computer is likely to hand a diver directly, and it is
the one format in this package that is genuinely *shared*: a Garmin Descent and a Suunto
Ocean write the same message numbers with the same units, because the units come from the
global FIT profile rather than from the vendor. Every other source this package reads
invents its own spelling and has to be read one writer at a time.

That cuts both ways. There is almost no unit table here, and there is no scale for a reader
to settle — so this reader raises no `resolved` finding, and never will. What it has
instead is one very sharp trap, below, and a file whose summary is written *after* the
samples it summarises, so nothing can be read cheaply from a header.

## Parsing

### The magic is `.FIT` at offset 8, and the format id is `fit`

Not at offset 0, which every other format in this package is claimed at, and not the `.fit`
extension, which a rename can fake. The four ASCII bytes sit immediately after the header
preamble.

**That is why an archive's own head can never decide the format of the files inside it.**
A bounded head of a zip reaches the container's local file header and no further, so the
member's magic is out of reach; the registry's archive walk sniffs each member's own head
instead. An application deciding "these bytes are a zip" before it asks anything else is
doing the only thing that works.

### A developer field may shadow a native one, and the native one wins

**This is the whole of what a FIT reader has to get right for these files.** Suunto's
exporter declares developer fields whose names collide with profile fields, so every Suunto
`session` in this project's hand carries **two** `max_depth` values:

| | value | how it is stored |
| --- | --- | --- |
| native, field 141 | `45.91` | a `uint32` of 45910, scaled by 1000 — exact |
| developer | `45.90999984741211` | a `float32` the exporter wrote beside it |

Walking `frame.fields` into a dictionary keyed by name keeps whichever came last, and the
developer duplicate is written last. The result validates perfectly and is wrong by a
rounding error, on every Suunto file there is.

So a field is taken by name **and by not being a developer field**. The half that matters
most is the one a positional match gets wrong anyway: a message carrying **only** the
developer duplicate reads as *not recorded* and falls through to the next source, rather
than passing a vendor's float off as the profile's scaled integer. What the reader then
produces is an `inferred` value and a report line saying where it came from, which is a
true statement; carrying the float would have been a false one.

Developer fields under names the profile has no field for — `dive_number_in_series`,
`surface_time`, `peak_epoc`, `feeling`, `dive_mode`, all of which real files carry — are
not read at all. A developer field is the vendor's own extension and this reader maps the
profile.

### The file is decoded once, and the decode is bounded

A FIT file is a stream whose `session` comes after the samples, so there is no header to
read: the file is decoded in one pass and the messages a dive is built from are kept.

At most **100,000 frames** are decoded, after which the file is refused as an activity log
rather than a dive log. Decoding is linear in frames and is the whole cost of reading a FIT,
and a long run of bare `record`s behind one definition — about ten bytes each, which is how
a device really encodes a long log — is where an unbounded read hurts. The fullest file in
this project's hand decodes to 4,339 frames for a 72-minute dive, so the cap is about 23
times that, or roughly 28 hours of continuous logging.

It **refuses rather than truncating**, because the summary comes last: stopping early and
keeping what had arrived would discard the start time, the duration and the depths, and
convert a confidently empty dive.

**The protocol version is recorded and not gated on.** It goes into
`extensions.divejson.fit_protocol_version` — 2.0 on every file in this project's hand, which
is the closest thing FIT has to `.ssrf`'s `@version` — and nothing branches on it. What a
reader has to agree with a writer about is the message and field numbers, and those come
from the profile.

Unlike the archive's `max_members` and `max_member_size`, this cap is the adapter's rather
than the caller's. Those bound an upload; this bounds the work one already-bounded file can
ask for.

### A corrupt file is refused as a sentence

The checksum is *not* enforced — it guards against transfer corruption rather than
tampering, nothing downstream trusts it, and refusing an otherwise readable logbook over it
would lose real dives for no gain.

What is caught is everything else. This is the one place in the package where a third-party
decoder walks bytes a stranger supplied, and a corrupt file does not reliably present as
the decoder's own error type: flipped bytes past the header raise `AssertionError` from the
reader, `ValueError` from a bad field definition and `TypeError` from the processors. Every
one of them reaches the caller as this package's own error, because the message reaches an
import screen.

### One file, one dive

A file with no `session` describes no dive and is refused. A file whose `session` names a
sport other than `diving` is refused too — unless it carried depth samples, which is the
stronger evidence and costs nothing to check. `sport = diving` covers every dive sub-sport:
single and multi-gas, gauge, apnea.

A second `session` is a second dive, and is reported and not converted. The sample stream is
cut at the first one, positionally rather than by timestamp, because a `dive_gas` carries no
timestamp to filter on. `dive_summary` and `tank_summary` sit above that cut, both being
written after the session they describe, and are bounded at the second session instead.

## Units

The profile states every one, so there is nothing here for a magnitude test to settle. The
scale factors below are the profile's own, applied by the decoder before this reader sees a
value.

| what | in the file | in DiveJSON |
| --- | --- | --- |
| depth, ceiling | `uint32` metres scaled by 1000 | metres; centimetres on a §6.5 channel |
| sample temperature | `sint8` whole degrees Celsius | tenths of a degree on a §6.5 channel |
| tank pressure | `uint16` bar scaled by 100 | bar; tenths of a bar on a §6.5 channel |
| elapsed and timer time | `uint32` seconds scaled by 1000 | whole seconds, halves away from zero |
| CNS | `uint8` percent | percent |
| OTU | `uint16` "OTUs" | the same number |
| oxygen, helium | `uint8` whole percent | whole percent |
| coordinates | `sint32` **semicircles** | decimal degrees at six places |
| timestamps | `uint32` seconds from 1989-12-31 UTC | §5.2 date-times |

**A semicircle is 180/2³¹ degrees**, a signed 32-bit count over a full circle of 2³², and
the profile declares no scale factor for it — so the raw count is what a decoder hands
back and this is the one conversion FIT does not do for you. Six decimal places is about
11 cm at the equator, and it is what makes one dive converted from two of its own exports
produce one position rather than two: the same fix reaches this package as a semicircle
count here and as a radian float from the Suunto app's JSON, and the two agree exactly at
six places and disagree below.

`position_lat` is a `sint32` whose invalid sentinel is `0x7FFFFFFF`, and the arithmetic on
that count gives 179.99999991618097 — which rounds to a real, in-range longitude that no
range check catches and that would pin every position-less dive to the antimeridian. The
decoder resolves the sentinel to nothing before this reader sees it.

## Identity

`uuid5(NAMESPACE_URL, "https://divejson.org/ns/fit")` =
**`1ecddff6-d7c6-58e8-a0ec-337a3338b855`**, frozen forever. Changing it would renumber every
document any released version of this converter has produced from a FIT file, so a port in
another language uses this value and not one of its own.

**A FIT file records no id for its dive**, so a dive's identity is the positional stand-in
`converting.md` defines, and every conversion says so in its report. Under an archive that
stand-in is prefixed by the member's name, so two copies of one file in one zip are two
dives with two identities — which is right: nothing in either file says they are the same
dive, and a watch that writes one file per dive gives an archive its order.

## The message map

Every number below was read off real files and off the MIT-licensed profile this package
decodes through. **Nothing here comes from Garmin's `Profile.xlsx`**, which nobody on this
project downloads; see the notice in [`README.md`](../README.md).

### Provenance — `file_id` (0), `device_info` (23), and the file header

| field | | into |
| --- | --- | --- |
| `file_id.product_name` (8), else `manufacturer` (1) | | `extensions.divejson.source_generator.name` |
| `device_info.software_version` (5) | | `…source_generator.version` |
| the header's protocol version | | `extensions.divejson.fit_protocol_version` |

`source_generator` is the **device** rather than an application: a FIT file is written by
the computer on the diver's wrist. The firmware is taken from the first `device_info` whose
`manufacturer` (2) matches the `file_id`'s — a `device_info` naming a different one is
another thing in the chain, and reading its firmware as the computer's would put a
transmitter's version on the dive. At most eight `device_info` messages are kept; a device
writes one every few minutes.

Neither Suunto file in `fixtures/fit/` carries a `software_version`, so neither generator
has a version, which is the honest answer rather than an omission to fill in.

### The dive — `session` (18), and `dive_summary` (268) where there is one

| field | | into |
| --- | --- | --- |
| `session.start_time` (2) | | `started_at`, in the zone below |
| `session.total_elapsed_time` (7), else `total_timer_time` (8), else `dive_summary.bottom_time` (11) **untested** | | `duration` |
| `session.max_depth` (141), else `dive_summary.max_depth` (3) **untested**, else the depth samples | | `max_depth` |
| `session.avg_depth` (140), else `dive_summary.avg_depth` (2) **untested**, else the depth samples | | `avg_depth` |
| `dive_summary.start_cns` (5) **untested**, else `session.start_cns` (143) | | `cns_start` |
| `dive_summary.end_cns` (6) **untested**, else `session.end_cns` (144) | | `cns_end` |
| `dive_summary.o2_toxicity` (9) **untested**, else `session.o2_toxicity` (155) | | `otu_end` |
| `dive_settings.water_type` (4) | | `water_type` |

**The duration order is what a diver means by one.** `total_elapsed_time` is the wall clock
from the moment the dive started to the moment it ended. `total_timer_time` excludes pauses,
a distinction that barely exists underwater, and stands in where a device omits the first.
Garmin's `bottom_time` is deliberately last: it measures time *at depth*, not the dive.

**The depths read the session first and the oxygen accounting reads the summary first**,
which looks inconsistent and is not. The depths agree wherever both exist, and the fallback
is for a device that summarises a dive in one message and not the other. The CNS and OTU
totals are the *dive's*, and on a multi-dive Garmin file the session's cover the whole
activity while the `dive_summary` describes the dive being read.

A Garmin freediving activity writes a `dive_summary` per descent **plus** a session-level
one, and `reference_mesg` (0) is what separates them: the summary referring to `session` is
the one read, and the first of any kind stands in for a single-dive export that writes one
with no `reference_mesg` at all. Taking the first unconditionally would read one descent's
depth and bottom time as the whole dive's.

**`o2_toxicity` is the dive's ending OTU total rather than the OTUs it added**, which the
profile's bare "OTUs" unit does not settle. A file does: one dive in the recorder's hand
exists as both a FIT and a Suunto DM5 XML export of the same 18.02 m dive on 2025-03-06,
and where the XML records `<OtuStart>22</OtuStart>` against `<OtuEnd>23</OtuEnd>`, the FIT
writes `o2_toxicity = 23`. Read as a delta it would have been 1. The same pairing settles
`end_cns` — `<CnsEnd>9</CnsEnd>` against the FIT's `end_cns = 9` — and shows the gap this
reader reports rather than fills: the XML records `<CnsStart>8</CnsStart>` and the FIT
carries no `start_cns` at all, which is why that member is the one `absent` line both
fixtures raise.

`water_type`'s enum is `{fresh, salt, en13319, custom}`, and three of the four are §6.2
members under the same name. `en13319` stays `en13319` rather than being folded into `salt`:
it is the calibration a computer ships set to, and rewriting it as the nearest real water
would be inventing a reading. `custom` says the diver dialled in a `water_density` number,
which §6.2 has nowhere to put, and is reported rather than rounded off. A device that wrote
no `dive_settings` raises nothing — the message is the computer's *configuration* rather
than a record of the dive, unlike the session summaries above, every one of which the
device was describing this dive when it left empty.

### The depth from the samples is `inferred`, and is listed

The third source for `max_depth` and `avg_depth` is the only one that is this converter's
own arithmetic, so it is `inferred` and the document lists the member under
`extensions.divejson.inferred` (spec §5.4). Every `inferred` note this reader raises has its
member on that list and every member on the list has a note; the two are one decision.

The computed `avg_depth` is the arithmetic mean of the depth readings, which is the mean
*depth of the dive* only where the device sampled at a constant rate. Every file in this
project's hand does; a device that sampled faster on descent would weight it towards the
descent, which is why this is the last resort and why it is labelled as computed. It is
quantized to two places, which is what a device's own `avg_depth` carries.

A mean deeper than the maximum cannot be — which a device's own `avg_depth` and a maximum
computed from the samples can produce between them — and the mean is dropped rather than
either being adjusted to fit (spec §6.2). **The `inferred` findings are raised after that
check rather than as the values are found**, so a dropped mean is reported as dropped and
nothing says the document carries a value computed from the samples when it carries no
value at all. Raising them first and unlisting the loser afterwards leaves the report and
the list disagreeing, which is the one thing this pairing may never do.

### The local time zone — `activity` (34)

Every FIT timestamp counts seconds from 1989-12-31 UTC. `activity.local_timestamp` (5) is
that same instant written as the wall clock at the dive site, so **the difference between it
and `activity.timestamp` (253) is the offset** §5.2 asks to be preserved. It is recovered,
not assumed, which is the distinction §5.2 exists for.

Rounded to whole minutes, since no real zone has sub-minute resolution and the two
timestamps are second-resolution readings that may be a second apart. A gap wider than
±14:00 means one of the two is corrupt and is read as no offset at all; the standard library
raises past 24 hours, which would leave a converter crash where a report line belongs.

A file with no `activity` message carries no offset to recover, and its dive is read as the
UTC instant the device recorded, reported. That is not an invented offset — FIT states
outright that its timestamps are UTC. What is lost is the wall clock the diver read.

**A recorded sub-second fraction would be preserved.** FIT's `date_time` counts whole
seconds, so no file can carry one today; §5.2 makes the fraction optional, and truncating a
recorded value is not something any of the four report kinds could honestly describe.

### Cylinders — `dive_gas` (259)

| field | | into |
| --- | --- | --- |
| `oxygen_content` (1) | | `oxygen`, whole percent |
| `helium_content` (0) | | `helium`, whole percent |
| `mode` (3) = `closed_circuit_diluent` **untested** | | `role: diluent` |
| `status` (2) = `disabled` | | the cylinder is dropped and reported |
| `message_index` (254) | | the order the cylinders are written in |

A device stores its **whole configured gas list**, so a `disabled` entry is a gas the
computer was programmed with and this dive did not carry: a recreational air dive on a
computer with two deco gases set up would otherwise arrive with three cylinders.
`backup_only` is deliberately *not* dropped — it is a pony bottle, carried and not breathed,
which is a cylinder that was on the dive and belongs in the logbook.

**`message_index` is a bitfield rather than a counter**: the low 12 bits are the index and
the top bits are flags. Without masking, a gas at index 1 with the "selected" bit set
arrives as 32769 and sorts after an unflagged gas at index 2, reordering the cylinders. The
list is deduplicated on the masked index, keeping the first, since a device may re-announce
it mid-file; entries with no index at all are kept in a space of their own and appended,
rather than being keyed by position — keying a position into the same table as a real
`message_index` makes a gas at position 0 collide with a gas declaring index 0, and one of
the two vanishes.

`mode` is the only field on `dive_gas` that speaks to a cylinder's role, and it answers half
the question: its enum is open circuit or closed-circuit diluent, so a diluent identifies
itself while `open_circuit` covers a back gas and a stage bottle alike and maps to nothing.
Pointedly not derived from `status`, which says whether a gas was carried rather than what
for.

**FIT has nowhere to record a cylinder's size.** Not on `dive_gas`, and `tank_summary`
carries only the volume *used* — so `volume` is absent on every cylinder this reader
produces. That is a property of the format rather than of any file, which is why it is here
and not in the report: a line per cylinder saying the format cannot hold something would say
it of every FIT ever written.

### Tank telemetry — `tank_summary` (323) and `tank_update` (319), both **untested**

| field | | into |
| --- | --- | --- |
| `tank_summary.start_pressure` (1), else the pod's first `tank_update.pressure` (1) | | `start_pressure` |
| `tank_summary.end_pressure` (2), else the pod's last `tank_update.pressure` | | `end_pressure` |
| every `tank_update.pressure` for one pod | | a `profile.pressures` channel |
| `sensor` (0) | | which pod a reading belongs to |

The two sources are joined **per pod and per field** rather than one taking over from the
other. The summary is the device's own figure and wins where it has one; anything it leaves
empty falls through to the ends of that pod's telemetry, since a transmitter streams
throughout the dive whether or not a summary is also written. The realistic case is the
partial one: a pod that drops out near the end writes a summary with a start pressure and no
end, and the last real reading is the one a gas calculation turns on.

"The ends" means the **earliest and latest recorded**, not the first and last the file
listed. No writer guarantees it emitted its samples in order — which is why the §6.5 axis
sorts — and this pod's own pressure channel comes off that axis, so reading the ends out of
file order would put one pair of readings on the cylinder and a different pair at the ends
of its channel in the same document.

**Nothing in a FIT file links a gas to a pod.** Telemetry is keyed by the transmitter's ANT
id and a `dive_gas` by its `message_index`, and no message maps one onto the other. Position
is the only signal there is, so the two are paired in order and **only when the counts match
exactly**; anything else — two gases and one pod — leaves the pressures out and says so,
rather than attaching a start pressure to a cylinder it may not have been measured in.

A file with tank telemetry and no gas list at all is the other way round: evidence of a tank
is evidence of a tank, so each pod becomes a cylinder carrying its pressures and nothing
else. A `tank_summary` naming a pod and carrying no pressures grows no cylinder, since it
describes nothing; one with no `sensor` at all cannot be joined to anything and stands as its
own cylinder, numbered after the identified ones and contributing no channel.

`sensor` is read **undecoded**. The profile renders a field whose type carries an enum by
exact value match and keeps bitfield masks in that same slot, so an ANT id that lands on
65535 comes back as the word `ant_device_number` rather than as a number. The same is true
of `message_index`, where 4095 renders as `mask` and 32768 as `selected` — and
`message_index = 0x8000` is an ordinary thing for a device to say about its first configured
gas.

A recorded **zero** start pressure is a device's absent-marker rather than a measurement
(§6.3), an end pressure above its start is dropped, and a pressure outside 0–350 bar is a
source defect rather than a scale to reinterpret: the profile states the unit outright.

### The profile — `record` (20)

| field | | into |
| --- | --- | --- |
| `timestamp` (253) | | the sample's place on the axis |
| `depth` (92) | | the `depth` channel, centimetres |
| `next_stop_depth` (93) | | the `ceiling` channel, centimetres |
| `temperature` (13) | | the `temperature` channel, tenths of a degree |
| `position_lat` (0) / `position_long` (1) | | `entry_position` / `exit_position` |

The axis origin is the session's own `start_time`, so the profile's seconds are elapsed time
from the instant `started_at` names; a file whose session recorded no start time falls back
to its earliest reading. The rest of the axis — ordering by recorded time, the sample with no
time, two samples on one second, a dive whose samples carry nothing this format can hold — is
`converting.md`'s and shared with every other format.

**Each channel takes only the records that carried its reading.** A Suunto Ocean writes 4,295
`record`s of which 431 carry a depth and 4,294 a temperature, and padding either to the
other's length would invent nearly four thousand depths the dive never reached. Readings from
different messages at one instant are collected into one sample, so a `record` and a
`tank_update` at one second are one sample rather than two; two readings of the *same*
channel at one instant keep the first and report the second.

`next_stop_depth` is FIT's deco ceiling — the depth of the next required stop, in metres,
scaled like `depth` beside it. **Not** `next_stop_time` (94), `time_to_surface` (95) or
`ndl_time` (96), the three neighbouring fields that measure durations rather than a depth.

**A ceiling of zero is not a ceiling.** Zero says the diver may surface — the absence of an
obligation rather than an obligation at 0 m — and reading it as a reading would draw a flat
line along the surface across every no-deco dive in a logbook.

### Positions, and where a fix belongs

**No fix is taken underwater**, a receiver not reaching a wrist through seawater, so every
position in a dive log was recorded at the surface and the only question worth asking of one
is which surface interval it belongs to. The deepest sample is the split: the last fix at or
before it is the entry and the first after it is the exit, because the fix that says where a
diver got in is the one taken just before they descended rather than the one from when the
boat left the jetty.

The deepest sample is the pivot in preference to an in-water *window*, which would need a
depth threshold this reader would have to invent. With no depth channel there is no pivot and
so no answer, and nothing is written.

### Events — `event` (21)

| `event` (0) | into |
| --- | --- |
| `dive_gas_switched` | `gas_switch` **untested** |
| `user_marker` | `bookmark` **untested** |
| `dive_alert` | `other`, labelled **untested** |

A table rather than a cast: this is Garmin's vocabulary, and the other 43 members of its
46-member enum — `battery`, `off_course`, `power_down`, every cycling and running alert the
shared enum holds — have to come out as nothing rather than be forced into a type of this
format's.

**`timer` is the deliberate omission.** It is the only `event` any file in this project's hand
writes, and its start/stop pair says where the dive begins and ends, which §6.4's `started_at`
and the profile's own axis already say twice over.

`dive_gas_switched` carries the switched-to gas's **`message_index`** in `data` (3), which is
the device's own key for a `dive_gas` entry and not the position §6.3 numbers a cylinder by.
It is resolved through the same list the cylinders were built from, so a marker names the
cylinder the logbook shows. Where it cannot be resolved the marker is still emitted with no
`gas_number`: a switch to a gas whose `dive_gas` was disabled or absent is a real switch that
happened, and saying "a gas switch, to something this file does not describe" is honest where
guessing a position would not be.

For `dive_alert`, `data` renders through the profile's own `dive_alert` enum, so the label is
the device's wording — `deco_ceiling_broken`, not a number — and an alert outside the enum
decodes to the bare integer, which is still more than "something happened". §6.5 requires a
label on `other`, so an event with nothing at all to say is dropped rather than failing the
whole conversion.

**`gas_number` is asserted only where something depends on it.** §6.3 calls it a label rather
than an array index, so the cylinders are numbered when the profile carries a pressure channel
or a gas switch naming one, and not otherwise.

## This format settles no ambiguity

The profile states every unit, so there is no fraction-or-percent and no litres-or-cubic-metres
for a magnitude test to decide. This reader therefore raises no `resolved` finding, and the
three kinds its report can carry are `absent`, `inferred` and `dropped`. A `resolved` appearing
here would mean it had started guessing at something the profile already says.

It is the only reader in this package that raises `inferred`, and the only one for which the
`extensions.divejson.inferred` list is ever written.

## Deliberately not mapped

- **`session.avg_temperature` / `max_temperature`** — whole-degree `sint8` summaries of the
  *activity*, and on one file in hand the "max" is a degree below the "avg", which is not a
  reading of anything a dive log has a member for. §6.2's `bottom_temperature` would have to
  come from the `record` channel instead, which would make it this converter's arithmetic and
  therefore `inferred`; the temperature channel is already in the profile, where a reader can
  see all of it rather than one summary of it.
- **`session.total_distance`, `avg_speed`, `max_speed`, `total_calories`,
  `training_stress_score`, `total_training_effect`, `enhanced_min_altitude` /
  `max_altitude`** — an activity tracker's members, not a dive log's. §6.2's `altitude` is the
  altitude of the *site*, which none of these is.
- **`session.dive_number` and `surface_interval`** — the device's own counter and the gap
  before the dive. §6.2's `dive_number` is the diver's own numbering, and a device's is not
  reliably it; the surface interval is derivable from two dives' start times and is a
  property of a pair rather than of one file.
- **`record.vertical_speed`, `distance`, `speed`, `altitude`, `cns_load`, `n2_load`, `po2`,
  `ndl_time`, `time_to_surface`, `absolute_pressure`** — §6.5 fixes the channels a profile
  carries, and none of these is one of them.
- **`dive_settings`'s thirty-odd other fields** — gradient factors, PO₂ alarm thresholds,
  backlight, safety-stop times. The computer's configuration, not the dive.
- **`event.timer`** — above.
- **`otu_start`** — there is no `start_otu` anywhere in the FIT profile, so this member has no
  source at all, on any device.
- **Every developer field** — above.

## The pairs

`fixtures/fit/` holds the conformance pairs for this reader — an input, and the document a
correct reader produces from it. The specification adopts them after a release, and
`fixtures/README.md` gains its rows then.

**Both inputs are committed as recorded**, which is the exception `fixtures/README.md` already
carries: a binary file cannot be reduced by hand, and a synthetic one would prove that an
encoder and a decoder agree rather than that a device's file reads. The recorder chose a dive
whose position they are content to publish.

| file | recorded on | what it covers |
| --- | --- | --- |
| `suunto-ocean.fit` | Suunto Ocean, product 62 | The known answer, and the developer-field trap: a `session` carrying `max_depth` 45.91 natively beside a developer `float32` of 45.90999984741211. 4,295 `record`s of which 431 carry a depth and 4,294 a temperature, on their own axes; two enabled gases at 21 % and 54 %; 28 satellite fixes, all of them after the deepest sample, so the dive has an exit position and no entry; a `+02:00` recovered from `activity`; and `start_cns` as the one mapped member its session leaves empty. |
| `suunto-d5.fit` | Suunto D5, product 39 | The same trap on a different product six years earlier — 32.41 against a developer 32.40999984741211 — and the small end of the format: 200 `record`s carrying a depth and a temperature each, one gas, and no position at all. |

Neither file carries a `dive_summary`, a `tank_summary`, a `tank_update`, a `water_type`, a
`software_version`, a disabled gas or any `event` but `timer` — which is the list of what a
Garmin export is expected to bring, and what the encoder-built tests stand in for until it
arrives.
