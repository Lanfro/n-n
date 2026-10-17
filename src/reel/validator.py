"""Clip validation (ffprobe) and composition-preserving repair (ffmpeg).

Single source of truth for "publishable clip": see
``specs/002-video-reel-pipeline/contracts/reel-clip-requirements.md``.

Validation reads the clip with ``ffprobe`` and, when needed, applies at most the
two allowed repairs with ``ffmpeg``:

1. missing audio  -> mux a silent AAC track (``anullsrc``)
2. below the minimum resolution -> upscale to the floor (quality-preserving)

No cropping, stretching, or re-timing is ever performed. When ``ffprobe`` is
unavailable the clip is treated as ``rejected`` (fail safe: never publish
unverified media).
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .models import (
    VALIDATION_REJECTED,
    VALIDATION_REPAIRED,
    VALIDATION_VALID,
    ClipReport,
)

logger = logging.getLogger(__name__)

_MB = 1024 * 1024
_ALLOWED_VIDEO_CODECS = {"h264", "hevc"}
_ALLOWED_CONTAINERS = {"mp4", "mov"}
_DEFAULT_MIN_RESOLUTION = (540, 960)


def _default_runner(cmd: list[str], timeout: int = 120) -> Any:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_fps(rate: Any) -> float:
    text = str(rate or "").strip()
    if not text:
        return 0.0
    if "/" in text:
        num, _, den = text.partition("/")
        den_f = _to_float(den)
        if den_f == 0:
            return 0.0
        return _to_float(num) / den_f
    return _to_float(text)


def _container_of(format_name: str) -> str:
    name = (format_name or "").lower()
    if "mp4" in name:
        return "mp4"
    if "mov" in name or "quicktime" in name:
        return "mov"
    return name


def _reject(report: ClipReport, reason: str) -> ClipReport:
    report.validation = VALIDATION_REJECTED
    report.reject_reason = reason
    logger.warning("Clip rejected (%s): %s", reason, report.path)
    return report


def _probe(
    path: Path, ffprobe_bin: str, runner: Callable[..., Any]
) -> tuple[dict | None, str | None]:
    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        proc = runner(cmd)
    except FileNotFoundError:
        return None, f"media inspection unavailable: '{ffprobe_bin}' not found"
    except subprocess.TimeoutExpired:
        return None, "media inspection timed out"
    except Exception as exc:  # noqa: BLE001 - any probe failure is a rejection
        return None, f"media inspection failed: {exc}"

    if getattr(proc, "returncode", 1) != 0:
        detail = (getattr(proc, "stderr", "") or "").strip().splitlines()
        tail = detail[-1] if detail else "ffprobe error"
        return None, f"unreadable media ({tail})"
    try:
        data = json.loads(getattr(proc, "stdout", "") or "{}")
    except ValueError:
        return None, "unreadable media (invalid ffprobe output)"
    if not data.get("streams"):
        return None, "no media streams found"
    return data, None


def _fill(report: ClipReport, data: dict) -> None:
    fmt = data.get("format", {}) or {}
    report.container = _container_of(fmt.get("format_name", ""))
    report.duration_seconds = _to_float(fmt.get("duration"))
    report.size_bytes = int(_to_float(fmt.get("size")))

    video = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
        None,
    )
    audio = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "audio"),
        None,
    )
    if video:
        report.video_codec = str(video.get("codec_name", ""))
        report.width = int(video.get("width", 0) or 0)
        report.height = int(video.get("height", 0) or 0)
        report.fps = _parse_fps(video.get("r_frame_rate", ""))
    report.has_audio = audio is not None
    report.audio_codec = str(audio.get("codec_name", "")) if audio else ""
    report.aspect_ratio = (
        report.width / report.height if report.height else 0.0
    )


def _run_repair(cmd: list[str], runner: Callable[..., Any]) -> bool:
    try:
        proc = runner(cmd)
    except Exception as exc:  # noqa: BLE001 - repair best-effort
        logger.warning("ffmpeg repair failed: %s", exc)
        return False
    return getattr(proc, "returncode", 1) == 0


def _mux_silent_audio(
    path: Path, ffmpeg_bin: str, runner: Callable[..., Any]
) -> bool:
    tmp = path.with_suffix(".silent.mp4")
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(path),
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-shortest",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(tmp),
    ]
    if not _run_repair(cmd, runner) or not tmp.exists() or tmp.stat().st_size == 0:
        return False
    tmp.replace(path)
    return True


def _upscale(
    path: Path, target_h: int, ffmpeg_bin: str, runner: Callable[..., Any]
) -> bool:
    tmp = path.with_suffix(".up.mp4")
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(path),
        "-vf",
        f"scale=-2:{target_h}:flags=lanczos",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(tmp),
    ]
    if not _run_repair(cmd, runner) or not tmp.exists() or tmp.stat().st_size == 0:
        return False
    tmp.replace(path)
    return True


def validate_clip(
    path: str | Path,
    *,
    config: dict | None = None,
    brief_id: str = "",
    ffprobe_bin: str = "ffprobe",
    ffmpeg_bin: str = "ffmpeg",
    runner: Callable[..., Any] | None = None,
) -> ClipReport:
    """Validate a single clip, applying allowed repairs, and return a report.

    Rejection reasons are recorded on ``ClipReport.reject_reason``; the clip is
    never modified beyond the two allowed repairs.
    """
    cfg = config or {}
    run = runner or _default_runner
    min_w, min_h = cfg.get("min_resolution", _DEFAULT_MIN_RESOLUTION)
    min_dur = float(cfg.get("min_duration_seconds", 3))
    max_dur = float(cfg.get("max_duration_seconds", 900))
    max_bytes = float(cfg.get("max_size_mb", 300)) * _MB

    p = Path(path)
    report = ClipReport(path=str(p), brief_id=brief_id)

    if not p.exists():
        return _reject(report, "clip file not found")
    if p.stat().st_size == 0:
        return _reject(report, "clip file is empty")

    data, err = _probe(p, ffprobe_bin, run)
    if err:
        return _reject(report, err)
    assert data is not None
    _fill(report, data)

    if report.container not in _ALLOWED_CONTAINERS:
        return _reject(
            report, f"unsupported container: {report.container or 'unknown'}"
        )
    if report.video_codec not in _ALLOWED_VIDEO_CODECS:
        return _reject(
            report,
            f"unsupported video codec: {report.video_codec or 'unknown'}",
        )
    if report.height <= report.width:
        return _reject(report, "not portrait (cannot satisfy 9:16)")
    if report.duration_seconds < min_dur:
        return _reject(
            report,
            f"too short ({report.duration_seconds:.1f}s < {min_dur:.0f}s)",
        )
    if report.duration_seconds > max_dur:
        return _reject(
            report,
            f"too long ({report.duration_seconds:.0f}s > {max_dur:.0f}s)",
        )
    if report.size_bytes > max_bytes:
        return _reject(
            report,
            f"too large ({report.size_bytes / _MB:.0f}MB > {max_bytes / _MB:.0f}MB)",
        )
    if report.video_codec == "hevc":
        report.caveats.append("hevc")

    repaired = False

    if not report.has_audio:
        if not shutil.which(ffmpeg_bin) and runner is None:
            return _reject(report, "audio repair unavailable: ffmpeg not found")
        if not _mux_silent_audio(p, ffmpeg_bin, run):
            return _reject(report, "audio repair failed")
        report.caveats.append("silent")
        repaired = True

    if report.height < min_h or report.width < min_w:
        if not shutil.which(ffmpeg_bin) and runner is None:
            return _reject(report, "upscale repair unavailable: ffmpeg not found")
        if not _upscale(p, int(min_h), ffmpeg_bin, run):
            return _reject(report, "upscale repair failed")
        report.caveats.append("upscaled")
        repaired = True

    if repaired:
        data, err = _probe(p, ffprobe_bin, run)
        if err:
            return _reject(report, f"repair verification failed: {err}")
        assert data is not None
        _fill(report, data)
        if report.height < min_h or report.width < min_w:
            return _reject(
                report, "resolution still below the platform floor after repair"
            )
        if not report.has_audio:
            return _reject(report, "audio track still missing after repair")

    if report.fps and report.fps < 23:
        report.caveats.append("low_fps")

    report.validation = VALIDATION_REPAIRED if repaired else VALIDATION_VALID
    report.reject_reason = None
    logger.info(
        "Clip %s: %s (container=%s codec=%s %dx%d %.1fs%s)",
        report.path,
        report.validation,
        report.container,
        report.video_codec,
        report.width,
        report.height,
        report.duration_seconds,
        f" caveats={report.caveats}" if report.caveats else "",
    )
    return report
