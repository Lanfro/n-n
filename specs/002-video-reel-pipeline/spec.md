# Feature Specification: AI Video Reel Pipeline from Real Cat Photos

**Feature Branch**: `002-video-reel-pipeline`

**Created**: 2026-10-17

**Status**: Draft

**Input**: User description: "Abandon static AI image generation. Design and build an AI video Reel pipeline: animate real cat photos from the vault into vertical (9:16) short-form videos using an assisted, semi-automatic browser workflow on a free third-party AI video service, then validate and publish them to Instagram as Reels through the official platform API after human approval."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Plan a Reel from a real cat photo (Priority: P1)

The operator asks the system to prepare a Reel for a chosen creative set. For each shot in the set, the system selects a real photo of the correct cat from the photo vault and produces a self-contained "Reel brief": a motion prompt describing how the cat should move, a reference to the source photo, the exact filename the finished video must have, and the ready-to-use caption, on-screen hook and hashtags. The briefs are human-readable and can be handed to either the assisted generator or a person who will generate the video by hand.

**Why this priority**: Without a reliable, identity-consistent brief there is nothing to animate or publish. This is the smallest independently valuable slice: it turns an existing photo plus a creative idea into an actionable production order.

**Independent Test**: Run the planning step for a set and confirm that one brief per requested shot is written to disk, referencing an existing cat photo, with a motion prompt, an expected output filename, and caption/hook/hashtags present. Delivers value on its own: a person can take the briefs and produce videos manually.

**Acceptance Scenarios**:

1. **Given** a creative set with several scenes and a vault containing labelled photos of the target cats, **When** the operator runs the planning step, **Then** the system writes one brief per shot, each referencing an existing source photo of the correct cat and a distinct expected output filename.
2. **Given** the operator limits the number of shots for the run, **When** planning completes, **Then** the number of briefs does not exceed the requested limit and never exceeds the number of scenes in the set.
3. **Given** a scene whose target cat is one of the two accounts' cats, **When** the brief is created, **Then** the motion prompt and the caption use that cat's persona voice and respect the on-screen hook word limit.
4. **Given** a vault with no labelled photos for the required cat, **When** the operator runs the planning step, **Then** the system reports the missing subject clearly instead of producing a brief with no source photo.

---

### User Story 2 - Produce the Reel video via assisted generation (Priority: P2)

The operator runs the production step. The system drives an already-authenticated browser session on a free AI video service to animate each source photo into a short vertical clip according to its brief, then downloads the finished clips into the working set folder. If the service presents a login challenge, an anti-bot check, or the free daily allowance is exhausted, the system notifies the operator with an alert and pauses gracefully rather than failing the whole batch; the operator can complete that clip by hand and the pipeline resumes.

**Why this priority**: This is the step that actually creates video. It is separable from publishing and its value can be demonstrated by a folder of finished clips.

**Independent Test**: With a prepared brief and an authenticated session, run production for a single clip and confirm an mp4 matching the expected filename appears in the set folder with vertical orientation. Simulate a challenge/limit condition and confirm an operator notification is raised and the batch pauses without crashing.

**Acceptance Scenarios**:

1. **Given** prepared briefs and an authenticated browser session, **When** the operator runs production, **Then** the system submits each brief's photo and motion prompt to the service and downloads the resulting clip under the brief's expected filename.
2. **Given** some expected clips already exist in the set folder, **When** production runs, **Then** only missing clips are generated and existing clips are left untouched.
3. **Given** the service shows a login or anti-bot challenge, **When** production encounters it, **Then** the system alerts the operator and stops cleanly, preserving already-produced clips.
4. **Given** the free daily allowance is exhausted, **When** production requests another clip, **Then** the system reports the limit reached and pauses for the next window instead of retrying endlessly.
5. **Given** automation cannot complete a clip for any reason, **When** the operator manually drops a correctly named clip into the set folder, **Then** the clip is treated as produced and the pipeline continues.

---

### User Story 3 - Validate, approve and publish the Reel to Instagram (Priority: P3)

The operator asks the system to publish a produced clip. The system validates the clip against Instagram's Reel requirements, repairs minor issues where possible, presents the clip to the operator for approval (with a viewable preview), and — only after explicit approval — publishes it as a Reel through the official Instagram API and records the result. Any failure is recorded against the post and can be retried.

