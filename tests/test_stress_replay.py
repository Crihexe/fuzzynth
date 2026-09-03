from __future__ import annotations

import unittest

from fuzzynth.stress_replay import (
    STRESS_PROFILES,
    StressProfile,
    applicable_profiles,
    worker_profile_for,
)


class StressReplayTests(unittest.TestCase):
    def test_only_msan_uses_the_slower_sanitizer_worker_limits(self) -> None:
        profiles = {profile.name: profile for profile in STRESS_PROFILES}

        self.assertEqual(worker_profile_for(profiles["uninitialized_memory"]), "sanitizer")
        self.assertEqual(worker_profile_for(profiles["undefined_behavior"]), "standard")

    def test_compiler_program_gets_specialized_jit_profiles(self) -> None:
        names = {
            profile.name
            for profile in applicable_profiles(b"function f(x) { return x + 1 }")
        }
        self.assertEqual(
            names,
            {
                "uninitialized_memory",
                "undefined_behavior",
                "compiler_verify",
                "compiler_concurrent",
                "forced_deopt",
                "maglev_assertions",
                "compilation_gc_race",
                "maglev_future_checks",
                "turbolev_future_checks",
            },
        )

    def test_feature_profiles_are_added_without_unrelated_profiles(self) -> None:
        names = {
            profile.name
            for profile in applicable_profiles(
                b"new WebAssembly.Memory({initial:1}); gc(); /x/.test('x')"
            )
        }
        self.assertEqual(
            names,
            {
                "uninitialized_memory",
                "undefined_behavior",
                "memory_gc",
                "minor_ms_randomized",
                "wasm_aggressive_inlining",
                "wasm_staging_checks",
                "wasm_tiering_memory",
                "wasm_stack_switching",
                "heap_verification",
                "experimental_regexp",
            },
        )

    def test_regexp_operations_select_experimental_engine(self) -> None:
        names = {
            profile.name
            for profile in applicable_profiles(b"'abc'.replace(/a/, 'z')")
        }
        self.assertEqual(
            names,
            {
                "uninitialized_memory",
                "undefined_behavior",
                "experimental_regexp",
            },
        )

    def test_shared_memory_selects_thread_safety_oracle(self) -> None:
        names = {
            profile.name
            for profile in applicable_profiles(
                b"const s = new SharedArrayBuffer(8); new Worker('');"
            )
        }

        self.assertEqual(
            names,
            {
                "uninitialized_memory",
                "thread_safety",
                "undefined_behavior",
                "memory_gc",
                "minor_ms_randomized",
                "heap_verification",
            },
        )

    def test_builder_wasm_and_exec_are_routed_to_specialized_profiles(self) -> None:
        builder_names = {
            profile.name
            for profile in applicable_profiles(
                b"load('/input/wasm-module-builder.js'); new WasmModuleBuilder();"
            )
        }
        regexp_names = {
            profile.name for profile in applicable_profiles(b"pattern.exec(text)")
        }

        self.assertIn("wasm_tiering_memory", builder_names)
        self.assertIn("wasm_aggressive_inlining", builder_names)
        self.assertIn("wasm_stack_switching", builder_names)
        self.assertIn("wasm_staging_checks", builder_names)
        self.assertIn("undefined_behavior", builder_names)
        self.assertIn("uninitialized_memory", builder_names)
        self.assertEqual(
            regexp_names,
            {
                "uninitialized_memory",
                "undefined_behavior",
                "experimental_regexp",
            },
        )

    def test_seed_variants_are_stable_distinct_and_recorded_in_flags(self) -> None:
        profile = StressProfile(
            name="seeded",
            build_profile="asan",
            flags=("--fuzzing", "--stress-marking=100"),
            markers=(b"",),
            seed_variants=3,
        )

        first = profile.flag_variants("a" * 64)
        second = profile.flag_variants("a" * 64)

        self.assertEqual(first, second)
        self.assertEqual(len(set(first)), 3)
        for flags in first:
            self.assertTrue(any(flag.startswith("--random-seed=") for flag in flags))
            self.assertTrue(
                any(flag.startswith("--fuzzer-random-seed=") for flag in flags)
            )


if __name__ == "__main__":
    unittest.main()
