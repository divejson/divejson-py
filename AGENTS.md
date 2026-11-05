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

For an existing mapping change, follow the documented spec-first merge order. Schema support alone
does not eliminate the need for a mapping or fixture change in the specification repository. Link
companion PRs and state which merge or release each depends on. Preserve commits installed by the
specification's CI, and re-pin to the merged specification commit before merging here.

## Importer verification

New mappings need shareable real-writer fixtures and expected outputs. Verify source units,
destination scales, invalid sentinels, recording origins and multi-session behavior independently.
For FIT changes, samples and events share one recording origin; events between samples or after the
last sample keep their timestamps without extending sampled duration. Apply the specification's
rules at the recording boundary. Unsupported layouts must fail explicitly instead of silently
losing a dive's samples.

Keep conversion-table types accurate, use named scale factors, and separate scaling checks from
message-ordering tests. Keep collision messages readable. Search source comments and mapping prose
for stale factual claims when adding fixture coverage, and add the Unreleased changelog entry.
Follow CONTRIBUTING.md's source, conformance and installed-package verification requirements.

For review fixes, inspect review bodies, inline comments and discussion attachments. Reconcile each
item with the final diff, comments and PR description, including fixture provenance and validation
results. State remaining verification or dependency blockers rather than calling them complete.
