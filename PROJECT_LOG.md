# Fuzzynth project log

This is the living operational memory for the project. Read it together with
`instruction.md` and `PLAN.md` before resuming work.

## Current state

- Phase: custom-only subsystem campaign and native replay oracles are live.
- Implementation authorization: granted by owner on 2026-09-01.
- Repository: initialized from `git@github.com:Crihexe/fuzzynth.git`.
- Provider calls: sustained alternate-endpoint Luna/Terra campaign with exact
  per-response usage accounting; official-provider workers remain disabled.
- V8 source: exact Chrome 152 revision checked out; release, optdebug, ASan,
  UBSan+vptr, TSan, and MSan builds are packaged as isolated workers.
- Fuzzing campaigns: 12 custom-only subsystem workers plus a feature-routed
  sanitizer/check replay matrix; no genuine V8 candidate has been verified yet.
- Telegram development notifier, crash/pause alerts, and authenticated command
  control are implemented; the hardened long-polling service is active.
- Historical PoC dataset: preview-v3 SQLite index loaded read-only with 9,830
  eligible deduplicated JavaScript sources and exact per-generation provenance.
- Remote status: implementation checkpoints are pushed frequently to
  `origin/main`; verify the exact head at each resume.

## Protected boundaries

- Work tree: `/root/fuzzynth`.
- `/root/red-sailor` is out of scope and must remain untouched.
- Provider credentials live outside the repository at
  `/root/fuzzynth_openai_credentials` and must not be logged or committed.
- Git pushes use the dedicated repository deploy key.
- `poc_dataset/` is concurrent owner-managed work and must not be read, edited,
  staged, or committed without a later explicit request.

## Decision records

### D-001 — Independently bounded campaign families

- Status: accepted and narrowed by owner.
- Decision: schedule the three initial iterative workers independently, with a
  possible later tool-driven Terra lane under a separate budget.
- Why: enables controlled comparisons and prevents a failing or expensive lane
  from stopping the experiment.

### D-002 — Raw response is canonical

- Status: accepted by owner.
- Decision: in iterative mode, archive and execute the assistant response bytes
  as the canonical program without a tool or JSON wrapper.
- Why: minimizes protocol tokens and latency while preserving exact evidence.

### D-003 — Sealed stream before speculative execution

- Status: superseded by D-023 on 2026-09-02.
- Decision: implement byte-exact streaming capture followed by execution at
  response completion before experimenting with prefix/PTY execution.
- Why: establishes reproducible semantics and a baseline for measuring the more
  aggressive stream variants.
- Supersession: the owner explicitly excluded streaming from the initial worker
  design in favor of one completed non-streaming response per executable turn.

### D-004 — Provider model identity is probed, not assumed

- Status: accepted by owner.
- Decision: preserve the requested `gpt-5.3-codex-spark` identifier and test it
  against the custom base URL. Do not substitute a public catalog model silently.
- Why: public documentation confirms Codex-Spark as a product capability and
  streaming for GPT-5.3-Codex, but does not currently document that exact API ID.

### D-005 — Corpus conditioning is an experiment matrix

- Status: accepted by owner.
- Decision: compare unconditioned, rotating, retrieved, technique-local, and
  large-context historical-PoC conditioning under matched budgets.
- Why: distinguishes useful generalization from memorization and replay while
  retaining the owner's deliberate style-conditioning hypothesis.

### D-006 — Internal syntax is allowed in dedicated lanes

- Status: accepted by owner.
- Decision: retain `%` intrinsics and useful `d8` helpers under versioned profiles,
  then annotate intentional abort primitives during triage rather than banning all
  internal syntax.
- Why: optimization intrinsics can reach valuable engine states, while profile
  separation keeps expected aborts from becoming false reports.

### D-007 — Controller and target are separate trust zones

- Status: accepted by implementation authorization.
- Decision: only the controller receives API/Telegram credentials. Disposable
  `d8` workers have no network or secrets and run within resource limits.
- Why: generated programs are untrusted and crash collection must survive target
  failure.

### D-008 — Runtime data stays out of Git

- Status: accepted by implementation authorization.
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

### D-015 — Reproducible V8 build profiles

- Status: accepted by implementation authorization.
- Decision: configure independent symbolized release, optdebug, and ASAN `d8`
  builds from versioned profiles, always verifying checkout revision first and
  recording a local machine-readable manifest after each build.
- Why: throughput, invariant checks, and memory-safety instrumentation serve
  different roles; exact GN args and binary hashes make findings reproducible.

### D-016 — Deterministic outcome classification

- Status: accepted by implementation authorization.
- Decision: classify captured process facts locally with strong-signal priority
  (`sanitizer`, V8 fatal/check, native signal), never by asking the model whether
  its own program found a bug.
- Why: ordinary exceptions, Wasm traps, timeouts, and OOM kills must not inflate
  crash counts, while raw stdout/stderr and termination facts remain preserved.

### D-017 — Content-addressed exact evidence bytes

- Status: accepted by implementation authorization.
- Decision: store generated programs, raw provider responses, stdout, stderr, and
  other byte artifacts by SHA-256 with private file permissions and integrity
  verification; metadata refers to immutable artifact identities.
- Why: provider bodies may not be valid semantic output, duplicate programs
  should not waste storage, and later replay must use the exact bytes originally
  observed.

### D-018 — Strict stream assembly

- Status: superseded/dormant under D-023.
- Decision: preserve raw SSE bytes independently, decode events incrementally
  across arbitrary transport chunks, and assemble the canonical program only
  from explicit `response.output_text.delta` strings.
- Why: transport chunk boundaries are not token or JavaScript boundaries, and
  protocol metadata must never be executed as part of the generated program.
- Supersession: the tested implementation remains compatibility code, but the
  active worker matrix uses non-streaming Responses and does not schedule it.

### D-019 — Fail-closed cost accounting

- Status: accepted by implementation authorization.
- Decision: prices are versioned per provider/model and never guessed; missing
  primary usage or pricing pauses work. Costs use provider-reported total output
  tokens without adding reasoning tokens a second time, and reservations must fit
  every active hard-budget window.
- Why: alternate-provider token behavior already diverged from the requested cap,
  and optimistic accounting would allow an unattended campaign to overspend.

### D-020 — Self-tested minimal worker envelope

- Status: accepted by implementation authorization.
- Decision: package each `d8` build into a `scratch` image containing only the
  binary and resolved runtime libraries, then require a networkless, read-only,
  non-root, capability-free startup smoke under the selected resource profile.
- Why: resource limits can themselves induce V8 checks; a worker must prove it
  can start before generated output is accepted, or infrastructure aborts will be
  misclassified as engine findings.

### D-021 — Docker state is authoritative for resource termination

- Status: accepted by implementation authorization.
- Decision: execute only by immutable image ID, stream stdout/stderr under a
  controller byte cap, enforce an external wall deadline, then combine Docker's
  exit/OOM state with captured V8 signal text for classification.
- Why: exit code 137 alone cannot distinguish OOM from a controller kill, and an
  output-flooding process must not deadlock the same controller preserving it.

### D-022 — Transactional evidence links

- Status: accepted by implementation authorization.
- Decision: keep large exact bytes in the content-addressed artifact store and
  link them from a private SQLite WAL catalog containing immutable generation,
  usage/cost, binary, flags, execution, termination, and classification metadata.
- Why: crashes must remain queryable across restarts without duplicating raw data,
  while foreign keys prevent a metadata record from silently losing its program,
  request, provider response, stdout, or stderr identity.

### D-023 — Iterative non-streaming turns

- Status: accepted by owner on 2026-09-02.
- Decision: each initial request is non-streaming; its complete assistant output
  is one canonical JavaScript program, executed immediately in a fresh `d8`, with
  bounded factual feedback passed to the next turn.
- Why: removes streaming/tool overhead while retaining clear program boundaries,
  deterministic execution semantics, and exact evidence.

### D-024 — Initial three-worker matrix

- Status: accepted by owner on 2026-09-02.
- Decision: start with alternate Spark at minimum requested reasoning/high
  verbosity, alternate Luna at `xhigh`/high verbosity, and official Luna at
  `none`/`low`, high verbosity, and high temperatures chosen per session.
  Alternate temperature is omitted. Terra `xhigh` tools remain disabled.
- Why: directly compares throughput, deep reasoning, provider, and sampling
  behavior before paying for a much more expensive tool investigator.

### D-025 — Cumulative multidimensional budgets

