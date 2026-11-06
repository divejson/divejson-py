# divejson-py

Read [CONTRIBUTING.md](CONTRIBUTING.md) before changing an adapter, especially "What is here, and
what is only borrowed". The specification and format decisions belong in
[divejson/divejson](https://github.com/divejson/divejson).

## Vendored files

Files in schema/, fixtures/ and docs/ that the specification already owns must match SPEC_REF.
Change those files in the specification repository, then update SPEC_REF and re-vendor together.
Verify byte equality separately from the ancestry check. A genuinely new reader or writer may carry
new mappings and pairs before the specification adopts them, as CONTRIBUTING.md describes; that
exception does not permit changing an existing specification-owned file here.

For an existing mapping change, follow the documented spec-first merge order. Link companion PRs
and state which merge or release each depends on. While the specification PR's CI installs a commit
from this branch, add new commits rather than amending or force-pushing, so the pinned commit stays
reachable. Re-pin to the merged specification commit before merging here.

## Importer verification

New mappings need a shareable fixture from the real writer, with expected output. A field no file
shows stays unmapped or is marked untested in place. Verify source units, destination scales, invalid
sentinels, recording origins and multi-session behavior independently. For FIT changes, event
placement follows the Events section of `docs/fit-mapping.md`. Never lose a dive's samples silently:
report what the reader cannot carry, and refuse the file only where the mapping document says to.

Annotate conversion tables with the types they hold, give scale factors a named constant, and test
scaling separately from event or sample ordering. When a reader gains a channel, add it to the label
table the reader uses (`_READING_LABELS` in `fit.py`, `CHANNEL_LABELS` in `series.py`), so the collision
note names it rather than printing the raw key. Search source comments and mapping prose for stale
factual claims when adding fixture coverage, and add the Unreleased changelog entry.
Run the commands under CONTRIBUTING.md's "Working on the package"; the installed-wheel check runs
in CI.

For review fixes, inspect review bodies, inline comments and discussion attachments. Reconcile each
item with the final diff, comments and PR description, including fixture provenance and validation
results. State remaining verification or dependency blockers rather than calling them complete.
