"""Reel brief builder: creative set + vault anchors -> self-contained briefs.

Reuses `src/engine/scene_writer.py` (the same persona-aware shot planner as the
photo pipeline) and maps its shot plan 1:1 onto `ReelBrief` production orders,
selecting a real, subject-labelled vault photo per shot. Writes one
`reel_brief_NN.json` per shot plus a set `manifest.json`.

All network-free and deterministic given the shot plan; the only external
dependency is the optional local Ollama model used for copy (with a
deterministic fallback in the scene writer).
"""

from __future__ import annotations

import html
import json
import logging
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from ..engine.prompt_generator import PersonaStore, PromptGenerator
from ..engine.scene_writer import SceneWriter, load_sets
from .models import ReelBrief

logger = logging.getLogger(__name__)


def _clean(text: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", str(text or ""))).strip()


def _limit_words(text: str, max_words: int) -> str:
    words = text.split()
    if max_words and len(words) > max_words:
        return " ".join(words[:max_words])
    return text


def _limit_sentences(text: str, max_sentences: int) -> str:
    parts = [p for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p]
    if max_sentences and len(parts) > max_sentences:
        parts = parts[:max_sentences]
    return " ".join(parts).strip()


def build_briefs(
    set_key: str,
    set_cfg: dict,
    personas: dict,
    pools: dict,
    reel_cfg: dict,
    *,
    plan: list[dict],
    shots: int | None = None,
) -> tuple[list[ReelBrief], list[dict]]:
    """Map a scene-writer shot plan to ReelBriefs, one anchor per shot.

    Returns ``(briefs, missing)`` where ``missing`` lists shots that had no
    labelled source photo for their cat (reported instead of aborting).
    """
    duration = int(reel_cfg.get("preferred_min_duration_seconds", 5))
    aspect = str(reel_cfg.get("aspect", "9:16"))
    created_at = datetime.now(UTC).isoformat()
    count = min(shots or len(plan), len(plan))
    briefs: list[ReelBrief] = []
    missing: list[dict] = []

    for shot in plan[:count]:
        cat = shot["cat"]
        account_key = shot["account"]
        persona = personas.get(account_key)
        if persona is None:
            raise ValueError(
                f"No persona for account '{account_key}' (cat '{cat}')"
            )
        pool = pools.get(cat) or []
        if not pool:
            missing.append(
                {
                    "shot_index": shot["index"],
                    "cat": cat,
                    "reason": f"no labelled vault photo for '{cat}'",
                }
            )
            logger.warning(
                "Shot %d (%s): no labelled vault photo; skipping",
                shot["index"],
                cat,
            )
            continue

        anchor = pool[shot["index"] % len(pool)]
        max_words = persona.get("reel_rules", {}).get("max_words", 7)
        max_sentences = persona.get("caption_rules", {}).get("max_sentences", 3)
        reel_text = _limit_words(_clean(shot.get("reel_text", "")), max_words)
        caption = _limit_sentences(
            _clean(shot.get("caption", "")), max_sentences
        )
        hashtags = PromptGenerator._normalize_hashtags(
            shot.get("hashtags", []), persona
        )

        briefs.append(
            ReelBrief(
                brief_id=f"{set_key}:{shot['index']}",
                set=set_key,
                shot_index=shot["index"],
                account_key=account_key,
                cat=cat,
                motion_prompt=_clean(shot["motion_prompt"]),
                source_photo_path=str(anchor["path"]),
                source_vault_media_id=int(anchor["vault_media_id"]),
                expected_output=f"reel_{shot['index']:02d}.mp4",
                caption=caption,
                reel_text=reel_text,
                hashtags=hashtags,
                aspect=aspect,
                duration_seconds=duration,
                created_at=created_at,
            )
        )
    return briefs, missing


def write_briefs(out_dir: Path, briefs: list[ReelBrief]) -> list[Path]:
    """Write one `reel_brief_NN.json` per brief (idempotent, overwrites)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for brief in briefs:
        path = out_dir / f"reel_brief_{brief.shot_index:02d}.json"
        path.write_text(
            json.dumps(brief.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        written.append(path)
    return written


def write_manifest(
    out_dir: Path,
    set_key: str,
    briefs: list[ReelBrief],
    *,
    missing: list[dict] | None = None,
) -> Path:
    """Write the set manifest: briefs, expected clips (present/missing)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    clips = []
    for brief in briefs:
        clip_path = out_dir / brief.expected_output
        clips.append(
            {
                "brief_id": brief.brief_id,
                "expected_output": brief.expected_output,
                "produced": clip_path.exists() and clip_path.stat().st_size > 0,
            }
        )
    manifest = {
        "set": set_key,
        "generated_at": datetime.now(UTC).isoformat(),
        "briefs": [b.to_dict() for b in briefs],
        "clips": clips,
        "missing": missing or [],
    }
    path = out_dir / "manifest.json"
    path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return path


def plan_set(
    config: dict,
    set_key: str,
    *,
    shots: int | None = None,
    writer: SceneWriter | None = None,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Orchestrate planning: load set/personas/anchors, write briefs + manifest.

    Returns 0 when at least one brief was written, 2 for an unknown set, else 1.
    """
    creative = config.get("creative", {})
    reel_cfg = config.get("reel", {})
    ollama_cfg = config.get("ollama", {})
    persona_cfg = config.get("personas", {})
    pipeline_cfg = config.get("pipeline", {})

    sets = load_sets(creative.get("sets_path", "config/creative/sets.json"))
    if set_key not in sets:
        logger.error(
            "Unknown creative set '%s'. Available: %s",
            set_key,
            ", ".join(sorted(sets)),
        )
        return 2
    set_cfg = sets[set_key]
    personas = PersonaStore(
        persona_cfg.get("config_path", "config/personas.json")
    ).accounts()

    out_root = Path(creative.get("out_root", "data/generated"))
    out_dir = out_root / set_key

    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(pipeline_cfg.get("db_path", "data/pipeline.db"))
    try:
        from ..generator.anchors import last_10_anchors

        pools = last_10_anchors(conn)
    finally:
        if own_conn:
            conn.close()

    if writer is None:
        writer = SceneWriter(
            base_url=ollama_cfg.get("base_url", "http://localhost:11434"),
            model=ollama_cfg.get("text_model", "qwen2.5"),
            timeout_seconds=ollama_cfg.get("timeout_seconds", 120),
            num_predict=ollama_cfg.get("num_predict"),
        )

    logger.info("Writing shot plan for '%s' (%s)", set_key, set_cfg.get("name", set_key))
    plan = writer.write_plan(set_key, set_cfg, personas, shots=shots)
    briefs, missing = build_briefs(
        set_key, set_cfg, personas, pools, reel_cfg, plan=plan, shots=shots
    )

    written = write_briefs(out_dir, briefs)
    manifest = write_manifest(out_dir, set_key, briefs, missing=missing)
    logger.info(
        "Reel planning: %d brief(s) -> %s; manifest %s",
        len(written),
        out_dir,
        manifest,
    )
    if missing:
        logger.warning(
            "%d shot(s) had no labelled source photo: %s",
            len(missing),
            ", ".join(f"#{m['shot_index']} ({m['cat']})" for m in missing),
        )
    return 0 if briefs else 1
