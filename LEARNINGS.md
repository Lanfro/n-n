# LEARNINGS.md — Environment Quirks & Resolved Gotchas

Resolved environment/setup issues so they are not rediscovered. Append each new
entry with its date in reverse-chronological order.

## 2026-09-05

- **PowerShell execution policy blocks `.ps1` scripts**: any
  `.specify/scripts/powershell/*.ps1` must be invoked with
  `powershell -NoProfile -ExecutionPolicy Bypass -File <script>` from the repo
  root. Plain `.\script.ps1` or `& script.ps1` can be blocked by policy.
- **specify-cli install tag**: the tool is installed from
  `git+https://github.com/github/spec-kit.git@v1.0.4` — the tag has a `v`
  prefix; `@1.0.4` fails.
- **`git push` works while `gh` reports not logged in**: Git Credential
  Manager holds cached credentials, so plain `git` is preferred over `gh`.
- **uv tool binaries are not on PATH by default**: use `uv run <tool>` or call
  the binary directly from `%USERPROFILE%\.local\bin`.
- **Config precedence**: `config/config.local.yaml` deep-merges over
  `config/config.yaml` (see `_merge_deep` in `main.py`). Secrets and local
  overrides belong only in the gitignored local file.
- **Ollama down is graceful**: the pipeline catches `OllamaUnavailableError`,
  marks the post `FAILED`, stores the reason in `meta_error`, and exits 1.
- **Meta dry-run default**: `meta.dry_run` defaults to `true` — live publishing
  needs an explicit override. When dry-run is off, the media URL must be
  publicly fetchable (R2/S3 via `src/vault/media_host.py`); a local `file://`
  URI fails loudly.
- **Telegram approval bot lifecycle**: `TelegramGateway._request_via_telegram`
  builds a short-lived `Application` per approval request. The vault uses a
  **dedicated bot token** so its long-polling (`--sync-vault`) never collides
  with the approval flow's `getUpdates` (Telegram enforces one consumer per
  token).
- **python-telegram-bot: `Application.start()` does NOT poll.** In PTB >=20
  `start()` only wires the update queue; you must also run
  `await application.updater.start_polling(...)` or the inline approval buttons
  silently do nothing (observed: 30-min approval window expired, post left
  `AWAITING_APPROVAL`, zero `getUpdates` requests in logs). Also requires the
  `[callback-data]` extra (`cachetools`) for `arbitrary_callback_data(True)`;
  without it `.build()` fails at runtime ("To use `CallbackDataCache`...").