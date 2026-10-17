# Contract: Reel Clip Requirements & Validation

Every produced clip MUST pass validation before it becomes eligible for approval
and publishing (FR-010). Validation is performed with `ffprobe`; repairs use
`ffmpeg`. This contract is the single source of truth for "publishable clip".

## Target platform (Instagram Reels) requirements

| Property | Requirement |
|---|---|
| Container | MP4 (MOV accepted by platform; MP4 preferred) with `moov` at front when possible |
| Video codec | H.264 (progressive, closed GOP, 4:2:0) |
| Audio codec | AAC, <= 48 kHz, mono or stereo |
| Aspect ratio | 9:16 (platform accepts 1.91:1 … 9:16; we require portrait 9:16) |
| Resolution | Minimum accepted 540×960; **target 1080×1920**; >= 720p recommended |
| Frame rate | >= 23 FPS (>= 30 recommended) |
| Duration | 3 s minimum, 15 min maximum; **5–90 s to appear in the Reels tab** |
| File size | <= 300 MB (conservative; platform allows more) |
| Audio track | MUST be present (a silent track is acceptable) |

## Validation outcome

| Outcome | Meaning | Effect |
|---|---|---|
| `valid` | Meets all requirements, no change needed | Eligible for approval |
| `repaired` | Meets requirements after an allowed repair | Eligible for approval; caveat recorded |
| `rejected` | Cannot be made compliant | **Never** offered for approval; reason recorded |

## Allowed repairs (ffmpeg, composition-preserving)

1. **Missing audio** → mux a silent AAC track (`anullsrc`) so the audio
   requirement is met. Caveat: none (silence is expected on free tier).
2. **Below minimum resolution** → upscale to the minimum accepted resolution
   (540×960) using a quality-preserving scaler. Caveat: `upscaled`.

No other modification is allowed. In particular, do **not** crop, stretch, or
re-time a clip to force compliance.

## Rejection conditions

- Unsupported container/codec that cannot be remuxed without re-encoding the video.
- Not portrait / cannot satisfy a 9:16 crop safely.
- `duration < 3 s` or `duration > 15 min`.
- `size_bytes > 300 MB`.
- Resolution still below 540×960 after repair.
- Corrupt/unreadable media (ffprobe failure).

## Operator caveats (surfaced at approval, FR-019)

- `watermark` — the free tier burned in a visible watermark.
- `upscaled` — the clip was below native resolution and was upscaled.
- `silent` — no audio present; a silent track was added (music can be added
  manually in the Instagram app afterwards).

## Configuration (`config/config.yaml`, `reel.*`)

```yaml
reel:
  aspect: "9:16"
  min_resolution: [540, 960]
  target_resolution: [1080, 1920]
  min_duration_seconds: 3
  max_duration_seconds: 900
  preferred_min_duration_seconds: 5
  max_size_mb: 300
```

## Tooling

- `ffprobe` read fields: container/format, `codec_type` streams, `codec_name`,
  width/height, `r_frame_rate`, `duration`, `size`.
- `ffmpeg` used only for the two allowed repairs above.
- If `ffprobe` is unavailable, clips are treated as `rejected` with a clear
  "media inspection unavailable" reason (fail safe, never publish unverified).
