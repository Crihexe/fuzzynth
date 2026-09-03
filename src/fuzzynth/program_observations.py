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

    if prompt_variant == "wasm_boundary_v1":
        wasm = {
            "constructs_module": bool(
                re.search(
                    r"(?:new\s+WebAssembly\.Module|WebAssembly\.instantiate)\s*\(",
                    source,
                )
            ),
            "constructs_instance": bool(
                re.search(
                    r"(?:new\s+WebAssembly\.Instance|WebAssembly\.instantiate)\s*\(",
                    source,
                )
            ),
            "calls_export": _calls_wasm_export(source),
            "uses_percent_intrinsic": bool(_PERCENT_INTRINSIC.search(source)),
        }
        observation["subsystem"] = "wasm"
        observation["subsystem_features"] = wasm
        observation["prompt_adherent"] = (
            wasm["constructs_module"]
            and wasm["constructs_instance"]
            and wasm["calls_export"]
            and not wasm["uses_percent_intrinsic"]
        )
        observation["runtime_path_completed"] = bool(
            observation["prompt_adherent"] and outcome == "ok"
        )
        if "CompileError: WebAssembly.Module()" in process_output:
            observation["corrective_hint"] = {
                "code": "wasm_binary_rejected_before_compiled_execution",
                "guidance": (
                    "The binary layout was invalid. Correct all section lengths, "
                    "vector counts, type/function indices, and body sizes; the next "
                    "program must instantiate and call an export successfully."
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

    elif prompt_variant == "concurrency_v1":
        concurrency = {
            "allocates_shared_array_buffer": "SharedArrayBuffer" in source,
            "uses_atomics": "Atomics." in source,
            "uses_worker": bool(re.search(r"\bnew\s+Worker\s*\(", source)),
            "uses_percent_intrinsic": bool(_PERCENT_INTRINSIC.search(source)),
        }
        observation["subsystem"] = "concurrency"
        observation["subsystem_features"] = concurrency
        observation["prompt_adherent"] = (
            concurrency["allocates_shared_array_buffer"]
            and concurrency["uses_atomics"]
            and not concurrency["uses_percent_intrinsic"]
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
        elif outcome == "timeout":
            observation["corrective_hint"] = {
                "code": "worker_protocol_did_not_terminate",
                "guidance": (
                    "The message protocol deadlocked. Keep worker state inside the "
                    "worker, make every wait satisfiable by the other side, send one "
                    "completion message on every path, and terminate the worker."
                ),
            }

    elif prompt_variant == "language_v1":
        forbidden = {
            "percent_intrinsic": bool(_PERCENT_INTRINSIC.search(source)),
            "shared_memory_or_worker": bool(
                re.search(r"\b(?:SharedArrayBuffer|Worker)\b", source)
            ),
            "wasm": "WebAssembly" in source,
        }
        observation["subsystem"] = "language"
        observation["subsystem_features"] = forbidden
        observation["prompt_adherent"] = not any(forbidden.values())
        observation["runtime_path_completed"] = bool(
            observation["prompt_adherent"] and outcome == "ok"
        )

    return observation
