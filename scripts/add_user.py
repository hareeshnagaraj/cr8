"""Add a full-access user to the app.

Usage:  .venv/bin/python scripts/add_user.py <username> "<Display Name>"

Writes a generated password to secrets/<username>-password.txt (0600) and
prints it once. Full access — same rights as the owner.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cr8.web.common.auth import AuthError, create_member  # noqa: E402
from cr8.web.common.database import reading  # noqa: E402
from cr8.web.common.settings import AppSettings  # noqa: E402

BASE = Path(__file__).resolve().parents[1]


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    settings = AppSettings(
        "owner",
        BASE,
        BASE / "catalog.db",
        BASE / "mirror",
        b"add-user-script-session-secret",
    )
    try:
        member = create_member(
            settings,
            username=sys.argv[1],
            display=sys.argv[2],
        )
    except AuthError as exc:
        print(f"could not create user: {exc}", file=sys.stderr)
        return 1

    secrets_dir = BASE / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    target = secrets_dir / f"{member.username}-password.txt"
    old_umask = os.umask(0o077)
    try:
        target.write_text(member.password + "\n")
    finally:
        os.umask(old_umask)
    target.chmod(0o600)

    with reading(settings.db_path) as connection:
        rows = list(
            connection.execute(
                "SELECT username, display, role FROM users"
            )
        )

    print(f"created: {member.username} ({member.display})")
    print(f"password written to: {target}")
    print(f"users now: {rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
