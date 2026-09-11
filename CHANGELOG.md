# Changelog

Notable changes to the DiveJSON tools for Python. The format's own version
(`major.minor`, declared in every document) is what readers and writers depend on, and it
is versioned in [the specification repository](https://github.com/divejson/divejson);
this file is about the package, whose version moves independently.

## Unreleased

- **Breaking: an event's `type` is OPTIONAL and `other` is gone.** §6.6 froze its
  vocabulary at five values with `type` REQUIRED, which made every alarm a computer records
  — a ceiling violation, a fast ascent, a ppO₂ alarm — an `other` carrying a label for the
  life of 1.x. An absent `type` is what `other` meant, so that is the spelling now, and a
  `label` is REQUIRED when the type is absent. Anything reading `event["type"]` has to reach
  for it as an optional member, and an event this package's readers used to emit as
  `{"type": "other", "label": …}` now arrives as `{"label": …}`.

- **A recording says what mode its computer ran in and what decompression model it ran, and
  a profile carries that model's readouts.** The members and their units are §6.4a, §6.4c and
  §6.4 in [the specification](https://github.com/divejson/divejson/blob/main/spec/divejson.md),
  and each format's own mapping document says which of them its files state; none of it is
  enumerated here, a list in two places being a list that disagrees with itself. What is
  worth knowing at this level is that every one of the five readers fills what its files
  carry — UDDF from `<divemode>` and the `<decomodel>` a dive links, FIT from
  `dive_settings`, the Suunto app's JSON from `Header.Diving`, the DM5 XML from `<Mode>` and
  `<PersonalMode>`, and `.ssrf` from nothing, no save file in hand carrying any of it.

- **A Suunto DM5 freedive is a dive.** `<Mode>3</Mode>` was skipped and reported because the
  format had no member for the kind of a dive; §6.4a's `mode` is that member, so the
  recording says `freedive` and the dive is carried. An archive of the reference export
  directory converts to **384** dives where it used to produce 342. `is_a_scuba_dive` is
  gone from that reader; the Suunto app JSON reader's `is_a_dive` stays, because a run and a
  dive really are the same shape there.

- **A Suunto alert carries a §6.6 type as well as the device's wording.** §6.6's vocabulary
  was seeded from that reader's own alert list — one value per distinct meaning, which is
  why two wordings of one occurrence share a value — so "Ceiling Broken" arrives classified
  *and* labelled. An alert outside the table is an event with no type and that wording as
  its label, and two more `Notify` values are carried where the reason for dropping them was
  that the format had nowhere to put them.

- **A negative decompression readout is dropped by the channel's own floor, once for every
  format.** §6.4 floors the new channels at zero, `series.Channel` reads that floor off the
  schema, and a device's absent-marker — a Suunto Ocean's `gf99: -100`, its `NoDecTime: -1`
  — is dropped from the channel and reported once per channel rather than once per sample.
  A value at a device's *display* cap is the opposite case and is carried through: 99
  minutes of no-decompression time is the number the diver read off their wrist.

- **The UDDF writer sends back what UDDF can hold.** The recording's mode goes out as
  `<divemode>` on the first waypoint and the readouts as `<nodecotime>`, `<calculatedpo2>`,
  `<cns>` and `<gradientfactor>` — the last as the documented *fraction*, because this
  writer is not a generator the reader's percent table names. A `gauge` recording,
  `deco_model`, `tts` and `surface_gradient_factor` are each reported `dropped`, the model
  because UDDF's `<decomodel>` requires a tissue table §6.4c has no member for and nothing is
  invented to satisfy a required element. An event whose type UDDF has no keyword for is
  written as its **label**, with the type reported; `docs/uddf-writing.md` has the four cases.

- The vendored `schema/`, `fixtures/` and `docs/` move to a `SPEC_REF` carrying all of the
  above: two new `invalid/` documents — a gradient-factor pair out of order, and an event
  with neither a type nor a label — beside one retired, `other` no longer being a type a
  document can fail on; six new reader pairs; and the mapping documents rewritten around
  what each format now carries.

## 0.5.0

- **Breaking: a dive's `profile` and `source_file` move into `recordings[]`.** A dive now
  carries an array of §6.4a Recordings — one device's record of one dive — and nothing is
  left behind on the dive itself: `dive["profile"]` is gone, and a reader wanting the
  profile a consumer would show takes `dive["recordings"][0]["profile"]`, the first entry
  being the primary. A recording carries at least one of `device`, `profile` and
  `source_files`, which `divejson validate` now checks per recording along with §3 rule 3's
  series integrity and the uniqueness of every stored file's uuid across a dive's
  recordings.

  **Every reader writes one, and a computer worn that sampled nothing is one too.** A
  source that names a device and records no samples yields a device-only recording, because
  a computer on the wrist is a fact about the dive. `.ssrf` is where this shows most: a dive
  may carry a `<divecomputer>` per computer the diver wore, each one a recording in file
  order — while an element that names no computer and kept no sample yields none at all, and
  the dive's own greatest depth, mean depth and water temperature come from the **first**
  element in file order with every later one reported.

- **A recording says what recorded it (§6.4b).** Every reader now fills a `device` — a
  brand, a model, a serial, a firmware version, the name the owner set on the hardware, and
  the device's own dive counter — from fields it previously read and dropped: `.ssrf`'s
  `<divecomputer @model>` and its `Serial` and `FW Version` `<extradata>`, UDDF's
  `<divecomputer>` and the dive's `<internaldivenumber>`, FIT's `file_id` and the
  `device_info` at `device_index` 0, `Device.SerialNumber` and `Header.Diving.NumberInSeries`
  in the Suunto app's JSON, and `<SerialNumber>` and `<DiveNumberInSerie>` in its DM5 XML.
  A device is data on a recording and never a gear item — except in UDDF, which has one
  element for both and where the reader keeps minting the kit item as well.

- **The UDDF writer writes the computer back, folding a kit item and a device into one
  `<divecomputer>`.** The predicate is in `docs/uddf-writing.md`: the dive must link the
  gear item, equal serials settle it either way, and otherwise a device's name-else-model
  has to equal the gear item's name with the brands not disagreeing. A device that folds
  into nothing takes an element of its own, `id="device-<n>"`, with a `<link>` from the dive
  — the one case where reading a written file back returns a gear item the document never
  had. `<serialnumber>` is written for every gear item that carries one and read back into
  §6.12's new `serial`, and the primary recording's counter goes out as the dive's
  `<internaldivenumber>`. UDDF holds one profile per dive, so the primary recording supplies
  the `<samples>` and every other recording is reported dropped with its device kept.

- **Shearwater Cloud Desktop's `Z` is read as no offset.** That application writes the wall
  clock the diver read off their wrist and suffixes it `Z`, so the instant the file appears
  to state is wrong by the diver's own offset. Under a generator table keyed on the exact
  `<generator><name>`, with the manufacturer id checked beside it, a dive's `<datetime>`
  loses that `Z` and the report carries a `resolved` finding. Every other generator is
  unchanged, and `<generator><datetime>` is left alone under this rule and every other.

- The vendored `schema/`, `fixtures/` and `docs/` move to a `SPEC_REF` carrying all of the
  above, along with `docs/writing.md` — the rules a converter follows whatever format it is
  writing, the mirror of `docs/converting.md` — and `docs/uddf-writing.md`, which the
  specification has now adopted.

## 0.4.0

- **Suunto's DM5 XML is the fifth format this package reads.** `divejson convert
  Dive_2021-04-06-1116.xml`, `sniff` answers `"suunto_xml"`, a zip of a whole export
  directory converts as one logbook, and `fixtures/suunto_xml/` carries the pairs any port
  is measured against. A file is claimed on its root element **and** its datacontract
  namespace, where the other XML readers here need only the root: `<uddf>` and `<divelog>`
  are each one format's, and `<dive>` is a name any dive-log format might reach for.

  **The same vendor's two exports disagree about three units**, and only a dive that exists
  in both makes it visible. CNS is whole percent here and a 0-1 fraction in the app's JSON;
  cylinder pressures are millibar against Pascal; and `<SurfacePressure>` is the one
  pressure in *this* file that is not millibar but Pascal — read at the cylinder scale a
  barometer at sea level reports a hundred metres of seawater. Every factor in
  `docs/suunto-xml-mapping.md` is stated beside the JSON reading of the same dive.

  **A `<Mode>3</Mode>` document is a freedive, and is skipped and reported.** DiveJSON has
  no member for the kind of a dive, so a converted freedive would arrive indistinguishable
  from a scuba dive with no gas and no algorithm — mislabelled by omission, in a logbook it
  shares with real scuba dives. It is the answer the app-JSON reader already gives an
  activity that is not a dive.

  **A cylinder with no transmitter writes both its pressures as `0`**, and the pair is the
  format's absent-marker rather than a tank breathed to nothing. §6.3 settles the start
  outright; the end follows it here, because this format never writes one of the two as
  zero on its own. And the sample stream's single unlabelled `<Pressure>` is tied to a
  cylinder by `<TransmitterId>` rather than by counting from the first, which is the same
  answer only until a transmitted deco bottle sits behind an untransmitted back gas.

- The vendored `schema/`, `fixtures/` and `docs/` move to `SPEC_REF` `22690a7`, picking up
  the mapping documents the specification adopted and the general rules it moved into
  `docs/converting.md`: where a fix belongs, the per-channel sample collision, and the
  preserved sub-second fraction.

- **UDDF is the first format this package writes, as well as reads.** `divejson convert
  --to uddf my-logbook.divejson` writes the UDDF beside it, `write_uddf(document)` is the
  same thing from Python, and the report is half the output going out as much as coming in:
  every member UDDF has no room for is named, with a path into the document rather than into
  a file. `registry.WRITTEN` carries `uddf`, and `divejson conform` gains the writer-pair
  comparison it could not run while nothing was registered — a `write/<format>/` pair is a
  document and the file writing it must produce, compared as canonical XML with
  `<generator>` ignored.

  **It is checked through the reader.** Reading a written file back returns the document it
  was written from, on every member `docs/uddf-mapping.md`'s element map carries, and
  `fixtures/write/uddf/` holds the pairs a port is measured against. Every generated document
  is also validated against the UDDF 3.2.2 XSD, now vendored at
  `tests/fixtures/uddf_3.2.2.xsd`, which is the only check that can see element order — and
  most of the types written here are an `xs:sequence`.

  **Nothing is invented to satisfy a required element.** UDDF makes `<greatestdepth>`,
  `<diveduration>` and `<tankpressurebegin>` mandatory where §6 does not, and each takes the
  zero this format's own reader takes back as "not recorded". `<geography>` makes a place
  name mandatory and has no such spelling, so a site with coordinates and no `location`
  loses the coordinates rather than having its name copied into them.
  `docs/uddf-writing.md` is the prose companion, and it marks every place this writer and
  the format's reference writer deliberately disagree.

- Corrects the stale reason behind the Suunto reader's cylinder extremes: the merged sample
  axis reproduces `211.625`, and what loses it is an unmerged one-entry-per-second axis
  (`211.26562`) or the axis's pressure channel in tenths (`211.6`). The rule the docstrings
  guard is unchanged.

## 0.3.0

- **The Suunto app's JSON is the fourth format this package reads.** `divejson convert
  my-dive.json`, `sniff` answers `"suunto_json"`, a zip of them converts as one logbook, and
  `fixtures/suunto_json/` carries the pairs any port is measured against. A file is claimed
  on its shape — an object whose one top-level member is `DeviceLog` — because JSON has no
  magic number and a `.json` suffix says nothing.

  **Its units are SI and §6's are not**: Pascal, cubic metres, Kelvin and a 0-1 gas fraction
  against bar, litres, tenths of a degree Celsius and whole percent, with a 0-1 `CNS`
  beside an `OTU` that needs no conversion at all. Nothing recorded is rounded — a
  transmitter's 21 162 500 Pa is `211.625` bar, to every digit it reported.

  **On the newest export a dive's cylinders are not in the header.** A 2026 Suunto Ocean
  writes no `Header.Diving` block at all; the cylinders are rebuilt from
  `Samples[].DiveEvents.GasSwitch` — the diver's own gas switches — rather than from which
  slots transmitted, because a two-tank dive read off the telemetry comes back as one
  cylinder carrying both pressures, which is the shape a gas-consumption figure is derived
  from.

  **A reading from after the dive ended is not the end pressure.** Bounding the cylinder's
  first and last readings on `Header.DiveTime` moves the answer on every one of the nineteen
  Ocean exports in hand, and on two of them it is the difference between a real end pressure
  and 0.14 bar — the tank once the regulator was purged on the boat. The profile's own
  pressure channel keeps the reading, because it is telemetry the device really recorded.

  This reader computes nothing and settles no scale, so it raises neither an `inferred` nor
  a `resolved` finding. `docs/suunto-json-mapping.md` carries the member map, the three
  header shapes, and what is deliberately unmapped and why.

- **FIT is the third format this package reads, and the first binary one.** `divejson
  convert my-dive.fit`, `sniff` answers `"fit"`, a zip of them converts as one logbook, and
  `fixtures/fit/` carries the pairs any port is measured against. `fitdecode` is a **core**
  dependency rather than an extra, so `pip install divejson` reads FIT with nothing else
  asked for.

  **Its magic is at offset 8**, not at the start of the file, which is why a zip's own first
  bytes can never decide the format of the files inside it — the archive walk sniffs each
  member on its own head, and an application should do the same.

  **A developer field may carry a profile field's name, and the native one wins.** Every
  Suunto session in this project's hand writes a `float32` `max_depth` of 45.90999984741211
  beside the native `uint32`'s exact 45.91; a reader taking the last match by name produces
  a document that validates perfectly and is wrong by a rounding error. Where the developer
  field is the *only* one, the value reads as not recorded and falls through — to Garmin's
  `dive_summary`, and then to the depth samples, where it becomes the first `inferred`
  finding any reader in this package raises and the first entry in a document's
  `extensions.divejson.inferred`.

  `dive_summary`, `tank_summary`, `tank_update` and most of `dive_gas` are Garmin's, no
  Garmin file exists in this project yet, and they are tested over encoder-built messages
  only. `docs/fit-mapping.md` marks every one of those rows **untested** and records what
  is deliberately unmapped and why.

- **Subsurface `.ssrf` is the second format this package reads.** Its save file rather than
  its UDDF export, so it holds everything Subsurface knows: `divejson convert
  my-logbook.ssrf`, `sniff` answers `"ssrf"`, and `fixtures/ssrf/` carries the pairs any
  port is measured against. Every measurement in the format states its unit — `'45.91 m'`,
  `'66:50 min'`, `'12.0 l'`, `'200.0 bar'`, `'22.4 C'`, `'32.0%'` — so one table maps each
  spelling to the number in front of it and **a unit the table does not carry is dropped and
  named** rather than converted by a factor no file has checked. That is also why this
  reader settles no scale and emits no `resolved` finding: the two kinds its report can
  carry are `absent` and `dropped`.

  Reading one Subsurface logbook through both of its export paths gives the same profiles,
  sample for sample, and eight members that differ — the five-star visibility its UDDF
  exporter turns into metres, the air blend and the `0.00` helium that exporter writes for
  a cylinder recording no gas, the `0` kg of lead it writes for a logbook holding no
  weights, the water temperature and the end-of-dive CNS and OTU the export drops, and the
  site `location` it fills with a copy of the site's own name. `docs/ssrf-mapping.md`
  records each, along with what is deliberately not mapped and why.

  **A `.ssrf` dive carries no id**, so its identity is its position in the file and every
  conversion says so. That makes the archive case ordinary rather than exotic: the
  positional stand-in is prefixed by the archive member, so two files' first dives do not
  collide.

- **One entry point for every source format.** `divejson.convert(source)` recognises what a
  file is from its own bytes and reads it through the adapter registered for it;
  `divejson.sniff(head)` answers the same question on a bounded head — `SNIFF_BYTES` of
  them — for an application that has to decide before it has the whole upload, and returns
  `None` for bytes nothing here claims rather than a parse error from whichever reader was
  asked first. `divejson convert --from <format>` says what a file is when the bytes do not.

  **`convert_uddf` and `convert_uddf_file` are gone**, renamed rather than aliased:
  `convert(data)` and `convert(data, format="uddf")` are what replace them, and both
  produce exactly the document `convert_uddf` did.

- **A zip of files in one format is one logbook.** A watch writes one file per dive and an
  account export is an archive of them, so the members are converted together, in
  member-name order, into one document — with every `where` path and every positional
  identity prefixed by the member it came from, so two files whose dives carry no ids do
  not collide. A record two members both define — every per-dive export repeats the site it
  was at — is one record: written once, referred to by both. An archive that mixes formats,
  or holds something nothing reads, is refused rather than partly imported. `max_members`
  and `max_member_size` bound the walk for a caller that needs them, and a member is
  measured before it is opened.

- **A note carries a kind** — `absent` for what the source never recorded, `inferred` for a
  value the converter computed from readings it did, `resolved` for a recorded number whose
  scale the source left ambiguous, `dropped` for what it recorded and this format cannot
  hold. `Conversion.grouped()` groups on the kind as well as the message and returns
  `NoteGroup(kind, message, wheres)`, and `divejson convert` prints the kind beside each
  line. A converter that computes a value lists the member under
  `extensions.divejson.inferred` as well, and those two always travel together; a
  resolution lists nothing, because the number is the source's own. UDDF's `<tankvolume>`
  and `<o2>` scale readings are the `resolved` case, so the list stays absent and nothing a
  UDDF conversion produces has changed.

- **One error base.** Everything a converter raises is a `ConverterError`:
  `UnsupportedSourceError`, `SourceTooLargeError`, `MalformedArchiveError`,
  `DoctypeRefusedError`, `NonConformingOutputError`, and a per-format branch —
  `UddfError`, `MalformedUddfError` — under it. `DoctypeRefusedError` moved off `UddfError`,
  because spec §9 binds every reader rather than the UDDF one.

- **The rules that are not any one format's now live in one place**, so the readers after
  this one inherit them instead of re-deriving them: the `<!DOCTYPE>` refusal and the parse
  target every XML source goes through, the sample axis (ordered by recorded time, one
  reading per second, each channel taking only the samples that carried one, no profile at
  all rather than one of zero length), and which way a source zero reads — asked of the
  member's own schema constraint, so `max_depth` of 0 is absence and `weight` of 0 is a
  diver's "no lead".

- **`divejson conform` walks a corpus by registry id.** A pair directory is checked against
  what this build registers rather than against a table beside the runner, so an adapter
  arrives with its pairs and nothing else has to be told.

## 0.2.0

- **The package moved here**, out of the specification repository. That repository keeps
  what a conformance suite is — the prose, the schema, the fixture pairs and the mapping
  documents — and runs a released implementation against them; every implementation, in
  whatever language, is a repository of its own. One of them living beside the
  specification was an asymmetry every port would have inherited.

- **First release to PyPI**: `pip install divejson`, where the only way to install it
  before was from git. It is 0.2.0 rather than 0.1.0 because 0.1.0 already exists, as the
  package in the specification repository's history — two different trees under one
  version number is worth a number rather than an explanation.

- **`divejson conform <corpus>`**, the conformance runner an implementation of the format
  provides, and what both this repository's CI and the specification repository's run. It
  walks `valid/`, `invalid/`, a directory of reader pairs per source format and
  `write/<format>/` of writer pairs, and separates a case that **failed** (status 1) from
  a corpus whose **shape** stopped cases from running at all (status 2) — an empty
  directory, a pair missing one of its halves, pairs for a format the implementation does
  not register. A suite that read "nothing to run" as success is what that distinction is
  for, and it replaces the hand-kept fixture count that used to guard the same thing.

- **The specification is vendored and pinned.** `SPEC_REF` names the commit that
  `schema/`, `fixtures/` and `docs/` were taken from, and CI checks on every pull request
  that every file the specification owns is byte-identical here and that the pin is an
  ancestor of its `main`. The copy may run ahead — an adapter lands with its own pairs and
  mapping document before the specification adopts them — but it may never contradict.

- **`load_schema()` resolves a schema per minor version**, and a built wheel carries every
  minor the vendored tree has rather than only the one this package validates against.
  Spec §7 gives each minor its own schema, and the directories were already named for
  them.

- **`fitdecode` is a core dependency**, before anything imports it and deliberately not
  behind an extra: `pip install divejson==<release>` has to be the whole install
  everywhere, and a format behind an extra is one every consumer has to know to ask for
  and every runner can quietly skip.

- **`divejson.conform.compared`** is now the package's own rule for what a converted
  document is compared on — everything except `exported_at` and `generator`, which are
  facts about the run rather than about the input. It was a test helper, where a port or
  an application checking its own determinism could not reach it.

- `py.typed`, and an `__all__` on the package naming what it exports.
