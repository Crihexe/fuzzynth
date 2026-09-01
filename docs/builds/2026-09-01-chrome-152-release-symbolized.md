# Chrome 152 release-symbolized `d8` build

Status: built and smoke-tested

Date: 2026-09-01

## Source identity

- Linux Stable Chrome: `152.0.7977.75`
- Chromium revision: `4999cc1efed37c4d91dc4ce6ec4b0a50e2a9a8cb`
- V8 revision: `3de6ffffbfdcf265e9f11a5c9d1cfb4d486d7550`
- `depot_tools` revision: `fc480ce004a9e95e6defae5995ea523e66176310`
- Reported `d8` version: `V8 version 15.2.124.19`

The V8 checkout `HEAD` was compared with the configured revision before GN
generation and again before the build.

## Build identity

- Profile: `release_symbolized`
- Base builder: `x64.release`
- Target: `d8`
- Build jobs: `16`
- Build result: 2313 steps succeeded in 12m45s; reported parallelism 15.6x
- Compiler: Chromium clang 23.0.0git at LLVM revision
  `53d18800eda3b7407e53366f27ca78e922c6e0db`
- Binary size: `196838120` bytes
- ELF build ID: `69cdc77e20e564db`
- Binary SHA-256:
  `c220e42e5720a58a422a85889fa178ef7c20e8a720f5ba2d294b3a238ff56a73`
- `d8 --help` stdout/stderr framing SHA-256:
  `07432e7240133e99a0c170805244721a3e54773d2147c7c582d719d748657c8e`

Exact GN arguments:

```gn
dcheck_always_on = false
is_debug = false
target_cpu = "x64"
symbol_level = 1
```

No Fuzzilli integration or sanitizer was enabled in this throughput profile.

## Smoke checks

- JavaScript execution: passed
- WebAssembly module compilation, instantiation, and execution: passed
- `--allow-natives-syntax` with optimization intrinsics: passed

## Minimal worker image

- Base: `scratch` (no shell or package manager)
- Runtime user: `65532:65532`
- Image ID:
  `sha256:285a33ff964e3770be2bbb348d24b7c7f0e6499c1f5488e346297458b9e1858b`
- Docker-reported size: `42477934` bytes
- Networkless/read-only/capability-free JavaScript smoke: passed
- Networkless/read-only/capability-free WebAssembly smoke: passed
- Networkless/read-only/capability-free natives-syntax smoke: passed

A 32-PID experimental limit was too small for V8's startup worker threads and
reliably produced `Check failed: Start()` followed by `SIGABRT`. Raising the
limit to 48 passed; the versioned standard worker profile uses 64 as headroom.
This infrastructure-induced abort is retained as a classifier/worker-baseline
test case, not treated as a V8 bug candidate.

The full machine-readable build and smoke manifests remain under ignored
`.local/build-manifests/` so operational paths do not enter Git. The hashes and
source/build identities required to match the binary are retained above.
