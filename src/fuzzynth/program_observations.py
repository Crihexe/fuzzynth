"""Compact, deterministic observations about generated d8 programs."""

from __future__ import annotations

import hashlib
import json
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

_MECHANISM_PATTERNS = (
    ("accessor_side_effects", r"\b(?:get|set)\s+[A-Za-z_$[]|__defineGetter__"),
    ("async_promises", r"\b(?:async|await|Promise)\b"),
    ("bigint", r"\bBigInt\b|\d+n\b|Big(?:Int64|Uint64)Array"),
    ("classes_private_fields", r"\bclass\b|#[A-Za-z_$][\w$]*"),
    ("coercion_hooks", r"Symbol\.toPrimitive|\b(?:valueOf|toString)\s*\("),
    ("collections", r"\b(?:Map|Set|WeakMap|WeakSet)\b"),
    ("dynamic_code", r"\b(?:eval|Function)\s*\("),
    ("generators_iterators", r"\bfunction\s*\*|Symbol\.iterator|\.next\s*\("),
    ("optimization_intrinsics", r"%(?:Prepare|Optimize|Deoptimize|NeverOptimize)"),
    ("prototypes_shapes", r"__proto__|Object\.(?:setPrototypeOf|definePropert|create)|delete\s+"),
    ("proxy_traps", r"\b(?:new\s+Proxy|Proxy\.revocable)\s*\("),
    ("regexp", r"\bRegExp\b|/(?:[^/\\\n]|\\.)+/[dgimsuvy]*"),
    ("resizable_transferable_buffers", r"maxByteLength|\.resize\s*\(|\.transfer(?:ToFixedLength)?\s*\("),
    ("shared_memory_atomics", r"SharedArrayBuffer|Atomics\."),
    ("species", r"Symbol\.species"),
    ("typed_views", r"\b(?:DataView|(?:Big)?(?:Int|Uint|Float)\w*Array)\b"),
    ("wasm", r"WebAssembly|WasmModuleBuilder"),
    ("wasm_custom_descriptors", r"(?:descriptor|describes)\s*:"),
    ("wasm_fp16", r"\bF16x8\b|kExprF16x8"),
    ("wasm_imported_strings", r"wasm:(?:js-string|text-(?:decoder|encoder))"),
    ("wasm_memory64", r"\.addMemory64\s*\(|\bmemory64\b"),
    ("wasm_shared_types", r"\.addSharedType\s*\(|kWasmShared"),
    ("wasm_stack_switching", r"\.addCont\s*\(|kExpr(?:ContNew|Resume|Switch)"),
    ("wasm_stringref", r"kWasmStringRef|kExprString"),
    ("wasm_wide_arithmetic", r"kExprI64Add128|wide[-_ ]arithmetic"),
    ("weak_lifetimes", r"\b(?:WeakRef|FinalizationRegistry)\b"),
    ("workers", r"\bnew\s+Worker\s*\("),
)


def _count_bucket(count: int) -> str:
    if count < 3:
        return str(count)
    if count < 5:
        return "3-4"
    if count < 9:
        return "5-8"
    return "9+"


