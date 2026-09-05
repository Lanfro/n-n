# AGENTS.md — Agent Session Guide

Loaded automatically on every new session alongside `CONTEXT.md`. Follow these
rules without being asked.

## Session start (read in order)

1. `prompt.md` — the active instruction file. Re-read it at the start of every
   task; it may have changed between sessions.
2. `CONTEXT.md` — project architecture, scope, safety rules (auto-injected).
3. `.specify/memory/constitution.md` — non-negotiable principles and quality gates.
4. `specs/001-instagram-growth-pipeline/` — current feature artifacts (spec,
   plan, tasks, contracts).

## Standing rules

- Commit + push to `main` directly after **every completed workflow step**
  (no PR, no approval). User-set rule.
- Quality gates before merge: `uv run ruff check`, the dry-run smoke test
  `uv run main.py --account cat_1 --media data/input_media/sample_cat.jpg
  --dry-run`, and `uv run pytest` once the suite exists.
- All runs use `uv run ...` (uv-managed environment).
- Never log or commit secrets; credentials live only in the gitignored
  `config/config.local.yaml`.
- Official Meta Graph API only — no browser automation or unofficial IG
  libraries.
- Nothing publishes without human approval (status-machine `APPROVED` gate).
- When a human decision is required: play the alert sound and send the
  Telegram notification (see `src/approval/`), then wait for input.
- Cat pictures live in the Telegram vault channel (private) + local
  `data/vault/`; the vault is archive/training-corpus only, never the publish
  URL source (that is R2/S3 via `src/vault/media_host.py`).

## Environment notes (Windows / PowerShell)

- `.ps1` scripts must be run with
  `powershell -NoProfile -ExecutionPolicy Bypass -File <script>`.
- `git push` works via Git Credential Manager; `gh auth status` may report
  not-logged-in (irrelevant, plain `git` is preferred).
- specify-cli install tag is `v1.0.4` (with the `v` prefix).