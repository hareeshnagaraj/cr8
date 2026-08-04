# SPEC: cr8 catalog core (Phase 1)

## Objective

A Python package `cr8` in `.//` providing a CLI that scans a
music corpus READ-ONLY, parses its filename convention into structured metadata, resolves files
into a song-level entity model in SQLite, and verifies coverage. This is the foundation layer;
mirror/build/web come later — design module boundaries so they bolt on cleanly.

## Hard rules (violating any of these fails the task)

1. **The corpus is sacred and read-only.** Root: `/path/to/your/music/folder`
   (yes, the directory name contains ` : `). The code must NEVER write, rename, move, delete,
   chmod, or touch xattrs on anything under this root. No `shutil` writes into it, ever.
2. **The ':' landmine:** only `pathlib.Path` objects and argv-list `subprocess.run([...])` may
   carry the root path. Never interpolate it into a shell string. `shell=True` is banned
   project-wide (enforce with a test that greps the source).
3. All state lives in `.//` (colon-free): `catalog.db`,
   `config.toml`, `keymap.yaml`. The root path appears in `config.toml` exactly once.
4. Python 3.13+ (system has 3.14.4 at /opt/homebrew/bin/python3). Project managed with
   `pyproject.toml` + a local `.venv` (create it; plain `python3 -m venv .venv && .venv/bin/pip
   install -e '.[dev]'`). Runtime deps allowed: `mutagen`, `pyyaml`. Dev deps: `pytest`.
   Everything else stdlib (`sqlite3`, `argparse`, `tomllib`, `hashlib`, `subprocess`).
   `ffprobe` (present at /opt/homebrew/bin, ffmpeg 7.1.1) via subprocess for durations.
5. Every CLI command idempotent — safe to re-run at any time; second run of `scan` on an
   unchanged tree changes nothing except `last_seen`/run bookkeeping.
6. The DB is opened with WAL mode, `busy_timeout=10000`, `foreign_keys=ON`. All writes in short
   transactions (per-batch, not per-file; never one transaction for the whole scan).

## Layout

```
Catalog/
  pyproject.toml            # project name "cr8", console script cr8 = cr8.cli:main
  .venv/                    # gitignored
  config.toml               # see below
  keymap.yaml               # key-spelling normalization (generate per §Parser, commit it)
  cr8/
    __init__.py
    cli.py                  # argparse subcommands: scan, status, verify, review, set, export-csv, import-csv
    config.py               # load/validate config.toml
    db.py                   # connection factory, schema DDL, migrations via meta.schema_version
    scan.py                 # walker + file classification + hashing + durations
    parse.py                # filename parser (pure functions, no I/O)
    keys.py                 # key normalization + Camelot mapping (pure)
    resolve.py              # song identity resolver, twin collapse, version chains
    review.py               # review queue + interactive TUI (afplay auditions)
    verify.py               # V1-V5, V7 checks (V6 remote / V8 backup are stubbed "skipped")
    csvio.py                # export-csv / import-csv with closed-vocabulary validation
  tests/
    test_parse.py           # table-driven, uses REAL corpus filenames (fixtures below)
    test_keys.py
    test_resolve.py
    test_scan.py            # against a tmp fixture tree that mimics corpus structure (incl. a dir with ' : ' in its name)
    test_verify.py
    test_no_shell_true.py   # greps cr8/ source: shell=True must not appear
  specs/                    # (this file)
```

## config.toml (ship this exact content, values verbatim)

