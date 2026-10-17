"""Unit tests for the Reel brief builder and scene-motion helpers."""

import json

import pytest

from src.engine.scene_writer import (
    build_motion_prompt,
    scene_motion,
    scene_text,
)
from src.reel.brief import build_briefs, write_briefs, write_manifest

PERSONAS = {
    "cat_1": {
        "name": "Nero",
        "reel_rules": {"max_words": 4},
        "caption_rules": {"max_sentences": 2},
        "hashtag_count": 3,
        "base_hashtags": ["catlogic"],
    },
    "cat_2": {
        "name": "Nuvola",
        "reel_rules": {"max_words": 4},
        "caption_rules": {"max_sentences": 2},
        "hashtag_count": 3,
        "base_hashtags": ["catdrama"],
    },
}

POOLS = {
    "nero": [
        {"vault_media_id": 1, "path": "data/vault/nero1.jpg"},
        {"vault_media_id": 2, "path": "data/vault/nero2.jpg"},
    ],
    "nuvola": [{"vault_media_id": 3, "path": "data/vault/nuvola1.jpg"}],
}

REEL_CFG = {"aspect": "9:16", "preferred_min_duration_seconds": 5}


def _plan() -> list[dict]:
    return [
        {
            "index": 0,
            "cat": "nero",
            "account": "cat_1",
            "motion_prompt": "blinks slowly, tail curling",
            "reel_text": "one two three four five",
            "caption": "A. B. C. D.",
            "hashtags": ["x", "y"],
        },
        {
            "index": 1,
            "cat": "nuvola",
            "account": "cat_2",
            "motion_prompt": "turns head gently",
            "reel_text": "short hook",
            "caption": "Only one sentence.",
            "hashtags": [],
        },
    ]


def test_build_briefs_maps_plan_and_anchors():
    briefs, missing = build_briefs(
        "parallel_lives", {}, PERSONAS, POOLS, REEL_CFG, plan=_plan()
    )
    assert missing == []
    assert [b.brief_id for b in briefs] == [
        "parallel_lives:0",
        "parallel_lives:1",
    ]
    assert [b.expected_output for b in briefs] == ["reel_00.mp4", "reel_01.mp4"]
    assert briefs[0].source_vault_media_id == 1
    assert briefs[0].source_photo_path.endswith("nero1.jpg")
    assert briefs[1].source_vault_media_id == 3
    assert briefs[0].motion_prompt.startswith("blinks")
    assert briefs[0].aspect == "9:16"
    assert briefs[0].duration_seconds == 5
    assert briefs[0].set == "parallel_lives"
    assert briefs[0].created_at


def test_briefs_respect_persona_limits():
    briefs, _ = build_briefs(
        "s", {}, PERSONAS, POOLS, REEL_CFG, plan=_plan()
    )
    nero = briefs[0]
    assert len(nero.reel_text.split()) == 4  # truncated from 5
    assert nero.caption == "A. B."  # limited to 2 sentences
    assert len(nero.hashtags) == 3  # padded to persona hashtag_count
    assert "catlogic" in nero.hashtags
    assert nero.reel_text == "one two three four"


def test_missing_source_photo_is_reported_not_fatal():
    pools = {"nero": POOLS["nero"], "nuvola": []}
    briefs, missing = build_briefs(
        "s", {}, PERSONAS, pools, REEL_CFG, plan=_plan()
    )
    assert [b.brief_id for b in briefs] == ["s:0"]
    assert len(missing) == 1
    assert missing[0]["cat"] == "nuvola"
    assert missing[0]["shot_index"] == 1


def test_unknown_account_fails_fast():
    plan = json.loads(json.dumps(_plan()))
    plan[1]["account"] = "cat_9"
    with pytest.raises(ValueError):
        build_briefs("s", {}, PERSONAS, POOLS, REEL_CFG, plan=plan)


def test_shots_limit_bounds_brief_count():
    briefs, _ = build_briefs(
        "s", {}, PERSONAS, POOLS, REEL_CFG, plan=_plan(), shots=1
    )
    assert len(briefs) == 1


def test_anchor_round_robin_varies_per_shot():
    plan = [
        {"index": 0, "cat": "nero", "account": "cat_1", "motion_prompt": "m",
         "reel_text": "hook here", "caption": "One.", "hashtags": []},
        {"index": 1, "cat": "nero", "account": "cat_1", "motion_prompt": "m",
         "reel_text": "hook here", "caption": "One.", "hashtags": []},
    ]
    briefs, _ = build_briefs("s", {}, PERSONAS, POOLS, REEL_CFG, plan=plan)
    assert briefs[0].source_vault_media_id == 1
    assert briefs[1].source_vault_media_id == 2


def test_write_briefs_and_manifest(tmp_path):
    briefs, _ = build_briefs(
        "s", {}, PERSONAS, POOLS, REEL_CFG, plan=_plan()
    )
    write_briefs(tmp_path, briefs)
    brief_file = tmp_path / "reel_brief_00.json"
    assert brief_file.exists()
    payload = json.loads(brief_file.read_text(encoding="utf-8"))
    assert payload["brief_id"] == "s:0"
    assert payload["expected_output"] == "reel_00.mp4"

    (tmp_path / "reel_00.mp4").write_bytes(b"fake clip")
    manifest_path = write_manifest(tmp_path, "s", briefs)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["set"] == "s"
    produced = {c["brief_id"]: c["produced"] for c in manifest["clips"]}
    assert produced["s:0"] is True
    assert produced["s:1"] is False


def test_scene_helpers_handle_dict_and_string():
    assert scene_text({"scene": "a room", "motion": "blinks"}) == "a room"
    assert scene_text("plain scene") == "plain scene"
    assert scene_motion({"scene": "a room", "motion": "blinks"}) == "blinks"
    assert scene_motion("plain scene") == ""


def test_build_motion_prompt_combines_cue_and_scene():
    prompt = build_motion_prompt("blinks slowly", "a Parisian cafe")
    assert "blinks slowly" in prompt
    assert "a Parisian cafe" in prompt
    assert build_motion_prompt("", "a room")  # default cue when motion empty
