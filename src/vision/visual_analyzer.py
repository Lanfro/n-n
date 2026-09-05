"""Local vision model integration (Ollama Qwen2-VL).

Encodes images to base64 and sends them to the local Ollama REST API.
Falls back to a clear, actionable error when Ollama is not running so the
rest of the pipeline degrades gracefully instead of crashing.
"""

import base64
from pathlib import Path

import requests

DEFAULT_PROMPT = (
    "Describe this cat's expression, posture, and surroundings in detail. "
    "Include facial expression, mood, background objects, and any notable "
    "details a content creator could use for a caption."
)


class OllamaUnavailableError(RuntimeError):
    """Raised when the local Ollama server cannot be reached."""


class VisualAnalyzer:
    def __init__(self, base_url: str, model: str, timeout_seconds: int = 120):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout_seconds

    def _encode_image(self, image_path: str | Path) -> str:
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {path}")
        return base64.b64encode(path.read_bytes()).decode("utf-8")

    def analyze(self, image_path: str | Path, prompt: str = DEFAULT_PROMPT) -> str:
        """Return the vision model's description of the image."""
        image_b64 = self._encode_image(image_path)
        payload = {
            "model": self.model,
            "prompt": prompt,
            "images": [image_b64],
            "stream": False,
        }
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
