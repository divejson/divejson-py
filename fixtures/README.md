# Conformance fixtures

`divejson validate` must pass every document in `valid/` and reject every document in
`invalid/`. CI enforces exactly that, so a change to the spec or schema that alters what
conforms shows up here as a failing fixture — update the three together
([CONTRIBUTING.md](../CONTRIBUTING.md)).

Beside those two sit the **pair** corpora, which are for *converters* rather than
validators: each input is paired with the document a correct reader produces from it.
`uddf/` was the first; `ssrf/`, `fit/`, `suunto_json/` and `suunto_xml/` sit beside it. The
pairs under `write/` run the other way — a document, and the file a correct writer produces
from it — and `write/uddf/` is the first of those.

An implementation walks all of it with one command — `divejson conform fixtures/ --strict`
([CONTRIBUTING.md](../CONTRIBUTING.md)) — and the directory layout is that command's
contract:

| path | what |
| --- | --- |
| `valid/` | documents that must validate |
| `invalid/` | documents that must not, one defect each |
| `<format>/` | reader pairs, the directory named for the source format's registry id: every file whose suffix is not `.divejson` is an input, and `<stem>.divejson` beside it is what a reader must produce from it |
| `write/<format>/` | writer pairs, the other direction: `<name>.divejson` in, `<name>.<ext>` beside it the file a writer must produce, compared the way that format's writing document says two of its files are compared when one was produced just now |

A pair directory for a format the implementation running the suite does not read is a
corpus-shape error rather than a failure, because it means cases that never ran. That is
what makes a fixture dropped into this tree impossible to ignore.

## valid/

