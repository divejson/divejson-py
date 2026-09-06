# Converting into DiveJSON

**Non-normative.** The specification is [`spec/divejson.md`](../spec/divejson.md); nothing
here changes what a conforming document is. This document records the rules a converter
follows whatever it is reading, so that they survive being reimplemented: the portable part
of a converter is its rules, not its code, and a port in another language starts here.

Every source format then has a mapping document of its own beside this one —
[`uddf-mapping.md`](uddf-mapping.md) is the first — carrying what is that format's: its
element or field map, its dialects, its writers' habits, its own identity namespace, and
what it deliberately does not map. Those documents do not repeat what is here. When a rule
turns out to hold for more than one format, it moves into this file and the format's
document keeps the example that first showed it.

A claim about a real writer is checked against a file that writer produced. Where a rule
rests on a file this repository does not carry — a personal export, a sample from a public
issue tracker — the document says so in place rather than implying otherwise.

## Schema validity is never a precondition

What ships in every one of these formats is a family of dialects, and the single most
valuable file a converter is ever handed does not validate against its own schema. A
converter that gated on validation would refuse exactly the files it exists for.

So a converter reads what is there and reports what it could not use. Its own **output** is
a different matter — see *The report* below.

## Reading the source

These are the leniencies. Each one is a place where a real writer produced something its
own schema forbids, and where refusing would cost a diver their logbook.

- **Ids and references are whitespace-stripped, and read as opaque strings.** Stripping
  happens on both sides of a reference or it stops resolving. An id that its own source
  schema would refuse is read anyway: to a converter an id is an opaque string, matched
  against other ids and never parsed for meaning.
- **An empty value is absent, not zero.** This is the single most load-bearing leniency
  there is. A source that has no coordinates, no pressure and no temperature says so with
  an empty element, a `null`, or a sentinel the device reserves for exactly that, and every
  one of them means *not recorded*.
- **Text is compared after stripping.** A name of pure whitespace is no name.
- **A number that is not finite, or is too large to carry, is absent.** `NaN` and
  `Infinity` are accepted by decimal parsers and are not readings. Neither is `1e999`: the
  bound is not physical — this format sets none on a depth or a temperature, and inventing
  one here would be a converter deciding how deep a dive can be — but a *representability*
  one. JSON numbers are doubles in every reader this format expects to meet, so a value
  past that range stops being a number on the way out: a serializer writes an overflowed
  float as a bare `Infinity` token no JSON parser accepts, a double-based parser reads an
  integer that large back as infinity, and a schema validator objects to neither. Reject it
  at the point the text is read, before any scale is applied to it, and the members derived
  from it are covered too.
- **Samples are ordered by their own recorded time, never by their position in the source.**
  §6.5 requires strictly increasing sample times and nothing guarantees a writer emitted
  them in order.

### For every XML source, a `<!DOCTYPE>` is refused outright

No dive-log format has a legitimate use for a document type declaration, and spec §9
requires readers not to execute or dereference anything found in one. A standard library's
XML parser blocks external entities on its own, but it caps entity *amplification* only in
recent versions of the underlying library — a several-hundredfold blowup still parses on
older ones, and that is a property of the library version rather than a guarantee the
language makes. Refusing the declaration is the guarantee, it costs nothing real, and it
saves taking on a hardening dependency for something one hook already settles.

The refusal fires from the parser's doctype hook, which runs before a single entity
reference in the content has been expanded. Every XML adapter shares one parse target, so
the refusal is written once rather than per format.

## Units and arithmetic

This is the highest-risk part of a converter: a wrong factor produces a document that
validates perfectly and describes a dive nobody took. Nothing downstream catches it — a
validator accepts any integer as a profile sample, and a count of samples is not a value.
Each format's mapping document carries its own factor table, and a converter's unit tests
are worth more than its fixtures here.

**The channel conversions carry a scale the scalar ones do not.** That is the trap. The
most-executed conversion in a converter is its depth samples, and a list of the scalar
conversions alone does not contain it.

Arithmetic runs on decimal values parsed from the source text, not on floating point:
`2.6 × 100` is exactly `260` that way, where the float route arrives at
`260.00000000000003` and has to be rounded back out. Rounding to an integer is
half-away-from-zero — a reading of 2.5 seconds is 3, not the 2 that banker's rounding
gives.

## Identity

Source ids are whatever the source used. DiveJSON requires a UUID on every record, and §5.3
asks that identifiers be stable across exports of the same data, which a fresh random UUID
per run breaks. So:

1. **If the source id already contains a UUID, reuse it.** A writer holding real UUIDs
   often has to prefix them, because its own id type forbids a leading digit and a hex UUID
   regularly has one. A short alphabetic prefix followed by a canonical UUID is stripped.
   This is what lets a logbook that went out through another format come back
   recognisable.
