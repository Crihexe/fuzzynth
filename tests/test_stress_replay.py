from __future__ import annotations

import unittest

from fuzzynth.stress_replay import STRESS_PROFILES, applicable_profiles, worker_profile_for


class StressReplayTests(unittest.TestCase):
    def test_only_msan_uses_the_slower_sanitizer_worker_limits(self) -> None:
        profiles = {profile.name: profile for profile in STRESS_PROFILES}

        self.assertEqual(worker_profile_for(profiles["uninitialized_memory"]), "sanitizer")
        self.assertEqual(worker_profile_for(profiles["undefined_behavior"]), "standard")

    def test_compiler_program_gets_three_jit_profiles(self) -> None:
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
                "wasm_aggressive_inlining",
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


if __name__ == "__main__":
    unittest.main()
