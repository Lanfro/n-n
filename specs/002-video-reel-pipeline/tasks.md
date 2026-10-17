---
description: "Task list for AI Video Reel Pipeline"
---

# Tasks: AI Video Reel Pipeline from Real Cat Photos

**Input**: Design documents from `/specs/002-video-reel-pipeline/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Included — the project constitution mandates `uv run pytest` as a quality gate, so unit/integration test tasks are part of the deliverable.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- Single project: `src/`, `tests/`, `config/`, `main.py` at repository root (per plan.md).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add the new dependency, configuration surface and module scaffold.

- [ ] T001 Add `playwright` as a project dependency (`uv add playwright`) and re-lock in `pyproject.toml` / `uv.lock`
- [ ] T002 [P] Add a `reel:` block to `config/config.yaml` (service, service_url, browser_profile_dir, selectors_path, headless, generation_timeout_seconds, aspect, min/target resolution, duration bounds, max size, publish.share_to_feed)
- [ ] T003 [P] Create `config/reel/selectors.json` with the PixVerse URLs and selectors (login check, upload input, prompt field, aspect/duration controls, generate button, result/download, challenge and limit markers)
- [ ] T004 [P] Add `data/.browser/` and `data/generated/` to `.gitignore` (browser profile must never be committed)
- [ ] T005 [P] Scaffold the `src/reel/` package with `__init__.py` and empty module files (`brief.py`, `producer.py`, `selectors.py`, `validator.py`, `models.py`)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core data plumbing shared by the user stories. No user story work begins until this is complete.

**⚠️ CRITICAL**: US3 depends on the DB changes; US1/US2 depend on the shared models.

- [ ] T006 Extend the `posts` schema in `src/database/db_manager.py` with additive columns `media_type`, `brief_id`, `reel_meta`, plus a guarded migration (`PRAGMA table_info` + `ALTER TABLE ADD COLUMN`) and a partial unique index on `(account_key, brief_id)` for `media_type='reel'`
- [ ] T007 [P] Add DB helpers in `src/database/db_manager.py`: create a reel post, fetch a reel post by `brief_id`, set `reel_meta`, and record reel publish results
- [ ] T008 [P] Define shared reel dataclasses in `src/reel/models.py` (`ReelBrief`, `ClipReport` with probe fields + validation outcome + caveats, `BrowserSessionState`) matching `specs/002-video-reel-pipeline/data-model.md`

**Checkpoint**: Foundation ready — user stories can now be implemented.

---

## Phase 3: User Story 1 - Plan a Reel from a real cat photo (Priority: P1) 🎯 MVP

**Goal**: Turn a creative set + real vault photos into self-contained Reel briefs (motion prompt, source photo, expected output, caption/hook/hashtags).

**Independent Test**: Run `--reel-plan --set <key>` and confirm one brief per shot is written, each referencing an existing labelled cat photo, with motion prompt, `expected_output`, and persona-compliant text — no network.

### Tests for User Story 1

- [ ] T009 [P] [US1] Unit test for the brief builder (shot plan + anchors → briefs, `brief_id`/`expected_output` format, persona limits, missing-subject handling) in `tests/unit/test_reel_brief.py`

### Implementation for User Story 1

- [ ] T010 [US1] Add per-scene motion cues to `config/creative/sets.json` (a `motion` field per scene) and extend `src/engine/scene_writer.py` to emit a `motion_prompt` per shot
- [ ] T011 [US1] Implement the brief builder in `src/reel/brief.py` (select anchor via `src/generator`→relocated anchor helper, build `ReelBrief`, enforce persona rules, write `reel_brief_NN.json`) — depends on T008, T010
- [ ] T012 [US1] Implement the set `manifest.json` writer and missing-source reporting in `src/reel/brief.py`
- [ ] T013 [US1] Add the `--reel-plan --set <key> [--set-shots N]` CLI path and dispatch in `main.py`

**Checkpoint**: US1 fully functional — briefs can be produced and handed to a human or the producer.

---

## Phase 4: User Story 2 - Produce the Reel video via assisted generation (Priority: P2)

**Goal**: Drive the authenticated browser session to animate each source photo into a clip, downloading it under the expected filename; pause and alert on challenges/limits; accept manual drop-ins.

**Independent Test**: With an authenticated session, run `--reel-produce --set <key>` and confirm missing clips are produced/downloaded and existing ones skipped; simulate a challenge and confirm an alert + clean pause.

### Tests for User Story 2

- [ ] T014 [P] [US2] Unit test for the producer with a mocked Playwright/selectors layer (idempotent skip, challenge/limit → pause + alert, manual drop-in accepted) in `tests/unit/test_reel_producer.py`

### Implementation for User Story 2

- [ ] T015 [US2] Implement the selector/config loader in `src/reel/selectors.py` (read `config/reel/selectors.json`, expose service URL + selectors + challenge/limit markers) — depends on T003
- [ ] T016 [US2] Implement the persistent browser session manager in `src/reel/producer.py` (persistent context on `data/.browser/reel`, headed, state detection: authenticated/logged_out/challenged/limit_reached)
- [ ] T017 [US2] Implement the per-clip generation flow in `src/reel/producer.py` (upload source photo, fill motion prompt, select 9:16 + duration, generate, bounded wait, download to `expected_output`) — depends on T016
- [ ] T018 [US2] Implement skip/resume and manual drop-in handling in `src/reel/producer.py` (skip present clips; accept hand-placed clip) — depends on T011, T016
- [ ] T019 [US2] Wire operator alerting on challenge/limit/unexpected-UI in `src/reel/producer.py` using `src/approval/notifier.py` + `src/approval/sound.py`, then pause cleanly
- [ ] T020 [US2] Add the `--reel-produce --set <key>` CLI path and dispatch in `main.py`

**Checkpoint**: US1 and US2 work independently — a folder of validated-or-not clips is produced.

---

## Phase 5: User Story 3 - Validate, approve and publish the Reel to Instagram (Priority: P3)

**Goal**: Validate clips against Reel requirements, repair only audio/resolution, gate on human approval with a video preview, then publish via the official Graph API Reels flow with status polling; record results and support retry without duplicates.

**Independent Test**: Run `--reel-publish --set <key> --brief <id> --dry-run` on a compliant clip and confirm validation → approval request (with preview) → synthetic publish reaching `PUBLISHED`; confirm an out-of-spec clip is refused before approval.

### Tests for User Story 3

- [X] T021 [P] [US3] Unit test for the validator (valid / repaired / rejected paths, missing-audio mux, low-res upscale, ffprobe-unavailable fail-safe) using fixture clips in `tests/unit/test_reel_validator.py`
- [X] T022 [P] [US3] Unit test for the Reels publisher (container param set, status polling to FINISHED, publish, ERROR handling, dry-run synthetic ids) with mocked HTTP in `tests/unit/test_meta_publisher_reels.py`

### Implementation for User Story 3

- [X] T023 [US3] Implement the clip validator in `src/reel/validator.py` (ffprobe parse; allowed repairs: silent AAC mux + min-res upscale; reject rules; caveats) per `contracts/reel-clip-requirements.md`
- [X] T024 [US3] Add `create_reel_container`, `wait_for_container` and reuse `publish_container` in `src/publisher/meta_publisher.py` (`media_type=REELS`, `video_url`, `caption`, `share_to_feed`; bounded status polling; dry-run synthetic ids)
- [X] T025 [US3] Extend `src/approval/telegram_gateway.py` to send a video preview (`send_video`) for reel approval requests, keeping the CLI fallback
- [X] T026 [US3] Implement the reel publish flow in `main.py`: create/reuse the reel post (validate → `AWAITING_APPROVAL` → gate → `APPROVED`), append the AI-generated disclosure to the caption, host the clip via `MediaHost`, publish via the Reels flow, record ids/errors — depends on T006, T007, T023, T024, T025
- [X] T027 [US3] Implement duplicate prevention and retry semantics in `main.py` (reuse post by `brief_id`; re-validate on retry; never create a second Reel)
- [X] T028 [US3] Add the `--reel-validate` and `--reel-publish --set <key> --brief <id> [--dry-run]` CLI paths and dispatch in `main.py`

**Checkpoint**: All three stories functional — a real, approved Reel can be published end-to-end.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Retire the old path, keep docs/gates green.

- [ ] T029 Remove the static SD path: delete `src/generator/` and `.venv-gen/`, drop `--generate-set`/`--set-shots` from `main.py`, and remove the `creative.gen_env_python`/`generator_script`/`model`/`steps`/`mode`/`cfg` keys from `config/config.yaml`
- [ ] T030 [P] Relocate any still-needed anchor helper (e.g. `last_10_anchors`, `prep_square`) out of `src/generator/` into `src/reel/` before deletion
- [ ] T031 [P] Update `CONTEXT.md` / `AGENTS.md` / `README` references to the retired static generator and the new `--reel-*` commands
- [ ] T032 [P] Add regression test asserting the photo pipeline still reaches `PUBLISHED` under `--dry-run` in `tests/integration/test_photo_pipeline_smoke.py`
- [ ] T033 Run `uv run ruff check` and fix all lint issues
- [ ] T034 Run `uv run pytest` and make the suite green
- [ ] T035 Run the dry-run smoke test `uv run main.py --account cat_1 --media data/input_media/sample_cat.jpg --dry-run` and confirm exit 0
- [ ] T036 Execute the `specs/002-video-reel-pipeline/quickstart.md` validation scenarios and record results

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories
- **User Stories (Phase 3+)**: Depend on Foundational
  - US1 can start right after Phase 2
  - US2 depends on US1's briefs (T011) but is independently testable with a hand-made brief
  - US3 depends on US2's clips (or a hand-made clip) and on the DB changes (T006/T007)
- **Polish (Phase 6)**: Depends on all desired stories

### User Story Dependencies

- **US1 (P1)**: after Foundational — no dependency on other stories
- **US2 (P2)**: after Foundational — consumes US1 briefs (`expected_output`) but testable with a fixture brief
- **US3 (P3)**: after Foundational — consumes a clip (from US2 or hand-made) and the DB schema

### Within Each Story

- Tests first (fail), then models, then services, then CLI wiring
- US1: T009 → T010/T011 → T012 → T013
- US2: T014 → T015/T016 → T017 → T018/T019 → T020
- US3: T021/T022 → T023/T024/T025 → T026 → T027 → T028

### Parallel Opportunities

- Setup: T002, T003, T004, T005 in parallel (T001 independent)
- Foundational: T007, T008 in parallel after T006
- US1: T009 (test) parallel with T010
- US2: T014 parallel with T015
- Polish: T030, T031, T032 in parallel

---

## Parallel Example: Setup + Foundational

```bash
# Setup (parallel):
Task: "T002 Add reel: block to config/config.yaml"
Task: "T003 Create config/reel/selectors.json"
Task: "T004 Gitignore browser profile + generated clips"
Task: "T005 Scaffold src/reel/ package"

# Foundational (parallel after T006):
Task: "T007 Add DB helpers in src/database/db_manager.py"
Task: "T008 Define shared reel dataclasses in src/reel/models.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1 Setup → Phase 2 Foundational
2. Phase 3 (US1) — briefs
3. **STOP and VALIDATE**: a human can execute briefs manually; the planner is useful on its own
4. Demo

### Incremental Delivery

1. Setup + Foundational → foundation ready
2. US1 → briefs (MVP) → validate
3. US2 → automated clip production → validate
4. US3 → validated, approved, published Reels → validate
5. Polish → retire the static path, green gates

---

## Notes

- [P] tasks = different files, no dependencies
- The retry/duplicate guarantee (SC-005) is enforced by the `brief_id` uniqueness (T006) plus the reuse logic (T027)
- Commit + push after each completed step per the project standing rule
- Constitution gates before merge: `uv run ruff check`, `uv run pytest`, dry-run smoke test
