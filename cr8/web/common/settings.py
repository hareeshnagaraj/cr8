"""Environment-backed settings for the authenticated ASGI application."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat

from ...config import ConfigError, load_config


class SettingsError(RuntimeError):
    """Raised when the web process cannot start safely."""


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise SettingsError(f"{name} must be true or false")


def _positive_int(name: str, default: int) -> int:
    value = _env(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise SettingsError(f"{name} must be positive")
    return parsed


def read_secret(path: Path) -> bytes:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        payload = path.read_bytes().strip()
    except OSError as exc:
        raise SettingsError(f"cannot read session secret {path}: {exc}") from exc
    if mode != 0o600:
        raise SettingsError(f"session secret must be mode 0600: {path}")
    if len(payload) < 32:
        raise SettingsError("session secret must contain at least 32 bytes")
    return payload


def _secret_create_command(path: Path) -> str:
    return (
        f"umask 077 && openssl rand -base64 48 > {path} && chmod 600 {path}"
    )


def ensure_secret(path: Path) -> bytes:
    """Return the session secret, creating it when the file is missing."""
    if path.is_file():
        return read_secret(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = os.urandom(48)
        fd = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
    except OSError as exc:
        raise SettingsError(
            f"cannot create session secret {path}: {exc}\n"
            f"create it with:\n  {_secret_create_command(path)}"
        ) from exc
    return read_secret(path)


def _env(name: str, default: str | None = None) -> str | None:
    """Read CR8_<NAME>, falling back to the old CRATE_<NAME>.

    The rename would otherwise strand anything already running with the old
    variables exported: launchd jobs, a live uvicorn, an open shell. Both names
    work, the new one wins, and the old spelling can be dropped once every
    plist and shell has cycled.
    """
    value = os.environ.get(f"CR8_{name}")
    if value is None:
        value = os.environ.get(f"CRATE_{name}")
    return default if value is None else value


@dataclass(frozen=True)
class AppSettings:
    """Resolved, immutable process settings."""

    app_kind: str
    base_dir: Path
    db_path: Path
    mirror_root: Path
    session_secret: bytes
    cookie_secure: bool = True
    ip_requests_per_minute: int = 240
    corpus_root: Path | None = None
    stems_root: Path | None = None
    # Where invite links should point. Empty means "whatever host the admin is
    # on", which is right until the domain sits behind an Access policy that
    # would turn away the very person being invited.
    public_base_url: str = ""
    archive_roots: tuple[Path, ...] = ()

    @property
    def cookie_name(self) -> str:
        return f"crate_{self.app_kind}_sid"

    @property
    def static_root(self) -> Path:
        return Path(__file__).parent / "static"

    @property
    def resolved_corpus_root(self) -> Path:
        return self.corpus_root or self.base_dir

    @property
    def resolved_stems_root(self) -> Path:
        return self.stems_root or self.base_dir / "stems"

    @classmethod
    def from_env(cls, app_kind: str) -> "AppSettings":
        if app_kind != "owner":
            raise SettingsError("app_kind must be owner")
        base = Path(_env("BASE_DIR", ".")).expanduser().resolve()
        db_path = Path(
            _env("DB_PATH", str(base / "catalog.db"))
        ).expanduser().resolve()
        mirror_root = Path(
            _env("MIRROR_ROOT", str(base / "mirror"))
        ).expanduser().resolve()
        secret_path = Path(
            _env("SECRET_FILE", str(base / "secrets" / f"{app_kind}-session.key"),
            )
        ).expanduser().resolve()
        corpus_value = _env("CORPUS_ROOT")
        configured = None
        if corpus_value:
            corpus_root = Path(corpus_value).expanduser().resolve()
            try:
                configured = load_config(base / "config.toml")
            except ConfigError:
                pass
        else:
            try:
                configured = load_config(base / "config.toml")
                corpus_root = configured.corpus.root
            except ConfigError as exc:
                raise SettingsError(
                    "CR8_CORPUS_ROOT is required when config.toml is unavailable"
                ) from exc
        stems_root = Path(
            _env("STEMS_ROOT", str(base / "stems"))
        ).expanduser().resolve()
        return cls(
            app_kind=app_kind,
            base_dir=base,
            db_path=db_path,
            mirror_root=mirror_root,
            session_secret=ensure_secret(secret_path),
            cookie_secure=_env_bool("COOKIE_SECURE", True),
            ip_requests_per_minute=_positive_int(
                "IP_REQUESTS_PER_MINUTE", 240
            ),
            public_base_url=_env("PUBLIC_BASE_URL", "") or "",
            corpus_root=corpus_root,
            stems_root=stems_root,
            archive_roots=(
                configured.corpus.archive_roots if configured is not None else ()
            ),
        )
