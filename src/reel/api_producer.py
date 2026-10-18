"""Official image-to-video HTTP API producer (no browser).

Alternative to the assisted browser producer (:mod:`src.reel.producer`): instead
of driving a logged-in web session, it calls a hosted image-to-video service's
official HTTP API and downloads the finished clip. Auth is a single API key
(config or environment); there is no browser, no selectors and no session to
babysit, which is the point - UI drift cannot break it.

Supported backends (``reel.api.backend``):

* ``fal``       - fal.ai queue API (``https://queue.fal.run``).
* ``replicate`` - Replicate predictions API (``https://api.replicate.com``).

Both accept the source photo as a base64 ``data:`` URI, so no public hosting
step is needed. The finished video URL is discovered generically (the first
video-like URL in the response), because output schemas vary per model.

Faults pause the batch instead of crashing it: :class:`ApiPaused` is a
:class:`BrowserPaused` subclass, and :class:`ApiError` is a
:class:`ProducerError` subclass, so the existing ``ReelProducer.produce`` loop
handles both unchanged. The driver exposes the same ``start`` / ``close`` /
``generate_clip`` surface as the Playwright driver and is injected through the
existing ``driver_factory`` hook.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
import time
from pathlib import Path
from typing import Any

import requests

from .models import (
    SESSION_AUTHENTICATED,
    SESSION_LIMIT_REACHED,
    SESSION_LOGGED_OUT,
    ReelBrief,
)
from .producer import BrowserPaused, ProducerError

logger = logging.getLogger(__name__)

# Normalised job states returned by the backends' ``poll`` implementations.
_STATUS_QUEUED = "queued"
_STATUS_RUNNING = "in_progress"
_STATUS_SUCCEEDED = "succeeded"
_STATUS_FAILED = "failed"
_STATUS_CANCELED = "canceled"

_VIDEO_EXTS = (".mp4", ".webm", ".mov", ".m4v")

_DEFAULT_KEY_ENV = {"fal": "FAL_KEY", "replicate": "REPLICATE_API_TOKEN"}
_DEFAULT_MODELS = {
    # Apache-2.0 (commercial use OK). Override in config if it drifts.
    "fal": "fal-ai/wan/v2.2-a14b/image-to-video",
    "replicate": "wan-video/wan-2.2-i2v-fast",
}


class ApiError(ProducerError):
    """A single job failed in a way that should not abort the whole batch."""


class ApiPaused(BrowserPaused):
    """Raised to pause the batch (auth/config/payment/quota/unreachable)."""


def _data_uri(path: str | Path) -> str:
    """Read a local image and return a ``data:`` URI for API upload."""
    p = Path(path)
    mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
    encoded = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _find_video_url(payload: Any) -> str | None:
    """First video-like URL anywhere in a (possibly nested) API response."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            cleaned = node.lower().split("?")[0]
            if node.startswith("http") and cleaned.endswith(_VIDEO_EXTS):
                found.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(payload)
    return found[0] if found else None


class _Backend:
    """Common HTTP/polling shell; subclasses map one provider's wire format."""

    name = ""
    default_base_url = ""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        params: dict | None = None,
        timeout_seconds: int = 600,
        base_url: str = "",
        session: Any | None = None,
    ):
        self.model = model
        self.api_key = api_key
        self.params = dict(params or {})
        self.timeout = timeout_seconds
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.session = session or requests.Session()

    @property
    def headers(self) -> dict[str, str]:
        raise NotImplementedError

    def submit(self, *, image_path: str | Path, prompt: str) -> dict:
        raise NotImplementedError

    def poll(self, job: dict) -> str:
        raise NotImplementedError

    def result_url(self, job: dict) -> str:
        raise NotImplementedError

    def _raise_for_status(self, resp: Any) -> None:
        status = getattr(resp, "status_code", 0)
        if status in (401, 403):
            raise ApiPaused(
                SESSION_LOGGED_OUT,
                f"{self.name} rejected the API key (HTTP {status})",
            )
        if status in (402, 429):
            raise ApiPaused(
                SESSION_LIMIT_REACHED,
                f"{self.name} quota/payment error (HTTP {status})",
            )
        if status >= 400:
            body = getattr(resp, "text", "")
            raise ApiError(f"{self.name} HTTP {status}: {str(body)[:300]}")

    def download(self, url: str, dest: Path) -> None:
        resp = self.session.get(url, timeout=self.timeout)
        if getattr(resp, "status_code", 0) >= 400:
            raise ApiError(
                f"download failed: HTTP {resp.status_code} for {url}"
            )
        dest.write_bytes(resp.content)


