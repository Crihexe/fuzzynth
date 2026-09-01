from __future__ import annotations

import unittest

from fuzzynth.responses import GenerationRequest


class GenerationRequestTests(unittest.TestCase):
    def test_omits_unsupported_optional_parameters(self) -> None:
        payload = GenerationRequest(
            model="gpt-test",
            instructions="code only",
            input_text="generate",
        ).to_payload()

        self.assertNotIn("temperature", payload)
        self.assertNotIn("reasoning", payload)
        self.assertNotIn("text", payload)

    def test_serializes_explicit_experiment_parameters(self) -> None:
        payload = GenerationRequest(
            model="gpt-test",
            instructions="code only",
            input_text="generate",
            temperature=1.3,
            reasoning_effort="low",
            verbosity="high",
        ).to_payload()

        self.assertEqual(payload["temperature"], 1.3)
        self.assertEqual(payload["reasoning"], {"effort": "low"})
        self.assertEqual(payload["text"], {"verbosity": "high"})

    def test_rejects_invalid_temperature(self) -> None:
        request = GenerationRequest(
            model="gpt-test",
            instructions="code only",
            input_text="generate",
            temperature=2.1,
        )

        with self.assertRaisesRegex(ValueError, "temperature"):
            request.to_payload()


if __name__ == "__main__":
    unittest.main()
