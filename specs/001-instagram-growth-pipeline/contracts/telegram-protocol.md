# Contract: Telegram Approval Gateway

Human-in-the-loop surface. Sends the draft (photo + caption) to an approved
chat and waits for one decision. Falls back to a CLI prompt when Telegram is
unavailable. Also carries the **vault channel** archive + sync responsibilities
(document below).

## Part A — Approval Gateway

### Prerequisites (primary mode)

- `telegram.bot_token` configured.
- `telegram.allowed_chat_ids` — non-empty list of numeric chat ids.

### Flow

1. Alert the operator (sound + optional Telegram notification) that a decision
   is required, then block.
2. Bot sends the draft photo with caption + inline buttons:
   - `Approve & Schedule`
   - `Regenerate`
   - `Discard`
3. Also handle slash commands as equivalent inputs: `/approve`, `/retry`,
   `/discard`.
4. Poll in-memory decision cache until a decision arrives or the timeout
   (`decision_timeout_seconds`, default 1800) expires.

## Decision outcomes

| Action | Meaning | Post transition |
|---|---|---|
| `approve` | Human-approved | -> `APPROVED` |
| `retry` | Regenerate draft | content re-run, stays/re-enters `AWAITING_APPROVAL` |
| `discard` | Reject content | -> `REJECTED` |
| `timeout` | No decision in window | stays `AWAITING_APPROVAL` (never auto-publishes) |

## Fallback: CLI approval

Triggers when no `bot_token`, empty `allowed_chat_ids`, or a Telegram error:

```
POST #<id> awaiting approval
Media : data/input_media/<file>
Caption: <caption ...>
Choose [a]pprove / [r]etry / [d]iscard:
```

Maps `a/r/d` (case-insensitive prefix) to the same outcomes as above with
`source: cli`.

## Dry-run / automation

`auto_approve(post_id)` transitions straight to `APPROVED` with no network
interaction. Used only by `--dry-run` smoke testing; never reached in a real
publish path.

## Configuration

`telegram.*` in `config/config.yaml` — `bot_token`, `allowed_chat_ids`,
`poll_interval`, `decision_timeout_seconds`. Secrets only via
`config/config.local.yaml` (Constitution § Security).

---

## Part B — Vault Channel (archive + sync)

### Role

Storage/archive + training corpus for all cat pictures. **Never** a source of
Meta publishing URLs (that is the R2/S3 `MediaHost`). Dedicated bot token
(`vault.telegram.bot_token`) to avoid `getUpdates` conflicts with the approval
bot.

### Archive (upload)

- `sendDocument` (never `sendPhoto` — sendPhoto recompresses/strips EXIF).
- Caption: `sha256:<hex-hash>` (64 hex chars).
- On success store `telegram_file_id` + `telegram_message_id` on the
  `vault_media` row.
- Dedup: an existing `sha256` row short-circuits the upload.
- On Telegram error: log + raise `VaultArchiveError`; the post still proceeds
  (archive is best-effort, publishing is not blocked by archive failure) —
  subject to configurable `vault.telegram.required` (default false).

### Sync (operator hand-adds "to teach the model")

- One-shot `getUpdates` poll restricted to `channel_post` updates.
- Persist `last_update_id` offset in the `channel_sync` table (single row) for
  idempotency.
- For each new message carrying photos/documents: download via `getFile`,
  verify downloaded size matches, ingest via `vault.ingest(..., source="telegram")`.
- Triggered by `--sync-vault` and automatically at the start of every submit.

### Configuration

<table>
<tr><th>Key</th><th>Meaning</th></tr>
<tr><td><code>vault.telegram.chat_id</code></td><td>Numeric channel id (captured once via <code>--resolve-chat</code>)</td></tr>
<tr><td><code>vault.telegram.bot_token</code></td><td>Dedicated vault bot token, gitignored</td></tr>
<tr><td><code>vault.telegram.required</code></td><td>Fail pipeline if archive upload fails (default false)</td></tr>
</table>