2. **Otherwise, UUIDv5 over a fixed namespace and `"{kind}:{source id}"`.** Each source
   format has **one namespace of its own, fixed forever**, derived as
   `uuid5(NAMESPACE_URL, "https://divejson.org/ns/<format>")` and recorded in that format's
   mapping document rather than here. Changing one would renumber every document any
   released converter has produced from that format.
3. **The record kind is in the hash, and that is not decoration.** A source id is not
   unique within a file — a dive and its enclosing group commonly share one — so hashing
   the bare id would hand two records one UUID.
4. **A record with no id gets its position in the file**, reported as such: its identity is
   stable for an unchanged file and moves if the file's order changes.
5. **Two records of the same kind sharing an id is a source defect.** The second is dropped
   and reported, because two records cannot share one identity.

Two properties worth stating rather than discovering. Derived identities are a function of
the source id alone, so two files from *different* divers that both use the id `owner`
produce the same diver UUID — §5.3 is explicit that UUIDs are not portable identities for
shared realities, so this is within the rules, but it means converted logbooks are not safe
to merge on UUID. And a source id that changes between exports changes the identity with
it; nothing can recover from a writer that does not keep its own ids stable.

## Provenance

What the source said about itself — its format version, its generating application — rides
under the `divejson` producer key in `extensions` (§5.5), because the source's own identity
is worth keeping and the core vocabulary has nowhere for it. Each format's mapping document
names the members it writes there.

`generator` is **the converter**, not the source; §4 defines it as what produced *this*
document. `exported_at` is the moment of conversion, always offset-aware.

## What the format cannot hold, and what the source did not record

- **A member whose type constrains the text a source may put in it is checked, not merely
  capped.** Most source strings reach a free-text member where the only limit is a length;
  a constrained one — an email address is the case in hand — is different. A source string
  that the member cannot hold is read as *not recorded* and reported: a member the format
  cannot hold is a member the source did not fill in. A converter that passed it through
  would emit a document that fails its own validation, and since that is treated as the
  converter's bug rather than the file's, one unusable header field would discard an entire
  logbook. Any mapping added later that lands a source string on a constrained member owes
  the same guard.
- **A record whose format-required member the source never recorded goes, along with every
  reference to it** — a trip with no dates (§6.8 makes `starts_on` REQUIRED), a site or a
  gear item with no name (§6.10, §6.12) — rather than gaining an invented one. §5.3 forbids
  a dangling reference, so the references go with the record. A source that records nothing
  at all about a logbook's owner produces no `diver` member (§6.1): minting an identity for
  one would be §5.4's fabrication applied to people.
- **An exact `0.000000` / `0.000000` pair is not a position.** Null Island is a place: a
  reader that trusts it pins a Red Sea wreck into the Atlantic. Half a pair is not a
  position either — §6's Position object makes both members REQUIRED, which is §5.4
  enforced by shape.
- **The recorded UTC offset is preserved exactly and never supplied.** That is §5.2's whole
  point, and converting to UTC — or assuming an offset where the source recorded none — is
  the failure every tested consumer of the incumbent format produced.
- **Which way a zero reads follows the format's own constraint on the member**: `> 0` means
  the zero was a placeholder for a value the source had to write and did not have, `≥ 0`
  means it was an answer. A `max_depth` of zero is not recorded; a `weight` of zero is a
  diver's "no lead", which §6.2 makes distinct from absence.

## Cylinders

- **A cylinder with a gas and no vessel is a cylinder with its vessel members absent**,
  which §6.3 names in as many words. It is not a defect and it is not skipped.
- **A start pressure of `0` is a device's absent-marker.** §6.3 says outright that writers
  must not emit one, so it is read as not recorded rather than as an empty cylinder.
- **A dive's cylinders are numbered from 0 in document order, and only where the profile
  needs the numbering** to tie a pressure channel or a gas switch to its cylinder. §6.3
  calls `gas_number` "a label, not an array index", so a converter does not assert a
  numbering where nothing depends on it — and a source's own gas number is a label too, not
  an index into `cylinders[]`.

## Profiles

**The samples set the time axis, and each channel takes only the samples that actually
carried a reading for it.** No channel is padded to another's length: a dive with 431 depth
readings beside 29 temperatures keeps both, rather than gaining 402 invented ones.

- A sample with **no time** has no place on the axis and is dropped, reported. Nothing else
  can place a reading.
- Samples whose readings are all unusable produce **no profile at all**, rather than one
  carrying a bare `duration: 0`. A zero-length sampled record is a claim the source did not
  make. This is reported, unlike a dive that simply carries no samples: the source did
  record a profile, and this is the converter unable to carry it — the same class as a
  dropped sample rather than an absence.
- Source sample times are commonly fractional while §6.5's `times` are strictly increasing
  integers, so **two samples that round to the same second keep the first and report the
  second**.

## A container is one logbook, not a format