def semantic_profile(source: str) -> dict[str, object]:
    mechanisms = [
        name
        for name, pattern in _MECHANISM_PATTERNS
        if re.search(pattern, source)
    ]
    operations = sorted(
        set(
            re.findall(r"\.\s*([A-Za-z_$][\w$]*)\s*\(", source)
            + re.findall(r"%([A-Za-z_$][\w$]*)\s*\(", source)
        )
    )[:24]
    constructors = sorted(
        set(re.findall(r"\bnew\s+([A-Za-z_$][\w$]*)", source))
    )[:16]
    shape = {
        "branches": _count_bucket(
            len(re.findall(r"\b(?:if|switch|case|catch)\b|\?", source))
        ),
        "functions": _count_bucket(
            len(re.findall(r"\bfunction\b|(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>", source))
        ),
        "loops": _count_bucket(len(re.findall(r"\b(?:for|while|do)\b", source))),
    }
    signature_input = {
        "constructors": constructors,
        "mechanisms": mechanisms,
        "operations": operations,
        "shape": shape,
    }
    signature = hashlib.sha256(
        json.dumps(
            signature_input,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    return {**signature_input, "signature": signature}


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
        "semantic_profile": semantic_profile(source),
    }

    optimized = len(_OPTIMIZED.findall(process_output))
    deopt_reasons = _DEOPT.findall(process_output)
    if optimized or deopt_reasons or "trace-opt" in prompt_variant:
        observation["jit_trace"] = {
            "completed_compilations": optimized,
            "deoptimizations": len(deopt_reasons),
            "deopt_reasons": [reason[:160] for reason in deopt_reasons[:4]],
        }

    if (
        prompt_variant == "wasm_boundary_v1"
        or prompt_variant == "wasm_staging_v1"
        or prompt_variant.startswith("wasm_builder_")
    ):
        builder_assisted = (
            prompt_variant.startswith("wasm_builder_")
            or prompt_variant == "wasm_staging_v1"
        )
        staging_variant = prompt_variant == "wasm_staging_v1"
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
            "uses_wasmfx": bool(
                re.search(r"(?:\.addCont\s*\(|kExpr(?:ContNew|Resume|Switch))", source)
            ),
            "uses_stringref": bool(
                re.search(r"(?:kWasmStringRef|kExprString)", source)
            ),
            "uses_fp16": bool(re.search(r"(?:\bF16x8\b|kExprF16x8)", source)),
            "uses_shared_types": bool(
                re.search(r"(?:\.addSharedType\s*\(|kWasmShared)", source)
            ),
            "uses_memory64": bool(
                re.search(r"(?:\.addMemory64\s*\(|\bmemory64\b)", source)
            ),
            "uses_wide_arithmetic": bool(
                re.search(r"(?:kExprI64Add128|wide[-_ ]arithmetic)", source)
            ),
            "uses_custom_descriptors": bool(
                re.search(r"(?:descriptor|describes)\s*:", source)
            ),
            "uses_imported_strings": bool(
                re.search(
                    r"wasm:(?:js-string|text-(?:decoder|encoder))", source
                )
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
            and (
                not staging_variant
                or any(
                    wasm[name]
                    for name in (
                        "uses_wasmfx",
                        "uses_stringref",
                        "uses_fp16",
                        "uses_shared_types",
                        "uses_memory64",
                        "uses_wide_arithmetic",
                        "uses_custom_descriptors",
                        "uses_imported_strings",
                    )
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
        if staging_variant and re.search(
            r"ReferenceError: (?:d8|assert[A-Za-z0-9_]*) is not defined",
            process_output,
        ):
            observation["corrective_hint"] = {
                "code": "wasm_staging_harness_leak",
                "guidance": (
                    "A historical test-harness call leaked into the program. "
                    "Use only load('/input/wasm-module-builder.js'), ordinary "
                    "JavaScript checks, and direct exported-function calls; do "
                    "not emit d8.file or assert* helpers."
                ),
            }
        elif builder_assisted and re.search(
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
        elif builder_assisted and "invalid lane index" in process_output:
            observation["corrective_hint"] = {
                "code": "wasm_simd_lane_immediate_missing",
                "guidance": (
                    "SimdInstr accepts only the opcode; an extra function argument "
                    "is ignored. Emit the lane after the expansion, for example "
                    "...SimdInstr(kExprI32x4ExtractLane), 0."
                ),
            }
        elif builder_assisted and re.search(
            r"ReferenceError: kExprCall is not defined",
            process_output,
        ):
            observation["corrective_hint"] = {
                "code": "wasm_direct_call_opcode_alias",
                "guidance": (
                    "The direct-call opcode is kExprCallFunction, not kExprCall. "
                    "Its following immediate is a numeric function index: use the "
                    "number returned by addImport(), or functionBuilder.index."
                ),
            }
        elif builder_assisted and re.search(
            r"ReferenceError: kExprV128(?:Load|Store) is not defined",
            process_output,
        ):
            observation["corrective_hint"] = {
                "code": "wasm_simd_memory_opcode_alias",
                "guidance": (
                    "The builder names SIMD memory opcodes kExprS128LoadMem and "
                    "kExprS128StoreMem. Emit them through ...SimdInstr(opcode), "
                    "followed by the alignment and offset immediates."
                ),
            }
        elif builder_assisted and re.search(
            r"ReferenceError: wasmSimdConst is not defined",
            process_output,
        ):
            observation["corrective_hint"] = {
                "code": "wasm_simd_const_helper_alias",
                "guidance": (
                    "The official helper is wasmS128Const(), not wasmSimdConst(). "
                    "Spread its returned bytes into addBody()."
                ),
            }
        elif builder_assisted and re.search(
            r"not enough arguments on the stack for call_indirect|"
            r"table index \d+ exceeds number of tables",
            process_output,
        ):
            observation["corrective_hint"] = {
                "code": "wasm_call_indirect_stack_or_immediates",
                "guidance": (
                    "Before kExprCallIndirect, push all callee arguments and then "
                    "one i32 table-slot operand. After the opcode emit the numeric "
                    "signature index followed by table.index; do not swap function, "
                    "signature, table, or stack-slot indices."
                ),
            }
        elif builder_assisted and "undeclared reference to function" in process_output:
            observation["corrective_hint"] = {
                "code": "wasm_undeclared_function_reference",
                "guidance": (
                    "A ref.func target must be declared by an element segment. "
                    "Reuse an already declared function index, or avoid ref.func and "
                    "use kExprCallFunction with functionBuilder.index."
                ),
            }
        elif builder_assisted and (
            "invalid body (entries must be 8 bit numbers)" in process_output
            and "[object Object]" in process_output
        ):
            observation["corrective_hint"] = {
                "code": "wasm_builder_object_in_body",
                "guidance": (
                    "addBody() received a builder object. Opcode immediates must be "
                    "bytes or numeric indices: use functionBuilder.index, while an "
                    "addImport() result is already a numeric index."
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
            r"ReferenceError: (?:wasm)?(?:Simd|GC)Instr is not defined",
            process_output,
            re.IGNORECASE,
        ):
            observation["corrective_hint"] = {
                "code": "unknown_wasm_prefixed_instruction_helper",
                "guidance": (
                    "The exact case-sensitive helper names are SimdInstr(opcode) "
                    "and GCInstr(opcode), with no wasm prefix. Expand the helper "
                    "inside addBody() and place any lane immediate after it."
                ),
            }
        elif builder_assisted and "tableObject.set is not a function" in process_output:
            observation["corrective_hint"] = {
                "code": "wasm_table_export_kind",
                "guidance": (
                    "builder.addExport() exports a function. Export a table with "
                    "builder.addExportOfKind('table', kExternalTable, table.index) "
                    "before mutating the resulting WebAssembly.Table."
                ),
            }
        elif builder_assisted and "Error: invalid element" in process_output:
            observation["corrective_hint"] = {
                "code": "wasm_element_segment_index",
                "guidance": (
                    "An element segment received a builder object instead of a "
                    "function index. Pass functionBuilder.index values in its "
                    "element array."
                ),
            }
        elif builder_assisted and "addElementSegment is not a function" in process_output:
            observation["corrective_hint"] = {
                "code": "wasm_unknown_element_segment_builder",
                "guidance": (
                    "WasmModuleBuilder has no addElementSegment(). Use "
                    "addActiveElementSegment(table.index, wasmI32Const(0), "
                    "[functionBuilder.index]) for a bounded active segment."
                ),
            }
        elif builder_assisted and (
            "invalid body (entries must be 8 bit numbers)" in process_output
            and re.search(r"(?<!\.\.\.)\b(?:Simd|GC)Instr\s*\(", source)
        ):
            observation["corrective_hint"] = {
                "code": "wasm_instruction_helper_not_spread",
                "guidance": (
                    "An instruction helper array was inserted as a nested body "
                    "entry. Spread every helper: ...SimdInstr(opcode) or "
                    "...GCInstr(opcode)."
                ),
            }
        elif builder_assisted and (
            "TypeError: expr is not iterable" in process_output
            and ".addActiveElementSegment" in source
        ):
            observation["corrective_hint"] = {
                "code": "wasm_element_segment_type_contract",
                "guidance": (
                    "For a numeric function-index element array, omit the optional "
                    "type argument: addActiveElementSegment(table.index, "
                    "wasmI32Const(0), [functionBuilder.index])."
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
