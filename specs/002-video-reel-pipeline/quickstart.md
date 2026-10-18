# Quickstart — Validation Guide

Phase 1 output of `/speckit.plan`. Runnable scenarios that prove the Reel feature
works end-to-end. Full implementation lives in `tasks.md`; this file is the
validation/run guide.

## Prerequisites

- `uv` installed; Python >= 3.11; repo cloned; `uv sync` run once.
- `ffmpeg`/`ffprobe` available on `PATH` (clip validation/repair).
- For assisted production: `uv run playwright install chromium`, and a one-time
  manual login to the video service in the persistent profile.
- For live publishing: `config/config.local.yaml` filled (`meta.*`, telegram,
  `vault.host.backend: r2` for a public clip URL).

## Scenario 1 — Plan Reel briefs (offline)

```
uv run main.py --reel-plan --set parallel_lives
```

**Expected**: `data/generated/parallel_lives/reel_brief_NN.json` written (one per
shot, bounded by the set's scene count), each referencing an existing, labelled
cat photo in the vault; `manifest.json` lists the briefs; each brief carries a
motion prompt, an `expected_output` filename, and persona-compliant
caption/hook/hashtags. No network calls.

**Proves**: FR-001–FR-004, SC-004.

## Scenario 2 — Validate a produced clip

Place a downloaded/obtained clip at the brief's `expected_output`, then:

```
uv run main.py --reel-validate --set parallel_lives --brief parallel_lives:0
```

**Expected**: ffprobe-derived report; a compliant clip reports `valid`; a silent
clip reports `repaired` with `silent` caveat; a non-vertical/too-short/too-large
clip reports `rejected` with the precise reason and is **not** eligible for
approval.

**Proves**: FR-010, FR-011, [reel-clip-requirements.md](contracts/reel-clip-requirements.md).

## Scenario 3 — Dry-run Reel publish (hermetic)

```
uv run main.py --reel-publish --set parallel_lives --brief parallel_lives:0 --dry-run
```

**Expected**: the clip is validated, then a post is created and moved
`PENDING_ANALYSIS -> AWAITING_APPROVAL -> APPROVED` via auto-approval, "would
create Reel container", poll skipped, "would publish container", and the post
reaches `PUBLISHED` with a synthetic `media_dry_*` id. No network, no browser.

**Proves**: FR-012, FR-013, FR-015, SC-005; the approval gate is the only path to
`PUBLISHED`.

## Scenario 4 — Assisted production (operator present)

First run, once per profile:

```
uv run main.py --reel-login
```

**Expected**: a persistent browser window opens on the service; the operator
signs in there (the account must be created in *this* window, not another
browser). The run exits `0` once the sign-in panel clears / a session marker
matches, and the session is persisted for every later run; exiting `3` means
sign-in was not confirmed within the wait.

Then:

```
uv run main.py --reel-produce --set parallel_lives
```

**Expected (steady state)**: for each missing clip the browser opens the service,
uploads the source photo, fills the motion prompt, generates, and downloads
`reel_NN.mp4`; already-present clips are skipped. Run it again and confirm no clip
is regenerated.

**Expected (blocked)**: on a login/anti-bot challenge or exhausted daily
allowance, an operator alert fires (sound + Telegram) within one minute, the run
pauses cleanly, and previously produced clips remain. The paid service's
"Insufficient Credits" paywall is treated the same way (`limit_reached`, exit
`3`); each 5s 540P clip costs credits, so top up or wait for the daily reset.
Drop the clip in by hand under `expected_output` and re-run to confirm it is
treated as produced.

**Proves**: FR-005, FR-007, FR-008, FR-009, SC-003, SC-006,
[video-service-browser.md](contracts/video-service-browser.md).

## Scenario 5 — Live Reel publish with human approval

1. Configure `meta.dry_run: false` and R2 hosting in `config.local.yaml`.
2. Run `uv run main.py --reel-publish --set parallel_lives --brief parallel_lives:0`.

**Expected**: the clip is validated → the approval request arrives on Telegram
**with a video preview** → on approve, the clip is uploaded to R2, a Reel
container is created (`media_type=REELS`), processing is polled to `FINISHED`, the
container is published, and the post reaches `PUBLISHED` with `ig_media_id`. The
caption contains the AI-generated disclosure.

**Caution**: `meta.dry_run` defaults to `true`; set it `false` explicitly. Media
must be publicly reachable ([meta-graph-api-reels.md](contracts/meta-graph-api-reels.md)
+ `specs/001-.../contracts/media-host.md`).

**Proves**: FR-012–FR-017, FR-019, SC-001.

## Scenario 6 — Retry a failed publish (no duplicate)

Force a publish failure (e.g. invalid token), observe the post in `FAILED` with
the platform error, then retry:

```
uv run main.py --reel-publish --set parallel_lives --brief parallel_lives:0
```

**Expected**: re-validation + a fresh approval-gated publish; on success no
duplicate Reel is created for the same `brief_id`.

**Proves**: FR-015, SC-005, SC-007.

## Scenario 7 — Static generation is retired

```
uv run main.py --generate-set parallel_lives      # must be rejected as unknown
```

**Expected**: the flag no longer exists (argparse error); `src/generator/` and
`.venv-gen/` are gone; no `creative.gen_env_python`/`model`/`steps` keys remain.

**Proves**: FR-018.

## Scenario 8 — Regression: photo pipeline intact

```
uv run main.py --account cat_1 --media data/input_media/sample_cat.jpg --dry-run
```

**Expected**: unchanged behaviour — reaches `PUBLISHED` in dry-run, exit 0.

**Proves**: the Reel feature does not regress the existing photo pipeline.

## Scenario 9 — Lint + tests

```
uv run ruff check
uv run pytest
```

**Expected**: ruff clean; pytest green (brief builder, validator with fixture
clips, Reels publisher with mocked HTTP, browser producer with mocked Playwright).

## References

- Data model & status machine: [data-model.md](data-model.md)
- Reel brief contract: [contracts/reel-brief.md](contracts/reel-brief.md)
- Browser producer contract: [contracts/video-service-browser.md](contracts/video-service-browser.md)
- Clip requirements: [contracts/reel-clip-requirements.md](contracts/reel-clip-requirements.md)
- Publishing contract: [contracts/meta-graph-api-reels.md](contracts/meta-graph-api-reels.md)

## Validation results (local run)

| # | Scenario | Result | Notes |
|---|---|---|---|
| 1 | Plan briefs (`--reel-plan --set parallel_lives`) | Pass | 5 briefs + manifest written; no network. Plan call ~3 min (local LLM). |
| 2 | Validate a clip (`--reel-validate`) | Pass | With ffmpeg 9.0.2 installed: compliant clip `valid` (exit 0); silent clip `repaired`/`silent`; landscape clip `rejected` (exit 1). |
| 3 | Dry-run Reel publish | Pass | `--reel-publish --set parallel_lives --brief parallel_lives:0 --dry-run` -> validate -> auto-approve -> synthetic container/media -> `PUBLISHED`, exit 0. No network/browser. |
| 4 | Assisted production (`--reel-produce`) | Pass | Playwright 1.64 + Chromium; `--reel-login` persists the operator session. A real run produced `reel_00.mp4` (576x1024, 5.04s, h264+aac) in ~1m46s and validated `valid` (exit 0). Fixes: SPA settle before the session guard; visible-only control selection; the create-settings popover driven for `540P`/`9:16`/`5s`; a Playwright fault mid-clip now pauses cleanly (`unexpected_ui`) instead of crashing; the service's "Insufficient Credits" paywall is detected as `limit_reached` (exit 3) rather than a 600s hang. |
| 5 | Live Reel publish | Not run | Needs `config.local.yaml` credentials + R2 host + `meta.dry_run: false`. |
| 6 | Retry / no duplicate | Pass | Re-running `--reel-publish` reports "already published ... not republishing" (exit 0); DB shows exactly one reel post for the brief. |
| 7 | Static generation retired | Pass | `--generate-set` → argparse "unrecognized arguments" (exit 2); `src/generator/`, `.venv-gen/` removed; config keys gone. |
| 8 | Regression: photo pipeline | Pass | `uv run main.py --account cat_1 --media data/input_media/sample_cat.jpg --dry-run` → exit 0, `PUBLISHED`. |
| 9 | Lint + tests | Pass | `ruff check` clean; `pytest` 157 passed with ffmpeg on PATH (the 5 real-ffmpeg validator tests skip when it is absent). |

