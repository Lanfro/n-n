"""Tests for VisualAnalyzer image preparation and batch describe resilience."""

import io
from pathlib import Path

import pytest
from PIL import Image

from src.database.db_manager import DBManager
from src.vault.media_vault import MediaVault
from src.vision.visual_analyzer import VisualAnalyzer


def _make_jpeg(
    tmp_path: Path, width: int, height: int, name: str = "img.jpg", color: str = "orange"
) -> Path:
    img = Image.new("RGB", (width, height), color=color)
    path = tmp_path / name
    img.save(path, format="JPEG", quality=95)
    return path


def _prepare(tmp_path: Path, **kwargs):
    analyzer = VisualAnalyzer(
        base_url="http://localhost:11434",
        model="qwen3-vl:8b",
        timeout_seconds=10,
        **kwargs,
    )
    return analyzer


# ---- _prepare_image_bytes tests ----


def test_small_image_passthrough_bytes_identical(tmp_path) -> None:
    path = tmp_path / "small.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0small-jpeg-data")
    analyzer = _prepare(tmp_path)
    result = analyzer._prepare_image_bytes(path)
    assert result == b"\xff\xd8\xff\xe0small-jpeg-data"


def test_large_image_downscaled_max_side(tmp_path) -> None:
    path = _make_jpeg(tmp_path, 4000, 3000, "big.jpg")
    original_size = path.stat().st_size
    analyzer = _prepare(tmp_path, max_side=1280)
    result = analyzer._prepare_image_bytes(path)
    assert len(result) < original_size  # smaller than the 4000px original
    decoded = Image.open(io.BytesIO(result))
    assert max(decoded.size) <= 1280
    ratio = max(decoded.size) / max((4000, 3000))
    assert abs(decoded.width - round(4000 * ratio)) <= 1
    assert abs(decoded.height - round(3000 * ratio)) <= 1


def test_large_landscape_downscaled(tmp_path) -> None:
    path = _make_jpeg(tmp_path, 3000, 2000, "land.jpg")
    analyzer = _prepare(tmp_path, max_side=1280)
    result = analyzer._prepare_image_bytes(path)
    decoded = Image.open(io.BytesIO(result))
    assert max(decoded.size) == 1280
    assert decoded.size == (1280, 853)  # 2000/3000*1280


def test_rgba_image_converted_to_rgb(tmp_path) -> None:
    img = Image.new("RGBA", (2000, 2000), color=(255, 0, 0, 128))
    path = tmp_path / "alpha.png"
    img.save(path, format="PNG")
    analyzer = _prepare(tmp_path, max_side=1280)
    result = analyzer._prepare_image_bytes(path)
    decoded = Image.open(io.BytesIO(result))
    assert decoded.mode == "RGB"
    assert max(decoded.size) <= 1280


def test_missing_file_raises(tmp_path) -> None:
    analyzer = _prepare(tmp_path)
    with pytest.raises(FileNotFoundError):
        analyzer._prepare_image_bytes(tmp_path / "nope.jpg")


def test_analyze_sends_num_predict_option(tmp_path, monkeypatch) -> None:
    import requests

    path = _make_jpeg(tmp_path, 100, 100)
    captured = {}

    def fake_post(url, json=None, timeout=120):
        captured["json"] = json
        class R:
            status_code = 200
            text = ""

            @staticmethod
            def raise_for_status():
                return None

            def json(self):
                return {"response": "a cat"}
        return R()

    monkeypatch.setattr(requests, "post", fake_post)
    analyzer = VisualAnalyzer(
        base_url="http://localhost:11434",
        model="qwen3-vl:8b",
        timeout_seconds=10,
        num_predict=200,
    )
    out = analyzer.analyze(path)
    assert out == "a cat"
    assert captured["json"]["options"] == {"num_predict": 200}


