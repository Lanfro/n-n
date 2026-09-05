"""SQLite database manager for the Instagram growth pipeline.

Tracks staged and published posts through a defined status machine:

    PENDING_ANALYSIS -> AWAITING_APPROVAL -> APPROVED -> PUBLISHED
                                                         -> FAILED
                                                         -> REJECTED

Only explicit, validated transitions are allowed so that no content
reaches the publisher without a recorded, human-approved state.
"""

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

# Valid post statuses
PENDING_ANALYSIS = "PENDING_ANALYSIS"
AWAITING_APPROVAL = "AWAITING_APPROVAL"
APPROVED = "APPROVED"
PUBLISHED = "PUBLISHED"
FAILED = "FAILED"
REJECTED = "REJECTED"

ALL_STATUSES = {
    PENDING_ANALYSIS,
    AWAITING_APPROVAL,
    APPROVED,
    PUBLISHED,
    FAILED,
    REJECTED,
}

# Allowed transitions: from -> set of to
TRANSITIONS = {
    PENDING_ANALYSIS: {AWAITING_APPROVAL, FAILED, REJECTED},
    AWAITING_APPROVAL: {APPROVED, REJECTED, FAILED, PENDING_ANALYSIS},
    APPROVED: {PUBLISHED, FAILED, PENDING_ANALYSIS},
    PUBLISHED: {FAILED},
    FAILED: {PENDING_ANALYSIS, REJECTED},
    REJECTED: {PENDING_ANALYSIS},
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_key TEXT NOT NULL,
    media_path TEXT NOT NULL,
    status TEXT NOT NULL,
    vision_description TEXT,
    persona_name TEXT,
    reel_text TEXT,
    caption TEXT,
    hashtags TEXT,
    telegram_message_id INTEGER,
    ig_container_id TEXT,
    ig_media_id TEXT,
    meta_error TEXT,
    vault_media_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vault_media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256 TEXT NOT NULL UNIQUE,
    original_filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    media_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    source TEXT NOT NULL,
    telegram_file_id TEXT,
    telegram_message_id INTEGER,
    public_url TEXT,
    added_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channel_sync (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_update_id INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
"""

VAULT_SOURCES = {"drop", "ai_generated", "telegram"}
VAULT_MEDIA_TYPES = {"image", "video"}


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


class DBManager:
    """Thread-safe wrapper around the SQLite posts table."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate_posts_vault_media()
            self._conn.execute(
                """
                INSERT OR IGNORE INTO channel_sync (id, last_update_id, updated_at)
                VALUES (1, 0, ?)
                """,
                (_utcnow(),),
            )
            self._conn.commit()

    def _migrate_posts_vault_media(self) -> None:
        """Add the vault_media_id column to posts if it predates the vault."""
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(posts)").fetchall()
        }
        if "vault_media_id" not in columns:
            self._conn.execute(
                "ALTER TABLE posts ADD COLUMN vault_media_id INTEGER"
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_post(self, account_key: str, media_path: str) -> int:
        """Insert a new post in PENDING_ANALYSIS and return its id."""
        now = _utcnow()
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO posts
                    (account_key, media_path, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (account_key, media_path, PENDING_ANALYSIS, now, now),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def get_post(self, post_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM posts WHERE id = ?", (post_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_posts(self, status: str | None = None) -> list[dict]:
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT * FROM posts WHERE status = ? ORDER BY id",
                    (status,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM posts ORDER BY id"
                ).fetchall()
            return [dict(r) for r in rows]

    def update_content(self, post_id: int, **fields) -> None:
        """Update analysis/generation fields on a post."""
        allowed = {
            "vision_description",
            "persona_name",
            "reel_text",
            "caption",
            "hashtags",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        updates["updated_at"] = _utcnow()
        cols = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [post_id]
        with self._lock:
            self._conn.execute(
                f"UPDATE posts SET {cols} WHERE id = ?", values
            )
            self._conn.commit()

    def transition(self, post_id: int, to_status: str) -> bool:
        """Validate and apply a status transition. Returns False if invalid."""
        if to_status not in ALL_STATUSES:
            raise ValueError(f"Unknown status: {to_status}")
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM posts WHERE id = ?", (post_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Post {post_id} not found")
            current = row["status"]
            if to_status not in TRANSITIONS.get(current, set()):
                return False
            self._conn.execute(
                "UPDATE posts SET status = ?, updated_at = ? WHERE id = ?",
                (to_status, _utcnow(), post_id),
            )
            self._conn.commit()
            return True

    def set_publishing_result(self, post_id: int, **fields) -> None:
        """Record container/media ids or errors from the publisher."""
        allowed = {
            "ig_container_id",
            "ig_media_id",
            "meta_error",
            "telegram_message_id",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        updates["updated_at"] = _utcnow()
        cols = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [post_id]
        with self._lock:
            self._conn.execute(
                f"UPDATE posts SET {cols} WHERE id = ?", values
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Vault media (content-addressed archive)
    # ------------------------------------------------------------------

    def get_vault_media_by_sha256(self, sha256: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM vault_media WHERE sha256 = ?", (sha256,)
            ).fetchone()
            return dict(row) if row else None

    def get_vault_media(self, vault_media_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM vault_media WHERE id = ?", (vault_media_id,)
            ).fetchone()
            return dict(row) if row else None

    def insert_vault_media(
        self,
        *,
        sha256: str,
        original_filename: str,
        stored_path: str,
        media_type: str,
        size_bytes: int,
        source: str,
    ) -> int:
        if source not in VAULT_SOURCES:
            raise ValueError(f"Unknown vault source: {source}")
        if media_type not in VAULT_MEDIA_TYPES:
            raise ValueError(f"Unknown vault media_type: {media_type}")
        now = _utcnow()
        with self._lock:
            existing = self.get_vault_media_by_sha256(sha256)
            if existing:
                return existing["id"]
            cur = self._conn.execute(
                """
                INSERT INTO vault_media
                    (sha256, original_filename, stored_path, media_type,
                     size_bytes, source, added_at, last_used_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sha256,
                    original_filename,
                    stored_path,
                    media_type,
                    size_bytes,
                    source,
                    now,
                    now,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def set_vault_archive(
        self,
        vault_media_id: int,
        *,
        telegram_file_id: str,
        telegram_message_id: int,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE vault_media
                   SET telegram_file_id = ?, telegram_message_id = ?,
                       last_used_at = ?
                 WHERE id = ?
                """,
                (telegram_file_id, telegram_message_id, _utcnow(), vault_media_id),
            )
            self._conn.commit()

    def set_public_url(self, vault_media_id: int, url: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE vault_media
                   SET public_url = ?, last_used_at = ?
                 WHERE id = ?
                """,
                (url, _utcnow(), vault_media_id),
            )
            self._conn.commit()

    def set_post_vault_media(self, post_id: int, vault_media_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE posts SET vault_media_id = ?, updated_at = ? WHERE id = ?",
                (vault_media_id, _utcnow(), post_id),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Channel sync cursor (single row, idempotent)
    # ------------------------------------------------------------------

    def get_latest_vision_for_vault(self, vault_media_id: int) -> str | None:
        """Most recent stored vision description for a vault asset.

        Duplicate media (same sha256) backs the same `vault_media_id`, so a
        later post of the same picture reuses this analysis instead of running
        the vision model again (US4/AC2, data-model.md invariant).
        """
        with self._lock:
            row = self._conn.execute(
                """
                SELECT vision_description FROM posts
                 WHERE vault_media_id = ?
                   AND vision_description IS NOT NULL
                   AND vision_description != ''
                 ORDER BY updated_at DESC, id DESC
                 LIMIT 1
                """,
                (vault_media_id,),
            ).fetchone()
            return row[0] if row else None

    def get_channel_offset(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT last_update_id FROM channel_sync WHERE id = 1"
            ).fetchone()
            return int(row["last_update_id"]) if row else 0

    def set_channel_offset(self, update_id: int) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO channel_sync (id, last_update_id, updated_at)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    last_update_id = excluded.last_update_id,
                    updated_at = excluded.updated_at
                """,
                (update_id, _utcnow()),
            )
            self._conn.commit()
