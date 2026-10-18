"""Unit tests for the official-API image-to-video producer (mocked HTTP)."""

import json

import pytest

from src.reel.api_producer import (
    ApiDriver,
    ApiError,
    ApiPaused,
    FalBackend,
    ReplicateBackend,
    _find_video_url,
    api_login_state,
    build_api_backend,
)
from src.reel.models import (
    SESSION_AUTHENTICATED,
    SESSION_LIMIT_REACHED,
    SESSION_LOGGED_OUT,
    ReelBrief,
)
from src.reel.producer import BrowserPaused, ProducerError, ReelProducer
from src.reel.selectors import ServiceSelectors


class FakeResponse:
    def __init__(self, status_code=200, payload=None, content=b"", text=""):
        self.status_code = status_code
        self._payload = payload
        self.content = content
        if text:
            self.text = text
        elif payload is not None:
            self.text = json.dumps(payload)
        else:
            self.text = ""

    def json(self):
        return self._payload


class FakeSession:
    """Routes post/get by URL; a list value is consumed one response per call."""

    def __init__(self, posts=None, gets=None):
        self.posts = posts or {}
        self.gets = gets or {}
        self.post_calls: list = []
        self.get_calls: list = []

    @staticmethod
    def _resolve(value):
        if isinstance(value, list):
            return value.pop(0) if len(value) > 1 else value[0]
        return value

    def post(self, url, json=None, headers=None, timeout=None):
        self.post_calls.append((url, json, headers))
        return self._resolve(self.posts[url])

    def get(self, url, headers=None, timeout=None):
        self.get_calls.append((url, headers))
        return self._resolve(self.gets[url])


def _brief(tmp_path, index=0) -> ReelBrief:
    src = tmp_path / f"src_{index}.jpg"
    src.write_bytes(b"image-bytes")
    return ReelBrief(
        brief_id=f"s:{index}",
        set="s",
        shot_index=index,
        account_key="cat_1",
        cat="nero",
        motion_prompt="blinks slowly",
        source_photo_path=str(src),
        source_vault_media_id=index + 1,
        expected_output=f"reel_{index:02d}.mp4",
        caption="caption",
        reel_text="hook",
        hashtags=["cat"],
        created_at="now",
    )


FAL_MODEL = "fal-ai/wan/v2.2-a14b/image-to-video"
FAL_SUBMIT = f"https://queue.fal.run/{FAL_MODEL}"


def _fal_session(*, statuses, video_url="https://cdn.fal.media/out.mp4"):
    status_url = FAL_SUBMIT + "/requests/abc/status"
    response_url = FAL_SUBMIT + "/requests/abc"
    return FakeSession(
        posts={
            FAL_SUBMIT: FakeResponse(
                200,
                {
                    "request_id": "abc",
                    "status_url": status_url,
                    "response_url": response_url,
                },
            )
        },
        gets={
            status_url: [FakeResponse(200, {"status": s}) for s in statuses],
            response_url: FakeResponse(200, {"video": {"url": video_url}}),
            video_url: FakeResponse(200, content=b"mp4-bytes"),
        },
    )


def test_fal_driver_produces_a_clip(tmp_path):
    session = _fal_session(statuses=["IN_QUEUE", "IN_PROGRESS", "COMPLETED"])
    backend = FalBackend(model=FAL_MODEL, api_key="k", session=session)
    driver = ApiDriver(config={}, poll_interval_seconds=0)
    driver.backend = backend
    dest = tmp_path / "out" / "reel_00.mp4"

    driver.generate_clip(_brief(tmp_path), dest)

    assert dest.read_bytes() == b"mp4-bytes"
    url, payload, headers = session.post_calls[0]
    assert url == FAL_SUBMIT
    assert headers["Authorization"] == "Key k"
    assert payload["input"]["prompt"] == "blinks slowly"
    assert payload["input"]["image_url"].startswith("data:image/jpeg;base64,")


def test_replicate_driver_produces_a_clip(tmp_path):
    model = "wan-video/wan-2.2-i2v-fast"
    submit = f"https://api.replicate.com/v1/models/{model}/predictions"
    get_url = "https://api.replicate.com/v1/predictions/xyz"
    video_url = "https://replicate.delivery/out.mp4"
    session = FakeSession(
        posts={
            submit: FakeResponse(
                200, {"id": "xyz", "status": "starting", "urls": {"get": get_url}}
            )
        },
        gets={
            get_url: [
                FakeResponse(200, {"status": "processing", "output": None}),
                FakeResponse(
                    200, {"status": "succeeded", "output": video_url}
                ),
            ],
            video_url: FakeResponse(200, content=b"rep-bytes"),
        },
    )
    backend = ReplicateBackend(model=model, api_key="tok", session=session)
    driver = ApiDriver(config={}, poll_interval_seconds=0)
    driver.backend = backend
    dest = tmp_path / "out" / "reel_00.mp4"

    driver.generate_clip(_brief(tmp_path), dest)

    assert dest.read_bytes() == b"rep-bytes"
    url, payload, headers = session.post_calls[0]
    assert url == submit
    assert headers["Authorization"] == "Bearer tok"
    assert payload["input"]["image"].startswith("data:image/jpeg;base64,")


def test_image_data_uri_uses_image_mime(tmp_path):
    src = tmp_path / "cat.png"
    src.write_bytes(b"\x89PNG")
    session = _fal_session(statuses=["COMPLETED"])
    backend = FalBackend(model=FAL_MODEL, api_key="k", session=session)
    brief = _brief(tmp_path)
    brief.source_photo_path = str(src)
    backend.submit(image_path=str(src), prompt="p")
    _, payload, _ = session.post_calls[0]
    assert payload["input"]["image_url"].startswith("data:image/png;base64,")


