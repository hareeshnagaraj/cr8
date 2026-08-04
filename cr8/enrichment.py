"""Mixed In Key import, key/BPM detection, and Chromaprint enrichment."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import sqlite3
import statistics
import tempfile
from typing import Iterable, Mapping

from .audio import analysis_source, bounce_files
from .config import Config
from .db import transaction, utc_now
from .keys import (
    canonical_pitch_mode,
    from_camelot,
    load_keymap,
    normalize as normalize_key,
)
from .resolve import enqueue_review, levenshtein
from .tooling import find_tool, run_tool
from .paths import source_path


@dataclass(frozen=True)
class MikImportSummary:
    imported: int
    matched: int
    unmatched: int
    conflicts: int


@dataclass(frozen=True)
class DetectSummary:
    candidates: int
    keys_analyzed: int
    bpms_analyzed: int
    failed: int
    skipped_tools: tuple[str, ...]
    # Tracks the analyser read successfully and had no answer for - an ambient
    # piece with no steady pulse, a voice memo, a drone. Counted apart from
    # `failed` because the tool did its job; there is simply no tempo there.
    undetermined: int = 0


@dataclass(frozen=True)
class FingerprintSummary:
    candidates: int
    analyzed: int
    skipped: int
    edges: int
    failed: int
    missing_tool: str | None = None


@dataclass(frozen=True)
class _MikTrack:
    source_id: int
    src_path: str | None
    name: str
    duration_s: float | None
    camelot: str | None
    key_std: str | None
    bpm: float | None
    energy: int | None
    cues_json: str


def _bookmark_path(blob: bytes | None) -> str | None:
    if not blob:
        return None
    printable = [
        item.decode("utf-8", "ignore")
        for item in re.findall(rb"[\x20-\x7e]{3,}", blob)
    ]
    absolute = [item for item in printable if item.startswith("/") and len(item) > 1]
    return max(absolute, key=len) if absolute else None


def _mik_key(camelot: object, tagged: object, keymap: Mapping[str, str]) -> tuple[str | None, str | None]:
    camelot_text = str(camelot).strip() if camelot is not None else ""
    canonical, code = from_camelot(camelot_text)
    if canonical:
        return canonical, code
    tagged_text = str(tagged).strip() if tagged is not None else ""
    if not tagged_text or tagged_text.casefold() == "all":
        return None, None
    canonical, code = normalize_key(tagged_text, keymap)
    if canonical:
        return canonical, code
    if re.fullmatch(r"[A-Ga-g](?:#|b)?", tagged_text):
        return normalize_key(f"{tagged_text}maj", keymap)
    return None, None


def _copy_mik_database(source: Path, scratch: Path) -> Path:
    destination = scratch / source.name
    shutil.copy2(source, destination)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{source}{suffix}")
        if sidecar.is_file():
            shutil.copy2(sidecar, Path(f"{destination}{suffix}"))
    return destination


def _read_mik_tracks(path: Path, keymap: Mapping[str, str]) -> list[_MikTrack]:
    source = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        cues: dict[int, list[dict[str, object]]] = {}
        if source.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ZCUEPOINT'"
        ).fetchone():
            for row in source.execute(
                "SELECT ZSONG, ZTIME, ZNAME, ZENERGYLEVEL FROM ZCUEPOINT ORDER BY ZSONG, ZTIME"
            ):
                cues.setdefault(int(row["ZSONG"]), []).append(
                    {
                        "time_s": row["ZTIME"],
                        "name": row["ZNAME"],
                        "energy": row["ZENERGYLEVEL"],
                    }
                )
        tracks: list[_MikTrack] = []
        for row in source.execute(
            """
            SELECT Z_PK, ZBOOKMARKDATA, ZNAME, ZKEY, ZTAGKEY,
                   ZTEMPO, ZTAGTEMPO, ZENERGY, ZTAGENERGY
            FROM ZSONG ORDER BY Z_PK
            """
        ):
            canonical, camelot = _mik_key(row["ZKEY"], row["ZTAGKEY"], keymap)
            tempo = float(row["ZTEMPO"] or row["ZTAGTEMPO"] or 0) or None
            raw_energy = row["ZENERGY"] or row["ZTAGENERGY"]
            energy = int(round(float(raw_energy))) if raw_energy is not None else None
            source_id = int(row["Z_PK"])
            tracks.append(
                _MikTrack(
                    source_id=source_id,
                    src_path=_bookmark_path(row["ZBOOKMARKDATA"]),
                    name=str(row["ZNAME"] or ""),
                    duration_s=None,
                    camelot=camelot,
                    key_std=canonical,
                    bpm=tempo,
                    energy=energy,
                    cues_json=json.dumps(
                        cues.get(source_id, []),
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
            )
        return tracks
    finally:
        source.close()


def _catalog_match_maps(
    connection: sqlite3.Connection, config: Config
) -> tuple[dict[str, int], dict[str, list[tuple[int, float | None]]]]:
    absolute: dict[str, int] = {}
    basename: dict[str, list[tuple[int, float | None]]] = {}
    for row in connection.execute(
        "SELECT id, relpath, duration_s FROM files WHERE missing_since IS NULL"
    ):
        file_id = int(row["id"])
        path = source_path(config, str(row["relpath"]))
        absolute[str(path)] = file_id
        basename.setdefault(path.name.casefold(), []).append(
            (
                file_id,
                float(row["duration_s"]) if row["duration_s"] is not None else None,
            )
        )
    return absolute, basename


def _match_mik_track(
    track: _MikTrack,
    absolute: Mapping[str, int],
    basename: Mapping[str, list[tuple[int, float | None]]],
) -> int | None:
    if track.src_path and track.src_path in absolute:
        return absolute[track.src_path]
    name = Path(track.src_path).name if track.src_path else track.name
    candidates = basename.get(name.casefold(), [])
    if track.duration_s is None:
        return candidates[0][0] if len(candidates) == 1 else None
    close = [
        file_id
        for file_id, duration in candidates
        if duration is not None and abs(duration - track.duration_s) <= 1.0
    ]
    return close[0] if len(close) == 1 else None


def _material_bpm_conflict(left: float, right: float) -> bool:
    return min(abs(left - right), abs(left - right * 2), abs(left * 2 - right)) > 2


def _promote_mik(
    connection: sqlite3.Connection,
    *,
    file_id: int,
    key_std: str | None,
    camelot: str | None,
    bpm: float | None,
    energy: int | None,
) -> int:
    row = connection.execute(
        """
        SELECT s.* FROM songs AS s
        JOIN bounces AS b ON b.song_id=s.id
        JOIN files AS f ON f.bounce_id=b.id
        WHERE f.id=?
        """,
        (file_id,),
    ).fetchone()
    if row is None:
        return 0
    conflicts = 0
    song_id = int(row["id"])
    if key_std:
        if row["key_canon"] is None:
            connection.execute(
                """
                UPDATE songs SET key_canon=?, key_camelot=?, key_source='mik'
                WHERE id=? AND key_canon IS NULL
                """,
                (key_std, camelot, song_id),
            )
        elif canonical_pitch_mode(str(row["key_canon"])) != canonical_pitch_mode(key_std):
            enqueue_review(
                connection,
                "key_conflict",
                song_id=song_id,
                payload={
                    "catalog": row["key_canon"],
                    "mik": key_std,
                    "file_id": file_id,
                },
            )
            conflicts += 1
    if bpm is not None:
        if row["bpm"] is None:
            connection.execute(
                "UPDATE songs SET bpm=?, bpm_source='mik' WHERE id=? AND bpm IS NULL",
                (bpm, song_id),
            )
        elif _material_bpm_conflict(float(row["bpm"]), bpm):
            enqueue_review(
                connection,
                "bpm_conflict",
                song_id=song_id,
                payload={"catalog": row["bpm"], "mik": bpm, "file_id": file_id},
            )
            conflicts += 1
    if energy is not None:
        connection.execute(
            "UPDATE songs SET energy=? WHERE id=? AND energy IS NULL",
            (energy, song_id),
        )
    return conflicts


def import_mik(
    connection: sqlite3.Connection,
    config: Config,
    *,
    source_path: Path | None = None,
) -> MikImportSummary:
    source = source_path or (
        Path.home() / "Library/Application Support/Mixedinkey/Collection10.mikdb"
    )
    if not source.is_file():
        raise FileNotFoundError(f"Mixed In Key database not found: {source}")
    keymap = load_keymap(config.keymap_path)
    with tempfile.TemporaryDirectory(prefix="crate-mik-") as scratch_text:
        copied = _copy_mik_database(source, Path(scratch_text))
        tracks = _read_mik_tracks(copied, keymap)
    absolute, basename = _catalog_match_maps(connection, config)
    matched = 0
    conflicts = 0
    imported_at = utc_now()
    with transaction(connection):
        connection.execute("DELETE FROM analysis WHERE source='mik'")
        connection.execute("DELETE FROM mik_tracks")
    for start in range(0, len(tracks), 250):
        with transaction(connection):
            for track in tracks[start : start + 250]:
                file_id = _match_mik_track(track, absolute, basename)
                if file_id is not None:
                    matched += 1
                connection.execute(
                    """
                    INSERT INTO mik_tracks(
                      id, src_path, name, duration_s, camelot, key_std, bpm,
                      energy, cues_json, matched_file_id, imported_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        track.source_id,
                        track.src_path,
                        track.name,
                        track.duration_s,
                        track.camelot,
                        track.key_std,
                        track.bpm,
                        track.energy,
                        track.cues_json,
                        file_id,
                        imported_at,
                    ),
                )
                if file_id is None:
                    continue
                for kind, value in (
                    ("key", track.key_std),
                    ("bpm", track.bpm),
                    ("energy", track.energy),
                    ("cues", track.cues_json if track.cues_json != "[]" else None),
                ):
                    if value is not None:
                        connection.execute(
                            """
                            INSERT INTO analysis(
                              file_id, kind, value, confidence, source, analyzed_at
                            ) VALUES(?, ?, ?, 0.9, 'mik', ?)
                            """,
                            (file_id, kind, str(value), imported_at),
                        )
                conflicts += _promote_mik(
                    connection,
                    file_id=file_id,
                    key_std=track.key_std,
                    camelot=track.camelot,
                    bpm=track.bpm,
                    energy=track.energy,
                )
    return MikImportSummary(
        imported=len(tracks),
        matched=matched,
        unmatched=len(tracks) - matched,
        conflicts=conflicts,
    )


