# Fuzzynth project log

This is the living operational memory for the project. Read it together with
`instruction.md` and `PLAN.md` before resuming work.

## Current state

- Phase: M1 setup and M2 V8 checkout/build preparation.
- Implementation authorization: granted by owner on 2026-09-01.
- Repository: initialized from `git@github.com:Crihexe/fuzzynth.git`.
- Provider calls made: none.
- V8 source: official `depot_tools`/`fetch v8` checkout started; build pending.
- Fuzzing campaigns running: none.
- Telegram development notifier: configured and tested; command/control bot not
  implemented.
- Historical PoC dataset available: no; owner is preparing it.
- Remote status: notifier commit `128cdf2` published to `origin/main`.

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

### D-010 — Dual provider boundary

- Status: accepted by owner.
- Decision: run Spark and Luna through the alternate endpoint, and Luna through
  the official OpenAI endpoint, with separate keys and no implicit fallback.
- Why: enables low-cost throughput experiments and an official control that can
  vary temperature.

### D-011 — Provider-valid parameter matrices

- Status: accepted by owner.
- Decision: omit temperature on the alternate endpoint; vary it only where
  supported. Test Luna reasoning effort and text verbosity as independent factors.
- Why: unsupported parameters must not block alternate runs, and higher reasoning
  or verbosity may trade speed/cost for useful structural diversity.

### D-012 — Context lifetime is an experiment factor

- Status: accepted by owner.
- Decision: randomize bounded dataset windows and compare fresh, short, medium,
  long, and adaptive conversation-reset policies.
- Why: frequent resets reduce context cost and mode lock-in, while long sessions
  may use accumulated feedback to push the model toward new cases.

### D-013 — Development gate lifted

- Status: accepted by owner.
- Decision: begin repository setup, bounded provider probes, and official V8
  checkout/build. Sustained fuzzing remains gated on evidence and budget controls.
- Why: the owner reviewed the initial direction and explicitly authorized work.

### D-014 — Chrome 152 V8 target

- Status: accepted by owner.
- Decision: build the exact V8 revision embedded in the latest stable Chrome 152
  release rather than moving V8 `main`.
- Why: fixes a reportable real-world browser-engine baseline while retaining an
  exact revision for reproduction.

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

### 2026-09-01 — Development authorization and dual credentials

- Owner authorized implementation and official V8 checkout/build.
- Verified, without printing values, non-empty alternate key/base URL and
  official OpenAI key fields in the external credentials file.
- Restricted the credentials file to owner-only permissions (`0600`).
- Configured future repository commits as
  `Cristian Di Nicola <cristiann.di.nicola@gmail.com>`.
- Started the official `depot_tools` plus `fetch v8` workflow under ignored
  project storage; `/root/red-sailor` remains untouched.
- Owner selected the latest stable Chrome 152 V8 revision as the build target;
  exact release and commit resolution is pending completion of official lookup.
- Sent Telegram development update message ID `8`.

### 2026-09-01 — Initial controller and experiment configuration

- Added a dependency-free Python package/CLI skeleton and project metadata.
- Added fail-closed, permission-checked loading for the alternate and official
  provider credentials with fixed official endpoint and no cross-provider
  fallback.
- Added a redacted offline `fuzzynth doctor` command.
- Added the first declarative model/provider/temperature/reasoning/verbosity and
  context-lifetime experiment matrix; unbounded campaigns remain disabled.
- Added the Chrome 152 V8 target placeholder and reproducible upstream checkout
  script.
- Added five credential-boundary unit tests and a local test script.

### 2026-09-01 — Chrome 152 resolution and provider probes

- Resolved latest Linux Stable Chrome 152 as `152.0.7977.75` through official
  Chromium release metadata.
- Pinned Chromium revision `4999cc1efed37c4d91dc4ce6ec4b0a50e2a9a8cb`
  and V8 revision `3de6ffffbfdcf265e9f11a5c9d1cfb4d486d7550`.
- Verified the pinned V8 commit exists in the official upstream repository.
- Added a bounded Responses API transport and explicit `--live` probe CLI that
  never prints generated text, keys, or endpoint values.
- Confirmed exact Spark and Luna model IDs on the alternate endpoint and Luna on
  the official endpoint.
- Observed alternate-provider coercion from requested reasoning `none` to
  effective `low`, default effective temperature `1.0` when omitted, and
  reflected high verbosity.
- Observed official Luna preserve temperature `1.3`, reasoning `none`, and high
  verbosity.
- Recorded initial redacted capability and usage observations under `docs/`.

## Verification performed

- No secret values were printed or added to the repository.
- No model-provider request was made.
- Official V8 checkout/build state is tracked separately under `.local/`, which
  is ignored by Git.
- No file under `/root/red-sailor` was modified.
- Telegram notifier passed Python bytecode compilation and dry-run validation.
- Live Telegram `sendMessage` test succeeded without printing credentials or the
  configured chat ID.
- Credential tests pass and `fuzzynth doctor` validates both providers without
  making network calls or displaying endpoint/key values.
- Eight unit tests pass after adding request omission/serialization checks.
- All live probe output was restricted to capability, latency, effective
  parameters, response state, and usage metadata.

## Waiting on owner

- Review and amend `instruction.md`, `PLAN.md`, and the proposed decisions above.
- Historical V8 PoC dataset when ready.
- Telegram command/control policy and allowlisted actor validation at a later
  milestone; the one-way development notifier is already configured.
- Answers to the open decisions in `PLAN.md` when convenient; reasonable defaults
  can be proposed during review.

## Next work

1. Complete official V8 checkout, install upstream build dependencies, and pin
   exact revisions.
2. Create the minimal project skeleton and test harness.
3. Add fail-closed dual-provider credential loading and endpoint separation.
4. Run strictly capped provider capability probes and record redacted results.
5. Build and smoke-test the first symbolized `d8`, then commit/push manifests.
