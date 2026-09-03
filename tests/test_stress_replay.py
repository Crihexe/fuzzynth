from __future__ import annotations

import unittest

from fuzzynth.stress_replay import applicable_profiles


class StressReplayTests(unittest.TestCase):
    def test_compiler_program_gets_three_jit_profiles(self) -> None:
        names = {
            profile.name
            for profile in applicable_profiles(b"function f(x) { return x + 1 }")
        }
        self.assertEqual(
            names,
            {"compiler_verify", "compiler_concurrent", "forced_deopt"},
        )

    def test_feature_profiles_are_added_without_unrelated_profiles(self) -> None:
        names = {
            profile.name
            for profile in applicable_profiles(
                b"new WebAssembly.Memory({initial:1}); gc(); /x/.test('x')"
            )
        }
        self.assertEqual(names, {"memory_gc", "wasm_tiering_memory"})

    def test_regexp_operations_select_experimental_engine(self) -> None:
        names = {
            profile.name
            for profile in applicable_profiles(b"'abc'.replace(/a/, 'z')")
        }
        self.assertEqual(names, {"experimental_regexp"})


if __name__ == "__main__":
    unittest.main()
