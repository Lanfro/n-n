# AGENTS.md — Agent Session Guide

Loaded automatically on every new session alongside `CONTEXT.md`. Follow these
rules without being asked.

## Session start (read in order)

1. `prompt.md` — the active instruction file. Re-read it at the start of every
   task; it may have changed between sessions.
2. `CONTEXT.md` — project architecture, scope, safety rules (auto-injected).
3. `.specify/memory/constitution.md` — non-negotiable principles and quality gates.
4. `specs/001-instagram-growth-pipeline/` — photo pipeline artifacts (spec,
   plan, tasks, contracts).
5. `specs/002-video-reel-pipeline/` — the active AI Video Reel feature.

## Standing rules

- Commit + push to `main` directly after **every completed workflow step**
  (no PR, no approval). User-set rule.
- Quality gates before merge: `uv run ruff check`, the dry-run smoke test
  `uv run main.py --account cat_1 --media data/input_media/sample_cat.jpg
  --dry-run`, and `uv run pytest`.
- All runs use `uv run ...` (uv-managed environment).
- Never log or commit secrets; credentials live only in the gitignored
  `config/config.local.yaml`.
- Official Meta Graph API only — no browser automation or unofficial IG
  libraries **against Instagram**. The one browser exception is the assisted
  image-to-video step (`src/reel/producer.py`, Playwright) which targets a
  free **external** service (PixVerse), never Instagram.
- Nothing publishes without human approval (status-machine `APPROVED` gate).
- When a human decision is required: play the alert sound and send the
  Telegram notification (see `src/approval/`), then wait for input.
- Cat pictures live in the Telegram vault channel (private) + local
  `data/vault/`; the vault is archive/training-corpus only, never the publish
  URL source (that is R2/S3 via `src/vault/media_host.py`).
- The static image "digital twin" generator is retired (`src/generator/` and
  `.venv-gen/` deleted). Reel production uses the `--reel-*` commands:
  `--reel-plan --set <key>`, `--reel-produce --set <key>`,
  `--reel-validate --set <key> --brief <id>`,
  `--reel-publish --set <key> --brief <id> [--dry-run]`.
- Clip validation/repair needs `ffmpeg`/`ffprobe` on `PATH`; assisted
  production needs `uv run playwright install chromium` and a one-time manual
  login to the video service in the persistent profile.

## Environment notes (Windows / PowerShell)

- `.ps1` scripts must be run with
  `powershell -NoProfile -ExecutionPolicy Bypass -File <script>`.
- `git push` works via Git Credential Manager; `gh auth status` may report
  not-logged-in (irrelevant, plain `git` is preferred).
- specify-cli install tag is `v1.0.4` (with the `v` prefix).