def _latest_main_bounces(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    rows = connection.execute(
        """
        SELECT v.id AS bounce_id, v.song_id, v.chain_position,
               s.key_canon, s.bpm
        FROM v_song_bounces AS v
        JOIN songs AS s ON s.id=v.song_id
        WHERE v.mixrole='main' AND (s.key_canon IS NULL OR s.bpm IS NULL)
        ORDER BY v.song_id, v.chain_position DESC, v.id DESC
        """
    ).fetchall()
    latest: dict[int, sqlite3.Row] = {}
    for row in rows:
        latest.setdefault(int(row["song_id"]), row)
    return list(latest.values())


def _detected_key(output: str, keymap: Mapping[str, str]) -> tuple[str | None, str | None]:
    values = [line.strip() for line in output.splitlines() if line.strip()]
    if not values:
        return None, None
    raw = values[-1].split()[-1]
    canonical, camelot = from_camelot(raw)
    if canonical:
        return canonical, camelot
    if re.fullmatch(r"[A-Ga-g](?:#|b)?m", raw):
        return normalize_key(raw, keymap)
    if re.fullmatch(r"[A-Ga-g](?:#|b)?", raw):
        return normalize_key(f"{raw}maj", keymap)
    return normalize_key(raw, keymap)


def _detected_bpm(output: str) -> float | None:
    explicit = re.search(r"(?i)\bbpm\s*[:=]\s*(\d+(?:\.\d+)?)", output)
    if explicit is None:
        explicit = re.search(r"(?i)\b(\d+(?:\.\d+)?)\s*bpm\b", output)
    if explicit:
        value = float(explicit.group(1))
    else:
        times: list[float] = []
        for line in output.splitlines():
            try:
                times.append(float(line.strip().split()[0]))
            except (IndexError, ValueError):
                continue
        intervals = [
            right - left
            for left, right in zip(times, times[1:])
            if 0.2 <= right - left <= 2.0
        ]
        if not intervals:
            return None
        value = 60.0 / statistics.median(intervals)
    while value < 60:
        value *= 2
    while value > 200:
        value /= 2
    return round(value, 3)


def detect(
    connection: sqlite3.Connection,
    config: Config,
    *,
    limit: int | None = None,
    tool_paths: Mapping[str, Path | None] | None = None,
) -> DetectSummary:
    tools = {
        name: (
            tool_paths.get(name)
            if tool_paths is not None and name in tool_paths
            else find_tool(name, state_dir=config.state_dir)
        )
        for name in ("keyfinder-cli", "aubio")
    }
    candidates = _latest_main_bounces(connection)
    if limit is not None:
        candidates = candidates[:limit]
    keymap = load_keymap(config.keymap_path)
    keys_analyzed = 0
    bpms_analyzed = 0
    failed = 0
    undetermined = 0
    for row in candidates:
        source = analysis_source(
            bounce_files(connection, config, int(row["bounce_id"]))
        )
        if source is None:
            failed += 1
            continue
        analyzed_at = utc_now()
        if row["key_canon"] is None and tools["keyfinder-cli"] is not None:
            exists = connection.execute(
                """
                SELECT 1 FROM analysis
                WHERE file_id=? AND kind='key' AND source='keyfinder'
                """,
                (source.id,),
            ).fetchone()
            if exists is None:
                result = run_tool(tools["keyfinder-cli"], (source.path,), timeout=300)
                canonical, camelot = (
                    _detected_key(result.stdout, keymap)
                    if result.returncode == 0
                    else (None, None)
                )
                if canonical:
                    with transaction(connection):
                        connection.execute(
                            """
                            INSERT INTO analysis(
                              file_id, kind, value, confidence, source, analyzed_at
                            ) VALUES(?, 'key', ?, 0.6, 'keyfinder', ?)
                            """,
                            (source.id, canonical, analyzed_at),
                        )
                        connection.execute(
                            """
                            UPDATE songs
                            SET key_canon=?, key_camelot=?, key_source='detected'
                            WHERE id=? AND key_canon IS NULL
                            """,
                            (canonical, camelot, int(row["song_id"])),
                        )
                    keys_analyzed += 1
                elif result.returncode == 0:
                    undetermined += 1
                else:
                    failed += 1
        if row["bpm"] is None and tools["aubio"] is not None:
            exists = connection.execute(
                """
                SELECT 1 FROM analysis
                WHERE file_id=? AND kind='bpm' AND source='aubio'
                """,
                (source.id,),
            ).fetchone()
            if exists is None:
                result = run_tool(tools["aubio"], ("tempo", source.path), timeout=300)
                bpm = _detected_bpm(result.stdout) if result.returncode == 0 else None
                if bpm is not None:
                    with transaction(connection):
                        connection.execute(
                            """
                            INSERT INTO analysis(
                              file_id, kind, value, confidence, source, analyzed_at
                            ) VALUES(?, 'bpm', ?, 0.6, 'aubio', ?)
                            """,
                            (source.id, str(bpm), analyzed_at),
                        )
                        connection.execute(
                            """
                            UPDATE songs SET bpm=?, bpm_source='detected'
                            WHERE id=? AND bpm IS NULL
                            """,
                            (bpm, int(row["song_id"])),
                        )
                    bpms_analyzed += 1
                elif result.returncode == 0:
                    # aubio ran fine and printed "unknown bpm". Not a failure:
                    # some music has no tempo to find, and calling that an
                    # error made the nightly detect stage fail permanently,
                    # which skipped the mirror build behind it and left every
                    # track's tags stale.
                    undetermined += 1
                else:
                    failed += 1
    return DetectSummary(
        candidates=len(candidates),
        keys_analyzed=keys_analyzed,
        bpms_analyzed=bpms_analyzed,
        failed=failed,
        undetermined=undetermined,
        skipped_tools=tuple(name for name, path in tools.items() if path is None),
    )


def _parse_fpcalc(output: str) -> str | None:
    for line in output.splitlines():
        if line.startswith("FINGERPRINT="):
            value = line.partition("=")[2].strip()
            return value or None
    return None


def _fingerprint_values(value: str) -> list[int]:
    return [int(item) for item in value.split(",") if item.strip()]


def _similar_song_pairs(rows: list[sqlite3.Row]) -> Iterable[tuple[sqlite3.Row, sqlite3.Row]]:
    for index, left in enumerate(rows):
        for right in rows[index + 1 :]:
            if left["song_id"] == right["song_id"]:
                yield left, right
                continue
            left_slug = str(left["slug"])
            right_slug = str(right["slug"])
            if (
                min(len(left_slug), len(right_slug)) >= 6
                and (
                    left_slug.startswith(right_slug)
                    or right_slug.startswith(left_slug)
                    or levenshtein(left_slug, right_slug) <= 2
                )
            ):
                yield left, right


def _similarity_review(
    connection: sqlite3.Connection,
    left: sqlite3.Row,
    right: sqlite3.Row,
    similarity: float,
) -> bool:
    if int(left["song_id"]) == int(right["song_id"]):
        if similarity >= 0.35:
            return False
        kind = "possible_distinct"
        song_id = int(left["song_id"])
    else:
        if similarity < 0.75:
            return False
        kind = "merge_suggestion"
        song_id = None
    payload = {
        "songs": sorted(
            (
                {"id": int(left["song_id"]), "slug": str(left["slug"])},
                {"id": int(right["song_id"]), "slug": str(right["slug"])},
            ),
            key=lambda item: item["id"],
        ),
        "files": sorted((str(left["relpath"]), str(right["relpath"]))),
        "fp_similarity": round(similarity, 6),
    }
    enqueue_review(connection, kind, song_id=song_id, payload=payload)
    return True


def fingerprint(
    connection: sqlite3.Connection,
    config: Config,
    *,
    tool_path: Path | None = None,
) -> FingerprintSummary:
    fpcalc = tool_path or find_tool("fpcalc", state_dir=config.state_dir)
    rows = connection.execute(
        """
        SELECT b.id AS bounce_id, b.song_id, s.slug
        FROM bounces AS b
        JOIN songs AS s ON s.id=b.song_id
        WHERE EXISTS (
          SELECT 1 FROM files AS f
          WHERE f.bounce_id=b.id AND f.layer='curated' AND f.missing_since IS NULL
        )
        ORDER BY b.id
        """
    ).fetchall()
    if fpcalc is None:
        return FingerprintSummary(
            candidates=len(rows),
            analyzed=0,
            skipped=len(rows),
            edges=0,
            failed=0,
            missing_tool="fpcalc",
        )
    analyzed = 0
    skipped = 0
    failed = 0
    for row in rows:
        source = analysis_source(
            bounce_files(connection, config, int(row["bounce_id"]))
        )
        if source is None:
            failed += 1
            continue
        existing = connection.execute(
            "SELECT fingerprint FROM files WHERE id=?", (source.id,)
        ).fetchone()
        if existing and existing["fingerprint"]:
            skipped += 1
            continue
        result = run_tool(fpcalc, ("-raw", source.path), timeout=300)
        value = _parse_fpcalc(result.stdout) if result.returncode == 0 else None
        if value is None:
            failed += 1
            continue
        with transaction(connection):
            connection.execute(
                "UPDATE files SET fingerprint=?, fp_at=? WHERE id=?",
                (value, utc_now(), source.id),
            )
        analyzed += 1

    candidate_rows = connection.execute(
        """
        SELECT f.id AS file_id, f.relpath, f.duration_s, f.fingerprint,
               b.song_id, s.slug
        FROM files AS f
        JOIN bounces AS b ON b.id=f.bounce_id
        JOIN songs AS s ON s.id=b.song_id
        WHERE f.fingerprint IS NOT NULL AND f.missing_since IS NULL
        ORDER BY b.song_id, f.id
        """
    ).fetchall()
    edges = 0
    try:
        import acoustid
    except ImportError:
        acoustid = None
    if acoustid is not None:
        with transaction(connection):
            for left, right in _similar_song_pairs(candidate_rows):
                try:
                    similarity = float(
                        acoustid.compare_fingerprints(
                            (
                                float(left["duration_s"] or 0),
                                acoustid.chromaprint.encode_fingerprint(
                                    _fingerprint_values(str(left["fingerprint"])),
                                    1,
                                ),
                            ),
                            (
                                float(right["duration_s"] or 0),
                                acoustid.chromaprint.encode_fingerprint(
                                    _fingerprint_values(str(right["fingerprint"])),
                                    1,
                                ),
                            ),
                        )
                    )
                except (ModuleNotFoundError, TypeError, ValueError):
                    continue
                edges += int(_similarity_review(connection, left, right, similarity))
    return FingerprintSummary(
        candidates=len(rows),
        analyzed=analyzed,
        skipped=skipped,
        edges=edges,
        failed=failed,
    )
