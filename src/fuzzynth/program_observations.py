"""Compact, deterministic observations about generated d8 programs."""

from __future__ import annotations

import re


_EXPORT_CALL = re.compile(
    r"\.exports(?:\.[A-Za-z_$][\w$]*|\[['\"][^'\"]+['\"]\])\s*\("
)
_EXPORT_BINDING = re.compile(
    r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*[^;\n]+"
    r"\.exports(?:\.[A-Za-z_$][\w$]*|\[['\"][^'\"]+['\"]\])"
)
_PERCENT_INTRINSIC = re.compile(r"%[A-Za-z_$][\w$]*\s*\(")
_OPTIMIZED = re.compile(
    r"(?:completed optimizing|completed compiling)", re.IGNORECASE
)
_DEOPT = re.compile(r"\[bailout \([^\n]*?reason: ([^):\n]+)", re.IGNORECASE)
_WASM_COMPILED_FUNCTION = re.compile(
    r"Compiled function .*? using ([A-Za-z0-9_+-]+)", re.IGNORECASE
)
_WASM_COMPILED_WRAPPER = re.compile(r"Compiled WasmToJS wrapper", re.IGNORECASE)


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _base_features(program: str) -> dict[str, bool]:
    return {
        "array_buffer": bool(re.search(r"(?<!Shared)\bArrayBuffer\b", program)),
        "gc": bool(re.search(r"(?<![%\w$])gc\s*\(", program)),
        "jit_intrinsic": bool(
            re.search(
                r"%(?:PrepareFunctionForOptimization|OptimizeFunctionOnNextCall|"
                r"OptimizeMaglevOnNextCall|DeoptimizeFunction|NeverOptimizeFunction)\b",
                program,
            )
        ),
        "proxy": bool(re.search(r"\b(?:new\s+Proxy|Proxy\.revocable)\s*\(", program)),
        "typed_array": bool(
            re.search(
                r"\b(?:BigInt64|BigUint64|Float32|Float64|Int8|Int16|Int32|"
                r"Uint8|Uint8Clamped|Uint16|Uint32)Array\s*\(",
                program,
            )
        ),
    }


def _calls_wasm_export(program: str) -> bool:
    if _EXPORT_CALL.search(program):
        return True
    return any(
        re.search(rf"\b{re.escape(binding)}\s*\(", program)
        for binding in _EXPORT_BINDING.findall(program)
    )


