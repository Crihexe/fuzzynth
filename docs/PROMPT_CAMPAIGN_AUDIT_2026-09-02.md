# Prompt and generation-quality audit — 2026-09-02

## Scope and method

This audit separates the preview-v3 campaign from earlier four-file canaries by
requiring generation metadata whose `corpus_pair_id` ends in `-v3-preview`.
That interval contains 2,346 provider generations and 2,344 release-symbolized
d8 executions under `--fuzzing`. The two missing programs are the paired Spark
quota failures. No execution in this interval is a bug candidate.

All percentages below are computed from the immutable catalog and content store.
Error families are derived only for non-successful executions from d8 stdout and
stderr; d8 emits ordinary uncaught exceptions on stdout. Keyword-based feature
counts are behavioral proxies, not code coverage. Representative programs from
every model/prompt cohort, errors, timeouts, exact-repeat clusters, and the most
feature-dense successful outputs were also inspected manually.

## Does the machinery work?

Operationally, yes. Program, request, raw response, feedback, stdout, stderr,
container state, d8 identity, exact flags, prompt digest, corpus-window digest,
and source names/hashes are retained. Every completed A/B session ordinal that
exists on both sides has the same seed, turn target, and corpus digest. All
preview-v3 executions use the intended pinned release-symbolized Chrome 152 d8
with `--allow-natives-syntax`, `--expose-gc`, and `--fuzzing`.

The weak point is generation quality and selection pressure, not evidence
capture or accidental reuse of one program/corpus.

## Rich versus lean

| Result family | Rich (1,079) | Lean (1,267) |
| --- | ---: | ---: |
| Reached exit 0 | 290 (26.9%) | 818 (64.6%) |
| Syntax error | 222 (20.6%) | 268 (21.2%) |
| Uncaught Wasm compile/validation error | 384 (35.6%) | 8 (0.6%) |
| TypeError | 104 (9.6%) | 113 (8.9%) |
| ReferenceError | 52 (4.8%) | 35 (2.8%) |
| RangeError | 7 (0.6%) | 11 (0.9%) |
| Timeout / OOM | 6 / 0 | 7 / 2 |
| Other nonzero / no program | 13 / 1 | 4 / 1 |

The rich prompt strongly controls content: 70.9% of outputs use a V8 intrinsic,
69.2% mention WebAssembly, 69.3% call `gc()`, and the median program spans five
feature families. This produces visibly engine-oriented programs, but frequently
as a kitchen-sink template. Only 26.9% run to completion, principally because
the model hand-encodes invalid Wasm sections. The most common failure is a
section length mismatch; `WasmModuleBuilder` being unavailable accounts for 30
ReferenceErrors.

The lean prompt is much more executable and diverse: 64.6% run to completion,
only 2.6% use intrinsics, 2.4% mention Wasm, and the median spans two feature
families. It generates many Proxy, coercion, iterator, class, and typed-array
interactions, but a substantial portion is ordinary application-like novelty
rather than engine stress. Representative small-model outputs merely reduce an
array, recurse five times, or mutate a Symbol-keyed object seven times.

A deliberately conservative `exit 0 + engine-stress keyword + no unavailable
API + no runtime randomness` proxy retains 265 rich programs (24.6%) and 398
lean programs (31.4%). It is not a coverage measurement, but demonstrates that
the lean prompt's higher validity outweighs its lower V8 specificity under the
current setup. Rich is better only if the metric is raw use of explicit tiering
or Wasm; lean is the better existing end-to-end prompt.

The fully balanced subset confirms this is not caused by one variant getting
more time before shutdown. Across 294 completed A/B session pairs and 1,065
turns per side, rich reaches exit 0 on 285 turns (26.8%) and lean on 679
(63.8%). On matched turns, lean alone succeeds 469 times while rich alone
succeeds 75 times.

## Temperature is not helping above 1.0

Across GPT-4o mini and GPT-4.1 nano, temperature 0.0–1.0 produced only 8 syntax
errors in 761 generations (1.1%). At temperature 1.5, 193 of 260 generations
(74.2%) were syntactically invalid. At temperature 2.0, all 275 of 275 were
invalid, frequently long multilingual token soup that filled the output limit.

The result is especially costly in six-turn Nano sessions: a corrupted first
program remains in history while the fixed session temperature remains high.
High temperature here does not yield useful semantic novelty; it crosses a
sharp coherence boundary. Future official lanes should use 0.0–1.0, with 0.5
and 1.0 the useful diversity comparison points.

