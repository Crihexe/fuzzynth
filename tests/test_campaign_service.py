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
        first = self.requests[0].input_messages
        second = self.requests[1].input_messages
        self.assertEqual([message.role for message in first], ["user"])
        self.assertNotIn("print(1);", first[0].content)
        self.assertEqual([message.role for message in second], ["user", "assistant", "user"])
        self.assertEqual(second[1].content, "print(1);")
        self.assertIn("execution-observation-json", second[2].content)
        self.assertNotIn("print(1);", second[2].content)
        self.assertEqual(result.session.next_turn, 3)
        self.assertEqual(result.session.status, "active")

    def test_crash_candidate_notifies_and_same_session_remains_active(self) -> None:
        notifications = []

        def crashing_executor(_program, **_kwargs):
            return RecordedExecution(
                execution_id="exec-candidate",
                profile="release_symbolized",
                image_id="sha256:" + "a" * 64,
                d8_sha256="b" * 64,
                program_sha256="c" * 64,
                stdout_sha256="d" * 64,
                stderr_sha256="e" * 64,
                duration_ms=5,
                outcome="v8_fatal",
                bug_candidate=True,
                exit_code=134,
                signal_name=None,
                timed_out=False,
                oom_killed=False,
                output_truncated=False,
                stdout=b"",
                stderr=b"Check failed: synthetic",
            )

        self.service.executor = crashing_executor
        self.service.event_notifier = (
            lambda session, result: notifications.append((session, result))
        )
        session = self.service.start_session(
            "spark-custom-iterative-js",
            seed=7,
            corpus_window=b"// selected historical example",
        )

        result = self.service.run_session(session.session_id, max_turns=1)

        self.assertEqual(result.session.status, "active")
        self.assertEqual(result.session.next_turn, 2)
        self.assertEqual(result.turns[0].stop_reason, "bug_candidate")
        self.assertEqual(len(notifications), 1)

    def test_unconditioned_control_requires_explicit_switch(self) -> None:
        session = self.service.start_session(
            "gpt-4o-mini-official-temperature-js",
            seed=99,
            corpus_window=None,
            allow_unconditioned=True,
        )

        self.assertIsNone(session.corpus)
        self.assertIn(session.temperature, (0.0, 0.5, 1.0, 1.5, 2.0))
        self.assertEqual(session.reasoning_effort, "none")
        self.assertLessEqual(session.target_turns, 3)

    def test_nano_sessions_are_exactly_six_turns(self) -> None:
        session = self.service.start_session(
            "gpt-4.1-nano-official-temperature-js",
            seed=101,
            corpus_window=None,
            allow_unconditioned=True,
        )

        self.assertEqual(session.target_turns, 6)

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