```toml
[corpus]
root = "/path/to/your/music/folder"

# Curated scope is an EXPLICIT include list. Top-level loose audio files are curated.
# These directories (relative to root) are curated, recursively:
curated_dirs = [
  "_demos_stage_1", "_demos_stage_2", "_8-26-24-workingplaylist",
  "2-15-25-demos-working", "11-21-23-workingbounces", "9-22-23-set-bounces",
  "9-14-23-set-prep-bounces", "4-9-bounces", "dontoli-sessionview",
  "suraj-remix-inst", "5-20-24-ranjeev-collab-files", "2-15-24-aditya-bassbits",
  "niranj-collab-1", "dudeimstilljacked-episode1", "sajni-remix",
  "clips-for-henry", "nehal-mixes", "henry-hangdrum", "bridges redo",
]
# Directories matching this glob (and this literal oddball) are the project layer:
project_glob = "* Project"
project_extra = ["2-25-24-brazilsessionview-take1 Project 2"]
# Known non-audio/reference dirs → layer "other":
other_dirs = ["dailyguitarscale", "damian-keyes", "untitled folder"]

[vocab]
status = ["idea", "jam", "demo", "mixed", "finished"]
mixrole = ["main", "vox", "novox", "inst", "bass", "gtar", "stems", "acap"]
# vibe / instr / collab start open; import-csv validates against the values already present
# plus new ones only when --allow-new is passed.
known_collabs = ["henry", "connor", "suraj", "aditya", "sid", "rjpasin", "ranjeev",
                 "rohiit", "lara", "nehal", "niranj", "mayank", "rohini", "dontoli", "nira", "em"]

[audio]
extensions = [".wav", ".mp3", ".m4a", ".aif", ".aiff", ".flac"]
```

## Schema (DDL — implement exactly; add indexes where queries need them)

Use this schema (from the adjudicated design). Tables: `files`, `songs`, `song_aliases`,
`bounces`, `song_tags`, `eras`, `analysis`, `mik_tracks`, `projects`, `song_projects`,
`review_queue`, `mirror_files`, `playlists`, `samply_uploads`, `feedback`, `rating_sync`,
`runs`, `meta`.

```sql
PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
CREATE TABLE files (
  id INTEGER PRIMARY KEY,
  relpath TEXT UNIQUE NOT NULL,          -- exact bytes relative to root
  layer TEXT NOT NULL CHECK(layer IN ('curated','project','other')),
  ext TEXT, size INTEGER, mtime REAL, md5 TEXT, duration_s REAL,
  bounce_id INTEGER REFERENCES bounces(id),
  parse_status TEXT NOT NULL DEFAULT 'unparsed'
    CHECK(parse_status IN ('unparsed','parsed','residue','assigned','na')),
  first_seen TEXT, last_seen TEXT, missing_since TEXT);
CREATE TABLE songs (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL, disambig TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'demo'
    CHECK(status IN ('idea','jam','demo','mixed','finished')),
  keeper INTEGER NOT NULL DEFAULT 0 CHECK(keeper BETWEEN 0 AND 5),
  key_canon TEXT, key_camelot TEXT, key_source TEXT,
  bpm REAL, bpm_source TEXT, energy INTEGER,
  era_id INTEGER REFERENCES eras(id),
  first_date TEXT, last_date TEXT, notes TEXT,
  UNIQUE(slug, disambig));
CREATE TABLE song_aliases (alias_slug TEXT PRIMARY KEY,
  song_id INTEGER NOT NULL REFERENCES songs(id));
CREATE TABLE bounces (
  id INTEGER PRIMARY KEY,
  song_id INTEGER NOT NULL REFERENCES songs(id),
  source_stem TEXT NOT NULL,
  bounce_date TEXT, date_source TEXT CHECK(date_source IN ('filename','mtime','human')),
  date_suspect INTEGER NOT NULL DEFAULT 0,
  version INTEGER,
  mixrole TEXT NOT NULL DEFAULT 'main'
    CHECK(mixrole IN ('main','vox','novox','inst','bass','gtar','stems','acap')),
  collab_raw TEXT,
  UNIQUE(song_id, source_stem));
CREATE TABLE song_tags (
  song_id INTEGER NOT NULL REFERENCES songs(id),
  dim TEXT NOT NULL CHECK(dim IN ('vibe','instr','collab')),
  value TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'human',
  author TEXT,                            -- who asserted it (username; NULL for machine)
  created_at TEXT,
  PRIMARY KEY(song_id, dim, value));
CREATE TABLE eras (id INTEGER PRIMARY KEY, name TEXT UNIQUE,
  date_start TEXT, date_end TEXT, color TEXT);
CREATE TABLE analysis (
  file_id INTEGER REFERENCES files(id), kind TEXT CHECK(kind IN ('key','bpm','energy','cues')),
  value TEXT, confidence REAL, source TEXT, analyzed_at TEXT);
CREATE TABLE mik_tracks (
  id INTEGER PRIMARY KEY, src_path TEXT, name TEXT, duration_s REAL,
  camelot TEXT, key_std TEXT, bpm REAL, energy INTEGER, cues_json TEXT,
  matched_file_id INTEGER REFERENCES files(id), imported_at TEXT);
CREATE TABLE projects (id INTEGER PRIMARY KEY, relpath TEXT UNIQUE,
  name_slug TEXT, name_date TEXT, als_count INTEGER, backup_als_count INTEGER, total_bytes INTEGER);
CREATE TABLE song_projects (song_id INTEGER REFERENCES songs(id),
  project_id INTEGER REFERENCES projects(id),
  method TEXT CHECK(method IN ('slug_exact','human')), PRIMARY KEY(song_id, project_id));
CREATE TABLE review_queue (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK(kind IN ('unparsed_name','merge_suggestion','possible_distinct',
    'twin_mismatch','key_conflict','bpm_conflict','date_suspect','project_link','stray_location')),
  file_id INTEGER, song_id INTEGER, payload TEXT,
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved','ignored')),
  created_at TEXT, resolved_at TEXT, resolution TEXT);
CREATE TABLE mirror_files (bounce_id INTEGER PRIMARY KEY REFERENCES bounces(id),
  mirror_relpath TEXT UNIQUE, src_md5 TEXT, tag_hash TEXT, built_at TEXT);
CREATE TABLE playlists (id INTEGER PRIMARY KEY, name TEXT UNIQUE,
  query TEXT NOT NULL, samply_sync INTEGER DEFAULT 0);
CREATE TABLE samply_uploads (bounce_id INTEGER REFERENCES bounces(id), box_id TEXT, playlist TEXT,
  uploaded_md5 TEXT, url TEXT, uploaded_at TEXT, PRIMARY KEY(bounce_id, box_id));
CREATE TABLE feedback (id INTEGER PRIMARY KEY, song_id INTEGER REFERENCES songs(id),
  source TEXT CHECK(source IN ('samply','navidrome','cr8','manual')),
  author TEXT, timecode_s REAL, body TEXT, ext_id TEXT UNIQUE,
  created_at TEXT, pulled_at TEXT, acked INTEGER DEFAULT 0);
CREATE TABLE rating_sync (song_id INTEGER REFERENCES songs(id), nd_user TEXT, stars INTEGER,
  loved INTEGER, updated_at TEXT, PRIMARY KEY (song_id, nd_user));
CREATE TABLE runs (id INTEGER PRIMARY KEY, kind TEXT, started TEXT, finished TEXT,
  ok INTEGER, notes TEXT);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
```

