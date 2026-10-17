# Contract: Assisted Video-Service Browser Producer

Drives a free third-party image-to-video service (PixVerse first) through a
persistent, operator-authenticated browser session to turn a brief's source photo
into a vertical clip, then downloads it. This automation targets the **external
video service only** — never Instagram (Constitution I).

## Session model

| Item | Value |
|---|---|
| Driver | Playwright, Chromium, **headed** (visible) |
| Context | Persistent profile directory, gitignored (`data/.browser/reel/`) |
| Authentication | Operator logs in once (email/OTP/captcha handled by the operator); cookies persist across runs |
| Re-auth | Required only when the session expires or is challenged |

## Per-clip flow

1. **Guard**: if `state` is `challenged`, `logged_out` or `limit_reached`, do not
   attempt generation — raise an operator alert and pause (FR-008).
2. Open the service's image-to-video workspace.
3. Upload `source_photo_path` as the first frame.
4. Fill the generation prompt with `motion_prompt`; select aspect `9:16` and
   `duration_seconds`.
5. Trigger generation and wait for completion (bounded wait; no unbounded retry).
6. Download the result to `data/generated/<set>/<expected_output>`.
7. Record success in the set `manifest.json`.

## Skip / resume semantics

- If `<expected_output>` already exists and is non-empty → skip (produced).
- On challenge/limit/logout → **pause cleanly**, preserve already-produced clips,
  alert the operator, and exit; the next run resumes from the missing clips.
- A clip the operator drops in by hand under `expected_output` is treated as
  produced (FR-009).

## Challenge / failure detection

The producer maps observable service conditions to `BrowserSession.state`:

| Condition | State | Action |
|---|---|---|
| Login page / session expired | `logged_out` | Alert + pause |
| CAPTCHA / "verify you are human" / anti-bot interstitial | `challenged` | Alert + pause, keep browser open for the operator |
| "Out of credits" / daily limit message | `limit_reached` | Alert + pause until next window |
| Unexpected UI (selector not found) | — | Alert with the service URL + which step failed; pause |

## Selector isolation

All service-specific selectors and URLs live in **one place**
(`src/reel/selectors.py` + `config/reel/selectors.json`) so that UI drift is a
one-file repair, not a code hunt. No service-specific selector may appear
elsewhere in the codebase.

## Configuration (`config/config.yaml`, `reel.*`)

```yaml
reel:
  service: "pixverse"
  service_url: "https://app.pixverse.ai"
  browser_profile_dir: "data/.browser/reel"
  selectors_path: "config/reel/selectors.json"
  headless: false          # headed is required for anti-bot + operator intervention
  generation_timeout_seconds: 600
```

## Safety

- The browser profile and any downloaded clips are gitignored; no credentials are
  ever written to the repository (Constitution § Security).
- The producer never automates Instagram; the only Instagram path is the Graph API
  ([meta-graph-api-reels.md](meta-graph-api-reels.md)).
