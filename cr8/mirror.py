"""Content-addressed, atomic, incrementally rebuilt listening mirror."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Mapping, NamedTuple

from mediafile import (
    Image,
    ImageType,
    MP3DescStorageStyle,
    MediaField,
    MediaFile,
)
from mediafile.exceptions import FileTypeError, MediaFileError
from mutagen._util import FileThing
from mutagen.mp3 import MP3
from PIL import Image as PillowImage
from PIL import ImageDraw, ImageFont, ImageOps

from .audio import (
    AudioFile,
    SourceChoice,
    bounce_files,
    choose_mirror_source,
    probe_duration,
    sha256_file,
)
from .config import Config
from .db import ensure_public_ids, transaction, utc_now
from .resolve import enqueue_review
from .tooling import find_tool, run_tool


ALBUM_ARTIST = "Hareesh"
SENTINEL = ".crate_mirror_sentinel"
ENCODER_SETTINGS = "libmp3lame-cbr-320k"
_CUSTOM_FIELDS = {
    "crate_camelot": "CAMELOT",
    "crate_status": "STATUS",
    "crate_era": "ERA",
    "crate_instr": "INSTR",
    "crate_collab": "COLLAB",
    "crate_mixrole": "MIXROLE",
    "crate_energy": "ENERGY",
    "crate_song_id": "SONGID",
    "crate_bounce_id": "BOUNCEID",
}


for _field_name, _description in _CUSTOM_FIELDS.items():
    if not hasattr(MediaFile, _field_name):
        MediaFile.add_field(
            _field_name,
            MediaField(MP3DescStorageStyle(desc=_description)),
        )


@dataclass(frozen=True)
class ExpectedMirror:
    bounce_id: int
    song_id: int
    bounce_public_id: str
    song_public_id: str
    source: SourceChoice
    src_sha256: str
    encoder_settings: str
    mirror_relpath: str
    tag_hash: str
    tags: dict[str, object]
    cover_bytes: bytes


class MirrorExpectations(NamedTuple):
    items: tuple[ExpectedMirror, ...]
    skipped: tuple[int, ...]


@dataclass(frozen=True)
class ExpectedStemMirror:
    stem_id: int
    bounce_id: int
    song_id: int
    stem_public_id: str
    song_public_id: str
    source: SourceChoice
    src_sha256: str
    mirror_relpath: str
    tag_hash: str
    tags: dict[str, object]
    cover_bytes: bytes


@dataclass(frozen=True)
class BuildSummary:
    total: int
    rebuilt: int
    retagged: int
    unchanged: int
    peaks_built: int
    covers_built: int
    pruned: int
    swept_tmp: int
    skipped_tools: tuple[str, ...]
    # Bounces the catalogue knows about whose audio was not on disk, so
    # they could not be mirrored this time round. Normal while a corpus is
    # still copying; worth looking at if it persists.
    awaiting_source: tuple[int, ...] = ()


def _hex_color(value: str | None, fallback_seed: str) -> tuple[int, int, int]:
    if value:
        cleaned = value.strip().lstrip("#")
        if re_full_hex(cleaned):
            return tuple(int(cleaned[index : index + 2], 16) for index in (0, 2, 4))
    digest = hashlib.sha256(fallback_seed.encode("utf-8")).digest()
    return tuple(45 + byte % 150 for byte in digest[:3])


def re_full_hex(value: str) -> bool:
    return len(value) == 6 and all(char in "0123456789abcdefABCDEF" for char in value)


def generate_cover_bytes(
    title: str,
    *,
    era_color: str | None = None,
    size: int = 512,
    camelot: str | None = None,
    peaks_path: Path | None = None,
) -> bytes:
    """Generate deterministic JPEG cover art.

    Owner's pick (2026-08-01): the envelope — the song's own loudness shape
    in its key hue — is the live cover whenever peaks exist. The era gradient
    stays as the fallback for tracks whose peaks have not been built yet; the
    next build upgrades them, so a missing peaks file degrades the art, never
    the build.
    """
    if peaks_path is not None and peaks_path.is_file():
        try:
            from .art import render_envelope_bytes

            return render_envelope_bytes(
                peaks_path, camelot=camelot, era_color=era_color
            )
        except Exception:
            pass
    base = _hex_color(era_color, title)
    digest = hashlib.sha256(title.encode("utf-8")).digest()
    accent = tuple(
        max(0, min(255, channel + (digest[index] % 91) - 45))
        for index, channel in enumerate(base)
    )
    gradient = PillowImage.linear_gradient("L").resize((size, size))
    image = ImageOps.colorize(gradient, black=base, white=accent).convert("RGB")

    # No title text. It was drawn at size//12 in PIL's default bitmap font,
    # which is legible on the 1400px original and complete mush at the 48px
    # the library actually displays - so the thing meant to identify a track
    # identified nothing, and the only place the full size is ever seen is the
    # ID3 art embedded in the mp3, where the player shows the title anyway.
    #
    # A quiet diagonal keeps the covers from reading as flat swatches, drawn
    # from the same digest so it stays deterministic per song.
    draw = ImageDraw.Draw(image, "RGBA")
    band = max(1, size // 90)
    offset = (digest[7] % 5 - 2) * (size // 12)
    draw.line(
        [(size * 0.18 + offset, size), (size * 0.72 + offset, 0)],
        fill=(255, 255, 255, 26),
        width=band,
    )
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92, optimize=False, progressive=False)
    return buffer.getvalue()


def _song_tags(
    connection: sqlite3.Connection, song_id: int
) -> dict[str, list[str]]:
    # Only the dimensions that get stamped into ID3. The catalog also carries
    # derived bookkeeping dims (e.g. 'use') that must never reach a tag frame,
    # so unknown dimensions are ignored rather than crashing the build.
    values: dict[str, list[str]] = {"vibe": [], "instr": [], "collab": []}
    for row in connection.execute(
        """
        SELECT dim, value FROM song_tags
        WHERE song_id=? AND dim IN ('vibe','instr','collab')
        ORDER BY dim, value
        """,
        (song_id,),
    ):
        values[str(row["dim"])].append(str(row["value"]))
    return values


def _qualified_title(title: str, version: object, mixrole: str) -> str:
    qualifiers: list[str] = []
    if version is not None:
        qualifiers.append(f"v{int(version)}")
    if mixrole != "main":
        qualifiers.append(mixrole)
    return f"{title} ({', '.join(qualifiers)})" if qualifiers else title


def _canonical_tags(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    cover_bytes: bytes,
) -> dict[str, object]:
    dimensions = _song_tags(connection, int(row["song_id"]))
    collaborators = sorted(
        {
            *(value for value in dimensions["collab"] if value != "solo"),
            *([str(row["collab_raw"])] if row["collab_raw"] else []),
        }
    )
    artist = ", ".join(collaborators) if collaborators else ALBUM_ARTIST
    return {
        "title": _qualified_title(
            str(row["title"]), row["version"], str(row["mixrole"])
        ),
        "album": str(row["title"]),
        "albumartist": ALBUM_ARTIST,
        "artist": artist,
        "track": int(row["chain_position"]),
        "date": str(row["bounce_date"]) if row["bounce_date"] else None,
        "genres": sorted(dimensions["vibe"]),
        "bpm": float(row["bpm"]) if row["bpm"] is not None else None,
        "initial_key": str(row["key_canon"]) if row["key_canon"] else None,
        "CAMELOT": str(row["key_camelot"] or ""),
        "STATUS": str(row["status"]),
        "ERA": str(row["era_name"] or ""),
        "INSTR": "; ".join(sorted(dimensions["instr"])),
        "COLLAB": "; ".join(collaborators),
        "MIXROLE": str(row["mixrole"]),
        "ENERGY": str(row["energy"] if row["energy"] is not None else ""),
        "SONGID": str(row["song_public_id"]),
        "BOUNCEID": str(row["bounce_public_id"]),
        "cover_sha256": hashlib.sha256(cover_bytes).hexdigest(),
    }


def _tag_hash(tags: Mapping[str, object]) -> str:
    encoded = json.dumps(
        dict(tags), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _mirror_rows(
    connection: sqlite3.Connection,
    bounce_ids: frozenset[int] | None = None,
) -> list[sqlite3.Row]:
    if bounce_ids is not None and not bounce_ids:
        return []
    bounce_filter = ""
    parameters: tuple[int, ...] = ()
    if bounce_ids is not None:
        ordered = tuple(sorted(bounce_ids))
        bounce_filter = f" AND b.id IN ({','.join('?' for _ in ordered)})"
        parameters = ordered
    return connection.execute(
        f"""
        SELECT b.id AS bounce_id, b.public_id AS bounce_public_id,
               b.song_id, b.bounce_date, b.version, b.mixrole, b.collab_raw,
               s.public_id AS song_public_id, s.title, s.status,
               s.key_canon, s.key_camelot, s.bpm, s.energy,
               e.name AS era_name, e.color AS era_color,
               v.chain_position
        FROM bounces AS b
        JOIN songs AS s ON s.id=b.song_id
        JOIN v_song_bounces AS v ON v.id=b.id
        LEFT JOIN eras AS e ON e.id=s.era_id
        WHERE EXISTS (
          SELECT 1 FROM files AS f
          WHERE f.bounce_id=b.id AND f.layer='curated'
            AND f.missing_since IS NULL
        )
        {bounce_filter}
        ORDER BY b.id
        """,
        parameters,
    ).fetchall()


def mirror_expectations(
    connection: sqlite3.Connection,
    config: Config,
    *,
    bounce_ids: frozenset[int] | None = None,
) -> MirrorExpectations:
    ensure_public_ids(connection)
    covers: dict[int, bytes] = {}
    expected: list[ExpectedMirror] = []
    skipped_sources: list[int] = []
    for row in _mirror_rows(connection, bounce_ids):
        bounce_id = int(row["bounce_id"])
        try:
            source = choose_mirror_source(
                bounce_files(connection, config, bounce_id),
                state_dir=config.state_dir,
            )
        except ValueError:
            # No readable source on disk, though the catalogue believes there
            # is one. That means the file has not arrived rather than that it
            # is gone - a corpus mid-copy, a drive not yet mounted - because
            # anything genuinely deleted gets marked missing by the scan and
            # filtered out before it reaches here.
            #
            # Skip this one bounce and keep going. Raising abandons the whole
            # build, so a single file that has not landed yet would stop every
            # other track on the machine from being mirrored, which is how one
            # missing file turns into an app with nothing new in it.
            skipped_sources.append(bounce_id)
            continue
        digest = sha256_file(source.source.path)
        song_id = int(row["song_id"])
        # Each bounce's cover comes from its OWN peaks, never a sibling's —
        # a cover that borrows another bounce's file flips its hash when that
        # bounce is pruned, and verify reads the flip as corruption.
        cover = generate_cover_bytes(
            str(row["title"]),
            era_color=str(row["era_color"]) if row["era_color"] else None,
            camelot=str(row["key_camelot"]) if row["key_camelot"] else None,
            peaks_path=config.state_dir / "mirror" / "peaks"
            / f"{row['bounce_public_id']}.json",
        )
        covers.setdefault(song_id, cover)
        tags = _canonical_tags(connection, row, cover)
        public_id = str(row["bounce_public_id"])
        expected.append(
            ExpectedMirror(
                bounce_id=bounce_id,
                song_id=song_id,
                bounce_public_id=public_id,
                song_public_id=str(row["song_public_id"]),
                source=source,
                src_sha256=digest,
                encoder_settings=source.encoder_settings,
                mirror_relpath=f"tracks/{public_id}.mp3",
                tag_hash=_tag_hash(tags),
                tags=tags,
                cover_bytes=cover,
            )
        )
    return MirrorExpectations(tuple(expected), tuple(skipped_sources))


def stem_mirror_expectations(
    connection: sqlite3.Connection,
    config: Config,
    *,
    bounce_ids: frozenset[int] | None = None,
) -> list[ExpectedStemMirror]:
    if bounce_ids is not None and not bounce_ids:
        return []
    stems_root = (config.state_dir / "stems").resolve()
    covers: dict[int, bytes] = {}
    expected: list[ExpectedStemMirror] = []
    bounce_filter = ""
    parameters: tuple[int, ...] = ()
    if bounce_ids is not None:
        ordered = tuple(sorted(bounce_ids))
        bounce_filter = f" WHERE b.id IN ({','.join('?' for _ in ordered)})"
        parameters = ordered
    rows = connection.execute(
        f"""
        SELECT st.id AS stem_id, st.public_id AS stem_public_id,
               st.bounce_id, st.kind, st.archive_relpath, st.archive_sha256,
               st.duration_s, b.bounce_date, b.version, b.collab_raw,
               b.public_id AS parent_bounce_public_id, b.song_id,
               s.public_id AS song_public_id, s.title, s.status,
               s.key_canon, s.key_camelot, s.bpm, s.energy,
               e.name AS era_name, e.color AS era_color,
               v.chain_position
        FROM stems AS st
        JOIN stem_runs AS sr ON sr.id=st.run_id AND sr.ok=1
        JOIN bounces AS b ON b.id=st.bounce_id
        JOIN songs AS s ON s.id=b.song_id
        JOIN v_song_bounces AS v ON v.id=b.id
        LEFT JOIN eras AS e ON e.id=s.era_id
        {bounce_filter}
        ORDER BY st.id
        """,
        parameters,
    ).fetchall()
    state_root = config.state_dir.resolve()
    for row in rows:
        relpath = Path(str(row["archive_relpath"]))
        if relpath.is_absolute():
            raise ValueError(f"absolute stem archive path: {relpath}")
        archive = (state_root / relpath).resolve(strict=True)
        if not archive.is_relative_to(stems_root):
            raise ValueError(f"stem archive escapes stems root: {relpath}")
        digest = sha256_file(archive)
        if digest != row["archive_sha256"]:
            raise ValueError(f"stem archive hash mismatch: {relpath}")
        song_id = int(row["song_id"])
        cover = generate_cover_bytes(
            str(row["title"]),
            era_color=str(row["era_color"]) if row["era_color"] else None,
            camelot=str(row["key_camelot"]) if row["key_camelot"] else None,
            peaks_path=config.state_dir / "mirror" / "peaks"
            / f"{row['stem_public_id']}.json",
        )
        covers.setdefault(song_id, cover)
        tag_row = dict(row)
        tag_row["bounce_public_id"] = str(row["stem_public_id"])
        tag_row["mixrole"] = str(row["kind"])
        tags = _canonical_tags(connection, tag_row, cover)
        stem_public_id = str(row["stem_public_id"])
        source = AudioFile(
            id=int(row["stem_id"]),
            bounce_id=int(row["bounce_id"]),
            relpath=str(relpath),
            path=archive,
            ext=".flac",
            duration_s=(
                float(row["duration_s"]) if row["duration_s"] is not None else None
            ),
            sha256=digest,
        )
        expected.append(
            ExpectedStemMirror(
                stem_id=int(row["stem_id"]),
                bounce_id=int(row["bounce_id"]),
                song_id=song_id,
                stem_public_id=stem_public_id,
                song_public_id=str(row["song_public_id"]),
                source=SourceChoice(source, ENCODER_SETTINGS),
                src_sha256=digest,
                mirror_relpath=f"tracks/{stem_public_id}.mp3",
                tag_hash=_tag_hash(tags),
                tags=tags,
                cover_bytes=cover,
            )
        )
    return expected


def _write_cover(path: Path, payload: bytes) -> bool:
    if path.is_file() and hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(payload).digest():
        return False
    temporary = Path(f"{path}.tmp.{os.getpid()}")
    temporary.write_bytes(payload)
    os.replace(temporary, path)
    return True


def _open_mediafile(path: Path) -> MediaFile:
    try:
        return MediaFile(path)
    except FileTypeError:
        try:
            mutagen_file = MP3(path)
        except Exception as exc:
            raise RuntimeError(f"cannot open MP3 for tagging: {path}: {exc}") from exc
        if mutagen_file.tags is None:
            mutagen_file.add_tags()
        media = MediaFile.__new__(MediaFile)
        handle = path.open("r+b")
        media.filething = FileThing(handle, str(path), str(path))
        media.mgfile = mutagen_file
        media.type = "mp3"
        media.id3v23 = False
        return media


def _close_mediafile(media: MediaFile) -> None:
    fileobj = getattr(media.filething, "fileobj", None)
    if fileobj is not None:
        fileobj.close()


def _write_tags(path: Path, tags: Mapping[str, object], cover_bytes: bytes) -> None:
    try:
        media = _open_mediafile(path)
        try:
            media.delete()
        finally:
            _close_mediafile(media)
        media = _open_mediafile(path)
        try:
            media.title = tags["title"]
            media.album = tags["album"]
            media.albumartist = tags["albumartist"]
            media.artist = tags["artist"]
            media.track = tags["track"]
            if tags["date"]:
                media.date = date.fromisoformat(str(tags["date"]))
            media.genres = list(tags["genres"])
            media.bpm = tags["bpm"]
            media.initial_key = tags["initial_key"]
            media.images = [Image(cover_bytes, desc="Cover", type=ImageType.front)]
            for field_name, description in _CUSTOM_FIELDS.items():
                setattr(media, field_name, str(tags[description]))
            media.save(v2_version=4)
        finally:
            _close_mediafile(media)
    except MediaFileError as exc:
        raise RuntimeError(f"cannot tag {path}: {exc}") from exc


def _transcode_or_copy(
    choice: SourceChoice,
    temporary: Path,
    *,
    ffmpeg: Path,
) -> None:
    if choice.encoder_settings == "copy-mp3":
        shutil.copyfile(choice.source.path, temporary)
        return
    result = run_tool(
        ffmpeg,
        (
            "-v",
            "error",
            "-y",
            "-i",
            choice.source.path,
            "-map_metadata",
            "-1",
            "-vn",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "320k",
            "-f",
            "mp3",
            temporary,
        ),
        timeout=1800,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed for {choice.source.relpath}: {result.stderr.strip()}"
        )


def _validate_track(path: Path, source: Path, *, ffmpeg: Path, state_dir: Path) -> None:
    source_duration = probe_duration(source, state_dir=state_dir)
    output_duration = probe_duration(path, state_dir=state_dir)
    if source_duration is None or output_duration is None:
        raise RuntimeError(f"cannot verify duration for {path}")
    if abs(source_duration - output_duration) > 0.5:
        raise RuntimeError(
            f"duration mismatch for {path}: source={source_duration:.3f}s "
            f"mirror={output_duration:.3f}s"
        )
    decoded = run_tool(
        ffmpeg,
        ("-v", "error", "-i", path, "-f", "null", "-"),
        timeout=1800,
    )
    if decoded.returncode != 0:
        raise RuntimeError(f"decode check failed for {path}: {decoded.stderr.strip()}")


def _process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Someone else's process, but it exists.
        return True
    return True


def _owns_temporary(path: Path, *, pid: int) -> bool:
    """Is this half-written file ours, or abandoned by a process that died?

    Never true for a file another LIVE process is in the middle of writing.
    That is the whole point: the mirror build writes `<name>.tmp.<pid>` and
    renames it into place, and the sweep used to delete every `*.tmp.*` it
    found. The ingest tick builds the mirror too and takes no lock, so a tick
    landing inside a nightly build - it runs every 5 minutes, the build takes
    about 6 - deleted the nightly's temporary files, and the nightly then died
    on the rename with FileNotFoundError. It took the build stage down, which
    took final-verify down with 83 stale tag hashes behind it.
    """
    suffix = path.suffix.lstrip(".")
    if not suffix.isdigit():
        # Not one of ours; leave it rather than guess.
        return False
    owner = int(suffix)
    return owner == pid or not _process_is_alive(owner)


def _sweep_temporary_files(root: Path, *, pid: int | None = None) -> int:
    mine = os.getpid() if pid is None else pid
    swept = 0
    for path in root.rglob("*.tmp.*"):
        if path.is_file() and _owns_temporary(path, pid=mine):
            path.unlink()
            swept += 1
    return swept


def _last_good_count(connection: sqlite3.Connection) -> int | None:
    row = connection.execute(
        "SELECT value FROM build_state WHERE key='last_good_count'"
    ).fetchone()
    return int(row["value"]) if row is not None else None


def _prune_expired(
    connection: sqlite3.Connection,
    mirror_root: Path,
    *,
    now: datetime,
) -> int:
    cutoff = now - timedelta(days=30)
    rows = connection.execute(
        """
        SELECT mf.bounce_id, mf.mirror_relpath, b.song_id,
               MAX(f.missing_since) AS missing_since,
               SUM(CASE WHEN f.missing_since IS NULL THEN 1 ELSE 0 END) AS active
        FROM mirror_files AS mf
        JOIN bounces AS b ON b.id=mf.bounce_id
        LEFT JOIN files AS f ON f.bounce_id=b.id AND f.layer='curated'
        GROUP BY mf.bounce_id
        """
    ).fetchall()
    expired: list[sqlite3.Row] = []
    for row in rows:
        if int(row["active"] or 0) != 0 or not row["missing_since"]:
            continue
        try:
            missing = datetime.fromisoformat(str(row["missing_since"]))
            if missing.tzinfo is not None:
                missing = missing.replace(tzinfo=None)
        except ValueError:
            continue
        if missing < cutoff:
            expired.append(row)
    for row in expired:
        track = mirror_root / str(row["mirror_relpath"])
        bounce_public = track.stem
        (mirror_root / "peaks" / f"{bounce_public}.json").unlink(missing_ok=True)
        track.unlink(missing_ok=True)
    if expired:
        with transaction(connection):
            connection.executemany(
                "DELETE FROM mirror_files WHERE bounce_id=?",
                [(int(row["bounce_id"]),) for row in expired],
            )
        for song_id in {int(row["song_id"]) for row in expired}:
            remaining = int(
                connection.execute(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM mirror_files AS mf
                       JOIN bounces AS b ON b.id=mf.bounce_id
                       WHERE b.song_id=?)
                      +
                      (SELECT COUNT(*) FROM stems AS st
                       JOIN bounces AS b ON b.id=st.bounce_id
                       WHERE b.song_id=? AND st.mirror_relpath IS NOT NULL)
                    """,
                    (song_id, song_id),
                ).fetchone()[0]
            )
            if remaining == 0:
                song = connection.execute(
                    "SELECT public_id FROM songs WHERE id=?", (song_id,)
                ).fetchone()
                if song is not None and song["public_id"]:
                    (
                        mirror_root / "art" / f"{song['public_id']}.jpg"
                    ).unlink(missing_ok=True)
    return len(expired)


