"""Deterministic first-pass classification for captured d8 process outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
import signal


class OutcomeKind(StrEnum):
    OK = "ok"
    JS_EXCEPTION = "js_exception"
    WASM_TRAP = "wasm_trap"
    NONZERO_EXIT = "nonzero_exit"
    TIMEOUT = "timeout"
    OOM = "oom"
    OUTPUT_LIMIT = "output_limit"
    SIGNAL = "signal"
    V8_FATAL = "v8_fatal"
    SANITIZER = "sanitizer"


@dataclass(frozen=True, slots=True)
class ProcessObservation:
    exit_code: int | None
    stdout: bytes = b""
    stderr: bytes = b""
    signal_number: int | None = None
    timed_out: bool = False
    oom_killed: bool = False
    output_truncated: bool = False

    def __post_init__(self) -> None:
        if self.exit_code is not None and self.exit_code < 0:
            raise ValueError("exit_code must not encode a negative subprocess signal")
        if self.signal_number is not None and self.signal_number < 1:
            raise ValueError("signal_number must be positive")


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    kind: OutcomeKind
    bug_candidate: bool
    signal_name: str | None
    markers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HarnessMisuseDiagnostic:
    code: str
    guidance: str


_SANITIZER_MARKERS = (
    (b"ERROR: AddressSanitizer", "asan"),
    (b"AddressSanitizer:DEADLYSIGNAL", "asan_deadly_signal"),
    (b"ERROR: LeakSanitizer", "lsan"),
    (b"WARNING: ThreadSanitizer", "tsan"),
    (b"WARNING: MemorySanitizer", "msan"),
    (b"UndefinedBehaviorSanitizer", "ubsan"),
    (b"runtime error:", "ubsan_runtime_error"),
)

_V8_FATAL_MARKERS = (
    (b"# Fatal error in", "v8_fatal"),
    (b"Check failed:", "check_failed"),
    (b"DCHECK failed", "dcheck_failed"),
    (b"V8_Fatal", "v8_fatal_symbol"),
)

_JS_EXCEPTION_MARKERS = (
    b"SyntaxError:",
    b"TypeError:",
    b"ReferenceError:",
    b"RangeError:",
    b"EvalError:",
    b"URIError:",
    b"Uncaught ",
)

_WASM_TRAP_MARKERS = (
    b"WebAssembly.RuntimeError:",
    b"RuntimeError: unreachable",
    b"RuntimeError: memory access out of bounds",
    b"RuntimeError: divide by zero",
)


_INLINE_ARROW_PREPARE = re.compile(
    rb"%PrepareFunctionForOptimization\s*\(\s*(?:async\s*)?\([^)]*\)\s*=>"
)
_INLINE_ARROW_OPTIMIZE = re.compile(
    rb"%OptimizeFunctionOnNextCall\s*\(\s*(?:async\s*)?\([^)]*\)\s*=>"
)


def diagnose_harness_misuse(
    program: bytes,
    stderr: bytes,
) -> HarnessMisuseDiagnostic | None:
    """Recognize only narrow, explainable d8 native-syntax contract mistakes."""

    if (
        b"EnsureCompiledAndFeedbackVector" in stderr
        and re.search(rb"%GC\s*\(", program) is not None
    ):
        return HarnessMisuseDiagnostic(
            code="invalid_percent_gc_intrinsic",
            guidance=(
                "Do not use %GC(). With --expose-gc call the d8 helper gc() "
                "instead, outside optimization intrinsics."
            ),
        )
    if (
        b"CheckMarkedForManualOptimization" in stderr
        and _INLINE_ARROW_PREPARE.search(program) is not None
        and _INLINE_ARROW_OPTIMIZE.search(program) is not None
    ):
        return HarnessMisuseDiagnostic(
            code="fresh_function_optimization_target",
            guidance=(
                "Prepare and optimize the same stable named function object; "
                "separate inline arrow expressions create different functions."
            ),
        )
    return None


def _present_markers(
    combined: bytes, candidates: tuple[tuple[bytes, str], ...]
) -> tuple[str, ...]:
    return tuple(label for needle, label in candidates if needle in combined)


def _signal_name(number: int | None) -> str | None:
    if number is None:
        return None
    try:
        return signal.Signals(number).name
    except ValueError:
        return f"SIG{number}"


def classify(observation: ProcessObservation) -> ExecutionOutcome:
    """Classify only captured facts; never ask the generating model to decide."""

    combined = observation.stdout + b"\n" + observation.stderr
    sanitizer = _present_markers(combined, _SANITIZER_MARKERS)
    if sanitizer:
        return ExecutionOutcome(
            kind=OutcomeKind.SANITIZER,
            bug_candidate=True,
            signal_name=_signal_name(observation.signal_number),
            markers=sanitizer,
        )

    v8_fatal = _present_markers(combined, _V8_FATAL_MARKERS)
    if v8_fatal:
        return ExecutionOutcome(
            kind=OutcomeKind.V8_FATAL,
            bug_candidate=True,
            signal_name=_signal_name(observation.signal_number),
            markers=v8_fatal,
        )

    if observation.signal_number is not None:
        return ExecutionOutcome(
            kind=OutcomeKind.SIGNAL,
            bug_candidate=True,
            signal_name=_signal_name(observation.signal_number),
            markers=(),
        )

    if observation.oom_killed:
        return ExecutionOutcome(
            kind=OutcomeKind.OOM,
            bug_candidate=False,
            signal_name=None,
            markers=("oom_killed",),
        )

    if observation.timed_out:
        return ExecutionOutcome(
            kind=OutcomeKind.TIMEOUT,
            bug_candidate=False,
            signal_name=None,
            markers=("wall_timeout",),
        )

    if observation.output_truncated:
        return ExecutionOutcome(
            kind=OutcomeKind.OUTPUT_LIMIT,
            bug_candidate=False,
            signal_name=None,
            markers=("output_limit",),
        )

    if observation.exit_code == 0:
        return ExecutionOutcome(
            kind=OutcomeKind.OK,
            bug_candidate=False,
            signal_name=None,
            markers=(),
        )

    if any(marker in combined for marker in _WASM_TRAP_MARKERS):
        return ExecutionOutcome(
            kind=OutcomeKind.WASM_TRAP,
            bug_candidate=False,
            signal_name=None,
            markers=("wasm_runtime_trap",),
        )

    if any(marker in combined for marker in _JS_EXCEPTION_MARKERS):
        return ExecutionOutcome(
            kind=OutcomeKind.JS_EXCEPTION,
            bug_candidate=False,
            signal_name=None,
            markers=("javascript_exception",),
        )

    return ExecutionOutcome(
        kind=OutcomeKind.NONZERO_EXIT,
        bug_candidate=False,
        signal_name=None,
        markers=(),
    )