def test_analyze_sends_keep_alive(tmp_path, monkeypatch) -> None:
    import requests

    path = _make_jpeg(tmp_path, 100, 100)
    captured = {}

    def fake_post(url, json=None, timeout=120):
        captured["json"] = json
        class R:
            status_code = 200
            text = ""

            @staticmethod
            def raise_for_status():
                return None

            def json(self):
                return {"response": "kept warm"}

        return R()

    monkeypatch.setattr(requests, "post", fake_post)
    analyzer = VisualAnalyzer(
        base_url="http://localhost:11434",
        model="qwen3-vl:8b",
        timeout_seconds=10,
        keep_alive="30m",
    )
    out = analyzer.analyze(path)
    assert out == "kept warm"
    assert captured["json"]["keep_alive"] == "30m"
    assert "options" not in captured["json"]


def test_analyze_omits_options_without_num_predict(tmp_path, monkeypatch) -> None:
    import requests

    path = _make_jpeg(tmp_path, 100, 100)
    captured = {}

    def fake_post(url, json=None, timeout=120):
        captured["json"] = json
        class R:
            status_code = 200
            text = ""

            @staticmethod
            def raise_for_status():
                return None

            def json(self):
                return {"response": "ok"}

        return R()

    monkeypatch.setattr(requests, "post", fake_post)
    analyzer = VisualAnalyzer(
        base_url="http://localhost:11434",
        model="qwen3-vl:8b",
        timeout_seconds=10,
    )
    analyzer.analyze(path)
    assert "options" not in captured["json"]


# ---- Batch resilience test ----


def test_describe_batch_continues_on_per_asset_error(tmp_path, monkeypatch) -> None:
    """RuntimeError from one asset's analyze should not abort the batch."""
    import main

    db = DBManager(tmp_path / "pipeline.db")
    vault = MediaVault(db, tmp_path / "vault")
    _make_jpeg(tmp_path, 100, 100, "good.jpg", color="orange")
    _make_jpeg(tmp_path, 90, 100, "bad.jpg", color="purple")
    vault.ingest(tmp_path / "good.jpg", source="telegram")
    vault.ingest(tmp_path / "bad.jpg", source="telegram")
    db.close()

    config = {
        "pipeline": {
            "db_path": str(tmp_path / "pipeline.db"),
            "descriptions_report": str(tmp_path / "descriptions.md"),
        },
        "ollama": {
            "base_url": "http://localhost:11434",
            "vision_model": "qwen3-vl:8b",
            "timeout_seconds": 10,
        },
    }

    call_count = {"n": 0}

    def flaky_analyze(self, path, prompt):
        call_count["n"] += 1
        with Image.open(Path(path)) as im:
            is_bad = (im.size[0], im.size[1]) == (90, 100)
        if is_bad:
            raise RuntimeError("simulated failure")
        return "a great cat photo"

    monkeypatch.setattr(main.VisualAnalyzer, "analyze", flaky_analyze)

    code = main.run_describe_vault(config)
    assert code == 0  # batch continues despite per-asset error

    db = DBManager(tmp_path / "pipeline.db")
    assets = db.list_vault_media("image")
    assert len(assets) == 2
    # "bad" asset has no analysis row; "good" asset does
    good = next(a for a in assets if a["original_filename"] == "good.jpg")
    bad = next(a for a in assets if a["original_filename"] == "bad.jpg")
    assert db.get_vault_analysis(good["id"], "qwen3-vl:8b") is not None
    assert db.get_vault_analysis(bad["id"], "qwen3-vl:8b") is None
    db.close()

    report = (tmp_path / "descriptions.md").read_text(encoding="utf-8")
    assert "a great cat photo" in report
    assert "analysis failed" in report
    assert "simulated failure" in report


