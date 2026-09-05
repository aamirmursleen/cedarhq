from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .config import BASE_DIR, config


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or config.database_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def migrate(path: Path | None = None) -> None:
    migrations_dir = BASE_DIR / "migrations"
    with transaction(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version TEXT PRIMARY KEY,
              applied_at TEXT NOT NULL
            )
            """
        )
        applied = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}
        for migration in sorted(migrations_dir.glob("*.sql")):
            version = migration.stem
            if version in applied:
                continue
            conn.executescript(migration.read_text())
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, utcnow()),
            )

