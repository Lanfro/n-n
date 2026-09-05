"""Unit tests for the Telegram approval gateway.

Regression: `Application.start()` in python-telegram-bot >=20 does NOT start
polling; the gateway must also start the Updater or button presses are never
delivered. A fake Application records whether `updater.start_polling` ran.
"""

import asyncio
from typing import Self

import pytest

from src.approval import telegram_gateway as gw


@pytest.fixture(autouse=True)
def _clear_pending():
    gw._pending.clear()
    yield
    gw._pending.clear()


class _FakeUpdater:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.start_kwargs: dict | None = None

    async def start_polling(self, **kwargs) -> None:
        self.started = True
        self.start_kwargs = kwargs

    async def stop(self) -> None:
        self.stopped = True


class _FakeMessage:
    chat_id = 123


class _FakeBot:
    async def send_photo(self, **kwargs) -> _FakeMessage:
        return _FakeMessage()


class _FakeApplication:
    def __init__(self) -> None:
        self.updater = _FakeUpdater()
        self.bot = _FakeBot()

    async def initialize(self) -> None:
        return None

    def add_handler(self, _handler) -> None:
        return None

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def __aenter__(self) -> "Self":
        return self

    async def __aexit__(self, *args) -> None:
        return None


class _FakeBuilder:
    def token(self, _t: str) -> "_FakeBuilder":
        return self

    def arbitrary_callback_data(self, _v: bool) -> "_FakeBuilder":
        return self

    def read_timeout(self, _v: int) -> "_FakeBuilder":
        return self

    def write_timeout(self, _v: int) -> "_FakeBuilder":
        return self

    def build(self) -> _FakeApplication:
        app = _FakeApplication()
        _FakeApplicationClass.last_built = app
        return app


class _FakeApplicationClass:
    last_built: _FakeApplication | None = None

    @staticmethod
    def builder() -> _FakeBuilder:
        return _FakeBuilder()


def test_approve_button_registers_pending() -> None:
    """A button press must settle a decision even without a prior message."""
    class _Msg:
        chat_id = 123

    class _Query:
        @property
        def message(self) -> _Msg:
            return _Msg()

        async def answer(self, text: str | None = None, **kwargs) -> None:
            return None

    class _Update:
        callback_query = _Query()

    asyncio.run(gw._approve_callback(_Update(), None))
    assert gw._pending[123]["action"] == "approve"


def test_telegram_approval_starts_polling(monkeypatch) -> None:
    monkeypatch.setattr(gw, "Application", _FakeApplicationClass)
    gateway = gw.TelegramGateway(
        db=None,  # type: ignore[arg-type]  # _request_via_telegram doesn't touch db
        bot_token="token",
        allowed_chat_ids=[123],
        decision_timeout_seconds=1,
    )
    decision = asyncio.run(
        gateway._request_via_telegram(9, "media.jpg", "caption")
    )
    assert decision["action"] == "timeout"
    app = _FakeApplicationClass.last_built
    assert app is not None
    assert app.updater.started is True
    assert app.updater.start_kwargs == {
        "poll_interval": 1.0,
        "timeout": 10,
        "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": True,
    }
    assert app.updater.stopped is True