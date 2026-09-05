# Cat Agent Instagram

Self-hosted pipeline that turns raw cat photos into published Instagram posts —
with human approval in the loop and only official Meta Graph API calls.

It includes a **Cat Pictures Vault**: a private Telegram channel as the durable
archive/training corpus, a content-addressed local mirror (`data/vault/`), and
R2/S3 publish-time media hosting.

See `CONTEXT.md` and `plan/instagram_growth_ai_agent_plan.md` for the full spec.

## Quick start

```bash
uv sync
uv run main.py --account cat_1 --media data/input_media/photo.jpg --dry-run
```

`--dry-run` runs the whole pipeline (DB, vault count, approval, publisher)
without calling Ollama, Telegram, or Meta.

Vault helpers:

```bash
# Pull operator-added channel pictures into the local vault
uv run main.py --sync-vault

# Resolve the private channel's numeric id (run after posting one message)
uv run main.py --resolve-chat --vault-bot-token <BOT_TOKEN>
```

Lint/test with the dev group:

```bash
uv run ruff check src main.py tests
uv run pytest
```

## Config

Copy `config/config.yaml` to `config/config.local.yaml` and fill in:

- `ollama.base_url` + models (run `ollama serve` and `ollama pull qwen3-vl:8b qwen2.5`)
- `meta.access_token` / `meta.instagram_user_id` (Creator API)
- `telegram.bot_token` / `telegram.allowed_chat_ids` (+ `notify_chat_id`)
- `vault.telegram.bot_token` / `vault.telegram.chat_id` (dedicated vault bot)
- `vault.host.kind` (`r2`, `s3`, or `none`) + bucket/credentials/`public_base_url`

Config values in `config/config.local.yaml` override the defaults; the file is
gitignored.

## Safety

- No browser scrapers or unofficial IG libraries. Graph API only.
- Publishing requires explicit human approval (Telegram or CLI); a sound +
  Telegram alert fires when a human decision is pending.
- No content reaches Instagram without approval (`APPROVED` gate).
- Reel hooks are kept under 8 words by the persona rules.
- The vault (channel + `data/vault/`) is archive/training only; publish-time
  URLs come from R2/S3, never from the vault.