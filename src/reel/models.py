"""Shared reel dataclasses.

Mirrors `specs/002-video-reel-pipeline/data-model.md`:

* ``ReelBrief``          - the per-shot production order (JSON handoff)
* ``ClipReport``         - probed facts + validation outcome for a clip
* ``BrowserSessionState``- the operator-authenticated external-service session
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

# Validation outcomes (contracts/reel-clip-requirements.md)
VALIDATION_VALID = "valid"
VALIDATION_REPAIRED = "repaired"
VALIDATION_REJECTED = "rejected"
VALIDATION_OUTCOMES = {VALIDATION_VALID, VALIDATION_REPAIRED, VALIDATION_REJECTED}

# Browser session states (contracts/video-service-browser.md)
SESSION_AUTHENTICATED = "authenticated"
SESSION_LOGGED_OUT = "logged_out"
SESSION_CHALLENGED = "challenged"
SESSION_LIMIT_REACHED = "limit_reached"
SESSION_STATES = {
    SESSION_AUTHENTICATED,
    SESSION_LOGGED_OUT,
    SESSION_CHALLENGED,
    SESSION_LIMIT_REACHED,
}


@dataclass
class ReelBrief:
    """A self-contained production order for one shot."""

    brief_id: str
    set: str
    shot_index: int
    account_key: str
    cat: str
    motion_prompt: str
    source_photo_path: str
    source_vault_media_id: int
    expected_output: str
    caption: str
    reel_text: str
    hashtags: list[str] = field(default_factory=list)
    aspect: str = "9:16"
    duration_seconds: int = 5
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ReelBrief:
        return cls(
            brief_id=str(data["brief_id"]),
            set=str(data["set"]),
            shot_index=int(data["shot_index"]),
            account_key=str(data["account_key"]),
            cat=str(data["cat"]),
            motion_prompt=str(data["motion_prompt"]),
            source_photo_path=str(data["source_photo_path"]),
            source_vault_media_id=int(data["source_vault_media_id"]),
            expected_output=str(data["expected_output"]),
            caption=str(data["caption"]),
            reel_text=str(data["reel_text"]),
            hashtags=list(data.get("hashtags", [])),
            aspect=str(data.get("aspect", "9:16")),
            duration_seconds=int(data.get("duration_seconds", 5)),
            created_at=str(data.get("created_at", "")),
        )


@dataclass
class ClipReport:
    """Technical facts about a produced clip plus its validation outcome."""

    path: str
    brief_id: str = ""
    container: str = ""
    video_codec: str = ""
    audio_codec: str = ""
    has_audio: bool = False
    width: int = 0
    height: int = 0
    aspect_ratio: float = 0.0
    duration_seconds: float = 0.0
    fps: float = 0.0
    size_bytes: int = 0
    validation: str = VALIDATION_REJECTED
    reject_reason: str | None = None
    caveats: list[str] = field(default_factory=list)

    @property
    def eligible(self) -> bool:
        """True when the clip may be offered for approval (not rejected)."""
        return self.validation in {VALIDATION_VALID, VALIDATION_REPAIRED}

    def to_dict(self) -> dict:
        out = asdict(self)
        out["eligible"] = self.eligible
        return out

    @classmethod
    def from_dict(cls, data: dict) -> ClipReport:
        return cls(
            path=str(data["path"]),
            brief_id=str(data.get("brief_id", "")),
            container=str(data.get("container", "")),
            video_codec=str(data.get("video_codec", "")),
            audio_codec=str(data.get("audio_codec", "")),
            has_audio=bool(data.get("has_audio", False)),
            width=int(data.get("width", 0)),
            height=int(data.get("height", 0)),
            aspect_ratio=float(data.get("aspect_ratio", 0.0)),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
            fps=float(data.get("fps", 0.0)),
            size_bytes=int(data.get("size_bytes", 0)),
            validation=str(data.get("validation", VALIDATION_REJECTED)),
            reject_reason=data.get("reject_reason"),
            caveats=list(data.get("caveats", [])),
        )


@dataclass
class BrowserSessionState:
    """State of the operator-authenticated external-service browser session."""

    service: str
    profile_dir: str
    state: str = SESSION_LOGGED_OUT
    last_used_at: str = ""

    @property
    def usable(self) -> bool:
        """True only when the session can attempt generation unattended."""
        return self.state == SESSION_AUTHENTICATED

    def to_dict(self) -> dict:
        return asdict(self)
