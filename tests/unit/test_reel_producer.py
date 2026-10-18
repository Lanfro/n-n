"""Unit tests for the assisted browser producer (mocked driver/selectors)."""

import json

import pytest

from src.reel.models import (
    SESSION_AUTHENTICATED,
    SESSION_CHALLENGED,
    SESSION_LIMIT_REACHED,
    SESSION_LOGGED_OUT,
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


class FakeLoginDriver:
    """Replays scripted (session state, sign-in panel visible) observations."""

    def __init__(self, observations, *, window_closed=False):
        self.observations = [tuple(o) for o in observations]
        self.page = _FakePage(closed=window_closed)
        self.started = False
        self.closed = False
        self._current = (SESSION_LOGGED_OUT, True)

    def start(self):
        self.started = True

    def close(self):
        self.closed = True

    def detect_state(self):
        if len(self.observations) > 1:
            self._current = self.observations.pop(0)
        else:
            self._current = self.observations[0]
        return self._current[0]

    def login_form_visible(self):
        return self._current[1]


class _FakePage:
    def __init__(self, *, closed=False):
        self.navigations: list[str] = []
        self._closed = closed

    def goto(self, url, wait_until=None):
        self.navigations.append(url)

    def is_closed(self):
        return self._closed


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


def test_login_polls_until_authenticated():
    driver = FakeLoginDriver(
        [
            (SESSION_LOGGED_OUT, True),
            (SESSION_LOGGED_OUT, True),
            (SESSION_AUTHENTICATED, False),
        ]
    )
    state = _producer(driver, []).login(timeout_seconds=5, poll_seconds=0)

    assert state == SESSION_AUTHENTICATED
    assert driver.started and driver.closed
    assert driver.page.navigations == ["https://example.invalid"]


def test_login_accepts_a_cleared_sign_in_panel():
    # Service markup drifts; the panel vanishing is enough proof of sign-in.
    driver = FakeLoginDriver(
        [
            (SESSION_LOGGED_OUT, True),
            (SESSION_LOGGED_OUT, False),
            (SESSION_LOGGED_OUT, False),
            (SESSION_LOGGED_OUT, False),
        ]
    )
    state = _producer(driver, []).login(timeout_seconds=5, poll_seconds=0)

    assert state == SESSION_AUTHENTICATED
    assert driver.closed


def test_login_does_not_assume_signed_in_before_the_panel_renders():
    driver = FakeLoginDriver([(SESSION_LOGGED_OUT, False)])
    state = _producer(driver, []).login(timeout_seconds=0, poll_seconds=0)

    assert state == SESSION_LOGGED_OUT
    assert driver.closed


def test_login_times_out_while_logged_out():
    driver = FakeLoginDriver([(SESSION_LOGGED_OUT, True)])
    state = _producer(driver, []).login(timeout_seconds=0, poll_seconds=0)

    assert state == SESSION_LOGGED_OUT
    assert driver.closed


def test_login_ends_when_window_is_closed():
    driver = FakeLoginDriver([(SESSION_LOGGED_OUT, True)], window_closed=True)
    state = _producer(driver, []).login(timeout_seconds=5, poll_seconds=0)

    assert state == SESSION_LOGGED_OUT
    assert driver.closed


class _FakeEl:
    def __init__(self, visible=True):
        self._visible = visible

    def is_visible(self):
        return self._visible


class FakePageDOM:
    """Answers ``query_selector(_all)``; a marker appears after N calls."""

    def __init__(self, *, present_after=0, present=("#marker",), hidden=()):
        self.calls = 0
        self.present_after = present_after
        self.present = set(present)
        self.hidden = set(hidden)

    def _maybe(self, sel):
        self.calls += 1
        if self.calls > self.present_after and sel in self.present:
            return _FakeEl(visible=sel not in self.hidden)
        return None

    def query_selector(self, sel):
        return self._maybe(sel)

    def query_selector_all(self, sel):
        el = self._maybe(sel)
        return [el] if el is not None else []


def _driver(page, *, selectors=None):
    from src.reel.producer import PlaywrightDriver

    drv = PlaywrightDriver(
        selectors=selectors or ServiceSelectors(service="t"),
        profile_dir="data/.browser/reel",
        service_url="https://example.invalid",
    )
    drv.page = page
    return drv


def test_settle_returns_immediately_once_a_marker_renders():
    page = FakePageDOM(present_after=0, present=("#any-marker",))
    drv = _driver(
        page,
        selectors=ServiceSelectors(
            service="t", selectors={"logged_in": ["#any-marker"], "login_form": []}
        ),
    )
    drv._settle(timeout_seconds=5)
    assert page.calls <= 2


def test_settle_polls_until_the_marker_appears():
    # Judging before hydration would read an empty DOM as "logged out".
    page = FakePageDOM(present_after=3, present=("#marker",))
    drv = _driver(
        page,
        selectors=ServiceSelectors(
            service="t", selectors={"logged_in": ["#marker"], "login_form": []}
        ),
    )
    drv._settle(timeout_seconds=10)
    assert page.calls > 3


def test_require_raises_a_clean_pause_when_the_element_never_appears():
    from src.reel.producer import UNEXPECTED_UI

    page = FakePageDOM(present_after=10**6, present=set())
    drv = _driver(
        page,
        selectors=ServiceSelectors(
            service="t", selectors={"upload_input": ["#gone"]}
        ),
    )
    with pytest.raises(BrowserPaused) as exc:
        drv._require("upload_input", "upload", timeout_seconds=0)
    assert exc.value.state == UNEXPECTED_UI


def test_require_waits_for_a_late_element():
    page = FakePageDOM(present_after=2, present=("#upload",))
    drv = _driver(
        page,
        selectors=ServiceSelectors(
            service="t", selectors={"upload_input": ["#upload"]}
        ),
    )
    el = drv._require("upload_input", "upload", timeout_seconds=10)
    assert el is not None


def test_require_skips_a_hidden_look_alike():
    # PixVerse keeps an off-screen prompt textarea that must never be used.
    page = FakePageDOM(present={"#hidden", "#real"}, hidden={"#hidden"})
    drv = _driver(
        page,
        selectors=ServiceSelectors(
            service="t", selectors={"prompt_field": ["#hidden", "#real"]}
        ),
    )
    el = drv._require("prompt_field", "prompt", timeout_seconds=1)
    assert el is not None and el.is_visible()


def test_require_can_explicitly_accept_hidden_inputs():
    # File inputs are legitimately hidden.
    page = FakePageDOM(present={"input[type=file]"}, hidden={"input[type=file]"})
    drv = _driver(
        page,
        selectors=ServiceSelectors(
            service="t", selectors={"upload_input": ["input[type=file]"]}
        ),
    )
    el = drv._require("upload_input", "upload", require_visible=False, timeout_seconds=1)
    assert el is not None


class _SettingsPage:
    """Records clicks/keys and answers the settings-popover JS probes."""

    def __init__(self, *, found=("540P", "9:16", "3s")):
        self.found = set(found)
        self.evaluated: list[object] = []
        self.clicks: list[tuple[float, float]] = []
        self.keys: list[str] = []
        self.mouse = self
        self.keyboard = self

    def evaluate(self, js, arg=None):
        self.evaluated.append(arg)
        if arg is None:  # _OPEN_SETTINGS_JS
            return [1.0, 2.0]
        return [10.0, 20.0] if arg[0] in self.found else None

    def click(self, x, y):
        self.clicks.append((x, y))

    def press(self, key):
        self.keys.append(key)


def test_setting_options_formats_duration_and_drops_blanks():
    from src.reel.producer import PlaywrightDriver

    assert PlaywrightDriver._setting_options(
        {"resolution": "540P", "aspect": "9:16", "duration": 3}
    ) == ["540P", "9:16", "3s"]
    assert PlaywrightDriver._setting_options({"resolution": "", "duration": ""}) == []
    assert PlaywrightDriver._setting_options(None) == []


def test_apply_settings_is_a_noop_without_configuration():
    page = _SettingsPage()
    drv = _driver(page)
    drv.settings = {}
    drv._apply_settings()
    assert page.evaluated == [] and page.clicks == [] and page.keys == []


def test_apply_settings_opens_popover_and_picks_each_option():
    page = _SettingsPage()
    drv = _driver(page)
    drv.settings = {"resolution": "540P", "aspect": "9:16", "duration": 3}
    drv._apply_settings()
    assert page.evaluated[0] is None  # popover open probe
    assert len(page.clicks) == 4  # open + one per option
    assert page.keys == ["Escape"]


def test_apply_settings_keeps_defaults_when_popover_is_absent():
    page = _SettingsPage()
    page.evaluate = lambda js, arg=None: None
    drv = _driver(page)
    drv.settings = {"resolution": "540P"}
    drv._apply_settings()
    assert page.clicks == []


def _paywall_driver():
    page = FakePageDOM(present={"text=insufficient credits"})
    return _driver(
        page,
        selectors=ServiceSelectors(
            service="t",
            selectors={"logged_in": [], "login_form": []},
            markers={"limit": ["text=insufficient credits"]},
        ),
    )


def test_detect_state_reports_limit_reached_on_the_paywall_marker():
    assert _paywall_driver().detect_state() == SESSION_LIMIT_REACHED


def test_guard_pauses_the_batch_when_credits_are_insufficient():
    with pytest.raises(BrowserPaused) as exc:
        _paywall_driver()._guard()
    assert exc.value.state == SESSION_LIMIT_REACHED


class _FakeInput:
    def __init__(self, page, index):
        self.page = page
        self.index = index

    def set_input_files(self, path):
        self.page.uploads.append((self.index, path))
        if self.index in self.page.attach_indexes:
            self.page.previews += 1


class _UploadPage:
    """Two file inputs; only ``attach_indexes`` actually attach an image."""

    def __init__(self, *, count=2, attach_indexes=(1,)):
        self.inputs = [_FakeInput(self, i) for i in range(count)]
        self.attach_indexes = set(attach_indexes)
        self.uploads: list[tuple[int, str]] = []
        self.previews = 0

    def query_selector_all(self, _sel):
        return list(self.inputs)

    def evaluate(self, _js):
        return self.previews


def test_upload_source_tries_each_input_until_a_preview_appears():
    page = _UploadPage(count=2, attach_indexes=(1,))
    drv = _driver(page)
    drv._upload_source("photo.jpg", timeout_seconds=0.1)
    assert [i for i, _ in page.uploads] == [0, 1]


def test_upload_source_pauses_when_no_input_attaches():
    from src.reel.producer import UNEXPECTED_UI

    page = _UploadPage(count=2, attach_indexes=())
    drv = _driver(page)
    with pytest.raises(BrowserPaused) as exc:
        drv._upload_source("photo.jpg", timeout_seconds=0)
    assert exc.value.state == UNEXPECTED_UI


class _ModePage:
    """Answers the mode-switcher JS probe and records clicks."""

    def __init__(self, *, box=(1.0, 2.0)):
        self.box = box
        self.mouse = self
        self.calls: list[object] = []
        self.clicks: list[tuple[float, float]] = []

    def evaluate(self, _js, arg=None):
        self.calls.append(arg)
        return list(self.box) if self.box else None

    def click(self, x, y):
        self.clicks.append((x, y))


def test_select_mode_clicks_the_video_switcher_item():
    page = _ModePage(box=(244.0, 499.0))
    drv = _driver(page)
    assert drv._select_mode("Video") is True
    assert page.calls == ["Video"]
    assert page.clicks == [(244.0, 499.0)]


def test_select_mode_is_best_effort_when_the_switcher_is_absent():
    page = _ModePage(box=None)
    drv = _driver(page)
    assert drv._select_mode("Video") is False
    assert page.clicks == []
