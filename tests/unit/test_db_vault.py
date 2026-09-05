"""Unit tests for the vault + channel-sync DB layer."""

import sqlite3

import pytest

from src.database.db_manager import DBManager

SAMPLE_SHA = "a" * 64
OTHER_SHA = "b" * 64


@pytest.fixture()
def db(tmp_path):
    mgr = DBManager(tmp_path / "test.db")
    yield mgr
    mgr.close()


def _insert_media(db: DBManager, sha: str = SAMPLE_SHA, source: str = "drop") -> int:
    return db.insert_vault_media(
        sha256=sha,
        original_filename="cat.jpg",
        stored_path=f"data/vault/2026/09/{sha[:12]}.jpg",
        media_type="image",
        size_bytes=1234,
        source=source,
    )


def test_insert_creates_row(db):
    vid = _insert_media(db)
    row = db.get_vault_media(vid)
    assert row["sha256"] == SAMPLE_SHA
    assert row["source"] == "drop"
    assert row["public_url"] is None
    assert row["telegram_file_id"] is None


def test_insert_dedups_by_sha256(db):
    first = _insert_media(db)
    second = _insert_media(db, source="ai_generated")
    assert first == second
    assert db.get_vault_media_by_sha256(SAMPLE_SHA)["source"] == "drop"


def test_lookup_by_sha256_missing(db):
    assert db.get_vault_media_by_sha256(OTHER_SHA) is None


def test_set_vault_archive(db):
    vid = _insert_media(db)
    db.set_vault_archive(vid, telegram_file_id="file_123", telegram_message_id=9)
    row = db.get_vault_media(vid)
    assert row["telegram_file_id"] == "file_123"
    assert row["telegram_message_id"] == 9


def test_set_public_url(db):
    vid = _insert_media(db)
    db.set_public_url(vid, "https://vault.example.dev/media/ab/cd/ab.jpg")
    assert db.get_vault_media(vid)["public_url"].startswith("https://")


def test_invalid_source_rejected(db):
    with pytest.raises(ValueError):
        _insert_media(db, source="bogus")


def test_set_post_vault_media_links(db):
    vid = _insert_media(db)
    post_id = db.create_post("cat_1", "data/input_media/cat.jpg")
    db.set_post_vault_media(post_id, vid)
    assert db.get_post(post_id)["vault_media_id"] == vid


def test_channel_offset_roundtrip(db):
    assert db.get_channel_offset() == 0
    db.set_channel_offset(42)
    assert db.get_channel_offset() == 42
    db.set_channel_offset(77)
    assert db.get_channel_offset() == 77


def test_existing_posts_table_is_migrated(tmp_path):
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        """
        CREATE TABLE posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_key TEXT NOT NULL,
            media_path TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO posts (account_key, media_path, status, created_at, updated_at)"
        " VALUES ('cat_1', 'x.jpg', 'PENDING_ANALYSIS', 'now', 'now')"
    )
    conn.commit()
    conn.close()

    mgr = DBManager(db_file)
    try:
        columns = {
            row["name"]
            for row in mgr._conn.execute("PRAGMA table_info(posts)").fetchall()
        }
        assert "vault_media_id" in columns
        assert mgr.get_post(1)["vault_media_id"] is None
    finally:
        mgr.close()