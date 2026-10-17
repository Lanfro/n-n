"""External video-service selectors, isolated in one place.

All service-specific URLs, CSS selectors and challenge/limit markers live in
`config/reel/selectors.json` and are loaded here so that UI drift is a
one-file repair and no service-specific selector appears elsewhere in the
codebase (contracts/video-service-browser.md § Selector isolation).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


class SelectorsError(RuntimeError):
    pass


@dataclass
class ServiceSelectors:
    service: str = ""
    urls: dict[str, str] = field(default_factory=dict)
    selectors: dict[str, list[str]] = field(default_factory=dict)
    markers: dict[str, list[str]] = field(default_factory=dict)

    def url(self, key: str, default: str = "") -> str:
        return self.urls.get(key, default)

    def get(self, key: str) -> list[str]:
        """Candidate selectors for a UI element (empty list when unknown)."""
        value = self.selectors.get(key)
        return list(value) if value else []

    def marker(self, key: str) -> list[str]:
        """Candidate selectors signalling a challenge/limit condition."""
        value = self.markers.get(key)
        return list(value) if value else []


def load_selectors(path: str | Path) -> ServiceSelectors:
    """Load and validate the selectors file, failing loud on bad JSON."""
    p = Path(path)
    if not p.exists():
        raise SelectorsError(f"Selectors file not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SelectorsError(f"Invalid selectors JSON at {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise SelectorsError(f"Selectors file must be a JSON object: {p}")
    selectors = ServiceSelectors(
        service=str(data.get("service", "")),
        urls=dict(data.get("urls", {}) or {}),
        selectors={
            k: list(v) for k, v in (data.get("selectors", {}) or {}).items()
        },
        markers=dict(data.get("markers", {}) or {}),
    )
    logger.debug(
        "Loaded selectors for service '%s' (%d element groups)",
        selectors.service,
        len(selectors.selectors),
    )
    return selectors
