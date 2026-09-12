"""Twin anchor pools: the "keep the last 10" rule.

For each cat (label), select the 10 most recent solo-labeled vault photos
as img2img identity anchors. If a cat has fewer than 10 solo photos, top up
from recent 'both'-labeled photos. Rolling window: recomputed at call time
by descending ingest id, so new photos slide the pool automatically.
"""

from __future__ import annotations

import os
import sqlite3

from PIL import Image

ANCHOR_SIZE = 10
SQUARE_PX = 512


def _select_ordered(db: sqlite3.Connection, label: str, limit: int) -> list[tuple]:
    solo = db.execute(
        """
        select m.id, m.stored_path
        from vault_media m
        join vault_subjects s on s.vault_media_id = m.id
        where m.media_type = 'image' and s.label = ?
        order by m.id desc
        limit ?
        """,
        (label, limit),
    ).fetchall()
    if len(solo) >= limit:
        return solo
    top_up = db.execute(
        """
        select m.id, m.stored_path
        from vault_media m
        join vault_subjects s on s.vault_media_id = m.id
        where m.media_type = 'image' and s.label = 'both'
        order by m.id desc
        limit ?
        """,
        (limit - len(solo),),
    ).fetchall()
    used = {r[0] for r in solo}
    return solo + [r for r in top_up if r[0] not in used]


def select_anchor_pool(
    db: sqlite3.Connection, label: str, size: int = ANCHOR_SIZE
) -> list[dict]:
    """Return the last `size` photos for `label` as {vault_media_id, path}."""
    return [
        {"vault_media_id": rid, "path": path}
        for rid, path in _select_ordered(db, label, size)
        if path and os.path.exists(path)
    ]


def last_10_anchors(db: sqlite3.Connection) -> dict[str, list[dict]]:
    return {
        label: select_anchor_pool(db, label)
        for label in ("nero", "nuvola")
    }


def prep_square(path: str, px: int = SQUARE_PX) -> Image.Image:
    """Center-crop to a square and resize to `px` (SD1.5 expects 512)."""
    img = Image.open(path).convert("RGB")
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return img.crop((left, top, left + side, top + side)).resize(
        (px, px), Image.LANCZOS
    )


def anchor_for_shot(pool: list[dict], shot_index: int) -> dict:
    """Deterministic round-robin anchor selection across a shot list."""
    return pool[shot_index % len(pool)]