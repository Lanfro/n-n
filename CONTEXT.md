# System Context: Exotic Shorthair Instagram Growth Agent (`cat-agent-instagram`)

## 1. Project Overview & Scope
* **Project Name**: Exotic Shorthair Instagram Automation & Growth Engine
* **Goal**: Automate content analysis, caption generation, scheduling, and official API publishing to grow two Exotic Shorthair Instagram accounts from ~400 to 1,000+ followers in 6 months.
* **Target Audience**: Cat lovers, pet meme enthusiasts, Exotic Shorthair / Persian cat owners.
* **Core Design Philosophy**: **Local-First, Self-Hosted, Safe**. Uses local Vision/LLM models via Ollama and Opencode, combined with Meta's official Graph API to prevent account security flags or bans.

---

## 2. Technical Stack & Infrastructure
* **Language**: Python 3.11+
* **Agent Framework**: Opencode / Local Ollama instance
* **Vision Model**: `qwen3-vl:8b` (or `llava`) running locally via Ollama (`http://localhost:11434`)
* **Text LLM**: `qwen2.5` or `llama3.1` running locally via Ollama
* **External Integrations**:
  * **Meta Graph API (Instagram Creator API)**: For scheduling and publishing posts/Reels.
  * **Telegram Bot API**: Serving as the Human-in-the-Loop (HITL) approval gateway, plus a dedicated **vault bot** for the private cat-pictures archive channel.
* **Media hosting**: R2 (default) / S3 via boto3 (`endpoint_url` override + `public_base_url`), with a fail-loud `NoHost` (`file://`) fallback for local inspection.
* **Data Storage**: Local SQLite database (`data/pipeline.db`) to track post statuses (`PENDING_ANALYSIS`, `AWAITING_APPROVAL`, `APPROVED`, `PUBLISHED`, `FAILED`) and vault assets (`vault_media`, `channel_sync`).
* **Cat Pictures Vault**: a private Telegram channel is the durable archive / training corpus; `data/vault/<yyyymm>/<sha[:12]>.<ext>` is the content-addressed local mirror; media is deduplicated by sha256 and re-used across posts; the vault is the legal source for archive/training only — publish-time URLs always come from R2/S3.

---

## 3. Directory Structure

```text
cat-agent-instagram/
├── CONTEXT.md                  # This file (AI workspace instructions)
├── config/
│   ├── config.yaml             # API keys, endpoints, and environment settings
│   └── personas.json           # Persona prompts for Cat 1 and Cat 2
├── src/
│   ├── vision/
│   │   └── visual_analyzer.py  # Local vision model integration (Ollama Qwen2-VL)
│   ├── engine/
│   │   └── prompt_generator.py # Persona-driven prompt engineering & LLM generation
│   ├── approval/
│   │   ├── telegram_gateway.py # Telegram bot for human approval (/approve, /retry)
│   │   ├── sound.py            # HITL alert beep (winsound system or custom .wav)
│   │   └── notifier.py         # HITL Telegram push notification helper
│   ├── vault/
│   │   ├── media_vault.py      # Sha256 ingest + content-addressed local store
│   │   ├── telegram_archive.py # Channel archive (sendDocument) + idempotent sync
│   │   └── media_host.py       # R2/S3 publish-time media host (boto3)
│   ├── publisher/
│   │   └── meta_publisher.py   # Meta Graph API media container creation & publishing
│   └── database/
│       └── db_manager.py       # SQLite tracking for staged and published posts
├── data/
│   ├── input_media/            # Folder to drop new raw photos/videos
│   ├── vault/                  # Content-addressed local vault mirror (gitignored)
│   └── pipeline.db             # Local pipeline database
├── tests/
│   └── unit/                   # pytest suite (vault, archive, host, alert, DB)
├── pyproject.toml              # Python dependencies + dev tools (ruff, pytest)
└── main.py                     # Entry point for pipeline orchestration
```

---

## 4. Persona Rules & Brand Identity

### Account A: Cat 1 ("The Cynical Philosopher")
* **Role**: Intellectually superior, unbothered, mildly annoyed by human presence.
* **Tone**: Sarcastic, concise, witty, dry humor.
* **Themes**: Judging human work routines, food delivery delays, unrequested affection.
* **Caption Rules**:
  * Max 3 short sentences.
  * Reel Overlay: 3–7 words max (high-converting hook).
  * Hashes: 10–12 targeted hashtags (#exoticshorthair, #catlogic, #cynicalcat).

### Account B: Cat 2 ("The Dramatic Introvert")
* **Role**: Overwhelmed by existence, hyper-sensitive, melodramatic.
* **Tone**: Panicked, existential, emotional, hyper-dramatic.
* **Themes**: Fear of appliances (vacuums), existential dread, staring into space.
* **Caption Rules**:
  * Max 3 short sentences with expressive punctuation.
  * Reel Overlay: 3–7 words max (dramatic hook).
  * Hashes: 10–12 targeted hashtags (#exoticshorthair, #catdrama, #relatablecats).

---

## 5. Development Guidelines & Safety Rules

1. **Strict Non-Browser Automation Policy**: NEVER use browser scrapers, Selenium, Puppeteer, or unofficial Instagram API libraries (e.g., `instagram-private-api`). Use ONLY official Meta Graph API endpoints with valid access tokens.
2. **Human-in-the-Loop Safeguard**: No content must be published directly to Instagram without prior human approval via the Telegram Bot or CLI interface. When a human decision is pending, the operator is alerted via a sound + Telegram notification (`src/approval/sound.py`, `notifier.py`).
3. **Short Reel Hooks**: Keep on-screen Reel overlays under 8 words. On-screen text must trigger an immediate emotional reaction to encourage rewatching (looping).
4. **Local Resource Optimization**: Process images locally using base64 encoding to send payloads to the local Ollama REST API. Avoid sending media payloads to external paid services.
5. **Vault = archive/training only**: Cat pictures in the Telegram vault channel and `data/vault/` are a durable archive/training corpus. They are **never** used as publish-time URL sources — uploaded media URLs always come from R2/S3 (`src/vault/media_host.py`).
6. **Dedicated vault bot**: The vault uses its own bot token (separate from the approval bot) to avoid `getUpdates` conflicts; `--resolve-chat` resolves the private channel id after the operator adds the bot and posts one message.

---

## 6. Execution Workflow Pipeline

```text
[ Raw Photo/Video dropped in data/input_media ]
                      │
                      ▼
[ Step 0: vault ingest ] -> sha256, dedup, content-addressed copy in data/vault/
                      │
                      ▼
[ Step 1: visual_analyzer.py ] -> Ollama Qwen2-VL extracts scene description
                      │
                      ▼
[ Step 2: prompt_generator.py ] -> Matches cat persona & generates Reel text + Caption
                      │
                      ▼
[ Step 3: telegram_gateway.py ] -> Sends image + generated text to owner's Telegram
                      │         (sound + Telegram alert when human input needed)
        ┌─────────────┴─────────────┐
 [ /approve ]                 [ /retry ]
        │                           │
        ▼                           ▼
[ Step 4: meta_publisher.py ]  [ Regenerate via LLM ]
   Uploads media to R2/S3, then
   publishes via Meta API (image_url)

[ Channel sync (non dry-run) ] --sync-vault pulls operator-added channel
   pictures into the vault at the start of each real submit.
[ Archive ] -> each vault asset is uploaded to the private vault channel
   as a Document with a sha256 caption (best-effort unless required).
```