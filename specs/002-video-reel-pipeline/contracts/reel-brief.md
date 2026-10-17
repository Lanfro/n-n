# Contract: Reel Brief (production order)

The brief is the handoff between the planner and the producer. It is a plain
JSON file written for each shot; it is the *only* input the assisted producer
(or a human) needs to produce one clip. Schema mirrors
[data-model.md § ReelBrief](../data-model.md).

## Location & naming

```
data/generated/<set>/reel_brief_<NN>.json      # one per shot, NN = shot_index zero-padded
data/generated/<set>/manifest.json             # list of briefs + produced clips
```

## Schema

```json
{
  "brief_id": "parallel_lives:0",
  "set": "parallel_lives",
  "shot_index": 0,
  "account_key": "cat_1",
  "cat": "nero",
  "motion_prompt": "the cat blinks slowly, licks a paw and glances toward the camera; subtle head movement, soft natural light",
  "source_photo_path": "data/vault/202609/ab12cd34ef56.jpg",
  "source_vault_media_id": 42,
  "expected_output": "reel_00.mp4",
  "caption": "Coffee in Paris tastes different when you have fur.",
  "reel_text": "Paris mornings hit different",
  "hashtags": ["cat", "cats", "catsofinstagram", "...", "..."],
  "aspect": "9:16",
  "duration_seconds": 5,
  "created_at": "2026-10-17T09:12:44Z"
}
```

## Rules

- **Source photo**: `source_photo_path` MUST exist and be a real, subject-labelled
  vault photo of `cat` at production time (FR-002). If it is missing, the producer
  skips that brief and reports it without aborting the batch.
- **Motion prompt**: describes motion (blinks, licks, turns, walks), not a still
  scene; the planner derives it from the set's scene plus explicit motion cues.
- **Expected output**: `expected_output` is the exact filename the producer must
  produce/download, and the same name a human must use when dropping a clip in by
  hand (FR-009). The producer treats a present, non-empty file as "already
  produced" and never regenerates it (FR-007).
- **Text rules**: `reel_text` < 8 words, `caption` <= 3 sentences, 10–12 hashtags,
  all per persona rules (FR-003). Hashtags carry no leading `#`.
- **Human usability**: the JSON is self-contained and readable so a person can
  execute the brief without the automated producer (FR-004).

## Idempotency

Re-running the planner for the same set/shot writes the same `brief_id` and
`expected_output`. Existing clips are never overwritten or regenerated.
