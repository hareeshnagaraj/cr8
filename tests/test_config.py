from pathlib import Path

import pytest

from conftest import CONFIG_TEMPLATE
from cr8.config import ConfigError, load_config


def _write_config(tmp_path: Path, archive_line: str = "") -> Path:
    root = tmp_path / "corpus"
    root.mkdir()
    text = CONFIG_TEMPLATE.format(root=str(root)).replace(
        "curated_dirs = [\"curated\"]",
        f"{archive_line}\ncurated_dirs = [\"curated\"]",
    )
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_archive_roots_are_optional(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    assert config.corpus.archive_roots == ()


def test_archive_roots_load_as_absolute_paths(tmp_path: Path) -> None:
    first = tmp_path / "2021-New-Projects"
    second = tmp_path / "2022-New-Projects"
    config = load_config(
        _write_config(
            tmp_path,
            f"archive_roots = [{str(first)!r}, {str(second)!r}]",
        )
    )
    assert config.corpus.archive_roots == (first, second)


def test_archive_roots_reject_relative_paths(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="absolute paths"):
        load_config(_write_config(tmp_path, "archive_roots = ['relative']"))


def test_archive_roots_require_unique_directory_names(tmp_path: Path) -> None:
    first = tmp_path / "a" / "archive"
    second = tmp_path / "b" / "archive"
    with pytest.raises(ConfigError, match="unique directory names"):
        load_config(
            _write_config(
                tmp_path,
                f"archive_roots = [{str(first)!r}, {str(second)!r}]",
            )
        )


def test_empty_optional_lists_default_without_raising(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    root.mkdir()
    path = tmp_path / "config.toml"
    path.write_text(
        f"""\
[corpus]
root = {str(root)!r}
project_glob = "* Project"

[vocab]
status = ["idea", "jam", "demo", "mixed", "finished", "released"]
mixrole = ["main", "vox", "novox", "inst", "bass", "gtar", "stems", "acap"]

[audio]
extensions = [".wav", ".mp3", ".m4a", ".aif", ".aiff", ".flac"]
""",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.corpus.curated_dirs == frozenset()
    assert config.corpus.project_extra == frozenset()
    assert config.corpus.other_dirs == frozenset()
    assert config.vocab.known_collabs == frozenset()