**Provenance contract:** machine passes (scan/parse/resolve) freely recompute: files inventory,
hashes, parse-derived bounce fields, twin pairs, version ordering, slug-exact project links.
They NEVER touch: `songs.status/keeper/notes/title` (once human-edited — track via
`meta` flag or a `songs.title_locked` pattern: simplest is `songs.human_touched INTEGER DEFAULT 0`
set by `set`/`import-csv`/review resolutions; machine passes skip human_touched fields),
`song_tags` rows with source='human', aliases, disambig, resolved review items.

## Scanner (`cr8 scan`)

- Walk root with `os.scandir` recursion (pathlib paths). Skip: `.DS_Store`, zero-byte `Icon`
  files, `.asd`, `.als`, `.amxd` (record COUNTS of these per top-level dir into `runs.notes`
  JSON but no `files` rows — files table is audio-only).
- Classify layer: inside a dir matching `project_glob`/`project_extra` (at any depth) → `project`;
  else top-level file or inside `curated_dirs` → `curated`; inside `other_dirs` → `other`;
  **anything else → create a `stray_location` review item** (one per new directory, not per file)
  and classify `other` provisionally.
- For each audio file (by extension list): upsert on `relpath`; `size`, `mtime`; md5 ONLY when
  new or size/mtime changed (stream in 1 MiB chunks); duration via one `ffprobe` call
  (`-show_entries format=duration -of csv=p=0`) — only for curated layer (project guts skip
  duration; too many files). Batch commits every 500 files.
- **Debounce rule:** skip (leave for next run) any file with `mtime` < 120 s ago OR whose size
  changes between two stats 2 s apart — the corpus is written by a live DAW.
