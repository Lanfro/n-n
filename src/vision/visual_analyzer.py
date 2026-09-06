"""Local vision model integration (Ollama Qwen2-VL).

Encodes images to base64 and sends them to the local Ollama REST API.
Large photos are downscaled client-side so the encoded image stays well
under Ollama's default context window (phones can otherwise produce
~4096+ image tokens and overflow it). Falls back to a clear, actionable
error when Ollama is not running so the rest of the pipeline degrades
gracefully instead of crashing.
"""

import base64
import io
from pathlib import Path

import requests
from PIL import Image, ImageOps

DEFAULT_PROMPT = (
    "Describe this cat's expression, posture, and surroundings in detail. "
    "Include facial expression, mood, background objects, and any notable "
    "details a content creator could use for a caption."
)


class OllamaUnavailableError(RuntimeError):
    """Raised when the local Ollama server cannot be reached."""


class VisualAnalyzer:
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: int = 120,
        max_side: int = 1280,
        num_predict: int | None = None,
        keep_alive: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout_seconds
        self.max_side = max_side
        self.num_predict = num_predict
        self.keep_alive = keep_alive

    def _prepare_image_bytes(self, image_path: str | Path) -> bytes:
        """Return image bytes sized for the model context window.

        Images whose longest side exceeds `max_side` are downscaled and
        re-encoded; smaller images pass through byte-for-byte unchanged so
        existing behaviour is preserved.
        """
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {path}")
        data = path.read_bytes()
        try:
            with Image.open(io.BytesIO(data)) as img:
                img = ImageOps.exif_transpose(img)
                if max(img.size) <= self.max_side:
                    return data
                ratio = self.max_side / max(img.size)
                img = img.resize(
                    (round(img.width * ratio), round(img.height * ratio)),
                    Image.LANCZOS,
                )
        except Exception:  # noqa: BLE001 - let Ollama try the raw file
            return data

        image = img
        if image.mode in ("RGBA", "LA") or (
            image.mode not in ("RGB", "L") and image.format == "PNG"
        ):
            image = image.convert("RGBA")
            bg = Image.new("RGBA", image.size, (255, 255, 255, 255))
            image = Image.alpha_composite(bg, image).convert("RGB")
        elif image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=90)
        return buf.getvalue()

    def _encode_image(self, image_path: str | Path) -> str:
        image_b64 = base64.b64encode(
            self._prepare_image_bytes(image_path)
        ).decode("utf-8")
        return image_b64

    def analyze(self, image_path: str | Path, prompt: str = DEFAULT_PROMPT) -> str:
        """Return the vision model's description of the image."""
        image_b64 = self._encode_image(image_path)
        payload = {
            "model": self.model,
            "prompt": prompt,
            "images": [image_b64],
            "stream": False,
        }
        if self.keep_alive is not None:
            payload["keep_alive"] = self.keep_alive
        if self.num_predict is not None:
            payload["options"] = {"num_predict": self.num_predict}
        url = f"{self.base_url}/api/generate"
        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
        except requests.exceptions.ConnectionError as exc:
            raise OllamaUnavailableError(
                f"Ollama is not reachable at {self.base_url}. "
                f"Start it with `ollama serve` and ensure the '{self.model}' "
                f"model is pulled (`ollama pull {self.model}`)."
            ) from exc
        except requests.exceptions.Timeout as exc:
            raise OllamaUnavailableError(
                f"Ollama request timed out after {self.timeout}s."
            ) from exc

        data = resp.json()
        return (data.get("response") or "").strip()
