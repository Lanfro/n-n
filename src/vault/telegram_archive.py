"""Telegram vault channel: archive uploads + idempotent channel sync.

Archive uploads use `sendDocument` (never `sendPhoto`) so Telegram stores the
original file byte-for-byte; each archived Document carries a `sha256:<hash>`
caption. Channel sync polls `channel_post` updates via a one-shot `getUpdates`
call (dedicated bot token), persists its offset cursor, downloads new pictures,
and ingests them as `source="telegram"`.

Ref: specs/001-instagram-growth-pipeline/contracts/telegram-protocol.md (Part B)
"""

import logging
import tempfile
from pathlib import Path

import requests

from ..database.db_manager import DBManager
from .media_vault import MediaVault

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}"


class VaultArchiveError(RuntimeError):
    pass


class TelegramVault:
    def __init__(self, db: DBManager, bot_token: str, chat_id: str | int):
        if not bot_token or not chat_id:
            raise VaultArchiveError(
                "Telegram vault requires vault.telegram.bot_token and chat_id"
            )
        self.db = db
        self.bot_token = bot_token
        self.chat_id = str(chat_id)

    def _api(self, method: str) -> str:
        return f"{_API.format(token=self.bot_token)}/{method}"

    def _post_document(
        self, path: Path, filename: str, caption: str
    ) -> dict:
        with open(path, "rb") as fh:
            files = {
                "document": (
                    filename,
                    fh,
                    "application/octet-stream",
                )
            }
            data = {"chat_id": self.chat_id, "caption": caption}
            try:
                resp = requests.post(
                    self._api("sendDocument"),
                    data=data,
                    files=files,
                    timeout=120,
                )
            except requests.exceptions.RequestException as exc:
                raise VaultArchiveError(
                    f"sendDocument failed: {exc}"
                ) from exc
        if not resp.ok:
            raise VaultArchiveError(
                f"sendDocument rejected: {resp.status_code} {resp.text[:300]}"
            )
        result = resp.json().get("result", {})
        document = result.get("document") or {}
        if not document.get("file_id"):
            raise VaultArchiveError(
                f"sendDocument response missing file_id: {resp.text[:300]}"
            )
        return {
            "file_id": document["file_id"],
            "message_id": result.get("message_id"),
        }

    def archive(self, vault_media_id: int) -> dict:
        """Upload a vault asset as a Document; idempotent per asset."""
        row = self.db.get_vault_media(vault_media_id)
        if row is None:
            raise VaultArchiveError(f"Vault media {vault_media_id} not found")
        if row.get("telegram_file_id"):
            logger.info(
                "Vault asset #%d already archived (file %s)",
                vault_media_id,
                row["telegram_file_id"],
            )
            return {
                "file_id": row["telegram_file_id"],
                "message_id": row.get("telegram_message_id"),
            }

        path = Path(row["stored_path"])
        caption = f"sha256:{row['sha256']}"
        uploaded = self._post_document(path, row["original_filename"], caption)
        self.db.set_vault_archive(
            vault_media_id,
            telegram_file_id=uploaded["file_id"],
            telegram_message_id=uploaded["message_id"] or 0,
        )
        logger.info(
            "Vault asset #%d archived to channel (message %s)",
            vault_media_id,
            uploaded["message_id"],
        )
        return uploaded

    def sync_from_channel(self, vault: MediaVault) -> dict:
        """Pull new channel pictures into the vault. Returns counts."""
        offset = self.db.get_channel_offset()
        params = {
            "offset": offset + 1,
            "timeout": 10,
            "limit": 100,
            "allowed_updates": '["channel_post"]',
        }
        try:
            resp = requests.get(
                self._api("getUpdates"), params=params, timeout=30
            )
        except requests.exceptions.RequestException as exc:
            raise VaultArchiveError(f"getUpdates failed: {exc}") from exc
        if not resp.ok:
            raise VaultArchiveError(
                f"getUpdates rejected: {resp.status_code} {resp.text[:300]}"
            )

        updates = resp.json().get("result", [])
        new_count = 0
        completable = True
        last_ok = offset
        for update in updates:
            update_id = update.get("update_id", 0)
            if not completable:
                break
            post = update.get("channel_post")
            if not post:
                last_ok = max(last_ok, update_id)
                continue
            if not self._is_our_channel(post):
                last_ok = max(last_ok, update_id)
                continue
            file_id = self._media_file_id(post)
            if not file_id:
                logger.debug("Skipping channel post without media: %s", post)
                last_ok = max(last_ok, update_id)
                continue
            try:
                if self._ingest_file_id(vault, file_id):
                    new_count += 1
                last_ok = max(last_ok, update_id)
            except Exception as exc:  # noqa: BLE001 - one bad post not fatal
                logger.warning(
                    "Failed to ingest channel media: %s; will retry on the "
                    "next sync",
                    exc,
                )
                completable = False

        if last_ok > offset:
            self.db.set_channel_offset(last_ok)
        return {"new": new_count, "offset": last_ok}

    def _is_our_channel(self, post: dict) -> bool:
        chat = post.get("chat") or {}
        return str(chat.get("id", "")) == self.chat_id

    @staticmethod
    def _media_file_id(post: dict) -> str | None:
        if post.get("document"):
            return post["document"].get("file_id")
        if post.get("video"):
            return post["video"].get("file_id")
        photos = post.get("photo")
        if photos:
            return max(photos, key=lambda s: s.get("file_size") or 0).get(
                "file_id"
            )
        return None

    def _ingest_file_id(self, vault: MediaVault, file_id: str) -> int:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "channel_media"
            downloaded = self.download_to(file_id, dest)
            return vault.ingest(
                downloaded, source="telegram", delete_source=True
            )

    def download_to(self, file_id: str, dest: Path) -> Path:
        """Fetch a Telegram media file and write it to `dest`."""
        try:
            info = requests.get(
                self._api("getFile"),
                params={"file_id": file_id},
                timeout=30,
            )
        except requests.exceptions.RequestException as exc:
            raise VaultArchiveError(f"getFile failed: {exc}") from exc
        if not info.ok:
            raise VaultArchiveError(
                f"getFile rejected: {info.status_code} {info.text[:300]}"
            )
        file_path = info.json().get("result", {}).get("file_path")
        if not file_path:
            raise VaultArchiveError(
                f"getFile returned no file_path: {info.text[:300]}"
            )

        url = f"https://api.telegram.org/file/bot{self.bot_token}/{file_path}"
        try:
            payload = requests.get(url, stream=True, timeout=120)
        except requests.exceptions.RequestException as exc:
            raise VaultArchiveError(f"file download failed: {exc}") from exc
        payload.raise_for_status()

        suffix = Path(file_path).suffix
        if suffix and dest.suffix != suffix:
            dest = dest.with_name(dest.stem + suffix)
        dest.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with open(dest, "wb") as fh:
            for chunk in payload.iter_content(chunk_size=1 << 16):
                fh.write(chunk)
                size += len(chunk)
        if size == 0:
            raise VaultArchiveError(f"Downloaded empty file for {file_id}")
        logger.info("Downloaded channel media (%d bytes) to %s", size, dest)
        return dest

    @staticmethod
    def resolve_channel_id(bot_token: str) -> int:
        """Resolve the numeric channel id by looking at channel posts.

        The operator adds the bot to the private channel and posts at least one
        message; the first `channel_post` update reveals the chat id.
        """
        url = _API.format(token=bot_token)
        params = {"timeout": 5, "limit": 100, "allowed_updates": '["channel_post"]'}
        try:
            resp = requests.get(f"{url}/getUpdates", params=params, timeout=20)
        except requests.exceptions.RequestException as exc:
            raise VaultArchiveError(f"getUpdates failed: {exc}") from exc
        if not resp.ok:
            raise VaultArchiveError(
                f"getUpdates rejected: {resp.status_code} {resp.text[:300]}"
            )
        for update in resp.json().get("result", []):
            post = update.get("channel_post")
            if post:
                return int(post["chat"]["id"])
        raise VaultArchiveError(
            "No channel_post found. Add the bot to the private channel and "
            "post one message, then retry --resolve-chat."
        )