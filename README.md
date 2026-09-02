# Fuzzynth

Fuzzynth is an LLM-driven JavaScript and WebAssembly fuzzing laboratory for
V8's `d8` shell.

The pinned V8 worker matrix and the non-streaming iterative controller are
implemented. No fuzzing campaign is running while the owner finishes the PoC
dataset.

Project documents:

- [instruction.md](instruction.md) — durable project instructions and constraints.
- [PLAN.md](PLAN.md) — architecture, campaign design, milestones, and open questions.
- [PROJECT_LOG.md](PROJECT_LOG.md) — living status, decisions, completed work, and next work.

Implementation is authorized. Live campaign startup remains intentionally absent
from the CLI until dataset selection is integrated and reviewed.

## Local checks

```bash
./scripts/test.sh
PYTHONPATH=src python3 -m fuzzynth doctor
```

`doctor` validates the external dual-provider credential boundary without making
network calls or displaying endpoint/key values.

The active worker matrix, durable budgets, and session state are inspectable
offline:

```bash
PYTHONPATH=src python3 -m fuzzynth workers --seed 7
PYTHONPATH=src python3 -m fuzzynth budget-status
PYTHONPATH=src python3 -m fuzzynth session-status
```

## Pinned V8 build

The target is resolved in `config/v8-target.toml`; build profiles live in
`config/v8-builds.toml`. Runtime checkouts, binaries, and manifests stay under
ignored `.local/` storage.

```bash
./scripts/v8_checkout.sh
./scripts/v8_install_deps.sh
FUZZYNTH_BUILD_JOBS="$(nproc)" ./scripts/v8_build.sh release_symbolized
./scripts/v8_smoke.py release_symbolized
```

The build refuses to run if checkout `HEAD` differs from the configured V8
revision. `optdebug` and `asan` are independent profiles for confirmation and
triage rather than silent changes to the throughput binary.

A single capped live capability request requires the explicit `--live` flag:

```bash
PYTHONPATH=src python3 -m fuzzynth probe \
  --live --provider alternate --model gpt-5.3-codex-spark
```

Optional parameters are omitted unless explicitly supplied. Probe output contains
only capability, latency, model identity, parameter-presence, and usage metadata;
generated response text is not printed.

## Evidence-preserving execution

After packaging a profile, one local JS file can be run through the same isolated
worker path intended for generated programs:

```bash
PYTHONPATH=src python3 -m fuzzynth execute \
  --program sample.js \
  --profile release_symbolized \
  --worker-profile standard \
  --flag=--allow-natives-syntax
```

The command resolves the tag to the image ID pinned in the local worker manifest,
captures bounded stdout/stderr as content-addressed artifacts, records termination,
build identity, final Docker state, and exact resource limits in the SQLite
catalog, and prints metadata rather than program output. Runtime evidence stays
under ignored `state/` storage.

## Development notifications

The owner has authorized a minimal Telegram development notifier:

```bash
python3 scripts/notify_telegram.py "Fuzzynth development update"
```

It reads the external owner-only credentials file by default. A message can also
be passed through standard input, and `--silent` suppresses notification sound.
The script sends updates only; it does not accept Telegram commands.
