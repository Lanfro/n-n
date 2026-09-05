<!--
SYNC IMPACT REPORT
Version change: (initial) -> 1.0.0
Modified principles: none (initial ratification)
Added sections: Core Principles, Security & Credential Handling, Development Workflow & Quality Gates, Governance
Removed sections: none
Follow-up TODOs: none
-->

# cat-agent-instagram Constitution

## Core Principles

### I. Official-API-Only (NON-NEGOTIABLE)

Every interaction with Instagram MUST go through Meta's official Graph API with a
valid access token. Browser scrapers, Selenium, Puppeteer, and unofficial
Instagram libraries (e.g. `instagram-private-api`) are strictly forbidden.
Rationale: unauthorized automation triggers permanent account bans; the two
brand accounts are irreplaceable assets.

### II. Human-in-the-Loop Approval (NON-NEGOTIABLE)

No content MAY be published to Instagram without prior human approval through
the Telegram gateway or CLI fallback. The publisher MUST reject any attempt to
publish a post not in the `APPROVED` state. Rationale: brand voice is the
differentiator; the algorithm punishes irrelevant or off-persona content.

### III. Local-First Processing

Vision analysis and text generation MUST run on the local Ollama instance;
media MUST be passed to local endpoints as base64 payloads. Uploads to paid
external services for analysis are forbidden. Rationale: zero-subscription cost
model and no leakage of branded content to third parties.

### IV. Short-Form Hook Discipline

On-screen Reel text MUST stay under 8 words and land within the first frame to
drive immediate emotional response and looping. Captions MUST respect the
persona rules (max 3 sentences; 10-12 targeted hashtags). Rationale: retention
loops and high save/share rates are the primary growth lever for follower
acquisition.

### V. Persona & Brand Consistency

Every post MUST be generated through one of the curated personas
(Cat 1 Cynical Philosopher / Cat 2 Dramatic Introvert) read from
`config/personas.json`. Generated text MUST follow the persona's tone and
themes. Rationale: differentiated archetypes split reach across audience
segments instead of competing with each other.

## Security & Credential Handling

- `meta.access_token`, `telegram.bot_token`, and chat IDs MUST live only in
  `config/config.local.yaml` or environment variables, never in versioned files.
- Secrets, local overrides, and the pipeline database MUST remain
  gitignored; teams MUST rotate tokens on suspected exposure.
- Logs MUST NOT contain tokens, media payloads, or private scene descriptions
  beyond what is needed to diagnose failures.

## Development Workflow & Quality Gates

- Tooling is managed with `uv`: `uv run main.py ...` to run, `uv run ruff check`
  to lint, `uv run pytest` to test.
- The pipeline status machine (PENDING_ANALYSIS -> AWAITING_APPROVAL ->
  APPROVED -> PUBLISHED/FAILED) MUST be the single source of truth for what may
  be published next.
- The Meta publisher MUST default to `dry_run: true`; live publishing requires
  an explicit operator override plus human approval.
- Every change MUST pass `uv run ruff check` and the dry-run smoke test
  (`--dry-run`) before merge.

## Governance

- This constitution supersedes ad-hoc practices and MUST be kept in sync with
  `CONTEXT.md` and the growth plan under `plan/`.
- Amendments require documentation in the Sync Impact Report, a semantic
  version bump, and are effective on the date recorded as Last Amended.
- Pull requests MUST verify compliance with the five principles and the quality
  gates above; complexity without a principle-backed justification is rejected.
- In-code runtime guidance is defined in `CONTEXT.md`, which serves as the
  reference for day-to-day development decisions.

**Version**: 1.0.0 | **Ratified**: 2026-09-05 | **Last Amended**: 2026-09-05