# cr8 first real-corpus scan

Date: 2026-07-29

Python: 3.14.4

Corpus root: loaded from `config.toml` (not duplicated here)

## Safety proof

A marker was created in the catalog workspace immediately before the first
scan. After the scan, and again after the correction/rescan cycle,
`/usr/bin/find` was invoked from Python with a list of argv values:

```text
marker_find_exit: 0
corpus_files_newer_than_marker: 0
```

No corpus file was written, renamed, moved, deleted, chmodded, or given an
xattr by crate.

## First scan

```text
scan: 11289 new, 0 changed, 0 unchanged, 0 deferred, 0 missing
resolve: 2969 parsed, 0 residue, 469 songs, 649 bounces
```

The first rescan exposed eight zero-byte audio rows whose stored size `0` was
incorrectly treated as absent during change detection. The comparison was
changed to distinguish SQL `NULL` from integer zero and a regression fixture
was added. Two curated Freeze-named bounces were also moved out of the
project-only `na` fast path, as required by V3.

## Final current-corpus result

```text
audio files: 11,289
curated: 1,278
curated parsed: 1,278
curated parse rate: 100.0%
songs: 471
bounces: 651
open unparsed: 0
open date_suspect: 121
open merge_suggestion: 166
open project_link: 89
open twin_mismatch: 8
```

The 471-song result is 5.8% below the nominal `≈500` guide in the
specification, while all other stated status ranges are met. It is the
deterministic result of the required same-normalized-slug identity rule on the
current corpus; no artificial disambiguations were introduced to inflate it.

## Idempotency

The final immediate rescan produced:

```text
scan: 0 new, 0 changed, 11289 unchanged, 0 deferred, 0 missing
resolve: 2971 parsed, 0 residue, 471 songs, 651 bounces
```

## Verification

```text
cr8 verify: exit 0
V1 disk↔catalog: PASS
V2 unclassified locations: PASS
V3 entity closure: PASS
V5 mirror integrity: SKIP (mirror not built yet)
V7 review SLA: PASS

cr8 verify --strict: exit 1 (dimension gaps are findings)
cr8 verify with a missing config: exit 2 (configuration error)

.venv/bin/python -m pytest: 47 passed
tests/test_no_shell_true.py: passed
```

The generated dimension gap list is in
`reports/coverage-2026-07-29.md`.
