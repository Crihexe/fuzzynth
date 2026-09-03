# Fuzzynth generation-quality audit — 2026-09-03

## Scope and frozen evidence

The focused-v3 snapshot is frozen at
`2026-09-03T01:13:01.598590+00:00`. It contains 939 primary executions:
905 custom Luna turns and 34 custom Terra turns. The separate
`interaction_v1` ablation contains exactly 100 custom Luna generations and 100
primary executions. Every generated program in both sets has a distinct
SHA-256. No candidate was observed.

This audit uses the primary execution linked from the durable session attempt,
not later shadow replays. It also inspected exact request/program/stdout/stderr
artifacts and the source names and hashes recorded in each request.

## What is working

- The execution and evidence pipeline is healthy. Every completed generation is
  linked to its exact prompt, corpus window, constituent source hashes, program,
  V8 build/flags, stdout, stderr, resource result, and usage.
- Corpus rotation is now real. The focused snapshot spans 327 sessions, 2,616
  source placements, and 1,170 distinct historical source files. The most
  frequent source appears in only 11 sessions.
- The model is not copying the historical programs. In an earlier 556-program
  checkpoint from this same focused run, maximum exact eight-token-shingle
  containment was below 10% for every output and the median was below 1% in
  every prompt/focus cohort.
- Generated JIT programs do reach optimized code. Thirty preserved exit-0
  programs were replayed with `--trace-opt --trace-deopt`: all 30 completed
  TurboFan compilation and 22 emitted a real deoptimization bailout. Thus the
  intrinsic-heavy output is not merely cosmetic.
- The sensitive-build matrix is functioning. A 13,669-execution replay across
  compiler verification, concurrent compilation, forced deoptimization, GC,
  Wasm tiering/memory, and experimental RegExp profiles completed with zero
  infrastructure errors. It found no candidate.

## Prompt comparison

### Focused iterative Luna v3

| Prompt | Programs | Exit 0 | JS error | Other nonzero | Timeout | Median bytes | Credits / exit-0 |
|---|---:|---:|---:|---:|---:|---:|---:|
| explicit v3 | 485 | 419 (86.4%) | 23 | 41 | 2 | 2,183 | 0.077 |
| lean v3 | 420 | 347 (82.6%) | 60 | 13 | 0 | 3,608 | 0.089 |

The aggregate rate favors explicit v3, but the 406 exactly comparable
corpus/turn pairs are more nuanced: 294 both succeeded, 55 only explicit
succeeded, 41 only lean succeeded, and 16 neither succeeded. The exact
two-sided McNemar/binomial p-value is about 0.184, so there is no defensible
single global winner at this checkpoint.

The subsystem split is decisive:

| Focus | Explicit-only wins | Lean-only wins | Exact p-value | Interpretation |
|---|---:|---:|---:|---|
| compiler | 13 | 4 | 0.049 | explicit is better |
| language | 17 | 8 | 0.108 | explicit trend, not conclusive |
| memory | 4 | 5 | 1.000 | tied |
| security | 12 | 2 | 0.013 | explicit is better |
| Wasm | 9 | 22 | 0.029 | lean is better |

One universal system prompt is therefore the wrong abstraction. Explicit v3 is
the best current base for compiler and security, while its detailed Wasm rules
cause repeated malformed-binary failures.

### Semantic mode collapse

High textual diversity masks strong template reuse:

- Explicit v3 used JIT intrinsics in 401/485 outputs and `gc()` in 395/485.
- Lean v3 used Proxy in 419/420, TypedArray/DataView in 402/420, JIT in
  323/420, and `gc()` in 333/420.
- Only one explicit output and no lean output exercised
  SharedArrayBuffer/Atomics/Worker.
- Lean is longer and has more surface features, but often emits the same
  Proxy + Reflect + TypedArray + GC “kitchen sink.” That is variety of names and
  syntax, not variety of engine hypotheses.

### Terra

Terra is qualitatively different but expensive:

| Focus | Programs | Exit 0 | Semantic adherence |
|---|---:|---:|---|
| security | 14 | 14 | strong; 6 RAB cases and 7 TypedArray cases |
| Wasm | 20 | 8 | 20/20 contain Wasm, but 12 fail before useful completion |

The 34 turns cost about 45.86 Terra credits, or 2.08 credits per exit-0 result.
Manual inspection found genuinely coherent programs, including resizable-buffer
shrink during TypedArray conversion and Wasm memory growth from an import
callback. Terra security is worth retaining as a low-volume depth lane. Terra
Wasm is not cost-effective until its validity improves.

## The 100-generation interaction/context ablation

`interaction_v1` was designed after the first audit to require two compatible
mechanisms on one central path. Five one-turn workers covered compiler, Wasm,
memory, security, and concurrency. Each produced ten programs with eight
examples and ten with sixteen examples.

| Context | Programs | Exit 0 | JS error | Other | Luna credits | Credits / exit-0 |
|---|---:|---:|---:|---:|---:|---:|
| 8 sources | 50 | 46 (92%) | 3 | 1 timeout | 3.027 | 0.0658 |
| 16 sources | 50 | 44 (88%) | 5 | 1 nonzero | 3.697 | 0.0840 |

Sixteen examples increased cost per useful execution by about 28% and did not
improve validity. It slightly increased static feature-signature entropy, but
that apparent benefit is irrelevant because the prompt failed its central
semantic objective:

