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
* **Vision Model**: `qwen2-vl` (or `llava`) running locally via Ollama (`http://localhost:11434`)
* **Text LLM**: `qwen2.5` or `llama3.1` running locally via Ollama
* **External Integrations**:
  * **Meta Graph API (Instagram Creator API)**: For scheduling and publishing posts/Reels.
  * **Telegram Bot API**: Serving as the Human-in-the-Loop (HITL) approval gateway.
* **Data Storage**: Local SQLite database (`data/pipeline.db`) to track post statuses (`PENDING_ANALYSIS`, `AWAITING_APPROVAL`, `APPROVED`, `PUBLISHED`, `FAILED`).

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
│   │   └── telegram_gateway.py # Telegram bot for human approval (/approve, /retry)
│   ├── publisher/
│   │   └── meta_publisher.py   # Meta Graph API media container creation & publishing
│   └── database/
│       └── db_manager.py       # SQLite tracking for staged and published posts
├── data/
│   ├── input_media/            # Folder to drop new raw photos/videos
│   └── pipeline.db             # Local pipeline database
├── requirements.txt            # Python dependencies
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
2. **Human-in-the-Loop Safeguard**: No content must be published directly to Instagram without prior human approval via the Telegram Bot or CLI interface.
3. **Short Reel Hooks**: Keep on-screen Reel overlays under 8 words. On-screen text must trigger an immediate emotional reaction to encourage rewatching (looping).
4. **Local Resource Optimization**: Process images locally using base64 encoding to send payloads to the local Ollama REST API. Avoid sending media payloads to external paid services.

---

## 6. Execution Workflow Pipeline

```text
[ Raw Photo/Video dropped in data/input_media ]
                      │
                      ▼
[ Step 1: visual_analyzer.py ] -> Ollama Qwen2-VL extracts scene description
                      │
                      ▼
[ Step 2: prompt_generator.py ] -> Matches cat persona & generates Reel text + Caption
                      │
                      ▼
[ Step 3: telegram_gateway.py ] -> Sends image + generated text to owner's Telegram
                      │
        ┌─────────────┴─────────────┐
 [ /approve ]                 [ /retry ]
        │                           │
        ▼                           ▼
[ Step 4: meta_publisher.py ]  [ Regenerate via LLM ]
   Publishes via Meta API
```