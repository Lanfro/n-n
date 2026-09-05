---

description: "Task list template for feature implementation"
---

# Tasks: Instagram Growth Pipeline — Media Vault, Hosting, HITL Alerting

**Input**: Design documents from `/specs/001-instagram-growth-pipeline/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Tests are requested — validation suites for vault dedup, media host,
channel sync, and the HITL alert (quickstart scenarios 5-8).

**Organization**: Tasks are grouped by user story to enable independent
implementation and testing of each story. The public-publish baseline (US1-3)
already exists in the codebase; this run covers the vault (US4) plus the US1
deltas (HITL alert FR-016, hosted publish URL FR-017).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Single project**: `src/`, `tests/` at repository root

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Dependencies + configuration for the vault and alerting.

- [ ] T001 Add `boto3` runtime dependency to `pyproject.toml` and run `uv lock`
- [ ] T002 Add `vault:` and `approval:` blocks to `config/config.yaml`
      (defaults; secrets blank; `vault.telegram.required: false`,
      `vault.host.backend: none`) per plan.md / contracts/media-host.md
- [ ] T003 Add `data/vault/` (and any uploaded media caches) to `.gitignore`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: DB schema + manager support every story; nothing else can start.

- [ ] T004 Add `vault_media` table to `_SCHEMA` in
      `src/database/db_manager.py` per data-model.md (sha256 UNIQUE, source,
      telegram_file_id/message_id, public_url, media_type, size_bytes)
- [ ] T005 Add `channel_sync` table to `_SCHEMA` in `src/database/db_manager.py`
      (single-row offset cursor)
- [ ] T006 Add `vault_media_id` column to the `posts` table in
      `src/database/db_manager.py`
- [ ] T007 Implement vault CRUD + sync-offset helpers on `DBManager` in
      `src/database/db_manager.py`: `get_vault_media_by_sha256`,
      `insert_vault_media`, `get_vault_media`, `set_vault_archive`,
      `set_public_url`, `set_post_vault_media`, `get_channel_offset`,
      `set_channel_offset`
- [ ] T008 Unit tests for the new schema + helpers in
      `tests/unit/test_db_vault.py` (parallel to T007 impl)

**Checkpoint**: Foundation ready — vault CRUD testable without network.

---

## Phase 3: User Story 1 — Publish loop deltas (Priority: P1) 🎯 MVP

**Goal**: FR-016 (alert before blocking) + FR-017 (hosted URL for live
publish); baseline publish loop already exists.

**Independent Test**: quickstart Scenario 6 (sound + Telegram notify fires on
`AWAITING_APPROVAL`) and Scenario 8 (dry-run only here — R2 upload mocked;
`public_url` resolved without network).

### Implementation for User Story 1

- [ ] T009 [P] [US1] Implement `src/approval/sound.py` — `alert()` plays
      `winsound` system beep, or a custom `.wav` via `PlaySound(SND_ASYNC)`;
      graceful no-op when `approval.sound: false` or winsound unavailable
- [ ] T010 [P] [US1] Implement `src/approval/notifier.py` — `notify()` sends a
      Telegram text message via the Bot API (`requests` POST `sendMessage`) to
      `telegram.notify_chat_id` or the first `allowed_chat_ids`; silent no-op
      when not configured or `approval.notify_telegram: false`
- [ ] T011 [US1] Wire alert into `TelegramGateway.request_approval` in
      `src/approval/telegram_gateway.py` so sound + notification fire before
      CLI or Telegram waiting (FR-016)
- [ ] T012 [P] [US1] Implement `src/vault/media_host.py` — `MediaHost` ABC +
      `R2Host`/`S3Host` (boto3, `endpoint_url` override, `public_base_url`,
      key `media/<sha[:2]>/<sha[:12]>.ext`, raise `MediaHostConfigError` on
      half-config) + `NoHost` per contracts/media-host.md
- [ ] T013 [US1] Rework `MetaPublisher._image_url` in
      `src/publisher/meta_publisher.py` to accept a resolved `public_url`
      (host upload) and keep the fail-loud `file://` placeholder only when
      None (FR-017)
- [ ] T014 [US1] Connect host upload + `public_url` in `main.py` publish path
      (non-dry-run only; skip on `--dry-run`)
