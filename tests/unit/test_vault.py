"""Unit tests for the content-addressed media vault."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.database.db_manager import DBManager
from src.vault.media_vault import MediaNotSupportedError, MediaVault


@pytest.fixture()
def db(tmp_path):
    mgr = DBManager(tmp_path / "test.db")
    yield mgr
    mgr.close()


@pytest.fixture()
def vault(db, tmp_path):
    return MediaVault(db, tmp_path / "vault")


def _make_jpg(tmp_path, name="cat.jpg", content=b"cat picture v1"):
    img = tmp_path / name
    img.write_bytes(content)
    return img


def test_ingest_stores_content_addressed_file(vault, db, tmp_path):
    src = _make_jpg(tmp_path)
    vid = vault.ingest(src)
    row = db.get_vault_media(vid)
    assert row["media_type"] == "image"
    assert row["source"] == "drop"
    stored = Path(row["stored_path"])
    assert stored.exists()
    assert stored.name == f"{row['sha256'][:12]}.jpg"
    expected = Path(datetime.now(UTC).strftime("%Y/%m"))
    assert Path(stored).parent.parts[-2:] == expected.parts


def test_ingest_dedups_by_content(vault, db, tmp_path):
    first = _make_jpg(tmp_path, "a.jpg")
    dup = _make_jpg(tmp_path, "b.jpg")  # same bytes -> same hash
    vid1 = vault.ingest(first)
    vid2 = vault.ingest(dup, source="ai_generated")
    assert vid1 == vid2
    assert db.get_vault_media(vid1)["source"] == "drop"


def test_ingest_delete_source(vault, db, tmp_path):
    src = _make_jpg(tmp_path)
    vault.ingest(src, source="telegram", delete_source=True)
    assert not src.exists()


def test_ingest_missing_file_raises(vault, tmp_path):
    with pytest.raises(RuntimeError):
        vault.ingest(tmp_path / "nope.jpg")


def test_unsupported_extension_raises(vault, db, tmp_path):
    bad = tmp_path / "archive.zip"
    bad.write_bytes(b"x")
    with pytest.raises(MediaNotSupportedError):
        vault.ingest(bad)


def test_video_classified(vault, db, tmp_path):
    clip = tmp_path / "reel.mp4"
    clip.write_bytes(b"video")
    vid = vault.ingest(clip, source="ai_generated")
    assert db.get_vault_media(vid)["media_type"] == "video"


def test_path_of_resolves(vault, db, tmp_path):
    src = _make_jpg(tmp_path)
    vid = vault.ingest(src)
    assert vault.path_of(vid).exists()
    with pytest.raises(RuntimeError):
        vault.path_of(99999)