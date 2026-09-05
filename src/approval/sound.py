"""Operator alerts: play a sound when a human decision is required.

Windows-first: uses the stdlib `winsound` module with a system beep (default)
or an optional custom `.wav`. Any failure is swallowed so the approval flow is
never blocked by an alert.
"""

import logging
import sys

logger = logging.getLogger(__name__)

_EXTENSION = ".wav"


def alert(sound_enabled: bool = True, sound_file: str = "") -> bool:
    """Play the alert sound. Returns True if a sound was triggered."""
    if not sound_enabled:
        return False
    if sys.platform != "win32":
        logger.debug("Sound alerts are Windows-only; skipped")
        return False
    try:
        import winsound
    except ImportError:
        logger.debug("winsound is unavailable; skipped")
        return False

    try:
        if sound_file:
            winsound.PlaySound(
                sound_file,
                winsound.SND_FILENAME
                | winsound.SND_ASYNC
                | winsound.SND_NODEFAULT,
            )
        else:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        return True
    except RuntimeError as exc:
        logger.debug("Sound alert failed: %s", exc)
        return False