- Status: accepted by owner on 2026-09-02.
- Decision: cap alternate Luna at 1250 total credits and the separate owner-set
  category ceilings; cap official Luna locally at `$4.90`; record Spark usage and
  pause on alternate quota/rate errors. Reserve before every request and retain a
  conservative reservation when usage is unknown.
- Why: provider billing mixes input, cache, output, and reasoning at different
  rates; no single token total can safely enforce the owner's limits.

### D-026 — No automatic crash replay in initial campaigns

- Status: superseded by D-029 on 2026-09-02.
- Decision: the original policy made a crash-candidate session terminal after
  persisting evidence and notifying Telegram. It did not replay, validate,
  minimize, or invoke another model automatically.
- Why: the initial experiment measures discovery. Human triage can deliberately
  reproduce a preserved candidate later without multiplying cost or risk.

### D-027 — Dataset and live-start gate

- Status: accepted by owner on 2026-09-02.
- Decision: continue offline implementation, but do not expose a live campaign
  command or start conditioned workers until the owner finishes and explicitly
  releases `poc_dataset/` for integration.
- Why: avoids premature token spend and prevents implementation code from reading
  owner-managed corpus work while it is still changing.

### D-028 — Durable authenticated Telegram control

- Status: accepted from the owner's requested Telegram control scope and deployed
  on 2026-09-02.
- Decision: persist global/worker dispatch state and enforce it before session
  start, resume, and every new model turn. Telegram accepts only the configured
  chat and owner identity; mutations are audited and idempotent. Global stop/start
  require the exact `CONFIRM` word and do not kill an already running `d8` process.
- Why: owner commands must affect the actual scheduler boundary without exposing
  a shell, losing repeated updates, or corrupting an in-flight evidence record.

### D-029 — Preserve and continue after crash candidates

- Status: accepted by owner on 2026-09-02; supersedes only D-026's terminal
  session transition, not its prohibition on automatic replay.
- Decision: persist and alert every crash candidate, then continue the same
  bounded model conversation with the factual d8 observation. Record the exact
  corpus-window hash and constituent dataset `.js` names and hashes on every
  generation. A deterministic recognizer may attach a corrective hint only for
  narrowly proved d8 intrinsic-contract mistakes; the record remains a bug
  candidate. Do not automatically replay, validate, minimize, or reproduce it.
- Why: an uninteresting generated misuse should not disable a productive lane,
  while conservative classification preserves every native failure for later
  human review and lets the model avoid repeating an identified harness mistake.

### D-030 — Mandatory V8 fuzzing execution mode

- Status: accepted by owner on 2026-09-02.
- Decision: execute every configured campaign worker with `--fuzzing` as a
  mandatory base d8 flag, alongside `--allow-natives-syntax` and `--expose-gc`.
  Reject campaign configuration that omits it. Continue recording the exact flag
  vector on every execution and retain all signal, sanitizer, and remaining V8
  fatal evidence under the existing classifier.
- Why: pinned Chrome 152 V8 documents this flag as making invalid intrinsic usage
  fail silently, and its runtime-test implementation specifically bypasses the
  `CheckMarkedForManualOptimization` fatal check while leaving useful manual
  optimization paths available. This removes repeated harness-contract noise
  before classification and makes surviving candidates materially stronger.

### D-031 — Indexed preview-v3 paired-prompt experiment

- Status: accepted by owner on 2026-09-02.
- Decision: replace the four-file v2 canary pool with a fail-closed SQLite-indexed
  preview-v3 pool. Run every enabled model/effort configuration as an exact
  `rich`/`lean` pair. For each pair ordinal, derive session plan and corpus from
  the shared pair ID so both prompts receive identical examples, temperature,
  reasoning effort, and turn limit. Persist prompt variant/hash, pair ID, and
  exact corpus provenance on every generation.
- Why: 3,688 earlier generations were conditioned by only four sources, causing
  avoidable priming and preventing a meaningful prompt comparison. The new
  design isolates prompt effects while preserving reproducibility and exposes a
  broad randomly sampled context-poisoning pool instead of a hand-selected set.

### D-032 — Reproducible experimental-tier replay

- Status: accepted by crash-search continuation on 2026-09-03.
- Decision: derive and record deterministic V8/fuzzer seeds from every generated
  program, then prioritize valid recent programs under optdebug Maglev-future,
  optdebug Turbolev-future/Turboshaft verification, ASan MinorMS randomized GC,
  and ASan Wasm-staging/type-assertion profiles. Run the 2,616-case priority set
  immediately after the already-active replay before expanding to all history.
- Why: unique source hashes did not imply unique engine paths. Experimental tier
  checks and per-program schedule variation add native-path diversity without
  another provider request, while exact recorded seeds keep every result
  reproducible.

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

### 2026-09-01 — Exact V8 checkout and build dependencies

- Completed a shallow official V8 dependency sync at revision
  `3de6ffffbfdcf265e9f11a5c9d1cfb4d486d7550`; verified checkout `HEAD` exactly.
- Worked around repeatable Git HTTP/2 transport failures by scoping HTTP/1.1 to
  the checkout command rather than changing global Git configuration.
- Installed dependencies through V8's upstream `install-build-deps.sh`; its
  prerequisite check first identified and then used the missing `file` package.
- Added reproducible release-symbolized, optdebug, and ASAN build definitions and
  a revision-verifying build/manifest script. Fuzzilli remains absent.
- Added a fixed smoke suite for JavaScript, WebAssembly, and natives syntax plus
  binary/help hashes; it will run after the in-progress link completes.
- Added the deterministic first-pass outcome classifier and tests covering normal
  exits, JS exceptions, Wasm traps, timeout, OOM, signals, V8 checks, and
  sanitizer diagnostics.

### 2026-09-01 — First pinned `d8` binary

- Built the `release_symbolized` profile on all 16 host CPUs: 2313 steps in
  12m45s with 15.6x effective parallelism.
- Produced `d8` reporting V8 `15.2.124.19`; recorded binary SHA-256
  `c220e42e5720a58a422a85889fa178ef7c20e8a720f5ba2d294b3a238ff56a73`
  and ELF build ID `69cdc77e20e564db`.
- Passed fixed JavaScript, WebAssembly, and `--allow-natives-syntax` optimization
  intrinsic smoke checks.
- Stored full local machine-readable manifests under ignored `.local/` storage
  and added a reviewable build identity report under `docs/builds/`.

### 2026-09-01 — Optdebug `d8` binary

- Built optdebug on all 16 host CPUs: 2507 steps in 16m59s with 15.4x reported
  parallelism.
- Verified V8 `15.2.124.19`, binary SHA-256
  `07b8d1fea242ea7acc0df3770f6e4a9db921f41511bca386f8f43e8f2ea69d31`,
  slow checks, backtraces, optimized-debug mode, component build, and full symbols.
- Passed local and isolated-worker JS, Wasm, and natives-syntax smoke tests.
- Packaged worker image
  `sha256:a55c538d94e0ff92485c8d6cded2a0804d20a839a25a1189fa18f99a23020570`.
- Added a concurrency-safe, content-addressed exact-byte artifact store with
  deduplication, private modes, canonical references, and tamper detection.
- Added incremental SSE decoding and strict Responses stream assembly, including
  split-chunk, CRLF, Unicode, malformed-delta, incomplete-tail, and terminal-event
  tests.
- Added bounded HTTP streaming integration with an explicit `stream=True` gate,
  separate raw-SSE/canonical-output capture, optional raw-chunk persistence hook,
  provider timeout, and a local hard byte cap independent of provider token caps.
- Added provider-neutral exact-decimal token accounting, conservative handling of
  missing cached-token counts, hard-window reservations, and a zero-budget default
  configuration that keeps sustained campaigns disabled until prices and limits
  are explicitly set.
- Packaged the release `d8` into a 42,477,934-byte `scratch` worker image running
  as UID/GID 65532, with no shell. Networkless/read-only/capability-free JS, Wasm,
  and natives-syntax smoke checks passed.
- Identified 32 PIDs as an invalid V8 startup envelope (`Check failed: Start()`,
  `SIGABRT`); 48 passed and the standard profile now reserves 64. This is an
  infrastructure baseline failure, not a candidate bug.
- Added the first real isolated executor: immutable image identity, network off,
  read-only root, no capabilities, no-new-privileges, PID/RAM/CPU/core limits,
  read-only program mount, tmpfs, wall timeout, total output cap, Docker state
  inspection, V8 signal extraction, and best-effort disposable cleanup.
