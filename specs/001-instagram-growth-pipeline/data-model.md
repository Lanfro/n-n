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
| `created_at` / `updated_at` | text (ISO-8601 UTC) | Timestamps | auto-managed |

**Relationships**: a Post belongs to exactly one account (`account_key`); it may
have one "current" draft (the latest generated content); it references at most
one approved decision gate.

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