# Data Model — Instagram Growth Pipeline

Phase 1 output of `/speckit.plan`. Entities extracted from the feature spec and
mapped to the existing SQLite schema (`src/database/db_manager.py`).

## Post

The core tracked unit — one submitted piece of media with its lifecycle state.

| Field | Type | Description | Validation |
|---|---|---|---|
| `id` | integer PK | Stable row id | auto-increment |
| `account_key` | text | Which cat account this post belongs to | one of `cat_1`, `cat_2` |
| `media_path` | text | Original media file location | must exist when created |
| `status` | text | Current lifecycle state | one of the status machine values |
| `vision_description` | text (nullable) | Scene description from local vision model | written once analysis succeeds |
| `persona_name` | text (nullable) | Persona used for generation | matches a persona in `config/personas.json` |
| `reel_text` | text (nullable) | On-screen hook (<= 7 words by persona rules) | regenerated drafts replace value |
| `caption` | text (nullable) | Caption body | latest draft only |
| `hashtags` | text (nullable) | Comma-separated tags without `#` | count follows persona |
| `telegram_message_id` | integer (nullable) | Telegram message carrying the draft | set when sent |
| `ig_container_id` | text (nullable) | Graph API container id | set after container creation |
| `ig_media_id` | text (nullable) | Published media id | set after successful publish |
| `meta_error` | text (nullable) | Last failure reason | set when a stage fails |
| `vault_media_id` | integer (nullable) FK | Backing media asset in the vault | set on ingest; one asset serves many posts |
| `created_at` / `updated_at` | text (ISO-8601 UTC) | Timestamps | auto-managed |

**Relationships**: a Post belongs to exactly one account (`account_key`); it may
have one "current" draft (the latest generated content); it references at most
one approved decision gate; it references at most one `VaultMedia` (a single
unique image can back many posts on either account).

## VaultMedia (vault_media table)

The content-addressed archive entry for one unique media file.

| Field | Type | Description | Validation |
|---|---|---|---|
| `id` | integer PK | Stable row id | auto-increment |
| `sha256` | text UNIQUE | Content hash = dedup key | 64 hex chars; unique across vault |
| `original_filename` | text | Name as first seen | non-empty |
| `stored_path` | text | Canonical vault file path (`data/vault/<yyyymm>/<sha[:12]>.jpg`) | must exist when created |
| `media_type` | text | `image` or `video` | image in v1 |
| `size_bytes` | integer | Original file size | > 0 |
| `source` | text | `drop` \| `ai_generated` \| `telegram` | one of the three |
| `telegram_file_id` | text (nullable) | Telegram file_id of the archived Document | set after archive upload |
| `telegram_message_id` | integer (nullable) | Channel message id carrying the archive copy | set after archive upload |
| `public_url` | text (nullable) | Publically fetchable URL after R2/S3 upload | set by `MediaHost.upload` |
| `added_at` / `last_used_at` | text (ISO-8601 UTC) | Timestamps | auto-managed |

**Invariant**: `sha256` uniqueness is the single dedup rule — the same photo is
stored/uploaded once, yet can back N posts. The stored vision analysis on any
backed Post is reusable for future duplicates.

## ChannelSync (channel_sync table)

Single-row cursor for idempotent sync of the private Telegram channel.

| Field | Type | Description |
|---|---|---|
| `id` | integer PK | always 1 (single row) |
| `last_update_id` | integer | last consumed `channel_post` update id |
| `updated_at` | text | last sync time |

## Status Machine

```
PENDING_ANALYSIS ──> AWAITING_APPROVAL ──> APPROVED ──> PUBLISHED
      │ ▲                 │   ▲             │   ▲
      │ │                 │   │             │   │
      ▼ │                 ▼   │             ▼   │
    FAILED ◄─────────────┤   │           FAILED ┤
      │ ▲                 │   │
      ▼ │                 ▼   │
    REJECTED           FAILED │
```

Legal transitions (enforced in `db_manager.transition`):

| From | To |
|---|---|
| PENDING_ANALYSIS | AWAITING_APPROVAL, FAILED, REJECTED |
| AWAITING_APPROVAL | APPROVED, REJECTED, FAILED, PENDING_ANALYSIS |
| APPROVED | PUBLISHED, FAILED, PENDING_ANALYSIS |
| PUBLISHED | FAILED |
| FAILED | PENDING_ANALYSIS, REJECTED |
| REJECTED | PENDING_ANALYSIS |

**Key invariant**: the only path into `PUBLISHED` is through `APPROVED`.
`transition()` returns `False` for any edge not in the table, so the approval
gate cannot be bypassed.

## Persona (config/personas.json)

Not stored in SQLite — a static configuration entity read at runtime.

| Field | Description |
|---|---|
| `accounts.*.name` | Human-readable brand name (e.g., "The Cynical Philosopher") |
| `accounts.*.system_prompt` | System prompt enforcing voice and themes |
| `accounts.*.caption_rules` | `max_sentences` (3), `tone`, style note |
| `accounts.*.reel_rules` | `max_words` (7), style note |
| `accounts.*.hashtag_count` | Target hashtag count (12) |
| `accounts.*.base_hashtags` | Mandatory leading tags |

**Validation**: `account_key` on a Post must resolve to an existing persona;
generation fails fast otherwise (`PersonaNotFoundError`).

## Draft (derived, not stored as a table)

A Draft is the latest generated content on a Post (`reel_text`, `caption`,
`hashtags`). Regenerating overwrites these fields — never appends. Only the
latest draft can be shown for approval and, once approved, published.

## Approval Decision (recorded on the Post)

The operator's last action before a state change: approve / regenerate /
discard / timeout. Timeout leaves the post in `AWAITING_APPROVAL` (never
auto-publishes). The decision is implicit in the recorded transition plus the
stored fields; no separate table is required at this scale.