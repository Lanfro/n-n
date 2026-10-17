"""Unit tests for the assisted browser producer (mocked driver/selectors)."""

import json

from src.reel.models import (
    SESSION_CHALLENGED,
    SESSION_LIMIT_REACHED,
    ReelBrief,
)
from src.reel.producer import (
    BrowserPaused,
    ProducerError,
    ReelProducer,
    load_briefs,
)
from src.reel.selectors import ServiceSelectors


def _brief(tmp_path, index, *, source_exists=True, cat="nero") -> ReelBrief:
    src = tmp_path / f"src_{index}.jpg"
    if source_exists:
        src.write_bytes(b"image-bytes")
    return ReelBrief(
        brief_id=f"s:{index}",
        set="s",
        shot_index=index,
        account_key="cat_1",
        cat=cat,
        motion_prompt="blinks slowly",
        source_photo_path=str(src),
        source_vault_media_id=index + 1,
        expected_output=f"reel_{index:02d}.mp4",
        caption="caption",
        reel_text="hook",
        hashtags=["cat"],
        created_at="now",
    )


class FakeDriver:
    def __init__(self, *, behavior="ok", pause_state=""):
        self.behavior = behavior
        self.pause_state = pause_state
        self.calls: list[str] = []
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def close(self):
        self.closed = True

    def generate_clip(self, brief, dest, *, timeout_seconds=None):
        self.calls.append(brief.brief_id)
        if self.behavior == "pause":
            raise BrowserPaused(self.pause_state, "simulated condition")
        if self.behavior == "error":
            raise ProducerError("simulated failure")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"clip-bytes")


def _producer(driver, alerts):
    return ReelProducer(
        selectors=ServiceSelectors(service="fake"),
        profile_dir="data/.browser/reel",
        service_url="https://example.invalid",
        alerter=alerts.append,
        driver_factory=lambda: driver,
    )


def test_produces_missing_clips(tmp_path):
    briefs = [_brief(tmp_path, 0), _brief(tmp_path, 1)]
    out = tmp_path / "out"
    driver = FakeDriver()
    result = _producer(driver, []).produce(briefs, out)

    assert result.produced == 2
    assert result.skipped == 0
    assert (out / "reel_00.mp4").read_bytes() == b"clip-bytes"
    assert (out / "reel_01.mp4").exists()
    assert driver.started and driver.closed
    assert driver.calls == ["s:0", "s:1"]


def test_skips_present_clips_idempotently(tmp_path):
    briefs = [_brief(tmp_path, 0)]
    out = tmp_path / "out"
    out.mkdir()
    (out / "reel_00.mp4").write_bytes(b"already here")
    driver = FakeDriver()
    result = _producer(driver, []).produce(briefs, out)

    assert result.skipped == 1
    assert result.produced == 0
    assert driver.calls == []
    assert (out / "reel_00.mp4").read_bytes() == b"already here"


def test_manual_drop_in_is_treated_as_produced(tmp_path):
    # A hand-placed, correctly named clip is equivalent to a generated one.
    briefs = [_brief(tmp_path, 0)]
    out = tmp_path / "out"
    out.mkdir()
    (out / "reel_00.mp4").write_bytes(b"hand-made clip")
    result = _producer(FakeDriver(), []).produce(briefs, out)
    assert result.skipped == 1
    assert result.produced == 0


def test_challenge_pauses_and_alerts(tmp_path):
    briefs = [_brief(tmp_path, 0), _brief(tmp_path, 1)]
    out = tmp_path / "out"
    alerts: list[str] = []
    driver = FakeDriver(behavior="pause", pause_state=SESSION_CHALLENGED)
    result = _producer(driver, alerts).produce(briefs, out)

    assert result.paused is True
    assert result.paused_state == SESSION_CHALLENGED
    assert driver.calls == ["s:0"]  # stopped after the first clip
    assert result.produced == 0
    assert alerts and "paused" in alerts[0].lower()
    assert driver.closed


def test_limit_pauses(tmp_path):
    briefs = [_brief(tmp_path, 0), _brief(tmp_path, 1)]
    out = tmp_path / "out"
    alerts: list[str] = []
    driver = FakeDriver(behavior="pause", pause_state=SESSION_LIMIT_REACHED)
    result = _producer(driver, alerts).produce(briefs, out)
    assert result.paused is True
    assert result.paused_state == SESSION_LIMIT_REACHED
    assert len(driver.calls) == 1


def test_missing_source_photo_skipped(tmp_path):
    briefs = [_brief(tmp_path, 0, source_exists=False)]
    out = tmp_path / "out"
    driver = FakeDriver()
    result = _producer(driver, []).produce(briefs, out)
    assert result.missing_source == 1
    assert result.produced == 0
    assert driver.calls == []


def test_generation_error_counts_failed(tmp_path):
    briefs = [_brief(tmp_path, 0)]
    out = tmp_path / "out"
    result = _producer(FakeDriver(behavior="error"), []).produce(briefs, out)
    assert result.failed == 1
    assert result.produced == 0


def test_load_briefs_reads_files_in_shot_order(tmp_path):
    for index in (1, 0):
        brief = _brief(tmp_path, index)
        (tmp_path / f"reel_brief_{index:02d}.json").write_text(
            json.dumps(brief.to_dict()), encoding="utf-8"
        )
    loaded = load_briefs(tmp_path)
    assert [b.shot_index for b in loaded] == [0, 1]
    assert loaded[0].brief_id == "s:0"