- Integration-tested normal Wasm execution (`ok`), an infinite loop (`timeout`,
  624 ms under a 500 ms deadline), an output flood (`output_limit`, exactly 1024
  bytes retained), and the known invalid 32-PID profile (`v8_fatal`, signal 6).
- Added the first transactional evidence catalog with private permissions, WAL +
  full synchronization, foreign keys, canonical parameter/flag JSON, generation
  usage and cost, exact build/image identities, execution termination facts, and
  aggregate generation/execution/candidate/cost status.
- Added `fuzzynth execute` as the first end-to-end evidence path: it resolves and
  verifies pinned build/image manifests, content-addresses the exact program,
  executes under a versioned worker profile, archives bounded output, records the
  execution transactionally, and returns only safe metadata.
- The first end-to-end smoke exposed that mode-0600 artifacts cannot be read by
  UID 65532 through a bind mount. The service now keeps artifacts private at rest
  and mounts a short-lived exact read-only copy from a mode-0700 directory; the
  repeated test completed `ok` and left no staging directory.
- Added versioned raw JavaScript and JavaScript-transported Wasm prompt baselines
  plus disabled campaign definitions for Spark/Luna, fresh/adaptive/short context,
  alternate/official providers, and official-temperature experiments. They keep
  the whole response executable and do not encode historical exploit techniques;
  later corpus windows provide the requested style conditioning.

### 2026-09-01 — ASAN `d8` binary and complete build matrix

- Built ASAN on all 16 host CPUs: 2481 steps in 14m40s with 14.8x reported
  parallelism.
- Verified V8 `15.2.124.19`, binary SHA-256
  `f85fdcb890214faf077d73ada858be94da067a38ea0bda6a06c2f7ab983c22b5`,
  and ELF build ID `244d899009cf3a1e`.
- Passed fixed local JavaScript, WebAssembly, and natives-syntax smoke tests.
- Packaged a symbolizing ASAN worker image
  `sha256:35a808af5f3fd8ecad5946032a846c174ffd74dd82b6b3a2a967776db09a7899`
  with the checkout-pinned `llvm-symbolizer` and `abort_on_error=1`.
- Verified the symbolizer inside the shell-free image and passed an end-to-end
  isolated JavaScript/WebAssembly execution through the evidence pipeline.
- Completed the three intended Chrome 152 worker variants: throughput-oriented
  release-symbolized, invariant-oriented optdebug, and memory-safety ASAN.

### 2026-09-02 — Iterative non-streaming controller

- Ran exactly one additional standalone request for each initial provider/model
  lane to inspect usage fields, without a conversation, execution, or campaign:
  alternate Spark reported 35 input, 286 total output, and 279 reasoning tokens;
  alternate Luna `xhigh` reported 331 input, 99 total output, and 91 reasoning
  tokens; official Luna at temperature 1.3 reported 35 input, 6 output, and zero
  reasoning tokens. Generated output was not displayed, retained, or executed.
- Added the three enabled worker definitions and the disabled Terra tool placeholder,
  with deterministic per-session sampling of turn limits and official parameters.
- Added a JavaScript-only system prompt that permits optimization intrinsics and
  `gc()` for useful state construction while prohibiting deliberate target aborts,
  exits, host access, and fabricated bug claims.
- Implemented bounded non-streaming Responses capture with separate immutable
  request JSON, raw HTTP response JSON, and semantic program artifacts. Bounded
  error bodies are retained privately for diagnostics.
- Implemented bounded session context, fresh isolated execution for every turn,
  compact factual feedback, and durable SQLite session/attempt state that can be
  inspected and resumed after controller restart.
- Implemented a durable multidimensional budget ledger with pre-request
  reservations, conservative unknown-usage handling, the owner-set custom Luna
  limits, a `$4.90` official Luna cap, and Spark quota/rate pause behavior.
- Implemented crash-terminal and pause transitions plus private Telegram alerts
  that contain identifiers and hashes but never a generated program or captured
  output. Delivery behavior was exercised with an injected fake sender; no fake
  crash was sent to the live chat.
- Extended every new execution record with a content-addressed detail manifest
  containing the immutable image and `d8` identities, final Docker state, exact
  flags, wall/CPU/RAM/PID/output/tmpfs limits, core policy, and capture sizes.
  Existing schema-1 execution evidence migrates safely to schema 2.
- Added offline `workers`, `budget-status`, and `session-status` CLI inspection.
  A live campaign command is intentionally absent until corpus integration.
- Kept streaming support dormant, made automatic crash replay unavailable, and
  did not read or modify the owner-managed `poc_dataset/` directory.

### 2026-09-02 — Telegram owner control deployed

- Added a private SQLite control ledger with `running`, `paused`, and `stopped`
  global state, independent worker pauses, monotonic Telegram update offsets, and
  an idempotent audit row for every state-changing update.
- Wired campaign start, resume, and turn dispatch to the control ledger. Changes
  take effect before the next provider request; an already running request or
  isolated `d8` execution is allowed to finish and preserve its evidence.
- Added `/status`, `/workers`, `/sessions`, `/cost`, `/budget`, `/lastcrash`,
  `/pause`, `/resume`, `/stop CONFIRM`, and `/start CONFIRM`. No input maps to an
  arbitrary command, path, provider call, replay, or campaign start.
- Added strict chat/sender authorization. The current private-chat configuration
  requires sender ID to equal chat ID; group use would additionally require an
  explicit `TELEGRAM_USER_ID` in the external credential file.
- Added bounded update/reply bodies, durable offsets, retry-safe mutation handling,
  and conservative budget reports showing money/credits, token dimensions, and
  uncertain reservations.
- Installed and enabled `fuzzynth-telegram-control.service`. It loads no provider
  credentials, can write only under `/root/fuzzynth/state`, has no Linux
  capabilities, and received systemd exposure score `3.5 OK`. It was active with
  zero restarts after installation.
- Sent owner update message ID `16` with the available command list.

## Verification performed

- No secret values were printed or added to the repository.
- Six explicitly capped standalone model-provider requests were made across the
  project; generated text was neither printed nor stored, and only redacted
  capability/usage metadata was retained. No campaign or multi-turn test ran.
- Official V8 checkout/build state is tracked separately under `.local/`, which
  is ignored by Git.
- No file under `/root/red-sailor` was modified.
- Telegram notifier passed Python bytecode compilation and dry-run validation.
- Live Telegram `sendMessage` test succeeded without printing credentials or the
  configured chat ID.
- Credential tests pass and `fuzzynth doctor` validates both providers without
  making network calls or displaying endpoint/key values.
- One hundred eleven unit tests pass after adding request
  omission/serialization, process outcome classification, artifact integrity,
  non-streaming evidence capture,
  budget reservations, session orchestration, alerts, executor isolation,
  execution detail manifests, migrations, and offline status inspection.
- All three pinned worker profiles pass local and isolated smoke verification.
- All live probe output was restricted to capability, latency, effective
  parameters, response state, and usage metadata.
- The systemd unit passed `systemd-analyze verify`; its install script passed
  shell syntax validation, and the active control service retained private state
  permissions (`0700` root, `0600` databases).

## Waiting on owner

- Explicit release of the historical V8 PoC dataset when ready.
- Dataset window sizes, initial worker concurrency, and the remaining open choices
  in `PLAN.md` when convenient.

## Next work

1. After explicit owner release, index the dataset immutably and implement bounded
   randomized corpus-window selection without silently editing source items.
2. Connect corpus selection to the existing session service and expose a bounded
   scheduler lifecycle only after its offline tests pass.
3. Run a synthetic incident drill, then one tightly bounded controlled session per
   worker only when the owner confirms the dataset and live-start review gates.
4. Keep replay, minimization, and Terra tools deferred until initial run results
   justify those separately authorized activities.

### 2026-09-02 — v2 corpus canary and first supervised campaigns

- Validated the owner-released archive
  `poc_dataset/v8_wasm_poc_handout_v2.zip` before extraction: 77 regular entries,
  no absolute/traversal paths or symlinks, 1.0 MiB uncompressed, archive SHA-256
  `635e89b7b5ac9b91e8f938bd3c5bde6fda4f0a1788d3f1ad8632775417964a1c`.
  Extracted it privately under ignored `.local/datasets/`; no dataset file was
  staged or modified.
- The handout contains 388 mostly metadata-only records but only four materialized
  JavaScript samples. Selected all four as a temporary explicit pool: a Maglev
  optimization trigger, a large generated/inlining trigger, a Wasm validate and
  detached-buffer/GC trigger, and a large async-generator constructor trigger.
  Historical programs were never executed.
