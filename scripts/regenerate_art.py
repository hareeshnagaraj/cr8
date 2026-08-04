#!/usr/bin/env python3
"""Redraw every cover at the current size, without rebuilding any audio.

The covers were generated at 1400x1400 with the title drawn across them. A
browser decodes an image at its natural size no matter how small you display
it, so each 65KB file became 7.5MB of bitmap and the ~22 rows on screen held
about 164MB, churned on every scroll - to paint squares the size of a
fingernail, with text that was illegible at that size anyway.

This only rewrites mirror/art/*.jpg. The copy embedded in each mp3's ID3 tag is
left alone until that track is next mirrored, which is correct: it is the one
place the full size is genuinely looked at.

    scripts/regenerate_art.py --dry-run
    scripts/regenerate_art.py
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cr8.mirror import generate_cover_bytes  # noqa: E402


def main() -> int:
    dry = "--dry-run" in sys.argv
    root = Path(__file__).resolve().parent.parent
    art_dir = root / "mirror" / "art"
    if not art_dir.is_dir():
        print(f"no art directory at {art_dir}")
        return 1

    connection = sqlite3.connect(root / "catalog.db")
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT s.public_id, s.title, s.key_camelot, e.color AS era_color,
               (SELECT b.public_id FROM bounces AS b
                JOIN mirror_files AS mf ON mf.bounce_id=b.id
                WHERE b.song_id=s.id ORDER BY b.id DESC LIMIT 1
               ) AS peaks_bounce
        FROM songs AS s
        LEFT JOIN eras AS e ON e.id = s.era_id
        """
    ).fetchall()
    connection.close()

    before = sum(f.stat().st_size for f in art_dir.glob("*.jpg"))
    written = 0
    skipped = 0

    for row in rows:
        target = art_dir / f"{row['public_id']}.jpg"
        if not target.exists():
            skipped += 1
            continue
        peaks = (
            root / "mirror" / "peaks" / f"{row['peaks_bounce']}.json"
            if row["peaks_bounce"]
            else None
        )
        payload = generate_cover_bytes(
            str(row["title"] or "Untitled"),
            era_color=row["era_color"],
            camelot=row["key_camelot"],
            peaks_path=peaks,
        )
        if not dry:
            # Write beside and rename, so a reader never sees a partial file.
            temporary = target.with_suffix(".jpg.tmp")
            temporary.write_bytes(payload)
            temporary.replace(target)
        written += 1

    after = sum(f.stat().st_size for f in art_dir.glob("*.jpg"))
    print(f"  {'would rewrite' if dry else 'rewrote'} {written} covers"
          f"{f', {skipped} had no file' if skipped else ''}")
    print(f"  on disk: {before / 1024 / 1024:.1f} MB -> {after / 1024 / 1024:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
