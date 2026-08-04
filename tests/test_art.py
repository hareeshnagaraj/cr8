from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

from PIL import Image
import pytest

from cr8.art import (
    CAMELOT_HUE_ORIGIN,
    CAMELOT_HUE_STEP,
    _contrast_stretch,
    _color_ramp,
    _stretch_image,
    camelot_hue,
    render_envelope_preview,
    render_spectral_preview,
    render_spectral_strip,
)
from cr8.cli import main
from cr8.db import connect
from cr8.tooling import find_tool


FFMPEG = find_tool("ffmpeg")
requires_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg required")


@pytest.mark.parametrize(
    ("camelot", "expected"),
    [("1A", 195), ("5A", 87), ("8A", 6), ("12B", 258), ("nope", None)],
)
def test_camelot_hue_matches_the_typescript_wheel(camelot, expected):
    assert camelot_hue(camelot) == expected


def test_camelot_hue_constants_match_typescript():
    """One Python home + one TS home; ORIGIN/STEP must stay pinned equal."""
    assert CAMELOT_HUE_ORIGIN == 195
    assert CAMELOT_HUE_STEP == 27
    colors_ts = Path("web/lib/colors.ts").read_text(encoding="utf-8")
    assert f"export const CAMELOT_HUE_ORIGIN = {CAMELOT_HUE_ORIGIN};" in colors_ts
    assert f"export const CAMELOT_HUE_STEP = {CAMELOT_HUE_STEP};" in colors_ts
    assert "export function camelotHue(" in colors_ts
    cover_ts = Path("web/lib/cover.ts").read_text(encoding="utf-8")
    assert "195 - (position" not in cover_ts
    assert "CAMELOT_BY_KEY" not in cover_ts
    assert "from \"@/lib/colors\"" in cover_ts or "from '@/lib/colors'" in cover_ts


@pytest.mark.parametrize(
    ("camelot", "expected"),
    [
        ("8A", ((14, 14, 16), (205, 89, 76), (241, 209, 205))),
        (None, ((14, 14, 16), (150, 153, 161), (240, 241, 245))),
    ],
)
def test_color_ramp_is_anchored_to_the_camelot_hue(camelot, expected):
    assert _color_ramp(camelot, None) == expected


def test_histogram_stretch_matches_the_percentile_reference():
    image = Image.new("L", (32, 16))
    image.putdata([(index * 17) % 256 for index in range(512)])
    expected = bytes(
        _contrast_stretch([float(value) for value in image.tobytes()])
    )
    assert _stretch_image(image).tobytes() == expected


def _synth_audio(path: Path) -> None:
    subprocess.run(
        [
            str(FFMPEG),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=2",
            "-f",
            "lavfi",
            "-i",
            "anoisesrc=color=white:duration=0.25:amplitude=0.35",
            "-filter_complex",
            "[0:a][1:a]amix=inputs=2:duration=first",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(path),
        ],
        check=True,
    )


def _write_peaks(path: Path) -> None:
    data: list[int] = []
    for index in range(80):
        amplitude = 8 + (index % 17) * 7
        data.extend((-amplitude, amplitude))
    path.write_text(json.dumps({"version": 2, "bits": 8, "data": data}))


def _assert_jpeg(path: Path, expected_size: tuple[int, int]) -> bytes:
    payload = path.read_bytes()
    assert payload.startswith(b"\xff\xd8") and payload.endswith(b"\xff\xd9")
    with Image.open(path) as image:
        assert image.format == "JPEG"
        assert image.size == expected_size
    return payload


@requires_ffmpeg
def test_both_preview_styles_are_valid_and_deterministic(tmp_path):
    audio = tmp_path / "fixture.mp3"
    peaks = tmp_path / "fixture.json"
    spectral = tmp_path / "spectral.jpg"
    spectral_again = tmp_path / "spectral-again.jpg"
    envelope = tmp_path / "envelope.jpg"
    envelope_again = tmp_path / "envelope-again.jpg"
    _synth_audio(audio)
    _write_peaks(peaks)

    render_spectral_preview(
        audio,
        spectral,
        camelot="8A",
        era_color="#3155aa",
        ffmpeg=FFMPEG,
    )
    render_envelope_preview(
        peaks,
        envelope,
        camelot="8A",
        era_color="#3155aa",
    )
    first_spectral = _assert_jpeg(spectral, (512, 512))
    first_envelope = _assert_jpeg(envelope, (256, 256))

    render_spectral_preview(
        audio,
        spectral_again,
        camelot="8A",
        era_color="#3155aa",
        ffmpeg=FFMPEG,
    )
    render_envelope_preview(
        peaks,
        envelope_again,
        camelot="8A",
        era_color="#3155aa",
    )
    assert _assert_jpeg(spectral_again, (512, 512)) == first_spectral
    assert _assert_jpeg(envelope_again, (256, 256)) == first_envelope


@requires_ffmpeg
def test_spectral_strip_is_valid_and_deterministic(tmp_path):
    audio = tmp_path / "fixture.mp3"
    strip = tmp_path / "strip.jpg"
    strip_again = tmp_path / "strip-again.jpg"
    _synth_audio(audio)

    render_spectral_strip(
        audio,
        strip,
        camelot="8A",
        era_color="#3155aa",
        ffmpeg=FFMPEG,
    )
    first = _assert_jpeg(strip, (2048, 256))

    render_spectral_strip(
        audio,
        strip_again,
        camelot="8A",
        era_color="#3155aa",
        ffmpeg=FFMPEG,
    )
    assert _assert_jpeg(strip_again, (2048, 256)) == first


