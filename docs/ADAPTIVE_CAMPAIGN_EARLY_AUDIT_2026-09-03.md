# Adaptive campaign early audit — 2026-09-03

## Scope

This is a frozen early snapshot at `2026-09-03T00:30:34.536100+00:00` of the
custom-Luna campaign introduced by commit `a8a336a`. It covers 126 generations
and 125 d8 executions: 70 `explicit_v2` outputs and 56 `lean_v2` outputs. The
campaign continued after the snapshot. Every program, request, system prompt,
corpus window, constituent corpus name/hash, response, token count, execution
profile, flag vector, stdout, and stderr is preserved in the normal ledgers.

## Outcome comparison

| Prompt | Generations | exit 0 | JS error | Wasm/nonzero | timeout | candidate |
|---|---:|---:|---:|---:|---:|---:|
| explicit_v2 | 70 | 61 (87.1%) | 5 | 3 | 1 | 0 |
| lean_v2 | 56 | 43 (76.8%) | 11 | 1 | 0 | 0 |

There were 56 exact prompt pairs with the same model, effort, build, flags,
session seed, corpus window, and turn number. Both variants succeeded in 38;
only explicit succeeded in 10; only lean succeeded in 5; neither succeeded in
3. The direction favors explicit, but the discordant sample is still too small
for a strong prompt-only conclusion (two-sided exact McNemar/binomial
`p ~= 0.302`). The much larger previous rich/lean audit and the bounded
rich/explicit experiment remain the stronger evidence that the original rich
prompt should not return.

All 126 program hashes were unique. Median program size was 2,040 bytes for
explicit and 3,591 for lean. Median all-pairs textual similarity was 0.073 for
explicit and 0.050 for lean; the 90th percentiles were 0.133 and 0.087. Lean is
therefore more textually diverse, but spends more output tokens per program and
has lower run validity.

At the custom price schedule, this snapshot consumed approximately 4.375
credits for explicit and 3.918 for lean. Explicit averaged about 1,001 output
tokens per generation; lean averaged 1,266. The lower total lean cost is only a
consequence of its smaller in-flight sample, not better per-program efficiency.

## What the programs actually do

Manual review agrees with the static feature census:

- Explicit usually chooses one optimized function and one assumption change.
  Representative programs exercise array element/prototype transitions,
  `Symbol.isConcatSpreadable` side effects, accessors changing representations,
  RegExp callbacks, or a JS/Wasm boundary. The intrinsic contract is now used
  correctly.
- Lean produces larger, more elaborate programs, often combining Proxy traps,
  TypedArrays, coercion, BigInt, generators, RegExp, accessors, and GC. The
  combinations are less repetitive textually but are again becoming a semantic
  kitchen-sink template.
- No old `CheckMarkedForManualOptimization`, invalid `%GC()`, or other
  intrinsic-misuse false positive reappeared. There was no ASan finding, signal,
  or optdebug CHECK.

Feature incidence makes the new limitation unambiguous:

| Feature | explicit_v2 | lean_v2 |
|---|---:|---:|
| JIT/native optimization | 70/70 | 56/56 |
| Proxy | 24/70 | 54/56 |
| TypedArray | 15/70 | 54/56 |
| ArrayBuffer | 2/70 | 39/56 |
| GC/WeakRef/FinalizationRegistry | 21/70 | 29/56 |
| Wasm | 7/70 | 1/56 |
| SharedArrayBuffer/Atomics | 0/70 | 0/56 |
| generator | 0/70 | 21/56 |

The mixed eight-example windows caused a new form of mode collapse. About 42%
of corpus references in the sampled windows had d8 intrinsic syntax. With eight
random examples, seeing at least one such source is nearly certain, so even the
lean prompt inferred that every output should use `%PrepareFunctionForOptimization`
and `%OptimizeFunctionOnNextCall`. Stratification guaranteed broad context but
did not translate into broad generated-engine coverage: the model blended the
most salient recurring patterns instead.

Stratified windows did improve exposure to rare source classes. Across 19
distinct stratified windows (152 references), 43 references were security
artifacts, 59 were Wasm-marked, 64 used intrinsics, and 21 had exploit markers.
The six uniform windows (48 references) contained only three security artifacts
and no exploit-marker source. Yet the model largely ignored Wasm and concurrency
examples when more familiar JIT/Proxy patterns were present.

## Failures and feedback behavior

Most failures are ordinary, useful language mistakes: unavailable `assertTrue`
or `TextEncoder`, invalid private-field receivers, accessor writes without a
setter, BigInt/Number mixing, incompatible branded built-in receivers, or
uncaught intentional coercion errors. Three explicit programs and one lean
program emitted invalid Wasm modules. These validate the adaptive decision to
retain one ordinary-error turn but rotate after repeated syntax/Wasm failure.

The one timeout is instructive. Its explicit `for` loops were fixed, but a
custom global RegExp `exec` repeatedly returned a match while resetting
`lastIndex` to zero. `String.prototype.replace` therefore looped internally.
The next prompt revision must cover implicit iteration protocols, not just
surface loop syntax.

## Why there is no real crash yet

1. The target is current stable Chrome 152 V8 with historical defects already
   fixed. Thousands of generated tests are a very small search compared with a
   mature coverage-guided fuzzer campaign; a true unknown crash is expected to
   be rare.
2. The first 3,688-program phase was conditioned on only four sources. The later
   2,346-generation preview-v3 phase sampled hundreds of sources but the old
   rich prompt produced invalid Wasm and repeated templates. Much of the nominal
   volume therefore did not explore distinct deep engine states.
3. More mixed examples increased stylistic priming but collapsed subsystem
   choice. In this snapshot every output still enters the same broad JIT funnel,
   while concurrency is absent and Wasm is scarcely executed.
4. The model receives process outcome and bounded stdout/stderr, not native code
   coverage. It can repair obvious errors but cannot tell whether two successful
   programs reached the same compiler/GC paths. Exact and textual diversity are
   not equivalent to engine-state diversity.
5. Programs are independent creations rather than mutations that preserve the
   delicate constraints of old triggers. This matches the intended experiment,
   but it sacrifices the locality advantage of conventional mutation fuzzing.

`--fuzzing` is not the explanation: it removes known harness-misuse noise while
retaining native signals, ordinary security CHECKs, and sanitizer reports. ASan
and optdebug are the right primary oracles; the weakness is input search, not
crash classification.

## Immediate changes derived from this audit

- Introduce immutable `explicit_v3` and `lean_v3` prompts. Both forbid
  unavailable test-harness helpers and require progress in customized implicit
  protocols. Explicit also says not to force JIT/Wasm into every response.
- Replace mixed windows with homogeneous, independently randomized eight-source
  windows for compiler, Wasm, memory/GC, pure ECMAScript, and security artifacts.
  This preserves context poisoning while making the worker portfolio—not each
  individual program—the source of subsystem diversity.
- Prefer standalone examples in focused windows, excluding context that calls
  `load`, `d8.file`, or an unavailable `WasmModuleBuilder`.
- Use V8's own `--jit-fuzzing` threshold profile. Add synchronous Wasm tier-up
  and moving-memory stress to the Wasm lanes, and compaction/incremental-marking
  stress to memory lanes. Keep ASan on four pairs and optdebug on the compiler
  pair.
- Preserve exact A/B prompt pairing and adaptive session rotation. Do not spend
  official OpenAI budget and do not reactivate Spark while its quota is absent.

Finding a real crash remains an open campaign objective; no candidate is
claimed by this audit.
