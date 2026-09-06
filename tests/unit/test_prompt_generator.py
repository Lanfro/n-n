"""Tests for the persona-driven text generator (--drafts-vault backend)."""

import requests

from src.engine.prompt_generator import PromptGenerator

PERSONA = {
    "name": "cat_1",
    "system_prompt": "You are an exotic shorthair cat.",
    "base_hashtags": ["exoticshorthair"],
    "hashtag_count": 2,
    "reel_rules": {"max_words": 7},
    "caption_rules": {"max_sentences": 3, "tone": "playful"},
}


def test_generate_sends_keep_alive_when_configured(monkeypatch) -> None:
    captured = {}

    def fake_post(url, json=None, timeout=120):
        captured["json"] = json

        class R:
            status_code = 200
            text = ""

            @staticmethod
            def raise_for_status():
                return None

            def json(self):
                return {
                    "response": '{"reel_text": "fluffy loaf", '
                    '"caption": "A soft purr for you.", '
                    '"hashtags": ["exoticshorthair"]}'
                }

        return R()

    monkeypatch.setattr(requests, "post", fake_post)
    gen = PromptGenerator(
        "http://localhost:11434", "qwen2.5", keep_alive="30m"
    )
    out = gen.generate(PERSONA, "A grey cat on a sofa.")
    assert out["caption"] == "A soft purr for you."
    assert captured["json"]["keep_alive"] == "30m"


def test_generate_omits_keep_alive_by_default(monkeypatch) -> None:
    captured = {}

    def fake_post(url, json=None, timeout=120):
        captured["json"] = json

        class R:
            status_code = 200
            text = ""

            @staticmethod
            def raise_for_status():
                return None

            def json(self):
                return {"response": '{"reel_text": "hi", "caption": "ok"}'}

        return R()

    monkeypatch.setattr(requests, "post", fake_post)
    gen = PromptGenerator("http://localhost:11434", "qwen2.5")
    gen.generate(PERSONA, "A grey cat on a sofa.")
    assert "keep_alive" not in captured["json"]
    assert "options" not in captured["json"]


def test_generate_sends_num_predict_when_configured(monkeypatch) -> None:
    captured = {}

    def fake_post(url, json=None, timeout=120):
        captured["json"] = json

        class R:
            status_code = 200
            text = ""

            @staticmethod
            def raise_for_status():
                return None

            def json(self):
                return {"response": '{"reel_text": "hi", "caption": "ok"}'}

        return R()

    monkeypatch.setattr(requests, "post", fake_post)
    gen = PromptGenerator(
        "http://localhost:11434", "qwen2.5", num_predict=512
    )
    gen.generate(PERSONA, "A grey cat on a sofa.")
    assert captured["json"]["options"] == {"num_predict": 512}
    assert "keep_alive" not in captured["json"]


def test_generate_parses_fenced_json(monkeypatch) -> None:
    def fake_post(url, json=None, timeout=120):
        class R:
            status_code = 200
            text = ""

            @staticmethod
            def raise_for_status():
                return None

            def json(self):
                return {
                    "response": '```json\n{"reel_text": "hi", '
                    '"caption": "hello", "hashtags": ["cat"]}\n```'
                }

        return R()

    monkeypatch.setattr(requests, "post", fake_post)
    gen = PromptGenerator("http://localhost:11434", "qwen2.5")
    out = gen.generate(PERSONA, "A grey cat on a sofa.")
    assert out["reel_text"] == "hi"
    assert out["caption"] == "hello"
    assert "cat" in out["hashtags"]


def test_generate_falls_back_to_blank_on_bad_json(monkeypatch) -> None:
    def fake_post(url, json=None, timeout=120):
        class R:
            status_code = 200
            text = ""

            @staticmethod
            def raise_for_status():
                return None

            def json(self):
                return {"response": "totally not json"}

        return R()

    monkeypatch.setattr(requests, "post", fake_post)
    gen = PromptGenerator("http://localhost:11434", "qwen2.5")
    out = gen.generate(PERSONA, "A grey cat on a sofa.")
    assert out["caption"] == ""
    assert out["hashtags"] == ["exoticshorthair", "exoticcats"]


def test_generate_strips_inline_hashtags_from_caption(monkeypatch) -> None:
    """A trailing '#tag' run inside the caption is removed on parse."""

    def fake_post(url, json=None, timeout=120):
        class R:
            status_code = 200
            text = ""

            @staticmethod
            def raise_for_status():
                return None

            def json(self):
                return {
                    "response": '{"reel_text": "no naps", '
                    '"caption": "Annoyed again. #exoticshorthair '
                    '#catlogic #cynicalcat", '
                    '"hashtags": ["exoticshorthair", "catlogic"]}'
                }

        return R()

    monkeypatch.setattr(requests, "post", fake_post)
    gen = PromptGenerator("http://localhost:11434", "qwen2.5")
    out = gen.generate(PERSONA, "A grey cat on a sofa.")
    assert out["caption"] == "Annoyed again."
    assert out["hashtags"] == ["exoticshorthair", "catlogic"]


def test_strip_inline_hashtags_mid_caption_untouched() -> None:
    gen = PromptGenerator("http://localhost:11434", "qwen2.5")
    # Only the *trailing* run is removed; mid-sentence tags survive.
    text = "My #favorite spot on the shelf, as always."
    assert gen._strip_inline_hashtags(text) == text
    assert gen._strip_inline_hashtags("") == ""
    assert gen._strip_inline_hashtags("  ") == ""