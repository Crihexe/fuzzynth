from __future__ import annotations

import unittest

from fuzzynth.execution_service import RecordedExecution


class RecordedExecutionTests(unittest.TestCase):
    def test_safe_dictionary_excludes_untrusted_output_bytes(self) -> None:
        result = RecordedExecution(
            execution_id="exec-1",
            profile="release_symbolized",
            image_id="sha256:" + "a" * 64,
            d8_sha256="b" * 64,
            program_sha256="c" * 64,
            stdout_sha256="d" * 64,
            stderr_sha256="e" * 64,
            duration_ms=10,
            outcome="ok",
            bug_candidate=False,
            exit_code=0,
            signal_name=None,
            timed_out=False,
            oom_killed=False,
            output_truncated=False,
            stdout=b"untrusted stdout",
            stderr=b"untrusted stderr",
        )

        safe = result.as_dict()
        self.assertNotIn("stdout", safe)
        self.assertNotIn("stderr", safe)
        self.assertEqual(safe["stdout_sha256"], "d" * 64)


if __name__ == "__main__":
    unittest.main()
