"""Load security-preserving worker limits from versioned configuration."""

from __future__ import annotations

from pathlib import Path
import tomllib

from fuzzynth.docker_executor import WorkerLimits


DEFAULT_WORKER_CONFIG = Path("config/worker-profiles.toml")


def load_worker_limits(name: str, path: Path = DEFAULT_WORKER_CONFIG) -> WorkerLimits:
    with path.open("rb") as stream:
        config = tomllib.load(stream)
    try:
        profile = config["profiles"][name]
    except KeyError as exc:
        raise ValueError(f"unknown worker profile: {name}") from exc

    required_security = {
        "network": "none",
        "read_only_root": True,
        "cap_drop": "all",
        "no_new_privileges": True,
    }
    for key, expected in required_security.items():
        if profile.get(key) != expected:
            raise ValueError(f"worker profile weakens required security field: {key}")

    return WorkerLimits(
        cpus=profile["cpus"],
        memory_bytes=profile["memory_bytes"],
        pids=profile["pids"],
        wall_seconds=profile["wall_seconds"],
        max_output_bytes=profile["max_output_bytes"],
    )