- Added immutable rotating two-sample corpus windows. Every session stores the
  exact corpus bytes and SHA-256. The prompt and corpus envelope both prohibit
  copying, renaming, mechanical mutation, combination, or targeting the same
  historical defect.
- Added the explicit-live parallel supervisor, safe JSON events, bounded worker
  and session canaries, crash-terminal behavior, durable pause/resume, Telegram
  operational alerts, and the hardened persistent Spark service. Generated code
  and captured output never enter operational logs or Telegram messages.
- The first parallel start revealed local SQLite initialization contention.
  Deterministic 750 ms worker staggering fixed it. A second canary revealed that
  unsigned 64-bit derived seeds can exceed SQLite's signed range; seeds now retain
  63 deterministic bits. Both fixes were regression-tested.
- Alternate Luna xhigh produced no program in two attempts: the first exceeded
  the original 90-second socket timeout, and the supervised retry returned HTTP
  524 after about 125 seconds. Both attempts have unknown usage and retain their
  full conservative reservations. The worker and session remain paused.
- Official Luna with temperature 1.8 and `reasoning=low` returned HTTP 400 before
  generation. The earlier `reasoning=none` probe had succeeded, so the incompatible
  low-reasoning definition was disabled and retained for provenance. A separate
  none-reasoning high-temperature worker completed a five-turn session.
- Spark completed two full iterative sessions. Session 1 used the async-generator
  and Maglev samples and yielded 15 programs: 1 `ok` and 14 Wasm `CompileError`
  nonzero exits. Session 2 used the async-generator and Wasm/GC samples and yielded
  16 programs: 14 `ok` and 2 expected JavaScript exceptions. No candidate crash,
  timeout, OOM, output truncation, or Docker failure occurred.
- Spark session input grew from 962 to 27,056 tokens in session 1 and from 929 to
  28,502 in session 2. Across both, usage was 538,300 input, 397,003 output, and
  284,420 reasoning tokens; the provider reported zero cached input. Median d8
  execution time was about 0.2 seconds. Initial output/corpus comparison found
  less than 0.071% exact 32-byte-gram overlap, with distinct program hashes.
- Official none-reasoning temperature 1.2 completed five programs: 3 `ok`, 2
  expected JavaScript exceptions, 0 reasoning tokens, and `$0.011312` actual cost.
  Its failed low-reasoning request retains a conservative `$0.010776` uncertain
  reservation. The official lane was paused after the supervised baseline.
- At the start of Spark session 3, the alternate provider returned HTTP 500 with
  `no healthy Codex account is available inside the key routing scope`. No program
  or usage was returned. The service correctly paused without retrying and sent a
  Telegram notification; it remains active and can be resumed after quota/account
  availability returns.
- The custom Luna ledger currently includes two uncertain reservations plus the
  configured historical reserve, totaling 8.727090 credits accounted/reserved.
  This is deliberately not reported as confirmed spend.
- Installed and enabled `fuzzynth-spark-campaign.service`. It is restricted to the
  Spark worker and the four reviewed v2 files, writes only project state, cannot
  access `/root/red-sailor`, and scored `3.5 OK` in `systemd-analyze security`.
  The Telegram control and Spark services are both active; Spark is waiting on its
  durable worker pause.
- One hundred seventeen unit tests pass. No secret, runtime state, extracted
  dataset item, generated program, or captured d8 output was committed. All code
  changes through this canary were committed as Cristian Di Nicola and pushed.

### Review queue for the next session

1. Inspect the 36 generated programs and their exact histories, especially the
   14-program CompileError mode collapse versus the 14/16 valid second session.
2. Compare novelty, feature mix, program size, executor outcomes, and token cost by
   corpus window and turn index; decide whether to shorten Spark lifetimes below
   8–16 turns before resuming quota.
3. Inspect the two alternate Luna gateway failures and decide whether xhigh needs
   smaller context/output, a provider-side asynchronous path, or a lower-effort
   comparison. Do not clear uncertain reservations without provider evidence.
4. Compare official Luna's concise no-reasoning programs against Spark at equal
   execution/output budgets before allocating more of the `$4.90` cap.
5. Replace the temporary four-file pool with the owner-provided v3 dataset only
   after explicit review and immutable ingestion. Keep replay/minimization and
   Terra deferred.

### 2026-09-02 — complete-SSE custom transport and Spark fallback

- The owner confirmed that the alternate gateway supports `xhigh`, but realistic
  non-streaming requests take roughly 301–535 seconds and Cloudflare terminates
  clients near 125 seconds. The endpoint also rejects `max_output_tokens`,
  `max_completion_tokens`, `temperature`, and `top_p`.
- Alternate workers now send `stream: true` solely as transport. Fuzzynth buffers
  and archives the exact SSE bytes, requires `response.completed`, compares the
  assembled output-text deltas with the terminal response, and only then records
  and executes the program. Partial or mismatched streams are never executed.
- A supervised custom Luna `xhigh` canary completed beyond the old gateway
  boundary in about six minutes. Its request contained only `input`,
  `instructions`, `model`, `reasoning`, `store`, `stream`, and `text`; all four
  unsupported fields were absent. The 1,114,515-byte raw SSE and semantic program
  were preserved separately. Usage was 1,356 input, 21,024 output including
  18,295 reasoning tokens, accounting for 0.637500 custom credits. d8 produced an
  ordinary JavaScript exception, not a candidate crash.
- The following stateful xhigh turn stayed connected for about 535 seconds, then
  the alternate provider emitted a terminal SSE `error` with code
  `request_timeout` after 868 output-text deltas. Fuzzynth preserved the
  554,595-byte partial stream, did not create or execute a program, retained the
  worst-case reservation because usage was absent, and paused only xhigh. This
  establishes two separate boundaries: SSE avoids Cloudflare's ~125-second 524,
  while the provider can still end exceptionally long generations near 535
  seconds.
- Two subsequent controlled xhigh turns completed in 345.3 and 424.0 seconds.
  Together they used 12,417 input and 42,567 output tokens, including 34,109
  reasoning tokens, and accounted for 1.339095 credits. Their programs were
  10,898 and 14,974 bytes and both produced ordinary JavaScript exceptions. The
  observed complete-SSE xhigh sample is therefore three completions and one
  provider `request_timeout`; xhigh was then owner-control paused with no active
  reservation while lower-effort lanes continued.
- The owner traced the remaining ~535-second timeout to upstream OpenAI and
  changed the terminal-partial policy: after a stream has definitively ended,
  Fuzzynth may execute the exact bounded output-text prefix once because even
  syntactically incomplete generated code can exercise parsing paths. It still
  never executes live stream fragments. Partial programs, raw SSE, provider code,
  execution evidence, and the unknown-usage reservation remain linked. For
  upstream request-timeout or terminal-incomplete prefixes, the bounded session
  continues: the next request contains that exact prefix plus factual d8 feedback.
  Crash, hard-budget, quota, and unrelated provider failures still pause.
- The sustained reasoning lane changed from custom Luna `xhigh` to `high` to test
  whether lower thinking latency improves terminal completion. The historical
  xhigh worker and evidence remain configured but disabled.
- The previously archived xhigh timeout contained a 2,619-byte semantic text
  prefix. After the owner changed policy, it was reconstructed from the immutable
  SSE, associated with its original generation, and executed exactly once in the
  isolated release-symbolized d8 worker. It produced an ordinary JavaScript
  exception in 176 ms and no candidate crash.
- The first custom Luna `high` SSE turn completed in about 152 seconds with 1,258
  input, 8,368 output, and 6,662 reasoning tokens (0.257330 credits). Its program
  completed successfully in d8. This initial observation is materially faster
  and cheaper than the 345–424-second completed xhigh turns, so high remains the
  active sustained reasoning lane.
- Added a distinct custom Luna `none`/high-verbosity fallback lane managed by a
  local reconciler. It runs only while Spark has a supervisor-originated provider
  or quota pause and never overrides owner, crash, or provider control. Its first
  streamed turns completed successfully while Spark remained quota-paused.
- Corrected both fallback and five-hour retry logic to recognize the precise
  `provider_quota_or_rate_limit` state emitted for HTTP 429, in addition to the
  generic provider-error state. Spark failures remain isolated from all Luna
  lanes.