def test_failed_job_raises_api_error(tmp_path):
    session = _fal_session(statuses=["COMPLETED"])
    session.gets[FAL_SUBMIT + "/requests/abc"] = FakeResponse(200, {"detail": "boom"})
    backend = FalBackend(model=FAL_MODEL, api_key="k", session=session)
    driver = ApiDriver(config={}, poll_interval_seconds=0)
    driver.backend = backend

    with pytest.raises(ApiError):
        driver.generate_clip(_brief(tmp_path), tmp_path / "o.mp4")


def test_timeout_raises_api_error(tmp_path):
    session = _fal_session(statuses=["IN_PROGRESS"])
    backend = FalBackend(model=FAL_MODEL, api_key="k", session=session)
    backend.timeout = 0
    driver = ApiDriver(config={}, poll_interval_seconds=0)
    driver.backend = backend

    with pytest.raises(ApiError):
        driver.generate_clip(_brief(tmp_path), tmp_path / "o.mp4")


def test_bad_key_pauses_logged_out(tmp_path):
    session = FakeSession(posts={FAL_SUBMIT: FakeResponse(401, text="no")})
    backend = FalBackend(model=FAL_MODEL, api_key="k", session=session)
    with pytest.raises(ApiPaused) as exc:
        backend.submit(image_path=str(_brief(tmp_path).source_photo_path), prompt="p")
    assert exc.value.state == SESSION_LOGGED_OUT


def test_quota_error_pauses_limit_reached(tmp_path):
    session = FakeSession(posts={FAL_SUBMIT: FakeResponse(402, text="pay")})
    backend = FalBackend(model=FAL_MODEL, api_key="k", session=session)
    with pytest.raises(ApiPaused) as exc:
        backend.submit(image_path=str(_brief(tmp_path).source_photo_path), prompt="p")
    assert exc.value.state == SESSION_LIMIT_REACHED


def test_build_backend_requires_a_key(monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    with pytest.raises(ApiPaused):
        build_api_backend({"reel": {"api": {"backend": "fal", "model": "m"}}})


def test_build_backend_reads_key_from_env(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "env-key")
    backend = build_api_backend({"reel": {"api": {"backend": "fal", "model": "m"}}})
    assert backend.api_key == "env-key"
    assert backend.model == "m"


def test_build_backend_config_key_overrides_env(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "env-key")
    backend = build_api_backend(
        {"reel": {"api": {"backend": "fal", "model": "m", "api_key": "cfg-key"}}}
    )
    assert backend.api_key == "cfg-key"


def test_build_backend_unknown_backend_pauses():
    with pytest.raises(ApiPaused):
        build_api_backend({"reel": {"api": {"backend": "nope", "api_key": "k"}}})


def test_api_login_state_reflects_key(monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    assert (
        api_login_state({"reel": {"api": {"backend": "fal", "model": "m"}}})
        == SESSION_LOGGED_OUT
    )
    assert (
        api_login_state(
            {"reel": {"api": {"backend": "fal", "model": "m", "api_key": "k"}}}
        )
        == SESSION_AUTHENTICATED
    )


def test_find_video_url_walks_nested_payloads():
    payload = {"a": [{"b": "https://x/y.png"}], "video": {"url": "https://x/z.mp4"}}
    assert _find_video_url(payload) == "https://x/z.mp4"
    assert _find_video_url({"nope": "not-a-url"}) is None


def test_api_paused_is_a_browser_paused():
    assert issubclass(ApiPaused, BrowserPaused)
    assert issubclass(ApiError, ProducerError)


def test_reel_producer_pauses_on_api_key_fault(tmp_path, monkeypatch):
    # A missing key surfaces inside the batch loop as a clean pause.
    monkeypatch.delenv("FAL_KEY", raising=False)
    briefs = [_brief(tmp_path, 0)]
    out = tmp_path / "out"
    alerts: list[str] = []
    producer = ReelProducer(
        selectors=ServiceSelectors(service="api"),
        profile_dir="x",
        service_url="",
        alerter=alerts.append,
        driver_factory=lambda: ApiDriver(
            config={"reel": {"api": {"backend": "fal", "model": "m"}}}
        ),
    )
    result = producer.produce(briefs, out)
    assert result.paused is True
    assert result.paused_state == SESSION_LOGGED_OUT
    assert alerts  # the operator is alerted


def test_produce_set_uses_api_provider(tmp_path, monkeypatch):
    import src.reel.api_producer as api_mod
    import src.reel.producer as producer_mod

    out_root = tmp_path / "generated"
    set_dir = out_root / "s"
    set_dir.mkdir(parents=True)
    (set_dir / "reel_brief_00.json").write_text(
        json.dumps(_brief(tmp_path, 0).to_dict()), encoding="utf-8"
    )

    class FakeApiDriver:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            pass

        def close(self):
            pass

        def generate_clip(self, brief, dest, *, timeout_seconds=None):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"api-clip")

    monkeypatch.setattr(api_mod, "ApiDriver", FakeApiDriver)
    config = {
        "creative": {"out_root": str(out_root)},
        "reel": {"provider": "api", "api": {"backend": "fal", "model": "m"}},
    }
    result = producer_mod.produce_set(config, "s")
    assert result.produced == 1
    assert (set_dir / "reel_00.mp4").read_bytes() == b"api-clip"
