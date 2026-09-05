"""Unit tests for the Telegram vault channel (archive + sync)."""

import pytest

from src.database.db_manager import DBManager
from src.vault.media_vault import MediaVault
from src.vault.telegram_archive import TelegramVault, VaultArchiveError

OK_RESPONSE = {"ok": True, "result": {}}


class FakeResponse:
    def __init__(self, ok=True, payload=None, status_code=200, text=""):
        self.ok = ok
        self._payload = payload if payload is not None else OK_RESPONSE
        self.status_code = status_code
        self.text = text or str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture()
def db(tmp_path):
    mgr = DBManager(tmp_path / "test.db")
    yield mgr
    mgr.close()


@pytest.fixture()
def vault_dir(tmp_path):
    return tmp_path / "vault"


@pytest.fixture()
def vault(db, tmp_path):
    return MediaVault(db, tmp_path / "vault")


def _seed_asset(db, vault, tmp_path):
    img = tmp_path / "cat.jpg"
    img.write_bytes(b"vault test bytes")
    return vault.ingest(img)


def _uploaded(asset_id):
    return {
        "result": {
            "message_id": 7,
            "document": {"file_id": f"FILE_{asset_id}"},
        }
    }


def test_requires_token_and_chat():
    with pytest.raises(VaultArchiveError):
        TelegramVault(db=None, bot_token="", chat_id="")
    with pytest.raises(VaultArchiveError):
        TelegramVault(db=None, bot_token="t", chat_id="")  # type: ignore[arg-type]


def test_archive_uploads_document(monkeypatch, db, vault, tmp_path):
    asset_id = _seed_asset(db, vault, tmp_path)
    calls = []

    def fake_post(url, data=None, files=None, timeout=120):
        calls.append((url, data, files))
        assert files["document"] is not None
        assert data["chat_id"] == "-100"
        assert data["caption"].startswith("sha256:")
        return FakeResponse(payload=_uploaded(asset_id))

    monkeypatch.setattr("requests.post", fake_post)
    tv = TelegramVault(db, "TOKEN", "-100")
    out = tv.archive(asset_id)
    assert out["file_id"] == f"FILE_{asset_id}"
    row = db.get_vault_media(asset_id)
    assert row["telegram_file_id"] == f"FILE_{asset_id}"
    assert row["telegram_message_id"] == 7


def test_archive_idempotent(monkeypatch, db, vault, tmp_path):
    asset_id = _seed_asset(db, vault, tmp_path)
    db.set_vault_archive(asset_id, telegram_file_id="FILE_X", telegram_message_id=3)
    called = []
    monkeypatch.setattr(
        "requests.post", lambda *a, **k: called.append(a) or FakeResponse()
    )
    tv = TelegramVault(db, "TOKEN", "-100")
    out = tv.archive(asset_id)
    assert out["file_id"] == "FILE_X"
    assert called == []


def test_archive_failure_raises(monkeypatch, db, vault, tmp_path):
    asset_id = _seed_asset(db, vault, tmp_path)
    monkeypatch.setattr(
        "requests.post", lambda *a, **k: FakeResponse(ok=False, status_code=400, text="bad")
    )
    tv = TelegramVault(db, "TOKEN", "-100")
    with pytest.raises(VaultArchiveError):
        tv.archive(asset_id)


def test_sync_ingests_own_channel_photo(monkeypatch, db, vault, tmp_path):
    other_post = {
        "message_id": 1,
        "chat": {"id": "-999"},
        "photo": [{"file_id": "OTHER", "file_size": 1}],
    }
    my_post = {
        "message_id": 2,
        "chat": {"id": "-100"},
        "photo": [{"file_id": "MY"}],
    }
    updates = [
        {"update_id": 10, "channel_post": other_post},
        {"update_id": 11, "channel_post": my_post},
    ]

    def fake_get(url, params=None, timeout=120):
        return FakeResponse(payload={"ok": True, "result": updates})

    def fake_download(self, file_id, dest):
        good = dest.with_suffix(".jpg")
        good.write_bytes(b"channel cat")
        return good

    monkeypatch.setattr("requests.get", fake_get)
    monkeypatch.setattr(TelegramVault, "download_to", fake_download)

    tv = TelegramVault(db, "TOKEN", "-100")
    result = tv.sync_from_channel(vault)
    assert result["new"] == 1
    assert result["offset"] == 11
    assert db.get_channel_offset() == 11
    with db._lock:
        rows = db._conn.execute("SELECT * FROM vault_media").fetchall()
    assert len(rows) == 1
    assert dict(rows[0])["source"] == "telegram"


def test_sync_is_idempotent_after_offset(monkeypatch, db, vault, tmp_path):
    db.create_post("cat_1", "x.jpg")
    db.set_channel_offset(11)

    calls = []

    def fake_get(url, params=None, timeout=120):
        calls.append(params.get("offset"))
        return FakeResponse(payload={"ok": True, "result": []})

    monkeypatch.setattr("requests.get", fake_get)
    tv = TelegramVault(db, "TOKEN", "-100")
    result = tv.sync_from_channel(vault)
    assert result["new"] == 0
    assert result["offset"] == 11
    assert calls == [12]


