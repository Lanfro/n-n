"""Unit tests for the clip validator (ffprobe parse + allowed repairs).

Uses a fake runner so no real ffmpeg/ffprobe is required: probe calls return
canned ffprobe JSON; ffmpeg calls create the temp output file the repair expects.
"""

import json
from pathlib import Path
from types import SimpleNamespace

from src.reel.models import VALIDATION_REJECTED, VALIDATION_REPAIRED, VALIDATION_VALID
from src.reel.validator import validate_clip

CONFIG = {
    "min_resolution": [540, 960],
    "target_resolution": [1080, 1920],
    "min_duration_seconds": 3,
    "max_duration_seconds": 900,
    "preferred_min_duration_seconds": 5,
    "max_size_mb": 300,
    "aspect": "9:16",
}


def probe(
    *,
    vcodec="h264",
    w=1080,
    h=1920,
    fps="30/1",
    dur="6.0",
    size=5_000_000,
    acodec="aac",
    fmt="mov,mp4,m4a,3gp,3g2,mj2",
):
    streams = [
        {
            "codec_type": "video",
            "codec_name": vcodec,
            "width": w,
            "height": h,
            "r_frame_rate": fps,
        }
    ]
    if acodec:
        streams.append({"codec_type": "audio", "codec_name": acodec})
    return {
        "format": {"format_name": fmt, "duration": dur, "size": str(size)},
        "streams": streams,
    }


class FakeRunner:
    def __init__(self, probes, *, ffprobe_missing=False, ffprobe_rc=0, bad_json=False):
        self.probes = probes
        self.ffprobe_missing = ffprobe_missing
        self.ffprobe_rc = ffprobe_rc
        self.bad_json = bad_json
        self.i = 0
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **_kwargs):
        self.calls.append(list(cmd))
        if self.ffprobe_missing:
            raise FileNotFoundError(cmd[0])
        if "-show_streams" in cmd:
            if self.ffprobe_rc != 0:
                return SimpleNamespace(returncode=self.ffprobe_rc, stdout="", stderr="boom")
            if self.bad_json:
                return SimpleNamespace(returncode=0, stdout="{not json", stderr="")
            data = self.probes[min(self.i, len(self.probes) - 1)]
            self.i += 1
            return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")
        # ffmpeg repair: create the temp output the repair expects
        Path(cmd[-1]).write_bytes(b"ffmpeg-output")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def _clip(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"original")
    return path


def test_valid_clip(tmp_path):
    path = _clip(tmp_path)
    report = validate_clip(path, config=CONFIG, runner=FakeRunner([probe()]))
    assert report.validation == VALIDATION_VALID
    assert report.eligible is True
    assert report.width == 1080 and report.height == 1920
    assert report.container == "mp4"
    assert report.has_audio is True
    assert report.caveats == []


def test_silent_clip_is_repaired(tmp_path):
    path = _clip(tmp_path)
    runner = FakeRunner([probe(acodec=None), probe(acodec="aac")])
    report = validate_clip(path, config=CONFIG, runner=runner)
    assert report.validation == VALIDATION_REPAIRED
    assert report.eligible is True
    assert "silent" in report.caveats
    assert report.has_audio is True


def test_low_resolution_is_upscaled(tmp_path):
    path = _clip(tmp_path)
    runner = FakeRunner([probe(w=480, h=854), probe(w=540, h=960)])
    report = validate_clip(path, config=CONFIG, runner=runner)
    assert report.validation == VALIDATION_REPAIRED
    assert report.eligible is True
    assert "upscaled" in report.caveats
    assert report.height == 960


def test_landscape_is_rejected(tmp_path):
    path = _clip(tmp_path)
    report = validate_clip(
        path, config=CONFIG, runner=FakeRunner([probe(w=1920, h=1080)])
    )
    assert report.validation == VALIDATION_REJECTED
    assert report.eligible is False
    assert "portrait" in (report.reject_reason or "")


def test_too_short_is_rejected(tmp_path):
    path = _clip(tmp_path)
    report = validate_clip(path, config=CONFIG, runner=FakeRunner([probe(dur="2.0")]))
    assert report.validation == VALIDATION_REJECTED
    assert "short" in (report.reject_reason or "")


def test_oversize_is_rejected(tmp_path):
    path = _clip(tmp_path)
    runner = FakeRunner([probe(size=400 * 1024 * 1024)])
    report = validate_clip(path, config=CONFIG, runner=runner)
    assert report.validation == VALIDATION_REJECTED
    assert "large" in (report.reject_reason or "")


def test_unsupported_codec_is_rejected(tmp_path):
    path = _clip(tmp_path)
    report = validate_clip(path, config=CONFIG, runner=FakeRunner([probe(vcodec="vp9")]))
    assert report.validation == VALIDATION_REJECTED
    assert "codec" in (report.reject_reason or "")


def test_ffprobe_unavailable_is_rejected(tmp_path):
    path = _clip(tmp_path)
    runner = FakeRunner([probe()], ffprobe_missing=True)
    report = validate_clip(path, config=CONFIG, runner=runner)
    assert report.validation == VALIDATION_REJECTED
    assert "unavailable" in (report.reject_reason or "")


def test_unreadable_media_is_rejected(tmp_path):
    path = _clip(tmp_path)
    runner = FakeRunner([probe()], ffprobe_rc=1)
    report = validate_clip(path, config=CONFIG, runner=runner)
    assert report.validation == VALIDATION_REJECTED
    assert "unreadable" in (report.reject_reason or "")


def test_missing_file_is_rejected(tmp_path):
    report = validate_clip(
        tmp_path / "nope.mp4", config=CONFIG, runner=FakeRunner([probe()])
    )
    assert report.validation == VALIDATION_REJECTED
    assert "not found" in (report.reject_reason or "")