- The deployed v2 service now includes Spark, custom Luna xhigh, custom Luna low,
  managed custom Luna none, and official Luna. Both fallback and cooldown timers
  are active. Service shutdown allows up to 660 seconds so a complete in-flight
  xhigh stream can finish cooperatively.
- Increased the raw response/SSE archive ceiling from 2 MiB to 16 MiB based on
  the observed canary size. The separately enforced executable program ceiling
  remains 512 KiB and every custom Luna request retains its 128k-token worst-case
  budget reservation.
- Terminal SSE errors now retain their provider code (for example,
  `request_timeout`) in generation metadata instead of collapsing to a generic
  incomplete-stream label. Partial deltas remain non-executable.
- All 126 offline tests and systemd unit verification pass. No candidate crash
  has been observed at this checkpoint.

### 2026-09-02 — role-correct conversation history and live Telegram state

- The owner clarified that previous generated JavaScript should remain the
  model's prior `assistant` message, not be copied into a newly synthesized user
  prompt. Replaced the flat `<recent-turn>/<program-data>` text envelope with a
  bounded Responses API message sequence: initial user corpus/context, exact
  assistant program, then user d8 observation and next-program request. API
  `store` remains false and the bounded history is reconstructed from immutable
  local session evidence for every request.
- The Telegram control service had kept a startup-time worker configuration
  snapshot from 08:33, so it incorrectly reported the newly enabled custom Luna
  high lane as disabled. Authorized commands now reload the TOML before reading
  or mutating worker state. `/workers` separately reports static configuration,
  persistent control, and effective schedulability; a disabled worker can no
  longer misleadingly appear to be dispatching.
- The full offline suite passes with 132 tests. Commit `323f002` was deployed
  cooperatively: active streams completed and were recorded before the old
  supervisor exited, Telegram restarted at 10:24:50 UTC, and the campaign
  restarted at 10:26:02 UTC.
- A live custom-endpoint request after deployment contained the role sequence
  `user, assistant, user, assistant, user`. Both assistant-content hashes exactly
  matched the corresponding locally archived prior programs, `store` was false,
  and no legacy program-data envelope appeared. Subsequent turns completed
  normally, proving custom-provider acceptance rather than only offline schema
  validity. At this checkpoint the catalog held 740 generations, 732 executions,
  and zero bug candidates.

### 2026-09-02 — cheaper official-model strategy

- Replaced the official GPT-5.6 Luna lane with two cheaper non-reasoning lanes:
  GPT-4o mini rotates temperature 0/0.5/1/1.5/2 over randomized 1–3-turn
  sessions; GPT-4.1 nano uses the same temperatures and exactly six turns.
- Minimal live official-endpoint probes confirmed GPT-4o mini snapshot
  `gpt-4o-mini-2024-07-18` and GPT-4.1 nano snapshot
  `gpt-4.1-nano-2025-04-14`, both with `temperature=2.0` and detailed usage.
  GPT-4.1 nano remains accessible despite its deprecated status.
- A separate bounded GPT-4.1 nano request with `reasoning.effort=medium`
  returned HTTP 400 `unsupported_parameter`, matching the model documentation:
  it has no reasoning step. Both new workers therefore omit the reasoning and
  text-verbosity fields entirely.
- Added per-model pricing profiles to the durable budget ledger. Both workers
  share one `$4.90` hard cap while GPT-4o mini settles at
  `$0.15/$0.075/$0.60` and GPT-4.1 nano at `$0.10/$0.025/$0.40` per million
  uncached-input/cached-input/output tokens.
- Added an automatic SQLite schema migration and legacy meter alias. A coherent
  copy of the live version-1 ledger migrated to version 2 with all 358 official
  Luna rows folded into `openai_official`; `$0.986597` and two uncertain
  reservations remained intact.
- All 134 offline tests pass before deployment.
- The first live GPT-4o mini temperature-2 session filled its 8,192-token output
  cap. Its terminal JSON response contained a 53,151-byte text prefix, but the
  SSE-only partial-output branch archived it without execution and paused that
  worker. Generalized terminal-prefix handling to official JSON: bounded output
  is now preserved, executed once, returned with factual d8 feedback, and does
  not pause solely for `max_output_tokens`. The regression suite now has 135
  passing tests.
- Recovered that already-archived GPT-4o mini prefix from the immutable raw JSON
  and executed it once under its original generation identity: d8 reported an
  ordinary JavaScript exception in 173 ms, not a crash candidate. Resuming a
  paused session whose attempted turn count already reached its target now
  closes that session instead of dispatching an unintended extra turn.
- Commits `25cdc99`, `a14a6b5`, and `6e56c0a` were pushed and deployed with
  cooperative supervisor restarts. Both official workers and the Telegram
  control plane are active; the old official-Luna lanes remain disabled.
- A post-fix live GPT-4o mini canary at temperature 2 again reached exactly
  8,192 output tokens. Fuzzynth preserved its 53,634-byte program prefix,
  recorded `incomplete_reason=max_output_tokens`, executed it once in d8
  (`js_exception`, no candidate), kept `pause_reason=null`, and advanced the
  three-turn session. The next request retained role-correct history with the
  preceding program byte-exactly in its `assistant` message and `store=false`.
- The full offline suite passes with 136 tests after the exhausted-session
  transition fix. No bug candidate has been observed in the new official lanes
  at this checkpoint.

### 2026-09-02 — continue-on-candidate policy and d8 misuse hints

- Two official-worker `v8_fatal` candidates were manually reviewed by the owner
  as uninteresting d8 intrinsic-contract mistakes: `%GC()` caused
  `EnsureCompiledAndFeedbackVector`, and distinct inline arrow objects caused
  `CheckMarkedForManualOptimization` after preparing one object and optimizing
  another.
- Changed candidate handling so the immutable execution and attempt retain
  `bug_candidate`, Telegram alerts immediately, and the same bounded session
  continues with the exact program as its prior `assistant` message and factual
  d8 feedback as the following `user` message. No replay, reproduction,
  validation, or minimization was added.
- Added a deliberately narrow deterministic recognizer for only those two
  explainable patterns. It emits `invalid_percent_gc_intrinsic` or
  `fresh_function_optimization_target` as a suspected-harness-misuse hint in the
  next turn and operational metadata. It never downgrades a native failure.
- Every new generation now records its corpus-window SHA-256 plus each source
  `.js` name and SHA-256. The same provenance was backfilled into the two
  existing candidate generation records from their immutable session corpus.
  Telegram candidate alerts and `/lastcrash` expose that provenance and any
  recognized misuse label without exposing program or stderr contents.
- The full offline suite passes with 143 tests. The two recognizers were also
  checked against the exact preserved program and stderr artifacts and produced
  the expected distinct labels.
- Deployed commit `7cfb547` with a cooperative campaign restart and restarted
  the Telegram controller. Recovered the original GPT-4.1 nano session at turn
  3/6 and GPT-4o mini session at turn 2/3, then restored both worker controls to
  running. Live request artifacts prove that both retained the candidate program
  in role-correct history and returned its fatal stderr marker. Nano advanced in
  the same session; 4o mini completed both remaining turns and began its next
  session normally. Both services remained active, and Telegram update 68
  reported the successful recovery.

### 2026-09-02 — mandatory `--fuzzing` rollout

- After additional owner-reviewed `CheckMarkedForManualOptimization` false
  positives, verified the pinned V8 implementation directly: the flag definition
  documents silent invalid-intrinsic failure, and runtime-test optimization paths
  bypass that particular fatal check when `v8_flags.fuzzing` is true.
- Added `--fuzzing` to all ten configured workers, including currently disabled
  comparison lanes, and made configuration loading fail closed if any worker
  omits it. Updated the generation contract so models know that `undefined` from
  invalid intrinsic use is harness behavior rather than a finding.
- Re-executed one exact preserved owner-reviewed false-positive program through
  the isolated release-symbolized worker with the new flag vector. The original
  evidence was unchanged; the canary completed in 211 ms as `js_exception` with
  `bug_candidate=false`, instead of the prior `v8_fatal` result.
- The full offline suite passes with 145 tests. Commit `9e47134` was pushed and
  deployed after a cooperative drain. The first eleven post-deploy executions
  span both official workers and all three active custom Luna lanes; all attest
  the exact flag vector
  `--allow-natives-syntax`, `--expose-gc`, `--fuzzing`; they contain zero
  candidates and zero executions missing the mandatory flag. Campaign and
  Telegram services remain active.

### 2026-09-02 — preview-v3 corpus and paired prompts

