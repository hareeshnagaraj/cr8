# Portable export

Run:

```sh
cr8 export ./crate-export
```

The directory contains:

- `songs.csv` — every song with factual metadata and all vibe, instrument, and collaborator tags.
- `songs.json` — every song plus each tag’s dimension, provenance, and author.
- `collections/*.m3u` — one ordered playlist per collection, pointing at the source audio.

The export is read-only. Existing files in the destination with these generated names are replaced; the catalog database and source audio are never changed.
