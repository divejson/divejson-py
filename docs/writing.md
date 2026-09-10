# Writing DiveJSON out again

**Non-normative.** The specification is [`spec/divejson.md`](../spec/divejson.md); nothing
here changes what a conforming document is. This document records the rules a converter
follows whatever format it is **writing**, so that they survive being reimplemented: the
mirror of [`converting.md`](converting.md), which records the rules it follows whatever it
is reading, and the place a port in another language starts from for this direction.

**The word points the other way here, and it is worth saying so once.** §1.1 defines a
*writer* as software that produces DiveJSON and a *reader* as software that consumes it —
so `converting.md`'s converters are §1.1 **writers**, and this document's subject, which
turns a DiveJSON document into a file of some other format, is a §1.1 **reader**. Below,
"a writer" means whatever writes that other file, which is the sense every
`<format>-writing.md` uses. Where the specification's sense is meant, it is spelled out.

Each written format then has a document of its own beside this one —
[`uddf-writing.md`](uddf-writing.md) is the first — carrying what is that format's: which
member lands in which element or field, what the format has no room for, what a reader will
make of each thing that did not fit, and what known consumers make of a correct file. Those
documents do not repeat what is here. **When a rule turns out to hold for more than one
format, it moves into this file and the format's document keeps the example that first
showed it** — `converting.md`'s own moving rule, pointed the other way. Until that happens a
rule with one instance and nothing to derive it from stays where its example is: nothing
here is stated that does not follow from the specification, from the corpus's own mechanics,
or from what writing a foreign format is.

Much of what a writer needs is not about direction at all and is in `converting.md`: the
four note kinds, identity and the prefix rule that lets an id survive a round trip, decimal
arithmetic, and the grouping of findings. This document is what changes when the arrow
reverses.

## What a correct writer is checked against

Three things, and not one of them is a byte comparison against another implementation. A
writer built to match another implementation byte for byte is a mirror of it, and the first
divergence between the two is a bug in whichever was read last.

**The writer pairs.** `fixtures/write/<format>/` holds a DiveJSON document and the file a
correct writer produces from it — a reader pair with its halves swapped
([`fixtures/README.md`](../fixtures/README.md)). `divejson conform` runs them, and the
comparison is the **format's** rather than the corpus's: every writer stamps something that
moves without the mapping moving — which software did the writing, most often — so each
format says how two of its files are compared when one of them was produced just now. A
corpus that failed on every release is one nobody keeps green.

**How many pairs is a property, not a number.** Enough that the format's writing document is
exercised — its answers, and not only its map. A document that is itself the reading of a
file in that format loses nothing on the way back out and therefore exercises none of the
report: it is the round trip that matters most and it is not, on its own, a corpus.

**The self round trip.** Reading a written file back through a reader of that format returns
the document it was written from, on every member that format's mapping document carries —
and everything that does not come back is named in the report. That is the check a writing
document is a description of, and it is why the list of what a pair loses is committed
beside the pair rather than left to be rediscovered.

Two members are outside that comparison and are not losses: `exported_at` and `generator`
are facts about a *run* rather than about the input, which is why the corpus ignores them in
either direction (`fixtures/README.md`). A third exclusion is this direction's own, and is
below.

**And the checks the corpus cannot make.** A corpus holds documents rather than schemas, so
validating written output against the target format's own schema — where the format has one
— belongs in an implementation's test suite. A writing document is where that debt is
recorded, because a schema is commonly the only check that can see element or field
**order**: a member written in the wrong place produces a file a lenient reader — including,
often, the format's own — is perfectly happy with and no other implementation can open.

## Never invent a value to satisfy a required element

§5.4 forbids a converter to fabricate, and this is the direction the pressure to do it comes
from: the target format requires something DiveJSON does not, or has nowhere to put
something DiveJSON does. Three answers are available.

1. **Write the format's own spelling for "not recorded".** A format commonly has one for
   some of its mandatory members — a depth or a duration whose zero its own readers take
   back off as an absence — and a reader recovers exactly what was there.
2. **Drop the value and report it.**
3. **Invent something.**

The third is never taken. What keeps it that way is asking, per member rather than per
format, whether the format has a spelling for absence at all. Where it has one, writing it
loses nothing a reader cannot recover. Where it has none, the tempting move is to copy in a
neighbouring member — a record's own name into a mandatory place name — and that hands the
diver back, on the way in again, something they never wrote. It is §5.4's fabrication with
an extra step, and it is worse than the omission it replaces, because the report can see an
omission and cannot see a plausible lie.

## The report, going out

A writer returns a report beside the bytes the same way a converter returns one beside the
document, and it is half the output rather than a diagnostic. `converting.md`'s four kinds
are what a diver reads in either direction, and **two of them are a reader's alone**:

| kind | what it says on the way out |
| --- | --- |
| `absent` | the format requires something the document had nothing for, so the format's own placeholder is written; the finding says what a reader will take it as |
| `dropped` | the document recorded this and the format has nowhere to put it |
| `inferred` | never produced by a writer: it computes nothing |
| `resolved` | never produced by a writer: it reads no ambiguous scale |

The two never produced are not a stylistic preference. `inferred` obliges the **document**
to list that member under `extensions.divejson.inferred`, and the document is the input
here rather than the output, so a writer reaching for it would break the coupling
`converting.md` keeps exact in both directions.

**A finding's `where` is a path into the document being written** — `dives/0`,
`dives/0/cylinders/1`, `$` for the document itself — where a converter's is a path into the
source file. Indices are zero-based and count records in document order. Everything else
about the report is `converting.md`'s, grouping included.

**A member with nowhere to go is reported from the record, not from a list.** A writer
carrying a hand-kept list of unmapped members silently drops the next member the format
gains; asking each record which of its own members were not placed reports that one instead.
The tables in a writing document are therefore a description of what the writer does and not
the source of it.

## A writer is a function of its input

No clock, no environment, no random draw: two writes of one document are one file. That is
what makes a committed writer pair stable rather than churning every time it is regenerated,
and it is what makes the pair comparison mean anything at all — a comparison that has to
ignore a moving value ignores whatever hides behind it.

Where the target format wants a timestamp for the writing itself, the document's own
`exported_at` is the answer, and the clock is not: it is the moment this data was exported,
the document states it, and using it keeps the whole file a function of its input.

## What a written file says about itself, and what it does not

A file's provenance is about that file. After a writer has run, the software that produced
the file in front of a reader is the writer — so wherever the target format records what
wrote it, that is what goes there: §4's sense of `generator`, in another format's spelling.

**A converted document's own provenance does not go out with it.** A document converted from
some other format keeps that source's generator and declared version under the `divejson`
producer key (§5.5), and carrying that block across into the written file's "what wrote
this" slot would put another application's name on a file it did not write. So `extensions`
is the third exclusion from the self round trip, beside the two the corpus already ignores,
and — being a member the document did carry — it is reported and written down rather than
dropped silently.

## What a writing document owes

Each written format's document carries, beside its map: what the format cannot hold, and how
each such member is reported; the format's own spelling for absence where it has one, and
which of its mandatory members use it; how two files of the format are compared when one was
produced just now; the check the corpus cannot make and where that check lives instead; and
the **artefacts known consumers produce** from a correctly written file, which is what saves
the next implementer the round trip — the mirror of what a mapping document already records
about the writers it reads.

Where the format is also written by something whose habits differ — an application exporting
its own data rather than a converter producing an interchange file — the document says where
it differs and why, in place, beside the rule. The two want opposite things often enough
that a bare difference reads as a bug in one of them.