def test_sync_halts_offset_before_failed_item(monkeypatch, db, vault, tmp_path):
    good_post = {
        "message_id": 1,
        "chat": {"id": "-100"},
        "photo": [{"file_id": "GOOD"}],
    }
    bad_post = {
        "message_id": 2,
        "chat": {"id": "-100"},
        "photo": [{"file_id": "BAD"}],
    }
    later_post = {
        "message_id": 3,
        "chat": {"id": "-100"},
        "video": {"file_id": "LATER"},
    }
    updates = [
        {"update_id": 20, "channel_post": good_post},
        {"update_id": 21, "channel_post": bad_post},
        {"update_id": 22, "channel_post": later_post},
    ]

    def fake_get(url, params=None, timeout=120):
        return FakeResponse(payload={"ok": True, "result": updates})

    def fake_download(self, file_id, dest):
        if file_id == "BAD":
            raise VaultArchiveError("boom")
        good = dest.with_suffix(".jpg")
        good.write_bytes(b"bytes")
        return good

    monkeypatch.setattr("requests.get", fake_get)
    monkeypatch.setattr(TelegramVault, "download_to", fake_download)

    tv = TelegramVault(db, "TOKEN", "-100")
    result = tv.sync_from_channel(vault)
    # Only the good item is ingested; offset pauses before the failed one.
    assert result["new"] == 1
    assert result["offset"] == 20
    assert db.get_channel_offset() == 20
    with db._lock:
        rows = db._conn.execute("SELECT * FROM vault_media").fetchall()
    assert len(rows) == 1

    # Next sync retries from the failed update and then catches the later one.
    def fixed_download(self, file_id, dest):
        if file_id == "LATER":
            good = dest.with_suffix(".mp4")
            good.write_bytes(b"video bytes")
        else:
            good = dest.with_suffix(".jpg")
            good.write_bytes(b"more")
        return good

    monkeypatch.setattr(TelegramVault, "download_to", fixed_download)
    result2 = tv.sync_from_channel(vault)
    # update 20 re-processes (dedup counts as processed), 21 recovers, 22 (video) is new
    assert result2["new"] == 3
    assert result2["offset"] == 22
    assert db.get_channel_offset() == 22
    with db._lock:
        rows = db._conn.execute("SELECT * FROM vault_media").fetchall()
    assert len(rows) == 3
    assert {dict(r)["media_type"] for r in rows} == {"image", "video"}


def test_sync_ingests_video_keyed_post(monkeypatch, db, vault, tmp_path):
    video_post = {
        "message_id": 1,
        "chat": {"id": "-100"},
        "video": {"file_id": "VID"},
    }
    updates = [{"update_id": 5, "channel_post": video_post}]

    def fake_get(url, params=None, timeout=120):
        return FakeResponse(payload={"ok": True, "result": updates})

    def fake_download(self, file_id, dest):
        good = dest.with_suffix(".mp4")
        good.write_bytes(b"video bytes")
        return good

    monkeypatch.setattr("requests.get", fake_get)
    monkeypatch.setattr(TelegramVault, "download_to", fake_download)
    tv = TelegramVault(db, "TOKEN", "-100")
    result = tv.sync_from_channel(vault)
    assert result["new"] == 1
    with db._lock:
        row = db._conn.execute(
            "SELECT * FROM vault_media WHERE source = 'telegram'"
        ).fetchone()
    assert dict(row)["media_type"] == "video"


def test_download_to_fetches_file(monkeypatch, tmp_path):
    class FakePayload:
        def __init__(self):
            self._chunks = [b"cat", b"bytes"]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size=1 << 16):
            yield from self._chunks

    def fake_get(url, params=None, timeout=120, **kwargs):
        if "getFile" in url:
            return FakeResponse(
                payload={"ok": True, "result": {"file_path": "x/y.jpg"}}
            )
        return FakePayload()

    monkeypatch.setattr("requests.get", fake_get)
    dest = tmp_path / "down.bin"
    tv = TelegramVault(db=None, bot_token="T", chat_id="1")  # type: ignore[arg-type]
    out = tv.download_to("FID", dest)
    assert out.name == "down.jpg"
    assert out.read_bytes() == b"catbytes"


def test_resolve_channel_id_finds_post(monkeypatch):
    monkeypatch.setattr(
        "requests.get",
        lambda *a, **k: FakeResponse(
            payload={
                "ok": True,
                "result": [{"channel_post": {"chat": {"id": -1001234567}}}],
            }
        ),
    )
    assert TelegramVault.resolve_channel_id("TOKEN") == -1001234567


def test_resolve_channel_id_no_post_raises(monkeypatch):
    monkeypatch.setattr(
        "requests.get", lambda *a, **k: FakeResponse(payload={"ok": True, "result": []})
    )
    with pytest.raises(VaultArchiveError):
        TelegramVault.resolve_channel_id("TOKEN")