A watch writes one file per dive and an account export is a zip of them, so a container
whose members are all one source format is read as **one logbook**. The container itself is
not a source format: it has no registry id, no identity namespace, and no mapping document.

- **Every member must name one registered format**, and the same one. A container whose
  members name no format, or two, is refused — a converter that guessed here would merge
  two logbooks or convert half of one.
- **`where` paths and positional identities are prefixed by the member's name**, so a
  finding points at a file inside the container and a record with no id of its own is
  identified within its member rather than within the container.
- **The caller sets the caps** — how many members, and how large a member may be once
  inflated — and the reader walks the members one at a time, **refusing before it inflates**
  rather than after. A converter that read a container whole would hand an application a
  decompression bomb the application had already guarded itself against.

## Refuse rather than guess, and say which

Every case where the source **did not record** a member resolves to omitting it and
reporting that, never to a plausible default. §5.4 is the rule; the report is how a diver
learns what their file did not carry. In particular:

- No implied air for a cylinder with no gas — §6.3 says absent oxygen means not recorded,
  not 21.
- No `(0, 0)` for a missing position, and no half a position.
- No synthesized samples, and no offset supplied where the source recorded none.
- No name invented for a record whose format-required name is missing; the record and the
  references to it go instead.

**An ambiguity is not this case.** A format has places where a recorded value's *scale* or
spelling is genuinely in doubt — the same field written as a fraction by one writer and a
percentage by another, both schema-valid, with no way to tell from the file which you have.
None of those has a clean answer. Each is handled explicitly and reported when its
heuristic fires — as a `resolved` finding, the kind *The report* below defines for exactly
this — because a silent guess is the failure a converter exists to avoid. The difference
from the rule above is that the value **was** recorded and only its
interpretation is in doubt, so a magnitude test interprets data rather than inventing it;
dropping the member instead would lose a real reading from every file that writer produced.
Each format's mapping document names its own ambiguities and the test each one uses.

## The report

Every conversion returns a list of findings alongside the document. Each names a location
in the **source** file — `dive/0`, `dive/0/tankdata/1`, `site/3`, `$` — because that is
where a diver looking for a missing value has to go. Indices are zero-based and count
records of that kind in source order.

A finding is grouped by its message before it is shown, since one habit of a whole file
produces one finding per record and a thousand-dive logbook would otherwise bury the
interesting ones.

**The converter validates its own output before it is written.** A converter that can emit
a non-conforming document is a bug factory, and every way a source can be wrong is supposed
to resolve to an omission and a note — so reaching a validation failure is a bug in the
converter rather than a property of the file, and it is reported as one.

### A finding has a kind

Four, and the difference between them is what a diver needs from the report:

| kind | what it says |
| --- | --- |
| `absent` | the source never recorded this |
| `inferred` | the converter computed this from readings the source did record |
| `resolved` | the source recorded the number and left its scale or units ambiguous; the converter decided only how to read it, and the value is still the source's own |
| `dropped` | the source recorded this and the converter could not carry it |

They read in order of how much of the value the source itself supplied: nothing at all, the
readings it was computed from, the number with its scale left open, and the whole thing,
uncarriable.

**An inferred value is emitted, and labelled.** A maximum depth computed from a dive's own
depth samples is a summary of recorded readings rather than a fabrication under §5.4, and
omitting it would leave a blank depth in every reader that does not derive from the profile
itself. So the value is written, the report says it was inferred, and the document lists
every inferred member's path under **`extensions.divejson.inferred`** — a producer-defined
member (§5.5) that lets a downstream reader tell a derivation from a reading, which is what
§5.4's "clearly labels as derived" asks and what a writer can do without a normative
change.

**The list is written only when it is non-empty**, so a conversion that infers nothing
produces exactly the document it would have produced without this rule.

**A resolved value is not listed, and that is why it is its own kind.** The number a
converter writes after settling an ambiguity is still the one the source recorded — only
its scale was in doubt — so there is no derivation for a downstream reader to be told
about, and nothing goes under `extensions.divejson.inferred`. Keeping the two apart is what
makes the coupling above exact in both directions: every `inferred` note's member is listed,
and every listed member has an `inferred` note. Filing a resolution under `inferred`
instead would break one direction or the other — either a member appears in the list with
no derivation behind it, or the list acquires an exception, and an exception a port has to
know about is one a port will get wrong.

**`profile.duration` is not inferred.** §6.4 defines it as the span of the profile's own
samples, so a converter taking the largest sample time across every channel is reading a
structural member off the samples rather than filling in one the source failed to record.
It carries no finding of any kind.

## What a mapping document owes

Each format's document carries, beside its map: the format's frozen identity namespace and
the string it was derived from; its unit factor table, channels included; the dialects and
writer habits its rules answer to; its own ambiguities and their tests; the kinds its
report can emit; and **what it deliberately does not map, listed rather than left silent**,
because a port needs to know those were considered rather than missed.
