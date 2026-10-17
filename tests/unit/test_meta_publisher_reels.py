"""Unit tests for the Reels publishing flow (mocked Graph API HTTP)."""

from types import SimpleNamespace

import pytest

from src.publisher.meta_publisher import MetaPublisher, MetaPublisherError


def resp(*, payload=None, status_code=200, ok=True, text=""):
    return SimpleNamespace(
        status_code=status_code,
        ok=ok,
        text=text,
        json=lambda: payload,
    )


def publisher(*, dry_run=False):
    return MetaPublisher(
        access_token="tok",
        instagram_user_id="999",
        api_version="v21.0",
        graph_base_url="https://graph.facebook.com",
        dry_run=dry_run,
    )


def test_dry_run_creates_synthetic_reel_ids_no_http(monkeypatch):
    def boom(*_a, **_k):  # pragma: no cover - must never be called
        raise AssertionError("no HTTP call allowed in dry-run")

    monkeypatch.setattr("requests.post", boom)
    monkeypatch.setattr("requests.get", boom)
    pub = publisher(dry_run=True)

    container = pub.create_reel_container(
        "https://cdn.example/clip.mp4", "cap", brief_id="parallel_lives:0"
    )
    assert container == "container_dry_parallel_lives:0"
    assert pub.wait_for_container(container) == "FINISHED"
    assert pub.publish_container(container) == (
        "media_dry_container_dry_parallel_lives:0"
    )


def test_create_reel_container_sends_reels_params(monkeypatch):
    captured = {}

    def fake_post(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return resp(payload={"id": "17857"})

    monkeypatch.setattr("requests.post", fake_post)
    pub = publisher()
    container = pub.create_reel_container(
        "https://cdn.example/clip.mp4",
        "caption text",
        share_to_feed=True,
    )
    assert container == "17857"
    assert captured["url"].endswith("/999/media")
    assert captured["params"]["media_type"] == "REELS"
    assert captured["params"]["video_url"] == "https://cdn.example/clip.mp4"
    assert captured["params"]["caption"] == "caption text"
    assert captured["params"]["share_to_feed"] == "true"
    assert captured["params"]["access_token"] == "tok"


def test_create_reel_container_failure_raises(monkeypatch):
    monkeypatch.setattr(
        "requests.post", lambda *a, **k: resp(payload={"error": "nope"}, ok=False)
    )
    with pytest.raises(MetaPublisherError):
        publisher().create_reel_container("https://x/y.mp4", "cap")


def test_wait_for_container_polls_to_finished(monkeypatch):
    states = iter(
        [
            {"status_code": "IN_PROGRESS"},
            {"status_code": "IN_PROGRESS"},
            {"status_code": "FINISHED"},
        ]
    )
    monkeypatch.setattr("requests.get", lambda *a, **k: resp(payload=next(states)))
    slept = []
    result = publisher().wait_for_container(
        "123", timeout_seconds=60, poll_interval=0.01, sleep=slept.append
    )
    assert result == "FINISHED"
    assert slept == [0.01, 0.01]


def test_wait_for_container_error_raises(monkeypatch):
    monkeypatch.setattr(
        "requests.get",
        lambda *a, **k: resp(payload={"status_code": "ERROR", "status": "bad codec"}),
    )
    with pytest.raises(MetaPublisherError, match="bad codec"):
        publisher().wait_for_container("123", sleep=lambda _s: None)


def test_wait_for_container_timeout_raises(monkeypatch):
    monkeypatch.setattr(
        "requests.get", lambda *a, **k: resp(payload={"status_code": "IN_PROGRESS"})
    )
    with pytest.raises(MetaPublisherError, match="timed out"):
        publisher().wait_for_container(
            "123", timeout_seconds=0, sleep=lambda _s: None
        )


def test_publish_container_returns_media_id(monkeypatch):
    monkeypatch.setattr("requests.post", lambda *a, **k: resp(payload={"id": "17833"}))
    assert publisher().publish_container("123") == "17833"
