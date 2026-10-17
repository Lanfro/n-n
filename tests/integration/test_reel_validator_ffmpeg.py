"""Real ffmpeg/ffprobe integration tests for the clip validator.

Generates genuine MP4 clips with ffmpeg and validates them with the real
ffprobe/ffmpeg pipeline (no fake runner). Skipped automatically when the tools
are not on PATH, so the suite stays green on machines without ffmpeg.

Complements the mocked unit tests in ``tests/unit/test_reel_validator.py``.
"""

import shutil
import subprocess

import pytest

from src.reel.models import VALIDATION_REJECTED, VALIDATION_REPAIRED, VALIDATION_VALID
from src.reel.validator import validate_clip

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)

CONFIG = {
    "min_resolution": [540, 960],
    "target_resolution": [1080, 1920],
    "min_duration_seconds": 3,
    "max_duration_seconds": 900,
    "max_size_mb": 300,
    "aspect": "9:16",
}


def _make_clip(path, w, h, dur, *, audio=True):
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=orange:s={w}x{h}:d={dur}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd += [str(path)]
    subprocess.run(cmd, capture_output=True, check=True)
    return path


def test_compliant_clip_is_valid(tmp_path):
    clip = _make_clip(tmp_path / "valid.mp4", 1080, 1920, 6)
    report = validate_clip(clip, config=CONFIG)
    assert report.validation == VALIDATION_VALID
    assert report.eligible is True
    assert report.width == 1080 and report.height == 1920
    assert report.container == "mp4"
    assert report.has_audio is True


def test_silent_clip_is_repaired(tmp_path):
    clip = _make_clip(tmp_path / "silent.mp4", 1080, 1920, 6, audio=False)
    report = validate_clip(clip, config=CONFIG)
    assert report.validation == VALIDATION_REPAIRED
    assert report.eligible is True
    assert "silent" in report.caveats
    assert report.has_audio is True  # silent AAC track was muxed


def test_low_resolution_clip_is_upscaled(tmp_path):
    clip = _make_clip(tmp_path / "lowres.mp4", 480, 854, 6)
    report = validate_clip(clip, config=CONFIG)
    assert report.validation == VALIDATION_REPAIRED
    assert report.eligible is True
    assert "upscaled" in report.caveats
    assert report.height >= 960


def test_landscape_clip_is_rejected(tmp_path):
    clip = _make_clip(tmp_path / "landscape.mp4", 1920, 1080, 6)
    report = validate_clip(clip, config=CONFIG)
    assert report.validation == VALIDATION_REJECTED
    assert report.eligible is False
    assert "portrait" in (report.reject_reason or "")


def test_too_short_clip_is_rejected(tmp_path):
    clip = _make_clip(tmp_path / "short.mp4", 1080, 1920, 2)
    report = validate_clip(clip, config=CONFIG)
    assert report.validation == VALIDATION_REJECTED
    assert "short" in (report.reject_reason or "")
