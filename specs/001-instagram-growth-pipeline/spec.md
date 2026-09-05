# Feature Specification: Exotic Shorthair Instagram Growth Engine

**Feature Branch**: `001-instagram-growth-pipeline`

**Created**: 2026-09-05

**Status**: Draft

**Input**: User description: "Grow two Exotic Shorthair Instagram accounts from ~400 to 1,000+ followers in 6 months by turning raw cat photos into brand-voiced, human-approved posts published through the official Instagram integration."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Publish an approved photo post (Priority: P1)

The operator drops a fresh photo of one of the cats into the system and, within
minutes, receives a ready-to-post draft written in that cat's voice. The
operator reviews it and taps "approve"; the post goes live on the correct
account. This is the core loop that keeps the accounts fed with unique,
on-brand content.

**Why this priority**: Without a frictionless publish loop there are no posts,
and without posts there is no growth. This is the minimal slice that delivers
everyday value.

**Independent Test**: Can be fully tested by submitting one photo and confirming
the operator can approve and land a live post on the intended account.

**Acceptance Scenarios**:

1. **Given** a valid photo submitted to the system, **When** the operator
   submits it, **Then** a complete draft (short hook, caption, hashtags) is
   produced within 10 minutes.
2. **Given** a produced draft, **When** the operator approves it, **Then** the
   post goes live on the selected account and the operator sees confirmation.
3. **Given** a produced draft, **When** the operator has not approved it, **Then**
   nothing is published under any circumstance.

---

### User Story 2 - Distinct brand voices for two cats (Priority: P2)

The operator manages two separate accounts with two very different
personalities. The same photo can be published to either account, but each
account must always sound like itself - one dry and sarcastic, the other
dramatic and existential. The operator selects which cat the post belongs to
before content is written.

**Why this priority**: Persona consistency is the brand moat and the retention
engine behind the growth target; it differentiates the two accounts' audiences.

**Independent Test**: Can be tested by submitting the same photo for both
accounts and confirming each draft follows its own defined tone and text rules.

**Acceptance Scenarios**:

1. **Given** a photo, **When** the operator selects Cat 1, **Then** the draft
   matches Cat 1's sarcastic, concise tone with its defined hashtag set.
2. **Given** a photo, **When** the operator selects Cat 2, **Then** the draft
   matches Cat 2's dramatic, expressive tone with its defined hashtag set.
3. **Given** any published post, **When** inspected, **Then** it can be traced
   back to exactly one brand voice, never a blend.

---

### User Story 3 - Keep a consistent posting cadence (Priority: P3)

Beyond individual posts, the operator wants the accounts fed on a predictable
schedule (roughly 4-5 short videos and 1 carousel per week per account) so the
platform algorithm learns the niche and follower growth compounds.

**Why this priority**: Volume and consistency drive the growth curve; but the
system must remain useful for manual, occasional use before this is built.

**Independent Test**: Can be tested by confirming the operator can prepare a
batch of posts for scheduled release and that the system publishes each at its
planned time without skipping human approval.

**Acceptance Scenarios**:

1. **Given** a batch of approved posts, **When** their planned release times
   arrive, **Then** each is published without any new approval being required.
2. **Given** a scheduled post, **When** its release time arrives but the draft
   was never approved, **Then** it is skipped and surfaced to the operator.
3. **Given** a published schedule, **When** the period ends, **Then** the
   operator can see how many posts each account actually published.

---

### Edge Cases

- What happens when the media cannot be understood (corrupt file, unsupported
  format, or the local analysis service is unavailable)?
- What happens when the operator takes no action on a draft within a reasonable
  window?
- What happens when an approved post fails to publish (network or platform
  error)?
- What happens when the operator approves and then regenerates a draft - which
  version is published?
- What happens when the same photo is processed for both accounts at once?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The operator MUST be able to submit a photo for processing on a
  chosen account.
- **FR-002**: The system MUST automatically derive a usable description of each
  submitted photo.
- **FR-003**: The system MUST produce a complete draft for every processed
  photo: a short on-screen hook, a caption, and a set of hashtags.
- **FR-004**: The draft MUST always follow the selected account's brand voice,
  including its hook length limit (under 8 words), caption length limit
  (max 3 sentences), and target hashtag count.
- **FR-005**: The system MUST present every draft to the operator for approval
  before any public action on that post.
- **FR-006**: The operator MUST be able to approve, regenerate, or discard any
  draft.
- **FR-007**: The system MUST publish only drafts that were explicitly approved
  by the operator for that exact post.
- **FR-008**: The system MUST record, for every post, its full lifecycle state
  and the final decision taken, so no post ever appears publicly without an
  approval trail.
- **FR-009**: The system MUST never skip, auto-approve, or silence-fail the
  approval step for any public action.
- **FR-010**: When a post fails at any stage, the system MUST mark it failed and
  show the operator a clear reason, with a way to retry without repeating work.
- **FR-011**: The system MUST support two independent brand voices whose tone
  and content rules are defined separately and enforced per account.
- **FR-012**: When the operator takes no decision within a configured timeout,
  the system MUST leave the post unresolved (not publish, not discard) and
  notify the operator.

### Key Entities *(include if feature involves data)*

- **Post**: A submitted piece of media tracked through its lifecycle
  (pending → awaiting approval → approved → published / failed / discarded).
  A post belongs to exactly one account.
- **Brand Voice (Persona)**: The identity and content rules for an account
  (Cat 1 Cynical Philosopher, Cat 2 Dramatic Introvert). Defines tone, hook
  length, caption length, and target hashtags.
- **Draft**: The generated content attached to a post (hook + caption +
  hashtags). Regenerating replaces the draft; only the newest draft can be
  approved.
- **Approval Decision**: The operator's recorded action on a post (approve,
  regenerate, discard, or none/timeout). Every approved publication references
  its decision.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The operator can go from submitting a photo to a live post in
  under 10 minutes when they approve immediately.
- **SC-002**: 100% of published posts have a recorded human approval - zero
  unauthorized publications over the measurement window.
- **SC-003**: Each of the two accounts grows from ~400 to at least 1,000
  followers within 6 months of sustained use.
- **SC-004**: At least 70% of generated drafts are approved on first review
  (regeneration rate under 30%), indicating drafts are on-brand and usable.
- **SC-005**: Zero account safety incidents (bans, restrictions, or flagged
  posts) attributable to the automation across the 6-month window.
- **SC-006**: A failed post can be retried by the operator without redoing
  analysis or rewriting from scratch.

## Assumptions

- The operator runs the system on their own computer with a local AI analysis
  and writing service (the project's zero-subscription, private-by-design
  approach).
- Both Instagram accounts are already converted to Creator accounts and
  connected for official, authorized publishing (growth plan Phase 1).
- The only permitted automation channel for publishing is the official
  integrated Instagram channel provided by the platform; browser automation and
  unofficial tools remain out of scope and are prohibited by project rules.
- Human approval is a hard gate; unattended, fully automatic publishing is out
  of scope.
- v1 covers still-image posts; short-video (Reels) publishing and carousel
  publishing are treated as separate follow-on features.
- The growth plan targets 4-5 short videos and 1 carousel per week per account;
  this baseline spec covers the post pipeline that both formats will build on.
- Temporary network or service interruptions occur; the operator can retry
  failed posts manually.