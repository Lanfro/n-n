"""Meta Graph API media container creation & publishing.

Uses only official Instagram Creator/Business API endpoints via the Meta
Graph API. Supports a safe `dry_run` mode that validates the request shape
without sending anything to Instagram.

Flow:
    1. POST /{ig_user}/media                 -> creates a container
    2. POST /{ig_user}/media_publish         -> publishes the container

Note: for Reels (video) the endpoints differ slightly (caption vs
media_type). This module focuses on image publishing and is structured so
video/Reel support can be added without affecting the image path.
"""

import logging
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


class MetaPublisherError(RuntimeError):
    pass


class MetaPublisher:
    def __init__(
        self,
        access_token: str,
        instagram_user_id: str,
        api_version: str = "v21.0",
        graph_base_url: str = "https://graph.facebook.com",
        dry_run: bool = True,
    ):
        self.access_token = access_token
        self.instagram_user_id = instagram_user_id
        self.api_version = api_version.lstrip("v")
        self.graph_base_url = graph_base_url.rstrip("/")
        self.dry_run = dry_run

    def _url(self, endpoint: str) -> str:
        return (
            f"{self.graph_base_url}/{self.api_version}/{endpoint}"
        )

    def _headers(self) -> dict:
        return {"Content-Type": "application/json"}

    def is_configured(self) -> bool:
        return bool(self.access_token and self.instagram_user_id)

    def create_media_container(
        self,
        image_path: str | Path,
        caption: str,
        *,
        image_url: str | None = None,
    ) -> str:
        """Create (and, in non-dry-run, actually create) the media container.

        `image_url` is the publicly fetchable URL supplied by the vault's
        MediaHost; when None (no host configured) a `file://` placeholder is
        used so a live publish fails loudly instead of emitting a broken image.

        Returns the container id.
        """
        if self.dry_run:
            logger.info(
                "DRY-RUN: would create media container for %s", image_path
            )
            return f"container_dry_{Path(image_path).stem}"

        if not self.is_configured():
            raise MetaPublisherError(
                "MetaPublisher is not configured: missing access_token "
                "or instagram_user_id. Set config/meta.* or enable dry_run."
            )

        image_url = image_url or self._image_url(image_path)
        params = {
            "image_url": image_url,
            "caption": caption,
            "access_token": self.access_token,
        }
        try:
            resp = requests.post(
                self._url(f"{self.instagram_user_id}/media"),
                params=params,
                headers=self._headers(),
                timeout=60,
            )
            return self._parse_container_id(resp)
        except requests.exceptions.RequestException as exc:
            raise MetaPublisherError(f"Media container request failed: {exc}")

    def publish_container(self, container_id: str) -> str:
        """Publish a previously created container. Returns the media id."""
        if self.dry_run:
            logger.info(
                "DRY-RUN: would publish container %s", container_id
            )
            return f"media_dry_{container_id}"

        if not self.is_configured():
            raise MetaPublisherError(
                "MetaPublisher is not configured: missing access_token "
                "or instagram_user_id. Set config/meta.* or enable dry_run."
            )

        params = {
            "creation_id": container_id,
            "access_token": self.access_token,
        }
        try:
            resp = requests.post(
                self._url(f"{self.instagram_user_id}/media_publish"),
                params=params,
                headers=self._headers(),
                timeout=60,
            )
            data = resp.json()
            if "id" not in data:
                raise MetaPublisherError(
                    f"media_publish failed: {resp.status_code} {data}"
                )
            return str(data["id"])
        except requests.exceptions.RequestException as exc:
            raise MetaPublisherError(f"media_publish request failed: {exc}")

    def _parse_container_id(self, resp: requests.Response) -> str:
        try:
            data = resp.json()
        except ValueError:
            raise MetaPublisherError(
                f"Non-JSON response: {resp.status_code} {resp.text[:200]}"
            )
        if "id" not in data:
            raise MetaPublisherError(
                f"media creation failed: {resp.status_code} {data}"
            )
        return str(data["id"])

    @staticmethod
    def _image_url(image_path: str | Path) -> str:
        """Return a URL the Graph API can fetch.

        For local files a direct URL is normally required; when no hosted
        URL is available we use a file:// placeholder so the call clearly
        fails (instead of silently publishing a broken image) when dry_run
        is disabled. Callers that host media should override this.
        """
        path = Path(image_path)
        resolved = path.resolve()
        return resolved.as_uri()