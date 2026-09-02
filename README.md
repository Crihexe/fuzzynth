# Fuzzynth

Fuzzynth is an LLM-driven JavaScript and WebAssembly fuzzing laboratory for
V8's `d8` shell.

The pinned V8 worker matrix and the complete-response iterative controller are
implemented. A supervised campaign is running against the owner-approved v2
sample while the larger PoC dataset is prepared.

Project documents:

- [instruction.md](instruction.md) — durable project instructions and constraints.
- [PLAN.md](PLAN.md) — architecture, campaign design, milestones, and open questions.
- [PROJECT_LOG.md](PROJECT_LOG.md) — living status, decisions, completed work, and next work.

Implementation and supervised live campaign operation are authorized. Every
live command still requires an explicit `--live` switch.

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
PYTHONPATH=src python3 -m fuzzynth control-status
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

## Telegram control

The long-polling control process loads only the external Telegram credential file;
it does not load provider keys. It accepts commands only from the configured chat
and owner identity and persists every state-changing action:

```text
/status  /workers  /sessions  /cost  /budget  /lastcrash
/pause <worker|all>
/resume <worker|all>
/stop CONFIRM
/start CONFIRM
```

Changes are enforced before the next model turn. They do not kill a currently
running isolated `d8` process. Install or refresh the hardened system service with:

```bash
sudo ./scripts/install_telegram_control_service.sh
```

## Supervised v2 campaign

The checked-in v2 service runs independent Spark, custom Luna, official GPT-4o
mini, and official GPT-4.1 nano threads over the four explicitly reviewed
JavaScript corpus files. Custom API
requests use SSE to keep the gateway connection alive. The controller always
waits until the stream terminates before executing anything. Completed output is
cross-checked against the terminal response; a bounded text prefix from a failed
or incomplete terminal response (SSE or JSON) is archived, explicitly marked
partial, and may be executed once only after termination.
Unsupported custom controls (`max_output_tokens`, `max_completion_tokens`,
`temperature`, and `top_p`) are never sent. A quota or provider failure pauses
only its own worker; the other threads continue. When Spark is provider-paused,
a managed custom Luna `none`/high-verbosity lane replaces it until Spark safely
returns. Successive turns are sent as bounded alternating `user`/`assistant`
messages with the generated JavaScript retained only in its original assistant
message. Every request, raw response/stream, program, execution capture, and
session transition is preserved under private `state/` storage while journald
receives only safe identifiers and metrics.

The official models rotate temperature from 0 through 2. GPT-4o mini sessions
last at most three turns; GPT-4.1 nano sessions last exactly six. Neither request
sends reasoning or verbosity controls because live probing confirmed that the
deprecated GPT-4.1 nano model rejects `reasoning.effort`. Both use exact
model-specific rates inside one shared cumulative `$4.90` ledger.

```bash
sudo ./scripts/install_v2_campaign_service.sh
systemctl status fuzzynth-v2-campaign.service
journalctl -u fuzzynth-v2-campaign.service
```

The service waits without retrying while its worker is paused. A native crash is
terminal and is never replayed automatically. The deployed unit cannot access
`/root/red-sailor`; generated programs still execute in the separately isolated,
networkless d8 container.