class FalBackend(_Backend):
    """fal.ai queue API: submit -> poll status -> fetch response."""

    name = "fal"
    default_base_url = "https://queue.fal.run"

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Key {self.api_key}"}

    def submit(self, *, image_path: str | Path, prompt: str) -> dict:
        payload = {
            "input": {
                **self.params,
                "prompt": prompt,
                "image_url": _data_uri(image_path),
            }
        }
        url = f"{self.base_url}/{self.model}"
        resp = self.session.post(url, json=payload, headers=self.headers, timeout=60)
        self._raise_for_status(resp)
        job = dict(resp.json())
        request_id = job.get("request_id", "")
        job.setdefault("status_url", f"{url}/requests/{request_id}/status")
        job.setdefault("response_url", f"{url}/requests/{request_id}")
        return job

    def poll(self, job: dict) -> str:
        resp = self.session.get(
            job["status_url"], headers=self.headers, timeout=60
        )
        self._raise_for_status(resp)
        raw = str(resp.json().get("status", "")).upper()
        return {
            "IN_QUEUE": _STATUS_QUEUED,
            "IN_PROGRESS": _STATUS_RUNNING,
            "COMPLETED": _STATUS_SUCCEEDED,
            "FAILED": _STATUS_FAILED,
        }.get(raw, _STATUS_RUNNING)

    def result_url(self, job: dict) -> str:
        resp = self.session.get(
            job["response_url"], headers=self.headers, timeout=60
        )
        self._raise_for_status(resp)
        data = resp.json()
        url = _find_video_url(data)
        if not url:
            raise ApiError(
                f"{self.name} returned no video URL: {str(data)[:300]}"
            )
        return url


class ReplicateBackend(_Backend):
    """Replicate predictions API: create -> poll ``urls.get`` -> output."""

    name = "replicate"
    default_base_url = "https://api.replicate.com/v1"

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def submit(self, *, image_path: str | Path, prompt: str) -> dict:
        payload = {
            "input": {
                **self.params,
                "prompt": prompt,
                "image": _data_uri(image_path),
            }
        }
        url = f"{self.base_url}/models/{self.model}/predictions"
        resp = self.session.post(url, json=payload, headers=self.headers, timeout=60)
        self._raise_for_status(resp)
        return dict(resp.json())

    def poll(self, job: dict) -> str:
        raw = str(job.get("status", "")).lower()
        if raw not in ("starting", "processing"):
            return {
                "succeeded": _STATUS_SUCCEEDED,
                "failed": _STATUS_FAILED,
                "canceled": _STATUS_CANCELED,
            }.get(raw, raw or _STATUS_RUNNING)
        get_url = (job.get("urls") or {}).get("get")
        if get_url:
            resp = self.session.get(get_url, headers=self.headers, timeout=60)
            self._raise_for_status(resp)
            job.update(resp.json())
            raw = str(job.get("status", "")).lower()
        return {
            "starting": _STATUS_QUEUED,
            "processing": _STATUS_RUNNING,
            "succeeded": _STATUS_SUCCEEDED,
            "failed": _STATUS_FAILED,
            "canceled": _STATUS_CANCELED,
        }.get(raw, _STATUS_RUNNING)

    def result_url(self, job: dict) -> str:
        url = _find_video_url(job.get("output"))
        if not url:
            raise ApiError(
                f"{self.name} returned no video URL: {str(job.get('output'))[:300]}"
            )
        return url