- The owner identified that the sustained v2 run had produced 3,688 generated
  programs while conditioning on only four materialized sources. That pool was
  intended only as a first canary; allowing it to continue without prominently
  re-escalating the limitation was an experiment-design error.
- Validated `poc_dataset/v8_js_pocs_preview_v3.zip` before extraction: SHA-256
  `f89960a11adcd0dfae78e99c2047ea9787b7609902f8f85c32a36c54a15d455f`,
  10,078 entries, 216,968,027 uncompressed bytes, 10,070 JavaScript files, no
  absolute/traversal paths, and no symlinks. Extracted it under ignored private
  storage without modifying the owner-managed archive.
- Audited the schema-2 SQLite index read-only with a successful integrity check.
  It contains 10,070 accepted artifacts, 22,767 provenance instances, 60,925 tag
  relations, 13,264 required-flag relations, 13,760 issue relations, 2,181 CVE
  relations, and full-text source records for every artifact. Detailed category,
  engine, syntax, size, intrinsic, marker, origin, tag, and flag distributions
  are recorded in `docs/V3_PREVIEW_AUDIT.md`.
- Implemented a fail-closed index loader that caps individual context samples at
  48 KiB, omits explicit SpiderMonkey/JavaScriptCore sources and support-only
  harnesses, retains one representative per normalized source, and verifies
  filename, size, SHA-256, and UTF-8 at every startup. The resulting uniform
  random pool contains 9,830 byte-distinct sources totaling 15,406,909 bytes.
- Added a minimal creativity-oriented system prompt without optimization, Wasm,
  GC, generator, or intrinsic recipes. Every enabled model/effort configuration
  now has exact `rich` and `lean` workers. Shared pair IDs derive identical
  corpus windows, temperature, reasoning effort, and turn limits; six offline
  first-session checks produced byte-identical windows inside every pair.
- Generation metadata now explicitly records `prompt_variant`, `prompt_sha256`,
  `corpus_pair_id`, corpus-window SHA-256, and constituent source names/hashes;
  the immutable request artifact continues to preserve the complete prompt.
- Updated paired Spark cooldown/fallback control, preview-v3 service wiring, and
  configuration invariants. All 9,830 eligible source files passed live size,
  hash, and encoding validation. The full offline suite passes with 149 tests and
  systemd unit verification succeeds before deployment.

### 2026-09-02 — preview-v3 paired campaign live rollout

- Pushed commit `fea3b31` and deployed the paired preview-v3 service. The
  campaign and authenticated Telegram controller are active, with twelve
  configured campaign lanes covering six exact rich/lean pairs.
- Started with only the official GPT-4o mini pair as a controlled canary. Its
  first rich and lean sessions had the same session seed, target turn count,
  corpus-window SHA-256, and two source names/hashes, while retaining distinct
  prompt SHA-256 values. Released the remaining pairs after that check.
- Spark rich and lean independently returned the expected provider quota/rate
  limit classification and paused. The fallback reconciler activated both Luna
  `none` rich/lean substitutes without blocking any other model lane.
- At the first live audit checkpoint, all 107 completed d8 executions used
  `--allow-natives-syntax`, `--expose-gc`, and mandatory `--fuzzing`; none was a
  bug candidate. Every comparable rich/lean session ordinal across all six pairs
  had identical corpus SHA-256, seed, and turn target. Faster variants can lead
  their mate temporarily, but the deterministic ordinal mapping remains intact.
- Audited 126 new immutable generation requests: every record had a prompt
  variant and digest, pair ID, session seed, turn index, corpus-window digest,
  and exactly two source names/hashes. Every request artifact passed its
  content-address digest check and its embedded full instructions matched the
  recorded prompt digest.
- Fixed the local `control-status` diagnostic to omit retired worker IDs retained
  for audit history in the durable control ledger. This does not delete history
  or alter dispatch. The regression test brings the full offline suite to 150
  passing tests.

### 2026-09-02 — preview-v3 campaign stopped by owner

- Applied the durable global `stopped` control state at the owner's request,
  stopped both campaign units and both Spark automation timers, and verified that
  no campaign, d8, or worker-container process remained. The authenticated
  Telegram controller remains active for read-only monitoring and future owner
  commands.
- The preview-v3 interval closed with 2,346 generations, 2,344 d8 executions,
  329 distinct paired corpus windows, 635 distinct historical source samples,
  and zero bug candidates under `--fuzzing`.
- The cooperative systemd stop waited on long-running provider calls, so the
  campaign process was terminated after dispatch had already been blocked. Its
  sole remaining active budget reservation was conservatively changed to
  `uncertain`, not released; all evidence already committed to the content store
  and SQLite ledgers remains intact.

### 2026-09-02 — prompt-quality audit and bounded explicit comparison

- Audited all 2,346 preview-v3 generations and 2,344 primary executions from
  immutable artifacts, including model/temperature cohorts, failure families,
  feature use, program size, latency, token use, exact/near duplication,
  iteration transitions, corpus-conditioned feature lift, and representative
  source review. The complete analysis is in
  `docs/PROMPT_CAMPAIGN_AUDIT_2026-09-02.md`.
- On 294 fully completed historical A/B session pairs (1,065 turns per side),
  lean reached exit 0 on 63.8% versus rich on 26.8%. Rich was dominated by
  malformed Wasm; lean was substantially more runnable but often too shallow.
  Official temperature 2.0 produced invalid syntax in 275/275 generations, and
  temperature 1.5 in 193/260; temperatures at or below 1.0 produced 8/761.
- Added and pushed the immutable explicit-v1 prompt plus a bounded Luna-low
  comparison in commit `113297a`. Ran exactly 50 current-rich and 50 explicit-v1
  turns, paired over ten identical eight-source corpus windows. The campaign
  service and Spark timers stayed inactive; only the two foreground experiment
  workers were dispatched.
- Current rich reached exit 0 in 21/50 and failed Wasm validation in 25/50.
  Explicit v1 reached exit 0 in 39/50, with two Wasm validation failures, three
  JS failures, two other nonzero exits, and four timeouts. All 100 program
  digests were unique. Explicit used fewer input/output tokens, 3.410 versus
  4.353 custom credits, lower provider latency, narrower feature composition,
  and lower adjacent structural similarity.
- Replayed the same 100 programs on ASan and optdebug: no candidate. Then
  replayed every one of the 1,108 original preview-v3 exit-0 programs on both
  sensitive builds without model calls. ASan completed all 1,108; optdebug
  completed 1,105 and timed out on three. No sanitizer report, native CHECK, or
  signal candidate was observed in 2,216 additional shadow executions.
- Disabled both one-shot experiment workers after completion, restored the
  durable global state to `stopped`, verified campaign/timers inactive and no
  campaign/d8 process, and confirmed zero active budget reservations. Telegram
  control remains active.

### 2026-09-03 — adaptive custom-only discovery campaign

- Converted the prompt audit into a new campaign rather than extending the
  ineffective rich-template run. Added `explicit_v2`, which retains the
  empirically successful single-hypothesis guidance and closes the dynamic-loop
  timeout failure, plus `lean_v2`, which remains deliberately underspecified as
  the high-diversity context-poisoning control.
- Extended indexed corpus samples with category, engine, syntax, marker, tag,
  and required-flag metadata. Added deterministic `stratified_v8` sampling over
  security artifacts, Wasm, compiler tiers/intrinsics, memory/GC, concurrency,
  regressions, and non-regressions. Eight-source windows are bounded to 120 KiB
  of source and preserve exact name/hash provenance.
- Added adaptive conversation rotation after a timeout, OOM, exact repeated
  program, two consecutive syntax errors, or two consecutive Wasm compilation
  failures. Ordinary exceptions retain the iterative history because the prior
  audit measured useful next-turn recovery around 73–74 percent.
- Replaced the active worker set with eight custom Luna lanes in four paired
  experiments: low and no reasoning under stratified ASan, low reasoning under
  uniform ASan, and low reasoning under stratified optdebug. Official OpenAI and
  Spark lanes are disabled; Spark auxiliary timers remain installed but will be
  inactive for this run.
- The full offline suite passes with 159 tests. Actual preview-v3 index loading
  yields 9,830 verified sources; smoke-built uniform and stratified windows each
  contain eight distinct references and fit the context boundary.
- Froze an early 126-generation audit in
  `docs/ADAPTIVE_CAMPAIGN_EARLY_AUDIT_2026-09-03.md`. Explicit v2 reached 87.1%
  exit 0 versus 76.8% for lean v2, with all hashes unique and zero candidates.
  However, every program in both variants used JIT intrinsics; mixed eight-source
  context caused subsystem mode collapse despite high textual diversity.