- [ ] T015 [US1] Tests: HITL alert + URL resolution in
      `tests/unit/test_alert.py` and `tests/unit/test_media_host.py`
      (mock winsound, requests, boto3)

**Checkpoint**: approval pings the operator; publisher uses hosted URLs; all
tests green offline.

---

## Phase 4: User Story 4 — Media vault & Telegram archive (Priority: P2)

**Goal**: FR-013/014/015 — content-addressed local vault + private-channel
archive + idempotent channel sync (US4).

**Independent Test**: quickstart Scenarios 5 and 7 (dedup on double submit;
`--sync-vault` idempotent with a mocked Telegram API).

### Implementation for User Story 4

- [ ] T016 [P] [US4] Implement `src/vault/media_vault.py` — `ingest(path, source,
      delete_source=False)` hashes (sha256), dedups against `vault_media`,
      copies into `data/vault/<yyyymm>/<sha[:12]>.ext`, inserts row, returns
      `vault_media_id`; reusable for drop / ai_generated / telegram
- [ ] T017 [P] [US4] Implement `src/vault/telegram_archive.py` —
      `archive(vault_media_id)` uploads Document with `sha256:<hash>` caption,
      `download_file(file_id)` size-verified, `resolve_chat_id(chat_ref)`,
      `sync_from_channel(offset)` -> new-vault-items + next offset; raises
      `VaultArchiveError` on failure
- [ ] T018 [US4] Add `--sync-vault` and `--resolve-chat` subcommands to `main.py`
      (vault-agent CLI per contracts/telegram-protocol.md Part B)
- [ ] T019 [US4] Hook ingest+archive into `run_pipeline` in `main.py`: ingest
      media (drop) -> archive to channel (best-effort, respecting
      `vault.telegram.required`) -> link post via `set_post_vault_media`,
      storing `public_url` when a host is configured
- [ ] T020 [US4] Auto-run vault channel sync at the start of every non-dry-run
      submit in `main.py` (FR-015) when vault telegram configured
- [ ] T021 [US4] Support AI-generated media: `ingest(..., source="ai_generated")`
      reusable from `main.py` for future generation output (US4/AC1 scope)
- [ ] T022 [US4] Tests: vault dedup/reuse + channel sync idempotency in
      `tests/unit/test_vault.py` and `tests/unit/test_telegram_archive.py`
      (mock httpx/Telegram + boto3)

**Checkpoint**: every unique cat picture archived once to the channel; manual
channel adds sync into the vault without duplicates.

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Docs, lint, smoke gate, and convergence.

- [ ] T023 [P] Update `CONTEXT.md` (add `src/vault/`, `data/vault/`, telegram
      vault role, HITL alert note)
- [ ] T024 Update `README.md` quickstart to cover `--sync-vault`,
      `--resolve-chat`, and the vault config
- [ ] T025 [P] Run `uv run ruff check src main.py tests` and fix all findings
- [ ] T026 Run the smoke test `uv run main.py --account cat_1 --media
      data/input_media/sample_cat.jpg --dry-run` (PUBLISHED reachable)
- [ ] T027 Run `uv run pytest` (full suite green)
- [ ] T028 Run `/speckit.converge` to close any remaining gaps (do not modify
      spec/plan; append convergence tasks if needed)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — boto3, config, gitignore first
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS US1 delta + US4
- **US1 deltas (Phase 3)**: Depends on Phase 2 (DB + config); independent of US4
- **US4 (Phase 4)**: Depends on Phase 2 only
- **Polish (Phase 5)**: Depends on Phases 3 + 4

### Parallel Opportunities

- T009/T010/T012 (sound, notifier, media host) all [P]
- T016/T017 (vault, archive) all [P]
- T001/T002/T003 [P] in setup
- T008 follows T007; T022 follows T016/T017

### Within Each User Story

- Tests-and-code pairs kept adjacent so each story is verified standalone.

---

## Implementation Strategy

### MVP First

Complete Phases 1-3 (foundation + US1 deltas) → validate alert + host with
mocked network → then US4 vault → polish. Each phase is independently testable
per its Checkpoint.

## Notes

- [P] tasks = different files, no dependencies
- Commit + push `main` after each phase completes (user-set rule)
- `--dry-run` must stay hermetic: no Telegram, no Ollama, no boto3