**Why this priority**: Publishing completes the loop and is the source of business value, but it depends on P2 having produced a valid clip. It is the last independently testable slice.

**Independent Test**: Take a produced clip that meets the requirements, run the publish step, confirm the operator receives an approval request with a preview, and confirm that on approval the post is published as a Reel and its platform identity and status are recorded. Confirm that an out-of-spec clip is rejected before any approval request is raised.

**Acceptance Scenarios**:

1. **Given** a produced clip that meets the platform's Reel requirements, **When** the operator runs publish, **Then** the clip is validated and an approval request showing the video is sent to the operator.
2. **Given** the operator approves the request, **When** approval is registered, **Then** the clip is published as a Reel through the official API and the post status advances to published with the platform media identity stored.
3. **Given** the operator rejects or does not respond in time, **When** the approval window closes, **Then** nothing is published and the post is left in a non-published state that can be retried.
4. **Given** a clip that is the wrong orientation, too short, or in an unsupported format, **When** publish is attempted, **Then** the system refuses to submit it for approval and explains which requirement failed.
5. **Given** the platform is still processing the video after submission, **When** the system polls the publish status, **Then** it waits until processing finishes before considering the Reel published, and reports an error if processing fails.
6. **Given** a previously failed publish, **When** the operator retries, **Then** the system re-validates the clip and repeats the approval-gated publish without creating a duplicate post.

---

### Edge Cases

- **Source photo unavailable**: a brief's source photo is later deleted or moved before production → the system reports the missing source and skips that clip without aborting the batch.
- **Video without an audio track**: a generated clip has no audio → the system adds a silent audio track so it meets the platform requirement.
- **Clip below the minimum resolution**: free-tier output may be lower resolution than recommended → the system either raises it to the minimum accepted resolution or rejects it, and never publishes below the platform minimum.
- **Oversized clip**: the clip exceeds the platform's maximum file size → the system reports it and does not submit.
- **Aspect ratio off**: the clip is not vertical 9:16 → the system attempts a compliant crop only when the source safely allows it, otherwise rejects.
- **Watermarked output**: free-tier clips may carry a visible watermark → accepted for v1, disclosed to the operator at approval time.
- **Repeated container failure**: the platform reports a failed/errored processing status → the post is marked failed with the platform error and can be retried.
- **Duplicate publish attempt**: a retry after a partial success must not create a second Reel for the same brief.
- **Session logged out mid-batch**: the authenticated session expires between clips → the system pauses and notifies the operator rather than looping on failures.
- **Same clip already published**: the operator tries to publish a clip already recorded as published → the system warns and does not publish again.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST prepare, for each requested shot of a creative set, a Reel brief containing: a motion prompt, a reference to a real source photo of the correct cat, the exact expected output filename, and the caption, on-screen hook and hashtags.
- **FR-002**: Each brief's source photo MUST be an existing, real photo of the correct cat taken from the local photo vault.
- **FR-003**: The on-screen hook in every brief MUST respect the project's hook word limit, the caption MUST respect the project's sentence limit, and the hashtag count MUST respect the project's persona rules.
- **FR-004**: The system MUST produce briefs in a human-readable, self-contained form so that a person can execute a brief manually without the automated generator.
- **FR-005**: The system MUST drive video generation through an already-authenticated browser session on a free third-party AI video service, requiring the operator to authenticate only once per session lifetime.
- **FR-006**: The system MUST NOT automate the Instagram website; publishing MUST use only the official Instagram publishing API.
- **FR-007**: The system MUST generate only the clips that are missing, leaving already-present clips in the set folder untouched.
- **FR-008**: On encountering a login challenge, an anti-bot check, or exhaustion of the free daily allowance, the system MUST alert the operator and pause cleanly, preserving progress, instead of failing the batch or retrying unboundedly.
- **FR-009**: The system MUST accept a correctly named clip that the operator placed manually in the set folder as an equivalent to an automatically generated clip.
- **FR-010**: The system MUST validate every clip against the platform's Reel requirements — container format, video and audio codecs, aspect ratio, minimum resolution, duration range, and maximum file size — before the clip is eligible for publishing.
- **FR-011**: The system MUST repair a clip that is fixable without changing its composition (at minimum: ensuring a present audio track and meeting the minimum resolution) and MUST reject a clip that cannot be made compliant.
- **FR-012**: The system MUST present every publishable clip to the operator for explicit approval, including a viewable preview of the video, before publishing.
- **FR-013**: The system MUST publish an approved clip as an Instagram Reel exclusively through the official publishing API, then record the platform's media identity and final status.
- **FR-014**: The system MUST wait for the platform to finish processing a submitted Reel and MUST treat an error processing status as a failure that is recorded and retryable.
- **FR-015**: The system MUST NOT publish any clip that has not passed validation and received explicit approval, and MUST NOT create a duplicate Reel when a publish is retried.
- **FR-016**: The published Reel's caption MUST include disclosure that the content was generated with AI.
- **FR-017**: The system MUST record, per Reel post, the account, the clip used, the caption, the current status, the platform identity, and the time of the last update.
- **FR-018**: The feature MUST replace the previous static AI image generation path; offering static AI image sets MUST no longer be part of the operator interface after this feature ships.
- **FR-019**: The system MUST surface, at the moment of approval, any known quality caveat of the free-tier output (such as a watermark) so the operator decides with full information.
- **FR-020**: The system MUST retain the existing human approval gate and status tracking, using the same approval channel and the same non-published-by-default guarantee.