## Does iterative feedback work?

It depends on the failure:

- After a lean TypeError, 74.2% of next turns reach exit 0; after a lean
  ReferenceError, 73.1% do. Retaining bounded history is useful in this case.
- After a syntax error, only 5.1% of rich and 3.2% of lean next turns recover;
  86.0% and 93.5%, respectively, repeat another syntax error.
- After a rich Wasm validation failure, only 30.3% recover and 64.6% repeat the
  same failure family.
- Some Nano sessions emit the exact same faulty program on four or five
  consecutive turns despite receiving the error. Rich adjacent programs have
  median normalized five-token-shingle similarity 0.583; 269 of 781 adjacent
  pairs exceed 0.7. Lean's median is 0.336 and 77 of 938 exceed 0.7.

The lifecycle should therefore retain turns after repairable runtime failures,
but rotate immediately after repeated syntax/Wasm-validation failures or an
identical program digest. Fixed-length sessions waste calls in failure loops.

## Does context poisoning work?

Yes, measurably, but it is weaker than the system prompt. With the lean prompt,
Wasm appears in 6.1% of outputs when either input example contains Wasm and
0.3% otherwise; async/Promise use is 32.7% versus 14.5%, and generator use is
62.2% versus 39.0%. With rich, corpus intrinsics raise output intrinsic use from
57.1% to 76.8%, and corpus Wasm raises output Wasm from 62.9% to 79.9%.

Uniform sampling also reflects the corpus's skew. The 635 distinct selected
sources comprise 531 regression tests, 25 fuzzer corpus/harness entries, 21
inline extractions, 18 security artifacts, 14 attachments, 9 issue-inline
reproducers, 7 benchmarks, 4 explicit PoCs, 4 exploits, and 2 search candidates.
Thirty-one have exploit markers and 135 have Wasm markers. Randomness works, but
two examples per session rarely provide a deliberate mix of subsystems.

Use stratified randomness rather than hand selection: for example, each larger
window can independently sample regression/minimal, security/PoC, JIT-intrinsic,
Wasm, memory/GC, and an unrestricted wildcard stratum. This remains random while
using the SQLite classification instead of letting the largest category occupy
almost every slot.

## Qualitative program review

Three recurring output shapes explain the aggregate data:

1. Small-model lean outputs are valid and superficially unusual, but often too
   shallow to change optimized engine assumptions. Runtime `Math.random()` is
   common (354 lean programs), which also weakens reproducibility.
2. Rich outputs often combine Wasm bytes, optimization intrinsics, Proxy,
   shared memory, Atomics, GC, weak references, generators, and async code in one
   program. A few Luna programs are technically strong, but most interactions
   are independent phases and a failure near the top prevents all later phases.
3. The strongest manually inspected program in the new explicit-prompt canary
   uses one hot array-summing function, then deliberately changes element kinds,
   holes, prototype getters, and accessor side effects. It is valid,
   deterministic, reaches optimized code, and does not dilute the hypothesis
   with unrelated Wasm or async phases. This is the desired shape.

## Why zero crash candidates is unsurprising

- 2,344 executions is extremely small for a mature engine. After early syntax,
  Wasm-validation, and environment errors, only 1,108 reached exit 0; a strict
  useful-execution proxy retains 663.
- The primary profile is an optimized release build. It catches signals and
  release CHECKs, but the already-built ASan and optdebug profiles were not used
  as primary or shadow oracles. Some native memory defects or debug invariants
  need those builds to become observable.
- The model receives exit/output feedback but no coverage, tiering, deopt, or
  edge-novelty signal. It cannot distinguish a novel internal path from a large
  program that exercised only familiar builtins.
- The rich prompt over-specifies a familiar recipe and causes invalid Wasm and
  repeated templates. The lean prompt under-specifies engine depth. Neither
  reliably produces the narrow, valid state transition most likely to expose an
  implementation defect.
- The campaign uses a five-second, one-CPU execution envelope. This is sensible
  for throughput, but a small set of complex programs time out before their late
  phases. Conversely, merely raising the limit would amplify unproductive loops.

Zero findings therefore does not falsify LLM-guided generation. It says the
effective campaign was hundreds, not millions, of reasonably targeted tests and
used only the least sensitive native oracle.

## Bounded explicit-prompt comparison

