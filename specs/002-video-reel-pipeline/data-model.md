# Data Model — AI Video Reel Pipeline

Phase 1 output of `/speckit.plan`. Entities extracted from the feature spec and
mapped onto the existing SQLite schema (`src/database/db_manager.py`), which is
extended additively. Reel posts reuse the existing `posts` table and status
machine so that the approval gate remains the single source of truth.

## ReelBrief (filesystem: `data/generated/<set>/reel_brief_NN.json`)

The production order for one shot. A plain, self-contained JSON handoff that
either the assisted producer or a human can execute. Not stored in SQLite (it is
a build artefact), but its `brief_id` is recorded on the resulting post.

| Field | Type | Description | Validation |
|---|---|---|---|
| `brief_id` | string | Stable identity of the brief | `"<set>:<shot_index>"`; unique within a set run |
| `set` | string | Creative set key | must exist in `config/creative/sets.json` |
| `shot_index` | integer | Shot position in the set | >= 0; unique within the run |
| `account_key` | string | Target account | one of `cat_1`, `cat_2` |
| `cat` | string | Target cat subject label | matches a persona/subject label (`nero`, `nuvola`) |
| `motion_prompt` | string | Instruction describing how the cat should move | non-empty; describes motion, not a still |
| `source_photo_path` | string | Path to the real source photo | must exist under the vault at production time |
| `source_vault_media_id` | integer | Vault media id of the source photo | resolves to an existing `vault_media` row |
| `expected_output` | string | Exact filename the clip must have | `reel_<shot_index>.mp4`, inside the set folder |
| `caption` | string | Ready-to-publish caption (without AI disclosure) | <= 3 sentences (persona rules) |
| `reel_text` | string | On-screen hook | < 8 words (persona rules) |
| `hashtags` | array[string] | Tags without `#` | 10–12 per persona rules |
| `aspect` | string | Target aspect ratio | `"9:16"` |
| `duration_seconds` | integer | Target clip duration | within the free tier / platform allowed range |
| `created_at` | string (ISO-8601 UTC) | Generation time | auto |

**Invariants**: (1) `source_photo_path` MUST reference a real, subject-labelled
vault photo of `cat` (FR-002). (2) `reel_text`/`caption`/`hashtags` MUST satisfy
the persona rules (FR-003). (3) `expected_output` is the contract that lets the
producer detect missing vs present clips and lets a human drop a clip in by hand
(FR-009).

## GeneratedClip (filesystem: `data/generated/<set>/reel_NN.mp4` + probed metadata)

A produced video file for a brief, with the technical facts needed to decide if it
may be published.

| Field | Type | Description | Validation |
|---|---|---|---|
| `path` | string | Clip file location | matches a brief's `expected_output` |
| `brief_id` | string | Back-reference to the producing brief | must match an existing brief |
| `container` | string | Container format | `mp4` or `mov` |
| `video_codec` | string | Video codec | H.264 (HEVC accepted by platform; H.264 preferred) |
| `audio_codec` | string | Audio codec | AAC; if absent, a silent AAC track is added |
| `has_audio` | boolean | Whether an audio stream exists | repaired to `true` when missing |
| `width` / `height` | integer | Resolution | portrait; height > width; >= min accepted resolution |
| `aspect_ratio` | float | Derived width/height | within 9:16 tolerance (or 1.91:1–9:16 platform range) |
| `duration_seconds` | float | Clip length | >= 3 s (>= 5 s preferred for the Reels tab); <= 15 min |
| `fps` | float | Frame rate | >= 23 (>= 30 recommended) |
| `size_bytes` | integer | File size | <= platform max (300 MB conservative) |
| `validation` | string | Outcome | `valid` \| `repaired` \| `rejected` |
| `reject_reason` | string (nullable) | Why rejected | set only when `validation = rejected` |
| `caveats` | array[string] | Quality notes for the operator | e.g. `watermark`, `upscaled` |

**Validation rules** (see [contracts/reel-clip-requirements.md](contracts/reel-clip-requirements.md)):
- **Repair (allowed)** → add silent AAC track when `has_audio = false`; upscale to
  the minimum accepted resolution when below it. Result is `repaired`.
