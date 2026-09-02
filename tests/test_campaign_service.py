from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from fuzzynth.campaign_service import CampaignService, CampaignServiceError
from fuzzynth.credentials import CredentialStore, ProviderCredentials
from fuzzynth.execution_service import RecordedExecution
from fuzzynth.responses import CreateResult, StreamResult


class FakeClient:
    def __init__(self, requests: list):
        self.requests = requests

    def _response(self, request):
        self.requests.append(request)
        index = len(self.requests)
        response = {
            "id": f"response-{index}",
            "status": "completed",
            "model": request.model,
            "reasoning": {"effort": request.reasoning_effort},
            "text": {"verbosity": request.verbosity},
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": f"print({index});"}
                    ],
                }
            ],
            "usage": {
                "input_tokens": 100,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": 10,
                "output_tokens_details": {"reasoning_tokens": 2},
            },
        }
        raw = json.dumps(response, separators=(",", ":")).encode()
        return raw, response

    def create_raw(self, request, *, max_response_bytes):
        raw, response = self._response(request)
        return CreateResult(raw_response=raw, response=response)

    def stream(self, request, *, max_stream_bytes):
        raw, response = self._response(request)
        return StreamResult(
            raw_sse=raw,
            output=f"print({len(self.requests)});".encode(),
            terminal_type="response.completed",
            response=response,
        )


class CampaignServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state_root = Path(temporary.name) / "state"
        self.requests: list = []
        credentials = CredentialStore(
            alternate=ProviderCredentials(
                name="alternate",
                base_url="https://alternate.invalid/v1",
                api_key="test-secret",
            ),
            official=ProviderCredentials(
                name="official",
                base_url="https://api.openai.com/v1",
                api_key="test-secret",
            ),
        )

        def factory(_provider, _timeout):
            return FakeClient(self.requests)

        def executor(program, **_kwargs):
            index = len(self.requests)
            return RecordedExecution(
                execution_id=f"exec-{index}",
                profile="release_symbolized",
                image_id="sha256:" + "a" * 64,
                d8_sha256="b" * 64,
                program_sha256="c" * 64,
                stdout_sha256="d" * 64,
                stderr_sha256="e" * 64,
                duration_ms=5,
                outcome="ok",
                bug_candidate=False,
                exit_code=0,
                signal_name=None,
                timed_out=False,
                oom_killed=False,
                output_truncated=False,
                stdout=f"ran {index}\n".encode(),
                stderr=b"",
            )

        self.service = CampaignService(
            repo_root=Path("."),
            state_root=self.state_root,
            credentials=credentials,
            client_factory=factory,
            executor=executor,
        )
        self.addCleanup(self.service.close)

    def test_requires_explicit_corpus_or_unconditioned_control(self) -> None:
        with self.assertRaisesRegex(CampaignServiceError, "corpus"):
            self.service.start_session(
                "spark-custom-iterative-js",
                seed=7,
                corpus_window=None,
            )

    def test_runs_immediate_iterative_turns_with_previous_feedback(self) -> None:
        session = self.service.start_session(
            "spark-custom-iterative-js",
            seed=7,
            corpus_window=b"// selected historical example",
        )

        result = self.service.run_session(session.session_id, max_turns=2)

        self.assertEqual(len(result.turns), 2)
        self.assertEqual(len(self.requests), 2)
        self.assertTrue(all(request.stream for request in self.requests))
        self.assertNotIn("print(1);", self.requests[0].input_text)
        self.assertIn("print(1);", self.requests[1].input_text)
        self.assertIn("execution-observation-json", self.requests[1].input_text)
        self.assertEqual(result.session.next_turn, 3)
        self.assertEqual(result.session.status, "active")

    def test_unconditioned_control_requires_explicit_switch(self) -> None:
        session = self.service.start_session(
            "luna-official-high-temperature-none-js",
            seed=99,
            corpus_window=None,
            allow_unconditioned=True,
        )

        self.assertIsNone(session.corpus)
        self.assertIn(session.temperature, (1.2, 1.5, 1.8))
        self.assertIn(session.reasoning_effort, ("none", "low"))

    def test_paused_worker_cannot_start_or_advance(self) -> None:
        session = self.service.start_session(
            "spark-custom-iterative-js",
            seed=7,
            corpus_window=b"// selected historical example",
        )
        self.service.control.set_worker(
            session.worker_id,
            "paused",
            request_id="test-pause",
            source="test",
            actor="owner",
            command="pause worker",
        )

        with self.assertRaisesRegex(CampaignServiceError, "paused"):
            self.service.run_session(session.session_id, max_turns=1)
        with self.assertRaisesRegex(CampaignServiceError, "paused"):
            self.service.start_session(
                "spark-custom-iterative-js",
                seed=8,
                corpus_window=b"// selected historical example",
            )


if __name__ == "__main__":
    unittest.main()
