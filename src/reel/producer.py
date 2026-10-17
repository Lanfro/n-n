"""Assisted browser producer for the external image-to-video service.

Drives an already-authenticated, persistent Playwright context on a free
third-party service (PixVerse first) to animate each brief's real source photo
into a vertical clip, then downloads it under the brief's ``expected_output``.

Safety / behaviour (contracts/video-service-browser.md):

* The browser targets the **external video service only** — never Instagram.
* A present, non-empty ``expected_output`` is treated as produced (idempotent
  skip; a hand-placed clip is accepted as equivalent, FR-007/FR-009).
* A login challenge, anti-bot check, exhausted allowance or unexpected UI
  raises :class:`BrowserPaused`; the caller alerts the operator and pauses
  cleanly, preserving already-produced clips (FR-008).
* All service-specific selectors live in :mod:`src.reel.selectors`.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import (
    SESSION_AUTHENTICATED,
    SESSION_CHALLENGED,
    SESSION_LIMIT_REACHED,
    SESSION_LOGGED_OUT,
    ReelBrief,
)
from .selectors import ServiceSelectors, load_selectors

logger = logging.getLogger(__name__)

UNEXPECTED_UI = "unexpected_ui"

# JS: centre of the smallest visible element whose text exactly equals `arg[0]`,
# inside the [arg[1], arg[2]] vertical band (used to pick a popover option).
_CLICK_TEXT_JS = r"""
([text, ymin, ymax]) => {
  const vis = (el) => { const r = el.getBoundingClientRect();
    const st = window.getComputedStyle(el);
    return r.width > 0 && r.height > 0 && st.visibility !== 'hidden'
      && st.display !== 'none'; };
  const cands = [];
  for (const el of document.querySelectorAll('div, span, button, p, label')) {
    if (!vis(el)) continue;
    if ((el.innerText || '').trim() !== text) continue;
    const r = el.getBoundingClientRect();
    if (r.y < ymin || r.y > ymax) continue;
    cands.push([r.width * r.height, r.x + r.width / 2, r.y + r.height / 2]);
  }
  cands.sort((a, b) => a[0] - b[0]);
  return cands.length ? [cands[0][1], cands[0][2]] : null;
}
"""

# JS: centre of the resolution token ("540P") that opens the settings popover.
_OPEN_SETTINGS_JS = r"""
() => {
  const vis = (el) => { const r = el.getBoundingClientRect();
    const st = window.getComputedStyle(el);
    return r.width > 0 && r.height > 0 && st.visibility !== 'hidden'
      && st.display !== 'none'; };
  const re = /^\d{3,4}P$/;
  for (const el of document.querySelectorAll('div, span, button')) {
    if (!vis(el)) continue;
    if (!re.test((el.innerText || '').trim())) continue;
    if (el.getBoundingClientRect().y < window.innerHeight * 0.7) continue;
    const r = el.getBoundingClientRect();
    return [r.x + r.width / 2, r.y + r.height / 2];
  }
  return null;
}
"""


class ProducerError(RuntimeError):
    pass


class BrowserPaused(Exception):
    """Raised to pause the batch (challenge/limit/logout/unexpected UI)."""

    def __init__(self, state: str, reason: str):
        self.state = state
        self.reason = reason
        super().__init__(f"{state}: {reason}")


@dataclass
class ProduceResult:
    produced: int = 0
    skipped: int = 0
    missing_source: int = 0
    failed: int = 0
    paused: bool = False
    paused_state: str = ""
    produced_files: list[str] = field(default_factory=list)


def load_briefs(out_dir: str | Path) -> list[ReelBrief]:
    """Load every ``reel_brief_NN.json`` in a set folder, ordered by shot."""
    briefs = []
    for path in sorted(Path(out_dir).glob("reel_brief_*.json")):
        try:
            briefs.append(
                ReelBrief.from_dict(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            )
        except (ValueError, KeyError, OSError) as exc:
            logger.warning("Skipping unreadable brief %s: %s", path.name, exc)
    return sorted(briefs, key=lambda b: b.shot_index)


def build_alerter(config: dict) -> Callable[[str], None]:
    """Build an operator alert (sound + Telegram push) from pipeline config."""
    from ..approval.notifier import notify
    from ..approval.sound import alert

    telegram_cfg = config.get("telegram", {}) or {}
    approval_cfg = config.get("approval", {}) or {}
    bot_token = telegram_cfg.get("bot_token", "")
    allowed = telegram_cfg.get("allowed_chat_ids") or []
    chat = (
        telegram_cfg.get("notify_chat_id")
        or approval_cfg.get("notify_chat_id")
        or (allowed[0] if allowed else None)
    )
    sound_enabled = approval_cfg.get("sound", True)
    sound_file = approval_cfg.get("sound_file", "")
    notify_enabled = approval_cfg.get("notify_telegram", True)

    def _alert(message: str) -> None:
        alert(sound_enabled, sound_file)
        if chat not in ("", None):
            notify(bot_token, chat, message, enabled=notify_enabled)

    return _alert


class PlaywrightDriver:
    """Real browser driver: persistent Chromium context, headed by default."""

    def __init__(
        self,
        *,
        selectors: ServiceSelectors,
        profile_dir: str | Path,
        service_url: str,
        headless: bool = False,
        timeout_seconds: int = 600,
        settings: dict | None = None,
    ):
        self.selectors = selectors
        self.profile_dir = Path(profile_dir)
        self.service_url = service_url
        self.headless = headless
        self.timeout = timeout_seconds
        self.settings = dict(settings or {})
        self._pw: Any = None
        self._context: Any = None
        self.page: Any = None

    def start(self) -> None:
        from playwright.sync_api import sync_playwright

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self._context = self._pw.chromium.launch_persistent_context(
            str(self.profile_dir),
            headless=self.headless,
            accept_downloads=True,
        )
        pages = self._context.pages
        self.page = pages[0] if pages else self._context.new_page()

    def close(self) -> None:
        for closer in (self._context, self._pw):
            try:
                if closer is not None:
                    closer.close() if closer is self._context else closer.stop()
            except Exception as exc:  # noqa: BLE001 - teardown must not raise
                logger.debug("Driver teardown warning: %s", exc)

    # -- state detection -------------------------------------------------

    def _first(self, candidates: Iterable[str]) -> Any:
        for sel in candidates:
            try:
                el = self.page.query_selector(sel)
            except Exception:  # noqa: BLE001 - page churn during queries
                el = None
            if el is not None:
                return el
        return None

    def detect_state(self) -> str:
        if self._first(self.selectors.marker("challenge")) is not None:
            return SESSION_CHALLENGED
        if self._first(self.selectors.marker("limit")) is not None:
            return SESSION_LIMIT_REACHED
        if self._first(self.selectors.get("logged_in")) is not None:
            return SESSION_AUTHENTICATED
        if self._first(self.selectors.get("login_form")) is not None:
            return SESSION_LOGGED_OUT
        # Unknown UI: treat as logged out so the batch pauses safely.
        return SESSION_LOGGED_OUT

    def login_form_visible(self) -> bool:
        """True while the service's sign-in panel is on screen."""
        return self._first(self.selectors.get("login_form")) is not None

    def _guard(self) -> None:
        state = self.detect_state()
        if state in (SESSION_CHALLENGED, SESSION_LIMIT_REACHED, SESSION_LOGGED_OUT):
            raise BrowserPaused(state, f"session state detected: {state}")

    # -- per-clip flow ---------------------------------------------------

    def _settle(self, *, timeout_seconds: float = 20.0) -> None:
        """Let the SPA hydrate before judging the session.

        Right after ``domcontentloaded`` the app has usually not rendered its
        header yet, so an immediate ``detect_state()`` sees neither a session
        marker nor the sign-in panel and falls through to "logged out", which
        would pause a perfectly good session. Poll until *some* marker exists.
        """
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if (
                self._first(self.selectors.get("logged_in")) is not None
                or self._first(self.selectors.get("login_form")) is not None
            ):
                return
            time.sleep(0.5)
        logger.debug(
            "No session marker rendered within %.0fs; judging as-is",
            timeout_seconds,
        )

    def _first_visible(self, key: str) -> Any:
        """First *visible* candidate for a key, else ``None``.

        A hidden look-alike must never be chosen for an interaction (PixVerse
        keeps an off-screen prompt textarea, for example).
        """
        for sel in self.selectors.get(key):
            try:
                elements = self.page.query_selector_all(sel)
            except Exception:  # noqa: BLE001 - page churn during queries
                elements = []
            for el in elements:
                if self._is_visible(el):
                    return el
        return None

    @staticmethod
    def _is_visible(el: Any) -> bool:
        try:
            return bool(el.is_visible())
        except Exception:  # noqa: BLE001 - detached handle
            return False

    def _require(
        self,
        key: str,
        step: str,
        *,
        require_visible: bool = True,
        timeout_seconds: float = 15.0,
    ) -> Any:
        deadline = time.monotonic() + timeout_seconds
        while True:
            el = (
                self._first_visible(key)
                if require_visible
                else self._first(self.selectors.get(key))
            )
            if el is not None:
                return el
            if time.monotonic() >= deadline:
                break
            time.sleep(0.5)
        url = self.selectors.url("workspace", self.service_url)
        raise BrowserPaused(
            UNEXPECTED_UI, f"'{key}' not found at {step} on {url}"
        )

    # -- create-settings popover ----------------------------------------

    @staticmethod
    def _setting_options(settings: dict | None) -> list[str]:
        """Popover option labels to pick, in order (empty when unset)."""
        cfg = settings or {}
        res = str(cfg.get("resolution") or "").strip()
        aspect = str(cfg.get("aspect") or "").strip()
        dur = str(cfg.get("duration") or "").strip()
        if dur.isdigit():
            dur = f"{dur}s"
        return [value for value in (res, aspect, dur) if value]

    def _click_exact_text(
        self, text: str, *, ymin: float = 0, ymax: float = 100000
    ) -> bool:
        box = self.page.evaluate(_CLICK_TEXT_JS, [text, ymin, ymax])
        if not box:
            return False
        self.page.mouse.click(box[0], box[1])
        return True

    def _open_settings_popover(self) -> bool:
        box = self.page.evaluate(_OPEN_SETTINGS_JS)
        if not box:
            return False
        self.page.mouse.click(box[0], box[1])
        return True

    def _apply_settings(self) -> None:
        """Pick resolution/aspect/duration in the create-settings popover.

        Best effort: when the popover cannot be found the service defaults are
        kept (the validator still rejects any clip that misses the bounds).
        """
        options = self._setting_options(self.settings)
        if not options:
            return
        if not self._open_settings_popover():
            logger.debug("Settings popover not found; keeping service defaults")
            return
        time.sleep(1.0)
        for value in options:
            if not self._click_exact_text(value, ymin=120):
                logger.debug("Setting option %r not found in popover", value)
            time.sleep(0.5)
        try:
            self.page.keyboard.press("Escape")
        except Exception as exc:  # noqa: BLE001 - closing is best effort
            logger.debug("Could not close settings popover: %s", exc)

    def generate_clip(
        self, brief: ReelBrief, dest: str | Path, *, timeout_seconds: int | None = None
    ) -> None:
        timeout = timeout_seconds or self.timeout
        self.page.goto(
            self.selectors.url("workspace", self.service_url),
            wait_until="domcontentloaded",
        )
        self._settle()
        self._guard()

        try:
            upload = self._require("upload_input", "upload", require_visible=False)
            upload.set_input_files(brief.source_photo_path)

            prompt = self._require("prompt_field", "prompt")
            prompt.fill(brief.motion_prompt)

            self._apply_settings()

            baseline_videos = self._video_count()
            generate = self._require("generate_button", "generate")
            generate.click()

            self._wait_for_result(timeout, baseline_videos=baseline_videos)
            self._download_result(Path(dest))
        except (BrowserPaused, ProducerError):
            raise
        except Exception as exc:  # page/selector faults must pause, not crash
            raise BrowserPaused(
                UNEXPECTED_UI,
                f"browser interaction failed: {str(exc)[:300]}",
            ) from exc

    def _video_count(self) -> int:
        """Number of ``<video>`` elements; the create page has a standby preview."""
        try:
            return len(self.page.query_selector_all("video"))
        except Exception:  # noqa: BLE001 - page churn during polling
            return 0

    def _wait_for_result(self, timeout: int, *, baseline_videos: int = 0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._guard()
            if self._first_visible("download_button") is not None:
                return
            if self._video_count() > baseline_videos:
                return
            time.sleep(2)
        raise ProducerError(
            f"timed out after {timeout}s waiting for the generated video"
        )

    def _download_result(self, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        button = self._first_visible("download_button")
        if button is not None:
            with self.page.expect_download(timeout=self.timeout * 1000) as info:
                button.click()
            info.value.save_as(str(dest))
            return

        video = self._first_visible("result_video")
        src = None
        if video is not None:
            src = video.get_attribute("src")
            if not src:
                source = video.query_selector("source")
                src = source.get_attribute("src") if source else None
        if not src:
            raise ProducerError("no downloadable result URL found")
        resp = self._context.request.get(src)
        if not resp.ok:
            raise ProducerError(
                f"result download failed: {resp.status} for {src}"
            )
        dest.write_bytes(resp.body())


class ReelProducer:
    """Orchestrates assisted production over a set's briefs (skip/pause/resume)."""

    def __init__(
        self,
        *,
        selectors: ServiceSelectors,
        profile_dir: str | Path,
        service_url: str,
        headless: bool = False,
        timeout_seconds: int = 600,
        alerter: Callable[[str], None] | None = None,
        driver_factory: Callable[[], Any] | None = None,
        settings: dict | None = None,
    ):
        self.selectors = selectors
        self.profile_dir = profile_dir
        self.service_url = service_url
        self.headless = headless
        self.timeout = timeout_seconds
        self.alerter = alerter or (lambda _msg: None)
        self.driver_factory = driver_factory
        self.settings = dict(settings or {})

    def _make_driver(self) -> Any:
        if self.driver_factory is not None:
            return self.driver_factory()
        return PlaywrightDriver(
            selectors=self.selectors,
            profile_dir=self.profile_dir,
            service_url=self.service_url,
            headless=self.headless,
            timeout_seconds=self.timeout,
            settings=self.settings,
        )

    @staticmethod
    def _is_produced(path: Path) -> bool:
        try:
            return path.exists() and path.stat().st_size > 0
        except OSError:
            return False

    def login(
        self, *, timeout_seconds: int = 300, poll_seconds: float = 2.0
    ) -> str:
        """Open the persistent browser for the operator's one-time sign-in.

        Navigates to the service workspace and polls ``detect_state`` until the
        session becomes authenticated (or the operator closes the window or the
        timeout elapses). Credentials live in the persistent profile, so later
        ``--reel-produce`` runs reuse the session.

        Sign-in is confirmed either by a positive ``logged_in`` selector or,
        because service markup drifts, by observing the sign-in panel appear
        and then clear for several consecutive polls.

        Returns the final session state (``authenticated`` on success).
        """
        driver = self._make_driver()
        logger.info(
            "Sign in to %s in the browser window; waiting up to %ss",
            self.service_url,
            timeout_seconds,
        )
        driver.start()
        try:
            driver.page.goto(
                self.selectors.url("workspace", self.service_url),
                wait_until="domcontentloaded",
            )
            deadline = time.monotonic() + timeout_seconds
            seen_login = False
            cleared_polls = 0
            last_state = ""
            while True:
                page = getattr(driver, "page", None)
                if page is not None and hasattr(page, "is_closed") and page.is_closed():
                    logger.warning(
                        "Browser window closed before login completed; "
                        "session not saved"
                    )
                    return SESSION_LOGGED_OUT
                state = driver.detect_state()
                if state != last_state:
                    logger.info("Session state while logging in: %s", state)
                    last_state = state
                if state == SESSION_AUTHENTICATED:
                    logger.info(
                        "Login confirmed for %s; session persisted for "
                        "future --reel-produce runs",
                        self.service_url,
                    )
                    return state
                visible = (
                    driver.login_form_visible()
                    if hasattr(driver, "login_form_visible")
                    else False
                )
                if visible:
                    seen_login = True
                    cleared_polls = 0
                elif seen_login:
                    # The panel we watched go away cannot be the still-logged-out
                    # state, even if no logged_in selector matched.
                    cleared_polls += 1
                    if cleared_polls >= 3:
                        logger.info(
                            "Sign-in panel cleared; session persisted for "
                            "future --reel-produce runs",
                        )
                        return SESSION_AUTHENTICATED
                if time.monotonic() >= deadline:
                    logger.warning(
                        "Login not confirmed within %ss (last state: %s)",
                        timeout_seconds,
                        state,
                    )
                    return state
                time.sleep(poll_seconds)
        finally:
            driver.close()

    def produce(
        self, briefs: list[ReelBrief], out_dir: str | Path
    ) -> ProduceResult:
        out = Path(out_dir)
        result = ProduceResult()
        driver = self._make_driver()
        driver.start()
        try:
            for brief in briefs:
                dest = out / brief.expected_output
                if self._is_produced(dest):
                    result.skipped += 1
                    logger.info("Clip present; skipping %s", brief.expected_output)
                    continue
                source = Path(brief.source_photo_path)
                if not source.exists():
                    result.missing_source += 1
                    logger.warning(
                        "Source photo missing for %s: %s", brief.brief_id, source
                    )
                    continue
                logger.info(
                    "Producing %s (%s) from %s",
                    brief.expected_output,
                    brief.motion_prompt[:60],
                    source.name,
                )
                try:
                    driver.generate_clip(brief, dest, timeout_seconds=self.timeout)
                except BrowserPaused as exc:
                    result.paused = True
                    result.paused_state = exc.state
                    self._pause(brief, exc)
                    break
                except ProducerError as exc:
                    result.failed += 1
                    logger.error("Generation failed for %s: %s", brief.brief_id, exc)
                    continue
                if self._is_produced(dest):
                    result.produced += 1
                    result.produced_files.append(str(dest))
                    logger.info("Produced %s", brief.expected_output)
                else:
                    result.failed += 1
                    logger.error(
                        "No file produced for %s", brief.expected_output
                    )
        finally:
            driver.close()
        return result

    def _pause(self, brief: ReelBrief, exc: BrowserPaused) -> None:
        if exc.state == SESSION_LIMIT_REACHED:
            action = (
                "The service refused the generation (e.g. 'Insufficient "
                "Credits'). Earn credits, subscribe, or drop the clip in by "
                "hand, then re-run."
            )
        elif exc.state == SESSION_CHALLENGED:
            action = "Solve the anti-bot check in the browser, then re-run."
        else:
            action = (
                "Complete this clip by hand (or re-authenticate) and re-run."
            )
        message = (
            f"Reel production paused at '{exc.state}' for brief "
            f"{brief.brief_id}: {exc.reason}. {action}"
        )
        logger.warning(message)
        try:
            self.alerter(message)
        except Exception as exc_alert:  # noqa: BLE001 - alert must not crash
            logger.warning("Operator alert failed: %s", exc_alert)


def produce_set(
    config: dict,
    set_key: str,
    *,
    alerter: Callable[[str], None] | None = None,
    driver_factory: Callable[[], Any] | None = None,
) -> ProduceResult:
    """Load a set's briefs and produce any missing clips (assisted)."""
    creative = config.get("creative", {})
    reel_cfg = config.get("reel", {})
    out_dir = Path(creative.get("out_root", "data/generated")) / set_key
    briefs = load_briefs(out_dir)
    if not briefs:
        logger.error(
            "No briefs found in %s; run --reel-plan --set %s first",
            out_dir,
            set_key,
        )
        return ProduceResult()

    selectors = load_selectors(
        reel_cfg.get("selectors_path", "config/reel/selectors.json")
    )
    settings = {
        "resolution": reel_cfg.get("resolution", ""),
        "aspect": reel_cfg.get("aspect", ""),
        "duration": reel_cfg.get("duration_seconds", ""),
    }
    producer = ReelProducer(
        selectors=selectors,
        profile_dir=reel_cfg.get("browser_profile_dir", "data/.browser/reel"),
        service_url=reel_cfg.get("service_url", ""),
        headless=bool(reel_cfg.get("headless", False)),
        timeout_seconds=int(reel_cfg.get("generation_timeout_seconds", 600)),
        alerter=alerter or build_alerter(config),
        driver_factory=driver_factory,
        settings=settings,
    )
    logger.info(
        "Producing %d brief(s) for set '%s' via %s",
        len(briefs),
        set_key,
        selectors.service,
    )
    result = producer.produce(briefs, out_dir)
    logger.info(
        "Production: %d produced, %d skipped, %d missing-source, %d failed%s",
        result.produced,
        result.skipped,
        result.missing_source,
        result.failed,
        f", PAUSED ({result.paused_state})" if result.paused else "",
    )
    return result


def login_session(
    config: dict,
    *,
    timeout_seconds: int = 300,
    poll_seconds: float = 2.0,
    driver_factory: Callable[[], Any] | None = None,
) -> str:
    """Open the persistent browser for the operator's one-time sign-in.

    Loads the reel config, then returns the session state once the sign-in is
    confirmed (``authenticated``), the window is closed (``logged_out``) or the
    wait times out (last observed state).
    """
    reel_cfg = config.get("reel", {})
    selectors = load_selectors(
        reel_cfg.get("selectors_path", "config/reel/selectors.json")
    )
    producer = ReelProducer(
        selectors=selectors,
        profile_dir=reel_cfg.get("browser_profile_dir", "data/.browser/reel"),
        service_url=reel_cfg.get("service_url", ""),
        headless=bool(reel_cfg.get("headless", False)),
        timeout_seconds=int(reel_cfg.get("generation_timeout_seconds", 600)),
        driver_factory=driver_factory,
    )
    return producer.login(
        timeout_seconds=timeout_seconds, poll_seconds=poll_seconds
    )
