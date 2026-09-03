"""Feature-routed V8 stress profiles for replaying preserved programs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StressProfile:
    name: str
    build_profile: str
    flags: tuple[str, ...]
    markers: tuple[bytes, ...]

    def applies(self, program: bytes) -> bool:
        lowered = program.lower()
        return any(marker in lowered for marker in self.markers)


_COMMON = ("--allow-natives-syntax", "--expose-gc", "--fuzzing")
_CODE_MARKERS = (b"function", b"=>", b"class ", b"eval(")

STRESS_PROFILES = (
    StressProfile(
        name="uninitialized_memory",
        build_profile="msan",
        flags=_COMMON + ("--jit-fuzzing",),
        # Like UBSan, MSan is an independent native oracle relevant to every
        # preserved program that completed its primary execution.
        markers=(b"",),
    ),
    StressProfile(
        name="thread_safety",
        build_profile="tsan",
        flags=_COMMON
        + (
            "--jit-fuzzing",
            "--stress-concurrent-allocation",
            "--stress-concurrent-inlining-attach-code",
            "--stress-background-compile",
        ),
        markers=(b"sharedarraybuffer", b"worker("),
    ),
    StressProfile(
        name="undefined_behavior",
        build_profile="ubsan",
        flags=_COMMON + ("--jit-fuzzing",),
        # UBSan is an independent native oracle, so every preserved exit-0
        # program is relevant even if it has no easily recognized feature.
        markers=(b"",),
    ),
    StressProfile(
        name="compiler_verify",
        build_profile="optdebug",
        flags=_COMMON
        + (
            "--jit-fuzzing",
            "--turbo-verify",
            "--verify-turboshaft",
            "--maglev-assert",
        ),
        markers=_CODE_MARKERS,
    ),
    StressProfile(
        name="compiler_concurrent",
        build_profile="asan",
        flags=_COMMON
        + (
            "--jit-fuzzing",
            "--stress-concurrent-inlining",
            "--stress-background-compile",
        ),
        markers=_CODE_MARKERS,
    ),
    StressProfile(
        name="forced_deopt",
        build_profile="optdebug",
        flags=_COMMON + ("--jit-fuzzing", "--deopt-every-n-times=13"),
        markers=_CODE_MARKERS,
    ),
    StressProfile(
        name="maglev_assertions",
        build_profile="optdebug",
        flags=_COMMON
        + (
            "--jit-fuzzing",
            "--stress-maglev",
            "--maglev-assert",
            "--maglev-assert-types",
        ),
        markers=_CODE_MARKERS,
    ),
    StressProfile(
        name="compilation_gc_race",
        build_profile="asan",
        flags=_COMMON
        + (
            "--jit-fuzzing",
            "--stress-gc-during-compilation",
        ),
        markers=_CODE_MARKERS,
    ),
    StressProfile(
        name="memory_gc",
        build_profile="asan",
        flags=_COMMON
        + (
            "--jit-fuzzing",
            "--stress-compaction",
            "--stress-incremental-marking",
            "--stress-scavenge=100",
            "--random-seed=20260903",
        ),
        markers=(
            b"arraybuffer",
            b"sharedarraybuffer",
            b"weakref",
            b"finalizationregistry",
            b"gc(",
        ),
    ),
    StressProfile(
        name="wasm_tiering_memory",
        build_profile="asan",
        flags=_COMMON
        + (
            "--jit-fuzzing",
            "--wasm-sync-tier-up",
            "--stress-wasm-memory-moving",
            "--stress-wasm-code-gc",
        ),
        markers=(b"webassembly", b"wasmmodulebuilder", b"wasm-module-builder"),
    ),
    StressProfile(
        name="wasm_aggressive_inlining",
        build_profile="asan",
        flags=_COMMON
        + (
            "--jit-fuzzing",
            "--wasm-sync-tier-up",
            "--wasm-inlining-ignore-call-counts",
            "--wasm-in-js-inlining-opt",
            "--turbo-inline-js-wasm-calls",
            "--turbo-optimize-inlined-js-wasm-wrappers",
        ),
        markers=(b"webassembly", b"wasmmodulebuilder", b"wasm-module-builder"),
    ),
    StressProfile(
        name="wasm_stack_switching",
        build_profile="asan",
        flags=_COMMON
        + (
            "--jit-fuzzing",
            "--wasm-sync-tier-up",
            "--stress-wasm-stack-switching",
        ),
        markers=(b"webassembly", b"wasmmodulebuilder", b"wasm-module-builder"),
    ),
    StressProfile(
        name="heap_verification",
        build_profile="optdebug",
        flags=_COMMON
        + (
            "--jit-fuzzing",
            "--verify-heap",
            "--stress-compaction",
            "--stress-marking=50",
            "--random-seed=20260903",
        ),
        markers=(
            b"arraybuffer",
            b"sharedarraybuffer",
            b"weakref",
            b"finalizationregistry",
            b"gc(",
        ),
    ),
    StressProfile(
        name="experimental_regexp",
        build_profile="asan",
        flags=_COMMON
        + (
            "--jit-fuzzing",
            "--enable-experimental-regexp-engine",
            "--default-to-experimental-regexp-engine",
            "--experimental-regexp-engine-capture-group-opt",
        ),
        markers=(
            b"regexp",
            b".exec(",
            b".test(",
            b".replace(",
            b".replaceall(",
            b".match(",
            b".matchall(",
            b".search(",
            b".split(",
        ),
    ),
)


def applicable_profiles(program: bytes) -> tuple[StressProfile, ...]:
    if not isinstance(program, bytes):
        raise TypeError("program must be bytes")
    return tuple(profile for profile in STRESS_PROFILES if profile.applies(program))


def worker_profile_for(profile: StressProfile) -> str:
    """Give high-overhead MSan runs bounded headroom without slowing all replays."""

    return "sanitizer" if profile.build_profile == "msan" else "standard"
