"""Tests for per-asset vault analysis storage and the batch describe tool."""

from pathlib import Path

from src.database.db_manager import DBManager
from src.vault.media_vault import MediaVault


def _make_image(tmp_path: Path, name: str, content: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def _setup(config_db_path: Path, tmp_path: Path) -> DBManager:
    db = DBManager(config_db_path)
    vault = MediaVault(db, tmp_path / "vault")
    image = _make_image(tmp_path, "cat_a.jpg", b"img-a")
    video = _make_image(tmp_path, "cat_b.mp4", b"vid-b")
    image_id = vault.ingest(image, source="drop")
    video_id = vault.ingest(video, source="drop")
    return db, image_id, video_id


def test_list_vault_media_filters_by_type(tmp_path) -> None:
    db = DBManager(tmp_path / "pipeline.db")
    vault = MediaVault(db, tmp_path / "vault")
    vault.ingest(_make_image(tmp_path, "a.jpg", b"a"), source="drop")
    vault.ingest(_make_image(tmp_path, "b.mp4", b"b"), source="drop")
    images = db.list_vault_media("image")
    videos = db.list_vault_media("video")
    assert [a["media_type"] for a in images] == ["image"]
    assert [a["media_type"] for a in videos] == ["video"]
    assert len(db.list_vault_media()) == 2


def test_upsert_vault_analysis_and_get(tmp_path) -> None:
    db, image_id, _ = _setup(tmp_path / "pipeline.db", tmp_path)
    db.upsert_vault_analysis(
        vault_media_id=image_id,
        model="qwen3-vl:8b",
        prompt="Describe this cat.",
        description="A flat-faced cat judging the room.",
    )
    row = db.get_vault_analysis(image_id, "qwen3-vl:8b")
    assert row is not None
    assert row["description"] == "A flat-faced cat judging the room."

    db.upsert_vault_analysis(
        vault_media_id=image_id,
        model="qwen3-vl:8b",
        prompt="Describe this cat.",
        description="Updated description.",
    )
    assert db.get_vault_analysis(image_id, "qwen3-vl:8b")["description"] == (
        "Updated description."
    )
    assert db.get_vault_analysis(image_id, "other-model") is None


def test_get_latest_vision_for_vault_falls_back_to_analysis(tmp_path) -> None:
    db, image_id, _ = _setup(tmp_path / "pipeline.db", tmp_path)
    assert db.get_latest_vision_for_vault(image_id) is None

    db.upsert_vault_analysis(
        vault_media_id=image_id,
        model="qwen3-vl:8b",
        description="From the analysis table.",
    )
    assert db.get_latest_vision_for_vault(image_id) == "From the analysis table."

    post_id = db.create_post("cat_1", "media.jpg")
    db.set_post_vault_media(post_id, image_id)
    db.update_content(post_id, vision_description="From the post.")
    assert db.get_latest_vision_for_vault(image_id) == "From the post."


def test_run_describe_vault_batch(tmp_path, monkeypatch) -> None:
    import main

    db = DBManager(tmp_path / "pipeline.db")
    vault = MediaVault(db, tmp_path / "vault")
    vault.ingest(
        _make_image(tmp_path, "cat_a.jpg", b"img-a"), source="telegram"
    )
    vault.ingest(
        _make_image(tmp_path, "video.mp4", b"vid-b"), source="telegram"
    )
    db.close()

    config = {
        "pipeline": {
            "db_path": str(tmp_path / "pipeline.db"),
            "descriptions_report": str(tmp_path / "descriptions.md"),
        },
        "ollama": {
            "base_url": "http://localhost:11434",
            "vision_model": "qwen3-vl:8b",
            "timeout_seconds": 30,
        },
    }
    monkeypatch.setattr(
        main.VisualAnalyzer,
        "analyze",
        lambda self, path, prompt: f"description-of-{Path(path).name}",
    )
    code = main.run_describe_vault(config)
    assert code == 0

    db = DBManager(tmp_path / "pipeline.db")
    assets = db.list_vault_media("image")
    assert len(assets) == 1
    analysis = db.get_vault_analysis(assets[0]["id"], "qwen3-vl:8b")
    assert analysis is not None
    stored_name = Path(assets[0]["stored_path"]).name
    assert analysis["description"] == f"description-of-{stored_name}"
    videos = db.list_vault_media("video")
    assert db.get_vault_analysis(videos[0]["id"]) is None

    report = (tmp_path / "descriptions.md").read_text(encoding="utf-8")
    assert "cat_a.jpg" in report  # original filename in the header
    assert "description-of-" in report
    assert "qwen3-vl:8b" in report
    db.close()


def test_run_describe_vault_is_idempotent(tmp_path, monkeypatch) -> None:
    import main

    config = {
        "pipeline": {
            "db_path": str(tmp_path / "pipeline.db"),
            "descriptions_report": str(tmp_path / "descriptions.md"),
        },
        "ollama": {"vision_model": "qwen3-vl:8b"},
    }
    monkeypatch.setattr(
        main.VisualAnalyzer,
        "analyze",
        lambda self, path, prompt: "canned",
    )
    assert main.run_describe_vault(config) == 0

    db = DBManager(tmp_path / "pipeline.db")
    vault = MediaVault(db, tmp_path / "vault")
    vault.ingest(_make_image(tmp_path, "x.jpg", b"x"), source="drop")
    db.close()

    assert main.run_describe_vault(config) == 0
    db = DBManager(tmp_path / "pipeline.db")
    assert db.get_vault_analysis(db.list_vault_media("image")[0]["id"]) is not None
    db.close()