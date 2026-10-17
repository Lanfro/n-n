# Implementation Plan: AI Video Reel Pipeline from Real Cat Photos

**Branch**: `002-video-reel-pipeline` | **Date**: 2026-10-17 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-video-reel-pipeline/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command; its definition describes the execution workflow.

## Summary

Replace the retired static "digital twin" image generator with an AI **video
Reel** pipeline. For each requested shot of a creative set the system builds a
self-contained **Reel brief** that pairs a real, labelled cat photo from the
vault with a motion prompt, an expected output filename, and persona-compliant
caption/hook/hashtags. An assisted, semi-automatic **browser producer** drives an
already-authenticated session on a free third-party image-to-video service
(PixVerse first) to animate the photo into a vertical 9:16 clip and downloads it;
if a login/anti-bot challenge or the free daily allowance blocks it, the operator
is alerted and may drop the clip in by hand. Every clip is validated against
Instagram's Reel requirements (repairing only audio/resolution), then passed
through the existing human-approval gate and published as a Reel exclusively via
the official Meta Graph API (`media_type=REELS`, `video_url`, status polling).
Post state, approval and retry reuse the existing status machine; the static SD
generator, its venv and its CLI flag are removed.

## Technical Context

**Language/Version**: Python >= 3.11 (3.14.5 in the working environment)

**Primary Dependencies**: existing — requests, PyYAML, Pillow, python-telegram-bot, boto3; **new** — `playwright` (browser driver for the external video service). External binary (not a Python dep, invoked via subprocess): `ffmpeg`/`ffprobe` for clip validation, audio mux and upscaling.

**Storage**: SQLite — `data/pipeline.db` (reused `posts` table + additive columns for reel posts); filesystem — `data/generated/<set>/` for briefs and clips; browser profile state on disk (gitignored).

**Testing**: pytest (unit + integration, mocked HTTP/browser/subprocess), ruff lint; existing dry-run smoke test as a CI-style gate.

**Target Platform**: Local desktop (Windows/macOS/Linux), 8 GB RAM; Ollama at `localhost:11434` (text planning only); a headed Chromium (Playwright) for the external video service; no cloud runtime for the pipeline itself.

**Project Type**: CLI tool + local module set (planner, browser producer, validator, publisher, approval gateway).

**Performance Goals**: Planning a full set of briefs < 3 minutes of system activity (SC-004); per-clip production dominated by the external service (typically tens of seconds to a few minutes on free tier); validate→approval→publish round-trip < 2 minutes after approval, excluding platform video processing.

**Constraints**: Instagram interactions only via the official Graph API (no browser automation against Instagram); human approval mandatory before publish; text planning stays on local Ollama; assisted browser automation runs **only against the free third-party video service**; free-tier output (lower resolution, possible watermark) accepted; single vertical clip per Reel (no multi-clip stitching); free daily allowance caps throughput to roughly 1–2 clips per account per day.

**Scale/Scope**: 2 accounts; target pacing ~4–5 short videos per week per account (per the growth plan); trivial DB volume; a handful of briefs/clips per set.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Official-API-Only (non-negotiable)**: PASS — Instagram is touched only by the
  Meta Graph API Reels flow. The Playwright browser driver targets a *third-party
  video service*, never Instagram; no Instagram scraping library is introduced.
- **Human-in-the-Loop (non-negotiable)**: PASS — the reel publish path reuses the
  `APPROVED` gate; a clip cannot be published without a recorded approval, and the
  approval message now carries the video preview.
- **Local-First Processing**: PASS with documented nuance (see Complexity
  Tracking) — the principle forbids *uploads to paid external services for
  analysis*. This feature uploads cat photos to a *free* external service for
  *generation*, with the operator explicitly in the loop. No paid subscription is
  introduced and no analysis/description work is offloaded.
- **Short-Form Hook Discipline**: PASS — the brief reuses the persona `reel_rules`
  (hook < 8 words) and `caption_rules` (≤ 3 sentences, 10–12 hashtags) that the
  photo pipeline already enforces.
- **Persona & Brand Consistency**: PASS — briefs are generated per persona from
  `config/personas.json`; no free-form voice is added.
