"""Tests for the batch persona-draft generation (T036-T038)."""

import pytest

from src.database.db_manager import DBManager
from src.vault.media_vault import MediaVault
from src.vision.visual_analyzer import OllamaUnavailableError
from tests.unit.test_visual_analyzer import _make_jpeg

PERSONA = {
    "name": "cat_1",
    "system_prompt": "You are an exotic shorthair cat in first person.",
    "base_hashtags": ["exoticshorthair", "catsofinstagram"],
    "hashtag_count": 4,
    "reel_rules": {"max_words": 7},
    "caption_rules": {"max_sentences": 3, "tone": "playful"},
}


@pytest.fixture()
def db_with_assets(tmp_path):
    db = DBManager(tmp_path / "pipeline.db")
    vault = MediaVault(db, tmp_path / "vault")
    _make_jpeg(tmp_path, 100, 100, "d1.jpg", color="orange")
    _make_jpeg(tmp_path, 90, 100, "d2.jpg", color="purple")
    _make_jpeg(tmp_path, 80, 100, "d3.jpg", color="green")
    for name in ("d1.jpg", "d2.jpg", "d3.jpg"):
        vault.ingest(tmp_path / name, source="telegram")
    return db


def _seed_descriptions(db):
    for asset in db.list_vault_media("image"):
        db.upsert_vault_analysis(
            vault_media_id=asset["id"],
            model="qwen3-vl:8b",
            prompt="describe",
            description=f"seen-{asset['original_filename']}",
        )


def test_upsert_and_get_vault_draft(db_with_assets, tmp_path):
    db = db_with_assets
    asset = db.list_vault_media("image")[0]
    db.upsert_vault_draft(
        vault_media_id=asset["id"],
        persona_name="cat_1",
        model="qwen2.5",
        reel_text="fluffy loaf",
        caption="Draft one.",
        hashtags="exoticshorthair,catsofinstagram",
    )
    row = db.get_vault_draft(asset["id"], "cat_1")
    assert row is not None
    assert row["reel_text"] == "fluffy loaf"
    assert row["caption"] == "Draft one."

    db.upsert_vault_draft(
        vault_media_id=asset["id"],
        persona_name="cat_1",
        model="qwen2.5",
        reel_text="fluffy loaf v2",
        caption="Draft two.",
        hashtags="catsofinstagram",
    )
    updated = db.get_vault_draft(asset["id"], "cat_1")
    assert updated["caption"] == "Draft two."  # conflict -> replaced, not dup
    db.close()


def test_get_vault_draft_missing(db_with_assets):
    db = db_with_assets
    asset = db.list_vault_media("image")[0]
    assert db.get_vault_draft(asset["id"], "cat_1") is None
    assert db.get_vault_draft(asset["id"], "cat_2") is None
    db.close()


def test_run_drafts_vault(tmp_path, monkeypatch):
    import main

    db = DBManager(tmp_path / "pipeline.db")
    vault = MediaVault(db, tmp_path / "vault")
    _make_jpeg(tmp_path, 100, 100, "a.jpg", color="orange")
    _make_jpeg(tmp_path, 90, 100, "b.jpg", color="purple")
    _make_jpeg(tmp_path, 80, 100, "c.jpg", color="green")
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        vault.ingest(tmp_path / name, source="telegram")
    _seed_descriptions(db)
    db.close()

    config = {
        "pipeline": {
            "db_path": str(tmp_path / "pipeline.db"),
            "drafts_report": str(tmp_path / "drafts_cat_1.md"),
        },
        "personas": {"config_path": str(tmp_path / "personas.json")},
        "ollama": {
            "base_url": "http://localhost:11434",
            "vision_model": "qwen3-vl:8b",
            "text_model": "qwen2.5",
            "timeout_seconds": 10,
        },
    }
    (tmp_path / "personas.json").write_text(
        '{"accounts": {"cat_1": ' + _dump(PERSONA) + "}}", encoding="utf-8"
    )

    hits = {}

    def generate(self, persona, description):
        name = description.removeprefix("seen-")
        hits[name] = hits.get(name, 0) + 1
        if name == "c.jpg":
            raise OllamaUnavailableError("permanent timeout")
        if name == "b.jpg" and hits[name] == 1:
            return {}  # empty once, then a real draft
        return {
            "reel_text": "fluffy loaf",
            "caption": f"Draft for {name}",
            "hashtags": ["exoticshorthair", "catsofinstagram"],
        }

    monkeypatch.setattr(main.PromptGenerator, "generate", generate)
    assert main.run_drafts_vault(config, "cat_1") == 0

    db = DBManager(tmp_path / "pipeline.db")
    rows = {
        a["original_filename"]: db.get_vault_draft(a["id"], "cat_1")
        for a in db.list_vault_media("image")
    }
    assert rows["a.jpg"]["caption"] == "Draft for a.jpg"
    assert rows["b.jpg"]["caption"] == "Draft for b.jpg"  # retried once
    assert rows["c.jpg"] is None  # skipped, no draft row
    assert hits["b.jpg"] == 2
    assert hits["c.jpg"] == 2
    db.close()

    report = (tmp_path / "drafts_cat_1.md").read_text(encoding="utf-8")
    assert "permanent timeout" in report
    assert "Draft for a.jpg" in report
    assert len([l for l in report.splitlines() if l.startswith("## Asset")]) == 3


def test_run_drafts_vault_skips_undescribed(tmp_path, monkeypatch):
    """Assets without a stored description produce no draft."""
    import main

    db = DBManager(tmp_path / "pipeline.db")
    vault = MediaVault(db, tmp_path / "vault")
    _make_jpeg(tmp_path, 100, 100, "u.jpg", color="orange")
    vault.ingest(tmp_path / "u.jpg", source="telegram")
    # No description seeded: even though a description requirement exists, the
    # mock seeding below would fail; assert the batch reports it.
    db.close()

    config = {
        "pipeline": {
            "db_path": str(tmp_path / "pipeline.db"),
            "drafts_report": str(tmp_path / "drafts_cat_1.md"),
        },
        "personas": {"config_path": str(tmp_path / "personas.json")},
        "ollama": {
            "base_url": "http://localhost:11434",
            "vision_model": "qwen3-vl:8b",
            "text_model": "qwen2.5",
            "timeout_seconds": 10,
        },
    }
    (tmp_path / "personas.json").write_text(
        '{"accounts": {"cat_1": ' + _dump(PERSONA) + "}}", encoding="utf-8"
    )

    def generate(self, persona, description):
        raise AssertionError("should not be called without a description")

    monkeypatch.setattr(main.PromptGenerator, "generate", generate)
    assert main.run_drafts_vault(config, "cat_1") == 0

    db = DBManager(tmp_path / "pipeline.db")
    assert db.get_vault_draft(db.list_vault_media("image")[0]["id"], "cat_1") is None
    db.close()
    assert not (tmp_path / "drafts_cat_1.md").exists()  # no work -> no report


def _dump(fixture: dict) -> str:
    import json

    return json.dumps(fixture)