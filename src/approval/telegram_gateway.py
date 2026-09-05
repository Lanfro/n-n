"""Human-in-the-Loop approval gateway.

Primary channel: a Telegram bot that sends the draft (image + caption) and
waits for a decision via buttons or slash commands.

Graceful degradation: when no Telegram token/network is configured, the
gateway falls back to a CLI prompt so the pipeline remains usable offline
and testable without credentials.
"""

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from ..database.db_manager import DBManager
from .notifier import notify
from .sound import alert

logger = logging.getLogger(__name__)

APPROVE_ACTION = "approve"
RETRY_ACTION = "retry"
DISCARD_ACTION = "discard"

# In-memory cache: chat_id -> {post_id, sent_photo}
_pending: dict[int, dict] = {}


class ApprovalDecision:
    pass


async def _approve_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    if chat_id in _pending:
        _pending[chat_id]["action"] = "approve"
    await query.edit_message_text("Approved. Proceeding to scheduling.")


async def _handle_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, action: str
) -> None:
    chat_id = update.effective_chat.id
    _pending[chat_id] = {"action": action}
    reply = {
        "approve": "Approved. Proceeding to scheduling.",
        "retry": "Retrying generation.",
        "discard": "Post discarded.",
    }
    if update.message:
        await update.message.reply_text(reply[action])


async def _cmd_approve(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await _handle_command(update, context, "approve")


async def _cmd_retry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_command(update, context, "retry")


async def _cmd_discard(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await _handle_command(update, context, "discard")


class TelegramGateway:
    """Sends drafts to Telegram and waits for a human decision.

    If no bot token is configured, request_approval falls back to a CLI
    prompt (approve/retry/discard).
    """

    def __init__(
        self,
        db: DBManager,
        bot_token: str = "",
        allowed_chat_ids: list[int] | None = None,
        decision_timeout_seconds: int = 1800,
        sound_enabled: bool = True,
        sound_file: str = "",
        notify_telegram: bool = True,
        notify_chat_id: str | int | None = None,
    ):
        self.db = db
        self.bot_token = bot_token
        self.allowed_chat_ids = set(allowed_chat_ids or [])
        self.timeout = decision_timeout_seconds
        self.sound_enabled = sound_enabled
        self.sound_file = sound_file
        self.notify_telegram = notify_telegram
        self.notify_chat_id = notify_chat_id
        self._fallback_reason: str | None = None

    @property
    def available(self) -> bool:
        return bool(self.bot_token)

    def _alert_operator(self, post_id: int) -> None:
        """Sound + Telegram push before blocking on a human decision."""
        alert(self.sound_enabled, self.sound_file)
        chat = self.notify_chat_id
        if chat in ("", None) and self.allowed_chat_ids:
            chat = next(iter(self.allowed_chat_ids))
        if chat not in ("", None):
            notify(
                self.bot_token,
                chat,
                f"Action needed: post #{post_id} is awaiting your approval.",
                enabled=self.notify_telegram,
            )

    def auto_approve(self, post_id: int) -> dict:
        """Approve a post without interactive input (for dry-run/testing)."""
        if not self.db.transition(post_id, "APPROVED"):
            raise RuntimeError(f"Post {post_id} cannot transition to APPROVED")
        return {"action": "approve", "source": "auto"}

    async def _request_via_telegram(
        self, post_id: int, media_path: str, caption: str
    ) -> dict:
        application = (
            Application.builder()
            .token(self.bot_token)
            .arbitrary_callback_data(True)
            .read_timeout(60)
            .write_timeout(60)
            .build()
        )
        application.add_handler(CommandHandler("approve", _cmd_approve))
        application.add_handler(CommandHandler("retry", _cmd_retry))
        application.add_handler(CommandHandler("discard", _cmd_discard))
        application.add_handler(CallbackQueryHandler(_approve_callback))

        async with application:
            await application.initialize()
            await application.start()

            bot: Bot = application.bot
            message = await bot.send_photo(
                chat_id=next(iter(self.allowed_chat_ids)),
                photo=Path(media_path).as_posix(),
                caption=caption,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "Approve & Schedule", callback_data="approve"
                            ),
                            InlineKeyboardButton(
                                "Regenerate", callback_data="retry"
                            ),
                            InlineKeyboardButton(
                                "Discard", callback_data="discard"
                            ),
                        ]
                    ]
                ),
            )
            # Poll for a decision
            deadline = datetime.now(UTC).timestamp() + self.timeout
            decision = None
            while datetime.now(UTC).timestamp() < deadline:
                pending = _pending.get(message.chat_id)
                if pending and pending.get("action"):
                    decision = pending["action"]
                    break
                await asyncio.sleep(2)

            await application.stop()
            await application.shutdown()

            if not decision:
                return {"action": "timeout", "source": "telegram"}
            return {"action": decision, "source": "telegram"}

    def request_approval(self, post_id: int, media_path: str, caption: str) -> dict:
        """Ask for human approval for the given post.

        Returns {'action': 'approve'|'retry'|'discard'|'timeout', ...}.
        """
        self._alert_operator(post_id)
        if not self.available:
            self._fallback_reason = (
                "Telegram bot_token not configured; using CLI approval."
            )
            logger.warning(self._fallback_reason)
            return self._request_via_cli(post_id, media_path, caption)

        if not self.allowed_chat_ids:
            self._fallback_reason = (
                "Telegram allowed_chat_ids not configured; using CLI approval."
            )
            logger.warning(self._fallback_reason)
            return self._request_via_cli(post_id, media_path, caption)

        try:
            decision = asyncio.run(
                self._request_via_telegram(post_id, media_path, caption)
            )
        except TelegramError as exc:
            self._fallback_reason = f"Telegram failed ({exc}); using CLI approval."
            logger.warning(self._fallback_reason)
            return self._request_via_cli(post_id, media_path, caption)
        except Exception as exc:  # noqa: BLE001 - asyncio/network -> CLI fallback
            self._fallback_reason = f"Telegram error ({exc}); using CLI approval."
            logger.warning(self._fallback_reason)
            return self._request_via_cli(post_id, media_path, caption)

        return decision

    def _request_via_cli(self, post_id: int, media_path: str, caption: str) -> dict:
        print("\n" + "=" * 60)
        print(f"POST #{post_id} awaiting approval")
        print(f"Media : {media_path}")
        print(f"Caption:\n{caption}")
        print("=" * 60)
        choice = input("Choose [a]pprove / [r]etry / [d]iscard: ").strip().lower()
        if choice.startswith("r"):
            return {"action": "retry", "source": "cli"}
        if choice.startswith("d"):
            return {"action": "discard", "source": "cli"}
        return {"action": "approve", "source": "cli"}