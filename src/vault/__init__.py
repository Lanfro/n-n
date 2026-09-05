"""Media vault package: archive, Telegram archive + sync, publish-time hosting."""

from .media_host import (
    MediaHost,
    MediaHostConfigError,
    MediaHostError,
    NoHost,
    R2Host,
    S3Host,
    build_media_host,
)
from .media_vault import (
    MediaNotSupportedError,
    MediaVault,
    MediaVaultError,
)
from .telegram_archive import TelegramVault, VaultArchiveError

__all__ = [
    "MediaHost",
    "MediaHostConfigError",
    "MediaHostError",
    "MediaNotSupportedError",
    "MediaVault",
    "MediaVaultError",
    "NoHost",
    "R2Host",
    "S3Host",
    "TelegramVault",
    "VaultArchiveError",
    "build_media_host",
]