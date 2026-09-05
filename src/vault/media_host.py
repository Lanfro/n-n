"""Publish-time media hosting: uploads vault media and returns public URLs.

Meta's Graph API fetches the image from a public `image_url`, so local archive
files cannot be published live. This module provides an R2-first (S3-compatible)
host plus a generic S3 backend and a `NoHost` fallback that keeps the pipeline's
fail-loud local behavior when nothing is configured.

Ref: specs/001-instagram-growth-pipeline/contracts/media-host.md
"""

import hashlib
import logging
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)


class MediaHostError(RuntimeError):
    pass


class MediaHostConfigError(MediaHostError):
    pass


class MediaHost(ABC):
    backend = "none"

    @abstractmethod
    def is_configured(self) -> bool: ...

    @abstractmethod
    def upload(
        self, local_path: str | Path, *, key_prefix: str = ""
    ) -> str | None:
        """Upload and return a public URL. Returns None when not configured."""

    @abstractmethod
    def delete(self, object_key: str) -> bool: ...


class NoHost(MediaHost):
    backend = "none"

    def is_configured(self) -> bool:
        return False

    def upload(self, local_path, *, key_prefix: str = "") -> str | None:
        return None

    def delete(self, object_key: str) -> bool:
        return False


class S3Host(MediaHost):
    backend = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        public_base_url: str,
        access_key_id: str,
        secret_access_key: str,
        endpoint_url: str = "",
        region_name: str = "",
    ):
        missing = [
            cfg
            for cfg, value in {
                "bucket": bucket,
                "public_base_url": public_base_url,
                "access_key_id": access_key_id,
                "secret_access_key": secret_access_key,
            }.items()
            if not value
        ]
        if missing:
            raise MediaHostConfigError(
                f"Missing required host config for backend '{self.backend}': "
                f"{', '.join(missing)}"
            )
        self.bucket = bucket
        self.public_base_url = public_base_url.rstrip("/")
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.endpoint_url = endpoint_url
        self.region_name = region_name
        self._client = None

    def is_configured(self) -> bool:
        return True

    def _get_client(self):
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - guarded by lockfile
                raise MediaHostError("boto3 is not installed") from exc
            kwargs = {
                "aws_access_key_id": self.access_key_id,
                "aws_secret_access_key": self.secret_access_key,
            }
            if self.region_name:
                kwargs["region_name"] = self.region_name
            self._client = boto3.client(
                "s3", endpoint_url=self.endpoint_url or None, **kwargs
            )
        return self._client

    @staticmethod
    def _sha256(local_path: str | Path) -> str:
        digest = hashlib.sha256()
        with open(local_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _object_key(self, local_path: str | Path, key_prefix: str) -> str:
        sha = self._sha256(local_path)
        suffix = Path(local_path).suffix.lstrip(".") or "bin"
        return f"{key_prefix}media/{sha[:2]}/{sha[:12]}.{suffix}"

    def upload(
        self, local_path: str | Path, *, key_prefix: str = ""
    ) -> str | None:
        key = self._object_key(local_path, key_prefix)
        try:
            self._get_client().upload_file(
                str(local_path), self.bucket, key
            )
        except Exception as exc:  # noqa: BLE001 - surface as MediaHostError
            raise MediaHostError(
                f"Upload to '{self.bucket}' failed for {key}: {exc}"
            )
        url = f"{self.public_base_url}/{key}"
        logger.info("Hosted media under %s", key)
        return url

    def delete(self, object_key: str) -> bool:
        try:
            self._get_client().delete_object(Bucket=self.bucket, Key=object_key)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Delete failed for %s: %s", object_key, exc)
            return False


class R2Host(S3Host):
    backend = "r2"


def build_media_host(config: dict) -> MediaHost:
    """Build a MediaHost from a `vault.host` config block (None-safe)."""
    host_cfg = config.get("host", {}) or {}
    backend = host_cfg.get("backend", "none") or "none"
    if backend in {"r2", "s3"}:
        cls = R2Host if backend == "r2" else S3Host
        return cls(
            bucket=host_cfg.get("bucket", ""),
            public_base_url=host_cfg.get("public_base_url", ""),
            access_key_id=host_cfg.get("access_key_id", ""),
            secret_access_key=host_cfg.get("secret_access_key", ""),
            endpoint_url=host_cfg.get("endpoint_url", ""),
            region_name=host_cfg.get("region_name", ""),
        )
    return NoHost()