_BACKENDS: dict[str, type[_Backend]] = {
    "fal": FalBackend,
    "replicate": ReplicateBackend,
}


def build_api_backend(config: dict, *, session: Any | None = None) -> _Backend:
    """Construct the configured backend, resolving the API key from config/env.

    Raises :class:`ApiPaused` (a human-action fault) when the backend name,
    model or API key is missing, so a misconfiguration pauses the batch with an
    operator alert instead of crashing it.
    """
    reel_cfg = config.get("reel", {}) or {}
    api_cfg = reel_cfg.get("api", {}) or {}
    backend_name = str(api_cfg.get("backend", "fal")).strip().lower()
    if backend_name not in _BACKENDS:
        raise ApiPaused(
            SESSION_LOGGED_OUT,
            f"unknown reel.api.backend {backend_name!r} "
            f"(expected one of {sorted(_BACKENDS)})",
        )
    model = str(api_cfg.get("model", "")).strip() or _DEFAULT_MODELS[backend_name]
    api_key = str(api_cfg.get("api_key", "")).strip()
    env_name = (
        str(api_cfg.get("api_key_env", "")).strip()
        or _DEFAULT_KEY_ENV[backend_name]
    )
    if not api_key:
        api_key = os.environ.get(env_name, "").strip()
    if not api_key:
        raise ApiPaused(
            SESSION_LOGGED_OUT,
            f"no {backend_name} API key (set reel.api.api_key or ${env_name})",
        )
    return _BACKENDS[backend_name](
        model=model,
        api_key=api_key,
        params=api_cfg.get("params") or {},
        timeout_seconds=int(reel_cfg.get("generation_timeout_seconds", 600)),
        base_url=str(api_cfg.get("base_url", "")),
        session=session,
    )


class ApiDriver:
    """API-backed driver with the same surface as the Playwright driver.

    ``start``/``close`` are no-ops; the backend is built lazily on the first
    ``generate_clip`` so any config/key fault surfaces *inside* the per-brief
    loop and pauses the batch cleanly (rather than escaping ``produce``).
    """

    def __init__(self, *, config: dict, poll_interval_seconds: float = 5.0,
                 session: Any | None = None):
        self.config = config
        self.poll_interval = poll_interval_seconds
        self.session = session
        self.backend: _Backend | None = None

    def start(self) -> None:
        return None

    def close(self) -> None:
        return None

    def _backend(self) -> _Backend:
        if self.backend is None:
            self.backend = build_api_backend(self.config, session=self.session)
        return self.backend

    def generate_clip(
        self, brief: ReelBrief, dest: str | Path, *, timeout_seconds: int | None = None
    ) -> None:
        backend = self._backend()
        timeout = timeout_seconds or backend.timeout
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Submitting %s to %s (%s)", brief.brief_id, backend.name, backend.model
        )
        job = backend.submit(
            image_path=brief.source_photo_path, prompt=brief.motion_prompt
        )
        deadline = time.monotonic() + timeout
        while True:
            status = backend.poll(job)
            if status == _STATUS_SUCCEEDED:
                break
            if status in (_STATUS_FAILED, _STATUS_CANCELED):
                raise ApiError(f"{backend.name} job {status}")
            if time.monotonic() >= deadline:
                raise ApiError(
                    f"{backend.name} timed out after {timeout}s "
                    f"(last status: {status})"
                )
            time.sleep(self.poll_interval)
        url = backend.result_url(job)
        backend.download(url, dest)
        logger.info("Downloaded %s from %s", dest.name, backend.name)


def api_login_state(config: dict) -> str:
    """Report whether the API provider is usable (an API key is configured).

    The API has no interactive login; ``--reel-login`` uses this to tell the
    operator whether the configured key is present.
    """
    try:
        build_api_backend(config)
    except BrowserPaused:
        return SESSION_LOGGED_OUT
    return SESSION_AUTHENTICATED