### Key Entities *(include if feature involves data)*

- **Reel Brief**: A production order for one shot. Represents the intent to turn a specific real cat photo into a specific short video. Key attributes: target account, target cat, motion prompt, source photo reference, expected output filename, caption, on-screen hook, hashtags, and the creative set/scene it came from.
- **Generated Clip**: A produced video file for a brief. Key attributes: file location, orientation, resolution, duration, file size, audio presence, validation outcome (valid / repaired / rejected), and any quality caveat (e.g. watermark).
- **Reel Post**: The publishable unit tracked by the system. Key attributes: account, associated clip and brief, caption, approval state, publication status, platform identity (submitted container and final media), platform error if any, and timestamps.
- **Browser Session**: The operator-authenticated session on the third-party video service. Key attributes: authentication state and the ability to be reused across production runs without re-authenticating (unless it expires).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The operator can produce a publishable Reel with no more than two manual interactions per Reel beyond an initial one-time login — one approval action and, if automation is blocked, one manual video drop.
- **SC-002**: 100% of published Reels pass validation before submission; zero clips that fail the platform requirements reach the approval stage as publishable.
- **SC-003**: After a one-time login, at least 70% of planned clips are produced automatically without any manual intervention in a normal operating window.
- **SC-004**: Planning a full set of briefs completes in under 3 minutes of system activity.
- **SC-005**: No Reel is ever published without explicit human approval, and no duplicate Reel is created for the same brief.
- **SC-006**: When a login/anti-bot challenge or a daily-allowance limit occurs, the operator is alerted within one minute and no partial/failed clip is recorded as produced.
- **SC-007**: A failed publish can be retried and either succeed or report a clear, actionable platform error 100% of the time.

## Assumptions

- A free AI image-to-video service with a browser interface is available for personal use, and the operator accepts the risk that automated/assisted use may violate that service's terms and could lead to the free account being limited or banned.
- The free tier will typically yield short clips (roughly 5–10 seconds) and possibly lower resolution and a visible watermark; these are accepted for the initial version.
- Generated clips may lack an audio track; adding a silent track is acceptable, and any background music is added manually in the Instagram app after publishing where desired.
- The version-one Reel is a single vertical clip; stitching multiple clips into a longer Reel is out of scope.
- The Instagram account is a professional account already able to publish content through the official API.
- Photos of both cats exist in the local vault and are labelled by subject; the creative set definitions and the persona text rules already in the project are reused.
- The free service imposes a daily allowance that effectively caps throughput at roughly one to two clips per account per day; the pipeline is designed to pause and resume rather than to fight the limit.
- The feature uses the project's existing approval channel for both operator alerts and the publish approval.
- Local media inspection and normalization capability (checking and adjusting video files) is available in the operating environment.

## Dependencies

- The existing photo vault, subject labels, creative set definitions and persona text rules.
- The existing human-approval channel and the publish status tracking already used for photo posts.
- The existing official Instagram publishing integration, extended to support Reels.
- The existing media hosting used to expose a file to the platform by public URL.
- A free third-party AI image-to-video service reachable through a browser.
- Local video inspection and normalization tooling in the operating environment.