- 99/100 outputs used JIT intrinsics;
- 0/20 concurrency outputs used SharedArrayBuffer, Atomics, or Worker;
- 0/20 Wasm outputs instantiated and executed a Wasm module;
- every compiler output followed the requested compiler area, but the same
  compiler template leaked into every other area.

This was not caused by missing target evidence. An eight-source concurrency
window had median counts of three SAB-bearing sources, 1.5 Atomics-bearing
sources, and 6.5 Worker-bearing sources. An eight-source Wasm window had a
median of seven sources mentioning WebAssembly/Wasm. The model ignored those
signals and chose the easiest system-prompt recipe: baseline, optimization,
shape/receiver transition.

The experiment is still useful: it rules out “just add more PoCs” as the next
move. For Luna, four to eight clean, target-dense examples are preferable to a
larger mixed window.

Seven syntax errors came from testing a native intrinsic with a construct such
as `typeof %PrepareFunctionForOptimization === "function"`; d8 native syntax
does not make an intrinsic a normal first-class expression. The single timeout
replaced `iterator.next` from inside the already cached `next` function, so the
active `for...of` loop continued calling the original non-terminating method.
Both are prompt/harness-quality defects, not V8 findings.

## Why there is no real crash yet

1. **The effective search space is much smaller than the program count.** The
   files are byte-distinct, but most reduce to a few learned templates. Several
   thousand such programs are not several thousand independent engine paths.
2. **Outcome-only feedback is nearly blind.** Exit 0 tells the next turn neither
   which tiers compiled nor which transition/deoptimization happened. The model
   has little signal for deciding whether a mutation went deeper.
3. **The corpus labels are coarse.** A file qualifies for concurrency if it
   mentions Worker once even when most of the file is a sandbox exploit or a
   different regression. Historical required flags and unavailable assert or
   harness helpers also dilute the target style.
4. **Wasm raw bytes are a precision task.** Small models frequently miscompute
   section lengths, counts, types, or indices. Those programs never reach the
   compiler/runtime paths of interest.
5. **Generic fuzzing instructions are a strong prior.** Mentioning native
   syntax, warm-up, optimization, Proxy, or GC makes a small model reuse those
   recognizable recipes even when the corpus points elsewhere. High verbosity
   increases program size, not sampling entropy.
6. **The target is a current stable V8 release.** It has already received very
   large conventional fuzzing and regression-test exposure. LLM generation can
   still find a bug, but indefinite generation provides no guarantee and the
   current hit probability per program is evidently very low.

The absence of a candidate across the 13,669 stress replays further argues that
we are not merely missing an execution flag. We need different semantic inputs.

## Changes justified by the evidence

1. Use subsystem-specific prompts rather than one generic explicit/lean prompt.
   Compiler should retain the proven explicit recipe. Wasm must require a
   successfully instantiated/called module and avoid default JIT intrinsics.
   Concurrency must require SAB plus Atomics/Worker. Pure-language output should
   explicitly prohibit `%` intrinsics and Wasm.
2. Keep context windows at four to eight examples. Select target-dense sources
   and exclude unavailable harness dependencies, `Sandbox.*`, assertions, and
   unrelated native-intrinsic priming from Wasm/concurrency pools.
3. Retain Terra security at low volume; pause Terra Wasm until a cleaner prompt
   raises validity. Its current cost per useful execution is too high.
4. Feed compact measured behavior—not only exit code—into iterative turns:
   actual TurboFan/Maglev compilation, deoptimization count/reason, Wasm
   successful instantiation/call, and a static subsystem-adherence fingerprint.
5. Preserve a small under-specified language lane for diversity, but stop
   treating long kitchen-sink programs as intrinsically higher quality.

The infrastructure is working and some generated programs reach deep optimized
paths. The generator policy is not yet broad or directed enough to call the
overall fuzzing strategy effective. The next campaign should optimize for
distinct, verified engine paths per credit rather than raw program count.

## Specialized canary after the audit

The first 62 outputs from the replacement matrix confirm that subsystem-specific
prompts fix the most serious mode-collapse defect. Direct inspection found:

| Lane | Programs | Prompt-adherent | Completed useful path | Other outcomes |
|---|---:|---:|---:|---|
| Wasm boundary | 16 | 16 | 6 | 10 rejected binaries |
| concurrency | 20 | 20 | 13 | 1 JS error, 2 other nonzero, 4 timeouts |
| ordinary language | 26 | 26 | 26 | none |

All 62 program hashes are unique. In contrast with `interaction_v1`, every Wasm
output now constructs a module and instance and uses an export, every
concurrency output uses SAB and Atomics (normally a real d8 Worker), and the
language lane avoids `%` intrinsics, Wasm, SAB, and Worker. This is strong
evidence that the old failure was prompt-level semantic collapse, not a broken
corpus sampler.

The canary also exposes the next bottleneck. Ten of sixteen Wasm programs failed
binary validation, while concurrency failures came from invalid Atomics view
types or incomplete message protocols. Fuzzynth now feeds a compact structured
observation into later turns with subsystem adherence, useful-path completion,
known corrective guidance, and JIT compilation/deoptimization counts. Compiler
and language lanes run with `--trace-opt` and `--trace-deopt`; exact raw output
continues to be preserved and primary execution remains under `--fuzzing`.