- Added immutable v3 prompts and homogeneous random focus pools for compiler,
  Wasm, memory/GC, pure ECMAScript, and security sources. Added `--jit-fuzzing`,
  focused Wasm tier/memory stress, and focused GC compaction/incremental stress.
  The focused campaign retains exact prompt pairing, custom Luna only, ASan as
  the main oracle, and optdebug for the compiler pair.
- Added an idempotent, feature-routed replay matrix that spends no model tokens.
  It schedules every preserved program with a prior successful execution under
  applicable alternate V8 configurations: optdebug compiler verification,
  concurrent compilation, forced deoptimization, ASan GC stress, Wasm
  tiering/memory movement/code GC, and the experimental RegExp engine. Exact
  profile/flag/program triples already in the catalog are skipped; every new
  execution remains linked to its originating generation and candidate alerts
  are sent immediately without stopping the matrix.
- Enabled two raw custom Terra lanes after Luna v3 established a strong
  validity baseline but continued to show model-family templates. Terra uses a
  separate hard 1,250-credit budget with the owner's exact 25M uncached-input,
  250M cached-input, and 4M output-token ceilings. The lanes use `high` rather
  than `xhigh` to avoid the observed upstream long-request timeout regime and
  focus on security and Wasm under ASan. No official API budget is used.

### 2026-09-03 — semantic-depth audit and interaction experiment

- Audited the first 497 focused-v3 primary executions. Explicit v3 reached
  87.1% exit 0 versus 79.1% for lean v3. Across 219 turns sharing the exact
  focus, corpus window, and turn index, explicit-only success occurred 35 times
  versus 15 lean-only successes. All 497 program hashes were unique.
- Textual diversity was not semantic diversity. Lean v3 nearly always emitted
  Proxy, TypedArray, Reflect, GC, and assorted language features together;
  explicit v3 heavily favored prepare/optimize sequences. The focused corpus
  was rotating correctly: 195 sessions had used 747 distinct source PoCs, and
  exact eight-token source/output overlap remained below 10% for every result.
- Found that context windows containing SharedArrayBuffer/Atomics almost never
  caused generated programs to exercise concurrency. Added a dedicated
  `focus_concurrency` pool and fixed its standalone-source filter: the old
  byte-substring check for global `load(...)` also rejected valid
  `Atomics.load(...)` examples. The corrected pool has 155 standalone samples
  below 15 KiB and produces valid 8- and 16-source windows.
- Added `interaction_v1`, a third prompt which permits independent
  recombination while requiring exactly two compatible mechanisms on one
  central path. Five bounded one-turn Luna workers cover compiler, Wasm,
  memory, security, and concurrency. They will produce 50 outputs with eight
  examples and 50 with sixteen, making context quantity directly observable in
  immutable generation metadata.
- Completed the initial feature-routed stress replay: 13,669 executions across
  compiler verification, concurrent compilation, forced deoptimization, GC,
  Wasm tiering/memory, and experimental RegExp profiles. It completed with zero
  infrastructure errors and zero bug candidates.
- Compacted Telegram `/workers` output to enabled workers plus a disabled count;
  this avoids stale disabled lanes exhausting Telegram's message limit while
  retaining full local configuration status. The full offline suite passes with
  165 tests.
- Completed the exact 100-generation interaction ablation. Eight-example
  contexts produced 46/50 exit-0 programs for 3.027 Luna credits; sixteen
  examples produced 44/50 for 3.697 credits. All hashes were unique and no
  candidate appeared. Despite the high validity, 99/100 outputs used JIT,
  0/20 concurrency outputs used SAB/Atomics/Worker, and 0/20 Wasm outputs ran a
  module. The prompt was rejected as a live strategy. Full evidence and the
  prompt-by-subsystem comparison are in
  `docs/GENERATION_QUALITY_AUDIT_2026-09-03.md`.
- Replayed 30 exit-0 generated programs with optimization tracing. All 30
  completed TurboFan compilation and 22 emitted an actual deoptimization
  bailout, proving the JIT-heavy templates reach optimized engine paths even
  though their hypothesis diversity is low.
- Replaced the generic 12-worker matrix with seven subsystem-specialized lanes:
  proven explicit compiler/memory/security Luna workers, dedicated Wasm,
  concurrency, and ordinary-language Luna prompts, plus the high-depth Terra
  security lane. Paused Terra Wasm and every generic lean lane. Wasm and
  concurrency corpus pools now reject assertion/harness, Sandbox, and native
  intrinsic contamination. Ten deterministic eight-example smoke windows per
  cleaned pool passed provenance validation; the full suite remains at 165
  passing tests.
- Inspected the first specialized canary programs directly rather than relying
  on routing metadata. Across 62 outputs, all 16 Wasm programs constructed a
  module/instance and reached for an export, all 20 concurrency programs used
  SharedArrayBuffer plus Atomics, and all 26 language programs avoided native
  intrinsics, Wasm, SAB, and Worker. All 62 program hashes were unique. The
  specialization therefore breaks the prior subsystem mode collapse.
- Canary validity differs sharply by subsystem: language completed 26/26,
  concurrency 13/20, and Wasm 6/16. Wasm failures were rejected raw binaries;
  concurrency failures were invalid wait/notify views or worker-protocol
  deadlocks. These are generator precision failures, not V8 candidates.
- Added compact deterministic program observations to iterative feedback. The
  next turn now sees prompt adherence, relevant static features, whether a
  successful Wasm/worker path completed, known corrective guidance for malformed
  Wasm and Atomics/deadlock failures, and summarized optimization/deoptimization
  traces. Enabled `--trace-opt`/`--trace-deopt` only on compiler and ordinary
  language lanes so later turns can distinguish real tiering/deoptimization from
  a merely successful exit.
- Added a second, builder-assisted Wasm lane while retaining the raw-byte lane
  as a control. It receives eight random examples from a clean pool of actual
  `WasmModuleBuilder` users and loads the official helper from the pinned V8
  revision through a read-only allowlisted container mount. The exact helper
  SHA-256, size, mount target, and V8 revision are recorded in each execution's
  immutable detail artifact. Ten corpus-window smokes covered 73 sources, and a
  real ASan container smoke instantiated and called an export successfully.
  The suite now passes 177 tests.
- Propagated each originating worker's allowlisted support files into the
  feature-routed stress replay. Without this, later alternate-flag executions
  of a valid builder-generated program would fail at `load()` instead of testing
  V8. The idempotence key remains program/profile/flags; support provenance is
  still captured in every replay detail artifact.
- Audited 175 post-feedback specialized turns. Compiler completed 41/42 with a
  median of four observed optimized compilations and one deopt; ordinary
  language completed 48/49 with medians of fourteen compilations and three
  deopts despite using no `%` intrinsic. Raw Wasm completed its intended path
  in 9/32, builder Wasm in 5/14, and concurrency in 19/38. The latter used
  `Atomics.wait`/`waitAsync` in 28/40 generated programs and timed out nine
  times. Iterative feedback recovered the next turn after 7/15 raw-Wasm and
  4/8 concurrency failures, so short conversations remain useful.
- Replaced the initial builder and concurrency prompts with immutable v2 lanes.
  Builder v2 starts from a straight-line known-valid export and permits only one
  advanced feature after measured success. Concurrency v2 requires a real
  Worker but forbids wait/waitAsync and busy polling, using message barriers
  instead. Historical v1 workers and exact evidence remain retained but disabled.
- Added dedicated Resizable ArrayBuffer and RegExp lanes to cover areas that the
  generic language worker neglected. Each has a subsystem-specific prompt,
  static adherence feedback, an eight-source clean focus pool, and ASan primary
  execution; the RegExp lane is also eligible for the experimental-engine stress
  replay. Ten window smokes covered 56 distinct buffer sources and 59 distinct
  RegExp sources. The full suite now passes 182 tests.
- Expanded stress routing so builder-only Wasm programs receive the Wasm tiering
  profile even if their generated source never spells `WebAssembly`, and RegExp
  programs using `exec`, `test`, `matchAll`, or `replaceAll` reach the
  experimental-engine replay. This closes two static-routing blind spots found
  while reviewing the first specialized outputs.
