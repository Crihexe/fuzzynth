# Fuzzynth implementation and experiment plan

Status: active implementation

Date: 2026-09-02

## 1. Experiment statement

Fuzzynth will test whether high-volume, corpus-conditioned language-model output
can discover genuine defects in V8 without using Fuzzilli or making source-guided
analysis the primary engine.

The system will not equate volume with effectiveness. It will run several
campaign families in parallel, preserve every raw generation and execution, and
compare yield, novelty, reproducibility, and cost across strategies.

Documentation note: the public model sheet for
[`gpt-5.3-codex`](https://developers.openai.com/api/docs/models/gpt-5.3-codex)
documents API streaming, but the exact requested identifier
`gpt-5.3-codex-spark` is not currently present in that public model catalog.
OpenAI's public use-case catalog does refer to
[Codex-Spark](https://learn.chatgpt.com/use-cases). Consequently, Fuzzynth must
preserve the requested provider identifier and discover its actual API behavior
with a bounded capability probe rather than infer or substitute capabilities.

Primary outcome classes:

1. symbolized native crash or failed V8 check;
2. ASAN/UBSAN finding;
3. reproducible semantic mismatch across valid execution profiles;
4. reproducible extreme resource behavior that is not intentionally constructed;
5. expected exception, trap, timeout, explicit abort, or historical replay.

Only the first three are initially treated as strong bug candidates. The final
classification remains a triage decision, not a model decision.

## 2. System shape

```text
 alternate provider       official provider
    | complete SSE         | complete JSON
    +-----------+----------+
          v
  +-----------------------+       +----------------------+
  | controller            |<----->| Telegram control     |
  | budgets + campaigns   |       | authenticated owner  |
  +-----------+-----------+       +----------------------+
              |
              v
  +-----------------------+
  | durable scheduler     |
  | leases + backpressure |
  +-----+------------+----+
        |            |
        v            v
 iterative JS     later tool/agent
 workers          investigator
        |            |
        +-----+------+
              v
  +-----------------------+
  | isolated d8 executor  |
  | release/debug/san     |
  +-----------+-----------+
              v
  +-----------------------+
  | evidence + triage     |
  | SQLite + artifacts    |
  +-----------------------+
```

The API-facing controller and the target executor are separate trust zones. The
controller holds provider/Telegram secrets; `d8` workers never receive them.

## 3. Campaign matrix

Weights will be configuration, not constants. The initial matrix deliberately
separates provider effects from model and generation effects:

| Worker | Provider/model | Parameters | Session turns | Status |
|---|---|---|---|---|
| `spark-custom-iterative-js` | alternate Spark | `none`, high verbosity; unsupported controls omitted | randomized 8–16 | enabled; quota-paused independently |
| `luna-custom-xhigh-iterative-js` | alternate GPT-5.6 Luna | `xhigh`, high verbosity; unsupported controls omitted | randomized 4–8 | sampled, disabled after upstream timeout |
| `luna-custom-high-iterative-js` | alternate GPT-5.6 Luna | `high`, high verbosity; unsupported controls omitted | randomized 4–8 | enabled replacement |
| `luna-custom-low-iterative-js` | alternate GPT-5.6 Luna | `low`, high verbosity; unsupported controls omitted | randomized 4–8 | enabled baseline |
| `luna-custom-none-spark-fallback-js` | alternate GPT-5.6 Luna | `none`, high verbosity; unsupported controls omitted | randomized 4–8 | managed fallback while Spark is paused |
| `luna-official-high-temperature-none-js` | official GPT-5.6 Luna | `none`, high verbosity, temperature 1.2/1.5/1.8 per session | randomized 4–8 | enabled |
| `terra-custom-xhigh-tool-investigator` | alternate GPT-5.6 Terra | `xhigh`, tool-driven | separately bounded | disabled/deferred |

The scheduler must be able to set a campaign to zero, pause it, or cap it without
affecting the others. Parallelism is bounded separately for API requests, normal
executions, sanitizer executions, and triage replays.

## 4. Iterative complete-response design

### 4.1 Turn lifecycle

1. Select one bounded corpus window for the session and combine it with the
   stable JavaScript-only generation contract.
2. Make one Responses request. Alternate workers use SSE and official workers use
   complete JSON. Buffer through the terminal response, then persist the exact
   request JSON, raw provider response/SSE, and extracted semantic output as
   separate immutable artifacts.
3. Treat the complete assistant output as the canonical program without repairing
   prose, fences, or syntax.
4. Execute it immediately in a fresh isolated `d8` process and persist the exact
   program, output, termination facts, worker identity, and resource envelope.
5. Return only a compact factual observation to the next turn. Do not ask the
   model to classify a crash or claim a V8 bug.
6. On an ordinary outcome, continue until the randomized session limit. On a
   crash candidate, save evidence, stop the session, and alert the owner without
   automatic replay or model investigation.

### 4.2 Session and context policy

Spark sessions run for 8–16 turns; Luna sessions run for 4–8 turns. The exact
length and official Luna temperature choice are sampled once per session and
recorded. Recent history is bounded independently of the full
archived history. A new session receives a newly sampled corpus window.

The first conditioned run uses four owner-approved examples selected from dataset
v2. An explicit unconditioned control can bypass corpus selection. Every live
campaign entry point requires `--live`, durable control permission, and a budget
reservation before provider dispatch.

Streaming is a transport property, not an incremental-execution strategy. No
bytes reach d8 while a custom stream is active. Completed responses require the
assembled deltas to equal terminal semantic output. When an error or incomplete
terminal event follows text deltas, the bounded exact prefix is marked partial
and executed once after termination; absent usage remains conservatively
reserved and the lane pauses. The custom request omits `max_output_tokens`,
`max_completion_tokens`, `temperature`, and `top_p`; local response/program byte
limits and worst-case token reservations remain in force.

### 4.3 Evidence and resource safeguards

- Bound response bodies, extracted programs, feedback, retained context, worker
  wall time, output bytes, memory, CPU, and PIDs.
- Apply a durable reservation before each provider request and settle it only
  from provider-reported usage.
- Preserve bounded error bodies and incomplete/failed request evidence without
  treating them as executable programs.
- Pause on missing usage, unknown cost, quota/rate errors, or exhausted budgets;
  never count unknown work as free.
- Run A/B controls with matched budgets after dataset integration to measure
  context lifetime and conditioning effects.

### 4.4 Parameter and context experiments

Provider capability is part of each experiment identity. The alternate endpoint
must omit `temperature`; the official Luna endpoint may test a bounded set of
temperature values. Unsupported parameters are never sent merely to make rows
look uniform.

The initial matrix intentionally fixes alternate Luna at `xhigh` and alternate
Spark at its minimum accepted reasoning, both with high verbosity. Official Luna
uses `none` or `low` reasoning and high verbosity while sampling temperatures
1.2, 1.5, and 1.8 per session. Later fixed-budget experiments can isolate each
factor; high effort or verbosity is a hypothesis, not an assumed improvement.

Initial capability probes refine this matrix: the alternate endpoint accepts a
requested `none` but reports effective effort `low`, reports temperature `1.0`
when the parameter is omitted, and reflects requested verbosity. The official
Luna endpoint preserved temperature with `reasoning=none`, but the first campaign
request rejected temperature with `reasoning=low`; these are distinct experiment
lanes and the incompatible combination is disabled. Every case must store
requested and effective parameters; HTTP success alone is not a capability
assertion.

Later context-lifetime comparisons may include:

- `fresh`: one program, then a new conversation;
- `short`: reset after a small randomized number of programs;
- `medium`: retain a bounded session and rotate dataset windows periodically;
- `long`: rarely reset, but enforce a hard input-token ceiling;
- `adaptive`: reset on mode collapse, repetition, or cost threshold.

Dataset windows are independently sampled and versioned. Experiments compare
frequent versus rare conversation resets at equal generated-token and execution
budgets. Archive full histories, but never resend the entire archived history by
default.

## 5. JavaScript and WebAssembly output formats

Raw JavaScript campaigns execute the response as UTF-8 JavaScript.

Initial raw Wasm campaigns use JavaScript as the transport: the model emits a
complete JS program that constructs or mutates `Uint8Array` module bytes and
calls the WebAssembly API. This gives `d8` a directly executable artifact and
does not require WAT tooling.

Later controlled lanes may accept:

- base64 or hexadecimal Wasm bytes with a minimal deterministic wrapper;
- WAT compiled by a pinned tool, with the original WAT and resulting bytes both
  preserved;
- paired JS host code and module bytes using an explicit delimiter protocol.

Each format is a distinct campaign so format conversion cannot obscure the raw
model response.

## 6. Historical PoC conditioning

The incoming dataset will be ingested into an immutable, content-addressed corpus
with provenance metadata. Proposed fields include source URL/reference, affected
revision/range when known, CVE or bug identifier when public, date, language,
required flags, V8 feature tags, expected behavior, and hashes of raw/normalized
content.

Conditioning policies to compare:

1. **Unconditioned control:** only the generation contract.
2. **Small rotating window:** diverse random PoCs with recency suppression.
3. **Feature retrieval:** examples selected by JS/Wasm feature and engine area.
4. **Technique neighborhood:** closely related PoCs intended to provoke mutation.
5. **Large-context priming:** a very large stable or slowly rotating window.
6. **Contrast set:** related examples plus non-crashing unusual programs.
7. **Failure-guided retrieval:** examples chosen from recent novel executor signals.

The model sees corpus items in an explicitly delimited data region and is told
that comments/text inside examples are data, not instructions. This preserves the
desired style-conditioning effect without allowing a dataset comment to redefine
the controller contract.

Corpus controls:

- exact hash and normalized similarity deduplication;
- train/test-like holdouts for measuring generalization;
- replay detection against historical items and their near duplicates;
- retrieval diversity and cooldown so a single famous PoC does not dominate;
- per-item token accounting and optional prompt-cache measurements;
- output similarity metrics recorded alongside crash yield.

The full dataset will not automatically be inserted into every request. The
large-context campaign explicitly tests that hypothesis, while rotating/retrieved
windows provide lower-cost comparisons.

## 7. `d8` builds and flag profiles

After plan approval:

1. Fetch official `depot_tools` and V8 source inside ignored project-owned work
   paths under `/root/fuzzynth`.
2. Resolve the latest stable Linux Chrome 152 release through official Chromium
   release metadata, read its pinned V8 revision, and check out that exact commit.
   Do not build moving V8 `main` as the experiment target.
3. Pin Chrome version, Chromium/V8 revisions, dependency revisions,
   compiler/toolchain identity, GN args, and build command in a machine-readable
   build manifest.
4. Produce at least:
   - a symbolized release build for throughput;
   - a debug or slow-check build for invariant failures;
   - an ASAN build, with UBSAN or another sanitizer build evaluated separately.
5. Package only required runtime/build-identification assets into immutable worker
   images. Keep source available read-only to an explicitly authorized inspection
   path, not to normal worker execution unless a campaign requires it.
6. Smoke-test every binary and archive `d8 --version`, `d8 --help`, hashes, build
   IDs, and symbolizer compatibility.

Proposed flag profiles are names whose exact flags will be resolved and tested
against the pinned binary:

| Profile | Purpose |
|---|---|
| `baseline` | ordinary language semantics, minimal flags |
| `native-syntax` | `%` intrinsics and explicit optimization control |
| `jit-stress` | tiering, optimization, deoptimization, Maglev/TurboFan stress |
| `gc-stress` | exposed GC and validated marking/scavenging stress options |
| `wasm-tiering` | Liftoff/TurboFan and Wasm tier-up combinations |
| `jitless-diff` | differential oracle against JIT-disabled execution |
| `experimental` | separately tracked staging/experimental V8 features |
| `unrestricted-lab` | broad valid flags/helpers, never used alone to claim a bug |

The controller validates flags against a generated manifest. The model chooses
profile names plus approved per-case options, not arbitrary host command lines.

Useful `%` intrinsics and shell helpers remain available in dedicated lanes. Code
is not rejected merely for containing them. Instead, the classifier annotates
known intentional exits/aborts and requires reproduction in a reportable profile
before escalating a finding.

## 8. Executor isolation

Each authoritative case runs in a fresh process. A worker container should have:

- no network;
- no provider or Telegram environment variables;
- non-root UID, dropped capabilities, `no-new-privileges`, and a restrictive
  seccomp profile compatible with V8 JIT requirements;
- read-only root filesystem and binary/source mounts;
- size-bounded tmpfs scratch space;
- cgroup CPU/memory/PID limits plus per-process wall timeout;
- explicit core-dump policy and a controlled artifact channel;
- deterministic locale/timezone and recorded environment allowlist.

The later persistent REPL tool is secondary. Its transcript, generation, restart
count, state lifetime, and final crash evidence will be recorded; replay remains
a deliberate human-triggered triage action.

## 9. Finding classification and reproduction

The first-pass classifier consumes facts, not model judgments:

- process exit code and terminating signal;
- stderr/stdout signatures;
- ASAN/UBSAN reports;
- V8 `CHECK`, `DCHECK`, fatal error, and stack output;
- timeout and cgroup OOM evidence;
- exact source scan annotations for explicit termination helpers;
- differential output after normalizing only known nondeterministic fields.

Initial candidate workflow:

1. store the original program, provider evidence, and execution evidence;
2. mark the session terminal so it cannot generate another turn;
3. emit a private alert containing metadata and artifact IDs, not the full PoC;
4. leave replay, validation, symbolization, deduplication, minimization, and model
   investigation to a later explicit human action.

The later triage design may replay across release, optdebug, ASAN, and differential
profiles, then calculate stable signatures and minimize immutable descendants.
None of those actions runs automatically in the initial campaign phase.

## 10. Storage and project memory

The single-host implementation uses SQLite in WAL mode for structured
state and content-addressed filesystem storage for large/raw artifacts. Database
rows reference immutable artifact hashes.

Minimum entities:

- campaigns and configuration revisions;
- exact request JSON, raw provider response JSON, and semantic model output;
- generated cases and parent/corpus relationships;
- executions, environments, outputs, resource limits, and exact detail manifests;
- findings, signatures, reproductions, and minimization lineage;
- provider usage, price revisions, durable reservations, and budget events;
- resumable campaign sessions, turn attempts, and terminal reasons;
- Telegram/control actions and authenticated actor identity;
- V8 build and flag-profile manifests.

Runtime state, V8 source/build output, datasets, and crash artifacts are ignored by
Git by default. Documentation, schemas, migrations, configuration examples, and
small synthetic test fixtures belong in Git.

`PROJECT_LOG.md` is the human-readable project memory. It is updated at meaningful
checkpoints and consulted before work resumes.

## 11. Cost accounting and backpressure

Cost calculations use a versioned provider price configuration. Provider usage is
stored raw; a local tokenizer/byte estimate is also recorded so missing or delayed
usage is visible.

Implemented initial controls:

- per-request response, program, feedback, context, and wall-time ceilings;
- durable worst-case reservations before network access;
- cumulative alternate-Luna ceilings of 1250 credits, 250M uncached-input
  tokens, 2.5B cached-input tokens, and 42M output/reasoning tokens;
- cumulative official-Luna local ceiling of `$4.90`, below the owner's external
  `$5` account limit;
- alternate Spark usage recording with quota/rate failures causing a pause;
- conservative reservation retention and a pause when usage is missing or
  ambiguous;
- explicit owner-controlled resume rather than an automatic budget reset.

Per-campaign concurrency, rolling-rate controls, spend projection, and cost per
novel finding remain later scheduler/dashboard work. Terra stays disabled and
will receive an independent budget before it is ever enabled.

If the provider omits usage, policy chooses between conservative estimated billing
and pause. It must never silently count the request as free.

## 12. Telegram control and development updates

Telegram access is restricted to the configured chat and owner identity. The
deployed commands are:

- `/status`, `/workers`, `/sessions`;
- `/cost`, `/budget`;
- `/pause <worker|all>`, `/resume <worker|all>`;
- `/stop CONFIRM`, `/start CONFIRM`;
- `/lastcrash`.

State-changing commands are audited and idempotent; global stop/start require the
exact confirmation word. Changes are enforced before the next model turn and do
not terminate an in-flight `d8` process. No command maps to an arbitrary shell,
provider request, replay, or campaign start.

A separate developer-update script will send commit/test/milestone summaries to
the owner once Telegram is configured. It must omit credentials and raw sensitive
PoCs and must not make campaign correctness depend on Telegram availability.

## 13. Milestones and acceptance gates

### M0 — Plan and review (complete)

- Planning, instructions, and project log committed and pushed.
- No provider calls, V8 download/build, or implementation.
- Gate: owner approval received on 2026-09-01.

### M1 — Skeleton and provider capability probe (complete)

- Configuration/secrets boundary, database skeleton, CLI, logging, and tests.
- Probe exact alternate-provider support for `gpt-5.3-codex-spark`, Luna,
  Responses, usage, reasoning controls, verbosity, and prompt caching without
  exposing credentials. Record that
  alternate temperature is unsupported and omit it from requests.
- Probe official Luna separately with a bounded high-temperature check.
- Gate: recorded capability matrix and bounded-cost smoke test.

### M2 — Reproducible V8 build supply chain (complete)

- Official V8 checkout and pinned release/debug/sanitizer `d8` builds.
- Build manifests, hashes, symbols, smoke tests, and worker images.
- Gate: identical test case reproducible across fresh workers.

### M3 — Executor and evidence collector (complete)

- Fresh-process runner, resource limits, classification, artifact storage, and
  synthetic crash/timeout/exception tests.
- Gate: evidence completeness audit and no secrets inside worker.

### M4 — Complete-response iterative campaign controller (complete)

- Exact request/raw-response/program capture, bounded context feedback, fresh
  execution per turn, durable sessions, multidimensional budgets, crash/pause
  alerts, and offline status inspection are implemented.
- Alternate requests use complete SSE transport and official requests use
  complete JSON responses. Reviewed v2 corpus selection, explicit live start,
  reconciled usage, and the controlled canary all passed.

### M5 — Dataset ingestion and conditioning experiment

- Provenance, deduplication, retrieval/window policies, replay detection, and
  unconditioned controls.
- Gate: fixed-budget comparison report across conditioning policies.

### M6 — Controlled worker comparison

- Run Spark custom, Luna custom `xhigh`, and official Luna temperature sessions
  under matched execution and recorded-token budgets.
- Gate: validity, novelty, throughput, usage, and cost report with zero lost
  evidence records.

### M7 — Tool-driven Terra investigator (deferred)

- Design a small, separately budgeted Terra `xhigh` tool lane only if initial
  worker results justify its 10x credit cost.
- Gate: explicit owner-reviewed budget and tool/evidence protocol.

### M8 — Manual triage, dashboard, and Telegram control

- Human-triggered finding lifecycle, reducer, symbolization, authenticated
  commands, and cost dashboard. Crash/pause alerts, authenticated runtime control,
  budget status, and development updates are already implemented.
- Gate: end-to-end synthetic incident drill and owner review.

### M9 — Long-running experiment

- Calibrated campaign mix, operational runbooks, recovery tests, retention policy,
  and periodic experiment reports.
- Gate: owner authorizes sustained spend and runtime.

## 14. Metrics for deciding whether the idea works

- valid-program and valid-module rate;
- unique normalized program rate and historical-PoC similarity;
- executions and model tokens per second;
- time/cost per execution;
- novel behavioral signatures per million generated tokens;
- reproducible strong candidates per campaign and flag profile;
- sanitizer/debug/release conversion rate;
- minimization success and final artifact size;
- proportion of findings that are intentional aborts, OOMs, or other false leads;
- stateful versus stateless yield;
- conditioned versus unconditioned yield at equal token and execution budgets;
- short versus longer session yield and cost.

## 15. Open decisions for owner review

1. Whether to add V8 `main` later as a secondary target; the primary target is
   now the latest stable Chrome 152 V8 revision.
2. Whether the first long run prioritizes sanitizer yield or release throughput.
3. Whether source inspection is exposed only to investigators or also to raw
   campaigns through `d8` file helpers.
4. Dataset window sizes, sampling policy, and context-lifetime comparison order.
5. Initial concurrent session counts for each of the three workers.
6. Dataset licensing/provenance requirements and whether private items must stay
   outside the Git repository.
7. Retention period and encryption requirements for potentially sensitive crash
   artifacts.

## 16. Immediate implementation action

Keep the supervised v2 campaign running with independent provider-failure
isolation, complete SSE capture for custom workers, hard cumulative budgets, and
the managed Spark-to-Luna fallback. Inspect validity, novelty, usage, latency, and
execution outcomes while the owner finishes dataset v3. Integrate that dataset
immutably only after review; keep automatic replay/minimization and Terra
disabled during this first-pass discovery run.
