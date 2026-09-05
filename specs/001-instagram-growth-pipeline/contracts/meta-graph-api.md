# Contract: Meta Graph API (Instagram Creator API)

Official publishing channel. Two-step flow: create a media container, then
publish it. This is the ONLY permitted automation path (Constitution I).

## Step 1 — Create media container

```
POST https://graph.facebook.com/v21.0/{instagram_user_id}/media
Content-Type: application/json
```

Params:
- `image_url` — a public URL where Meta can fetch the image.
- `caption` — full caption text including hashtags.
- `access_token` — page/account token with `instagram_content_publish` scope.

**Success** (200): `{"id": "17857..."}` — the container id.

**Failure**: non-JSON or missing `id` → `MetaPublisherError` with server
status + body preview.

> ### Local-file limitation
>
> The scaffold substitutes a `file://` URI for `image_url`. Meta cannot fetch
> that, so live container creation fails loudly rather than publishing a
> broken image. Operators MUST host media at a public URL (or swap in a real
> `image_url`) before disabling dry-run. `dry_run` is on by default.

## Step 2 — Publish container

```
POST https://graph.facebook.com/v21.0/{instagram_user_id}/media_publish
```

Params:
- `creation_id` — container id from Step 1.
- `access_token` — same token.

**Success** (200): `{"id": "17833..."}` — the published media id.

**Failure**: missing `id` → `MetaPublisherError`.

## Dry-run mode

When `dry_run=true` (default via config, `--dry-run` flag, or `MetaPublisher`
constructor), neither endpoint is hit:
- container id := `container_dry_{media_stem}`
- media id := `media_dry_{container_id}`

No request leaves the machine under dry-run.

## Configuration

`meta.*` in `config/config.yaml`: `api_version` (v21.0), `graph_base_url`,
`access_token`, `instagram_user_id`, `dry_run` — secrets only via
`config/config.local.yaml` or env vars (Constitution § Security).