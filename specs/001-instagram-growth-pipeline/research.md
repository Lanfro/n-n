# Research & Technical Decisions — Instagram Growth Pipeline

Phase 0 output of `/speckit.plan`. All uncertainties resolved from project
context, industry best practice, and platform requirements. No
`NEEDS CLARIFICATION` items remain.

## 1. Local vision model selection

- **Decision**: Use `qwen2-vl` for scene/emotion description, with `llava` as
  the documented fallback model.
- **Rationale**: Context requires a local vision model capable of reading a
  photo and describing pose, facial expression, background, and lighting for
  caption creative. `qwen2-vl` is the configured default (`config.yaml`), runs
  fully offline via Ollama, and matches the ecosystem already chosen.
- **Alternatives considered**: `llava` (smaller, weaker fidelity — acceptable
  fallback only); hosted vision APIs (rejected — violates Local-First and
  zero-subscription principles).

## 2. Local text generation model

- **Decision**: Use `qwen2.5` for persona-driven caption generation (fallback
  `llama3.1`).
- **Rationale**: Persona prompts need consistent, structured JSON output
  (reel text, caption, hashtags). A local model keeps generation free and
  private. The persona prompt + strict "output ONLY JSON" instruction is the
  control mechanism for quality.
- **Alternatives considered**: `llama3.1` — heavier, used as model fallback
  since 2025-era hardware may run it comfortably; hosted LLM APIs — rejected
  (cost + content leakage).

## 3. Publishing channel (safety-critical)

- **Decision**: Publish only through the official Instagram Graph API media
  container flow: `POST /{ig-user}/media` then `POST /{ig-user}/media_publish`.
- **Rationale**: This is the only Meta-sanctioned automation channel for
  Creator/Business accounts. It preserves the account safety goal (zero bans)
  and satisfies the Official-API-Only constitution principle.
- **Alternatives considered**: Selenium/Puppeteer and `instagram-private-api` —
  explicitly rejected and permanently out of scope (ban risk).
- **Constraint**: Media must be reachable by Meta at a public URL to create a
  container. The scaffold uses a `file://` URI placeholder that fails loudly
  unless the operator hosts media; dry-run remains the safe default.

## 4. Human-in-the-loop approval channel

- **Decision**: Telegram bot as the primary approval surface (send draft photo
  + caption, inline buttons for approve / regenerate / discard); CLI prompt as
  an automatic fallback when the bot token or approved chat ids are missing.
- **Rationale**: Telegram gives rapid mobile approval from anywhere, matching
  the operator's need to review before posting. The CLI fallback keeps the
  system usable and testable without credentials.
- **Alternatives considered**: approval via Meta Business Suite (no API for
  this flow), email (too slow), unattended auto-publish (forbidden by
  constitution).

## 5. Post lifecycle storage

- **Decision**: Single SQLite `posts` table with an explicit, validated status
  machine: `PENDING_ANALYSIS -> AWAITING_APPROVAL -> APPROVED -> PUBLISHED`,
  plus `FAILED`/`REJECTED`, and legal retry edges.
- **Rationale**: Local-first data, zero infra, transactional integrity via a
  single writer connection, and machine-enforced transitions so an unapproved
  post can never be published.
- **Alternatives considered**: Postgres (overkill for 10-30 rows/week);
  JSON files (no integrity/transition enforcement).

## 6. Dependency & quality tooling

- **Decision**: `uv` for environment/lock management (`uv sync`, `uv.lock`);
  `ruff` for lint; `pytest` for tests; publisher defaults to `dry_run: true`.
- **Rationale**: Reproducible environments with minimal friction on a local
  desktop; lint gate keeps the codebase green; dry-run default makes
  accidental publishing impossible.
- **Alternatives considered**: pip + requirements.txt (superseded — uv
  migration completed), manual venv (not reproducible).

## 7. Failure handling & retry semantics

- **Decision**: Any stage failure marks the post `FAILED` with a stored human
  readable error; `FAILED -> PENDING_ANALYSIS` is a legal transition so retry
  reuses the post row without duplicating work.
- **Rationale**: SC-006 requires retry without rework; storing the error in
  the row gives the operator a clear reason (FR-010).
- **Alternatives considered**: silent retry loops (rejected — masks failures);
  throwing fatal errors (rejected — poor operator experience).