The follow-up experiment uses custom Luna at low reasoning, with no temperature
parameter. It pairs 50 turns of the current rich prompt against 50 turns of
`iterative_js_explicit_v1.txt`. Both sides receive the same ten deterministic
random corpus windows, eight sources per window, five turns per session, the
same d8 profile/flags, and the same resource limits. Full requests and corpus
provenance remain immutable.

The pairing invariant held for all ten sessions: each side has the same seed,
five-turn target, corpus-window digest, and eight source names/hashes. The 80
sources were all distinct. Results were:

| Metric | Current rich | Explicit v1 |
| --- | ---: | ---: |
| Release executions | 50 | 50 |
| Exit 0 | 21 (42%) | 39 (78%) |
| Wasm compile failure | 25 | 2 |
| Other JS/nonzero failure | 4 | 5 |
| Timeout | 0 | 4 |
| Unique program digests | 50 | 50 |
| Median / p90 program bytes | 3,428 / 4,366 | 2,387 / 3,280 |
| Median / p90 provider seconds | 26.56 / 32.69 | 19.75 / 24.38 |
| Input / output / reasoning tokens | 437,767 / 72,132 / 10,606 | 374,948 / 51,180 / 12,308 |
| Charged custom credits | 4.353 | 3.410 |
| Median / p90 feature-family breadth | 7 / 7 | 2 / 4 |
| Median adjacent normalized similarity | 0.581 | 0.322 |

The matched-turn contingency is also favorable: both prompts finish 14 pairs,
only current rich finishes 7, only explicit finishes 25, and neither finishes
4. The exploratory exact two-sided sign test over the 32 discordant pairs gives
`p=0.0021`; the effect is large enough to act on despite the small sample.

Current rich collapses into its instructed template: all 50 programs use JIT
intrinsics, Wasm, Proxy/Reflect, typed memory, and GC/weak behavior; 37 also add
shared memory or iteration. Twenty-five fail while decoding their module. Eight
examples rather than two did not repair this: the earlier low-reasoning rich
lane reached exit 0 in 49.5% of turns, versus 42% here.

Explicit v1 also deliberately biases toward tiering—all 50 use a stable hot
function—but varies the stressed subsystem. Thirty-five use object/prototype or
Proxy transitions, ten typed memory, five iterator behavior, four classes, and
only two Wasm. It contains no runtime randomness, unavailable helper, Markdown,
or exact duplicate. Its four release timeouts expose one remaining prompt flaw:
some generated loops use a live `length` whose Proxy getter grows the same
object, or perform excessive mutation inside nested warm-up loops. Future text
should require a fixed loop bound captured before invoking user code and a
bounded total-operation budget.

The same 100 exact programs were replayed with the pinned ASan and optdebug
builds. Neither produced a candidate. Current-rich outcomes were identical on
all three builds. Explicit v1 reached exit 0 35 times on ASan and optdebug; four
additional release successes became sanitizer/debug timeouts, and optdebug had
one further timeout.

Finally, every one of the 1,108 original preview-v3 programs that reached exit 0
was replayed locally under both sensitive profiles. ASan completed all 1,108;
optdebug completed 1,105 and timed out on three. There were zero sanitizer
findings and zero native CHECK/signal candidates. These 2,216 shadow executions
used no model API budget.

## Recommended next campaign

1. Use the explicit single-hypothesis prompt as the main engine-focused lane;
   keep lean as a diversity/control lane and retire the present rich prompt from
   high-volume use. Explicit is the best tested compromise; lean remains the
   closest test of context poisoning without strong task recipes.
2. Remove official temperatures 1.5 and 2.0. Rotate/restart immediately on an
   exact duplicate, two syntax failures, or two Wasm-validation failures; retain
   history after ordinary TypeError/ReferenceError because repair works there.
3. Make ASan the main memory-safety oracle and run an optdebug shadow sample.
   Preserve release mode as the throughput/control lane.
4. Use deterministic, stratified 6–8-example windows driven by the SQLite tags;
   preserve a wildcard slot so the corpus can still surprise the model.
5. Record an internal-path proxy: sampled optimization/deoptimization and Wasm
   tiering telemetry, or preferably edge coverage from a separately instrumented
   build. Do not feed verbose traces wholesale to the model; use them for
   selection and concise novelty feedback.
6. Add a deterministic differential lane for correctness bugs: require a final
   checksum, then compare optimized/default execution with a conservative
   baseline. A wrong-code mismatch can find bugs that never crash.
