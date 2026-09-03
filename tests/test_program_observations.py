from __future__ import annotations

import unittest

from fuzzynth.program_observations import observe_program


class ProgramObservationTests(unittest.TestCase):
    def test_semantic_profile_ignores_local_names_but_tracks_mechanisms(self) -> None:
        first = observe_program(
            prompt_variant="language_v1",
            program=(
                b"function alpha(x){return proxy.valueOf()+x;}"
                b"const proxy=new Proxy({valueOf(){return 1;}},{}); alpha(2);"
            ),
            outcome="ok",
            stdout=b"",
            stderr=b"",
        )["semantic_profile"]
        renamed = observe_program(
            prompt_variant="language_v1",
            program=(
                b"function beta(y){return holder.valueOf()+y;}"
                b"const holder=new Proxy({valueOf(){return 1;}},{}); beta(2);"
            ),
            outcome="ok",
            stdout=b"",
            stderr=b"",
        )["semantic_profile"]

        self.assertEqual(first["signature"], renamed["signature"])
        self.assertIn("proxy_traps", first["mechanisms"])
        self.assertIn("coercion_hooks", first["mechanisms"])
        self.assertIn("valueOf", first["operations"])

    def test_semantic_profile_changes_for_a_different_engine_interaction(self) -> None:
        proxy = observe_program(
            prompt_variant="language_v1",
            program=b"new Proxy({}, {get(){return 1;}}).x;",
            outcome="ok",
            stdout=b"",
            stderr=b"",
        )["semantic_profile"]
        buffer = observe_program(
            prompt_variant="language_v1",
            program=(
                b"const b=new ArrayBuffer(8,{maxByteLength:16});"
                b"new Uint8Array(b); b.resize(12);"
            ),
            outcome="ok",
            stdout=b"",
            stderr=b"",
        )["semantic_profile"]

        self.assertNotEqual(proxy["signature"], buffer["signature"])

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

    def test_builder_simd_encoding_failure_has_exact_hint(self) -> None:
        observation = observe_program(
            prompt_variant="wasm_builder_advanced_v1",
            program=(
                b"load('/input/wasm-module-builder.js');"
                b"const b=new WasmModuleBuilder();"
                b"b.addFunction('f',kSig_i_i).addBody([kSimdPrefix,"
                b"kExprI32x4Add]).exportFunc();"
            ),
            outcome="nonzero_exit",
            stdout=(
                b"CompileError: WebAssembly.Module(): Compiling function #0 "
                b"failed: Invalid prefixed opcode 458414"
            ),
            stderr=b"",
        )

        self.assertEqual(
            observation["corrective_hint"]["code"],
            "wasm_prefixed_opcode_not_leb_encoded",
        )

    def test_builder_invented_prefixed_helper_has_exact_hint(self) -> None:
        observation = observe_program(
            prompt_variant="wasm_builder_advanced_v1",
            program=(
                b"load('/input/wasm-module-builder.js');"
                b"const b=new WasmModuleBuilder();"
                b"b.addFunction('f',kSig_i_i).addBody(["
                b"...wasmSimdInstr(kExprI32x4Splat)]).exportFunc();"
            ),
            outcome="js_exception",
            stdout=b"ReferenceError: wasmSimdInstr is not defined",
            stderr=b"",
        )

        self.assertEqual(
            observation["corrective_hint"]["code"],
            "unknown_wasm_prefixed_instruction_helper",
        )

    def test_builder_lowercase_prefixed_helper_has_exact_hint(self) -> None:
        observation = observe_program(
            prompt_variant="wasm_builder_advanced_v1",
            program=b"simdInstr(kExprI32x4Splat)",
            outcome="js_exception",
            stdout=b"ReferenceError: simdInstr is not defined",
            stderr=b"",
        )

        self.assertEqual(
            observation["corrective_hint"]["code"],
            "unknown_wasm_prefixed_instruction_helper",
        )

    def test_builder_wrong_table_export_kind_has_exact_hint(self) -> None:
        observation = observe_program(
            prompt_variant="wasm_builder_advanced_v1",
            program=b"const tableObject=instance.exports.table; tableObject.set(0, f);",
            outcome="js_exception",
            stdout=b"TypeError: tableObject.set is not a function",
            stderr=b"",
        )

        self.assertEqual(
            observation["corrective_hint"]["code"], "wasm_table_export_kind"
        )

    def test_builder_unspread_simd_helper_has_exact_hint(self) -> None:
        observation = observe_program(
            prompt_variant="wasm_builder_advanced_v1",
            program=b"b.addBody([SimdInstr(kExprI32x4Splat)])",
            outcome="nonzero_exit",
            stdout=b"Error: invalid body (entries must be 8 bit numbers)",
            stderr=b"",
        )

        self.assertEqual(
            observation["corrective_hint"]["code"],
            "wasm_instruction_helper_not_spread",
        )

    def test_builder_missing_simd_lane_has_exact_hint(self) -> None:
        observation = observe_program(
            prompt_variant="wasm_builder_advanced_v1",
            program=b"...SimdInstr(kExprI32x4ExtractLane, 0)",
            outcome="nonzero_exit",
            stdout=(
                b"CompileError: WebAssembly.Module(): Compiling function #0 "
                b"failed: invalid lane index"
            ),
            stderr=b"",
        )

        self.assertEqual(
            observation["corrective_hint"]["code"],
            "wasm_simd_lane_immediate_missing",
        )

    def test_builder_direct_call_alias_has_exact_hint(self) -> None:
        observation = observe_program(
            prompt_variant="wasm_builder_advanced_v1",
            program=b"b.addBody([kExprCall, f])",
            outcome="js_exception",
            stdout=b"ReferenceError: kExprCall is not defined",
            stderr=b"",
        )

        self.assertEqual(
            observation["corrective_hint"]["code"],
            "wasm_direct_call_opcode_alias",
        )

    def test_builder_object_body_entry_has_exact_hint(self) -> None:
        observation = observe_program(
            prompt_variant="wasm_builder_advanced_v1",
            program=b"b.addBody([kExprCallFunction, f])",
            outcome="nonzero_exit",
            stdout=(
                b"Error: invalid body (entries must be 8 bit numbers): "
                b"16,[object Object]"
            ),
            stderr=b"",
        )

        self.assertEqual(
            observation["corrective_hint"]["code"],
            "wasm_builder_object_in_body",
        )

    def test_builder_call_indirect_stack_failure_has_exact_hint(self) -> None:
        observation = observe_program(
            prompt_variant="wasm_builder_advanced_v1",
            program=b"b.addBody([kExprCallIndirect, sig, table.index])",
            outcome="nonzero_exit",
            stdout=(
                b"CompileError: WebAssembly.Module(): not enough arguments on "
                b"the stack for call_indirect (need 1, got 0)"
            ),
            stderr=b"",
        )

        self.assertEqual(
            observation["corrective_hint"]["code"],
            "wasm_call_indirect_stack_or_immediates",
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

    def test_wasm_trace_and_builder_feature_families_are_compacted(self) -> None:
        observation = observe_program(
            prompt_variant="wasm_builder_v2",
            program=b"""
              load('/input/wasm-module-builder.js');
              const b = new WasmModuleBuilder();
              b.addMemory(1, 2);
              const imp = b.addImport('m', 'f', kSig_i_i);
              const i = b.instantiate({m: {f(x) { return x; }}});
              i.exports.run();
            """,
            outcome="ok",
            stdout=(
                b"Compiled function 0x1#0 using Liftoff, took 1 us\n"
                b"Compiled WasmToJS wrapper wasm-to-js-5-i, took 2 us\n"
                b"Compiled function 0x1#0 using TurboFan, took 3 us\n"
            ),
            stderr=b"",
        )

        features = observation["subsystem_features"]
        self.assertTrue(features["imports_js_function"])
        self.assertTrue(features["declares_memory"])
        self.assertEqual(observation["wasm_trace"]["compiled_functions"], 2)
        self.assertEqual(
            observation["wasm_trace"]["tiers"], {"liftoff": 1, "turbofan": 1}
        )
        self.assertEqual(
            observation["wasm_trace"]["compiled_wasm_to_js_wrappers"], 1
        )

    def test_advanced_builder_variant_uses_wasm_observation(self) -> None:
        observation = observe_program(
            prompt_variant="wasm_builder_advanced_v1",
            program=b"""
              load('/input/wasm-module-builder.js');
              const b = new WasmModuleBuilder();
              b.addStruct([]);
              const i = b.instantiate();
              i.exports.run();
            """,
            outcome="ok",
            stdout=b"",
            stderr=b"",
        )

        self.assertTrue(
            observation["subsystem_features"]["uses_reference_or_gc_types"]
        )
        self.assertTrue(observation["prompt_adherent"])

    def test_staging_builder_requires_and_reports_a_proposal_family(self) -> None:
        observation = observe_program(
            prompt_variant="wasm_staging_v1",
            program=b"""
              load('/input/wasm-module-builder.js');
              const b = new WasmModuleBuilder();
              b.addMemory64(1, 2);
              const i = b.instantiate();
              i.exports.run();
            """,
            outcome="ok",
            stdout=b"",
            stderr=b"",
        )

        self.assertTrue(observation["subsystem_features"]["uses_memory64"])
        self.assertTrue(observation["prompt_adherent"])
        self.assertIn(
            "wasm_memory64", observation["semantic_profile"]["mechanisms"]
        )

    def test_staging_builder_harness_leak_has_exact_hint(self) -> None:
        observation = observe_program(
            prompt_variant="wasm_staging_v1",
            program=b"d8.file.execute('wasm-module-builder.js');",
            outcome="js_exception",
            stdout=b"ReferenceError: d8 is not defined",
            stderr=b"",
        )

        self.assertEqual(
            observation["corrective_hint"]["code"],
            "wasm_staging_harness_leak",
        )

    def test_staging_builder_tracks_descriptor_and_imported_string_families(self) -> None:
        observation = observe_program(
            prompt_variant="wasm_staging_v1",
            program=b"""
              load('/input/wasm-module-builder.js');
              const b = new WasmModuleBuilder();
              b.addStruct({descriptor: 1});
              b.addImport('wasm:js-string', 'cast', kSig_r_r);
              const i = b.instantiate({}, {builtins: ['js-string']});
              i.exports.run();
            """,
            outcome="ok",
            stdout=b"",
            stderr=b"",
        )

        features = observation["subsystem_features"]
        self.assertTrue(features["uses_custom_descriptors"])
        self.assertTrue(features["uses_imported_strings"])
        self.assertIn(
            "wasm_custom_descriptors",
            observation["semantic_profile"]["mechanisms"],
        )
        self.assertIn(
            "wasm_imported_strings",
            observation["semantic_profile"]["mechanisms"],
        )

    def test_builder_memory_export_failure_has_exact_contract_hint(self) -> None:
        observation = observe_program(
            prompt_variant="wasm_builder_v2",
            program=b"load('/input/wasm-module-builder.js'); new WasmModuleBuilder();",
            outcome="js_exception",
            stdout=b"TypeError: builder.exportMemory is not a function",
            stderr=b"",
        )

        self.assertEqual(
            observation["corrective_hint"]["code"],
            "wasm_memory_export_contract",
        )

    def test_message_concurrency_rejects_wait_even_after_exit_zero(self) -> None:
        observation = observe_program(
            prompt_variant="concurrency_message_v2",
            program=b"""
              const s = new SharedArrayBuffer(8);
              const v = new Int32Array(s);
              const w = new Worker('postMessage(1)', {type:'string'});
              Atomics.wait(v, 0, 0);
            """,
            outcome="ok",
            stdout=b"",
            stderr=b"",
        )

        self.assertFalse(observation["prompt_adherent"])

    def test_buffer_lane_requires_resize_and_two_views(self) -> None:
        observation = observe_program(
            prompt_variant="buffers_v1",
            program=b"""
              const b = new ArrayBuffer(8, {maxByteLength: 16});
              const a = new Uint8Array(b);
              const d = new DataView(b);
              b.resize(12);
            """,
            outcome="ok",
            stdout=b"",
            stderr=b"",
        )

        self.assertTrue(observation["prompt_adherent"])
        self.assertTrue(observation["runtime_path_completed"])
        self.assertEqual(
            observation["subsystem_features"]["view_types"],
            ["DataView", "Uint8Array"],
        )

    def test_regexp_lane_requires_construct_and_execution(self) -> None:
        observation = observe_program(
            prompt_variant="regexp_v1",
            program=b"const r = new RegExp('x', 'g'); r.exec('xx');",
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

    def test_concurrency_main_realm_message_misuse_has_hint(self) -> None:
        observation = observe_program(
            prompt_variant="concurrency_message_v2",
            program=(
                b"const s=new SharedArrayBuffer(8);"
                b"Atomics.add(new Int32Array(s),0,1);"
                b"const w=new Worker('postMessage(1)',{type:'string'});"
                b"postMessage(s);"
            ),
            outcome="js_exception",
            stdout=b"ReferenceError: postMessage is not defined",
            stderr=b"",
        )

        self.assertEqual(
            observation["corrective_hint"]["code"], "worker_message_wrong_realm"
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