- **Reject (not allowed)** → unsupported container/codec that cannot be remuxed;
  non-vertical that cannot be safely cropped; duration/size out of range;
  resolution below the platform floor after repair. Result is `rejected` and the
  clip is never offered for approval (FR-011).

## ReelPost (existing `posts` table, extended)

The publishable unit. Reels are stored in the same table as photo posts so the
status machine, approval gateway and publisher are shared. New additive columns
(guarded migration via `PRAGMA table_info` + `ALTER TABLE ADD COLUMN`):

| Column | Type | Description | Validation |
|---|---|---|---|
| `media_type` | text (nullable) | `image` (default/legacy) or `reel` | one of the two; `reel` marks an AI video post |
| `brief_id` | text (nullable) | Brief identity for duplicate prevention | unique per `account_key` among `media_type='reel'` (partial index) |
| `reel_meta` | text JSON (nullable) | Serialised validation summary + caveats | valid JSON or null |

Existing `posts` columns reused as-is: `account_key`, `media_path` (holds the
clip path for reels), `status`, `persona_name`, `reel_text`, `caption`,
`hashtags`, `telegram_message_id`, `ig_container_id`, `ig_media_id`,
`meta_error`, `created_at`/`updated_at`. `vision_description` and `vault_media_id`
are unused for reels (no vision analysis stage).

**Relationships**: a ReelPost references exactly one Brief (by `brief_id`) and one
GeneratedClip (by `media_path`), belongs to one account, and has at most one
approval decision and one published media identity.

## BrowserSession (filesystem: `data/.browser/reel/`, gitignored)

The operator-authenticated session on the external video service.

| Field | Type | Description |
|---|---|---|
| `service` | string | Which service the profile belongs to (e.g. `pixverse`) |
| `profile_dir` | string (path) | Persistent Playwright context directory |
| `state` | string | `authenticated` \| `logged_out` \| `challenged` \| `limit_reached` |
| `last_used_at` | string (ISO-8601 UTC) | Last successful use |

**Invariant**: the profile directory MUST be gitignored and MUST NOT contain
committed credentials (Constitution § Security). Challenge/limit states cause the
producer to alert and pause rather than retry unboundedly (FR-008).

## Status Machine (reused, unchanged)

Reel posts follow the same validated transitions enforced by
`db_manager.transition`:

```
PENDING_ANALYSIS -> AWAITING_APPROVAL -> APPROVED -> PUBLISHED
      |                   |   ^            |   ^
      v                   v   |            v   |
    FAILED <------------- REJECTED/FAILED ...  (retry edges via FAILED/REJECTED)
```

| From | To |
|---|---|
| PENDING_ANALYSIS | AWAITING_APPROVAL, FAILED, REJECTED |
| AWAITING_APPROVAL | APPROVED, REJECTED, FAILED, PENDING_ANALYSIS |
| APPROVED | PUBLISHED, FAILED, PENDING_ANALYSIS |
| PUBLISHED | FAILED |
| FAILED | PENDING_ANALYSIS, REJECTED |
| REJECTED | PENDING_ANALYSIS |

**Key invariants**: (1) the only path into `PUBLISHED` is through `APPROVED`
(the approval gate cannot be bypassed — Constitution II). (2) A reel post is
created at `PENDING_ANALYSIS` and moved to `AWAITING_APPROVAL` only after the clip
has passed validation, so no out-of-spec/rejected clip is ever offered for
approval (SC-002). (3) Retry after a failed publish re-validates and repeats the
approval-gated publish without creating a second post for the same `brief_id`
(SC-005).

## Persona / Reel rules (config/personas.json — reused)

Not new to this feature. The brief builder reads the same static persona entity
used by the photo pipeline: `reel_rules.max_words` (< 8), `caption_rules.max_sentences`
(≤ 3), `hashtag_count` (10–12), and `base_hashtags`. `account_key` on a brief must
resolve to an existing persona or generation fails fast.