| file | what it covers |
| --- | --- |
| `minimal.divejson` | The smallest conforming document: `format`, `version`, `exported_at` — no diver (a source that records nothing about its owner omits the member), no collections (absent ≡ empty). |
| `demo-logbook.divejson` | A real export of the reference writer's demo account (all names are seeded demo data; pulled 2026-09-02, courses-era writer): 8 dives, one carrying a recording with its device, its stored file and a full sampled profile, which is what a dive imported from a computer file looks like — sites, trips, an empty `courses` collection, gear with a service history, certifications, and producer extensions carrying application-specific values. Its device is written by hand: the export predates recordings, and the file it names was read by the `suunto_json` parser, so the device is what that reader takes from such a file. Regenerate from a fresh export when the writer changes. |
| `two-computers.divejson` | Hand-built coverage of §6.4a, and of the half of it no converter can reach: a dive with **two** recordings that both carry a profile — `ssrf/two-computers.ssrf` reaches that much from one file — where the first holds **two** `source_files`, one recording the app exported twice, as JSON beside FIT, each file read by a different parser. Its second recording carries its own `started_at`, 31.33 s after the dive's, so its samples sit on their own axis. Its two devices differ in every way §6.4b allows two devices to differ, and the second computer's deepest sample is deeper than the dive's logged `max_depth`, which is the ordinary disagreement between two devices rather than a defect. It has no pair: §6.7's stored-file records are the application's and no reader produces one, which is the point of it. It is also where the two halves of §6.4c the other hand-built file cannot reach live: its first recording runs an RGBM model named in words with a **negative** `conservatism` and no gradient factors, the shape a Suunto states, while its second is in **`gauge`** mode and carries no `deco_model` at all — a computer run as a bottom timer runs no decompression model, and saying so is not the same as saying nothing. Between the two files every member of §6.4c's table and two of §6.4a's five `mode` values are exercised by a document no converter wrote; `freedive` comes from `suunto_xml/freedive.xml`, and `closed_circuit` and `semi_closed` wait for a file, no source in hand stating either. |
| `technical-dive.divejson` | Hand-built coverage of what the demo corpus lacks: trimix, a sidemount pair (two cylinders, one blend, `usage: "parallel"`), staged deco cylinders, gas-switch events, a ceiling channel with a gap, per-cylinder pressure channels, a `+12:45` UTC offset **and** an offset-less local `started_at` (§5.2's third state), a dive with no recorded duration, a common-name-only species, an antimeridian-crossing bounding box, an `agency: "other"` certification with `front_file`/`back_file`, a dive-count service interval, two courses — a completed `"other"`-agency course linked from a dive and its certification, and an unreferenced `"planned"` one with no dates — a lowercase-`z`, one-digit-fraction `created_at` (both spellings the grammar allows and naive parsers reject), and a `bookmark` 85 s **past** `profile.duration` — the surface-marker case §6.4 blesses, which the pre-2026-09-04 validator rejected, and which also carries a `label` beside a `type`, the pairing §6.6 asks for on any value defined after 1.0. Its recording's device carries **all six** of §6.4b's members, hand-built like the rest of the file. It is also the **decompression** fixture: `mode: "open_circuit"` and a Bühlmann `deco_model` with a name and a 30/70 gradient-factor pair, and all six of §6.4's readout channels sampled on the depth channel's own seconds — an `ndl` that runs from the 99-minute display cap down to zero and back, a zero `ndl` at the same second as a `ceiling` (the coexistence §6.4 blesses), a `tts` present only while there is an obligation, a `ppo2` that climbs through three gases, a `cns` whose ends are the dive's own `cns_start` and `cns_end`, and a `surface_gradient_factor` above 100 with the `gradient_factor` beside it far below. Its profile's `extensions` carries a tissue-loading array, which is what §6.4c says tissue state rides until a reader can compute from it. |

## invalid/

One defect per file. Every rule the schema alone cannot express (spec §3) has a fixture
here; several schema-level defects are included so the validator's schema pass and the
format's structural guarantees (Position objects, the null ban) are exercised too.

**Three of §3's rules now read *per recording*, and each keeps its defect off the first
one** — `recording-without-content` puts the empty recording second, `non-increasing-samples`
puts the bad channel in a second recording behind a device-only first, and
`duplicate-file-uuid-across-recordings` spans two. A validator that walked only the primary
recording would accept all three, which is exactly the implementation §6.4a's ordering rule
invites and the only thing that catches it is a fixture that puts the defect where such a
validator does not look.

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
| `duplicate-file-uuid-across-recordings.divejson` | two recordings of one dive carry the same stored-file record | §5.3 |
| `recording-without-content.divejson` | a recording carrying none of `device`, `profile`, `source_files` | §3, §6.4a |
| `dive-profile-outside-recording.divejson` | a `profile` on the dive rather than inside a recording — the retired shape | §6.2, §6.4a |
| `device-empty-member.divejson` | a device with an empty `serial` | §6.4b |
| `naive-exported-at.divejson` | `exported_at` without a UTC offset | §5.2 |
| `trailing-newline-datetime.divejson` | a date-time with a trailing newline inside the string | §5.2 |
| `position-incomplete.divejson` | a Position missing `longitude` | §6 |
| `oxygen-helium-sum.divejson` | `oxygen + helium > 100` on a cylinder | §6.3 |
| `pressure-order.divejson` | `end_pressure > start_pressure` on a cylinder | §6.3 |
| `avg-depth-exceeds-max.divejson` | `avg_depth > max_depth` on a dive | §6.2 |
| `profile-duration-short.divejson` | `profile.duration` below the latest sample | §6.4 |
| `channel-length-mismatch.divejson` | a series' `times` and `values` differ in length | §6.5 |
| `non-increasing-samples.divejson` | a series' `times` is not strictly increasing, **in a dive's second recording** | §6.5, §3 rule 3 |
| `event-without-type-or-label.divejson` | an event with neither a `type` nor a `label` | §6.6 |
| `deco-model-gf-order.divejson` | `gf_low > gf_high` on a recording's deco model | §3 rule 7, §6.4c |
| `trip-dates-reversed.divejson` | `ends_on` before `starts_on` on a trip | §6.8 |
| `course-dates-reversed.divejson` | `ends_on` before `starts_on` on a course | §6.17 |
| `bbox-missing-corner.divejson` | a Bounding Box missing one corner | §6.9 |
| `bbox-south-exceeds-north.divejson` | `south > north` in a Bounding Box | §6.9 |
| `bbox-without-position.divejson` | a `bbox` on a location with no `position` | §6.9 |
| `species-no-identity.divejson` | a species with no AphiaID and no name at all | §6.11 |
| `agency-other-missing.divejson` | `agency: "other"` without `agency_other` | §6.16 |

## What a pair is, in every one of these directories

`<name>.<ext>` is the input and `<name>.divejson` beside it is what `divejson convert` must
produce from it. The pair is the unit — a conformance case for converters the way the two
directories above are one for validators, and one any port in another language can be run
against. No implementation lives here; the first to read these formats,
[divejson-py](https://github.com/divejson/divejson-py), is measured against these
directories like any that follows it.

**The comparison ignores exactly two members, `exported_at` and `generator`.** Both are
facts about the *run* rather than about the input: the first is the moment of conversion
and the second is whatever software did it, which for a port is not this one. Everything
else is compared, `extensions` included — the provenance block under the `divejson`
producer key records the source's own version and generator, which are properties of the
input like any other.

**Every text input is hand-built**, reduced from a real export of that writer rather than
being one. That is the licensing-safe answer, and it is why each file is small enough to
read: a fixture whose point is one element is easier to check when it is not surrounded by
four hundred waypoints. The corpus mixes the two habits the directories above already have
— `demo-logbook.divejson` is a real export while `minimal.divejson` and
`technical-dive.divejson` are hand-built. Naming a file after a writer says which output it
is modelled on, not that the bytes came from it; the readings in a reduction are real and
unaltered, so its answers are the answers the whole file gives.

**A binary format is the exception**, and it has to be: a `.fit` file cannot be reduced by
hand, and a hand-built one would prove that an encoder and a decoder agree rather than that
a device's file reads. Such an input is committed **as recorded**, whole, from a dive whose
position the recorder is content to publish. `fit/` is the only directory this applies to
today.

**A writer pair is the same unit with its halves swapped**, and the two rules above are
where it differs. `<name>.divejson` is the input and `<name>.<ext>` is what a correct writer
produces from it, so what is compared is two files of *that* format rather than two DiveJSON
documents, and each written format says how ([`docs/writing.md`](../docs/writing.md)). And
its input is a document rather than a reduction of somebody's export, so neither the
hand-built rule nor the binary exception reaches it: both of `write/uddf/`'s inputs are
documents this tree already carries, which is what makes the answers checkable by eye.

`write/uddf/opendiving.divejson` is the one place that stops being literally true, and only
on purpose. It is `uddf/opendiving.divejson` with a richer **device** on its recording and a
`serial` on its `computer` gear item, because what a UDDF input states about a computer is a
brand and a name and rarely more — that is all its own input states — and a writer pair
carrying only that would exercise none of what the writer does with a device. Its device
adds exactly three members to the reader pair's, and each buys one
element of the written file: `model` → `<model>`, `serial` → `<serialnumber>`, `dive_number`
→ the dive's `<internaldivenumber>`. Regenerate the input from any shorter list and the pair
stops matching.

**The gear item's `serial` is the fourth difference, and it is not a fourth element.** It is
the same `<serialnumber>` the device's already buys, given to both records because they
describe one computer — which is also what makes the fold in this pair fire on a serial
rather than on a name. Put it on the device alone and reading the written file back hands
the gear item a serial the document never had, which is exactly what a pair whose point is
losing nothing cannot afford. So the two documents are identical in every member but those
four: the device's three, and the gear item's serial.

Each directory's mapping rules — what each expectation below follows from — are in that
format's document under [`docs/`](../docs).

## uddf/

[`docs/uddf-mapping.md`](../docs/uddf-mapping.md).

| file | modelled on | what it covers |
| --- | --- | --- |
| `subsurface.uddf` | Subsurface 6.0.x | The known answer for the channel arithmetic: eight of the 431 depth samples and two of the 29 temperatures of a real Subsurface export of the demo account, whose full 431 and 29 equal `valid/demo-logbook.divejson`'s after scaling, sample for sample. It also fails the 3.2.2 XSD, in the kinds the real export does — `mix(21/0)` ids carrying parentheses, a site id beginning with a digit, `<link ref>`s pointing at both, dive ids duplicating their own `<repetitiongroup>`'s, empty `<latitude/>`/`<longitude/>` and an empty `<divetrip/>` — plus the site id `" ff47210"`, whose leading space passes the schema and breaks a reference lookup that does not strip. Its second dive carries the six-point profile Subsurface fabricates for a dive that has none, and its first has a `profile.duration` of 4300 over a logged `duration` of 4010. |
| `divelogs.uddf` | divelogs.de | **No namespace at all**, so it fails the XSD at the root. `<tankdata>` sits before `<informationbeforedive>`, which no version of `diveType` permits — the converter takes children by name for the reason above it, that 3.2.1 and 3.2.2 disagree about the order and the namespace does not say which you have. Also `0.000000` coordinates on every site, a site with an empty `<name/>` and the dive whose link to it therefore resolves to nothing, zero-depth waypoints on every other sample, and its `<inifinity/>` misspelling of `<infinity>`. |
| `shearwater-cloud.uddf` | Shearwater Cloud Desktop 2.12.10 | The generator table's only pair, and the export the rule in [`docs/uddf-mapping.md`](../docs/uddf-mapping.md) was written against: a `<datetime>` of `2026-09-08T15:18:10Z` read as a wall clock with **no offset**, reported `resolved`, because `<generator><name>` is the exact string `Shearwater Cloud Desktop` with the manufacturer id `Shearwater_Research_Inc` beside it — while the `Z` on `<generator><datetime>` is left alone. Its `/uddf/@version` is `3.2.3`. Its `<divecomputer>` is the full hardware block `opendiving.uddf`'s is not — a `<model>`, a `<serialnumber>` and a `<notes>` holding the firmware and log versions UDDF has no element for, which reach the gear item's notes and never the device's `firmware` — under an `id` of `Perdix 3_D9772626`, which is neither a `<kind>-<uuid>` nor an `NCName`, so both records take a derived uuid rather than one read back off it. Also four `<link>`s under `<informationbeforedive>` where a site is expected, none of which is one: the file's own calculated profile and a string nothing defines are dropped and reported, an empty `ref` passes without a finding, and the fourth — `ref="zhl16c"` — **resolves**, to the `<decomodel><buehlmann>` that gives this recording its `deco_model` of `buhlmann` with a 50/85 gradient-factor pair. It was the "not a site this converter carries" note until §6.4c existed. It is the generator table's other rule too: those gradient factors are whole percent because Shearwater Cloud Desktop wrote them, where the documentation says fraction. The recording's `mode` is `open_circuit` off the first waypoint's `<divemode>`, which the last four waypoints stop stating without that being a change, and the profile carries an `ndl` sitting on `5940` — the Perdix's 99-minute display maximum, a reading and not an absence — beside a `ppo2` read as bar rather than the documented Pascal and a three-sample `gradient_factor`. Its two `<tankdata>` blocks state both pressures as `0`, so each cylinder loses the start to §6.3's absent-marker and keeps the zero end pressure, and its `<switchmix>` at second 0 names a mix no `<tankdata>` links, kept as a gas switch that cannot say which cylinder it was to. |
| `shearwater-cloud-cns.uddf` | Shearwater Cloud Desktop 2.12.10 | The **second** dive of the same day off the same Perdix 3, reduced the same way — the only file in hand with a per-sample `<cns>`, and the one that makes the `cns` channel a checked mapping rather than a written one. Sixteen of its 295 waypoints, chosen for what they carry: the CNS series climbing 1 % to 8 % (`10` to `80` in the channel's tenths), the `<nodecotime>` counting down from the display cap to 3 720 s and back, a `<gradientfactor>` spread of `0`, `1` and `4` through `17` where the first pair has only `0` and `1`, and a `<calculatedpo2>` from `0.34` to `0.96`. Six `<tankdata>` blocks with both pressures zero, where the other pair has two, so six cylinders arrive carrying an end pressure each and no start. Its dive links the same `<decomodel>` and carries the same 50/85 pair, which is the point: the model is the diver's setting and does not move between dives, while everything sampled does. |
| `mix-only-cylinder.uddf` | hand-built | The shape §6.3 blesses and no real file on disk carried: a `<tankdata>` with a gas link, both pressures and a real drop between them, and **no `<tankvolume>`** — so a cylinder's size is the only input a gas-consumption figure lacks. Its second dive has no `<tankdata>` at all, which is what APD DiveSight exports. |
| `opendiving.uddf` | this format's reference writer | The richest mapping, and the round trip that matters most: `dive-<uuid>` style ids coming back as those uuids, trips with `<trippart>` dates and places, a kit list under `<equipment>` with per-dive `<equipmentused>` links, two cylinders on two gases, `<tankpressure>` channels, a `<switchmix>` gas switch and a `<setmarker>`. It is also a `<divecomputer>` read two ways at its thinnest: its `<name>Ocean</name>` becomes both the gear item's `name` and the device's, which is the shape the format produces far more often than the full hardware block `shearwater-cloud.uddf` carries — this element states no `<model>`, `<serialnumber>` or `<internaldivenumber>` at all. |
| `legacy-writer.uddf` | 2.x writers | Four habits in one dive: an uppercase `<UDDF>` root with uppercase element *and attribute* names, `<o2>34</o2>` as whole percent, `<tankvolume>12</tankvolume>` as litres where UDDF specifies cubic metres, and the `<datetime>2002-06-18T</datetime>` a midnight dive gets from an unguarded string concatenation. Its waypoints are out of order, two of them round to the same second, and one has no `<divetime>` at all. |

## ssrf/

[`docs/ssrf-mapping.md`](../docs/ssrf-mapping.md).

| file | modelled on | what it covers |
| --- | --- | --- |
| `subsurface.ssrf` | Subsurface 6.0.x | The same two dives of the same logbook as `uddf/subsurface.uddf`, reduced from the save file the way that file is reduced from the UDDF export: the first dive keeps eight of its 431 depth samples and two of its 29 temperatures, sample for sample the same eight and two, which is what makes the two documents comparable. The second keeps all six of the profile Subsurface fabricates for a dive that has none, carried through as recorded — the UDDF fixture reduced that one to three. Also the site id `" ff47210"` with its leading space, a cylinder with a gas and one without, and the two attributes the reader reads, refuses and reports — `@model` was a third until it became a recording's device, which both dives here now carry. |
| `two-computers.ssrf` | Subsurface 6.0.x | Two `<divecomputer>` elements on one dive **both carrying samples**, which no other input here has: two recordings each with a profile, where `refusals.ssrf`'s two-computer dive reaches only the device-only shape. The second element states its own `@date` and `@time`, 32 s after the dive's, so that recording carries a `started_at` of its own and its samples sit on their own axis (§6.5); the dive's `max_depth`, `avg_depth` and `bottom_temperature` are the **first** element's throughout, and the second's deeper `@max`, its own `@mean` and its warmer `<temperature>` are dropped and reported. It is also the only input here with `<extradata>`: the first computer's `Serial` and `FW Version` children become that recording's device serial and firmware, the two members no attribute of this format reaches, while the second computer carries a `@model` and nothing else. The same dive as `fit/suunto-ocean-2026.fit` and `suunto_json/suunto-ocean-2026.json`, which are that first computer's own exports of it. |
| `trip-grouping.ssrf` | hand-built | A `<trip>` wrapping two dives with a third beside it, so the walk-through and the dropped grouping are both visible, and the positional identities run across the flattening. |
| `refusals.ssrf` | hand-built | Everything this format's reader refuses: a `'150.6 ft'` maximum depth and a `'67.1 F'` sample temperature, a nameless site and the dive whose reference to it therefore resolves to nothing, a reference to a site nothing defines, a dive with no date, a `@time` with no seconds, two `<divecomputer>` elements on one dive, which since neither carries samples become two device-only recordings, the second stating a deeper maximum, its own mean and a warmer temperature than the first so that the first-element rule for a dive's three scalars is a rule the pair can hold a reader to — a sample with no time, two samples on one second, a zero start pressure beside a zero end pressure, a pressure past 350 bar, and a mix summing past 100 %. |

## fit/

[`docs/fit-mapping.md`](../docs/fit-mapping.md). Every input here is binary, so every one is
the exception above: **committed whole, exactly as the device recorded them**, rather than
reduced.

| file | recorded on | what it covers |
| --- | --- | --- |
| `suunto-ocean.fit` | Suunto Ocean, product 62 | **The recording committed whole.** The known answer, and the developer-field trap: a `session` carrying `max_depth` 45.91 natively beside a developer `float32` of 45.90999984741211. 4,295 `record`s of which 431 carry a depth and 4,294 a temperature, on their own axes; two enabled gases at 21 % and 54 %; 28 satellite fixes, all of them after the deepest sample, so the dive has an exit position and no entry; a `+02:00` recovered from `activity`; and `start_cns` as the one mapped member its session leaves empty. |
| `suunto-d5.fit` | Suunto D5, product 39 | **The recording committed whole.** The same trap on a different product six years earlier — 32.41 against a developer 32.40999984741211 — and the small end of the format: 200 `record`s carrying a depth and a temperature each, one gas, and no position at all. **No tank-telemetry pair exists**, here or beside it: no FIT input carries a `tank_summary` or a `tank_update`, so every pressure-pod row of `docs/fit-mapping.md` is marked untested there and waits on a file that has one. The same goes for a `dive_summary`, a `water_type`, a `software_version`, a disabled gas and any `event` but `timer` — the list of what a Garmin export is expected to bring. |
| `suunto-ocean-2026.fit` | Suunto Ocean, product 62 | **The recording committed whole**, and the corpus's one dive read three ways: the same dive as `suunto_json/suunto-ocean-2026.json`, which is this computer's own app export of it, and as the first `<divecomputer>` of `ssrf/two-computers.ssrf`, which is Subsurface's download of it beside a second computer's. The developer-field trap on a shallower dive — a `session` carrying `max_depth` 19.04 natively beside a developer `float32` of 19.040000915527344, and `dive_number_in_series` as a developer duplicate of the native `dive_number` 3, which is the counter §6.4b puts on the device. 3,467 `record`s of which 361 carry a depth and 3,464 a temperature, on their own axes; one enabled gas at 33 %; 25 satellite fixes, none before the deepest sample, so the dive has an exit position and no entry; a `+03:00` recovered from `activity`; and `start_cns` again the one mapped member its session leaves empty, beside an `end_cns` of 9 and an `o2_toxicity` of 22. |

## suunto_json/

[`docs/suunto-json-mapping.md`](../docs/suunto-json-mapping.md).

| file | modelled on | what it covers |
| --- | --- | --- |
| `suunto-ocean.json` | Suunto Ocean, 2026 | The known answer, and the shape with no gas block: two cylinders from two gas switches where only one slot ever transmitted, `start_pressure` 211.625 bar and `end_pressure` 127.15625 bar against a pressure channel whose last value, 1273 tenths of a bar, is a reading the transmitter sent after the dive had ended, a `.510` sub-second fraction preserved on `started_at`, three entries merging onto second 0, a zero ceiling beside a real one, an entry position in degrees off `DiveRouteOrigin` and an exit in radians off a satellite fix — the same exit `fit/suunto-ocean.divejson` carries for this dive, from a different file through a different reader. |
| `suunto-ocean-2026.json` | Suunto Ocean, 2026 | The app's export of the dive `fit/suunto-ocean-2026.fit` is the computer's own, so the two carry different halves of one device: a `Device.Name` of `Porvoo` — the owner's name for the watch, which is what §6.4b's `name` holds and what this format has instead of a product name — beside the serial and the firmware the FIT file states nowhere. Also the Ocean shape with **no mixture anywhere**: five cylinder slots on every sample with only slot 0 transmitting, and a `GasSwitch` to gas 0, so the dive's one cylinder carries pressures, a gas number and no gas — absent means not recorded, never air. The `DiveTime` bound leaves `end_pressure` at 44.03125 bar while the pressure channel keeps the 45.0 bar the transmitter sent after that bound had passed; the entry position comes in degrees off `DiveRouteOrigin` and the exit in radians off a satellite fix; and `DiveTime`'s 3 051 s beats the longer `Duration` beside it to the dive's own `duration`, the in-water figure winning wherever the export states one. It is also the pair that holds the readout rules: five samples carry the `NoDecTime`/`TimeToSurface`/`RtGradientFactors` trio, and what comes out is an `ndl` of all five, a `tts` of **two** — the three zeros are the Ocean's absent-marker, not a time to surface of nothing from 14 and 19 m — a `gradient_factor` of **one**, the four `gf99: -100` samples being where no compartment leads, and a `surface_gradient_factor` of all five including its zeros. Four rules, one file. |
| `purged-regulator.json` | Suunto Ocean, 2026 | The `DiveTime` bound where it is worth two thirds of a tank: an `end_pressure` of 53.34375 bar beside a pressure channel whose last value is 1 tenth of a bar, read six minutes after the diver got out. |
| `suunto-d5.json` | Suunto D5, 2025 | The header's own gas block, in SI: two gases at 21 % and 49 % from cubic metres, Pascal and 0-1 fractions, the second carried and never transmitted from; telemetry on slot 1 resolving to cylinder 0; a switch to gas 2 resolving to cylinder 1; and the oxygen clock, `CNS` as a fraction beside `OTU` as itself. |
| `header-only.json` | Suunto D5, 2021 | The shape with no gas anywhere: a header and no samples at all, so no cylinders and no profile, and neither reported — the source recorded none rather than a reader failing to carry them. The header does name a device, so the dive carries a **device-only recording** (§6.4a) — the case that says a computer worn is a fact about the dive even when it sampled nothing. `ssrf/refusals.ssrf` reaches the same shape from the other direction, with two computers on one dive and samples from neither. Constructed rather than reduced; no real export of this shape is in hand. |
| `d5-stop-alarms.json` | Suunto D5, 2021 | The first of **five pairs that exist for §6.6's vocabulary**: a seed value is only in the spec because a real file names the alarm it stands for, and these are those files, each reduced to the samples carrying an alert plus a spread of depths, pressures and fixes. This one carries `Ascent Speed`, `Mandatory Safety Stop`, `Safety Stop Broken` and `Mandatory Safety Stop Broken` — the last two different wordings of one occurrence, which is why `safety_stop_violation` is one value rather than two — and a `Notify` of `Deco`, the moment the dive became a decompression dive, which maps to `ndl_reached` with **no label**: a `Notify` names the computer's state and not what the diver was shown. Its `Header.Diving` gives the recording an RGBM model named `Suunto Fused2 RGBM`, which is the spelling 16 exports in hand use and not the one `suunto-d5.json` carries, and a `Conservatism` of `0` — the P0 setting, a reading and not an absence. |
| `d5-deep-stop-broken.json` | Suunto D5, 2025 | `Deep Stop Broken` and `Violated Deep Stop`, the two wordings behind `deep_stop_violation`, and the `Notify` of `Safety Stop Broken` that is the second of the two `Notify` values this reader carries. Its `Conservatism` is `-1`, P−1, which is the fixture that says §6.4c puts no floor on the member. |
| `d5-deco-max-depth.json` | Suunto D5, 2025 | A real decompression dive to 46.29 m, and the file that settles what a `NoDecTime` of zero is: it carries all five of the export's zeros, at 42.61 to 44.52 m, with a `TimeToSurface` of 256 to 268 s beside them and the first ceiling ten seconds after the last — a reading, not an absence, and the reason the reduction keeps them rather than a spread of depths. Also `Max.Depth` for `depth_alarm` and `Ceiling Broken` for `ceiling_violation`. |
| `ocean-deco-ppo2.json` | Suunto Ocean, 2026 | `PO2 High` for `ppo2_high` and `NoDecoTime` for `ndl_reached`, on a 45.5 m decompression dive. It is the only pair carrying a `NoDecTime` of **−1**, the Ocean's absent-marker, which the channel's own floor is what drops; and the only one whose `gradient_factor` runs into three figures — `398` at 7.62 m and `192` at 5.70 m, where the full export reaches 12 575 — which the mapping document carries as recorded and does not explain, the member having no upper bound precisely so a reader cannot quietly decide one. It is also the `gtSurface` pair: this firmware, 2.40.56, spells the surface gradient factor with a `t`, where the 2.51.28 export `suunto-ocean-2026.json` came from spells it `gfSurface`, and both read into the same channel. |
| `ocean-tank-pressure.json` | Suunto Ocean, 2026 | `Tank Pressure` for `pressure_low`, the one alert no other file in hand carries, on a dive with a transmitter. |
| `not-a-dive.json` | Suunto Ocean, 2026 | An activity that is not a dive at all, skipped and reported, producing a document with no dives. Constructed: every real export in hand is a dive, which is exactly why this rule needs a pair. A freedive is **not** this case — §6.4a's `mode` says which kind of dive one is, and `suunto_xml/freedive.xml` carries it. |

## suunto_xml/

[`docs/suunto-xml-mapping.md`](../docs/suunto-xml-mapping.md). One dive per document, so a
whole logbook is a directory of these and reaches a converter as an archive. The three
reductions keep every element a reader reads or refuses, with its recorded value unaltered,
and drop the bulk elements nothing reads: `<SampleBlob>`, the four `TissuePressures*` arrays
and their four blobs, most of the samples, and all but two of the `<Mark>`s. The two
constructed files are shapes no real export in hand has, which is exactly why they need
pairs — the branches they exercise would otherwise be reachable only by reading an
implementation.

| file | modelled on | what it covers |
| --- | --- | --- |
| `suunto-d5.xml` | Suunto D5, 2021 | The known answer: `max_depth` 32.41 and `started_at` `2021-04-06T11:16:42.6`, the fraction preserved and no offset supplied. **The same dive as `fit/suunto-d5.fit`**, recorded once and exported twice, which is what lets the two be read against each other through entirely different unit paths: on every second both documents sample, their depths are equal to the centimetre. They are not one grid, though — this reduction's last two samples, at 1 991 s and 2 001 s, have no counterpart in the FIT recording, whose own last sample is at 1 992 s. Also the untransmitted cylinder whose pressures are both the zero absent-marker, a single-gas switch at second 0, and the `<Marks>` block producing nothing. |
| `nitrox-deco.xml` | Suunto D5, 2025 | Two cylinders and three elements that have to agree: a 21 % back gas with the pod on it, a 52 % deco bottle that never transmitted and so carries no pressures at all, a pressure channel labelled from `<TransmitterId>`, markers at 0 and 1 592 s taken from the `<DiveMixture>` each sits inside, and a real ceiling channel. |
| `freedive.xml` | Suunto D5, 2023 | `<Mode>3</Mode>`: a **freedive**, and a dive. It converted to a conforming logbook with no dives at all until §6.4a gained `mode`; it now produces one dive whose recording says `freedive`, with no cylinder, no gas and no `deco_model` — a computer in freedive mode runs no decompression model, so its `<PersonalMode>` is the watch's setting rather than this dive's and is not carried. This is also the only pair where the same-second collision fires on a real file: five samples, three seconds, because a 1 s interval meets a `<Time>` that is not quite an integer — the collision every one of the corpus's 37 is, and unreachable while the reader stopped at `<Mode>` before it read a sample. |
| `refusals.xml` | constructed | Everything this format's reader refuses that fits on one dive: a `<StartTime>` with no seconds, an average depth deeper than the maximum, a millibar surface pressure, a ppO₂ limit of 3.2 bar, a mix summing to 110 %, a gas change at -30 s, text in a `<Depth>`, a sample with a nil `<Time>`, two samples on one second, a tank reading past 350 bar, the dive-conditions block, and **two** cylinders claiming the transmitter. |
| `unlabelled-pressure.xml` | constructed | Tank readings no cylinder claims, arriving as a cylinder of their own with nothing but the channel; beside them a zero `<MaxDepth>`, `<AvgDepth>` and `<Duration>`, a nil `<Mode>`, and a `<Note>` with whitespace around it. |

## write/uddf/

[`docs/uddf-writing.md`](../docs/uddf-writing.md), and the corpus's first writer pairs.
Compared as canonical XML with `<generator>` ignored, which is what makes them stable across
releases: everything else in a written file is a function of the document, `<datetime>`
included — it is the document's own `exported_at` and never the clock.

Both inputs are documents this tree already carries, and the pair is the two of them
together — the point of each row below is which half of `uddf-writing.md` it reaches.

| file | written from | what it covers |
| --- | --- | --- |
| `opendiving.divejson` | `uddf/opendiving.divejson`, plus a device | The round trip that matters most, and the pair that exercises almost none of the report: this document is itself the *reading* of a UDDF export, so there is nothing in it UDDF cannot hold, and the only finding is the `extensions` exclusion every written file carries. `dive-<uuid>` ids that come back as those uuids, a trip as a `<trippart>` with its dates and its place, a kit list under `<equipment>` with per-dive `<equipmentused>` links, two cylinders on two gases with their pressure channels, a `<switchmix>` gas switch and a `<setmarker>`. And the **fold**: its dive lists that `computer` gear item, which satisfies the predicate's link leg, and its recording's device and the gear item carry the same serial, so the serial leg fires and the two become one `<divecomputer>` element carrying both halves, with the device's counter on the dive as `<internaldivenumber>`. It is the corpus's only pair that reaches the serial leg. **No pair reaches the link leg refusing** — a `computer` gear item the document carries and one of its own dives does not link — so that branch has no pair either way, and [`docs/uddf-writing.md`](../docs/uddf-writing.md) writes it down rather than leaving it to the first writer to meet one. |
| `technical-dive.divejson` | `valid/technical-dive.divejson` | Everything the first one cannot reach, being hand-built to hold what no UDDF export carries. The `dropped` half of the report: `courses`, `certifications`, `gear_sets`, gear service and `species`, which UDDF has no slot for; `role` and `usage` on a sidemount pair and its staged deco cylinders; a ceiling channel; a location's bounding box; the gas numbering UDDF cannot record; `shears` landing in `<variouspieces>` and reading back as `other`; a trip location with coordinates and no name, which loses the coordinates rather than borrowing the name; a `bookmark` carrying a label, which keeps its type and loses the label; an event with a label and no type, which goes out as a `<setmarker>` carrying the label; the `deco_model`, `tts` and `surface_gradient_factor` its document now carries, each reported `dropped` — the first because UDDF's `<decomodel>` requires a tissue table this format has no member for, the other two because UDDF has no element at all; an empty note, which no UDDF file can spell; and a device's `firmware`, for which `equipmentPieceType` has no element. It is also the **other** half of the device fold: its gear list holds no computer, so the device matches nothing and gets a `<divecomputer>` of its own with a non-UUID id and a `<link>` from the dive — the one case where reading the written file back returns a gear item the input never had. That its device carries a `name` is what puts it inside that exception rather than beside it: a nameless device on an element of its own gets an empty `<name>`, which comes back as no gear item at all. The `absent` half is its second dive: no maximum depth, no duration and a cylinder with no start pressure, so `<greatestdepth>`, `<diveduration>` and `<tankpressurebegin>` are each written as the `0` a reader takes back off. |
