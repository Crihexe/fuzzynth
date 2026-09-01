# Chrome 152 optdebug `d8` build

Status: built, smoke-tested, and packaged

Date: 2026-09-01

## Identity

- Chrome: `152.0.7977.75`
- V8 revision: `3de6ffffbfdcf265e9f11a5c9d1cfb4d486d7550`
- Reported `d8` version: `V8 version 15.2.124.19`
- Profile/base builder: `optdebug` / `x64.optdebug`
- Build: 2507 steps in 16m59s on 16 jobs; 15.4x reported parallelism
- Compiler: Chromium clang 23.0.0git at LLVM revision
  `53d18800eda3b7407e53366f27ca78e922c6e0db`
- Binary SHA-256:
  `07b8d1fea242ea7acc0df3770f6e4a9db921f41511bca386f8f43e8f2ea69d31`
- ELF build ID: `3da1e1e5961f08ca`
- `d8 --help` stdout/stderr framing SHA-256:
  `360ba58d5e4dd1fd8d920e5e24aaae986ee75fe3f18a567d9750ec1257d493b7`
- GN args SHA-256:
  `1c13419b03459577d86b1c95f01ca6ed7725b69af7d7c7fa09a4b2a4ffe18d37`

Exact explicit GN arguments:

```gn
is_debug = true
target_cpu = "x64"
v8_enable_backtrace = true
v8_enable_slow_dchecks = true
```

Resolved defaults additionally report `v8_optimized_debug=true`,
`is_component_build=true`, and `symbol_level=2`.

## Verification and worker

- JavaScript smoke: passed
- WebAssembly smoke: passed
- Natives-syntax optimization smoke: passed
- Minimal `scratch` worker smoke: passed
- Worker image ID:
  `sha256:a55c538d94e0ff92485c8d6cded2a0804d20a839a25a1189fa18f99a23020570`
- Docker-reported worker size: `112205894` bytes
- Runtime user: `65532:65532`

The larger component build and full symbol data remain under ignored `.local/`
storage; the worker contains only runtime objects, while the host retains symbols
for triage and symbolization.
