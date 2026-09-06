"""Persona-driven prompt engineering & LLM text generation.

Loads persona definitions from personas.json and drives a local Ollama
text model to produce structured Instagram output:

    * reel_text: on-screen hook (3-7 words)
    * caption:   narrative aligned with the persona (<=3 sentences)
    * hashtags:  list of hashtags without the leading '#'
"""

import json
import re
from pathlib import Path

import requests

from ..vision.visual_analyzer import OllamaUnavailableError

OUTPUT_INSTRUCTION = (
    "Respond ONLY with a valid JSON object using exactly these keys:\n"
    '{"reel_text": "<3-7 word on-screen hook>", '
    '"caption": "<caption>", '
    '"hashtags": ["tag1", "tag2", ...]}\n'
    "Do not include markdown fences or any other text."
)

# Hashtags requiring no underscore/spaces; used to enrich generic output.
CANONICAL_HASHTAGS = [
    "exoticshorthair",
    "exoticcats",
    "persiancat",
    "flatfacecat",
    "catsofinstagram",
    "instacat",
    "catlover",
    "catoftheday",
    "catstagram",
    "catmemes",
    "cutecats",
]


class PersonaNotFoundError(KeyError):
    pass


class PersonaStore:
    def __init__(self, config_path: str | Path):
        self.path = Path(config_path)
        self._data = self._load()

    def _load(self) -> dict:
        with open(self.path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def accounts(self) -> dict:
        return self._data.get("accounts", {})

    def get(self, account_key: str) -> dict:
        accounts = self.accounts()
        if account_key not in accounts:
            raise PersonaNotFoundError(
                f"Unknown account '{account_key}'. "
                f"Available: {', '.join(accounts)}"
            )
        return accounts[account_key]


class PromptGenerator:
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: int = 120,
        keep_alive: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout_seconds
        self.keep_alive = keep_alive

    def _assemble_prompt(self, persona: dict, vision_description: str) -> str:
        reel_rules = persona.get("reel_rules", {})
        caption_rules = persona.get("caption_rules", {})
        max_reel_words = reel_rules.get("max_words", 7)
        max_caption_sentences = caption_rules.get("max_sentences", 3)
        hashtag_count = persona.get("hashtag_count", 12)

        system = persona["system_prompt"]
        return f"""{system}

OUTPUT CONSTRAINTS:
- REEL_TEXT must be at most {max_reel_words} words and use {caption_rules.get('tone', 'the accent above')}.
- CAPTION must be at most {max_caption_sentences} short sentences and {caption_rules.get('style_note', '')}.
- HASHTAGS must include exactly {hashtag_count} niche hashtags, starting with {', '.join('#' + h for h in persona.get('base_hashtags', []))}.

VISUAL CONTEXT:
"{vision_description}"

{OUTPUT_INSTRUCTION}
"""

    def _parse_json(self, raw: str, /, default: dict) -> dict:
        text = raw.strip()
        # Strip any surrounding markdown fences
        text = re.sub(r"^```(?:json)?\s*", "", text).strip()
        text = re.sub(r"\s*```$", "", text).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Best-effort: extract the JSON object substring
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
            return dict(default)

    @staticmethod
    def _normalize_hashtags(tags: list, persona: dict) -> list[str]:
        cleaned = []
        for t in tags:
            t = str(t).strip().lstrip("#").replace(" ", "").lower()
            if t and t not in cleaned:
                cleaned.append(t)
        base = [h.lstrip("#").lower() for h in persona.get("base_hashtags", [])]
        for h in base:
            if h not in cleaned:
                cleaned.append(h)
        for h in CANONICAL_HASHTAGS:
            if len(cleaned) >= persona.get("hashtag_count", 12):
                break
            if h not in cleaned:
                cleaned.append(h)
        return cleaned[: persona.get("hashtag_count", 12)]

    def generate(
        self,
        persona: dict,
        vision_description: str,
        *,
        system_override: str | None = None,
    ) -> dict:
        """Generate and return {reel_text, caption, hashtags}."""
        if system_override:
            persona = {**persona, "system_prompt": system_override}

        prompt = self._assemble_prompt(persona, vision_description)
        payload: dict = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }
        if self.keep_alive:
            payload["keep_alive"] = self.keep_alive
        url = f"{self.base_url}/api/generate"
        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
        except requests.exceptions.ConnectionError as exc:
            raise OllamaUnavailableError(
                f"Ollama is not reachable at {self.base_url}. "
                f"Start it with `ollama serve` and pull '{self.model}' "
                f"(`ollama pull {self.model}`)."
            ) from exc
        except requests.exceptions.Timeout as exc:
            raise OllamaUnavailableError(
                f"Ollama request timed out after {self.timeout}s."
            ) from exc

        raw = (resp.json().get("response") or "").strip()
        default = {
            "reel_text": "",
            "caption": "",
            "hashtags": self._normalize_hashtags([], persona),
        }
        parsed = self._parse_json(raw, default=default)
        return {
            "reel_text": str(parsed.get("reel_text") or "").strip(),
            "caption": str(parsed.get("caption") or "").strip(),
            "hashtags": self._normalize_hashtags(
                parsed.get("hashtags") or [], persona
            ),
        }
