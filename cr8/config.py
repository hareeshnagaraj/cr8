"""Configuration loading and validation."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
import tomllib


class ConfigError(ValueError):
    """Raised when config.toml is invalid."""


@dataclass(frozen=True)
class CorpusConfig:
    root: Path
    curated_dirs: frozenset[str]
    project_glob: str
    project_extra: frozenset[str]
    other_dirs: frozenset[str]
    # Uploads land here, deliberately outside the corpus: that directory is a
    # read-only mirror another machine syncs, and writing into it would put our
    # files at the mercy of that sync. Defaults to <state dir>/drops.
    drops_root: Path | None = None
    # Static, read-only corpus roots that follow the same curation rules as the
    # primary root but are not part of its live sync.
    archive_roots: tuple[Path, ...] = ()

    def is_project_name(self, name: str) -> bool:
        return fnmatch(name, self.project_glob) or name in self.project_extra

    @property
    def resolved_drops_root(self) -> Path:
        if self.drops_root is not None:
            return self.drops_root
        raise ConfigError("drops root is not configured")


@dataclass(frozen=True)
class VocabConfig:
    status: tuple[str, ...]
    mixrole: tuple[str, ...]
    known_collabs: frozenset[str]


@dataclass(frozen=True)
class AudioConfig:
    extensions: frozenset[str]


@dataclass(frozen=True)
class AutomationConfig:
    healthcheck_url: str | None
    monthly_healthcheck_url: str | None
    owner_url: str


@dataclass(frozen=True)
class Config:
    path: Path
    corpus: CorpusConfig
    vocab: VocabConfig
    audio: AudioConfig
    automation: AutomationConfig

    @property
    def state_dir(self) -> Path:
        return self.path.parent

    @property
    def db_path(self) -> Path:
        return self.state_dir / "catalog.db"

    @property
    def keymap_path(self) -> Path:
        return self.state_dir / "keymap.yaml"


def _string_list(
    section: dict[str, object],
    key: str,
    *,
    default: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    if key not in section:
        if default is not None:
            return default
        raise ConfigError(f"{key} must be a list of strings")
    value = section[key]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{key} must be a list of strings")
    return tuple(value)


def _optional_string(
    section: dict[str, object],
    key: str,
    *,
    default: str | None = None,
) -> str | None:
    value = section.get(key, default)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{key} must be a string")
    return value


def load_config(path: str | Path = "config.toml") -> Config:
    config_path = Path(path).expanduser().resolve()
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot load {config_path}: {exc}") from exc

    try:
        corpus_raw = data["corpus"]
        vocab_raw = data["vocab"]
        audio_raw = data["audio"]
    except KeyError as exc:
        raise ConfigError(f"missing section: {exc.args[0]}") from exc
    if not all(isinstance(section, dict) for section in (corpus_raw, vocab_raw, audio_raw)):
        raise ConfigError("corpus, vocab, and audio must be tables")
    automation_raw = data.get("automation", {})
    if not isinstance(automation_raw, dict):
        raise ConfigError("automation must be a table")

    root_value = corpus_raw.get("root")
    project_glob = corpus_raw.get("project_glob")
    if not isinstance(root_value, str) or not root_value:
        raise ConfigError("corpus.root must be a non-empty string")
    if not isinstance(project_glob, str) or not project_glob:
        raise ConfigError("corpus.project_glob must be a non-empty string")

    status = _string_list(vocab_raw, "status")
    mixrole = _string_list(vocab_raw, "mixrole")
    extensions = tuple(ext.casefold() for ext in _string_list(audio_raw, "extensions"))
    if len(status) != len(set(status)) or len(mixrole) != len(set(mixrole)):
        raise ConfigError("vocabulary values must be unique")
    if any(not ext.startswith(".") for ext in extensions):
        raise ConfigError("audio extensions must begin with '.'")

    drops_value = corpus_raw.get("drops_root")
    if drops_value is not None and not isinstance(drops_value, str):
        raise ConfigError("corpus.drops_root must be a string")
    drops_root = (
        Path(drops_value).expanduser()
        if drops_value
        else config_path.parent / "drops"
    )
    archive_values = corpus_raw.get("archive_roots", [])
    if not isinstance(archive_values, list) or not all(
        isinstance(item, str) for item in archive_values
    ):
        raise ConfigError("archive_roots must be a list of strings")
    archive_roots = tuple(Path(item).expanduser() for item in archive_values)
    if any(not root.is_absolute() for root in archive_roots):
        raise ConfigError("archive_roots must contain absolute paths")
    archive_names = [root.name.casefold() for root in archive_roots]
    if any(not name for name in archive_names) or len(archive_names) != len(
        set(archive_names)
    ):
        raise ConfigError("archive_roots must have unique directory names")

    return Config(
        path=config_path,
        corpus=CorpusConfig(
            root=Path(root_value).expanduser(),
            curated_dirs=frozenset(
                _string_list(corpus_raw, "curated_dirs", default=())
            ),
            project_glob=project_glob,
            project_extra=frozenset(
                _string_list(corpus_raw, "project_extra", default=())
            ),
            other_dirs=frozenset(
                _string_list(corpus_raw, "other_dirs", default=())
            ),
            drops_root=drops_root,
            archive_roots=archive_roots,
        ),
        vocab=VocabConfig(
            status=status,
            mixrole=mixrole,
            known_collabs=frozenset(
                value.casefold()
                for value in _string_list(vocab_raw, "known_collabs", default=())
            ),
        ),
        audio=AudioConfig(extensions=frozenset(extensions)),
        automation=AutomationConfig(
            healthcheck_url=_optional_string(automation_raw, "healthcheck_url"),
            monthly_healthcheck_url=_optional_string(
                automation_raw, "monthly_healthcheck_url"
            ),
            owner_url=_optional_string(
                automation_raw,
                "owner_url",
                default="http://127.0.0.1:8080/healthz",
            )
            or "http://127.0.0.1:8080/healthz",
        ),
    )
