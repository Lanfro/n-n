"""Telegram push notifications when a human decision is required.

Uses the Bot API `sendMessage` endpoint over plain `requests` so notifications
are fire-and-forget (no long-polling, no shared consumer conflict with the
vault bot). Silent no-op when not configured or disabled.
"""

import logging

import requests

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/sendMessage"


def notify(
    bot_token: str,
    chat_id: str | int,
    text: str,
    *,
    enabled: bool = True,
) -> bool:
    """Send a one-way Telegram message. Returns True when delivered."""
    if not enabled or not bot_token or chat_id in ("", None):
        return False
    url = _API.format(token=bot_token)
    payload = {"chat_id": chat_id, "text": text}
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if not resp.ok:
            logger.debug(
                "Telegram notify rejected: %s %s", resp.status_code, resp.text[:200]
            )
            return False
        return True
    except requests.exceptions.RequestException as exc:
        logger.debug("Telegram notify failed: %s", exc)
        return False