- Files present in DB but missing on disk → set `missing_since` (never delete the row); if it
  reappears, clear it.
- Project dirs: upsert `projects` rows (relpath, name minus " Project" suffix through the parser
  → `name_slug`/`name_date`, `.als` counts split main-vs-Backup, `total_bytes` cheap sum).
- Also parse+resolve run automatically after scan (single `cr8 scan` does scan→parse→resolve →
  print delta summary). Keep each phase a separate function for testability.

## Parser (`cr8/parse.py` — pure functions)

Input: filename stem (no extension). Output dataclass:
`ParsedName(date: str|None, date_source, date_suspect: bool, title_tokens: list[str],
key_raw: str|None, version: int|None, mixrole: str, bpm: int|None, collab: str|None,
tunings: list[str], parse_branch: str)`.

**Pre-pass repairs (ordered):** collapse repeated separators (`9--26-25` → `9-26-25`);
treat `_` between digit groups as `-` for date-matching only (`12_8_23-jine` → date `12-8-23`);
4-digit year → 2-digit (`12-7-2025` → `12-7-25`); glued month-day (`709-26` → try splits
`7|09-26` and `70|9-26`, accept the one yielding a valid date; if both/neither valid → no date,
flag); month>12 and day≤12 → swap and set `date_suspect`.

**Branches (first match wins):**
- B1 date-prefix: `^M-D-YY-rest`
- B2 date-suffix: `rest-M-D-YY$`
- B3 no-year date-prefix: `^M-D-rest` (year from file mtime; if parsed month-day > mtime's
  month-day, use mtime year − 1)
- B4 undated: everything else.

**Date sanity:** resulting date must fall in 2019-01-01 .. today+2d, else discard → `date=None`,
fall back to mtime (`date_source='mtime'`). If |filename date − mtime| > 45 days → keep filename
date, `date_suspect=True` (also file a `date_suspect` review item during resolve).

**Token consumption (right-to-left over the `rest` tokens, splitting on `-`):**
1. version: `^v([0-7])$`
2. mixrole: `novox|vox|inst|instrumental|bass|gtar|gtr|guitar|acap|acapella|stems` →
   canonical mixrole (gtr/guitar→gtar; instrumental→inst; acapella→acap). Default `main`.
3. bpm: 2-3 digit int in 60–200, **right-edge only** (must be consumed before non-numeric
   tokens interpose; never eat a token that was part of the date).
4. key: token found in keymap (see below) → `key_raw`. Tunings (`dropd`, `dropc`, `dropc#`,
   `dadgad`, `dropb` — any `drop*`) are NOT keys → collect into `tunings`.
5. collab: token in `known_collabs` (also handle embedded forms: `henrysesh` → collab henry +
   remaining token `sesh` back to title).
6. Remaining tokens (original order) = title. Empty title after consumption → residue.

No branch match or empty title → `parse_status='residue'` + `unparsed_name` review item.

**Also classify project-internals** (for `files.parse_status='na'` fast-path): Ableton recorded
takes `^(.+) \d{4} \[\d{4}-\d{2}-\d{2} \d{6}\]$`, Freeze/Consolidate/Reverse/Crop patterns,
sample-pack files (inside `Samples/Imported`). These get `parse_status='na'`, no review items.

## Keys (`cr8/keys.py` + `keymap.yaml`)

