# Changelog

Notable changes to the DiveJSON tools for Python. The format's own version
(`major.minor`, declared in every document) is what readers and writers depend on, and it
is versioned in [the specification repository](https://github.com/divejson/divejson);
this file is about the package, whose version moves independently.

## Unreleased

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
  sample for sample, and six members that differ — the five-star visibility its UDDF
  exporter turns into metres, the air blend and the `0.00` helium that exporter writes for
  a cylinder recording no gas, the `0` kg of lead it writes for a logbook holding no
  weights, a water temperature the export drops, and the site `location` it fills with a
  copy of the site's own name. `docs/ssrf-mapping.md` records each, along with what is
  deliberately not mapped and why.

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
