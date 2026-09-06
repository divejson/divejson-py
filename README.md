# divejson

Python tools for [DiveJSON](https://divejson.org), an open interchange format for scuba
dive logs: the validator, the converters that read other dive-log formats into DiveJSON,
and `divejson conform`, the conformance runner an implementation of the format is checked
with.

The format itself — the normative specification, the JSON Schema and the conformance
corpus — lives in [divejson/divejson](https://github.com/divejson/divejson). This is an
implementation of it, in a repository of its own, and it **vendors** that repository's
schema, fixtures and mapping documents from the commit named in
[`SPEC_REF`](https://github.com/divejson/divejson-py/blob/main/SPEC_REF). CI checks the
copy against the specification at that commit on every pull request, so which version of
the format this package implements is a fact in the tree rather than a claim in a
sentence.

## Install

```bash
pip install divejson
```

Python 3.10 or newer.

## Validate a document

```bash
divejson validate my-logbook.divejson
```

The JSON Schema, and then the requirements the specification states in prose and a schema
cannot — identifier uniqueness, referential closure, profile-series integrity, the member
order, the UTC offset on `exported_at`. Exit status is non-zero if any file fails, with
one line per violation.

## Convert a logbook into DiveJSON

```bash
divejson convert my-logbook.uddf
```

writes `my-logbook.divejson` beside the input and reports, line by line, what the source
did not carry — no UTC offsets, a cylinder whose size nobody recorded, coordinates that
were `0.000000`. **Nothing absent is filled in**: that report is the other half of the
output, not a diagnostic, and it is what tells a diver which parts of their history their
old application never kept. Every line says which kind of news it is:

| kind | what it means |
| --- | --- |
| `absent` | the source never recorded this |
| `inferred` | the converter computed it from other readings the source did keep |
| `resolved` | the source recorded the number and left its scale ambiguous; the converter decided how to read it |
| `dropped` | the source recorded it and this format cannot hold it |

`inferred` and `resolved` are worth telling apart, because they answer different questions
about the number in front of you: an `inferred` one is the converter's arithmetic, a
`resolved` one is the source's own figure at the scale it must have meant — a
`<tankvolume>` of `12` in a field UDDF specifies in cubic metres is twelve litres, not a
twelve-thousand-litre cylinder.

An `inferred` member is also listed under `extensions.divejson.inferred` in the document
itself, so a reader can tell a derivation from a reading (spec §5.4). A `resolved` one is
not: nothing was derived, so there is nothing to label.

**The format is recognised from the file's own bytes**, not from its extension, and
`--from <format>` says what a file is when the bytes do not. A **zip** whose files are all
one format is read as one logbook — which is what a watch that writes one file per dive
produces — and an archive that mixes formats, or holds something no reader claims, is
refused rather than partly imported.

Each format has a mapping document of its own: the rules every converter follows whatever
it is reading are in
[`docs/converting.md`](https://github.com/divejson/divejson-py/blob/main/docs/converting.md),
and what is one format's — its element map, its writers' habits, its ambiguities, and what
is deliberately left unmapped — is in
[`docs/uddf-mapping.md`](https://github.com/divejson/divejson-py/blob/main/docs/uddf-mapping.md)
and
[`docs/ssrf-mapping.md`](https://github.com/divejson/divejson-py/blob/main/docs/ssrf-mapping.md).

From Python, the same two steps an application takes:

```python
import divejson

divejson.sniff(head)          # a format id, "zip", or None — from divejson.SNIFF_BYTES bytes
conversion = divejson.convert(open("my-logbook.uddf", "rb"))
conversion.document           # the DiveJSON document
conversion.grouped()          # NoteGroup(kind, message, wheres), one per finding
```

## Run a conformance corpus

```bash
divejson conform fixtures --strict
```

Walks a corpus — `valid/` documents that must validate, `invalid/` ones that must not,
and one directory of reader pairs per source format, named for the format — and says what
this implementation makes of it. The exit status distinguishes two ways of not passing:

| status | meaning |
| --- | --- |
| 0 | every case passed |
| 1 | a case **failed**: a valid document that does not validate, an invalid one that does, a pair whose produced document differs from the expected one |
| 2 | the corpus's **shape** is wrong: an empty directory, a pair missing one of its halves, or pairs for a format this implementation does not register — cases that never ran, which is not the same answer as cases that failed |

`--only <format>` and `--skip <format>` narrow the run to a format's pairs, and neither
reaches `valid/` or `invalid/`. `--strict` turns a format this implementation reads and
the corpus has no pairs for from a warning into an error.

## What it reads

| format | id | what this package does | versions |
| --- | --- | --- | --- |
| DiveJSON | — | validates | 1.0 |
| UDDF | `uddf` | reads into DiveJSON | 3.0 – 3.2.3 |
| Subsurface | `ssrf` | reads into DiveJSON | save format 3 |

The **id** is the whole coupling between this package and everything around it: it is what
`sniff` returns, what `--from` takes, and what a conformance corpus names a directory of
pairs after. A zip of files in one format sniffs as `zip`, which is a container rather than
a format — it has no reader, no identity namespace and no pair directory.

The UDDF reader matches element names rather than the declared version, so older
documents using the same names are read too: the corpus it is checked against carries
2.2.0, 3.2.0, 3.2.1 and 3.2.2, under three different root shapes.

Subsurface can export both, and the two are not equivalent: `.ssrf` is its **save file**
and holds everything it knows, while its UDDF export fills gaps in ways that survive into a
converted document. Reading the same logbook both ways gives the same profiles, sample for
sample, and four members that differ — each of them the exporter's doing.
[`docs/ssrf-mapping.md`](https://github.com/divejson/divejson-py/blob/main/docs/ssrf-mapping.md)
lists them.

## Releasing

A release is a tag. Move `__version__` in `divejson/__init__.py` — the build reads the
version from there, so it is the only place it lives — head the changelog's new entries
with it, land that, and push `v<version>`.
`.github/workflows/release.yml` then builds the sdist and, from it, the wheel; checks
that the tag names the version it built and that the wheel actually runs the corpus; and
publishes to PyPI by trusted publishing, with no API token anywhere. Nothing else
publishes, and a release is the only thing another repository can pin.

## Notices

`fitdecode` (MIT) is a dependency of this package. The FIT Protocol and FIT file format
are proprietary to Garmin; this project is not affiliated with or endorsed by Garmin, and
carries no part of the FIT SDK.

## License

MIT — see [`LICENSE`](https://github.com/divejson/divejson-py/blob/main/LICENSE). The
vendored `schema/`, `fixtures/` and `docs/` are MIT in the specification repository too;
the specification prose itself, which is CC BY 4.0, is not vendored here.