def build_mirror(
    connection: sqlite3.Connection,
    config: Config,
    *,
    mirror_root: Path | None = None,
    force_shrink: bool = False,
    tool_paths: Mapping[str, Path | None] | None = None,
    bounce_ids: frozenset[int] | None = None,
    _settling: bool = False,
) -> BuildSummary:
    root = (mirror_root or (config.state_dir / "mirror")).resolve()
    if root == config.corpus.root.resolve() or config.corpus.root.resolve() in root.parents:
        raise ValueError("mirror root must not be inside the source corpus")
    tracks_dir = root / "tracks"
    peaks_dir = root / "peaks"
    art_dir = root / "art"
    for directory in (root, tracks_dir, peaks_dir, art_dir):
        directory.mkdir(parents=True, exist_ok=True)
    swept = _sweep_temporary_files(root)
    tools = {
        name: (
            tool_paths.get(name)
            if tool_paths is not None and name in tool_paths
            else find_tool(name, state_dir=config.state_dir)
        )
        for name in ("ffmpeg", "ffprobe", "rsgain", "audiowaveform")
    }
    if tools["ffmpeg"] is None or tools["ffprobe"] is None:
        missing = [
            name for name in ("ffmpeg", "ffprobe") if tools[name] is None
        ]
        raise ValueError(f"mirror build requires: {', '.join(missing)}")
    projection = mirror_expectations(
        connection, config, bounce_ids=bounce_ids
    )
    expected = projection.items
    awaiting_source = projection.skipped
    expected_stems = stem_mirror_expectations(
        connection, config, bounce_ids=bounce_ids
    )
    full_count = int(
        connection.execute(
            """
            SELECT COUNT(DISTINCT b.id)
            FROM bounces AS b
            JOIN files AS f ON f.bounce_id=b.id
            WHERE f.layer='curated' AND f.missing_since IS NULL
            """
        ).fetchone()[0]
    )
    last_good = _last_good_count(connection)
    if (
        last_good is not None
        and full_count * 10 < last_good * 9
        and not force_shrink
    ):
        raise ValueError(
            f"cascade guard: curated bounce count {full_count} is below "
            f"90% of last-known-good {last_good}; use --force-shrink to override"
        )
    sentinel = root / SENTINEL
    sentinel_was_present = sentinel.is_file()
    sentinel.unlink(missing_ok=True)
    existing = {
        int(row["bounce_id"]): row
        for row in connection.execute("SELECT * FROM mirror_files")
    }
    rebuilt = 0
    retagged = 0
    unchanged = 0
    peaks_built = 0
    covers_built = 0
    changed_tracks: list[Path] = []
    peaks_needed: list[tuple[Path, Path]] = []
    records: list[ExpectedMirror] = []
    stem_records: list[ExpectedStemMirror] = []
    written_covers: set[int] = set()
    for item in expected:
        if item.source.mismatch is not None:
            original, compressed, delta = item.source.mismatch
            with transaction(connection):
                enqueue_review(
                    connection,
                    "twin_mismatch",
                    payload={
                        "files": sorted((original.relpath, compressed.relpath)),
                        "duration_delta_s": round(delta, 3) if delta is not None else None,
                        "build_action": "transcode_lossless",
                    },
                )
        with transaction(connection):
            connection.execute(
                "UPDATE files SET sha256=? WHERE id=?",
                (item.src_sha256, item.source.source.id),
            )
        track_path = root / item.mirror_relpath
        peak_path = peaks_dir / f"{item.bounce_public_id}.json"
        art_path = art_dir / f"{item.song_public_id}.jpg"
        prior = existing.get(item.bounce_id)
        audio_changed = (
            prior is None
            or prior["src_sha256"] != item.src_sha256
            or prior["encoder_settings"] != item.encoder_settings
            or not track_path.is_file()
        )
        tag_changed = (
            audio_changed or prior is None or prior["tag_hash"] != item.tag_hash
        )
        if item.song_id not in written_covers:
            covers_built += int(_write_cover(art_path, item.cover_bytes))
            written_covers.add(item.song_id)
        if audio_changed:
            temporary = Path(f"{track_path}.tmp.{os.getpid()}")
            _transcode_or_copy(
                item.source,
                temporary,
                ffmpeg=tools["ffmpeg"],
            )
            _write_tags(temporary, item.tags, item.cover_bytes)
            _validate_track(
                temporary,
                item.source.source.path,
                ffmpeg=tools["ffmpeg"],
                state_dir=config.state_dir,
            )
            os.replace(temporary, track_path)
            rebuilt += 1
            changed_tracks.append(track_path)
            peaks_needed.append((track_path, peak_path))
        elif tag_changed:
            _write_tags(track_path, item.tags, item.cover_bytes)
            _validate_track(
                track_path,
                item.source.source.path,
                ffmpeg=tools["ffmpeg"],
                state_dir=config.state_dir,
            )
            retagged += 1
            changed_tracks.append(track_path)
        else:
            unchanged += 1
            if not peak_path.is_file():
                peaks_needed.append((track_path, peak_path))
        records.append(item)

    existing_stems = {
        int(row["id"]): row
        for row in connection.execute(
            "SELECT id, mirror_relpath, built_at FROM stems"
        )
    }
    stem_tag_hashes = {
        int(str(row["key"]).removeprefix("stem_tag_hash:")): str(row["value"])
        for row in connection.execute(
            "SELECT key, value FROM build_state WHERE key LIKE 'stem_tag_hash:%'"
        )
    }
    for item in expected_stems:
        track_path = root / item.mirror_relpath
        peak_path = peaks_dir / f"{item.stem_public_id}.json"
        art_path = art_dir / f"{item.song_public_id}.jpg"
        prior = existing_stems[item.stem_id]
        audio_changed = (
            prior["mirror_relpath"] != item.mirror_relpath
            or prior["built_at"] is None
            or not track_path.is_file()
        )
        tag_changed = (
            audio_changed or stem_tag_hashes.get(item.stem_id) != item.tag_hash
        )
        if item.song_id not in written_covers:
            covers_built += int(_write_cover(art_path, item.cover_bytes))
            written_covers.add(item.song_id)
        if audio_changed:
            temporary = Path(f"{track_path}.tmp.{os.getpid()}")
            _transcode_or_copy(
                item.source,
                temporary,
                ffmpeg=tools["ffmpeg"],
            )
            _write_tags(temporary, item.tags, item.cover_bytes)
            _validate_track(
                temporary,
                item.source.source.path,
                ffmpeg=tools["ffmpeg"],
                state_dir=config.state_dir,
            )
            os.replace(temporary, track_path)
            rebuilt += 1
            changed_tracks.append(track_path)
            peaks_needed.append((track_path, peak_path))
        elif tag_changed:
            _write_tags(track_path, item.tags, item.cover_bytes)
            _validate_track(
                track_path,
                item.source.source.path,
                ffmpeg=tools["ffmpeg"],
                state_dir=config.state_dir,
            )
            retagged += 1
            changed_tracks.append(track_path)
        else:
            unchanged += 1
            if not peak_path.is_file():
                peaks_needed.append((track_path, peak_path))
        stem_records.append(item)

    skipped_tools: list[str] = []
    if changed_tracks:
        rsgain = tools["rsgain"]
        if rsgain is None:
            skipped_tools.append("rsgain")
        else:
            result = run_tool(
                rsgain,
                ("custom", "-a", "-s", "i", "-I", "4", *changed_tracks),
                timeout=3600,
            )
            if result.returncode != 0:
                raise RuntimeError(f"rsgain failed: {result.stderr.strip()}")
    if peaks_needed:
        audiowaveform = tools["audiowaveform"]
        if audiowaveform is None:
            skipped_tools.append("audiowaveform")
        else:
            for track_path, peak_path in peaks_needed:
                temporary = Path(f"{peak_path}.tmp.{os.getpid()}")
                result = run_tool(
                    audiowaveform,
                    (
                        "-i",
                        track_path,
                        "-o",
                        temporary,
                        "--output-format",
                        "json",
                        "--pixels-per-second",
                        "10",
                        "-b",
                        "8",
                    ),
                    timeout=600,
                )
                if result.returncode != 0:
                    raise RuntimeError(
                        f"audiowaveform failed for {track_path}: "
                        f"{result.stderr.strip()}"
                    )
                os.replace(temporary, peak_path)
                peaks_built += 1
    built_at = utc_now()
    for start in range(0, len(records), 500):
        with transaction(connection):
            connection.executemany(
                """
                INSERT INTO mirror_files(
                  bounce_id, mirror_relpath, src_sha256, encoder_settings,
                  tag_hash, built_at
                ) VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(bounce_id) DO UPDATE SET
                  mirror_relpath=excluded.mirror_relpath,
                  src_sha256=excluded.src_sha256,
                  encoder_settings=excluded.encoder_settings,
                  tag_hash=excluded.tag_hash,
                  built_at=excluded.built_at
                """,
                [
                    (
                        item.bounce_id,
                        item.mirror_relpath,
                        item.src_sha256,
                        item.encoder_settings,
                        item.tag_hash,
                        built_at,
                    )
                    for item in records[start : start + 500]
                ],
            )
    for start in range(0, len(stem_records), 500):
        chunk = stem_records[start : start + 500]
        with transaction(connection):
            connection.executemany(
                """
                UPDATE stems
                SET mirror_relpath=?, built_at=?
                WHERE id=?
                """,
                [
                    (item.mirror_relpath, built_at, item.stem_id)
                    for item in chunk
                ],
            )
            connection.executemany(
                """
                INSERT INTO build_state(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                [
                    (f"stem_tag_hash:{item.stem_id}", item.tag_hash)
                    for item in chunk
                ],
            )
    pruned = (
        _prune_expired(connection, root, now=datetime.now())
        if bounce_ids is None
        else 0
    )
    with transaction(connection):
        if bounce_ids is None:
            connection.execute(
                """
                INSERT INTO build_state(key, value) VALUES('last_good_count', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (str(full_count),),
            )
        connection.execute(
            """
            INSERT INTO build_state(key, value) VALUES('last_build_at', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (built_at,),
        )
    # Envelope covers derive from peaks, and a brand-new bounce computes its
    # expectations before its peaks exist — so its first pass stamps the
    # gradient fallback. Settle in the same build: one incremental pass over
    # what was just processed now sees the peaks and retags the drifted
    # covers, so verify never meets the halfway state. Guarded against
    # recursing more than once; a settled pass is stable by construction.
    if not _settling and peaks_built > 0:
        settle_ids = frozenset(
            {record.bounce_id for record in records}
            | {stem.bounce_id for stem in stem_records}
        )
        if settle_ids:
            settled = build_mirror(
                connection,
                config,
                mirror_root=mirror_root,
                tool_paths=tool_paths,
                bounce_ids=settle_ids,
                _settling=True,
            )
            rebuilt += settled.rebuilt
            retagged += settled.retagged
            covers_built += settled.covers_built

    # Only our own leftovers mean this build did not finish. A temporary file
    # belonging to a live process is another build still working, which is not
    # this build's failure to report.
    mine = os.getpid()
    if any(
        path.is_file() and _owns_temporary(path, pid=mine)
        for path in root.rglob("*.tmp.*")
    ):
        raise RuntimeError("mirror build incomplete: temporary files remain")
    if bounce_ids is None or sentinel_was_present:
        temporary_sentinel = Path(f"{sentinel}.tmp.{os.getpid()}")
        temporary_sentinel.write_text("cr8 mirror\n", encoding="utf-8")
        os.replace(temporary_sentinel, sentinel)
    return BuildSummary(
        total=len(expected) + len(expected_stems),
        rebuilt=rebuilt,
        retagged=retagged,
        unchanged=unchanged,
        peaks_built=peaks_built,
        covers_built=covers_built,
        pruned=pruned,
        swept_tmp=swept,
        awaiting_source=tuple(awaiting_source),
        skipped_tools=tuple(sorted(set(skipped_tools))),
    )