Generate `keymap.yaml` covering ALL of: for each pitch class in
{c, c#, db, d, d#, eb, e, f, f#, gb, g, g#, ab, a, a#, bb, b}: spellings `X m`/`Xm`/`Xmin`/
`Xminor` → "X minor"; `Xmaj`/`Xmajor` → "X major"; bare `X` is NOT a key token (too ambiguous —
skip). Camelot: full standard 24-entry wheel mapping (e.g. B minor→10A, F# minor→11A, C# minor→12A,
G# minor→1A, … C major→8B, G major→9B, …). Enharmonic fold (db→c#, eb→d#, gb→f#, ab→g#, bb→a#
for minors; standard majors likewise). Provide `normalize(key_raw) -> (canon, camelot)`.

## Resolver (`cr8/resolve.py`)

1. `slug = normalize(title tokens joined)` — lowercase, strip all non-alphanumerics.
   Same slug = same song (respect `song_aliases` first, and `songs.disambig` splits).
2. **Twin collapse:** same parent dir + same stem + extensions {wav, mp3|m4a} → ONE `bounces`
   row, both `files.bounce_id` point at it. Cross-dir mp3 pairing: same stem + |duration Δ| ≤ 0.5 s
   → same bounce; stem match but duration Δ > 0.5 s → `twin_mismatch` review item, separate bounce.
3. Bounce fields from parse (date, version, mixrole, collab_raw). `date_source` per parser.
4. Song rollups (machine-maintained unless human_touched): `title` = title tokens joined with
   spaces (display-cased) from the MOST RECENT bounce; `first_date`/`last_date` from bounces;
   `key_canon/key_camelot` from filename keys under precedence `human > filename > mik > detected`
   (mik/detected arrive in a later phase — leave the precedence hook).
   Filename-key conflicts within one song (different pitch-class/mode after enharmonic fold)
   → `key_conflict` review item; keep the most frequent value meanwhile.
5. **Review flags (never auto-resolve):** same slug + conflicting keys + bounce-date span >
   18 months → `possible_distinct`; slugs with Levenshtein ≤ 2 (both ≥ 6 chars) or one a prefix
   of the other → `merge_suggestion` (one open item per pair, dedupe).
6. **Project links:** project `name_slug` == song slug → `song_projects(method='slug_exact')`;
   near-miss (Levenshtein ≤ 2) → `project_link` review item.
7. Version chain = bounces ordered by (bounce_date, version NULLS FIRST as 0, mtime) — computed
   in a view `v_song_bounces`, not stored.

## Review (`cr8 review`)

Plain-prompt terminal loop over open review items, oldest first, grouped by kind. Shows evidence
(payload JSON pretty), offers per-kind actions:
- `unparsed_name`: play 10 s (`afplay` via subprocess list-argv, `-t 10`), prompt for
  title/key/date/collab (blank = skip), assign → creates/updates song+bounce, file→`assigned`.
- `merge_suggestion`: A/B play, `[m]erge` (pick survivor; loser slug → `song_aliases`) /
  `[k]eep separate` / `[s]kip`.
- `possible_distinct`: `[s]plit` (prompt disambig for the newer group) / `[k]eep together`.
- `twin_mismatch`, `key_conflict`, `date_suspect`, `project_link`, `stray_location`: show
  evidence, offer sensible resolutions (pick value / link / add-dir-to-config-hint) or ignore.
All resolutions set `human_touched` where applicable and persist so rescans never re-ask.

## set / export-csv / import-csv

- `cr8 set <slug-or-id> status=demo key=f#m bpm=132 +vibe=dreamy -vibe=harsh +collab=henry
  title="Drown Me" notes="..."` — `+`/`-` prefixes for multi-valued dims; validates against vocab
  (new vibe/instr values require `--allow-new`); sets source='human', author=$USER, human_touched.
- `cr8 export-csv [--filter ...] out.csv`: one row per song: id, slug, title, status, keeper,
  key_canon, bpm, first_date, last_date, n_bounces, audition_path (latest main bounce ABSOLUTE
  path), vibe, instr, collab (each `; `-joined), notes.
- `cr8 import-csv file.csv [--allow-new]`: diffs against DB, validates vocab, applies human
  values, prints a change summary; `--dry-run` default OFF but `--dry-run` flag supported.

## verify (`cr8 verify`) — exit 0 clean / 1 findings / 2 error

- V1 disk↔catalog: every on-disk audio file has a fresh `files` row (last_seen == this scan) and
  every non-missing row exists on disk. Lists offenders.
- V2 unclassified locations: no `stray_location` items open → else fail with the dir list.
- V3 entity closure: every curated file → bounce → song; `parse_status` ∈ {parsed, assigned, na(0 curated)}
  or an open `unparsed_name` item exists for it.
- V4 dimension coverage per song: status set (non-default counts as set only if human_touched or
  reviewed — report %, don't fail), key or `key=none` explicit, ≥1 vibe, ≥1 instr, ≥1 collab or
  `solo`. Report per-dimension % + exact gap list (this IS the to-do list). Missing dimensions
  don't fail the exit code until `--strict`.
- V5 mirror integrity: SKIP (stub prints "mirror not built yet") until mirror phase.
- V7 review SLA: open items older than 14 days → listed.
- Output: human table to stdout + `reports/coverage-YYYY-MM-DD.md`.

## status (`cr8 status`)

One screen: file counts by layer/parse_status, song count, bounce count, open review items by
kind, dimension coverage %, last scan time, DB size. This is the smoke-test command.

## Tests — table-driven with REAL corpus names (minimum set; add more)

parse fixtures (stem → expected):
- `1-13-24-drownme-bm` → date 2024-01-13, title [drownme], key bm, branch B1
- `1-15-26-idontwannaneedu-f#maj-v2` → date 2026-01-15, key f#maj, version 2
- `5-20-24-backingtrack-take1-novox` → mixrole novox, title [backingtrack, take1]
- `7-29-26-stayhere-cm-v2` → key cm, v2
- `12_8_23-jine-fmin-132` → date 2023-12-08, key fmin, bpm 132, title [jine]
- `7-21-dropc#jam-vox` → B3 (year from mtime), tuning dropc#… careful: `dropc#jam` is ONE token —
  tuning prefix + title remainder; handle `drop{note}` prefix-glued tokens: strip tuning prefix,
  remainder `jam` back to title; mixrole vox
- `diamond-11-20-25-v2` → B2 date-suffix 2025-11-20, title [diamond], v2
- `jine-fmin-125` → B4 undated, key fmin, bpm 125
- `709-26-skylinedrive` → glued month-day repair → 2026-07-09, title [skylinedrive]
- `9--26-25-dm-jam` → separator collapse → 2025-09-26, key dm, title [jam]
- `12-7-2025-something` → 4-digit year repair → 2025-12-07
- `1-14-24-gtarjam2=verterae` → tolerate `=` (split to tokens or keep in title; must not crash;
  title contains verterae)
- `01 Get It Right (feat. Rohiit)` → B4, no key/bpm, title preserved (spaces ok in residue-ish
  undated names — undated names with spaces are parseable as title-only, NOT residue)
- `tmp63024` → B4 title [tmp63024] (scratch detection not required in v1)
- `leaving` → B4 title-only
- `tracking 0017 [2025-08-15 101703]` → project-internal 'na' classifier matches
- `Freeze NewKick [2022-03-28 202258]` → 'na'
- keys: `bm`/`bmin`/`bminor` → B minor/10A; `f#m` → F# minor/11A; `ebm` → D# minor (folded)/2A;
  `cmaj` → C major/8B; `dropd` → tuning, not key
- resolver: twin collapse pair; cross-dir duration mismatch → twin_mismatch; same-slug key
  conflict + 20-month span → possible_distinct; `drownme` project folder links to drownme song.
- scan test tree: build tmpdir with a ` : `-named root, nested "* Project" dir, curated dirs,
  loose top-level pairs; assert layers, debounce (fresh-mtime file skipped), missing_since
  behavior, idempotency (second scan → zero changes).

## Acceptance criteria (ALL must pass)

1. `.venv/bin/python -m pytest` green.
2. `cr8 scan` against the REAL corpus completes with zero writes to the corpus (run it; verify
   via `find <root> -newer <marker-file>` showing no corpus files modified — create the marker
   before scan, and note .DS_Store/Spotlight noise is exempt if it appears; the test is that no
   file OUR code touches changes).
3. `cr8 status` reports: ~11,200+ audio files; curated ≈ 1,200–1,350; parse rate ≥ 90% of
   curated; songs ≈ 500–650; open unparsed ≈ ≤ 120.
4. `cr8 verify` runs, produces the report, exit code correct.
5. Re-running `cr8 scan` immediately → "0 new, 0 changed" (idempotent).
6. `test_no_shell_true.py` passes.

Record actual numbers from the real scan in `reports/first-scan.md`.

## Out of scope (do NOT build yet)

MIK import, key/BPM detection, mirror build, web app, Samply, launchd, restic. But keep
`analysis`/`mirror_files`/`feedback` tables in the schema now so later phases don't migrate.
