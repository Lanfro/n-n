# Quickstart — Validation Guide

Phase 1 output of `/speckit.plan`. Runnable scenarios that prove the feature
works end-to-end. Full implementation lives in `tasks.md`; this file is the
validation/run guide.

## Prerequisites

- `uv` installed (`winget install astral-sh.uv` or equivalent).
- Python >= 3.11.
- Repo cloned; run `uv sync` once.

## Setup

```bash
uv sync            # create env + lock
uv run ruff check  # lint gate
```

## Scenario 1 — Hermetic dry-run pipeline (no external services)

```
uv run main.py --account cat_1 --media data/input_media/sample_cat.jpg --dry-run
```

**Expected**: log lines showing auto-approval, "would create media container",
"would publish container", and finally `post <id> reached PUBLISHED`. Exit code 0.

**Proves**: DB lifecycle runs `PENDING_ANALYSIS -> AWAITING_APPROVAL ->
APPROVED -> PUBLISHED`; approval gate is the only path into `PUBLISHED`;
publisher does not touch the network in dry-run.

## Scenario 2 — Graceful failure when local AI is down

```
uv run main.py --account cat_2 --media data/input_media/sample_cat.jpg
```

(no `--dry-run`, Ollama not running)

**Expected**: clear `Ollama unavailable ...` error, exit code 1, post marked
`FAILED` with the message stored in `meta_error`.

**Proves**: FR-010 (clear failure + recorded reason); no crash, no half state.

## Scenario 3 — Lint + tests

```
uv run ruff check src main.py
uv run pytest        # once the test suite exists (added in implementation phase)
```

**Expected**: ruff clean; pytest green.

## Scenario 4 — Live publish (operator-configured)

1. Fill `config/config.local.yaml`: `ollama.*`, `telegram.bot_token`,
   `telegram.allowed_chat_ids`, `meta.access_token`, `meta.instagram_user_id`.
2. Start Ollama (`ollama serve`) and pull `qwen2-vl`, `qwen2.5`.
3. Post a photo to `data/input_media/`.
4. `uv run main.py --account cat_1 --media data/input_media/photo.jpg`
5. Approve via Telegram (or the CLI prompt fallback).

**Expected**: draft arrives on Telegram; after approval the container is
created, published, and the row reaches `PUBLISHED` with `ig_media_id`.
**Caution**: `meta.dry_run` defaults to `true`; set it `false` explicitly to
publish for real. Media must be reachable at a public URL (see
[meta-graph-api.md](contracts/meta-graph-api.md)).

## References

- Data model & status machine: [data-model.md](data-model.md)
- Local AI contract: [contracts/ollama-api.md](contracts/ollama-api.md)
- Publishing contract: [contracts/meta-graph-api.md](contracts/meta-graph-api.md)
- Approval contract: [contracts/telegram-protocol.md](contracts/telegram-protocol.md)