def test_describe_batch_retries_once_then_skips(tmp_path, monkeypatch) -> None:
    """First Ollama failure is retried; second failure skips, batch continues."""
    import main
    from src.vision.visual_analyzer import OllamaUnavailableError

    db = DBManager(tmp_path / "pipeline.db")
    vault = MediaVault(db, tmp_path / "vault")
    _make_jpeg(tmp_path, 100, 100, "a.jpg", color="orange")
    _make_jpeg(tmp_path, 90, 100, "b.jpg", color="purple")
    _make_jpeg(tmp_path, 80, 100, "c.jpg", color="green")
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        vault.ingest(tmp_path / name, source="telegram")
    # Map stored hash filename -> original name so the mock is deterministic.
    by_name = {
        Path(a["stored_path"]).name: a["original_filename"]
        for a in db.list_vault_media("image")
    }
    db.close()

    config = {
        "pipeline": {
            "db_path": str(tmp_path / "pipeline.db"),
            "descriptions_report": str(tmp_path / "descriptions.md"),
        },
        "ollama": {
            "base_url": "http://localhost:11434",
            "vision_model": "qwen3-vl:8b",
            "timeout_seconds": 10,
        },
    }

    hits = {}

    def analyze(self, path, prompt):
        name = by_name[Path(path).name]
        hits[name] = hits.get(name, 0) + 1
        if name == "c.jpg":  # always fails
            raise OllamaUnavailableError("permanent timeout")
        if name == "b.jpg" and hits[name] == 1:  # fails once, then succeeds
            raise OllamaUnavailableError("once only")
        return f"canned-{name}"

    monkeypatch.setattr(main.VisualAnalyzer, "analyze", analyze)
    code = main.run_describe_vault(config)
    assert code == 0

    db = DBManager(tmp_path / "pipeline.db")
    rows = {
        a["original_filename"]: db.get_vault_analysis(a["id"], "qwen3-vl:8b")
        for a in db.list_vault_media("image")
    }
    # a: success, b: retried then success, c: skipped with no analysis
    assert rows["a.jpg"]["description"] == "canned-a.jpg"
    assert rows["b.jpg"]["description"] == "canned-b.jpg"
    assert rows["c.jpg"] is None
    assert hits["b.jpg"] == 2  # retried once
    assert hits["c.jpg"] == 2  # first attempt + retry, both skipped
    db.close()

    report = (tmp_path / "descriptions.md").read_text(encoding="utf-8")
    assert "permanent timeout" in report
    assert len([l for l in report.splitlines() if l.startswith("## Asset")]) == 3


def test_describe_batch_empty_result_retried_then_skipped(
    tmp_path, monkeypatch
) -> None:
    """An empty response is treated as a failure: retried, then skipped."""
    import main

    db = DBManager(tmp_path / "pipeline.db")
    vault = MediaVault(db, tmp_path / "vault")
    _make_jpeg(tmp_path, 100, 100, "e1.jpg", color="blue")
    _make_jpeg(tmp_path, 90, 100, "e2.jpg", color="purple")
    for name in ("e1.jpg", "e2.jpg"):
        vault.ingest(tmp_path / name, source="telegram")
    by_name = {
        Path(a["stored_path"]).name: a["original_filename"]
        for a in db.list_vault_media("image")
    }
    db.close()

    config = {
        "pipeline": {
            "db_path": str(tmp_path / "pipeline.db"),
            "descriptions_report": str(tmp_path / "descriptions.md"),
        },
        "ollama": {
            "base_url": "http://localhost:11434",
            "vision_model": "qwen3-vl:8b",
            "timeout_seconds": 10,
        },
    }

    hits = {}

    def analyze(self, path, prompt):
        name = by_name[Path(path).name]
        hits[name] = hits.get(name, 0) + 1
        if name == "e1.jpg" and hits[name] == 1:
            return ""  # empty once, then a real description
        if name == "e2.jpg":
            return ""  # always empty -> skip
        return f"real-{name}"

    monkeypatch.setattr(main.VisualAnalyzer, "analyze", analyze)
    assert main.run_describe_vault(config) == 0

    db = DBManager(tmp_path / "pipeline.db")
    rows = {
        a["original_filename"]: db.get_vault_analysis(a["id"], "qwen3-vl:8b")
        for a in db.list_vault_media("image")
    }
    assert rows["e1.jpg"]["description"] == "real-e1.jpg"
    assert rows["e2.jpg"] is None  # skipped, no analysis row
    assert hits["e1.jpg"] == 2
    assert hits["e2.jpg"] == 2
    db.close()

    report = (tmp_path / "descriptions.md").read_text(encoding="utf-8")
    assert "empty description returned twice" in report
