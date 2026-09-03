"""Bounded execution of untrusted JavaScript inside a prebuilt d8 container."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import time
import uuid

from fuzzynth.outcomes import ExecutionOutcome, ProcessObservation, classify


class DockerExecutionError(RuntimeError):
    """The worker infrastructure failed before a trustworthy result existed."""


@dataclass(frozen=True, slots=True)
class WorkerLimits:
    cpus: float = 1.0
    memory_bytes: int = 1024 * 1024 * 1024
    pids: int = 64
    wall_seconds: float = 5.0
    max_output_bytes: int = 1024 * 1024
    tmpfs_bytes: int = 64 * 1024 * 1024

    def __post_init__(self) -> None:
        for name in ("cpus", "wall_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be positive")
        for name in ("memory_bytes", "pids", "max_output_bytes", "tmpfs_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class ExecutionCapture:
    container_id: str
    duration_ms: int
    stdout: bytes
    stderr: bytes
    observation: ProcessObservation
    outcome: ExecutionOutcome
    docker_error: str
    container_state: dict[str, object]


_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RECEIVED_SIGNAL = re.compile(rb"Received signal\s+([0-9]{1,3})")


class DockerExecutor:
    def __init__(
        self,
        image_id: str,
        limits: WorkerLimits | None = None,
        *,
        unconfined_seccomp: bool = False,
    ):
        if not _IMAGE_ID.fullmatch(image_id):
            raise ValueError("image_id must be an immutable sha256 Docker image ID")
        self.image_id = image_id
        self.limits = limits or WorkerLimits()
        self.unconfined_seccomp = unconfined_seccomp

    def run(
        self,
        program_path: Path,
        flags: tuple[str, ...] = (),
        support_files: tuple[tuple[Path, str], ...] = (),
    ) -> ExecutionCapture:
        source = program_path.resolve(strict=True)
        if not source.is_file():
            raise ValueError("program_path must be a regular file")
        if "," in str(source) or "\n" in str(source):
            raise ValueError("program_path cannot be represented as a Docker mount")
        for flag in flags:
            if not isinstance(flag, str) or not flag.startswith("--") or "\0" in flag:
                raise ValueError("d8 flags must be NUL-free --options")
        resolved_support: list[tuple[Path, str]] = []
        targets: set[str] = set()
        for support_source, target in support_files:
            selected = support_source.resolve(strict=True)
            if not selected.is_file():
                raise ValueError("support source must be a regular file")
            if "," in str(selected) or "\n" in str(selected):
                raise ValueError("support source cannot be represented as a Docker mount")
            if re.fullmatch(r"/input/[a-z0-9_-]+\.js", target) is None:
                raise ValueError("support target must be a safe /input JavaScript path")
            if target == "/input/program.js" or target in targets:
                raise ValueError("support target conflicts with another mount")
            targets.add(target)
            resolved_support.append((selected, target))

        name = f"fuzzynth-{uuid.uuid4().hex}"
        container_id = ""
        try:
            created = subprocess.run(
                self._create_command(name, source, flags, tuple(resolved_support)),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
            )
            if created.returncode != 0:
                raise DockerExecutionError(
                    f"docker create failed (exit={created.returncode})"
                )
            container_id = created.stdout.decode("ascii", errors="strict").strip()
            if not re.fullmatch(r"[0-9a-f]{64}", container_id):
                raise DockerExecutionError("docker create returned an invalid container ID")

            started_at = time.monotonic()
            stdout, stderr, timed_out, output_truncated = self._start_and_capture(
                container_id
            )
            duration_ms = round((time.monotonic() - started_at) * 1000)
            state = self._inspect_state(container_id)
            exit_code = state.get("ExitCode")
            if not isinstance(exit_code, int) or isinstance(exit_code, bool):
                raise DockerExecutionError("docker state has no valid exit code")
            oom_killed = state.get("OOMKilled") is True
            docker_error = state.get("Error")
            if not isinstance(docker_error, str):
                docker_error = ""

            signal_number = None
            if not timed_out and not output_truncated and not oom_killed:
                matches = _RECEIVED_SIGNAL.findall(stdout + b"\n" + stderr)
                if matches:
                    signal_number = int(matches[-1])

            observation = ProcessObservation(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                signal_number=signal_number,
                timed_out=timed_out,
                oom_killed=oom_killed,
                output_truncated=output_truncated,
            )
            return ExecutionCapture(
                container_id=container_id,
                duration_ms=duration_ms,
                stdout=stdout,
                stderr=stderr,
                observation=observation,
                outcome=classify(observation),
                docker_error=docker_error,
                container_state=state,
            )
        except subprocess.TimeoutExpired as exc:
            raise DockerExecutionError("Docker control command timed out") from exc
        finally:
            if container_id:
                try:
                    subprocess.run(
                        ["docker", "rm", "--force", container_id],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=10,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    # Do not replace a trustworthy execution result/error with a
                    # cleanup error. A controller-level reaper handles leftovers.
                    pass

    def _create_command(
        self,
        name: str,
        source: Path,
        flags: tuple[str, ...],
        support_files: tuple[tuple[Path, str], ...] = (),
    ) -> list[str]:
        limits = self.limits
        command = [
            "docker",
            "create",
            "--name",
            name,
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(limits.pids),
            "--memory",
            str(limits.memory_bytes),
            "--memory-swap",
            str(limits.memory_bytes),
            "--cpus",
            str(limits.cpus),
            "--ulimit",
            "core=0:0",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,nodev,size={limits.tmpfs_bytes}",
            "--mount",
            f"type=bind,src={source},dst=/input/program.js,readonly",
        ]
        if self.unconfined_seccomp:
            # LLVM TSan must call personality(ADDR_NO_RANDOMIZE) before it can
            # reserve shadow memory on hosts with high mmap ASLR entropy. The
            # default Docker seccomp policy blocks that syscall. All other
            # isolation (no network, read-only rootfs, no capabilities,
            # no-new-privileges and resource limits) remains enforced.
            command.extend(("--security-opt", "seccomp=unconfined"))
        for support_source, target in support_files:
            command.extend(
                (
                    "--mount",
                    f"type=bind,src={support_source},dst={target},readonly",
                )
            )
        command.extend((self.image_id, *flags, "/input/program.js"))
        return command

    def _start_and_capture(
        self, container_id: str
    ) -> tuple[bytes, bytes, bool, bool]:
        process = subprocess.Popen(
            ["docker", "start", "--attach", container_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if process.stdout is None or process.stderr is None:
            process.kill()
            raise DockerExecutionError("failed to capture Docker worker output")

        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        total = 0
        timed_out = False
        output_truncated = False
        killed = False
        deadline = time.monotonic() + self.limits.wall_seconds

        try:
            while selector.get_map():
                now = time.monotonic()
                if process.poll() is None and now >= deadline and not killed:
                    timed_out = True
                    self._detach_and_kill(process, selector, container_id)
                    killed = True
                    break

                events = selector.select(timeout=0.1)
                for key, _mask in events:
                    chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    remaining = self.limits.max_output_bytes - total
                    if remaining > 0:
                        buffers[key.data].extend(chunk[:remaining])
                        total += min(len(chunk), remaining)
                    if len(chunk) > remaining and not output_truncated:
                        output_truncated = True
                        if process.poll() is None and not killed:
                            self._detach_and_kill(process, selector, container_id)
                            killed = True
                            break

                if killed:
                    break

                if process.poll() is not None and not events:
                    # A final select iteration observes EOF and unregisters pipes.
                    continue
            if not killed:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        finally:
            selector.close()

        return (
            bytes(buffers["stdout"]),
            bytes(buffers["stderr"]),
            timed_out,
            output_truncated,
        )

    @staticmethod
    def _kill_container(container_id: str) -> None:
        subprocess.run(
            ["docker", "kill", "--signal", "KILL", container_id],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )

    @classmethod
    def _detach_and_kill(
        cls,
        process: subprocess.Popen[bytes],
        selector: selectors.BaseSelector,
        container_id: str,
    ) -> None:
        # Detach the high-volume attach client before asking the daemon to kill
        # the container. Otherwise Docker can wait on its own blocked attach
        # stream while the controller waits on `docker kill`.
        for key in list(selector.get_map().values()):
            selector.unregister(key.fileobj)
            key.fileobj.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        cls._kill_container(container_id)

    @staticmethod
    def _inspect_state(container_id: str) -> dict[str, object]:
        inspected = subprocess.run(
            ["docker", "inspect", "--format", "{{json .State}}", container_id],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        if inspected.returncode != 0:
            raise DockerExecutionError("docker inspect failed")
        try:
            state = json.loads(inspected.stdout)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise DockerExecutionError("docker inspect returned invalid state JSON") from exc
        if not isinstance(state, dict):
            raise DockerExecutionError("docker inspect state is not an object")
        return state
