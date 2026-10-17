# Contract: Meta Graph API — Instagram Reels Publishing

Official publishing channel for Reels. Same two-step container flow as images
(see `specs/001-instagram-growth-pipeline/contracts/meta-graph-api.md`) but the
container is a **Reel** and, unlike images, the video must be **polled** until
processing finishes before publishing. This is the ONLY permitted automation path
for Instagram (Constitution I).

## Step 1 — Create Reel container

```
POST https://graph.facebook.com/v{version}/{instagram_user_id}/media
Content-Type: application/json
```

Params:
- `media_type` — `REELS` (required for a Reel container).
- `video_url` — a public URL where Meta can fetch the clip (R2/S3 via `MediaHost`).
- `caption` — full caption text **including the AI-generated disclosure** and hashtags.
- `share_to_feed` — boolean (configurable; default `true`).
- `access_token` — token with `instagram_content_publish` scope.

**Success** (200): `{"id": "17857..."}` — the container id.

**Failure**: non-JSON or missing `id` → `MetaPublisherError` with server status +
body preview.

## Step 2 — Wait for processing (video only)

```
GET https://graph.facebook.com/v{version}/{container_id}?fields=status_code,status&access_token={token}
```

Poll until `status_code == "FINISHED"`, with a bounded timeout and backoff.

| `status_code` | Action |
|---|---|
| `IN_PROGRESS` / `EXPIRED`(-ish transient) | keep polling (bounded) |
| `FINISHED` | proceed to publish |
| `ERROR` | record the platform error → post `FAILED`, retryable |

**Failure**: timeout while not `FINISHED`, or `ERROR` → `MetaPublisherError`
carrying the platform `status` detail.

## Step 3 — Publish container

```
POST https://graph.facebook.com/v{version}/{instagram_user_id}/media_publish
```

Params: `creation_id` (container id), `access_token`.

**Success** (200): `{"id": "17833..."}` — the published media id (stored as
`ig_media_id`).

**Failure**: missing `id` → `MetaPublisherError`.

## Dry-run mode

When `dry_run=true` (default via config, `--dry-run` flag, or constructor), **no
endpoint is hit**:
- container id := `container_dry_{brief_id}`
- poll is skipped (treated as immediately `FINISHED`)
- media id := `media_dry_{container_id}`

No request leaves the machine under dry-run. This is the single-most important
safety default (Constitution § Development Workflow).

## Rules & limits

- Containers expire after 24 h; publish promptly after `FINISHED`.
- ~25 API-published posts per 24 h per IG user; a Reel counts once.
- Media URL must be publicly fetchable and stay valid until processing completes.
- Reels cannot appear in carousels; `share_to_feed` controls feed duplication.
- AI-generated content MUST be disclosed in the caption (FR-016); Meta's own
  auto-labelling is independent and not relied upon.

## Configuration

`meta.*` in `config/config.yaml`: `api_version`, `graph_base_url`, `access_token`,
`instagram_user_id`, `dry_run`. Reel-specific publish behaviour (`share_to_feed`)
lives under `reel.publish.*` or `meta.reels.*`. Secrets only via
`config/config.local.yaml` or env vars (Constitution § Security).