- Added four independent no-token replay oracles after live-smoke testing their
  exact flags on the pinned images: optdebug Maglev assertions (including type
  assertions), ASan GC-during-compilation race stress, optdebug heap verification
  under deterministic marking/compaction stress, and ASan Wasm secondary-stack
  execution. Every profile retains `--fuzzing`; none changes primary generation
  classification or treats timeout/OOM as a candidate.
- Froze and inspected the first 100 outputs of the builder-Wasm v2, wait-free
  concurrency v2, resizable-buffer, and RegExp lanes. All 100 hashes are unique;
  98 satisfy their static subsystem contract and 84 complete a useful exit-0
  path. This fixes the old cross-subsystem collapse, but exposes within-lane
  convergence: Worker favors one CAS/message template and builder Wasm favors
  imports plus memory growth.
- Expanded structured feedback with exact prior feature families, targeted
  correction for builder API/opcode/immediate mistakes and expected buffer or
  RegExp misuse, and compact actual Wasm compilation-tier/wrapper observations.
  The four specialized prompts now request a mechanism absent from the preceding
  successful turn. Active Wasm lanes run with
  `--trace-wasm-compilation-times`; a live ASan smoke observed both Liftoff and
  TurboFan compilation plus a Wasm-to-JS wrapper.
- Completed a 5,753-execution stress replay with zero errors/candidates and
  launched a second 16,520-execution matrix using the corrected routing and four
  new sensitive profiles. The complete quantitative interpretation is in
  `docs/GENERATION_QUALITY_AUDIT_2026-09-03.md`.
- Froze a second exact 100-program snapshot after feature-aware feedback. Exit-0
  rose from 86% to 89% and useful paths from 84% to 87%. Buffer validity reached
  22/22 while producing four transfer and ten BigInt-view cases, previously
  absent. Concurrency broadened into BigInt and growable SAB at a modest validity
  cost. Builder Wasm still produced zero table/GC/SIMD/exception cases, proving
  that more instructions alone do not overcome its memory/import prior.
- Added a separate advanced-builder Wasm lane backed by clean corpus examples
  that actually contain GC/reference, table/indirect-call, SIMD, or exception
  builder constructs. Ten deterministic windows cover 70 distinct sources. The
  prompt selects one exact corpus-demonstrated advanced family and one JS
  boundary; it retains eight examples because the 16-example ablation was more
  expensive and less valid.
- Completed the second 16,520-execution sensitive replay with zero
  infrastructure errors and zero candidates. Together with the prior round,
  the specialized corpus has now received 22,273 alternate-flag executions
  without a native finding.
- The advanced Wasm lane immediately reached table/call-indirect, SIMD, and GC
  programs and actual Liftoff/TurboFan compilation, but exposed repeatable
  builder precision errors. Added exact, measured feedback for prefixed opcode
  encoding, official `SimdInstr`/`GCInstr` spelling, table export kinds, and
  element indices. A high-reasoning custom Terra companion lane produced its
  first two table/SIMD programs successfully and is retained as a low-volume
  depth comparison.
- Built and packaged an optimized UBSan+vptr d8 image from the pinned Chrome
  152 V8 revision. The isolated image smoke test passed. Native sanitizer/fatal
  signatures now require nonzero termination and stderr evidence, preventing a
  generated `print('runtime error: ...')` from forging a candidate. Every
  preserved successful program is routed through the new UBSan replay oracle.
- Completed the UBSan+vptr matrix: 11,853 executions over 5,854 preserved
  programs, zero infrastructure errors and zero native candidates.
- Built and packaged a pinned optimized TSan d8 image. Docker's default seccomp
  policy blocked TSan's required `personality(ADDR_NO_RANDOMIZE)` call on this
  high-ASLR host, so only the TSan runner opts out of default seccomp while
  retaining no network, read-only rootfs, all capabilities dropped,
  no-new-privileges, and resource limits. Image smoke and an end-to-end
  generated Worker/SAB execution passed; the full concurrency replay is active.
- Fixed permanent pauses after a single generic provider failure. The supervisor
  now performs at most three consecutive retries with 5/15/60-second backoff,
  only for `provider_error`. It never auto-resumes quota/rate-limit, budget,
  unknown-usage, or owner pauses. The stalled Terra advanced session was
  recovered through the new path.
- Froze a 2,902-program custom-only long-run snapshot of the specialized matrix.
  Non-Wasm lanes completed 92.9% of programs, while the three Wasm strategies
  plus the Terra depth lane completed 51.4%; no native candidate appeared. This
  localizes the remaining validity bottleneck to Wasm construction rather than
  the executor, corpus rotation, or general JS generation.
- Completed the 8,831-execution delta/stress matrix with zero errors and zero
  candidates. Its 1,725 TSan executions produced 1,660 clean exits, 59 bounded
  timeouts, six ordinary nonzero/JS outcomes, and no TSan data-race diagnostic.
- Classified the current advanced-Wasm failures and added exact next-turn
  feedback for direct-call and SIMD-memory aliases, the official S128 constant
  helper, builder objects used instead of numeric indices, `call_indirect`
  stack/immediate order, and undeclared function references. This targets the
  measured error modes without further enlarging the system prompt.
- Built and packaged the pinned Chrome 152 V8 MSan/chained-origins profile.
  Binary SHA-256 is
  `4f0973f004b46c0e2262eb3e4a6c8fff17b03cc5774a9cd21c7af369583abfaf`;
  image ID is
  `sha256:e433aa8b9215364a4ab3d748514510ffaba65cc6884ec9657297194e4fd99f14`.
  The instrumented loader requires the official checkout-relative runtime
  layout and, like TSan on this host, an ASLR-capable unconfined seccomp profile.
  Network isolation, read-only root, dropped capabilities, no-new-privileges,
  PID/CPU/memory limits, exact manifests, and stderr-only candidate detection
  remain enforced. Image smoke and a real preserved-program execution passed.
- Added an ASan aggressive Wasm-inlining replay using sync tier-up,
  call-count-independent Wasm inlining, JS-to-Wasm inlining, and optimized
  inlined wrappers. The new replay matrix plans 19,320 executions over 8,066
  preserved programs: 8,064 MSan and 2,449 aggressive-Wasm executions plus
  ordinary deltas.
- Replaced the stress planner's quadratic correlated success lookup with an
  equivalent deduplicated SQLite selection. Planning the current 8,066-program
  corpus now takes about four seconds instead of minutes.
- Froze a post-correction advanced-Wasm canary at 04:10:42Z. Luna completed
  52/85 programs; Terra high completed 70/75, including valid indirect calls,
  SIMD, GC/reference types, Wasm exceptions, and memory cases. Settled cost per
  successful result remains roughly nine times higher for Terra (0.822 credits
  versus 0.089 for Luna), supporting its use as a depth rather than throughput
  lane.
- Added deterministic per-program `--random-seed` and
  `--fuzzer-random-seed` flags to primary campaign execution. Added four
  feature-routed replay oracles for Maglev future features, Turbolev future plus
  Turboshaft verification, randomized MinorMS/young-generation GC, and Wasm
  staging with type assertions. A selective newest-first replay CLI and queued
  2,616-case priority service put these higher-yield checks ahead of the full
  48,901-case historical expansion. All 206 tests pass and all new flag vectors
  passed smoke execution on their pinned images.
- Added a compact semantic fingerprint to every next-turn observation. It hashes
  bounded engine mechanisms, builtin/intrinsic operations, constructors, and
  coarse control-flow shape while ignoring local identifier names. Iterative
  feedback now explicitly identifies renamed copies of the same engine-facing
  template, addressing the gap between 100% unique source hashes and much lower
  behavioral diversity without adding a second model call.
- Replaced the 382-word generic explicit prompt on the compiler and memory lanes
  with 230- and 227-word subsystem contracts. The compiler lane now concentrates
  on one hot Turbolev path and one optimizer-assumption transition; the memory
  lane concentrates on one bounded lifetime/write-barrier/backing-store
  transition under MinorMS. Both print a deterministic checksum and use the
  semantic signature to avoid renamed template repeats.
- Added a private cross-session semantic-novelty ledger and deterministic
  historical backfill. Only successful d8 paths enter its frequency baseline;
  each next-turn observation now reports whether its signature repeated
  globally and which mechanism/operation families are genuinely new for that
  worker. Failed programs remain repairable without falsely consuming novelty.
- Adaptive sessions now rotate their corpus after two consecutive successful
  paths whose semantic signatures were already seen globally, even when the
  source text differs. A single repeated path still gets the next turn to react
  to feedback; two repeats stop spending context on a known template.
