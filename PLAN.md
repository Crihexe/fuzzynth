# Fuzzynth implementation and experiment plan

Status: proposed for owner review

Date: 2026-09-01

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

## 2. Proposed system shape

```text
 custom OpenAI-compatible provider
          | streaming / responses
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
 raw-stream       tool/agent campaigns
 workers          and investigator
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

Weights will be configuration, not constants. An initial experiment allocation
for discussion is:

| Campaign | Initial share | Generator | Feedback |
|---|---:|---|---|
| Raw JS stream | 30% | `gpt-5.3-codex-spark` | next-turn compact result |
| Raw Wasm-via-JS stream | 15% | `gpt-5.3-codex-spark` | next-turn compact result |
| Independent raw turns | 10% | Spark and Luna controls | none or corpus seed only |
| Tool-driven JS | 15% | GPT-5.6 Luna | typed execution tools |
| Tool-driven Wasm | 10% | GPT-5.6 Luna | typed execution tools |
| Mutation/recombination | 10% | configurable | selected corpus parents |
| Replay/differential triage | 5% | deterministic first | variant results |
| Minimization/investigation | 5% | Luna plus deterministic reducer | crash signature |

The scheduler must be able to set a campaign to zero, pause it, or cap it without
affecting the others. Parallelism is bounded separately for API requests, normal
executions, sanitizer executions, and triage replays.

## 4. Raw continuous-stream design

### 4.1 Sealed-response mode (first implementation)

1. Send a short stable instruction prefix plus the selected corpus context.
2. Request plain text with no tool schema, JSON, or Markdown wrapper.
3. Spool streaming deltas byte-for-byte to an append-only temporary artifact
   while recording event type, order, and monotonic timestamp.
4. On successful response completion, atomically seal the artifact, hash it, and
   execute the entire response as one program in a fresh `d8` process.
5. Store the response ID, finish reason, usage, model identifier returned by the
   provider, request parameters, and complete execution result.
6. Feed only a compact, bounded observation into the next turn when the campaign
   is stateful. Stateless campaigns start from a fresh conversation every time.

Raw output is never silently repaired. If it contains prose or fences, that exact
text is executed and likely classified as a syntax failure. A separate derived
lane may later test explicitly named normalizers while retaining the raw parent.

### 4.2 Speculative streaming execution (experimental)

Three variants need measurement:

- **PTY feed:** forward deltas into an interactive `d8` process. Lowest latency,
  but REPL parsing, scope, automatic semicolon insertion, and partial-token
  boundaries can change program semantics.
- **Complete-unit feed:** accumulate until a conservative lexical boundary, then
  send complete top-level units to a PTY. Safer than raw deltas but requires a
  boundary detector and still differs from file execution.
- **Snapshot execution:** at selected byte/line thresholds, run immutable prefix
  snapshots in fresh processes. Expensive, but deterministic and easy to replay.

Every speculative finding must be reproduced from a sealed standalone artifact.
When a speculative process crashes, record the exact consumed byte offset and
either cancel the provider stream to save cost or continue spooling according to
a campaign policy. Both choices will be tested.

Recommendation: ship sealed-response mode first, then add snapshot execution,
and treat direct PTY feed as a research lane rather than the authoritative path.

### 4.3 Stream protocol safeguards

- Bound maximum output tokens, bytes, wall time, and idle time.
- Preserve incomplete responses and network failures as artifacts but do not
  confuse them with valid completed turns.
- Apply backpressure: a fast model must not create an unbounded execution queue.
- Record time-to-first-token, token/byte throughput, time-to-execution, queue
  latency, and cancellations.
- Avoid tool calls in the raw lane; use a compact textual observation only when
  feedback is enabled.
- Run A/B controls with identical prompts but no feedback to measure whether
  stateful history helps or causes mode collapse.

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
2. Pin the V8 Git revision, dependency revisions, compiler/toolchain identity,
   GN args, and build command in a machine-readable build manifest.
3. Produce at least:
   - a symbolized release build for throughput;
   - a debug or slow-check build for invariant failures;
   - an ASAN build, with UBSAN or another sanitizer build evaluated separately.
4. Package only required runtime/build-identification assets into immutable worker
   images. Keep source available read-only to an explicitly authorized inspection
   path, not to normal worker execution unless a campaign requires it.
5. Smoke-test every binary and archive `d8 --version`, `d8 --help`, hashes, build
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

The persistent REPL tool is secondary. Its transcript, generation, restart count,
state lifetime, and final crash evidence are recorded; every interesting result
is replayed as a standalone file.

## 9. Finding classification and reproduction

The first-pass classifier consumes facts, not model judgments:

- process exit code and terminating signal;
- stderr/stdout signatures;
- ASAN/UBSAN reports;
- V8 `CHECK`, `DCHECK`, fatal error, and stack output;
- timeout and cgroup OOM evidence;
- exact source scan annotations for explicit termination helpers;
- differential output after normalizing only known nondeterministic fields.

Candidate workflow:

1. store the original artifact before any replay;
2. rerun in fresh workers and require a configurable reproduction threshold;
3. replay across release/debug/sanitizer and relevant differential profiles;
4. symbolize and calculate a signature from failure type and stable stack frames;
5. deduplicate against historical and current findings;
6. minimize with deterministic reductions first, optionally followed by an LLM
   investigator that cannot overwrite the original;
7. emit a private alert containing metadata and artifact IDs, not the full PoC;
8. prepare a separate responsible-disclosure bundle only after human review.

## 10. Storage and project memory

The first single-host implementation can use SQLite in WAL mode for structured
state and content-addressed filesystem storage for large/raw artifacts. Database
rows reference immutable artifact hashes.

Minimum entities:

- campaigns and configuration revisions;
- model requests/responses and streaming events;
- generated cases and parent/corpus relationships;
- executions, environments, outputs, and resource samples;
- findings, signatures, reproductions, and minimization lineage;
- provider usage, local usage estimates, price revisions, and budget events;
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

Required controls:

- per-request maximum input/output;
- concurrent request and tokens-per-minute governors;
- campaign hourly/daily/total budgets;
- global hourly/daily/total budgets;
- warning thresholds, hard pause, and explicit owner resume;
- spend projection using recent moving averages;
- generation cost, execution cost, and cost per novel/reproduced finding;
- cancellation accounting for speculative streams.

If the provider omits usage, policy chooses between conservative estimated billing
and pause. It must never silently count the request as free.

## 12. Telegram control and development updates

After credentials are supplied, Telegram access is restricted to configured chat
and user IDs. Proposed commands:

- `/status`, `/campaigns`, `/workers`, `/queue`;
- `/cost`, `/budget`, `/rate`;
- `/pause [campaign]`, `/resume [campaign]`, `/stop`;
- `/lastcrash`, `/finding <id>`, `/replay <id>`;
- `/help`.

State-changing commands are audited and idempotent; dangerous global actions may
require a short confirmation token. No command maps to an arbitrary shell.

A separate developer-update script will send commit/test/milestone summaries to
the owner once Telegram is configured. It must omit credentials and raw sensitive
PoCs and must not make campaign correctness depend on Telegram availability.

## 13. Milestones and acceptance gates

### M0 — Plan and review (current)

- Planning, instructions, and project log committed and pushed.
- No provider calls, V8 download/build, or implementation.
- Gate: explicit owner approval.

### M1 — Skeleton and provider capability probe

- Configuration/secrets boundary, database skeleton, CLI, logging, and tests.
- Probe exact custom-provider support for `gpt-5.3-codex-spark`, Luna, Responses,
  streaming event shape, cancellation, usage, temperature, reasoning controls,
  and prompt caching without exposing credentials.
- Gate: recorded capability matrix and bounded-cost smoke test.

### M2 — Reproducible V8 build supply chain

- Official V8 checkout and pinned release/debug/sanitizer `d8` builds.
- Build manifests, hashes, symbols, smoke tests, and worker images.
- Gate: identical test case reproducible across fresh workers.

### M3 — Executor and evidence collector

- Fresh-process runner, resource limits, classification, artifact storage, replay,
  and synthetic crash/timeout/exception tests.
- Gate: evidence completeness audit and no secrets inside worker.

### M4 — Raw sealed-stream campaign

- Byte-exact streaming spool, sealed execution, budgets, scheduler, backpressure,
  and JS/Wasm-via-JS campaigns.
- Gate: controlled run with zero lost cases and reconciled usage.

### M5 — Tool-driven and REPL campaigns

- Typed tools, batch execution, persistent console, reset/replay, and context
  compaction.
- Gate: all interesting REPL signals replayed standalone.

### M6 — Dataset ingestion and conditioning experiment

- Provenance, deduplication, retrieval/window policies, replay detection, and
  unconditioned controls.
- Gate: fixed-budget comparison report across conditioning policies.

### M7 — Speculative stream execution

- Snapshot mode first; complete-unit and PTY feed only after measured review.
- Gate: byte-offset evidence and standalone reproduction semantics demonstrated.

### M8 — Triage, minimization, cost dashboard, and Telegram

- Finding lifecycle, reducer, symbolization, alerts, authenticated commands, and
  development-update scripts.
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
- sealed versus speculative yield and cost.

## 15. Open decisions for owner review

1. First pinned V8 target: current main, a current stable revision, or both.
2. Whether the first long run prioritizes sanitizer yield or release throughput.
3. Whether source inspection is exposed only to investigators or also to raw
   campaigns through `d8` file helpers.
4. Initial campaign weights and total budget.
5. Cancellation policy when speculative execution crashes before generation ends.
6. Dataset licensing/provenance requirements and whether private items must stay
   outside the Git repository.
7. Retention period and encryption requirements for potentially sensitive crash
   artifacts.
8. Telegram confirmation policy for global stop/resume and replay commands.

## 16. Immediate next action after approval

Implement M1 as a small, tested vertical slice. Do not start an unbounded campaign.
The first provider call will be a capped capability probe using the custom base URL,
and the result will be committed only as redacted capability metadata. V8 build
work begins at M2 after that probe is reviewed.