- **Security & Credential Handling**: PASS — credentials stay in
  `config.local.yaml`; the Playwright browser profile directory and downloaded
  clips are gitignored; logs exclude tokens and full media payloads.
- **Quality Gates (uv, ruff, pytest, dry-run default)**: PASS — the feature keeps
  `uv run ruff check`, `uv run pytest` and the `--dry-run` smoke test green; the
  publisher still defaults to `dry_run: true`.

## Project Structure

### Documentation (this feature)

```text
specs/002-video-reel-pipeline/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
│   ├── reel-brief.md
│   ├── video-service-browser.md
│   ├── reel-clip-requirements.md
│   └── meta-graph-api-reels.md
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/
├── approval/            # Telegram gateway (+ CLI fallback), alerts — extend to send video previews
├── database/            # SQLite wrapper + status machine — additive reel columns + migration
├── engine/              # Persona store + prompt generator + scene_writer (reused for motion briefs)
├── publisher/           # Meta Graph API — add Reels container + status polling
├── reel/                # NEW: brief builder, Playwright producer, selectors, clip validator
│   ├── brief.py         #   scene plan + vault anchors -> ReelBrief (JSON handoff)
│   ├── producer.py      #   assisted browser producer (session, generate, download, fallback)
│   ├── selectors.py     #   external-service selectors isolated in one place (repairable)
│   └── validator.py     #   ffprobe validation + ffmpeg repair (silent audio, upscale)
├── vault/               # media_vault, telegram_archive, media_host (reused; hosts .mp4 as-is)
└── vision/              # Ollama vision analyzer (unchanged; not used by reels)
config/
├── config.yaml          # Add a `reel:` block; retire `creative.*` render keys
├── personas.json        # Reused (hook/caption/hashtag rules)
└── creative/sets.json   # Extended with per-scene motion prompts (or a reel-scenes variant)
data/
├── vault/               # Reused source photos (real cat photos, subject-labelled)
├── generated/<set>/     # briefs (reel_brief_NN.json) + clips (reel_NN.mp4) + manifest
└── .browser/            # NEW: persistent Playwright profile state (gitignored)
main.py                  # CLI: add --reel-plan/--reel-produce/--reel-publish; remove --generate-set
tests/                   # pytest unit + integration for the new modules
```

**Removed**: `src/generator/` (SD1.5 renderer + anchors + requirements), `.venv-gen/`,
the `--generate-set`/`--set-shots` CLI flags, and the `creative.gen_env_python`/
`generator_script`/`model`/`steps` config keys. `src/engine/scene_writer.py` and
`config/creative/sets.json` are **kept** (reused for motion planning).

**Configuration additions**: a `reel:` block in `config/config.yaml` (service
name/URL, browser profile dir, out root, aspect, duration, min/target resolution,
max file size, headed/headless, selectors path). A parallel `config/reel/selectors.json`
holds the external-service selectors so UI drift is repairable without touching code.

**Structure Decision**: Single-project layout consistent with feature 001 — one
`src/` package with a focused `src/reel/` submodule for the new stage, a thin CLI
orchestrator, and a config split that keeps secrets and the browser profile out of
version control. The vault's existing `MediaHost` already hosts `.mp4` files
unchanged, and the existing status machine and approval gateway are reused, so no
new infastructural layer is needed.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Third-party (free) external service for AI video generation, and assisted browser automation against it | Local video generation is infeasible on 8 GB RAM CPU-only; a free image-to-video service is the only $0 path, and its only interface is a browser UI | (a) Local open-weights video models (e.g. Wan 2.2) — far too slow/heavy for 8 GB RAM CPU; (b) paid video APIs — violate the zero-subscription cost model and are also less robust to build now; (c) pure manual, no automation — rejected as the primary path but retained as an explicit per-clip fallback |

*Note*: Constitution III forbids paid external services **for analysis**; this
feature uses a **free** external service **for generation**, with the operator
explicitly in the loop and an accepted ToS/ban risk documented in the spec
Assumptions. The Instagram constitution principles (I, II) are fully preserved.
