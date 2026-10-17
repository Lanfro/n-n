"""Anchor pools for Reel briefs: the "keep the last 10" rule.

For each cat (label), select the 10 most recent solo-labeled vault photos as
identity anchors for the Reel briefs. If a cat has fewer than 10 solo photos,
top up from recent 'both'-labeled photos. The rolling window is recomputed at
call time by descending ingest id, so new photos slide the pool automatically.

Relocated from the retired static generator (`src/generator/anchors.py`); the
SD-specific `prep_square` helper (PIL-only) was dropped with the rest of that
path.
"""

from __future__ import annotations

import os
import sqlite3

ANCHOR_SIZE = 10


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
    """Return {label: anchor pool} for the two cat labels."""
    return {
        label: select_anchor_pool(db, label)
        for label in ("nero", "nuvola")
    }
