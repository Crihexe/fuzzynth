"""Strict configuration and reproducible session choices for campaign workers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import random
import tomllib


class CampaignConfigurationError(RuntimeError):
    """Worker configuration is incomplete or unsafe to execute."""


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    max_context_bytes: int
    max_feedback_bytes: int
    dataset_enabled: bool
    dataset_root: str
    dataset_window_policy: str


@dataclass(frozen=True, slots=True)
class CampaignWorker:
    worker_id: str
    enabled: bool
    provider: str
    model: str
    meter: str
    mode: str
    prompt_path: Path
    reasoning_efforts: tuple[str, ...]
    verbosity: str
    temperatures: tuple[float, ...]
    min_turns_per_session: int
    max_turns_per_session: int
    history_turns: int
    max_output_tokens: int
    reservation_output_tokens: int
    v8_build_profile: str
    v8_worker_profile: str
    d8_flags: tuple[str, ...]
    send_reasoning: bool = True
    send_verbosity: bool = True
    pricing_profile: str | None = None
    prompt_variant: str = "legacy"
    corpus_pair_id: str = "legacy"
    corpus_strategy: str = "uniform"


@dataclass(frozen=True, slots=True)
class SessionPlan:
    seed: int
    target_turns: int
    reasoning_effort: str
    temperature: float | None


@dataclass(frozen=True, slots=True)
class CampaignConfiguration:
    context: ContextPolicy
    workers: dict[str, CampaignWorker]

    def enabled_workers(self) -> tuple[CampaignWorker, ...]:
        return tuple(worker for worker in self.workers.values() if worker.enabled)


def _integer(raw: dict[str, object], name: str, *, minimum: int = 0) -> int:
    value = raw.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise CampaignConfigurationError(f"{name} must be an integer >= {minimum}")
    return value


def _string(raw: dict[str, object], name: str) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value.strip():
        raise CampaignConfigurationError(f"{name} must be a non-empty string")
    return value


def _string_list(raw: dict[str, object], name: str) -> tuple[str, ...]:
    value = raw.get(name)
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise CampaignConfigurationError(f"{name} must be a string array")
    return tuple(value)


def _optional_bool(raw: dict[str, object], name: str, *, default: bool) -> bool:
    value = raw.get(name, default)
    if not isinstance(value, bool):
        raise CampaignConfigurationError(f"{name} must be boolean")
    return value


def _optional_string(raw: dict[str, object], name: str) -> str | None:
    value = raw.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CampaignConfigurationError(f"{name} must be a non-empty string")
    return value


def load_campaign_configuration(path: Path, *, repo_root: Path = Path(".")) -> CampaignConfiguration:
    try:
        with path.open("rb") as stream:
            document = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CampaignConfigurationError("unable to load campaign workers") from exc
    if document.get("schema_version") != 1:
        raise CampaignConfigurationError("unsupported campaign configuration version")

    raw_context = document.get("context")
    if not isinstance(raw_context, dict):
        raise CampaignConfigurationError("campaign context configuration is missing")
    context = ContextPolicy(
        max_context_bytes=_integer(raw_context, "max_context_bytes", minimum=1),
        max_feedback_bytes=_integer(raw_context, "max_feedback_bytes", minimum=1),
        dataset_enabled=raw_context.get("dataset_enabled") is True,
        dataset_root=_string(raw_context, "dataset_root"),
        dataset_window_policy=_string(raw_context, "dataset_window_policy"),
    )

    configured_workers = document.get("workers")
    if not isinstance(configured_workers, list) or not configured_workers:
        raise CampaignConfigurationError("no campaign workers are configured")
    workers: dict[str, CampaignWorker] = {}
    allowed_reasoning = {"none", "low", "medium", "high", "xhigh", "max"}
    for raw in configured_workers:
        if not isinstance(raw, dict):
            raise CampaignConfigurationError("worker entry is not a table")
        worker_id = _string(raw, "id")
        if worker_id in workers:
            raise CampaignConfigurationError(f"duplicate worker id: {worker_id}")
        reasoning = _string_list(raw, "reasoning_efforts")
        if not reasoning or not set(reasoning) <= allowed_reasoning:
            raise CampaignConfigurationError(f"invalid reasoning efforts: {worker_id}")
        temperature_values = raw.get("temperatures")
        if not isinstance(temperature_values, list) or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 <= value <= 2
            for value in temperature_values
        ):
            raise CampaignConfigurationError(f"invalid temperatures: {worker_id}")
        temperatures = tuple(float(value) for value in temperature_values)
        provider = _string(raw, "provider")
        if provider == "alternate" and temperatures:
            raise CampaignConfigurationError(
                f"alternate worker must omit temperature: {worker_id}"
            )
        prompt_path = repo_root / _string(raw, "prompt")
        if not prompt_path.is_file():
            raise CampaignConfigurationError(f"worker prompt is missing: {worker_id}")
        minimum_turns = _integer(raw, "min_turns_per_session", minimum=1)
        maximum_turns = _integer(raw, "max_turns_per_session", minimum=1)
        if maximum_turns < minimum_turns:
            raise CampaignConfigurationError(f"invalid turn range: {worker_id}")
        flags = _string_list(raw, "d8_flags")
        if any(not flag.startswith("--") for flag in flags):
            raise CampaignConfigurationError(f"invalid d8 flag: {worker_id}")
        if "--fuzzing" not in flags:
            raise CampaignConfigurationError(
                f"campaign worker must execute d8 with --fuzzing: {worker_id}"
            )
        verbosity = _string(raw, "verbosity")
        if verbosity not in {"low", "medium", "high"}:
            raise CampaignConfigurationError(f"invalid verbosity: {worker_id}")
        enabled = raw.get("enabled")
        if not isinstance(enabled, bool):
            raise CampaignConfigurationError(f"enabled must be boolean: {worker_id}")
        worker = CampaignWorker(
            worker_id=worker_id,
            enabled=enabled,
            provider=provider,
            model=_string(raw, "model"),
            meter=_string(raw, "meter"),
            mode=_string(raw, "mode"),
            prompt_path=prompt_path,
            reasoning_efforts=reasoning,
            verbosity=verbosity,
            temperatures=temperatures,
            min_turns_per_session=minimum_turns,
            max_turns_per_session=maximum_turns,
            history_turns=_integer(raw, "history_turns"),
            max_output_tokens=_integer(raw, "max_output_tokens", minimum=1),
            reservation_output_tokens=_integer(raw, "reservation_output_tokens"),
            v8_build_profile=_string(raw, "v8_build_profile"),
            v8_worker_profile=_string(raw, "v8_worker_profile"),
            d8_flags=flags,
            send_reasoning=_optional_bool(
                raw, "send_reasoning", default=True
            ),
            send_verbosity=_optional_bool(
                raw, "send_verbosity", default=True
            ),
            pricing_profile=_optional_string(raw, "pricing_profile"),
            prompt_variant=_optional_string(raw, "prompt_variant") or "legacy",
            corpus_pair_id=_optional_string(raw, "corpus_pair_id") or worker_id,
            corpus_strategy=_optional_string(raw, "corpus_strategy") or "uniform",
        )
        if worker.corpus_strategy not in {"uniform", "stratified_v8"}:
            raise CampaignConfigurationError(
                f"invalid corpus strategy: {worker_id}"
            )
        if enabled and worker.mode != "iterative_raw_js":
            raise CampaignConfigurationError(
                f"enabled worker mode is not implemented: {worker_id}"
            )
        workers[worker_id] = worker
    paired: dict[str, list[CampaignWorker]] = {}
    for worker in workers.values():
        if worker.enabled:
            paired.setdefault(worker.corpus_pair_id, []).append(worker)
    for pair_id, variants in paired.items():
        if len(variants) != 2 or len(
            {item.prompt_variant for item in variants}
        ) != 2:
            raise CampaignConfigurationError(
                f"enabled corpus pair must contain two distinct prompt variants: {pair_id}"
            )
        comparable = {
            (
                item.provider,
                item.model,
                item.meter,
                item.mode,
                item.reasoning_efforts,
                item.verbosity,
                item.temperatures,
                item.min_turns_per_session,
                item.max_turns_per_session,
                item.history_turns,
                item.max_output_tokens,
                item.reservation_output_tokens,
                item.v8_build_profile,
                item.v8_worker_profile,
                item.d8_flags,
                item.send_reasoning,
                item.send_verbosity,
                item.pricing_profile,
                item.corpus_strategy,
            )
            for item in variants
        }
        if len(comparable) != 1:
            raise CampaignConfigurationError(
                f"paired workers differ beyond their prompt: {pair_id}"
            )
    return CampaignConfiguration(context=context, workers=workers)


def choose_session_plan(worker: CampaignWorker, seed: int) -> SessionPlan:
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("session seed must be an integer")
    generator = random.Random(seed)
    return SessionPlan(
        seed=seed,
        target_turns=generator.randint(
            worker.min_turns_per_session,
            worker.max_turns_per_session,
        ),
        reasoning_effort=generator.choice(worker.reasoning_efforts),
        temperature=(
            generator.choice(worker.temperatures) if worker.temperatures else None
        ),
    )
