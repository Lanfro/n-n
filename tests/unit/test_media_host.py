"""Unit tests for the MediaHost (NoHost / S3 / R2) interface."""

import pytest

from src.vault.media_host import (
    MediaHostConfigError,
    NoHost,
    R2Host,
    S3Host,
    build_media_host,
)

HOST_CFG = {
    "bucket": "cat-agent-vault",
    "public_base_url": "https://vault.example.dev",
    "access_key_id": "key",
    "secret_access_key": "secret",
    "endpoint_url": "https://acct.r2.cloudflarestorage.com",
}


def test_nohost_is_default_and_returns_none():
    host = NoHost()
    assert host.backend == "none"
    assert host.is_configured() is False
    assert host.upload("data/input_media/cat.jpg") is None
    assert host.delete("media/ab/cat.jpg") is False


def test_s3host_missing_config_raises():
    with pytest.raises(MediaHostConfigError):
        S3Host(**{**HOST_CFG, "bucket": ""})


def test_r2host_is_s3_with_r2_backend():
    host = R2Host(**HOST_CFG)
    assert host.backend == "r2"
    assert host.is_configured()


def test_build_media_host_none_backend():
    host = build_media_host({"host": {"backend": "none"}})
    assert isinstance(host, NoHost)
    assert build_media_host({"host": None}).backend == "none"


def test_build_media_host_r2():
    host = build_media_host({"host": {"backend": "r2", **HOST_CFG}})
    assert isinstance(host, R2Host)


def test_upload_returns_public_url(monkeypatch, tmp_path):
    img = tmp_path / "cat.jpg"
    img.write_bytes(b"fake image bytes")

    uploaded = {}

    class FakeClient:
        def upload_file(self, path, bucket, key):
            uploaded["path"] = path
            uploaded["bucket"] = bucket
            uploaded["key"] = key

        def delete_object(self, **kwargs):
            uploaded["deleted"] = kwargs

    monkeypatch.setattr(
        "src.vault.media_host.S3Host._get_client", lambda self: FakeClient()
    )

    host = S3Host(**HOST_CFG)
    url = host.upload(str(img))
    assert url is not None
    assert url.startswith("https://vault.example.dev/media/")
    assert uploaded["bucket"] == "cat-agent-vault"
    assert uploaded["key"].endswith(".jpg")


def test_object_key_is_sha_based(tmp_path):
    img = tmp_path / "cat.jpg"
    img.write_bytes(b"fixed bytes")
    host = S3Host(**HOST_CFG)
    key = host._object_key(str(img), "")
    assert key.startswith("media/")
    parts = key.split("/")
    assert len(parts) == 3
    assert parts[1] == parts[2][:2]


def test_delete_returns_true(monkeypatch):
    monkeypatch.setattr(
        "src.vault.media_host.S3Host._get_client",
        lambda self: type(
            "FakeClient",
            (),
            {"delete_object": lambda self, **k: None},
        )(),
    )
    host = S3Host(**HOST_CFG)
    assert host.delete("media/ab/cat.jpg") is True


def test_delete_error_swallowed(monkeypatch):
    class FakeClient:
        def delete_object(self, **kwargs):
            raise OSError("nope")

    monkeypatch.setattr(
        "src.vault.media_host.S3Host._get_client", lambda self: FakeClient()
    )
    host = S3Host(**HOST_CFG)
    assert host.delete("media/ab/cat.jpg") is False