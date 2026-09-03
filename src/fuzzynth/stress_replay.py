"""Feature-routed V8 stress profiles for replaying preserved programs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib


@dataclass(frozen=True, slots=True)
class StressProfile:
    name: str
    build_profile: str
    flags: tuple[str, ...]
    markers: tuple[bytes, ...]
    seed_variants: int = 1

    def __post_init__(self) -> None:
        if self.seed_variants < 1:
            raise ValueError("seed_variants must be positive")
        if self.seed_variants > 1 and any(
            flag.startswith(("--random-seed=", "--fuzzer-random-seed="))
            for flag in self.flags
        ):
            raise ValueError("seeded profiles must not contain fixed seed flags")

    def applies(self, program: bytes) -> bool:
        lowered = program.lower()
        return any(marker in lowered for marker in self.markers)

    def flag_variants(self, program_sha256: str) -> tuple[tuple[str, ...], ...]:
        """Resolve deterministic scheduling seeds for exact crash reproduction."""

        if self.seed_variants == 1:
            return (self.flags,)
        variants: list[tuple[str, ...]] = []
        for ordinal in range(self.seed_variants):
            material = f"{self.name}:{program_sha256}:{ordinal}".encode("ascii")
            digest = hashlib.sha256(material).digest()
            random_seed = (int.from_bytes(digest[:4], "big") & 0x7FFFFFFF) or 1
            fuzzer_seed = (int.from_bytes(digest[4:8], "big") & 0x7FFFFFFF) or 1
            variants.append(
                self.flags
                + (
                    f"--random-seed={random_seed}",
                    f"--fuzzer-random-seed={fuzzer_seed}",
                )
            )
        return tuple(variants)


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
        name="maglev_future_checks",
        build_profile="optdebug",
        flags=_COMMON
        + (
            "--jit-fuzzing",
            "--stress-maglev",
            "--maglev-future",
            "--maglev-object-tracking",
            "--maglev-non-eager-inlining",
            "--maglev-licm",
            "--maglev-verify-dominance",
            "--maglev-assert",
            "--maglev-assert-types",
        ),
        markers=_CODE_MARKERS,
    ),
    StressProfile(
        name="turbolev_future_checks",
        build_profile="optdebug",
        flags=_COMMON
        + (
            "--jit-fuzzing",
            "--turbolev",
            "--turbolev-future",
            "--turbolev-escape-analysis",
            "--turbolev-non-eager-inlining",
            "--turbolev-non-eager-loop-peeling",
            "--maglev-range-verification",
            "--maglev-verify-dominance",
            "--turbo-verify",
            "--verify-turboshaft",
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
        name="minor_ms_randomized",
        build_profile="asan",
        flags=_COMMON
        + (
            "--jit-fuzzing",
            "--minor-ms",
            "--stress-compaction",
            "--stress-marking=100",
            "--stress-scavenge=100",
            "--stress-scavenger-conservative-object-pinning-random",
            "--stress-concurrent-allocation",
        ),
        markers=(
            b"arraybuffer",
            b"sharedarraybuffer",
            b"weakref",
            b"finalizationregistry",
            b"gc(",
        ),
        seed_variants=2,
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
        name="wasm_staging_checks",
        build_profile="asan",
        flags=_COMMON
        + (
            "--jit-fuzzing",
            "--wasm-staging",
            "--wasm-wasmfx",
            "--wasm-fp16",
            "--wasm-shared",
            "--wasm-stringref",
            "--wasm-wide-arithmetic",
            "--wasm-memory-control",
            "--wasm-sync-tier-up",
            "--wasm-assert-types",
            "--stress-wasm-code-gc",
            "--stress-wasm-memory-moving",
            "--wasm-inlining-ignore-call-counts",
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
