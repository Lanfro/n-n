# Research & Technical Decisions — AI Video Reel Pipeline

Phase 0 output of `/speckit.plan`. All uncertainties resolved from the feature
spec, project context, platform requirements and current (October 2026) market
research. No `NEEDS CLARIFICATION` items remain.

## 1. Generation approach: assisted browser automation vs API vs local

- **Decision**: An **assisted, semi-automatic browser producer** (Playwright)
  drives a free third-party image-to-video service; the operator authenticates
  once in a persistent profile, then the system uploads the source photo, fills
  the prompt, triggers generation and downloads the clip. A correctly named clip
  dropped in by hand is accepted as equivalent.
- **Rationale**: On 8 GB RAM CPU-only hardware, local open-weights video
  generation (e.g. Wan 2.2) is not viable. In October 2026 the only genuinely
  sustainable $0 tier is a browser UI (PixVerse: 60 sign-up + ~30 daily credits);
  competitors have collapsed their recurring free tiers (Kling daily credits
  dropped, Luma has no free plan, Runway is a one-time 125 credits, Hailuo is a
  one-time welcome pack, Pika free = 0, Sora shut down). No stable free *API*
  exists. Assisted automation keeps the pipeline largely hands-off while staying
  within the free tier.
- **Alternatives considered**: Local SD/AnimateDiff or Wan 2.2 (rejected — CPU/RAM
  infeasible at usable latency); paid video APIs (rejected — breaks $0 budget;
  PixVerse/Kling APIs bill separately from the web free tier); fully manual (kept
  as an explicit fallback, not the primary path).
- **Accepted risk**: automating/assisting a service's web UI likely violates its
  ToS and could get the free account limited or banned. Documented in spec
  Assumptions; mitigated by selector isolation (repairable) and the manual drop-in
  fallback.

## 2. First service and platform-agnostic briefs

- **Decision**: Target **PixVerse** first, but keep all briefs and the producer
  interface **service-agnostic**: the brief is a plain JSON production order, and
  the service-specific selectors/flows live behind one driver + a selectors file.
- **Rationale**: The free tier is the deciding constraint; PixVerse currently
  offers the most durable daily allowance. Keeping the brief service-agnostic lets
  the operator switch service (or go fully manual) without touching the planner,
  validator or publisher.
- **Alternatives considered**: Hard-coding one service end-to-end (rejected —
  brittle and unportable); designing a generic multi-driver plugin system up front
  (rejected — over-engineering for one driver; the interface seam is enough).

## 3. Video source: real vault photos (image-to-video)

- **Decision**: Animate a **real, subject-labelled cat photo from the vault** as
  the first frame (image-to-video), reusing the existing anchor selection
  (last-10 per subject label).
- **Rationale**: For a two-cat brand channel, identity consistency is the whole
  game; image-to-video anchored on a real photo preserves the cats' actual
  appearance far better than text-to-video, and the pipeline already selects
  anchors deterministically per shot. It also gives the plan's prompts a stable
  subject.
- **Alternatives considered**: Pure text-to-video (rejected — identity drift);
  generating a still first then animating it (rejected — reintroduces the static
  generator we are retiring).

## 4. Springboarding from existing planning artefacts

- **Decision**: Reuse `src/engine/scene_writer.py` and `config/creative/sets.json`
  to produce a shot plan, and add a **motion prompt** per shot; the shot plan is
  then mapped 1:1 into Reel briefs.
- **Rationale**: The scene writer already emits a deterministic, persona-aware
  shot plan (index, cat, account, prompt, caption, hook, hashtags, seed). Only the
  renderer is being replaced; the planner's value is retained. This also preserves
  the persona hook/caption/hashtag guarantees for free.
- **Alternatives considered**: A brand-new reel planner (rejected — duplicates
  working code and risks persona drift); reusing the SD image prompt verbatim
  (rejected — image prompts describe a still; video needs explicit motion cues).

## 5. Reel clip validation and repair

- **Decision**: Validate every clip with **ffprobe** against Instagram's Reel
  requirements (container, video/audio codecs, aspect ratio, resolution, duration,
  file size). Repair only two fixable issues with **ffmpeg**: add a **silent AAC
  track** when audio is missing, and **upscale** when below the minimum accepted
  resolution. Reject anything else (e.g. non-vertical that cannot be safely
  cropped, unsupported format that cannot be remuxed).
