"""Unit tests for HITL alerting (sound + Telegram notify)."""

import sys

from src.approval.notifier import notify
from src.approval.sound import alert

# ---------------------------------------------------------------------------
# sound.alert
# ---------------------------------------------------------------------------


def test_alert_disabled_returns_false():
    assert alert(sound_enabled=False) is False


def test_alert_non_windows_returns_false(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert alert(sound_enabled=True) is False


def test_alert_default_beep(monkeypatch):
    calls = []

    class FakeWinsound:
        MB_ICONEXCLAMATION = 0x30

        @classmethod
        def MessageBeep(cls, *args):
            calls.append(("beep", args))

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winsound", FakeWinsound)
    assert alert(sound_enabled=True) is True
    assert calls == [("beep", (FakeWinsound.MB_ICONEXCLAMATION,))]


def test_alert_custom_wav(monkeypatch):
    calls = []

    class FakeWinsound:
        SND_FILENAME = 1
        SND_ASYNC = 2
        SND_NODEFAULT = 4

        @classmethod
        def PlaySound(cls, path, flags):
            calls.append((path, flags))

        @classmethod
        def MessageBeep(cls, *args):  # pragma: no cover
            raise AssertionError("should not beep when a wav is set")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winsound", FakeWinsound)
    expected_flags = FakeWinsound.SND_FILENAME | FakeWinsound.SND_ASYNC | FakeWinsound.SND_NODEFAULT
    assert alert(sound_enabled=True, sound_file="meow.wav") is True
    assert calls == [("meow.wav", expected_flags)]


def test_alert_winsound_runtime_error_swallowed(monkeypatch):
    class FakeWinsound:
        MB_ICONEXCLAMATION = 0x30

        @classmethod
        def MessageBeep(cls, *args):
            raise RuntimeError("no sound device")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winsound", FakeWinsound)
    assert alert(sound_enabled=True) is False


# ---------------------------------------------------------------------------
# notifier.notify
# ---------------------------------------------------------------------------


def test_notify_disabled_returns_false():
    assert notify("t", 1, "hi", enabled=False) is False


def test_notify_without_token_or_chat_returns_false():
    assert notify("", 1, "hi") is False
    assert notify("t", None, "hi") is False


def test_notify_posts_message(monkeypatch):
    captured = {}

    class FakeResp:
        ok = True
        status_code = 200

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return FakeResp()

    monkeypatch.setattr("requests.post", fake_post)
    assert notify("TOKEN", 123, "Action needed") is True
    assert captured["url"] == "https://api.telegram.org/botTOKEN/sendMessage"
    assert captured["json"]["chat_id"] == 123
    assert captured["json"]["text"] == "Action needed"


def test_notify_http_error_returns_false(monkeypatch):
    class FakeResp:
        ok = False
        status_code = 500
        text = "boom"

    monkeypatch.setattr(
        "requests.post",
        lambda *a, **k: FakeResp(),
    )
    assert notify("t", 1, "hi") is False


def test_notify_network_error_returns_false(monkeypatch):
    import requests

    def raise_error(*a, **k):
        raise requests.exceptions.RequestException("offline")

    monkeypatch.setattr("requests.post", raise_error)
    assert notify("t", 1, "hi") is False