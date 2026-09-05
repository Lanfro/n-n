# Contract: Media Host (R2 / S3) — publish-time URLs

Provides the **publicly fetchable media URL** the Meta Graph API needs in
`image_url`. The Telegram vault is deliberately NOT a source for these URLs.

## Interface (`src/vault/media_host.py`)

```python
class MediaHost(ABC):
    backend: str  # "none" | "r2" | "s3"
    def upload(self, local_path: str | Path, *, key_prefix: str = "") -> str | None:
        """Upload and return a public URL. Returns None when backend is none."""
    def delete(self, object_key: str) -> bool: ...
    def is_configured(self) -> bool: ...
```

`None` from `upload()` means "not configured" → the publisher keeps its
fail-loud `file://` placeholder so a real publish can never silently emit a broken
image.

## Backends

| Backend | Behavior |
|---|---|
| `none` | `upload()` returns `None` (dry-run / unconfigured default) |
| `r2` | `boto3` client with `endpoint_url=https://<account_id>.r2.cloudflarestorage.com`; key = `media/<sha256[:2]>/<sha256[:12]>.ext`; returns `public_base_url + key` |
| `s3` | same boto3 path with user-supplied endpoint/bucket/region |

## Configuration (`config.local.yaml`, gitignored)

```yaml
vault:
  host:
    backend: r2            # none | r2 | s3
    bucket: cat-agent-vault
    public_base_url: "https://vault.example.dev"   # no trailing slash
    endpoint_url: "https://<account_id>.r2.cloudflarestorage.com"
    access_key_id: ""      # gitignored
    secret_access_key: ""  # gitignored
```

## Fail-fast rule

If `backend != none` and any of `bucket`/`endpoint_url`/`access_key_id`/
`secret_access_key`/`public_base_url` is missing → raise `MediaHostConfigError`
at construction (do not publish with a half-configured host).

## Dry-run

`--dry-run` skips upload entirely; the publisher ignores `image_url`.