# Contributing

Issues and pull requests on GitHub are the whole process — there is no mailing list, no
CLA, and no meeting. This repository is an *implementation*; the format itself is decided
in [divejson/divejson](https://github.com/divejson/divejson), whose `GOVERNANCE.md` says
who decides and whose `CONTRIBUTING.md` says how a change to the format lands.

## What is here, and what is only borrowed

| path | what |
| --- | --- |
| `divejson/` | the package: the validator, the UDDF reader, the conformance runner, the CLI |
| `tests/` | its tests |
| `schema/`, `fixtures/`, `docs/` | **vendored** from the specification repository at the commit `SPEC_REF` names |
| `SPEC_REF` | that commit, on one line — a tag once the format has one |

The vendored three are not editable here. CI checks out the specification at `SPEC_REF`
and asserts that every file it owns is byte-identical in this tree, and that `SPEC_REF`
is an ancestor of its `main`. What the copy *may* do is run ahead: it can carry pairs and
mapping documents the specification has not adopted yet, which is what lets a new reader
land here self-contained, with the expected documents it produces and the document that
explains them. It can never carry a different version of a file the specification already
has.

So a change lands in one of two orders.

**A change to what validates** — the schema, or a rule the fixtures encode — starts in
the specification repository, whose pull request is red against the current release and
says what is missing. The change here then sets `SPEC_REF` to that pull request's head,
which passes the byte check and fails only the ancestor one; the specification's pull
request points its own pin at this one and goes green; it merges; this one re-pins
`SPEC_REF` to the merged commit, merges, and is released. `main` here never pins a commit
that is not on the specification's `main`.

**A new reader, or a change to a mapping** goes the other way: it lands here first,
self-contained and green, then a release, and then the specification adopts the pairs and
the mapping document and moves its pin. The next change here bumps `SPEC_REF` and
re-vendors whatever moved.

## Working on the package

```bash
pip install -e ".[dev]"
pytest
divejson conform fixtures --strict
```

The tests cover the converter — the unit conversions above all, where a wrong factor
produces a document that validates perfectly and describes a dive nobody took. CI runs
them on the Python floor, on a current version, and on the version the applications that
install this package run; it also builds the sdist, builds the wheel from *that*, and
checks that an installed copy resolves the schema it carries with no checkout anywhere
near it. That last one is the failure this arrangement is most exposed to: the package
and the corpus sit in one tree here and in different worlds everywhere else.

## Regenerating an expected document

Each input under a pair directory is paired with the document it must produce, so a
mapping change shows up as a failing pair. Regenerate the expected side, and read the
diff before committing it — the point of the pair is that a human agreed with the new
answer:

```bash
divejson convert fixtures/<format>/<name>.<ext> --force \
  --exported-at "$(grep -m1 exported_at fixtures/<format>/<name>.divejson | cut -d'"' -f4)"
```

Both flags matter. Without `--force` the command refuses to replace a file that exists,
which is the right default everywhere except here. And `exported_at` is one of the two
members a converted document asserts about its own run rather than about the source, so
left to default it moves every time — reusing the value already in the file you are
replacing keeps the moving lines to the ones your change actually moved. A test runs this
recipe over the whole corpus, writing elsewhere and comparing, because a documented
command nobody runs is a command that stops working.

The other such member is `generator`, which carries this package's version. Both are
excluded from the comparison — `divejson.conform.compared` is the rule, and it is in the
package because a port and an application checking its own determinism need the same one
— so a release never regenerates anything, and the committed expectations keep whatever
version wrote them. Regenerate when the *mapping* changes, and remember that a pair the
specification already owns cannot be regenerated here: that change starts over there.

## Pull request titles

Use a semantic **PR title** — `<type>[(scope)][!]: <description>`, where type is one of
`feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`, `build`, `revert`.
Pull requests are squash-merged with the title as the commit subject, so it is the only
part of a branch that outlives the branch.

`.github/workflows/pr-title.yml` checks the format and re-runs when a title is edited —
a failing check is fixed by correcting the title, with nothing to push.

## Licensing of contributions

Everything in this repository is MIT, including the vendored schema, fixtures and
documents, which are MIT in the specification repository too. By contributing you license
your contribution under that license.
