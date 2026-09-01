# Fuzzynth project log

This is the living operational memory for the project. Read it together with
`instruction.md` and `PLAN.md` before resuming work.

## Current state

- Phase: M0 — plan and review.
- Implementation authorization: not yet granted.
- Repository: initialized from `git@github.com:Crihexe/fuzzynth.git`.
- Provider calls made: none.
- V8 source downloaded or built: no.
- Fuzzing campaigns running: none.
- Telegram development notifier: configured and tested; command/control bot not
  implemented.
- Historical PoC dataset available: no; owner is preparing it.
- Remote status: planning commit `0be3174` published to `origin/main`.

## Protected boundaries

- Work tree: `/root/fuzzynth`.
- `/root/red-sailor` is out of scope and must remain untouched.
- Provider credentials live outside the repository at
  `/root/fuzzynth_openai_credentials` and must not be logged or committed.
- Git pushes use the dedicated repository deploy key.

## Decision records

### D-001 — Parallel campaign families

- Status: proposed.
- Decision: schedule independent raw-stream, tool-driven, mutation, replay, and
  triage campaigns with separate budgets and concurrency.
- Why: enables controlled comparisons and prevents a failing or expensive lane
  from stopping the experiment.

### D-002 — Raw response is canonical

- Status: proposed.
- Decision: in continuous mode, archive and execute the assistant response bytes
  as the canonical program without a tool or JSON wrapper.
- Why: minimizes protocol tokens and latency while preserving exact evidence.

### D-003 — Sealed stream before speculative execution

- Status: proposed.
- Decision: implement byte-exact streaming capture followed by execution at
  response completion before experimenting with prefix/PTY execution.
- Why: establishes reproducible semantics and a baseline for measuring the more
  aggressive stream variants.

### D-004 — Provider model identity is probed, not assumed

- Status: proposed.
- Decision: preserve the requested `gpt-5.3-codex-spark` identifier and test it
  against the custom base URL. Do not substitute a public catalog model silently.
- Why: public documentation confirms Codex-Spark as a product capability and
  streaming for GPT-5.3-Codex, but does not currently document that exact API ID.

### D-005 — Corpus conditioning is an experiment matrix

- Status: proposed.
- Decision: compare unconditioned, rotating, retrieved, technique-local, and
  large-context historical-PoC conditioning under matched budgets.
- Why: distinguishes useful generalization from memorization and replay while
  retaining the owner's deliberate style-conditioning hypothesis.

### D-006 — Internal syntax is allowed in dedicated lanes

- Status: proposed.
- Decision: retain `%` intrinsics and useful `d8` helpers under versioned profiles,
  then annotate intentional abort primitives during triage rather than banning all
  internal syntax.
- Why: optimization intrinsics can reach valuable engine states, while profile
  separation keeps expected aborts from becoming false reports.

### D-007 — Controller and target are separate trust zones

- Status: proposed.
- Decision: only the controller receives API/Telegram credentials. Disposable
  `d8` workers have no network or secrets and run within resource limits.
- Why: generated programs are untrusted and crash collection must survive target
  failure.

### D-008 — Runtime data stays out of Git

- Status: proposed.
- Decision: do not commit V8 checkouts/builds, credentials, datasets by default,
  database state, generated corpus, or crash artifacts.
- Why: avoids secret exposure, oversized history, and accidental publication of
  security-sensitive findings.

### D-009 — M0 development notifier exception

- Status: accepted by owner.
- Decision: permit a one-way Telegram notification script during M0 while all
  campaign/provider/V8 implementation remains gated on plan approval.
- Why: gives the owner lightweight progress visibility without broadening the
  current development authorization.

## Work completed

### 2026-09-01 — Planning bootstrap

- Generated and installed a dedicated GitHub deploy key.
- Restricted the external provider credential file to owner-only permissions.
- Cloned the empty private repository into `/root/fuzzynth`.
- Configured Git locally to use the repository-specific deploy key.
- Verified Docker is present on the host; `d8`, `gdb`, and `llvm-symbolizer` are
  not currently installed on the host.
- Verified `/root/red-sailor` exists and kept it out of scope.
- Consulted official OpenAI documentation for the requested model strategy.
- Drafted project instructions, architecture, campaigns, stream semantics,
  dataset conditioning, V8 build profiles, evidence capture, cost controls,
  Telegram scope, milestones, and review questions.
- Committed and pushed the initial planning baseline as `0be3174`.

### 2026-09-01 — Telegram development notifier

- Received the external Telegram credentials file from the owner and restricted
  it to owner-only permissions (`0600`).
- Added `scripts/notify_telegram.py` with safe dotenv parsing, permission checks,
  a message-length limit, dry-run support, bounded network timeout, and redacted
  failures.
- Performed a successful dry-run and sent the requested live test message;
  Telegram returned message ID `7`.
- Documented the narrowly scoped M0 authorization and notifier usage.

## Verification performed

- No secret values were printed or added to the repository.
- No model-provider request was made.
- No V8 source or build dependency was downloaded.
- No file under `/root/red-sailor` was modified.
- Telegram notifier passed Python bytecode compilation and dry-run validation.
- Live Telegram `sendMessage` test succeeded without printing credentials or the
  configured chat ID.

## Waiting on owner

- Review and amend `instruction.md`, `PLAN.md`, and the proposed decisions above.
- Explicit approval before M1 implementation.
- Historical V8 PoC dataset when ready.
- Telegram command/control policy and allowlisted actor validation at a later
  milestone; the one-way development notifier is already configured.
- Answers to the open decisions in `PLAN.md` when convenient; reasonable defaults
  can be proposed during review.

## Next work after approval

1. Mark approved decisions accepted and record plan changes.
2. Create the minimal project skeleton and test harness.
3. Add fail-closed external credential loading with custom-base enforcement.
4. Run a strictly capped provider capability probe and record redacted results.
5. Commit, test, and push that vertical slice before beginning the V8 build.
