# Contract: Telegram Approval Gateway

Human-in-the-loop surface. Sends the draft (photo + caption) to an approved
chat and waits for one decision. Falls back to a CLI prompt when Telegram is
unavailable.

## Prerequisites (primary mode)

- `telegram.bot_token` configured.
- `telegram.allowed_chat_ids` — non-empty list of numeric chat ids.

## Flow

1. Bot sends the draft photo with caption + inline buttons:
   - `Approve & Schedule`
   - `Regenerate`
   - `Discard`
2. Also handle slash commands as equivalent inputs: `/approve`, `/retry`,
   `/discard`.
3. Poll in-memory decision cache until a decision arrives or the timeout
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