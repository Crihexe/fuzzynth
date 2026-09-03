from __future__ import annotations

import unittest

from fuzzynth.program_observations import observe_program


class ProgramObservationTests(unittest.TestCase):
    def test_wasm_success_requires_static_boundary_and_exit_zero(self) -> None:
        observation = observe_program(
            prompt_variant="wasm_boundary_v1",
            program=b"""
              const m = new WebAssembly.Module(bytes);
              const i = new WebAssembly.Instance(m);
              i.exports.run();
            """,
            outcome="ok",
            stdout=b"",
            stderr=b"",
        )

        self.assertTrue(observation["prompt_adherent"])
        self.assertTrue(observation["runtime_path_completed"])

    def test_wasm_compile_failure_has_corrective_hint(self) -> None:
        observation = observe_program(
            prompt_variant="wasm_boundary_v1",
            program=b"new WebAssembly.Module(bytes)",
            outcome="nonzero_exit",
            stdout=b"CompileError: WebAssembly.Module(): type kind @+20",
            stderr=b"",
        )

        self.assertFalse(observation["runtime_path_completed"])
        self.assertEqual(
            observation["corrective_hint"]["code"],
            "wasm_binary_rejected_before_compiled_execution",
        )

    def test_wasm_export_alias_call_is_recognized(self) -> None:
        observation = observe_program(
            prompt_variant="wasm_boundary_v1",
            program=b"""
              const m = new WebAssembly.Module(bytes);
              const i = new WebAssembly.Instance(m);
              const run = i.exports.run;
              run();
            """,
            outcome="ok",
            stdout=b"",
            stderr=b"",
        )

        self.assertTrue(observation["subsystem_features"]["calls_export"])
        self.assertTrue(observation["runtime_path_completed"])

    def test_builder_assisted_wasm_requires_pinned_load_and_builder(self) -> None:
        observation = observe_program(
            prompt_variant="wasm_builder_v1",
            program=b"""
              load('/input/wasm-module-builder.js');
              const b = new WasmModuleBuilder();
              const i = b.instantiate();
              i.exports.run();
            """,
            outcome="ok",
            stdout=b"",
            stderr=b"",
        )

        self.assertTrue(observation["prompt_adherent"])
        self.assertTrue(observation["runtime_path_completed"])

    def test_concurrency_wrong_notify_view_has_actionable_hint(self) -> None:
        observation = observe_program(
            prompt_variant="concurrency_v1",
            program=(
                b"const s = new SharedArrayBuffer(8);"
                b"Atomics.notify(new Uint32Array(s), 0);"
            ),
            outcome="js_exception",
            stdout=b"TypeError: view is not an int32 or BigInt64 typed array",
            stderr=b"",
        )

        self.assertTrue(observation["prompt_adherent"])
        self.assertEqual(
            observation["corrective_hint"]["code"],
            "atomics_wait_notify_wrong_view_type",
        )

    def test_language_forbidden_features_are_reported(self) -> None:
        observation = observe_program(
            prompt_variant="language_v1",
            program=b"new SharedArrayBuffer(8);",
            outcome="ok",
            stdout=b"",
            stderr=b"",
        )

        self.assertFalse(observation["prompt_adherent"])
        self.assertTrue(
            observation["subsystem_features"]["shared_memory_or_worker"]
        )

    def test_modulo_is_not_mistaken_for_a_percent_intrinsic(self) -> None:
        observation = observe_program(
            prompt_variant="language_v1",
            program=b"for (let i = 0; i < 4; i++) print(i % 2);",
            outcome="ok",
            stdout=b"",
            stderr=b"",
        )

        self.assertTrue(observation["prompt_adherent"])

    def test_jit_trace_is_compacted(self) -> None:
        observation = observe_program(
            prompt_variant="explicit_v3",
            program=b"function f(){}; %PrepareFunctionForOptimization(f);",
            outcome="ok",
            stdout=(
                b"[completed optimizing 0x123 <JSFunction f>]\n"
                b"[bailout (kind: deopt-eager, reason: wrong map): begin]\n"
            ),
            stderr=b"",
        )

        self.assertEqual(observation["jit_trace"]["completed_compilations"], 1)
        self.assertEqual(observation["jit_trace"]["deoptimizations"], 1)
        self.assertEqual(observation["jit_trace"]["deopt_reasons"], ["wrong map"])


if __name__ == "__main__":
    unittest.main()
