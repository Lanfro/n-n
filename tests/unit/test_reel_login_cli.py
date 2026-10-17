"""CLI wiring for the one-time video-service sign-in (`--reel-login`)."""

import main as cli
from src.reel.models import SESSION_AUTHENTICATED, SESSION_LOGGED_OUT


def test_reel_login_exits_zero_once_authenticated(monkeypatch):
    monkeypatch.setattr(cli, "_reel_login_session", lambda cfg: SESSION_AUTHENTICATED)
    assert cli.main(["--reel-login"]) == 0


def test_reel_login_exits_three_when_still_logged_out(monkeypatch):
    monkeypatch.setattr(cli, "_reel_login_session", lambda cfg: SESSION_LOGGED_OUT)
    assert cli.main(["--reel-login"]) == 3


def test_reel_login_does_not_require_a_set(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli, "_reel_login_session", lambda cfg: calls.append(cfg) or SESSION_AUTHENTICATED
    )
    assert cli.main(["--reel-login"]) == 0
    assert len(calls) == 1
