from __future__ import annotations

from decimal import Decimal
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
from fuzzynth.campaign_turn import CampaignTurnRunner
from fuzzynth.catalog import EvidenceCatalog
from fuzzynth.execution_service import RecordedExecution
from fuzzynth.responses import CreateResult, ResponsesError


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
        )
        self.executed_generation_id = None

    def executor(self, program, **kwargs):
        self.executed_generation_id = kwargs["generation_id"]
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
        client = FakeClient(CreateResult(raw_response=raw, response=response))

        result = self.runner().run_turn(
            worker=self.worker,
            session_id="session-1",
            turn_index=1,
            plan=SessionPlan(7, 4, "xhigh", None),
            instructions="code only",
            input_bytes=b"next program",
            client=client,
        )

        self.assertEqual(result.program, b"print('ok');")
        self.assertEqual(self.executed_generation_id, result.generation_id)
        self.assertIsNotNone(result.feedback)
        self.assertIsNone(result.pause_reason)
        status = self.budgets.status("luna")
        self.assertEqual(status["cached_input_tokens"], 60)
        self.assertEqual(status["output_tokens"], 50)
        generation = self.catalog.connection.execute(
            "SELECT status, raw_stream_sha256, program_sha256 FROM generation"
        ).fetchone()
        self.assertEqual(generation[0], "completed")
        self.assertIsNotNone(generation[1])
        self.assertIsNotNone(generation[2])

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
            input_bytes=b"next program",
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
