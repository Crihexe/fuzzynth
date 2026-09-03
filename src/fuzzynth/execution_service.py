"""Evidence-preserving one-shot execution through a pinned isolated worker."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import tomllib
import uuid

from fuzzynth.artifacts import ArtifactStore
from fuzzynth.catalog import EvidenceCatalog, ExecutionRecord
from fuzzynth.docker_executor import DockerExecutor
from fuzzynth.worker_config import load_worker_limits


class ExecutionServiceError(RuntimeError):
    """Pinned worker identity or local execution configuration is unavailable."""


_SUPPORT_FILES = {
    "wasm_module_builder": (
        Path("test/mjsunit/wasm/wasm-module-builder.js"),
        "/input/wasm-module-builder.js",
    ),
}


@dataclass(frozen=True, slots=True)
class RecordedExecution:
    execution_id: str
    profile: str
    image_id: str
    d8_sha256: str
    program_sha256: str
    stdout_sha256: str
    stderr_sha256: str
    duration_ms: int
    outcome: str
    bug_candidate: bool
    exit_code: int | None
    signal_name: str | None
    timed_out: bool
    oom_killed: bool
    output_truncated: bool
    stdout: bytes = field(repr=False)
    stderr: bytes = field(repr=False)
    details_sha256: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
            if name not in {"stdout", "stderr"}
        }


def execute_file(
    program_path: Path,
    *,
    build_profile: str = "release_symbolized",
    worker_profile: str = "standard",
    flags: tuple[str, ...] = (),
    repo_root: Path = Path("."),
    state_root: Path = Path("state"),
    max_program_bytes: int = 2 * 1024 * 1024,
    support_files: tuple[str, ...] = (),
) -> RecordedExecution:
    return execute_program(
        program_path.read_bytes(),
        build_profile=build_profile,
        worker_profile=worker_profile,
        flags=flags,
        repo_root=repo_root,
        state_root=state_root,
        max_program_bytes=max_program_bytes,
        support_files=support_files,
    )


def execute_program(
    program: bytes,
    *,
    generation_id: str | None = None,
    build_profile: str = "release_symbolized",
    worker_profile: str = "standard",
    flags: tuple[str, ...] = (),
    repo_root: Path = Path("."),
    state_root: Path = Path("state"),
    max_program_bytes: int = 2 * 1024 * 1024,
    support_files: tuple[str, ...] = (),
) -> RecordedExecution:
    repo_root = repo_root.resolve()
    state_root = state_root.resolve()
    if not isinstance(program, bytes):
        raise TypeError("program must be exact bytes")
    if len(program) > max_program_bytes:
        raise ExecutionServiceError("program exceeds local byte limit")

    with (repo_root / "config/v8-target.toml").open("rb") as stream:
        target_config = tomllib.load(stream)
    with (repo_root / "config/v8-builds.toml").open("rb") as stream:
        build_config = tomllib.load(stream)
    try:
        profile_config = build_config["profiles"][build_profile]
    except KeyError as exc:
        raise ExecutionServiceError(f"unknown V8 build profile: {build_profile}") from exc

    revision = target_config["v8_revision"]
    v8_root = repo_root / ".local/v8-workspace/v8"
    build_manifest_path = (
        repo_root / ".local/build-manifests" / f"{build_profile}-{revision}.json"
    )
    try:
        build_manifest = json.loads(build_manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExecutionServiceError("pinned local build manifest is unavailable") from exc
    d8_sha256 = build_manifest.get("binary_sha256")
    if not isinstance(d8_sha256, str) or len(d8_sha256) != 64:
        raise ExecutionServiceError("build manifest has no valid d8 identity")
    if (
        build_manifest.get("v8_revision") != revision
        or build_manifest.get("profile") != build_profile
    ):
        raise ExecutionServiceError("build manifest identity does not match configuration")

    selected_support: list[tuple[Path, str]] = []
    support_evidence: list[dict[str, object]] = []
    if len(set(support_files)) != len(support_files):
        raise ExecutionServiceError("duplicate d8 support file")
    if support_files:
        try:
            checked_revision = subprocess.run(
                ["git", "-C", str(v8_root), "rev-parse", "HEAD"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                text=True,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ExecutionServiceError(
                "unable to verify d8 support source revision"
            ) from exc
        if (
            checked_revision.returncode != 0
            or checked_revision.stdout.strip() != revision
        ):
            raise ExecutionServiceError(
                "d8 support source does not match the pinned V8 revision"
            )
    for name in support_files:
        try:
            relative_source, target = _SUPPORT_FILES[name]
        except KeyError as exc:
            raise ExecutionServiceError(f"unknown d8 support file: {name}") from exc
        source = (v8_root / relative_source).resolve()
        try:
            data = source.read_bytes()
        except OSError as exc:
            raise ExecutionServiceError(f"d8 support file is unavailable: {name}") from exc
        selected_support.append((source, target))
        support_evidence.append(
            {
                "name": name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "target": target,
                "v8_revision": revision,
            }
        )

    worker_manifest_path = (
        repo_root / ".local/worker-images" / f"{build_profile}-{revision}.json"
    )
    try:
        worker_manifest = json.loads(worker_manifest_path.read_text(encoding="utf-8"))
        expected_image_id = worker_manifest[0]["Id"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise ExecutionServiceError("pinned worker image manifest is unavailable") from exc
    if not isinstance(expected_image_id, str):
        raise ExecutionServiceError("worker manifest has no valid image identity")

    image_tag = f"fuzzynth/d8-{build_profile}:{revision[:12]}"
    try:
        inspected = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image_tag],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ExecutionServiceError("worker image inspection failed") from exc
    if inspected.returncode != 0:
        raise ExecutionServiceError("pinned worker image is unavailable")
    image_id = inspected.stdout.strip()
    if image_id != expected_image_id:
        raise ExecutionServiceError("worker image tag does not match pinned manifest")

    store = ArtifactStore(state_root / "artifacts")
    program_ref = store.put(program)
    limits = load_worker_limits(
        worker_profile, repo_root / "config/worker-profiles.toml"
    )
    started_at = datetime.now(timezone.utc).isoformat()
    # Artifacts remain mode 0600 at rest. A short-lived copy inside a mode-0700
    # directory is read-only to the non-root container user during the bind mount.
    with tempfile.TemporaryDirectory(prefix="execute-", dir=state_root) as temporary:
        staged_program = Path(temporary) / "program.js"
        staged_program.write_bytes(program)
        staged_program.chmod(0o444)
        unconfined_seccomp = build_profile in {"tsan", "msan"}
        capture = DockerExecutor(
            image_id,
            limits,
            unconfined_seccomp=unconfined_seccomp,
        ).run(
            staged_program,
            flags,
            tuple(selected_support),
        )
    stdout_ref = store.put(capture.stdout)
    stderr_ref = store.put(capture.stderr)
    details_ref = store.put(
        json.dumps(
            {
                "build_profile": build_profile,
                "capture": {
                    "stderr_bytes": len(capture.stderr),
                    "stdout_bytes": len(capture.stdout),
                },
                "container_id": capture.container_id,
                "container_state": capture.container_state,
                "core_dump_limit": 0,
                "d8_flags": list(flags),
                "d8_sha256": d8_sha256,
                "image_id": image_id,
                "schema_version": 1,
                "seccomp_profile": (
                    "unconfined_for_sanitizer_aslr"
                    if unconfined_seccomp
                    else "docker_default"
                ),
                "support_files": support_evidence,
                "worker_limits": {
                    "cpus": limits.cpus,
                    "max_output_bytes": limits.max_output_bytes,
                    "memory_bytes": limits.memory_bytes,
                    "pids": limits.pids,
                    "tmpfs_bytes": limits.tmpfs_bytes,
                    "wall_seconds": limits.wall_seconds,
                },
                "worker_profile": worker_profile,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    execution_id = f"exec-{uuid.uuid4()}"

    with EvidenceCatalog(state_root / "catalog.sqlite3") as catalog:
        catalog.record_execution(
            ExecutionRecord(
                execution_id=execution_id,
                generation_id=generation_id,
                program=program_ref,
                stdout=stdout_ref,
                stderr=stderr_ref,
                profile=build_profile,
                image_id=image_id,
                d8_sha256=d8_sha256,
                flags=flags,
                outcome=capture.outcome.kind,
                bug_candidate=capture.outcome.bug_candidate,
                exit_code=capture.observation.exit_code,
                signal_name=capture.outcome.signal_name,
                timed_out=capture.observation.timed_out,
                oom_killed=capture.observation.oom_killed,
                output_truncated=capture.observation.output_truncated,
                duration_ms=capture.duration_ms,
                docker_error=capture.docker_error,
                started_at=started_at,
                details=details_ref,
            )
        )

    return RecordedExecution(
        execution_id=execution_id,
        profile=build_profile,
        image_id=image_id,
        d8_sha256=d8_sha256,
        program_sha256=program_ref.sha256,
        stdout_sha256=stdout_ref.sha256,
        stderr_sha256=stderr_ref.sha256,
        duration_ms=capture.duration_ms,
        outcome=capture.outcome.kind,
        bug_candidate=capture.outcome.bug_candidate,
        exit_code=capture.observation.exit_code,
        signal_name=capture.outcome.signal_name,
        timed_out=capture.observation.timed_out,
        oom_killed=capture.observation.oom_killed,
        output_truncated=capture.observation.output_truncated,
        stdout=capture.stdout,
        stderr=capture.stderr,
        details_sha256=details_ref.sha256,
    )
