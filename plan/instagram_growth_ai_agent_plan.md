# Instagram Growth Strategy & Local AI Agent Implementation Plan
## Executive Summary & Goal
* **Target Audience/Subject**: Two Exotic Shorthair cats (separate Instagram accounts).
* **Current Baseline**: ~400 followers per account.
* **Primary Target**: 1,000 followers per account within 6 months.
* **Core Advantage**: Local software engineering skills, utilizing **opencode** and local AI models to build a fully automated, self-hosted, zero-subscription content pipeline.

---

## 1. Social Media Brand Strategy & Content Positioning

Exotic Shorthairs possess distinct visual traits—flat faces, wide circular eyes, and deadpan or dramatic expressions. This inherent comic value is your primary growth leverage.

### 1.1 Account Persona Differentiation
To maximize reach and engage different audience segments, each cat should represent a distinct character archetype:

* **Cat 1 Archetype: "The Cynical Philosopher"**
  * **Tone**: Sarcastic, dry humor, unbothered, intellectual superiority over human behavior.
  * **Angle**: Judging human routines, work-from-home habits, and late food deliveries.
* **Cat 2 Archetype: "The Dramatic Introvert"**
  * **Tone**: Overwhelmed, sensitive, melodramatic, highly existential.
  * **Angle**: Overreacting to minor household events (e.g., the vacuum cleaner, a fly in the room, unexpected guests).

### 1.2 Content Matrix & Formats
Instagram's algorithm rewards specific content types differently depending on your growth objectives:

| Content Format | Share of Volume | Primary Strategic Function | Algorithm Mechanism |
| :--- | :--- | :--- | :--- |
| **Short-form Reels** | **70%** | **Follower Acquisition** | Distributed primarily to **non-followers** via the Explore and Reels feed. |
| **Carousels** | **20%** | **Nurturing & Shares** | Shown to existing followers and high-intent non-followers; high save/share rate. |
| **Stories** | **10%** | **Retention & Loyalty** | Maintains active engagement with existing followers. |

---

## 2. Technical Architecture of the Local AI Pipeline

The pipeline connects local image processing, local LLM text generation, human validation, and official Instagram publishing.

```
[ Local Image / Video Repository ]
               │
               ▼
   [ 1. Local Vision Model ]  (Ollama: Qwen2-VL / LLaVA)
               │  Extracted visual scene context & emotion
               ▼
    [ 2. Opencode Core Agent ]  (Persona System Prompts)
               │  Generates Reel text overlay, Caption, Hashtags
               ▼
 [ 3. Human-in-the-Loop Gateway ]  (Telegram Bot / CLI Approval)
               │  Approved / Rejected / Regenerate
               ▼
   [ 4. Meta Graph API Publisher ]  (Official Instagram Creator API)
```

### 2.1 System Components

1. **Multimodal Vision Analyzer (Local Ollama)**:
   * Uses local vision models (e.g., `qwen2-vl` or `llava`) running on Ollama.
   * Analyzes photo/video frames to extract pose, facial expression, background details, and lighting.
2. **Opencode Agent Engine**:
   * Takes the vision output and processes it through persona-specific system prompts.
   * Output structure:
     * **On-Screen Reel Text**: Short, punchy hook (3–7 words).
     * **Caption**: Narrative aligned with the cat's personality.
     * **Hashtag Set**: 10–15 targeted niche hashtags (#exoticshorthair, #catsofinstagram, #persiancat).
3. **Human-in-the-Loop (HITL) Interface**:
   * Sends draft posts to a Telegram Bot or CLI interface.
   * Provides quick action buttons: `[Approve & Schedule]`, `[Regenerate Caption]`, `[Discard]`.
4. **Publisher Module (Meta Graph API)**:
   * Uses official Instagram Graph APIs (via Creator or Business account).
   * Automatically schedules and publishes approved media.

---

## 3. Six-Month Implementation Roadmap

### Phase 1: Infrastructure & API Setup (Month 1)
* **Objective**: Establish the technical pipeline and convert Instagram accounts.
* **Tasks**:
  * Convert both Instagram accounts to **Creator Accounts** (free).
  * Connect Instagram accounts to a Facebook Page to obtain Meta Graph API access keys.
  * Set up Ollama with a local vision model (`qwen2-vl`).
  * Implement the Opencode agent orchestration script and Telegram approval bot.

### Phase 2: Consistency & Algorithmic Baseline (Months 2–3)
* **Objective**: Train the Instagram algorithm on your content niche.
* **Tasks**:
  * Publish **4–5 Reels per week** and **1 Carousel per week** per account.
  * Utilize 5–8 second Reels with looping text and trending audio.
  * Maintain strict posting consistency using scheduled API calls.

### Phase 3: Optimization, Analytics & Scaling (Months 4–6)
* **Objective**: Double down on high-performing formats to hit the 1,000-follower mark.
* **Tasks**:
  * Implement local analytics tracking via Meta API to evaluate top-performing posts.
  * Refine system prompts based on post engagement data.
  * Execute daily organic interaction routines (commenting on niche accounts).

---

## 4. Workflow Code Templates & Prompts

### 4.1 Vision & Persona Prompting Structure

```text
SYSTEM PROMPT (Cat 1 - Cynical Philosopher):
You are an Exotic Shorthair cat named [Cat Name]. You view humans as well-meaning but incompetent servants.
Your tone is intellectual, sarcastic, concise, and dry.

INPUT VISUAL CONTEXT:
"{vision_model_output}"

TASK:
Generate Instagram post metadata:
1. REEL_TEXT: On-screen text (max 8 words, relatable/cynical hook).
2. CAPTION: 2-3 sentences explaining your cynical reaction to the scene.
3. HASHTAGS: 12 niche-relevant hashtags.
```

### 4.2 Python Automation Script Skeleton

```python
import os
import requests
import json

# Local Ollama Endpoint
OLLAMA_URL = "http://localhost:11434/api/generate"

def analyze_image_with_vision(image_path):
    # Call local Ollama Vision model
    payload = {
        "model": "qwen2-vl",
        "prompt": "Describe this cat's expression, posture, and surroundings in detail.",
        "images": [encode_image_to_base64(image_path)],
        "stream": False
    }
    response = requests.post(OLLAMA_URL, json=payload)
    return response.json().get("response", "")

def generate_instagram_content(visual_description, persona_prompt):
    full_prompt = f"{persona_prompt}\n\nVisual Scene: {visual_description}"
    payload = {
        "model": "llama3.1",
        "prompt": full_prompt,
        "stream": False
    }
    response = requests.post(OLLAMA_URL, json=payload)
    return response.json().get("response", "")

# Meta Graph API Publisher Function Placeholder
def publish_to_instagram(container_id, access_token):
    # Official Meta Graph API endpoint for publishing media
    pass
```

---

## 5. Community Growth Rules & Safety Protocols

1. **Avoid Automation Violations**: Never use unauthorized web scrapers, browser automation (Selenium/Puppeteer), or unofficial login APIs. Instagram actively bans accounts using unauthorized automation. Stick strictly to Meta's official Graph API.
2. **Reels Engagement Loop**:
   * Keep video overlays under 7 words to encourage multiple video rewatches (increasing retention rate).
   * Ensure the first frame presents a clear, high-contrast expression of the cat.
3. **Daily Organic Engagement**:
   * Spend 10 minutes daily interacting with other Exotic Shorthair / Persian cat accounts.
   * Respond to all comments on your posts within the first 30 minutes of publishing.