- **Rationale**: Free-tier output is ~5–10 s, often silent and possibly 540p.
  Instagram accepts 540×960 as the floor and recommends 1080×1920; a silent audio
  track satisfies the audio requirement; a controlled upscale keeps the clip
  publishable without altering composition. Repairing beyond these two risks
  degrading the brand content.
- **Alternatives considered**: Publish raw free-tier output (rejected — silent
  clips and sub-floor resolution risk rejection/quality loss); heavy transcode
  pipeline (rejected — unnecessary at this scale and cost to quality); skip
  validation and let the API reject (rejected — wastes approval attention and
  container quota).

## 6. Publishing: Meta Graph API Reels flow

- **Decision**: Publish approved clips as Reels through the official Graph API:
  `POST /{ig-user-id}/media` with `media_type=REELS`, `video_url` (public R2/S3
  URL) and `caption`, then **poll** `GET /{container-id}?fields=status_code`
  until `FINISHED`, then `POST /{ig-user-id}/media_publish`. `share_to_feed` is
  configurable and defaults on.
- **Rationale**: Meta's sanctioned Reels publishing path; requires a public video
  URL (the vault's `MediaHost` already hosts `.mp4` unchanged). Unlike images,
  video is processed asynchronously, so status polling is mandatory before publish.
- **Alternatives considered**: Size/duration outside the Reels tab window
  (rejected — must land in the Reels tab for growth); publishing the raw file via
  a resumable upload endpoint now (rejected for v1 — the public-URL path is
  simpler and R2 already exists; resumable upload noted as a future option).
- **Content note**: Reels captions include an explicit AI-generated disclosure
  requirement (FR-016), independent of platform auto-labelling.

## 7. Post lifecycle: reuse the existing status machine

- **Decision**: Reel posts are stored in the **same `posts` table** and flow
  through the existing status machine (`PENDING_ANALYSIS -> AWAITING_APPROVAL ->
  APPROVED -> PUBLISHED`, plus `FAILED`/`REJECTED`). Add additive columns:
  `media_type` (image|reel) and `brief_id` (dup guard), plus a small JSON
  `reel_meta` for validation details. Initial reel posts start at
  `PENDING_ANALYSIS` (content is already generated) and transition to
  `AWAITING_APPROVAL`.
- **Rationale**: The approval gate, retry edges and publisher path are already
  proven; reusing them avoids a parallel lifecycle and keeps the "single source of
  truth" invariant. `brief_id` (e.g. `<set>:<index>`) with a partial unique index
  satisfies the no-duplicate-publish guarantee (SC-005).
- **Alternatives considered**: A separate `reel_posts` table (rejected — forks the
  status machine and approval flow); no DB tracking, filesystem only (rejected —
  loses retry/duplicate guarantees).

## 8. Browser session management

- **Decision**: A **persistent Playwright browser context** (a profile directory,
  gitignored) holds the logged-in session; the operator authenticates once
  (including any captcha/OTP). The producer checks for challenge/limit signals and,
  if present, raises an operator alert and **pauses cleanly**, leaving a resumable
  state. The browser runs **headed** so anti-bot checks pass and the operator can
  intervene.
- **Rationale**: Login flows (email/OTP, Cloudflare) are hostile to automation;
  interactive authentication once per session is the reliable pattern. Failing
  cleanly (rather than looping) protects the free account and matches SC-006.
- **Alternatives considered**: Headless + credential automation (rejected — bot
  detection and OTP break it); Google OAuth automation (rejected — bot detection
  effectively blocks it); storing account credentials in the repo (rejected —
  security principle).

## 9. Dependency & quality tooling

- **Decision**: Add `playwright` (and its Chromium browser) as the only new
  Python dependency; rely on system `ffmpeg`/`ffprobe` via subprocess for media
  work. Keep `uv` / `ruff` / `pytest` / dry-run-default gates.
- **Rationale**: Playwright is the de-facto standard for reliable browser
  automation and supports persistent contexts. ffmpeg is ubiquitous and avoids a
  heavyweight Python video dependency; calling it via subprocess keeps the Python
  environment light on an 8 GB machine and testable via mocks.
- **Alternatives considered**: Selenium (weaker context persistence/tracing);
  PyAV/moviepy (heavier, redundant with system ffmpeg); a pure-Python MP4 parser
  (rejected — incomplete validation vs ffprobe).
