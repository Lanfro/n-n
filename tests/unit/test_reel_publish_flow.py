"""Integration tests for the approval-gated Reel publish flow in main.py.

The clip validator is stubbed (ffmpeg/ffprobe may be absent) so the tests focus
on the orchestration: validate -> AWAITING_APPROVAL -> gate -> PUBLISHED, the
brief-id deduplication, and retry-reuse semantics. Publishing runs in dry-run
mode, so no network or browser is touched.
"""

import json
from pathlib import Path

import main as cli
from src.database.db_manager import DBManager
from src.reel.models import (
    VALIDATION_REJECTED,
    VALIDATION_VALID,
    ClipReport,
    ReelBrief,
)


def _write_brief(out: Path) -> ReelBrief:
    brief = ReelBrief(
        brief_id="s:0",
        set="s",
        shot_index=0,
        account_key="cat_1",
        cat="nero",
        motion_prompt="blinks slowly",
        source_photo_path=str(out / "src.jpg"),
        source_vault_media_id=1,
        expected_output="reel_00.mp4",
        caption="A grumpy cat surveys its realm.",
        reel_text="Reign of Grump",
        hashtags=["#cat", "#reels"],
        created_at="now",
    )
    (out / "reel_brief_00.json").write_text(
        json.dumps(brief.to_dict()), encoding="utf-8"
    )
    return brief


def _setup(tmp_path: Path) -> dict:
    out = tmp_path / "generated" / "s"
    out.mkdir(parents=True)
    _write_brief(out)
    (out / "reel_00.mp4").write_bytes(b"clip-bytes")
    return {
        "creative": {"out_root": str(tmp_path / "generated")},
        "pipeline": {"db_path": str(tmp_path / "pipeline.db")},
        "reel": {
            "min_resolution": [540, 960],
            "min_duration_seconds": 3,
            "max_duration_seconds": 900,
            "max_size_mb": 300,
            "publish": {"share_to_feed": True},
        },
        "meta": {"dry_run": True},
    }


def _valid(path: Path) -> ClipReport:
    return ClipReport(
        path=str(path),
        brief_id="s:0",
        container="mp4",
        video_codec="h264",
        audio_codec="aac",
        has_audio=True,
        width=1080,
        height=1920,
        aspect_ratio=0.5625,
        duration_seconds=6.0,
        fps=30.0,
        size_bytes=1000,
        validation=VALIDATION_VALID,
    )


def test_dry_run_publish_reaches_published(tmp_path, monkeypatch):
    cfg = _setup(tmp_path)
    monkeypatch.setattr(
        cli, "_validate_reel_clip", lambda path, **k: _valid(Path(path))
    )
    assert cli.run_reel_publish(cfg, "s", "s:0", dry_run=True) == 0

    post = DBManager(cfg["pipeline"]["db_path"]).get_reel_post_by_brief(
        "cat_1", "s:0"
    )
    assert post is not None
    assert post["status"] == "PUBLISHED"
    assert post["media_type"] == "reel"
    assert post["ig_media_id"].startswith("media_dry_container_dry_s:0")
    assert post["ig_container_id"] == "container_dry_s:0"


def test_second_publish_is_deduplicated(tmp_path, monkeypatch):
    cfg = _setup(tmp_path)
    monkeypatch.setattr(
        cli, "_validate_reel_clip", lambda path, **k: _valid(Path(path))
    )
    assert cli.run_reel_publish(cfg, "s", "s:0", dry_run=True) == 0
    assert cli.run_reel_publish(cfg, "s", "s:0", dry_run=True) == 0

    posts = [
        p
        for p in DBManager(cfg["pipeline"]["db_path"]).list_posts()
        if p["media_type"] == "reel"
    ]
    assert len(posts) == 1


def test_retry_reuses_failed_post(tmp_path, monkeypatch):
    cfg = _setup(tmp_path)
    db = DBManager(cfg["pipeline"]["db_path"])
    post_id = db.create_reel_post("cat_1", "clip", "s:0")
    db.transition(post_id, "AWAITING_APPROVAL")
    db.transition(post_id, "FAILED")

    monkeypatch.setattr(
        cli, "_validate_reel_clip", lambda path, **k: _valid(Path(path))
    )
    assert cli.run_reel_publish(cfg, "s", "s:0", dry_run=True) == 0

    posts = [
        p for p in db.list_posts() if p["media_type"] == "reel"
    ]
    assert len(posts) == 1
    assert posts[0]["status"] == "PUBLISHED"


def test_rejected_clip_never_offered(tmp_path, monkeypatch):
    cfg = _setup(tmp_path)
    monkeypatch.setattr(
        cli,
        "_validate_reel_clip",
        lambda path, **k: ClipReport(
            path=str(path),
            brief_id="s:0",
            validation=VALIDATION_REJECTED,
            reject_reason="not portrait (cannot satisfy 9:16)",
        ),
    )
    assert cli.run_reel_publish(cfg, "s", "s:0", dry_run=True) == 1

    post = DBManager(cfg["pipeline"]["db_path"]).get_reel_post_by_brief(
        "cat_1", "s:0"
    )
    assert post["status"] in {"REJECTED", "PENDING_ANALYSIS"}
    assert post["status"] != "PUBLISHED"


def test_unknown_brief_returns_2(tmp_path, monkeypatch):
    cfg = _setup(tmp_path)
    monkeypatch.setattr(
        cli, "_validate_reel_clip", lambda path, **k: _valid(Path(path))
    )
    assert cli.run_reel_publish(cfg, "s", "s:99", dry_run=True) == 2
