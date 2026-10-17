"""Regression: the photo pipeline still reaches PUBLISHED under --dry-run.

Hermetic — no vault, Ollama, Telegram, or Meta calls. Proves the Reel feature
did not regress the existing photo path (quickstart scenario 8).
"""

from pathlib import Path

import main as cli
from src.database.db_manager import DBManager

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_photo_pipeline_dry_run_reaches_published(tmp_path):
    db_path = tmp_path / "pipeline.db"
    media = tmp_path / "sample_cat.jpg"
    media.write_bytes(b"placeholder-bytes")  # vault disabled, content not read

    config = {
        "pipeline": {"db_path": str(db_path)},
        "personas": {"config_path": str(REPO_ROOT / "config" / "personas.json")},
    }

    rc = cli.run_dry_run_pipeline(config, "cat_1", str(media))
    assert rc == 0

    posts = DBManager(str(db_path)).list_posts()
    assert len(posts) == 1
    assert posts[0]["status"] == "PUBLISHED"
    assert posts[0]["ig_container_id"].startswith("container_dry_")
    assert posts[0]["ig_media_id"].startswith("media_dry_")
