#!/usr/bin/env python3
"""Bake a catalog track's envelope into the static web login mark."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sqlite3

from cr8.art import _color_ramp, _envelope_buckets, camelot_hue


@dataclass(frozen=True)
class LoginMarkTrack:
    title: str
    bounce_ulid: str
    camelot: str
    peaks_path: Path


def _connection(db_path: Path) -> sqlite3.Connection:
    resolved = db_path.resolve(strict=True)
    connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _track_from_row(row: sqlite3.Row, mirror_root: Path) -> LoginMarkTrack:
    bounce_ulid = str(row["bounce_ulid"])
    camelot = str(row["key_camelot"]).strip().upper()
    if camelot_hue(camelot) is None:
        raise RuntimeError(f"bounce {bounce_ulid} has an invalid Camelot key")
    peaks_path = mirror_root / "peaks" / f"{bounce_ulid}.json"
    if not peaks_path.is_file():
        raise RuntimeError(f"bounce {bounce_ulid} has no peaks JSON")
    return LoginMarkTrack(
        title=" ".join(str(row["title"]).split()),
        bounce_ulid=bounce_ulid,
        camelot=camelot,
        peaks_path=peaks_path,
    )


def select_track(
    db_path: Path,
    mirror_root: Path,
    *,
    bounce_ulid: str | None = None,
) -> LoginMarkTrack:
    resolved_mirror = mirror_root.resolve(strict=True)
    with _connection(db_path) as connection:
        if bounce_ulid:
            row = connection.execute(
                """
                SELECT s.title, s.key_camelot, b.public_id AS bounce_ulid
                FROM bounces AS b
                JOIN songs AS s ON s.id=b.song_id
                WHERE b.public_id=?
                  AND NULLIF(TRIM(s.key_camelot), '') IS NOT NULL
                """,
                (bounce_ulid,),
            ).fetchone()
            if row is None:
                raise RuntimeError(
                    f"bounce {bounce_ulid} was not found or has no Camelot key"
                )
            return _track_from_row(row, resolved_mirror)

        rows = connection.execute(
            """
            SELECT s.title, s.key_camelot, b.public_id AS bounce_ulid
            FROM bounces AS b
            JOIN songs AS s ON s.id=b.song_id
            WHERE b.public_id IS NOT NULL
              AND NULLIF(TRIM(s.key_camelot), '') IS NOT NULL
            ORDER BY s.keeper DESC,
                     COALESCE(NULLIF(TRIM(b.bounce_date), ''), '') DESC,
                     b.id DESC
            """
        )
        for row in rows:
            try:
                return _track_from_row(row, resolved_mirror)
            except RuntimeError:
                continue
    raise RuntimeError("no keyed catalog bounce has a peaks JSON")


def _hex_color(color: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{channel:02x}" for channel in color)


def render_module(track: LoginMarkTrack) -> str:
    levels = _envelope_buckets(track.peaks_path)
    bars = ", ".join(f"{level / 255:.2f}" for level in levels)
    _, middle, _ = _color_ramp(track.camelot, None)
    return (
        f"// Baked by scripts/bake-login-mark.py from {track.title} "
        f"({track.bounce_ulid}).\n"
        "// Regenerate: ./.venv/bin/python scripts/bake-login-mark.py "
        "[--bounce ULID]\n"
        f'export const LOGIN_MARK = {{bars: [{bars}], hue: "{_hex_color(middle)}"}};\n'
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bounce", metavar="ULID", help="bake a specific bounce")
    parser.add_argument("--db", type=Path, default=Path("catalog.db"))
    parser.add_argument(
        "--mirror-root",
        type=Path,
        help="mirror root (defaults to <database directory>/mirror)",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("web/lib/loginMark.ts")
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    mirror_root = args.mirror_root or args.db.resolve().parent / "mirror"
    try:
        track = select_track(args.db, mirror_root, bounce_ulid=args.bounce)
        source = render_module(track)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        raise SystemExit(f"error: {exc}") from exc
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(source, encoding="utf-8")
    print(f"Baked login mark from {track.title} ({track.bounce_ulid}).")


if __name__ == "__main__":
    main()
