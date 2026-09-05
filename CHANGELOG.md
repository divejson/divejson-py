# Changelog

Notable changes to the DiveJSON tools for Python. The format's own version
(`major.minor`, declared in every document) is what readers and writers depend on, and it
is versioned in [the specification repository](https://github.com/divejson/divejson);
this file is about the package, whose version moves independently.

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
