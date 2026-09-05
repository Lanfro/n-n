"""Content-addressed media vault: ingest, dedup, canonical storage.

Every unique cat picture ends up as exactly ONE file under
`data/vault/<yyyymm>/<sha256[:12]>.ext`, indexed by its sha256 in
`vault_media`. Re-ingesting a file with the same hash reuses the existing
record (and any stored vision analysis), never duplicating the archive copy.
"""

import hashlib
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

from ..database.db_manager import DBManager

logger = logging.getLogger(__name__)


class MediaVaultError(RuntimeError):
    pass


class MediaNotSupportedError(MediaVaultError):
    pass


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".avi"}


class MediaVault:
    def __init__(self, db: DBManager, root: str | Path):
        self.db = db
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def sha256(path: str | Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def classify(path: str | Path) -> str:
        suffix = Path(path).suffix.lower()
        if suffix in IMAGE_EXTENSIONS:
            return "image"
        if suffix in VIDEO_EXTENSIONS:
            return "video"
        raise MediaNotSupportedError(
            f"Unsupported media extension '{suffix or '<none>'}' for {path}"
        )

    def path_of(self, vault_media_id: int) -> Path:
        row = self.db.get_vault_media(vault_media_id)
        if row is None:
            raise MediaVaultError(f"Vault media {vault_media_id} not found")
        return Path(row["stored_path"])

    def ingest(
        self,
        path: str | Path,
        *,
        source: str = "drop",
        delete_source: bool = False,
    ) -> int:
        """Hash + dedup + copy into the vault. Returns the vault_media id."""
        src = Path(path)
        if not src.exists():
            raise MediaVaultError(f"Media file not found: {src}")

        digest = self.sha256(src)
        existing = self.db.get_vault_media_by_sha256(digest)
        if existing:
            logger.info(
                "Vault dedup: %s already archived as #%d (%s)",
                src.name,
                existing["id"],
                existing["source"],
            )
            if delete_source:
                src.unlink(missing_ok=True)
            return existing["id"]

        media_type = self.classify(src)
        size_bytes = src.stat().st_size
        yyyymm = datetime.now(UTC).strftime("%Y/%m")
        stored_dir = self.root / yyyymm
        stored_dir.mkdir(parents=True, exist_ok=True)
        suffix = src.suffix.lower() or ".bin"
        stored = (stored_dir / f"{digest[:12]}{suffix}").resolve()
        shutil.copy2(src, stored)

        vid = self.db.insert_vault_media(
            sha256=digest,
            original_filename=src.name,
            stored_path=str(stored),
            media_type=media_type,
            size_bytes=size_bytes,
            source=source,
        )
        logger.info("Vault ingest: %s -> %s (asset #%d)", src, stored, vid)
        if delete_source:
            src.unlink(missing_ok=True)
        return vid