@requires_ffmpeg
def test_spectral_strip_rejects_a_destination_outside_its_artifact_directory(
    tmp_path,
):
    mirror = tmp_path / "mirror"
    audio = mirror / "tracks" / "fixture.mp3"
    art_strips = mirror / "art-strips"
    audio.parent.mkdir(parents=True)
    art_strips.mkdir()
    _synth_audio(audio)

    with pytest.raises(
        RuntimeError,
        match="spectral destination escapes its artifact directory",
    ):
        render_spectral_strip(
            audio,
            art_strips / ".." / "escaped.jpg",
            camelot="8A",
            era_color=None,
            ffmpeg=FFMPEG,
            source_root=mirror,
            destination_root=art_strips,
        )


@requires_ffmpeg
def test_cli_limit_writes_preview_and_strip_art_without_touching_live_art(
    fixture_config, capsys
):
    config, _ = fixture_config
    mirror = config.state_dir / "mirror"
    for name in ("tracks", "peaks", "art"):
        (mirror / name).mkdir(parents=True, exist_ok=True)
    live_art = mirror / "art" / "live.jpg"
    live_art.write_bytes(b"live-art-must-not-change")
    audio = mirror / "tracks" / "first.mp3"
    _synth_audio(audio)

    connection = connect(config.db_path)
    try:
        for index, bounce_ulid in enumerate(("first", "second"), start=1):
            connection.execute(
                """
                INSERT INTO songs(slug, title, public_id, key_camelot)
                VALUES(?, ?, ?, '8A')
                """,
                (f"song-{index}", f"Song {index}", f"song-{index}"),
            )
            song_id = int(
                connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO bounces(public_id, song_id, source_stem)
                VALUES(?, ?, ?)
                """,
                (bounce_ulid, song_id, bounce_ulid),
            )
            bounce_id = int(
                connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            )
            track = mirror / "tracks" / f"{bounce_ulid}.mp3"
            if index == 2:
                shutil.copyfile(audio, track)
            connection.execute(
                "INSERT INTO mirror_files(bounce_id, mirror_relpath) VALUES(?, ?)",
                (bounce_id, f"tracks/{bounce_ulid}.mp3"),
            )
            _write_peaks(mirror / "peaks" / f"{bounce_ulid}.json")
    finally:
        connection.close()

    assert main(
        [
            "--config",
            str(config.path),
            "render-cover-previews",
            "--limit",
            "1",
            "--workers",
            "2",
        ]
    ) == 0
    output = capsys.readouterr().out
    assert "spectral: 1 rendered, 0 failed" in output
    assert "envelope: 1 rendered, 0 failed" in output
    assert len(list((mirror / "art-preview" / "spectral").glob("*.jpg"))) == 1
    assert len(list((mirror / "art-preview" / "envelope").glob("*.jpg"))) == 1

    assert main(
        [
            "--config",
            str(config.path),
            "render-strips",
            "--limit",
            "1",
            "--workers",
            "2",
        ]
    ) == 0
    output = capsys.readouterr().out
    assert "render-strips: 1 rendered, 0 failed" in output
    assert len(list((mirror / "art-strips").glob("*.jpg"))) == 1

    escaped_audio = mirror.parent / "outside.mp3"
    shutil.copyfile(audio, escaped_audio)
    connection = connect(config.db_path)
    try:
        connection.execute(
            """
            UPDATE mirror_files SET mirror_relpath='../outside.mp3'
            WHERE bounce_id=(SELECT id FROM bounces WHERE public_id='second')
            """
        )
    finally:
        connection.close()
    assert main(
        [
            "--config",
            str(config.path),
            "render-strips",
            "--workers",
            "2",
        ]
    ) == 0
    output = capsys.readouterr().out
    assert "render-strips: 1 rendered, 1 failed" in output
    assert "render-strips: second: spectral source escapes the mirror root" in output
    assert len(list((mirror / "art-strips").glob("*.jpg"))) == 1
    assert live_art.read_bytes() == b"live-art-must-not-change"
    assert not list(mirror.rglob("*.tmp.*"))


def test_preview_renderer_does_not_import_numpy():
    source = Path("cr8/art.py").read_text(encoding="utf-8").casefold()
    assert "numpy" not in source


def test_live_cover_uses_the_envelope_when_peaks_exist(tmp_path):
    from cr8.mirror import generate_cover_bytes

    peaks = tmp_path / "peaks.json"
    peaks.write_text(json.dumps({"data": [0, 40, -60, 90, -30, 120] * 40}))
    with_peaks = generate_cover_bytes(
        "Bake Off", era_color="#c9d64f", camelot="5A", peaks_path=peaks
    )
    gradient = generate_cover_bytes("Bake Off", era_color="#c9d64f")
    assert with_peaks != gradient
    assert with_peaks[:2] == b"\xff\xd8"
    # A missing or unreadable peaks file must degrade to the gradient, never
    # fail the build.
    fallback = generate_cover_bytes(
        "Bake Off", era_color="#c9d64f", camelot="5A",
        peaks_path=tmp_path / "absent.json",
    )
    assert fallback == gradient
