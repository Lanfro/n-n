"""Tests for the subject-labeling pass (--label-vault)."""

import pytest

from main import (
    _classify_subject_heuristic,
    _parse_vision_label,
    run_label_vault,
)
from src.database.db_manager import DBManager
from src.vault.media_vault import MediaVault
from src.vision.visual_analyzer import OllamaUnavailableError
from tests.unit.test_visual_analyzer import _make_jpeg


def test_heuristic_classifies_colours() -> None:
    assert (
        _classify_subject_heuristic("A black cat lies on a tiled floor.")
        == "nero"
    )
    assert (
        _classify_subject_heuristic("A black kitten snoozes on the rug.")
        == "nero"
    )
    assert _classify_subject_heuristic("A gray and white cat rests.") == "nuvola"
    assert (
        _classify_subject_heuristic("A grey-and-white cat stares back.")
        == "nuvola"
    )
    assert (
        _classify_subject_heuristic("A light gray tabby with blue eyes.")
        == "nuvola"
    )
    assert (
        _classify_subject_heuristic("A black cat and a gray-and-white cat nap.")
        == "both"
    )
    assert _classify_subject_heuristic("A grumpy close-up of a paw.") == "unclear"
    assert _classify_subject_heuristic("A white bookshelf in the background.") == "unclear"
    assert _classify_subject_heuristic("") == "unclear"
    assert _classify_subject_heuristic("Left untouched like a bear") == "unclear"


def test_parse_vision_label() -> None:
    assert _parse_vision_label("The cat is Nero. Solid black coat") == "nero"
    assert _parse_vision_label("nuvola") == "nuvola"
    assert _parse_vision_label("Both Nero and Nuvola are visible") == "both"
    assert _parse_vision_label("a blurry close-up, unclear case") == "unclear"
    assert _parse_vision_label("cannot tell from this crop") is None


@pytest.fixture()
def labeled_db(tmp_path):
    db = DBManager(tmp_path / "pipeline.db")
    vault = MediaVault(db, tmp_path / "vault")
    descs = {
        "black.jpg": "A black cat lies on the floor.",
        "gray.jpg": "A gray and white cat rests.",
        "close.jpg": "A very tight close-up of a cat's ear.",
    }
    colors = {
        "black.jpg": "black",
        "gray.jpg": "white",
        "close.jpg": "green",
    }
    for name, desc in descs.items():
        path = _make_jpeg(tmp_path, 100, 100, name, color=colors[name])
        mid = vault.ingest(path, source="telegram")
        db.upsert_vault_analysis(
            vault_media_id=mid,
            model="qwen3-vl:8b",
            prompt="describe",
            description=desc,
        )
    return db


def _label_map(db):
    return {
        a["original_filename"]: db.get_vault_subject(a["id"])
        and db.get_vault_subject(a["id"])["label"]
        for a in db.list_vault_media("image")
    }


def test_run_label_vault_heuristic_then_vision(
    labeled_db, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "main.VisualAnalyzer.analyze",
        lambda self, path, prompt: "The subject is Nero.",
    )
    config = {
        "pipeline": {
            "db_path": str(tmp_path / "pipeline.db"),
            "labels_report": str(tmp_path / "labels.md"),
        },
        "ollama": {
            "base_url": "http://localhost:11434",
            "vision_model": "qwen3-vl:8b",
            "timeout_seconds": 10,
        },
    }
    assert run_label_vault(config) == 0
    db = labeled_db
    labels = _label_map(db)
    assert labels["black.jpg"] == "nero"
    assert labels["gray.jpg"] == "nuvola"
    assert labels["close.jpg"] == "nero"  # unresolved heuristic + vision answer
    row = db.get_vault_subject(
        next(a["id"] for a in db.list_vault_media("image") if a["original_filename"] == "close.jpg")
    )
    assert row["method"] == "vision"
    report = (tmp_path / "labels.md").read_text(encoding="utf-8")
    assert "## Summary" in report
    assert "**nero:** 2" in report
    db.close()


def test_run_label_vault_vision_failure_keeps_unclear(
    labeled_db, tmp_path, monkeypatch
) -> None:
    def boom_analyze(self, path, prompt):
        raise OllamaUnavailableError("ollama down")

    monkeypatch.setattr("main.VisualAnalyzer.analyze", boom_analyze)
    config = {
        "pipeline": {
            "db_path": str(tmp_path / "pipeline.db"),
            "labels_report": str(tmp_path / "labels.md"),
        },
        "ollama": {
            "base_url": "http://localhost:11434",
            "vision_model": "qwen3-vl:8b",
            "timeout_seconds": 10,
        },
    }
    assert run_label_vault(config) == 0
    db = labeled_db
    labels = _label_map(db)
    assert labels["black.jpg"] == "nero"
    assert labels["gray.jpg"] == "nuvola"
    assert labels["close.jpg"] == "unclear"  # vision failed, stays heuristic
    report = (tmp_path / "labels.md").read_text(encoding="utf-8")
    assert "vision failed" in report
    db.close()