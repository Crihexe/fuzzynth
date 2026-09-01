# Fuzzynth

Fuzzynth is a planned LLM-driven JavaScript and WebAssembly fuzzing laboratory for
V8's `d8` shell.

The repository is currently in the planning and review phase. No fuzzer,
provider call, V8 checkout, or V8 build has been started yet.

Project documents:

- [instruction.md](instruction.md) — durable project instructions and constraints.
- [PLAN.md](PLAN.md) — architecture, campaign design, milestones, and open questions.
- [PROJECT_LOG.md](PROJECT_LOG.md) — living status, decisions, completed work, and next work.

Implementation starts only after the owner reviews and approves the plan.

## Development notifications

The owner has authorized a minimal Telegram notifier during the planning phase:

```bash
python3 scripts/notify_telegram.py "Fuzzynth development update"
```

It reads the external owner-only credentials file by default. A message can also
be passed through standard input, and `--silent` suppresses notification sound.
The script sends updates only; it does not accept Telegram commands.
