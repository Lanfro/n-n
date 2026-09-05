# Implementation Plan: Instagram Growth Pipeline

**Branch**: `001-instagram-growth-pipeline` | **Date**: 2026-09-05 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-instagram-growth-pipeline/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command; its definition describes the execution workflow.

## Summary

The pipeline turns raw cat photos into on-brand, human-approved Instagram posts.
An operator drops a photo (`data/input_media/`), the system derives a
description of the scene using a local vision model, generates voice-matched
draft text (short hook + caption + hashtags) from half the two curated personas,
sends the draft to the operator via Telegram (with a CLI fallback) for
approve/regenerate/discard, and finally publishes only approved posts through
the official Instagram Graph API. A SQLite-backed status machine guarantees no
post reaches the public without a recorded human approval.

The scaffolded codebase already delivers this design; the plan's artifacts
formalize the contracts and data model so implementation can be verified and
completed milestone-by-milestone.

## Technical Context

**Language/Version**: Python >= 3.11 (3.14.5 in the working environment)

**Primary Dependencies**: requests, PyYAML, Pillow, python-telegram-bot
(runtime); ruff, pytest (dev); uv (tooling)

**Storage**: SQLite — `data/pipeline.db` (WAL mode)

**Testing**: pytest (unit + integration), ruff lint; dry-run smoke test as a
CI-style gate

**Target Platform**: Local desktop (Windows/macOS/Linux), Ollama at
`localhost:11434`; no cloud runtime

**Project Type**: CLI tool + set of local microservices (vision, generator,
approval gateway, publisher)

**Performance Goals**: Operator-visible parity — submit to ready draft in
< 10 minutes; publish round-trip in < 60 seconds after approval

**Constraints**: Official Instagram Graph API only (no browser automation or
unofficial libraries); human approval required pre-publish; local-first media
processing (base64 to local Ollama); on-screen hooks under 8 words; persona
caption/limit rules enforced; zero subscription cost

**Scale/Scope**: 2 accounts, ~4-5 image/video posts per week per account;
10-30 posts/week peak; trivial DB volume

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Official-API-Only (non-negotiable)**: PASS by design — publisher uses only
  the Meta Graph API; no scraping libraries in the dependency set.
- **Human-in-the-Loop (non-negotiable)**: PASS by design — publish path is
  gated on `APPROVED` state which only the approval gateway can set.
- **Local-First Processing**: PASS by design — vision + text generation use the
  local Ollama endpoint only; media stays on local disk.
- **Short-Form Hook Discipline**: PASS by design — persona rules cap reel text
  at 7 words and captions at 3 sentences.
- **Persona & Brand Consistency**: PASS by design — generation reads strict
  personas from `config/personas.json`; no free-form voice.
- **Security & Credential Handling**: PASS by design — credentials live only in
  `config/config.local.yaml`; secrets gitignored; logs exclude tokens.
- **Quality Gates (uv, ruff, pytest, dry-run default)**: PASS by design —
  `uv run ruff check` clean; smoke test reaches `PUBLISHED`; publisher defaults
  to dry-run.

## Project Structure

### Documentation (this feature)

```text
specs/001-instagram-growth-pipeline/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/
├── approval/            # Telegram gateway (+ CLI fallback), alerts (sound + notify)
├── database/            # SQLite DB wrapper + status machine + vault tables
├── engine/              # Persona store + prompt/generator
├── publisher/           # Meta Graph API container creation/publish
├── vault/               # media_vault (ingest/dedup), telegram_archive, media_host (R2/S3)
└── vision/              # Ollama vision analyzer
config/
├── config.yaml          # Defaults (secrets blank), seeded by config.local.yaml
└── personas.json        # Cat 1 / Cat 2 persona definitions
data/
├── input_media/         # Operator drop folder
└── vault/               # Content-addressed media archive (<yyyymm>/<sha[:12]>.ext)
main.py                  # CLI orchestrator (--dry-run, --sync-vault, --resolve-chat)
tests/                   # Added in implementation phase (pytest)
```

**Configuration additions**: a `vault:` block (`root`, `telegram.*`, `host.*`)
and an `approval:` block (`sound`, `sound_file`, `notify_telegram`) in
`config/config.yaml`, with real values only in gitignored `config.local.yaml`.

**Human-in-the-loop alerting**: whenever a decision is required, the pipeline
plays a sound (`winsound`, optional custom `.wav`) and sends a Telegram
notification message before blocking, so the operator is pinged when "around".

**Structure Decision**: Single-project layout mirroring the existing scaffold —
one `src/` package with focused submodules per pipeline stage, a thin CLI
orchestrator in `main.py`, and a `config/` split that keeps secrets out of
version control. This fits the existing codebase; no mono-repo or multi-app
structure is needed at this scale.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No constitution violations — complexity tracking not required.