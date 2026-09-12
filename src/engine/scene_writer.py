"""Creative-set scene writing: shot plans + on-voice copy via local Ollama.

A "creative set" (config/creative/sets.json) defines a theme, a handful of
scenes, negative prompt, and img2img strength. The scene writer expands that
catalog into a list of ready-to-render shots:

    {index, cat, account, scene, image_prompt, negative, denoise, seed,
     caption, reel_text, hashtags}

Every image_prompt embeds the cat's persona `image_descriptor`, so the same
character goes through the whole set. Copy (caption/reel/hashtags) is written
in the cat's voice by qwen2.5:3b; if the model is unreachable or returns
unparseable JSON the writer falls back to deterministic templates so the
pipeline never blocks on the LLM.
"""

import hashlib
import json
import re
from pathlib import Path

import requests

from .prompt_generator import OllamaUnavailableError  # noqa: F401 (re-export)

ACCOUNT_BY_CAT = {"nero": "cat_1", "nuvola": "cat_2"}

SHOT_INSTRUCTION = (
    "Respond ONLY with a valid JSON object using exactly these keys:\n"
    '{{"prompt": "<image generation prompt>", '
    '"caption": "<caption>", '
    '"reel": "<3-7 word hook>", '
    '"hashtags": ["tag1", "tag2", ...]}}\n'
    "The prompt must faithfully describe {name} as: {descriptor}. "
    "The prompt must depict this scene: {scene}. "
    "Style: {style}. One single main subject, vertical 4:5 portrait, "
    "photorealistic keywords, no text or letters, no watermark.\n"
    "The caption must be a NEW short text (at most 3 sentences) written "
    "first-person from {name}'s point of view about living this moment, "
    "in this voice: {voice}. It must NOT repeat or describe the image "
    "prompt; it is dialogue, not a description.\n"
    "The reel must be a {reel_words}-word dramatic hook in the same voice.\n"
    "Return only the JSON object, without code fences."
)

QUALITY_TAGS = (
    "photorealistic, sharp focus, soft natural light, 4:5 vertical portrait, "
    "single subject, no text, no watermark"
)


def load_sets(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _seed_for(set_key: str, index: int) -> int:
    digest = hashlib.sha256(f"{set_key}:{index}".encode()).hexdigest()
    return int(digest[:8], 16)


def _fallback_shot(
    set_key: str, set_cfg: dict, index: int, scene: str, persona: dict
) -> dict:
    name = persona.get("name", "cat")
    descriptor = persona.get("image_descriptor", "a photorealistic cat")
    style = set_cfg.get("style", "photorealistic")
    connective = (
        f"in the style of {scene.split('the style of', 1)[1]}"
        if "the style of" in scene
        else scene
    )
    prompt = (
        f"photo of {descriptor}, {connective}, {style}, "
        f"{QUALITY_TAGS}"
    )
    voice = persona.get("name", name)
    caption = (
        f"Scene: {scene.split(',' )[0]} - pitched from {name}&#39;s point "
        f"of view, in the voice of {voice}."
    )
    return {
        "index": index,
        "cat": name.lower(),
        "account": ACCOUNT_BY_CAT.get(name.lower(), "cat_1"),
        "scene": scene,
        "image_prompt": prompt,
        "negative": set_cfg.get("negative", ""),
        "denoise": float(set_cfg.get("denoise", 0.45)),
        "seed": _seed_for(set_key, index),
        "caption": caption,
        "reel_text": f"{name} POV",
        "hashtags": ["cat", "cats"],
    }


class SceneWriter:
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: int = 120,
        num_predict: int | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout_seconds
        self.num_predict = num_predict

    def write_plan(
        self,
        set_key: str,
        set_cfg: dict,
        personas: dict,
        *,
        shots: int | None = None,
    ) -> list[dict]:
        scenes = set_cfg.get("scenes", [])
        count = min(shots or int(set_cfg.get("shots", 5)), len(scenes))
        mode = set_cfg.get("cat", "both")
        plan = []
        for i in range(count):
            scene = scenes[i]
            if mode == "both":
                cat = "nero" if i % 2 == 0 else "nuvola"
            else:
                cat = mode
            persona = personas[ACCOUNT_BY_CAT[cat]]
            plan.append(self._write_shot(set_key, set_cfg, i, scene, persona))
        return plan

    def _write_shot(
        self, set_key: str, set_cfg: dict, index: int, scene: str, persona: dict
    ) -> dict:
        caption_rules = persona.get("caption_rules", {})
        voice = caption_rules.get("tone", persona.get("name", "cat"))
        style_note = caption_rules.get("style_note", "")
        if style_note:
            voice = f"{voice}. {style_note}"
        instruction = SHOT_INSTRUCTION.format(
            name=persona.get("name", "cat"),
            descriptor=persona.get("image_descriptor", "a photorealistic cat"),
            scene=scene,
            style=set_cfg.get("style", "photorealistic"),
            voice=voice,
            reel_words=persona.get("reel_rules", {}).get("max_words", 7),
        )
        raw = None
        for attempt in range(2):
            raw = self._call_model(instruction)
            if raw and self._parse_json(raw).get("prompt"):
                break
        if raw is None:
            return _fallback_shot(set_key, set_cfg, index, scene, persona)

        parsed = self._parse_json(raw)
        if not parsed.get("prompt") or not parsed.get("caption"):
            return _fallback_shot(set_key, set_cfg, index, scene, persona)

        caption = str(parsed.get("caption", ""))
        reel = str(parsed.get("reel", ""))
        caption = re.sub(
            r"(?:[,;:]*\s+#[0-9A-Za-z_]+[,.;!?]*)+$", "", caption
        ).rstrip(" ,;:.").strip()
        if caption.lower().strip().startswith("scene:") or len(caption) < 8:
            return _fallback_shot(set_key, set_cfg, index, scene, persona)
        if re.search(r"\bpoint\s+of\s+view\b", caption, re.IGNORECASE):
            return _fallback_shot(set_key, set_cfg, index, scene, persona)

        return {
            "index": index,
            "cat": persona.get("name", "cat").lower(),
            "account": ACCOUNT_BY_CAT.get(
                persona.get("name", "cat").lower(), "cat_1"
            ),
            "scene": scene,
            "image_prompt": str(parsed["prompt"]).strip(),
            "negative": set_cfg.get("negative", ""),
            "denoise": float(set_cfg.get("denoise", 0.45)),
            "seed": _seed_for(set_key, index),
            "caption": re.sub(r"\s+", " ", caption).strip(),
            "reel_text": re.sub(r"\s+", " ", reel).strip(),
            "hashtags": [
                str(h).lstrip("#").lower()
                for h in parsed.get("hashtags", [])[:12]
            ],
        }

    def _call_model(self, prompt: str) -> str | None:
        payload: dict = {"model": self.model, "prompt": prompt, "stream": False}
        if self.num_predict:
            payload["options"] = {"num_predict": self.num_predict}
        try:
            resp = requests.post(
                f"{self.base_url}/api/generate", json=payload, timeout=self.timeout
            )
            resp.raise_for_status()
        except (requests.exceptions.RequestException, ValueError):
            return None
        return resp.json().get("response")

    @staticmethod
    def _parse_json(raw: str) -> dict:
        text = raw.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text).strip()
        text = re.sub(r"\s*```$", "", text).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
            return {}