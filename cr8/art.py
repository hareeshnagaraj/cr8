"""Deterministic preview and strip rendering from mirrored audio data."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import colorsys
import io
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Literal

from PIL import Image as PillowImage
from PIL import ImageDraw, ImageOps

from .config import Config
from .tooling import find_tool, run_tool


PreviewStyle = Literal["spectral", "envelope", "all"]

# showspectrumpic's vertical orientation puts low frequencies at the bottom.
# Keep the complete analysis filter here so changes to the plate are deliberate.
SPECTRUM_FILTER = (
    "showspectrumpic=s=512x512:legend=0:saturation=0:scale=log:fscale=log"
)
STRIP_FILTER = (
    "showspectrumpic=s=2048x256:legend=0:saturation=0:scale=log:fscale=log"
)
GROUND = (14, 14, 16)
OUTPUT_SIZE = 256
JPEG_QUALITY = 82
STRIP_JPEG_QUALITY = 80
BAR_COUNT = 10


@dataclass(frozen=True)
class PreviewTrack:
    bounce_ulid: str
    track_path: Path
    peaks_path: Path
    camelot: str | None
    era_color: str | None


@dataclass(frozen=True)
class PreviewFailure:
    style: str
    bounce_ulid: str
    error: str


@dataclass(frozen=True)
class PreviewSummary:
    selected: int
    spectral: int
    envelope: int
    failures: tuple[PreviewFailure, ...]

    def failures_for(self, style: str) -> int:
        return sum(failure.style == style for failure in self.failures)


@dataclass(frozen=True)
class StripSummary:
    selected: int
    rendered: int
    failures: tuple[PreviewFailure, ...]


# Position 1 sits at teal; the wheel walks backwards through green, yellow,
# orange, red, magenta and violet. Keep these in lockstep with web/lib/colors.ts.
CAMELOT_HUE_ORIGIN = 195
CAMELOT_HUE_STEP = 27


def camelot_hue(camelot: str | None) -> int | None:
    """Hue on the Camelot wheel. One Python home; TS twin in web/lib/colors.ts."""
    if not camelot:
        return None
    cleaned = camelot.strip().upper()
    if len(cleaned) not in (2, 3) or cleaned[-1] not in {"A", "B"}:
        return None
    try:
        position = int(cleaned[:-1])
    except ValueError:
        return None
    if not 1 <= position <= 12:
        return None
    return (CAMELOT_HUE_ORIGIN - (position - 1) * CAMELOT_HUE_STEP + 360) % 360


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = round((len(ordered) - 1) * fraction)
    return float(ordered[index])


def _contrast_stretch(values: list[float]) -> list[int]:
    if not values:
        return []
    low = _percentile(values, 0.02)
    high = _percentile(values, 0.98)
    if high <= low:
        return [255 if high > 0 and value >= high else 0 for value in values]
    scale = 255.0 / (high - low)
    return [
        max(0, min(255, round((value - low) * scale)))
        for value in values
    ]


def _stretch_image(image: PillowImage.Image) -> PillowImage.Image:
    grayscale = image.convert("L")
    histogram = grayscale.histogram()
    total = sum(histogram)
    if total == 0:
        return grayscale

    def percentile(fraction: float) -> int:
        target = round((total - 1) * fraction)
        seen = 0
        for intensity, occurrences in enumerate(histogram):
            seen += occurrences
            if seen > target:
                return intensity
        return 255

    low = percentile(0.02)
    high = percentile(0.98)
    if high <= low:
        lookup = [
            255 if high > 0 and intensity >= high else 0
            for intensity in range(256)
        ]
    else:
        scale = 255.0 / (high - low)
        lookup = [
            max(0, min(255, round((intensity - low) * scale)))
            for intensity in range(256)
        ]
    return grayscale.point(lookup)


def _parse_hex_color(value: str | None) -> tuple[int, int, int] | None:
    if not value:
        return None
    cleaned = value.strip().lstrip("#")
    if len(cleaned) != 6 or any(
        character not in "0123456789abcdefABCDEF" for character in cleaned
    ):
        return None
    return tuple(
        int(cleaned[index : index + 2], 16) for index in (0, 2, 4)
    )


def _mix(
    first: tuple[int, int, int],
    second: tuple[int, int, int],
    amount: float,
) -> tuple[int, int, int]:
    return tuple(
        round(left * (1.0 - amount) + right * amount)
        for left, right in zip(first, second, strict=True)
    )


def _color_ramp(
    camelot: str | None, era_color: str | None
) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]:
    era = _parse_hex_color(era_color)
    ground = _mix(GROUND, era, 0.14) if era is not None else GROUND
    hue = camelot_hue(camelot)
    if hue is None:
        # Keyless stays graphite, but bright enough to read: at the old
        # (110, 112, 118) mid a keyless spectrogram strip rendered as grey
        # fog on the playing row — honest, but it looked broken.
        return ground, (150, 153, 161), (240, 241, 245)
    red, green, blue = colorsys.hls_to_rgb(hue / 360.0, 0.55, 0.56)
    middle = tuple(round(channel * 255) for channel in (red, green, blue))
    return ground, middle, _mix(middle, (255, 255, 255), 0.72)


def _colorize(
    intensity: PillowImage.Image, camelot: str | None, era_color: str | None
) -> PillowImage.Image:
    ground, middle, top = _color_ramp(camelot, era_color)
    return ImageOps.colorize(
        intensity,
        black=ground,
        mid=middle,
        white=top,
    ).convert("RGB")


def _write_jpeg(
    image: PillowImage.Image,
    destination: Path,
    *,
    quality: int = JPEG_QUALITY,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.tmp.",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        image.save(
            temporary,
            format="JPEG",
            quality=quality,
            optimize=False,
            progressive=False,
        )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _run_spectrum_ffmpeg(
    ffmpeg: Path,
    track_path: Path,
    temporary: Path,
    *,
    spectrum_filter: str,
) -> None:
    # Spectral rendering is background work. nice is the parent process so the
    # ffmpeg analysis and every thread it creates inherit the lower priority.
    nice = Path("/usr/bin/nice")
    if not nice.is_file():
        raise RuntimeError("spectral rendering requires /usr/bin/nice")
    result = run_tool(
        nice,
        (
            "-n",
            "10",
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-i",
            track_path,
            "-lavfi",
            spectrum_filter,
            "-frames:v",
            "1",
            "-f",
            "image2",
            "-c:v",
            "png",
            temporary,
        ),
        timeout=600,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"ffmpeg spectral analysis failed: {detail}")


def _render_spectrum(
    track_path: Path,
    destination: Path,
    *,
    camelot: str | None,
    era_color: str | None,
    spectrum_filter: str,
    jpeg_quality: int,
    ffmpeg: Path | None = None,
    source_root: Path | None = None,
    destination_root: Path | None = None,
) -> None:
    source = track_path.resolve(strict=True)
    if source_root is not None and not source.is_relative_to(
        source_root.resolve(strict=True)
    ):
        raise RuntimeError("spectral source escapes the mirror root")
    executable = ffmpeg or find_tool("ffmpeg")
    if executable is None:
        raise RuntimeError("ffmpeg is unavailable")
    resolved_destination = destination.resolve()
    if destination_root is not None and not resolved_destination.is_relative_to(
        destination_root.resolve(strict=True)
    ):
        raise RuntimeError("spectral destination escapes its artifact directory")
    destination = resolved_destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.spectrum.tmp.",
        suffix=".png",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        _run_spectrum_ffmpeg(
            executable,
            source,
            temporary,
            spectrum_filter=spectrum_filter,
        )
        with PillowImage.open(temporary) as analyzed:
            intensity = _stretch_image(analyzed)
        _write_jpeg(
            _colorize(intensity, camelot, era_color),
            destination,
            quality=jpeg_quality,
        )
    finally:
        temporary.unlink(missing_ok=True)


def render_spectral_preview(
    track_path: Path,
    destination: Path,
    *,
    camelot: str | None,
    era_color: str | None,
    ffmpeg: Path | None = None,
    source_root: Path | None = None,
    destination_root: Path | None = None,
) -> None:
    _render_spectrum(
        track_path,
        destination,
        camelot=camelot,
        era_color=era_color,
        spectrum_filter=SPECTRUM_FILTER,
        jpeg_quality=JPEG_QUALITY,
        ffmpeg=ffmpeg,
        source_root=source_root,
        destination_root=destination_root,
    )


def render_spectral_strip(
    track_path: Path,
    destination: Path,
    *,
    camelot: str | None,
    era_color: str | None,
    ffmpeg: Path | None = None,
    source_root: Path | None = None,
    destination_root: Path | None = None,
) -> None:
    _render_spectrum(
        track_path,
        destination,
        camelot=camelot,
        era_color=era_color,
        spectrum_filter=STRIP_FILTER,
        jpeg_quality=STRIP_JPEG_QUALITY,
        ffmpeg=ffmpeg,
        source_root=source_root,
        destination_root=destination_root,
    )


def _envelope_buckets(peaks_path: Path) -> list[int]:
    try:
        payload = json.loads(peaks_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read peaks JSON: {exc}") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if (
        not isinstance(data, list)
        or not data
        or len(data) % 2
        or any(not isinstance(value, (int, float)) for value in data)
    ):
        raise RuntimeError("peaks JSON must contain interleaved min/max data")

    bucket_rms = [
        math.sqrt((float(data[index]) ** 2 + float(data[index + 1]) ** 2) / 2.0)
        for index in range(0, len(data), 2)
    ]
    reduced: list[float] = []
    for index in range(BAR_COUNT):
        start = index * len(bucket_rms) // BAR_COUNT
        end = (index + 1) * len(bucket_rms) // BAR_COUNT
        group = bucket_rms[start:end]
        if not group:
            group = [bucket_rms[min(start, len(bucket_rms) - 1)]]
        reduced.append(math.sqrt(sum(value * value for value in group) / len(group)))
    return _contrast_stretch(reduced)


def render_envelope_image(
    peaks_path: Path,
    *,
    camelot: str | None,
    era_color: str | None,
) -> PillowImage.Image:
    levels = _envelope_buckets(peaks_path.resolve(strict=True))
    ground, middle, _ = _color_ramp(camelot, era_color)
    image = PillowImage.new("RGB", (OUTPUT_SIZE, OUTPUT_SIZE), ground)
    draw = ImageDraw.Draw(image)
    margin = 10
    gap = 4
    width = (OUTPUT_SIZE - 2 * margin - gap * (BAR_COUNT - 1)) // BAR_COUNT
    usable_height = OUTPUT_SIZE - 2 * margin
    for index, level in enumerate(levels):
        height = round(usable_height * level / 255)
        if height <= 0:
            continue
        left = margin + index * (width + gap)
        top = OUTPUT_SIZE - margin - height
        draw.rectangle(
            (left, top, left + width - 1, OUTPUT_SIZE - margin - 1),
            fill=middle,
        )
    return image


def render_envelope_bytes(
    peaks_path: Path,
    *,
    camelot: str | None,
    era_color: str | None,
) -> bytes:
    """The envelope as JPEG bytes — the live cover path uses this."""
    image = render_envelope_image(
        peaks_path, camelot=camelot, era_color=era_color
    )
    buffer = io.BytesIO()
    image.save(
        buffer, format="JPEG", quality=JPEG_QUALITY, optimize=False,
        progressive=False,
    )
    return buffer.getvalue()


def render_envelope_preview(
    peaks_path: Path,
    destination: Path,
    *,
    camelot: str | None,
    era_color: str | None,
) -> None:
    image = render_envelope_image(
        peaks_path, camelot=camelot, era_color=era_color
    )
    _write_jpeg(image, destination)


def _preview_tracks(
    connection: sqlite3.Connection,
    root: Path,
    *,
    limit: int | None,
) -> list[PreviewTrack]:
    sql = """
        SELECT b.public_id AS bounce_ulid, mf.mirror_relpath,
               s.key_camelot, e.color AS era_color
        FROM mirror_files AS mf
        JOIN bounces AS b ON b.id=mf.bounce_id
        JOIN songs AS s ON s.id=b.song_id
        LEFT JOIN eras AS e ON e.id=s.era_id
        WHERE b.public_id IS NOT NULL AND mf.mirror_relpath IS NOT NULL
        ORDER BY b.id
    """
    parameters: tuple[int, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        parameters = (limit,)
    resolved_root = root.resolve(strict=True)
    tracks: list[PreviewTrack] = []
    for row in connection.execute(sql, parameters):
        bounce_ulid = str(row["bounce_ulid"])
        track_path = resolved_root / str(row["mirror_relpath"])
        tracks.append(
            PreviewTrack(
                bounce_ulid=bounce_ulid,
                track_path=track_path,
                peaks_path=resolved_root / "peaks" / f"{bounce_ulid}.json",
                camelot=(str(row["key_camelot"]) if row["key_camelot"] else None),
                era_color=(str(row["era_color"]) if row["era_color"] else None),
            )
        )
    return tracks


def render_cover_previews(
    connection: sqlite3.Connection,
    config: Config,
    *,
    style: PreviewStyle = "all",
    limit: int | None = None,
    workers: int = 4,
    mirror_root: Path | None = None,
) -> PreviewSummary:
    """Best-effort preview backfill; one failed track never stops the batch."""
    if style not in {"spectral", "envelope", "all"}:
        raise ValueError(f"unknown preview style: {style}")
    if limit is not None and limit < 0:
        raise ValueError("--limit must be non-negative")
    if workers < 1:
        raise ValueError("--workers must be at least 1")

    root = (mirror_root or (config.state_dir / "mirror")).resolve()
    tracks = _preview_tracks(connection, root, limit=limit)
    spectral_count = 0
    envelope_count = 0
    failures: list[PreviewFailure] = []

    if style in {"spectral", "all"}:
        ffmpeg = find_tool("ffmpeg", state_dir=config.state_dir)
        if ffmpeg is None:
            failures.extend(
                PreviewFailure("spectral", track.bounce_ulid, "ffmpeg is unavailable")
                for track in tracks
            )
        else:
            destination = root / "art-preview" / "spectral"
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(
                        render_spectral_preview,
                        track.track_path,
                        destination / f"{track.bounce_ulid}.jpg",
                        camelot=track.camelot,
                        era_color=track.era_color,
                        ffmpeg=ffmpeg,
                        source_root=root,
                    ): track
                    for track in tracks
                }
                for future in as_completed(futures):
                    track = futures[future]
                    try:
                        future.result()
                    except Exception as exc:
                        failures.append(
                            PreviewFailure("spectral", track.bounce_ulid, str(exc))
                        )
                    else:
                        spectral_count += 1

    if style in {"envelope", "all"}:
        destination = root / "art-preview" / "envelope"
        for track in tracks:
            try:
                render_envelope_preview(
                    track.peaks_path,
                    destination / f"{track.bounce_ulid}.jpg",
                    camelot=track.camelot,
                    era_color=track.era_color,
                )
            except Exception as exc:
                failures.append(
                    PreviewFailure("envelope", track.bounce_ulid, str(exc))
                )
            else:
                envelope_count += 1

    return PreviewSummary(
        selected=len(tracks),
        spectral=spectral_count,
        envelope=envelope_count,
        failures=tuple(
            sorted(failures, key=lambda item: (item.style, item.bounce_ulid))
        ),
    )


def render_art_strips(
    connection: sqlite3.Connection,
    config: Config,
    *,
    limit: int | None = None,
    workers: int = 4,
    mirror_root: Path | None = None,
) -> StripSummary:
    """Best-effort strip backfill; one failed track never stops the batch."""
    if limit is not None and limit < 0:
        raise ValueError("--limit must be non-negative")
    if workers < 1:
        raise ValueError("--workers must be at least 1")

    root = (mirror_root or (config.state_dir / "mirror")).resolve()
    tracks = _preview_tracks(connection, root, limit=limit)
    ffmpeg = find_tool("ffmpeg", state_dir=config.state_dir)
    if ffmpeg is None:
        return StripSummary(
            selected=len(tracks),
            rendered=0,
            failures=tuple(
                PreviewFailure("strip", track.bounce_ulid, "ffmpeg is unavailable")
                for track in tracks
            ),
        )

    destination = root / "art-strips"
    try:
        destination.mkdir(parents=True, exist_ok=True)
        destination = destination.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        return StripSummary(
            selected=len(tracks),
            rendered=0,
            failures=tuple(
                PreviewFailure("strip", track.bounce_ulid, str(exc))
                for track in tracks
            ),
        )
    if not destination.is_relative_to(root.resolve(strict=True)):
        return StripSummary(
            selected=len(tracks),
            rendered=0,
            failures=tuple(
                PreviewFailure(
                    "strip",
                    track.bounce_ulid,
                    "art-strips directory escapes the mirror root",
                )
                for track in tracks
            ),
        )
    rendered = 0
    failures: list[PreviewFailure] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                render_spectral_strip,
                track.track_path,
                destination / f"{track.bounce_ulid}.jpg",
                camelot=track.camelot,
                era_color=track.era_color,
                ffmpeg=ffmpeg,
                source_root=root,
                destination_root=destination,
            ): track
            for track in tracks
        }
        for future in as_completed(futures):
            track = futures[future]
            try:
                future.result()
            except Exception as exc:
                failures.append(PreviewFailure("strip", track.bounce_ulid, str(exc)))
            else:
                rendered += 1

    return StripSummary(
        selected=len(tracks),
        rendered=rendered,
        failures=tuple(sorted(failures, key=lambda item: item.bounce_ulid)),
    )
