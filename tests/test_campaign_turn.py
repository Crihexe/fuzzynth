from __future__ import annotations

from decimal import Decimal
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from fuzzynth.artifacts import ArtifactStore
from fuzzynth.budgets import BudgetLedger, MeterPolicy
from fuzzynth.campaign_config import (
    CampaignWorker,
    SessionPlan,
    load_campaign_configuration,
)
from fuzzynth.campaign_turn import CampaignTurnRunner, resolve_execution_flags
from fuzzynth.catalog import EvidenceCatalog
from fuzzynth.execution_service import RecordedExecution
from fuzzynth.responses import CreateResult, ResponsesError, StreamResult
from fuzzynth.session_context import ConversationMessage


class FakeClient:
    def __init__(self, result=None, error: ResponsesError | None = None):
        self.result = result
        self.error = error
        self.request = None

    def create_raw(self, request, *, max_response_bytes):
        self.request = request
        if self.error is not None:
            raise self.error
        return self.result

    def stream(self, request, *, max_stream_bytes):
        self.request = request
        if self.error is not None:
            raise self.error
        return self.result


class CampaignTurnTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = ArtifactStore(self.root / "artifacts")
        self.catalog = EvidenceCatalog(self.root / "catalog.sqlite3")
        self.addCleanup(self.catalog.close)
        self.policy = MeterPolicy(
            meter_id="luna",
            unit="credit",
            metered=True,
            input_per_million=Decimal("5"),
            cached_input_per_million=Decimal("0.5"),
            output_per_million=Decimal("30"),
            hard_total_microunits=1_250_000_000,
            hard_output_tokens=42_000_000,
        )
        self.budgets = BudgetLedger(
            self.root / "budgets.sqlite3", {"luna": self.policy}
        )
        self.addCleanup(self.budgets.close)
        configured = load_campaign_configuration(Path("config/campaign-workers.toml"))
        base = configured.workers["luna-custom-xhigh-iterative-js"]
        self.worker = CampaignWorker(
            worker_id=base.worker_id,
            enabled=True,
            provider=base.provider,
            model=base.model,
            meter="luna",
            mode=base.mode,
            prompt_path=base.prompt_path,
            reasoning_efforts=base.reasoning_efforts,
            verbosity=base.verbosity,
            temperatures=base.temperatures,
            min_turns_per_session=base.min_turns_per_session,
            max_turns_per_session=base.max_turns_per_session,
            history_turns=base.history_turns,
            max_output_tokens=base.max_output_tokens,
            reservation_output_tokens=base.reservation_output_tokens,
            v8_build_profile=base.v8_build_profile,
            v8_worker_profile=base.v8_worker_profile,
            d8_flags=base.d8_flags,
            prompt_variant=base.prompt_variant,
            corpus_pair_id=base.corpus_pair_id,
        )
        self.executed_generation_id = None
        self.executed_flags = None

    def executor(self, program, **kwargs):
        self.executed_generation_id = kwargs["generation_id"]
        self.executed_flags = kwargs["flags"]
        return RecordedExecution(
            execution_id="exec-test",
            profile="release_symbolized",
            image_id="sha256:" + "a" * 64,
            d8_sha256="b" * 64,
            program_sha256="c" * 64,
            stdout_sha256="d" * 64,
            stderr_sha256="e" * 64,
            duration_ms=12,
            outcome="ok",
            bug_candidate=False,
            exit_code=0,
            signal_name=None,
            timed_out=False,
            oom_killed=False,
            output_truncated=False,
            stdout=b"done\n",
            stderr=b"",
        )

    def runner(self) -> CampaignTurnRunner:
        return CampaignTurnRunner(
            repo_root=Path("."),
            state_root=self.root,
            store=self.store,
            catalog=self.catalog,
            budgets=self.budgets,
            meter_policy=self.policy,
            max_response_bytes=4096,
            max_program_bytes=2048,
            max_feedback_bytes=512,
            executor=self.executor,
        )

    @staticmethod
    def input_messages() -> tuple[ConversationMessage, ...]:
        return (ConversationMessage(role="user", content="next program"),)

    @staticmethod
    def response() -> dict:
        return {
            "id": "resp-test",
            "status": "completed",
            "model": "gpt-5.6-luna",
            "temperature": 1.0,
            "reasoning": {"effort": "xhigh"},
            "text": {"verbosity": "high"},
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "print('ok');"}
                    ],
                }
            ],
            "usage": {
                "input_tokens": 100,
                "input_tokens_details": {"cached_tokens": 60},
                "output_tokens": 50,
                "output_tokens_details": {"reasoning_tokens": 30},
            },
        }

    def test_completed_response_is_preserved_executed_and_metered(self) -> None:
        response = self.response()
        raw = json.dumps(response, separators=(",", ":")).encode()
        client = FakeClient(
            StreamResult(
                raw_sse=raw,
                output=b"print('ok');",
                terminal_type="response.completed",
                response=response,
            )
        )

        result = self.runner().run_turn(
            worker=self.worker,
            session_id="session-1",
            turn_index=1,
            plan=SessionPlan(7, 4, "xhigh", None),
            instructions="code only",
            input_messages=self.input_messages(),
            client=client,
        )

        self.assertEqual(result.program, b"print('ok');")
        self.assertEqual(self.executed_generation_id, result.generation_id)
        self.assertTrue(
            any(flag.startswith("--random-seed=") for flag in self.executed_flags)
        )
        self.assertTrue(
            any(
                flag.startswith("--fuzzer-random-seed=")
                for flag in self.executed_flags
            )
        )
        self.assertIsNotNone(result.feedback)
        self.assertIsNone(result.pause_reason)
        self.assertTrue(client.request.stream)
        payload = client.request.to_payload()
        self.assertNotIn("max_output_tokens", payload)
        self.assertNotIn("temperature", payload)
        status = self.budgets.status("luna")
        self.assertEqual(status["cached_input_tokens"], 60)
        self.assertEqual(status["output_tokens"], 50)
        generation = self.catalog.connection.execute(
            "SELECT status, raw_stream_sha256, program_sha256, "
            "requested_parameters_json FROM generation"
        ).fetchone()
        self.assertEqual(generation[0], "completed")
        self.assertIsNotNone(generation[1])
        self.assertIsNotNone(generation[2])
        parameters = json.loads(generation[3])
        self.assertEqual(parameters["prompt_variant"], self.worker.prompt_variant)
        self.assertEqual(parameters["corpus_pair_id"], self.worker.corpus_pair_id)
        self.assertEqual(parameters["corpus_strategy"], "uniform")
        self.assertEqual(
            parameters["prompt_sha256"],
            hashlib.sha256(b"code only").hexdigest(),
        )

    def test_execution_seed_flags_are_stable_and_do_not_override_explicit(self) -> None:
        first = resolve_execution_flags(
            ("--fuzzing",), worker_id="worker", program=b"print(1)"
        )
        second = resolve_execution_flags(
            ("--fuzzing",), worker_id="worker", program=b"print(1)"
        )
        explicit = resolve_execution_flags(
            ("--fuzzing", "--random-seed=7", "--fuzzer-random-seed=8"),
            worker_id="worker",
            program=b"print(1)",
        )

        self.assertEqual(first, second)
        self.assertEqual(
            explicit,
            ("--fuzzing", "--random-seed=7", "--fuzzer-random-seed=8"),
        )

    def test_official_provider_uses_complete_json_with_supported_controls(self) -> None:
        response = self.response()
        raw = json.dumps(response, separators=(",", ":")).encode()
        client = FakeClient(CreateResult(raw_response=raw, response=response))
        official = CampaignWorker(
            worker_id="official-test",
            enabled=True,
            provider="official",
            model=self.worker.model,
            meter=self.worker.meter,
            mode=self.worker.mode,
            prompt_path=self.worker.prompt_path,
            reasoning_efforts=("none",),
            verbosity="high",
            temperatures=(1.5,),
            min_turns_per_session=1,
            max_turns_per_session=1,
            history_turns=0,
            max_output_tokens=8192,
            reservation_output_tokens=8192,
            v8_build_profile=self.worker.v8_build_profile,
            v8_worker_profile=self.worker.v8_worker_profile,
            d8_flags=self.worker.d8_flags,
            send_reasoning=False,
            send_verbosity=False,
        )

        result = self.runner().run_turn(
            worker=official,
            session_id="session-official",
            turn_index=1,
            plan=SessionPlan(8, 1, "none", 1.5),
            instructions="code only",
            input_messages=self.input_messages(),
            client=client,
        )

        self.assertEqual(result.program, b"print('ok');")
        self.assertFalse(client.request.stream)
        self.assertEqual(client.request.max_output_tokens, 8192)
        self.assertEqual(client.request.temperature, 1.5)
        payload = client.request.to_payload()
        self.assertNotIn("reasoning", payload)
        self.assertNotIn("text", payload)

    def test_stream_mismatch_is_archived_and_never_executed(self) -> None:
        response = self.response()
        client = FakeClient(
            StreamResult(
                raw_sse=b"raw-partial-or-conflicting-sse",
                output=b"print('different');",
                terminal_type="response.completed",
                response=response,
            )
        )

        result = self.runner().run_turn(
            worker=self.worker,
            session_id="session-mismatch",
            turn_index=1,
            plan=SessionPlan(9, 1, "xhigh", None),
            instructions="code only",
            input_messages=self.input_messages(),
            client=client,
        )

        self.assertEqual(result.pause_reason, "provider_error")
        self.assertIsNone(result.execution)
        self.assertIsNone(self.executed_generation_id)
        generation = self.catalog.connection.execute(
            "SELECT status, raw_stream_sha256 FROM generation"
        ).fetchone()
        self.assertEqual(generation[0], "failed")
        self.assertIsNotNone(generation[1])

    def test_terminal_stream_error_executes_preserved_partial_output_once(self) -> None:
        client = FakeClient(
            StreamResult(
                raw_sse=b'data: {"type":"error","code":"request_timeout"}\n\n',
                output=b"print('partial');",
                terminal_type="error",
                response=None,
                error_code="request_timeout",
            )
        )

        result = self.runner().run_turn(
            worker=self.worker,
            session_id="session-timeout",
            turn_index=1,
            plan=SessionPlan(10, 1, "xhigh", None),
            instructions="code only",
            input_messages=self.input_messages(),
            client=client,
        )

        self.assertIsNone(result.pause_reason)
        self.assertEqual(result.program, b"print('partial');")
        self.assertIsNotNone(result.execution)
        self.assertEqual(self.executed_generation_id, result.generation_id)
        row = self.catalog.connection.execute(
            "SELECT status, effective_parameters_json, raw_stream_sha256, "
            "program_sha256 FROM generation"
        ).fetchone()
        self.assertEqual(row[0], "incomplete")
        effective = json.loads(row[1])
        self.assertEqual(effective["error_code"], "request_timeout")
        self.assertTrue(effective["partial_output_continuable"])
        self.assertTrue(effective["partial_output_executable"])
        self.assertIsNotNone(row[2])
        self.assertIsNotNone(row[3])

    def test_terminal_incomplete_response_is_metered_and_partial_is_executed(self) -> None:
        response = self.response()
        response["status"] = "incomplete"
        response["incomplete_details"] = {"reason": "provider_limit"}
        response["output"][0]["content"][0]["text"] = "print('prefix');"
        client = FakeClient(
            StreamResult(
                raw_sse=b"complete terminal incomplete SSE",
                output=b"print('prefix');",
                terminal_type="response.incomplete",
                response=response,
            )
        )

        result = self.runner().run_turn(
            worker=self.worker,
            session_id="session-incomplete",
            turn_index=1,
            plan=SessionPlan(11, 1, "high", None),
            instructions="code only",
            input_messages=self.input_messages(),
            client=client,
        )

        self.assertIsNone(result.pause_reason)
        self.assertEqual(result.program, b"print('prefix');")
        self.assertIsNotNone(result.execution)
        self.assertEqual(self.budgets.status("luna")["output_tokens"], 50)
        status = self.catalog.connection.execute(
            "SELECT status FROM generation"
        ).fetchone()[0]
        self.assertEqual(status, "incomplete")

    def test_official_max_output_prefix_is_executed_without_pausing(self) -> None:
        response = self.response()
        response["status"] = "incomplete"
        response["incomplete_details"] = {"reason": "max_output_tokens"}
        response["output"][0]["content"][0]["text"] = "print('json-prefix');"
        raw = json.dumps(response, separators=(",", ":")).encode()
        client = FakeClient(CreateResult(raw_response=raw, response=response))
        official = CampaignWorker(
            worker_id="official-prefix-test",
            enabled=True,
            provider="official",
            model="gpt-4o-mini",
            meter="luna",
            mode=self.worker.mode,
            prompt_path=self.worker.prompt_path,
            reasoning_efforts=("none",),
            verbosity="medium",
            temperatures=(2.0,),
            min_turns_per_session=1,
            max_turns_per_session=3,
            history_turns=2,
            max_output_tokens=8192,
            reservation_output_tokens=8192,
            v8_build_profile=self.worker.v8_build_profile,
            v8_worker_profile=self.worker.v8_worker_profile,
            d8_flags=self.worker.d8_flags,
            send_reasoning=False,
            send_verbosity=False,
        )

        result = self.runner().run_turn(
            worker=official,
            session_id="session-official-prefix",
            turn_index=1,
            plan=SessionPlan(12, 3, "none", 2.0),
            instructions="code only",
            input_messages=self.input_messages(),
            client=client,
        )

        self.assertIsNone(result.pause_reason)
        self.assertEqual(result.program, b"print('json-prefix');")
        self.assertIsNotNone(result.execution)
        row = self.catalog.connection.execute(
            "SELECT status, effective_parameters_json FROM generation"
        ).fetchone()
        self.assertEqual(row[0], "incomplete")
        effective = json.loads(row[1])
        self.assertEqual(effective["incomplete_reason"], "max_output_tokens")
        self.assertTrue(effective["partial_output_continuation"])

    def test_provider_limit_error_is_preserved_and_pauses(self) -> None:
        client = FakeClient(
            error=ResponsesError(
                "limited",
                status=429,
                code="rate_limit_exceeded",
                raw_response=b'{"error":{"code":"rate_limit_exceeded"}}',
            )
        )

        result = self.runner().run_turn(
            worker=self.worker,
            session_id="session-1",
            turn_index=1,
            plan=SessionPlan(7, 4, "xhigh", None),
            instructions="code only",
            input_messages=self.input_messages(),
            client=client,
        )

        self.assertEqual(result.pause_reason, "provider_quota_or_rate_limit")
        self.assertIsNone(result.execution)
        self.assertEqual(self.budgets.status("luna")["uncertain_reservations"], 1)
        generation = self.catalog.connection.execute(
            "SELECT status, raw_stream_sha256 FROM generation"
        ).fetchone()
        self.assertEqual(generation[0], "failed")
        self.assertIsNotNone(generation[1])


if __name__ == "__main__":
    unittest.main()
