# M152 Maglev mixed-elements sort / skipped write barrier

Status: verified native V8 crash on the pinned Chrome 152 target; known upstream
and fixed in the next M152 V8 point revision. This is a harness-validation
finding, not an unknown zero-day and not a blind LLM discovery.

## Target and signature

- Vulnerable V8: `15.2.124.19`, revision
  `3de6ffffbfdcf265e9f11a5c9d1cfb4d486d7550`
- Pinned optdebug `d8` SHA-256:
  `07b8d1fea242ea7acc0df3770f6e4a9db921f41511bca386f8f43e8f2ea69d31`
- Worker image:
  `sha256:a55c538d94e0ff92485c8d6cded2a0804d20a839a25a1189fa18f99a23020570`
- Reproducer SHA-256:
  `0a890b8d71d0cca0a12e331d7a91b8cc6dddcca31810628a147b07a3a59b841a`
- Outcome: `v8_fatal`, `SIGABRT`
- Fatal signature:
  `Heap::VerifySkippedWriteBarrier` and
  `Check failed: !WriteBarrier::IsRequired(heap_object, Tagged<Object>(value))`

The reproducer uses valid `%PrepareFunctionForOptimization` /
`%OptimizeMaglevOnNextCall` pairs and the supported `gc()` API. It reproduces
with `--fuzzing`, so it is distinct from the invalid-intrinsic false positives
seen in the first campaign.

## Reproduction

From the repository root:

```sh
PYTHONPATH=src python3 -m fuzzynth execute \
  --program findings/m152-sort-mixed-elements-write-barrier/reproducer.js \
  --profile optdebug \
  --flag=--allow-natives-syntax \
  --flag=--maglev \
  --flag=--expose-gc \
  --flag=--fuzzing \
  --flag=--random-seed=1 \
  --flag=--fuzzer-random-seed=1 \
  --state-root state
```

Three independent minimized executions reproduce the same fatal invariant:

| Seed | Execution ID | stderr SHA-256 |
|---:|---|---|
| 1 | `exec-bb2f0112-6216-424d-a21a-87dc565b8dc6` | `04c396feb54429852b73d2bef10c3d7f5b18cc5c4b96e0746fcf357688669cf7` |
| 31337 | `exec-627cc2fb-0cea-481d-a1d1-44888c6659b6` | `2201b20f1777bb05781d6b740509654067546d29d95809dc3429a46bfdb4f9dc` |
| 2147483646 | `exec-a1854263-3162-41bc-8dec-d0c651b363b9` | `a4c6d1c45c95441fcb0d92faa358b3c9ff848f41d526582d61135b561924c9e8` |

The differing stderr hashes are caused by process addresses; the fatal file,
line, check text, and top symbolic frame are identical.

## Controls and upstream fix

The same minimized program exits zero on the same pinned optdebug image with
`--no-maglev` (`exec-c20a6597-8617-4a24-952c-e618506469fc`). This localizes the
failure to optimized Maglev behavior rather than parser, d8, or GC API misuse.

The official M152 follow-up commit
`94c34b200ae9caf83d06c167a070746a1c7e825c` prevents inlining
`Array.prototype.sort` when polymorphic receiver maps have mixed elements
kinds. An optdebug build from official revision
`4323497a6a73839e6d5260f6acd7ec0212cb3321` (`V8 15.2.124.21`) executes the
same derived probe normally. The local source checkout and optdebug output are
restored to the pinned `15.2.124.19` revision after this control build.

## Why it crashes

Polymorphic feedback from packed Smi and packed object arrays was unioned into
one elements kind while the inlined sort took a snapshot. Its comparison
callback changed the live receiver back to packed Smi elements, but copy-back
restored HeapObjects without correcting the now-inconsistent representation.
The second optimized function consumed that array as Smi-only and stored the
actual young HeapObject into an old object without a required write barrier.
The optdebug heap verifier caught the skipped barrier immediately.

## Provenance

The vulnerable pattern was identified by comparing the pinned official M152
revision with the newer official `15.2-lkgr` branch, then independently extended
and minimized into a native-crashing write-barrier check. It was not emitted by
an LLM worker. A separate bounded custom-Luna rediscovery experiment is used to
measure whether Fuzzynth can derive this crash shape from historical context;
its result must not change the classification above.
