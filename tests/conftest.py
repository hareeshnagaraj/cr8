from __future__ import annotations

from pathlib import Path
import math
import os
import struct
import time
import wave

import pytest

from cr8.config import Config, load_config


CONFIG_TEMPLATE = """\
[corpus]
root = {root!r}
curated_dirs = ["curated"]
project_glob = "* Project"
project_extra = ["Odd Project 2"]
other_dirs = ["other"]

[vocab]
status = ["idea", "jam", "demo", "mixed", "finished", "released"]
mixrole = ["main", "vox", "novox", "inst", "bass", "gtar", "stems", "acap"]
known_collabs = ["henry", "rohiit"]

[audio]
extensions = [".wav", ".mp3", ".m4a", ".aif", ".aiff", ".flac"]
"""


@pytest.fixture
def fixture_config(tmp_path: Path) -> tuple[Config, Path]:
    root = tmp_path / "corpus : fixture"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(CONFIG_TEMPLATE.format(root=str(root)), encoding="utf-8")
    source_keymap = Path(__file__).parents[1] / "keymap.yaml"
    (tmp_path / "keymap.yaml").write_bytes(source_keymap.read_bytes())
    return load_config(config_path), root


def old_audio(path: Path, payload: bytes = b"not-real-audio") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    old = time.time() - 300
    os.utime(path, (old, old))
    return path


def tone_wav(
    path: Path,
    *,
    duration_s: float = 0.25,
    frequency: float = 440.0,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 44_100
    frames = int(sample_rate * duration_s)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(
            b"".join(
                struct.pack(
                    "<h",
                    int(12_000 * math.sin(2 * math.pi * frequency * index / sample_rate)),
                )
                for index in range(frames)
            )
        )
    old = time.time() - 300
    os.utime(path, (old, old))
    return path
