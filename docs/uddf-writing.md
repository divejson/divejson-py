# Writing DiveJSON as UDDF

**Non-normative.** The specification is [`spec/divejson.md`](../spec/divejson.md); nothing
here changes what a conforming document is. [`uddf-mapping.md`](uddf-mapping.md) is the
other direction — every element a reader takes into DiveJSON — and this document is what a
writer does with a DiveJSON document that has to become a UDDF file: which member lands in
which element, what UDDF has no room for, and what a reader will make of each thing that
did not fit.

The rules a writer follows whatever format it is writing are in
[`writing.md`](writing.md) — how a writer is checked, the three answers to a required
element the document has nothing for, the report's kinds read on the way out, and a
writer being a function of its input — and the rules that hold in **either** direction are
in [`converting.md`](converting.md), identity among them. Neither is repeated here. This
document carries what is UDDF's, and keeps beside each general rule the example that first
showed it.

## Why write UDDF at all

Because it is what the rest of the world opens. Subsurface, divelogs.de and MacDive read
UDDF, and a diver whose logbook is DiveJSON needs a file they can hand to a dive shop, load
into a desktop application, or upload — the same reason the reading direction exists,
pointed the other way.

DiveJSON's own **reference writer** is the application the format came out of, and it stays
the reference: this is a second writer serving applications that are not that one, not a
replacement for it. The two agree on everything a logbook actually holds, and differ wherever
an application exporting its own data and a converter producing an interchange file want
opposite things. **Each such place is marked "Differs from the reference writer" below**, and
those markers are the list — no count of them is stated anywhere, a figure kept away from the
thing it counts being a second place for it to be wrong.

## What a correct writer is checked against

The three checks are `writing.md`'s. What is UDDF's is how each one lands here.

