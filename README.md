# Cat Agent Instagram

Self-hosted pipeline that turns raw cat photos into published Instagram posts —
with human approval in the loop and only official Meta Graph API calls.

See `CONTEXT.md` and `plan/instagram_growth_ai_agent_plan.md` for the full spec.

## Quick start

```bash
pip install -r requirements.txt
python main.py --account cat_1 --media data/input_media/photo.jpg --dry-run
```

`--dry-run` runs the whole pipeline (DB, approval, publisher) without calling
Ollama, Telegram, or Meta.

## Config

Copy `config/config.yaml` to `config/config.local.yaml` and fill in:

- `ollama.base_url` + models (run `ollama serve` and `ollama pull qwen2-vl`)
- `meta.access_token` / `meta.instagram_user_id` (Creator API)
- `telegram.bot_token` / `telegram.allowed_chat_ids`

Config values in `config/config.local.yaml` override the defaults; the file is
gitignored.

## Safety

- No browser scrapers or unofficial IG libraries. Graph API only.
- Publishing requires explicit human approval (Telegram or CLI).
- Reel hooks are kept under 8 words by the persona rules.