def observe_program(
    *,
    prompt_variant: str,
    program: bytes,
    outcome: str,
    stdout: bytes,
    stderr: bytes,
) -> dict[str, object]:
    """Return bounded factual signals suitable for iterative model feedback."""

    source = _decode(program)
    process_output = _decode(stdout + b"\n" + stderr)
    features = _base_features(source)
    observation: dict[str, object] = {
        "static_features": features,
    }

    optimized = len(_OPTIMIZED.findall(process_output))
    deopt_reasons = _DEOPT.findall(process_output)
    if optimized or deopt_reasons or "trace-opt" in prompt_variant:
        observation["jit_trace"] = {
            "completed_compilations": optimized,
            "deoptimizations": len(deopt_reasons),
            "deopt_reasons": [reason[:160] for reason in deopt_reasons[:4]],
        }

    if prompt_variant == "wasm_boundary_v1" or prompt_variant.startswith(
        "wasm_builder_"
    ):
        builder_assisted = prompt_variant.startswith("wasm_builder_")
        wasm_tiers = [tier.lower() for tier in _WASM_COMPILED_FUNCTION.findall(process_output)]
        wasm = {
            "constructs_module": bool(
                re.search(
                    r"(?:new\s+WebAssembly\.Module|WebAssembly\.instantiate)\s*\(",
                    source,
                )
            ) or (builder_assisted and ".instantiate(" in source),
            "constructs_instance": bool(
                re.search(
                    r"(?:new\s+WebAssembly\.Instance|WebAssembly\.instantiate)\s*\(",
                    source,
                )
            ) or (builder_assisted and ".instantiate(" in source),
            "calls_export": _calls_wasm_export(source),
            "loads_official_builder": (
                "load('/input/wasm-module-builder.js')" in source
                or 'load("/input/wasm-module-builder.js")' in source
            ),
            "uses_module_builder": "new WasmModuleBuilder" in source,
            "uses_percent_intrinsic": bool(_PERCENT_INTRINSIC.search(source)),
            "imports_js_function": ".addImport(" in source,
            "declares_memory": bool(
                re.search(r"\.(?:addMemory|addImportedMemory)\s*\(", source)
            ),
            "grows_memory": bool(
                re.search(r"(?:kExprMemoryGrow|\.grow\s*\()", source)
            ),
            "uses_table_or_indirect_call": bool(
                re.search(r"\.(?:addTable|addActiveElementSegment)\s*\(", source)
                or "kExprCallIndirect" in source
            ),
            "uses_reference_or_gc_types": bool(
                re.search(
                    r"(?:\.addStruct\s*\(|\.addArray\s*\(|kGCPrefix|"
                    r"wasmRef(?:Null)?Type)",
                    source,
                )
            ),
            "uses_simd": bool(
                re.search(
                    r"(?:kSimdPrefix|kExprS128|kExpr[IF](?:8x16|16x8|32x4|64x2))",
                    source,
                )
            ),
            "uses_wasm_exceptions": bool(
                re.search(r"(?:\.addTag\s*\(|kExprTry|kExprThrow|kExprCatch)", source)
            ),
        }
        observation["subsystem"] = "wasm"
        observation["subsystem_features"] = wasm
        observation["prompt_adherent"] = (
            wasm["constructs_module"]
            and wasm["constructs_instance"]
            and wasm["calls_export"]
            and not wasm["uses_percent_intrinsic"]
            and (
                not builder_assisted
                or (
                    wasm["loads_official_builder"]
                    and wasm["uses_module_builder"]
                )
            )
        )
        observation["runtime_path_completed"] = bool(
            observation["prompt_adherent"] and outcome == "ok"
        )
        if wasm_tiers or _WASM_COMPILED_WRAPPER.search(process_output):
            observation["wasm_trace"] = {
                "compiled_functions": len(wasm_tiers),
                "tiers": {
                    tier: wasm_tiers.count(tier) for tier in sorted(set(wasm_tiers))
                },
                "compiled_wasm_to_js_wrappers": len(
                    _WASM_COMPILED_WRAPPER.findall(process_output)
                ),
            }
        if builder_assisted and re.search(
            r"(?:Invalid prefixed opcode|invalid simd opcode)",
            process_output,
            re.IGNORECASE,
        ):
            observation["corrective_hint"] = {
                "code": "wasm_prefixed_opcode_not_leb_encoded",
                "guidance": (
                    "A prefixed SIMD opcode was encoded as a raw byte sequence. "
                    "Emit every SIMD instruction with ...SimdInstr(kExpr...) "
                    "and every GC instruction with ...GCInstr(kExpr...), then "
                    "instantiate and call the export again."
                ),
            }
        elif "CompileError: WebAssembly.Module()" in process_output:
            observation["corrective_hint"] = {
                "code": "wasm_binary_rejected_before_compiled_execution",
                "guidance": (
                    "The binary layout was invalid. Correct all section lengths, "
                    "vector counts, type/function indices, and body sizes; the next "
                    "program must instantiate and call an export successfully."
                ),
            }
        elif builder_assisted and re.search(
            r"ReferenceError: k(?:Expr|Wasm|Sig)[A-Za-z0-9_]+ is not defined",
            process_output,
        ):
            observation["corrective_hint"] = {
                "code": "unknown_wasm_builder_constant",
                "guidance": (
                    "A guessed WasmModuleBuilder constant does not exist. Reuse only "
                    "a constant whose exact spelling appeared in a known-valid prior "
                    "program or the supplied corpus; keep the next body simple."
                ),
            }
        elif builder_assisted and "invalid body (entries must be 8 bit numbers)" in process_output:
            observation["corrective_hint"] = {
                "code": "unencoded_wasm_immediate",
                "guidance": (
                    "A raw numeric immediate exceeded one byte. Encode constants with "
                    "helpers such as ...wasmI32Const(value) instead of placing the "
                    "full value directly in addBody()."
                ),
            }
        elif builder_assisted and (
            "exportMemoryAs is not a function" in process_output
            or "builder.exportMemory is not a function" in process_output
            or "undefined (reading 'buffer')" in process_output
        ):
            observation["corrective_hint"] = {
                "code": "wasm_memory_export_contract",
                "guidance": (
                    "Declare memory first, then call builder.exportMemoryAs('memory') "
                    "as a separate builder method before instantiate(); only then read "
                    "instance.exports.memory.buffer."
                ),
            }
        elif outcome != "ok" and "RuntimeError:" in process_output:
            observation["corrective_hint"] = {
                "code": "uncaught_wasm_runtime_trap",
                "guidance": (
                    "An uncaught Wasm trap stopped the stress path. Keep any expected "
                    "trap local and continue into a valid compiled export path."
                ),
            }

    elif prompt_variant in {"concurrency_v1", "concurrency_message_v2"}:
        concurrency = {
            "allocates_shared_array_buffer": "SharedArrayBuffer" in source,
            "uses_atomics": "Atomics." in source,
            "uses_worker": bool(re.search(r"\bnew\s+Worker\s*\(", source)),
            "uses_wait_or_spin": bool(
                re.search(r"Atomics\.wait(?:Async)?\s*\(", source)
                or re.search(r"\bwhile\s*\([^)]*Atomics\.", source)
            ),
            "uses_percent_intrinsic": bool(_PERCENT_INTRINSIC.search(source)),
            "atomics_operations": sorted(
                set(re.findall(r"Atomics\.([A-Za-z_$][\w$]*)\s*\(", source))
            )[:12],
            "shared_view_types": sorted(
                set(
                    re.findall(
                        r"new\s+((?:Big)?(?:Int|Uint)(?:8|16|32|64)?Array)\s*\(",
                        source,
                    )
                )
            )[:8],
            "worker_count": len(re.findall(r"\bnew\s+Worker\s*\(", source)),
            "growable_shared_buffer": bool(
                "maxByteLength" in source and re.search(r"\.grow\s*\(", source)
            ),
            "coercion_side_effect": bool(
                re.search(r"(?:Symbol\.toPrimitive|valueOf\s*\(|toString\s*\()", source)
            ),
        }
        observation["subsystem"] = "concurrency"
        observation["subsystem_features"] = concurrency
        observation["prompt_adherent"] = (
            concurrency["allocates_shared_array_buffer"]
            and concurrency["uses_atomics"]
            and not concurrency["uses_percent_intrinsic"]
            and (
                prompt_variant != "concurrency_message_v2"
                or (
                    concurrency["uses_worker"]
                    and not concurrency["uses_wait_or_spin"]
                )
            )
        )
        observation["runtime_path_completed"] = bool(
            observation["prompt_adherent"] and outcome == "ok"
        )
        if "is not an int32 or BigInt64 typed array" in process_output:
            observation["corrective_hint"] = {
                "code": "atomics_wait_notify_wrong_view_type",
                "guidance": (
                    "Atomics.wait/notify require an Int32Array or BigInt64Array view; "
                    "use that same valid control view for the next bounded protocol."
                ),
            }
        elif "ReferenceError: postMessage is not defined" in process_output:
            observation["corrective_hint"] = {
                "code": "worker_message_wrong_realm",
                "guidance": (
                    "Bare postMessage/getMessage are available inside the worker "
                    "source. In the main d8 realm call worker.postMessage(value) "
                    "and worker.getMessage() on the Worker object."
                ),
            }
        elif outcome == "timeout":
            observation["corrective_hint"] = {
                "code": "worker_protocol_did_not_terminate",
                "guidance": (
                    "The message protocol deadlocked. Keep worker state inside the "
                    "worker, make every wait satisfiable by the other side, send one "
                    "completion message on every path, and terminate the worker."
                ),
            }

    elif prompt_variant in {"language_v1", "buffers_v1", "regexp_v1"}:
        forbidden = {
            "percent_intrinsic": bool(_PERCENT_INTRINSIC.search(source)),
            "shared_memory_or_worker": bool(
                re.search(r"\b(?:SharedArrayBuffer|Worker)\b", source)
            ),
            "wasm": "WebAssembly" in source,
        }
        if prompt_variant == "buffers_v1":
            buffer_features = {
                **forbidden,
                "resizable_buffer": (
                    "maxByteLength" in source and bool(re.search(r"\.resize\s*\(", source))
                ),
                "view_constructions": len(
                    re.findall(
                        r"\b(?:DataView|BigInt64Array|BigUint64Array|"
                        r"Float(?:32|64)Array|Int(?:8|16|32)Array|"
                        r"Uint(?:8|8Clamped|16|32)Array)\s*\(",
                        source,
                    )
                ),
                "view_types": sorted(
                    set(
                        re.findall(
                            r"\b(DataView|BigInt64Array|BigUint64Array|"
                            r"Float(?:32|64)Array|Int(?:8|16|32)Array|"
                            r"Uint(?:8|8Clamped|16|32)Array)\s*\(",
                            source,
                        )
                    )
                )[:12],
                "uses_proxy": bool(re.search(r"\bnew\s+Proxy\s*\(", source)),
                "uses_coercion_side_effect": bool(
                    re.search(
                        r"(?:Symbol\.toPrimitive|valueOf\s*\(|toString\s*\()",
                        source,
                    )
                ),
                "uses_transfer": bool(
                    re.search(r"\.transfer(?:ToFixedLength)?\s*\(", source)
                ),
                "uses_bigint_view": bool(
                    re.search(r"\bBig(?:Int64|Uint64)Array\s*\(", source)
                ),
            }
            observation["subsystem"] = "buffers"
            observation["subsystem_features"] = buffer_features
            observation["prompt_adherent"] = (
                not any(forbidden.values())
                and buffer_features["resizable_buffer"]
                and buffer_features["view_constructions"] >= 2
            )
        elif prompt_variant == "regexp_v1":
            regexp_features = {
                **forbidden,
                "constructs_or_literals": bool(
                    re.search(r"\bRegExp\s*\(", source)
                    or re.search(r"/(?:[^/\\\n]|\\.)+/[dgimsuvy]*", source)
                ),
                "executes_regexp_protocol": bool(
                    re.search(
                        r"\.(?:exec|test|match|matchAll|replace|replaceAll|search|split)\s*\(",
                        source,
                    )
                ),
                "protocols": sorted(
                    set(
                        re.findall(
                            r"\.(exec|test|match|matchAll|replace|replaceAll|search|split)\s*\(",
                            source,
                        )
                    )
                ),
                "uses_subclass": bool(
                    re.search(r"class\s+[A-Za-z_$][\w$]*\s+extends\s+RegExp", source)
                ),
                "uses_symbol_protocol": bool(
                    re.search(r"Symbol\.(?:match|matchAll|replace|search|split)", source)
                ),
                "uses_replacement_callback": bool(
                    re.search(
                        r"\.replace(?:All)?\s*\([^,]+,\s*(?:function|(?:async\s*)?\(?[\w\s,]*\)?\s*=>)",
                        source,
                    )
                ),
            }
            observation["subsystem"] = "regexp"
            observation["subsystem_features"] = regexp_features
            observation["prompt_adherent"] = (
                not any(forbidden.values())
                and regexp_features["constructs_or_literals"]
                and regexp_features["executes_regexp_protocol"]
            )
        else:
            observation["subsystem"] = "language"
            observation["subsystem_features"] = forbidden
            observation["prompt_adherent"] = not any(forbidden.values())
        observation["runtime_path_completed"] = bool(
            observation["prompt_adherent"] and outcome == "ok"
        )
        if prompt_variant == "buffers_v1" and outcome != "ok":
            if "start offset" in process_output and "multiple of" in process_output:
                observation["corrective_hint"] = {
                    "code": "misaligned_typed_array_offset",
                    "guidance": (
                        "The typed-array byteOffset was not aligned to its element "
                        "width. Use an aligned initial offset, then test resize effects."
                    ),
                }
            elif "out-of-bounds ArrayBuffer" in process_output or "offset is out of bounds" in process_output:
                observation["corrective_hint"] = {
                    "code": "uncaught_expected_buffer_boundary",
                    "guidance": (
                        "An expected resized-view RangeError/TypeError escaped. Catch "
                        "that single access locally and continue to a valid post-resize path."
                    ),
                }
        elif (
            prompt_variant == "regexp_v1"
            and outcome != "ok"
            and "re.replace is not a function" in process_output
        ):
            observation["corrective_hint"] = {
                "code": "regexp_replace_wrong_receiver",
                "guidance": (
                    "replace/replaceAll are String methods: call subject.replace(re, "
                    "replacement), or invoke re[Symbol.replace](subject, replacement)."
                ),
            }

    return observation