**The pairs are compared as canonical XML with `<generator>` ignored**, which is this
format's answer to the general question of how two of its files are compared when one was
produced just now: two runs of one writer differ in the version stamped there and in nothing
else. [`fixtures/write/uddf/`](../fixtures/write/uddf) holds them and `divejson conform` runs
them; what each covers is in [`fixtures/README.md`](../fixtures/README.md#writeuddf).

**The self round trip** reads a written file back through a UDDF reader and expects the
document it was written from, on every member [`uddf-mapping.md`](uddf-mapping.md)'s element
map carries. This document is the description of what does not come back.

**It is the only thing that checks a scale both directions agree on.** `divejson conform`
compares a written file with a committed one and never reads it back, so a writer and a
reader that disagree about whether `<gradientfactor>` is percent or a fraction produce two
green corpora and a value a hundred times wrong — see *the gradient factors* below. Every
member this writer scales owes that test in an implementation's own suite, not a fixture.

**`extensions` is the exclusion this direction adds** to the two the corpus already ignores
(`writing.md`), and UDDF is where it is visible: `<generator>` and `/uddf/@version` describe
the file in front of a reader, which after a write is the one this writer produced, so the
source's own generator and declared version stay behind.

**The XSD is the check the corpus cannot make.** UDDF 3.2.2 has one, so validating output
against it belongs in an implementation's own suite — and here it is the only check that can
see element **order**, which is a live hazard in this format: `informationbeforedive`,
`waypoint`, `equipment`, `tankdata` and `trippart` are all `xs:sequence`, so a member added
in the wrong place produces a file a lenient reader — including this format's own, which
takes children by name — is perfectly happy with and no other implementation can open.

## The three answers, in UDDF

`writing.md` states them: write the format's own spelling for "not recorded", or drop the
value and report it, and never invent. UDDF is a clean example of the distinction that
decides between the first two, because it has both shapes as **mandatory** elements.

`<greatestdepth>` is mandatory and its `0` *is* the format's spelling for absence, so it is
written and a reader takes it straight back off. `<geography><location>` is mandatory and a
place name has no such spelling — so a site with coordinates and no `location` loses the
coordinates rather than having its own **name** copied into a member that means something
else. A round trip through that would hand the diver back a location they never wrote.

## The report, going out

`writing.md` has the kinds and what a `where` is. In this format `absent` is an element UDDF
requires that the document had nothing for, and `dropped` is a member UDDF has nowhere to
put — or, in the two findings *Devices* below describes, a fact about the order a dive's
recordings come back in, which the file has nowhere to carry either; the paths are
`dives/0`, `dives/0/cylinders/1`, `trips/0/locations/1` and `$`.

A member with nowhere to go is reported from the record itself and not from a list
(`writing.md`), so **the tables below are a description of what a writer does and not the
source of it** — the next member §6 gains reports itself here rather than going silently.

## The element map

Read `uddf-mapping.md`'s map backwards for everything not called out here — the rows are the
same rows. What follows is only where writing is not simply reading in reverse.

### Document

| DiveJSON | UDDF |
| --- | --- |
| — | `/uddf/@version` — `3.2.2`, the schema written against |
| `generator` | not carried; `/uddf/generator` names **the writer** |
| `exported_at` | `/uddf/generator/datetime` |
| `extensions` | not carried — see above |

`<generator><type>` is `converter`, which is one of the three values `generatorType`
enumerates. The reference writer says `logbook`, being one.

**`<generator><datetime>` is the document's own `exported_at` and never the clock**, which
is where `writing.md`'s function-of-its-input rule lands in this format: the only thing left
moving between two writes of one document is the version stamped beside it, which is exactly
what the pair comparison ignores.

### Identity

Ids are written `<kind>-<uuid>` — `dive-019fec36-b9ec-71c6-a03e-64f59b8b92b1` — because
`xs:ID` is an `NCName` and cannot begin with a digit, which a hex UUID regularly does.
`converting.md`'s identity rule reads a short alphabetic prefix back off, so an id written
this way returns as the UUID it was and a logbook that went out through UDDF comes back with
the identities it left with. The prefixes are `dive`, `site`, `trip`, `gear`, `diver` and
`mfr`.

`<owner id>` is `diver-<uuid>` where the document names a person and the bare string `owner`
otherwise — the latter being what every UDDF writer in the corpus emits and what a reader is
careful never to read as an identity.

### Diver and gear

A document's gear lives inside `<diver><owner><equipment>`, so `<diver>` is written whenever
there is either an owner to describe **or** a piece of kit to hang on one. An owner the
document says nothing about gets empty `<firstname>`/`<lastname>` elements, which are valid
`xs:string`s that a reader takes as no diver at all.

`personalType` requires a first and last name where DiveJSON holds one string: the first
whitespace-separated token becomes the given name and the remainder the family name, and a
one-token name leaves `<lastname>` **empty** rather than repeating the given name. A reader
joins them back with a space, so any split survives.

**Differs from the reference writer**: `diver.email` is written to
`<contact><email>`. The reference writer deliberately omits it — a UDDF file is the thing a
diver hands to a dive shop, and their address riding along in it would be a surprise — which
is the right call for an application exporting on a diver's behalf and the wrong one for a
library asked to write the document it was given.

Gear types land in `equipmentType`'s elements. A type UDDF's own vocabulary does not name
goes to **`<variouspieces>`**, its catch-all, which a reader maps back to `other`; that is
the same translation a reader makes for `<scooter>` and `<lead>` in the other direction, and
it is reported, since the type did not survive.

| `gear.type` | UDDF element | comes back as |
| --- | --- | --- |
| `mask`, `fins`, `gloves`, `boots`, `compass`, `knife`, `light`, `regulator` | the same name | itself |
| `bcd` | `buoyancycontroldevice` | itself |
| `computer` | `divecomputer` | itself |
| `cylinder` | `tank` | itself |
| `wetsuit`, `drysuit` | `suit`, with `<suittype>` | itself |
| `other` | `variouspieces` | itself |
| `snorkel`, `vest`, `hood`, `smb`, `mirror`, `whistle`, `reel`, `camera`, `line_cutter`, `shears` | `variouspieces` | `other` |

`camera` is in the last row for a schema reason rather than a vocabulary one: `cameraType`
extends `ID_TYPE` rather than `namedType`, so a `<camera>` has no `<name>` at all and could
carry only a nameless body-and-lens breakdown.

**A gear item's `serial` (§6.12) is written to `<serialnumber>`**, on whatever element its
type produced. `equipmentPieceType` carries that element for every piece rather than only
for a computer, which is the same breadth §6.12 gives the member, so a serialled regulator
keeps its serial through a round trip like any other piece. On a `computer` it does more
than travel: it is what the fold below tests before any name, and it is what a folded
element hands back to the gear item on the way in ([`uddf-mapping.md`](uddf-mapping.md)),
which is what closes that round trip rather than merely surviving it.

**Differs from the reference writer**: it sends `line_cutter` and `shears` to `<knife>`, on
the grounds that they are cutting tools and that scattering a diver's cutting tools into the
catch-all beside the SMB reads worse. A converter does not, because `<knife>` asserts a
knife where `<variouspieces>` asserts nothing — losing a type honestly beats substituting a
near one, and the report is what makes the loss visible either way.

**`equipmentType` is an `xs:sequence`, so a logbook's gear comes back grouped by type**
rather than in the order the document listed it. Nothing is lost by that — every piece keeps
its uuid — so it carries no finding.

### Devices, and the one element they share with gear

UDDF's `<divecomputer>` is a piece of kit *and* the hardware that recorded a dive
(`uddf-mapping.md` reads it both ways), and this direction has to put both into one
element wherever it can: **one `<divecomputer>` per computer**, not one per gear item
plus one per recording. Writing the same computer twice would put two kit items in a
reader's gear list where the diver owns one, and give one machine two `xs:ID`s. The fold
below is how the two are recognised as one — and its first leg is the only thing that can
still cost a computer a second element, for a reason set out below.

**When two are one computer.** The devices go first: they fold with each other before any
gear item is considered, so that one computer's every recording is a single **device
record**. That record is what the legs below test, and testing the record rather than each
recording's device separately is what keeps the cardinality rule at the end of the list from
reading one computer's nine other dives as nine ties.

Between a gear item `G` whose `type` is `"computer"` and a device record `D`, with every
string trimmed and case-folded and `label_D` being `D.name` else `D.model`:

- **The dive must link the gear item**, and this leg is asked per **recording** rather than
  per computer. A folded element reaches a dive only through the `<equipmentused><link>` the
  dive's own `gear_uuids` produced, so a recording whose dive does not list `G` does **not**
  fold, whatever the legs below would say, and its device takes an element of its own. Where
  `D` covers several recordings and only some of their dives link `G`, `D` splits along that
  line: those recordings fold and the rest do not. This leg asks whether folding is open at
  all; the ones below ask which gear item a linked `D` folds into.
- **Both carry a serial** — §6.12's and §6.4b's — → fold **iff** the two serials are equal.
  **Serials that differ mean different computers, and there is no fall-through to the
  label.** Without that leg, two Suunto Oceans each plausibly named `Suunto Ocean` with
  brand `Suunto` fold on the label and one machine's serial goes out on the other's element.
- **Otherwise** → fold iff `label_D` is present, `G.name` equals it, and the brands do not
  disagree — `G.brand` against `D.brand`, an absent brand on either side disagreeing with
  nothing.
- **`label_D` absent** → **no fold**, whatever else matches. A device carrying only a brand
  matches nothing.
- The **same predicate folds two devices**, `label` being `name` else `model` on each side.
  This is the step above stated as the rule it is, and it is the one leg the link plays no
  part in: two devices have no gear item between them, so the first leg has nothing to ask
  of them and a computer's recordings group whatever their dives link. That is where the
  serial leg does its real work: one computer recording ten dives is ten recordings and one
  device record.
- **At most one gear item per device record and one device record per gear item.** On a tie,
  the first in document order wins and the rest are reported — a fold is not a merge, and
  silently picking one of three would put a serial on an element the diver never meant. A
  tie here is two *different* computers claiming one kit item, the losing one keeping an
  element of its own.

**This is deliberately not symmetric about absence, and the asymmetry is worth stating
because the neighbouring comparison is.** Asking whether two *files* are records of one
computer, an absent member on either side means "this format has no such field" rather than
a mismatch — calling it one there would split one computer's recordings across two records.
The fold is not that comparison. One side is a user's own record whose `name` is REQUIRED
(§6.12) and the other is a file reading that is routinely one member wide, so carrying the
symmetric rule across would make a bare device match every computer the diver owns. Hence
the `label_D`-absent leg above: absence on the device's side is a refusal to guess, not a
match.

What a folded element carries, in `equipmentPieceType`'s own sequence — `<name>`,
`<manufacturer>`, `<model>`, `<serialnumber>`, `<notes>`, which is an `xs:sequence` and not
a free order:

| DiveJSON | UDDF |
| --- | --- |
| the gear item's `name`, else the device's `name` | `<name>` — mandatory, exactly one; **empty** where neither side carries one |
| the gear item's `brand`, else the device's `brand` | `<manufacturer><name>` |
| `recordings[].device.model` | `<model>` |
| the device's `serial`, else the gear item's | `<serialnumber>` — where both carry one the fold has already made them equal |
| `recordings[].device.dive_number` | the dive's `<internaldivenumber>` |

**The `<name>` row stops at the device's `name` and does not fall through to its `model`**,
which is what makes the element of a device that did not fold round-trip. `<name>` is
mandatory on the element, so a device on an element of its own with no `name` of its own —
there being no gear item beside it to supply one — gets an **empty** one: a valid
`xs:string` that the reading direction takes as no name at all, so it comes back as no
`device.name` (§6.4b forbids an empty member) and, because §6.12 makes a gear item's `name`
REQUIRED, as no gear item either. Writing the model there instead would hand a reader back
two things the document never had: a `device.name` and a kit item, both spelled `Perdix 2`.

`<internaldivenumber>` sits on the dive rather than on the element, between `<divenumber>`
and `<datetime>` in `informationbeforediveType`'s sequence, and it is an
`xs:positiveInteger` where §6.4b puts a floor of 0 under the counter — so a device counter
of `0` is **not written**, and is reported, the same trade `<divenumber>` already makes: a
zero there invalidates the whole document rather than one element.

**A device that does not fold gets an element of its own, and that element reads back
as a gear item — where the device carries a name.** Three things send a device here: no gear
item matches it; one does and its recording's dive does not link it, which is the first leg
above; or one does and the cardinality rule awarded that gear item to a different device
record, the tie's loser being reported and left with an element of its own. This is
the one place a written file returns *more* than it was written from, and it is the only
documented exception to `writing.md`'s self round trip, which is otherwise a rule about what
does not come back. A **nameless** such device is outside the exception rather than a second
one: its element carries the empty `<name>` above, the reading direction drops a nameless
piece, and nothing comes back that did not go out. A logbook whose dives were imported from
files but whose owner never listed the computer in their kit is the ordinary case, so the
alternative — writing no element — would lose the device from every such file, and the
device is why this member exists. The element is `<divecomputer id="device-<n>">`, numbered
from 0 over the document's recordings in order, with `<manufacturer id="mfr-device-<n>">`
beside it: a deliberately **non-UUID** id, so that `converting.md`'s identity rule mints the
returning gear item a derived uuid of its own rather than reading a real one back off it —
an id built from a dive's uuid would come back as a gear item wearing that dive's identity,
which §5.3 forbids outright. The dive's `<equipmentused>` gains a `<link>` to it, after the
links its `gear_uuids` produced.

**A folded device reaches a dive only through its gear item's link, which is why the fold
has a first leg at all.** The element is the gear item's, so a dive whose `gear_uuids` does
not list that item carries no link to the computer that recorded it — and a document may
perfectly well say a recording's device was `D` while leaving the matching kit item off that
dive's list. Folding there would drop the device from the file: nothing on the dive would
point at the element holding it, and a reader would hand that dive back with no device at
all. So that recording does not fold, and its device takes the `device-<n>` element above,
which needs no gear link. Both facts then survive — which computer recorded the dive, and
which gear the diver recorded using — and since nothing is lost, nothing is reported.

**Linking the gear item anyway** would be the tidier file and a worse one: `<equipmentused>`
is what the diver wore, and a writer adding a piece to it would be answering a question about
the dive that the document answered differently. Coming back in, it would also credit that
item with a dive it was never worn on, inflating a `dive_count` the diver never recorded.

**The price is a computer that appears twice**, and a document pays it whenever a `computer`
gear item it carries is matched by a device whose dive does not link that item. That takes in
the ordinary shape where **no** dive links any gear at all — a logbook keeping its kit list
at the owner's level rather than per dive — as much as the mixed one: the gear item goes into
`<equipment>` whatever the dives say, and the recordings that could not fold into it take a
`device-<n>` element beside it, so two elements describe one machine and a reader takes the
second as a second kit item, under the exception above. Where some dives link the item and
others do not, both elements are in use at once, the linked dives reading their device off
the gear item's element and the rest off the `device-<n>` one. This is the one thing the
*one `<divecomputer>` per computer* rule this section opens with does not hold for. A
duplicate in a kit list is visible to the diver and correctable in a moment; a device that
never arrived is neither, which is what makes this the cheaper of the two.

**The links come back in the kit list's order rather than the recordings'**, and where the
two disagree the dive loses something on the way in. A reader recovers a dive's recordings
from its `<equipmentused>` links and gives the dive's two once-per-dive facts — its
`<samples>` and its `<internaldivenumber>` — to the **first** `<divecomputer>` linked, there
being nothing else in the file to give them to
([`uddf-mapping.md`](uddf-mapping.md), which also says a reader must not read primacy into
that order). The links themselves are the dive's `gear_uuids` in the diver's own order, with
the `device-<n>` elements appended after them. So the sequence is a fact about the gear list
and not about the recordings, and the two agree only by construction. Rewriting them is not
open: `<equipmentused>` is the diver's own list and its order is a member of the document, so
a disagreement is a loss to name rather than a file to rearrange.

Two shapes, each reported against the dive:

- **A `computer` gear item the dive links that no recording's device matches takes the first
  link**, so the profile and the device counter come back on *that* computer rather than on
  the one that recorded the dive. The ordinary case is a diver whose kit list still holds an
  old computer, listed on a dive some other machine recorded.
- **A dive's own computers reached in another order** — its kit list running through them
  differently, or one of its devices folding into nothing and taking a `device-<n>` element
  appended behind the rest — so its recordings come back reordered. §6.4a makes that a fact
  about the document rather than a presentation detail: a recording has no uuid (§5.3), so
  its position is the only thing that says it is the primary.

**Neither has a pair**, both writer pairs putting the primary's element first, so both are
written down here rather than left to the first writer to meet one — the same answer the link
leg's refusal gets in [`fixtures/README.md`](../fixtures/README.md#writeuddf).

**What is *gained* is not reported, which is the same rule read the other way.** A linked
computer no recording answers to, anywhere but that first link, comes back as a recording the
document never had — exactly as it comes back as a gear item the document never had, which is
the documented exception above. Nothing is lost, so nothing is said. That covers the most
ordinary two-computer logbook there is, and the hand-logged dive with no recordings at all in
a logbook whose owner listed their computer in their kit.

**`device.firmware` has no slot**, `equipmentPieceType` carrying no such element, and is
reported once per device that has one. So is a recording's **`source_files`**: §6.7 is
metadata about bytes UDDF has nowhere to reference, which is the same answer the dive-level
member got before it moved.

**A recording's `started_at` has no slot either**, and this is the one worth being careful
about. UDDF gives a dive one `<datetime>` and one `<samples>`, so a document whose dive
carries more than one recording cannot be written whole: the **primary** recording — the
first, §6.4a — supplies the `<samples>`, and every other recording is reported as dropped,
with its device still folded into `<equipment>` so that what was worn is not lost along with
what it sampled. Where the primary recording states its own `started_at`, `<datetime>`
remains the **dive's**: §6.2's `started_at` is the logbook's and is what every reader of a
UDDF file expects to find there.

### Sites and trips

`geographyType` makes `<location>` mandatory, so **coordinates are written only where the
record has a place name**: a site with a `position` and no `location`, or a trip location
with a `position` and no `display_name`, keeps its name and loses its coordinates, reported.

**Differs from the reference writer**: it puts the record's own **name** in `<location>` and
keeps the coordinates, which is defensible for an application exporting data it holds a name
for and wrong for a converter — a round trip through it hands the diver back a `location`
they never wrote. This is the same trade *The three answers* describes, and it is the one
place in the document where the reference writer takes the third of them.

A trip becomes one `<trippart>` **per §6.9 location**, which is the only shape a list of
places fits: a reader takes a trip's span as the span of its parts and its locations from
their names, so a part per location comes back as the list it was written from. A trip with
no locations still needs one part — `tripType` requires at least one — and gets a nameless
one, an empty `<name>` being a valid `xs:string` that reads back as no location rather than
as one. The trip's dates and its note go on the **first** part, since a reader takes the
span of every part's dates and joins every part's notes.

`<dateoftrip>`'s `startdate` and `enddate` are both `use="required"` and both `xs:dateTime`
where DiveJSON holds plain dates, so each is widened to midnight and a reader takes the date
back off the front. A trip with no `ends_on` has nothing for `enddate`: **the start date is
repeated**, reported, and a reader sees a trip that ended the day it began. UDDF has no
spelling for an open one.

`trips[].locations[].bbox` has no UDDF slot at all.

### Dives

`<divenumber>` is an `xs:positiveInteger` where §6.2 puts no floor under `dive_number`, so a
dive numbered 0 or below is written **without** one and reported. A zero there would make
the whole document invalid, which is a file nobody can open rather than a dive nobody can
number.

`<greatestdepth>` and `<diveduration>` are both mandatory where `max_depth` and `duration`
are optional. Both take **`0`**, reported: the schema excludes zero from each of those
members, so a reader takes the zero back as "not recorded" rather than as the surface or as
a dive of no length.

`informationbeforediveType` is an `xs:sequence`: `<link>`s first, then `<divenumber>`,
`<internaldivenumber>`, `<datetime>`, `<altitude>`, `<equipmentused>`, `<tripmembership>`,
`<surfacepressure>`. `informationafterdiveType` is an `xs:all` and its order is free.

`started_at` is written **exactly as recorded**, offset and sub-second fraction and all;
§5.2's rule that an offset is never supplied applies as much to a writer as to a reader.

Members with no UDDF slot anywhere: `water_type`, `cns_start`, `cns_end`, `otu_start`,
`otu_end`, `entry_position`, `exit_position`, `course_uuid`, `species_uuids`,
`created_at`, and — on the recording rather than the dive —
`recordings[].source_files`, `recordings[].started_at` and
**`recordings[].device.firmware`**, `equipmentPieceType` carrying no firmware element, so a
`dropped` finding reports it on every export whose device has one. *Devices* above has the
reasoning for each of the three. `source_files` was a dive member until it moved onto the
recording (§6.4a) and the answer did not change with it.

### Cylinders and gases

`<tankpressurebegin>` is mandatory where `start_pressure` is optional, and takes **`0`** —
which §6.3 names as the absent-marker devices write and a reader reads back as not
recorded. The cylinder therefore keeps its size and its gas.

**Differs from the reference writer**: it omits the whole `<tankdata>` for a cylinder with
no start pressure, having a `logbook.divejson` beside the export to keep the cylinder in. A
converter has no such second file.

**A cylinder gets a `<mix>` of its own within its dive.** Gases deduplicate across the
logbook — a hundred air dives share one `<mix>` — but two cylinders of *one* dive never
share, even carrying an identical blend. `<tankpressure ref>` and `<switchmix ref>` address
a **mix** rather than a cylinder, and a reader resolves a shared reference positionally, so
a sidemount pair on one blend would come back with its two pressure channels crossed the
moment one of them missed a waypoint the other had.

A cylinder whose gas the document never recorded still gets a `<mix>`, carrying a `<name>`
of `unrecorded` and no `<o2>` at all — which is how UDDF says nothing about a gas, and what
gives that cylinder's pressure channel something valid to point at. "No mix recorded" and
"air" are different gases and one `<mix>` cannot be both.

`<mix>` fractions are 0–1 where §6.3 holds percentages; `<maximumpo2>` is bar in both.
`<mix><n2>`, `<ar>` and `<h2>` are not written: §6.3 models the remainder as nitrogen and
does not model argon or trace gases.

`cylinders[].role` and `cylinders[].usage` have no UDDF slot — the format has no manifold or
sidemount representation at all, so there is nothing to write `usage` into.

**UDDF records no cylinder numbering.** A reader recovers one by counting `<tankdata>`
elements in file order, and only where the profile asks for a numbering — a pressure
channel, or a gas switch naming the cylinder it switched to. So `gas_number` survives in
exactly one case: numbered from 0 by position, on a dive whose profile needs a numbering.
Anything else — a label that is not its position, or a dive with no profile at all — is
reported, there being nowhere in the file to put it.

### Profiles

**Every reading keeps its own second.** UDDF puts everything recorded at one instant inside
one `<waypoint>`, so the waypoints are the **union** of every channel's times, and a
waypoint carries only what was measured at that second — a temperature taken between two
depth samples becomes its own waypoint, with a `<divetime>` and a `<temperature>` and no
depth.

**Differs from the reference writer**, and this is the largest of them. It snaps every
other channel onto the depth axis and *drops* a reading that cannot reach a waypoint within
half the depth channel's typical interval, because two importers mishandle a depth-less
waypoint in opposite and equally fatal ways: Subsurface silently discards it (a 706-sample
temperature curve arrived as 29 in one measured case) and divelogs.de reads its missing
depth as **zero**, producing a stored profile that saws between the seabed and the surface
on every other sample. Those are the right trades for a file written *for* those two
consumers and the wrong ones for a file written to be read back, where a dropped reading is
data loss and a moved timestamp is a reading presented as measured where it was not. See
*Known consumer artefacts* below.

`waypointType` is an `xs:sequence`, and the children written go in this order: `<cns>`,
`<calculatedpo2>`, `<depth>`, `<divetime>`, `<setmarker>`, `<switchmix>`, `<tankpressure>`
(repeatable), `<temperature>`, `<divemode>`, `<gradientfactor>`, `<nodecotime>`. That is the
XSD's own order and not a preference — `<cns>` comes third in the type and therefore first
in a waypoint that carries no alarm or battery reading, while `<nodecotime>` is last of all.

Channel units: depth centimetres → metres, temperature tenths of °C → Kelvin, pressures
tenths of a bar → Pascal, ppO₂ hundredths of a bar → bar, CNS tenths of a percent →
percent, `ndl` seconds → seconds, `gradient_factor` whole percent → the documented fraction,
÷ 100. Each is a decimal factor, and doing the arithmetic in decimal is what makes a round
trip through Kelvin land back on the number it started from.

**The per-waypoint `<gradientfactor>` goes out as the documented fraction**: §6.4's whole
percent divided by 100, so a `gradient_factor` of `67` is
`<gradientfactor>0.67</gradientfactor>`. It is the **only** gradient factor this writer
emits — `<gradientfactorlow>` and `<gradientfactorhigh>` exist in UDDF only inside
`<decomodel><buehlmann>`, and `<decomodel>` is dropped whole for the reason below, so a
document's `gf_low` and `gf_high` reach the file nowhere at all.
`uddf-mapping.md` keys the percent-or-fraction
question on the generator, and **this writer is not a generator that table names** — it
stamps `<generator><name>divejson convert</name>`, and the table's one row is
`Shearwater Cloud Desktop`. So a file this writer produces is read back by the *other*
branch of that rule, the fraction one, and a written `0.67` comes back as `67`. Writing
whole percent instead would come back as `6700`: the round trip a writing document exists
to prevent, and one no conformance pair would catch, since the corpus never reads a written
file back (`CONTRIBUTING.md`, *the checks the corpus cannot make*).

*Rejected:* adding this writer to the generator table so it could write whole percent. The
table exists to record what a **third party's** files need read differently; a writer that
has to be in it to be read correctly by its own reader is a writer producing files nobody
else can read correctly, which is the opposite of the point.

**The recording's `mode` is written as `<divemode type>` on the first waypoint**, in UDDF's
spelling: `open_circuit` → `opencircuit`, `closed_circuit` → `closedcircuit`, `semi_closed`
→ `semiclosedcircuit`, `freedive` → **`apnoe`**. `divemodeType` spells a freedive twice,
`apnoe` and the `apnea` added beside it in 2017; `apnoe` is written because it is the older
of the two and every 3.2.x reader knows it, while `uddf-mapping.md` reads both. A **`gauge`**
recording is reported `dropped`: `divemodeType`'s five values do not include one, and
writing the nearest is the kind of guess §5.4 forbids.

`profile.duration` is not written anywhere: UDDF records no duration for a profile, and
§6.4 defines the member as the span of the samples, which a reader takes off them. A
document whose `duration` is not that span is reported.

`profile.ceiling` has no UDDF slot: the only per-waypoint element is `<decostop>`, whose
`@duration` is `use="required"`, and a ceiling sample says how deep the obligation was and
never how long the stop should last.

`profile.tts` and `profile.surface_gradient_factor` have no UDDF element at all — there is
no time-to-surface element in 3.2.1 and no surface gradient factor — so both are reported
`dropped`, which is what a member outside the carried set gets.

**`deco_model` is reported `dropped`, and that is a fact about UDDF.** The XSD makes
`<decomodel>` an `xs:all` of `<buehlmann>`, `<rgbm>` and `<vpm>` with none of the three
optional, and each of those types requires at least one `<tissue>` carrying a half-time and
its coefficients. A DiveJSON deco model carries a family, a name and a gradient-factor pair
and no tissue table, so there is no way to write one and stay valid against the schema this
writer's pairs are held to. Nothing is invented to satisfy a required element — this
document's own first rule, and §5.4's. Shearwater Cloud Desktop ships
`<decomodel><buehlmann>` with the pair alone, which is evidence the XSD is stricter than
practice and not a licence to match it: the XSD assertion is what holds element order right
across this writer, and exempting one element from it would cost more than the member is
worth. *Rejected:* writing Shearwater's shape and skipping the assertion for `<decomodel>`.

Events:

| DiveJSON event | UDDF |
| --- | --- |
| `deep_stop`, `safety_stop`, `bookmark` | `<setmarker>` carrying the type as its text |
| no `type`, with a `label` | `<setmarker>` carrying the label |
| any other `type`, with a `label` | `<setmarker>` carrying the **label**; the type is reported `dropped` |
| `gas_switch` with a `gas_number` | `<switchmix ref>` naming that cylinder's mix |

Four cases lose something, each reported:

- **A typed event with no label** is dropped rather than written as the word its type spells,
  which would come back as an event labelled `ppo2_high` — a label the document did not have,
  and the device's wording is what §6.6's `label` holds.
- **A named type carrying a label** keeps the type and loses the label. `<setmarker>` is one
  string with no type beside it, and the three named types are the only thing a round trip
  through it has to go on.
- **A type outside those three, carrying a label**, is the mirror of it: the label is written
  and the type is lost. That way round because the label is the half UDDF can carry back —
  `<setmarker>ppo2_high</setmarker>` would return as an unclassified event labelled
  `ppo2_high`, where `<setmarker>PO2 High</setmarker>` returns as the marker the diver saw.
  *Rejected:* dropping every such event, which would lose the whole alarm class in the one
  direction this writer exists to make less lossy.
- **A gas switch naming a cylinder this dive does not have** is dropped: `<switchmix ref>`
  is an `xs:IDREF` and there is nothing valid to point it at. Pointing it at another dive's
  mix would say the diver breathed a gas they did not carry.

`waypointType` allows one `<setmarker>` and one `<switchmix>`, so a **second** event of
either kind on one second is dropped and reported.

**Differs from the reference writer**: it joins simultaneous markers with `"; "` rather than
dropping the later one, which is right for a file a human is reading and wrong for one being
read back, where the join returns as a single event whose label is two labels.

## What is never written

The members `uddf-mapping.md` lists as slotless, from the writing side. Each is reported
once per record that carries it, and none of them has anywhere in UDDF to go:

| DiveJSON | why not |
| --- | --- |
| `courses` | `<divetrip>` is the nearest thing and a training course is not a trip; folding one in would make a reader show "PADI Open Water" as a holiday, beside the real trips already there |
| `certifications` | not written — see the note below |
| `gear_sets` | `<equipmentconfiguration>` describes how pieces are rigged together, which is not a named set of them |
| `gear_service_schedules`, `gear_service_records` | not written — see the note below |
| `species` | `<site><ecology>` is site-level flora and fauna where §6.11's species are per-dive sightings |
| a record's `created_at` | no slot on any of them |
| `gear` `rented`, `archived`, `archived_at`, `dive_count` | no slot |
| `diver.username` | `<owner id>` is an XML id and not a handle |
| a recording's `source_files`, `started_at` and its device's `firmware`, and every recording after the first | UDDF gives a dive one `<samples>`, and `equipmentPieceType` no firmware element — *Devices* above has each answer and why the device of a dropped recording is kept even so |
| `trips[].locations[].bbox` | `geographyType` carries a point, not a box |
| a record's `extensions` | producer-defined members (§5.5) |

An **empty** note — `notes: ""` — is not written either: `<para></para>` and no `<notes>` at
all are the same file to every reader, every XML reader here taking an empty element as
absent. It is a value UDDF has no spelling for rather than one a writer chose to drop, and
it is reported.

**Two of those rows are "not written" rather than "no slot", and it is worth being exact
about which.** `uddf-mapping.md`'s *Deliberately not mapped* table groups `courses`,
`certifications`, `gear_sets` and gear service under one reason, which is the right summary
for a **reader**: nothing in the corpus emits any of them, so there is nothing to read.
Going out, the schema does carry `<owner><education><certification>` and a pair of
`<serviceinterval>`/`<nextservicedate>` elements on every equipment piece — neither of which
is what DiveJSON holds (§6.13 keeps a schedule *and* the services performed against it,
where UDDF's two elements are a plan on one piece with no history behind it), and neither of
which any file on record uses. A writer that filled them would be the first to, with no
reader to check the guess against. Mapping either is a change to what the format says it
carries, so it starts in the specification.

## Known consumer artefacts

What a consumer will make of a file written this way, as distinct from what a correct reader
makes of it. Recording them here saves the next reader the round trip; they are the mirror
of `uddf-mapping.md`'s section of the same name.

- **Subsurface discards every depth-less waypoint on import.** A file written here puts a
  reading on its own second, so a temperature or pressure sampled between two depth samples
  is a depth-less waypoint and Subsurface will not keep it. The depth channel arrives whole.
- **divelogs.de reads a missing depth as zero**, so the same waypoints arrive as a profile
  that saws between the real depth and the surface.
- **Subsurface reads no trip element at all**, and the only equipment it reads is the dive
  computer's model. Trips and gear **are** written here — the loss is on the import side,
  in `uddf.xslt`, and not in the encoding.

A writer for a specific consumer rather than for interchange should snap its channels onto
the depth axis the way the reference writer does, and accept the loss that costs. That is a
choice about the consumer, not about the format, which is why it is not this document's
default.
