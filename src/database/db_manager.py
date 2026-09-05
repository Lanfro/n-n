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
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


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
            self._conn.commit()

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
