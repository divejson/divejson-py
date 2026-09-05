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
old application never kept. The mapping rules, the three places UDDF is genuinely
ambiguous, and what is deliberately left unmapped are in
[`docs/uddf-mapping.md`](https://github.com/divejson/divejson-py/blob/main/docs/uddf-mapping.md).

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

| format | what this package does | versions |
| --- | --- | --- |
| DiveJSON | validates | 1.0 |
| UDDF | reads into DiveJSON | 3.0 – 3.2.3 |

The UDDF reader matches element names rather than the declared version, so older
documents using the same names are read too: the corpus it is checked against carries
2.2.0, 3.2.0, 3.2.1 and 3.2.2, under three different root shapes.

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
