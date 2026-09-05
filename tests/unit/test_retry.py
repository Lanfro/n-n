"""Unit tests for retrying a FAILED post without redoing completed work."""

import pytest

from main import run_retry
from src.database.db_manager import DBManager


@pytest.fixture()
def db(tmp_path):
    mgr = DBManager(tmp_path / "test.db")
    yield mgr
    mgr.close()


@pytest.fixture()
def config(tmp_path, db):
    return {
        "pipeline": {"db_path": str(db.db_path)},
        "meta": {"dry_run": True},
        "telegram": {},
        "approval": {},
        "personas": {"config_path": "config/personas.json"},
        "ollama": {},
    }


def _failed_post(db, tmp_path, **content):
    media = tmp_path / "cat.jpg"
    media.write_bytes(b"cat")
    post_id = db.create_post("cat_1", str(media))
    if content:
        db.update_content(post_id, **content)
    assert db.transition(post_id, "FAILED")
    return post_id, str(media)


def test_retry_resumes_same_row_without_reanalysis(db, config, tmp_path):
    vid = db.insert_vault_media(
        sha256="c" * 64,
        original_filename="cat.jpg",
        stored_path=str(tmp_path / "cat.jpg"),
        media_type="image",
        size_bytes=3,
        source="drop",
    )
    post_id, _media_path = _failed_post(
        db,
        tmp_path,
        vision_description="already analyzed",
        caption="stored caption",
        hashtags="cat,grimalkin",
    )
    db.set_post_vault_media(post_id, vid)

    assert run_retry(config, post_id, dry_run=True) == 0
    post = db.get_post(post_id)
    assert post["id"] == post_id
    assert post["status"] == "PUBLISHED"
    assert post["vision_description"] == "already analyzed"
    assert "stored caption" in post["caption"]
    assert post["vault_media_id"] == vid


def test_retry_rejects_non_failed_post(db, config, tmp_path):
    media = tmp_path / "cat.jpg"
    media.write_bytes(b"cat")
    post_id = db.create_post("cat_1", str(media))
    assert run_retry(config, post_id, dry_run=True) == 2
    assert db.get_post(post_id)["status"] == "PENDING_ANALYSIS"


def test_retry_missing_post(db, config):
    assert run_retry(config, 9999, dry_run=True) == 2


def test_retry_without_draft_generates_dry_caption(db, config, tmp_path):
    post_id, _ = _failed_post(db, tmp_path, vision_description="vision only")
    assert run_retry(config, post_id, dry_run=True) == 0
    post = db.get_post(post_id)
    assert post["status"] == "PUBLISHED"
    assert "Mocked retry caption" in post["caption"]


def test_retry_missing_media_fails(db, config, tmp_path):
    post_id = db.create_post("cat_1", str(tmp_path / "gone.jpg"))
    assert db.transition(post_id, "FAILED")
    assert run_retry(config, post_id, dry_run=True) == 2