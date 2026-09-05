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

## 8. Media vault & Telegram archive

- **Decision**: All cat pictures live in a private Telegram channel (archive +
  training corpus), mirrored locally in a content-addressed vault
  (`data/vault/<yyyymm>/<sha[:12]>.jpg`) whose SQLite index is the authority.
  Archive uploads use `sendDocument` with a `sha256:<hash>` caption so originals
  are byte-preserved (sendPhoto recompresses/strips EXIF). A dedicated vault bot
  token is used.
- **Rationale**: Free, phone-accessible, durable off-machine backup that the
  operator can also add to by hand; content-addressing gives exact dedup;
  keeping a local index preserves Local-First and makes the corpus queryable.
- **Alternatives considered**: raw folders only (no off-machine backup),
  sendPhoto (recompresses — rejected), git LFS for media (overkill at this
  scale), using the approval bot token (risks Telegram 409 `getUpdates`
  conflicts — rejected).

## 9. Live-publish media hosting (R2/S3)

- **Decision**: The vault exposes a `MediaHost` interface; the default backend
  is Cloudflare R2 via `boto3` with an `endpoint_url` override and a public
  base URL; generic S3 buckets are supported; a `NoHost` backend keeps the
  current fail-loud `file://` behavior when nothing is configured.
- **Rationale**: Meta must fetch the image from a public URL (Graph API
  `image_url`); R2 has zero egress fees for the upload pattern and is
  S3-compatible, so one boto3 code path covers R2/MinIO/S3.
- **Alternatives considered**: Telegram-hosted URLs (require the bot token —
  leaking it to Meta; rejected), local web server + tunnel (extra moving parts).
- **Constraint**: the vault/Telegram channel is never the publish URL source.

## 10. Vault channel sync (operator hand-adds)

- **Decision**: `--sync-vault` performs a one-shot `getUpdates` poll for
  `channel_post` media, deduplicated by an offset cursor persisted in
  `channel_sync`; new pictures are downloaded and ingested as `source=telegram`.
  Sync also auto-runs at the start of every submit.
- **Rationale**: The operator adds pictures by hand to teach the model; polling
  with a persisted offset is idempotent and needs no long-running server.
- **Alternatives considered**: webhook (requires public URL + TLS — overkill);
  scheduled background service (not wanted yet).