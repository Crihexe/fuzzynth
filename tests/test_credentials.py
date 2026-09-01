from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from fuzzynth.credentials import (
    CredentialError,
    OFFICIAL_OPENAI_BASE_URL,
    load_credentials,
)


VALID_CONTENT = """\
ALTERNATE_OPENAI_API_KEY=alternate-test-value
ALTERNATE_OPENAI_BASE_URL=https://alternate.invalid/v1/
OPENAI_API_KEY=official-test-value
"""


class CredentialTests(unittest.TestCase):
    def write_credentials(self, content: str, mode: int = 0o600) -> Path:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        path = Path(temporary_directory.name) / "credentials"
        path.write_text(content, encoding="utf-8")
        os.chmod(path, mode)
        return path

    def test_loads_separate_providers(self) -> None:
        credentials = load_credentials(self.write_credentials(VALID_CONTENT))

        self.assertEqual(credentials.alternate.name, "alternate")
        self.assertEqual(
            credentials.alternate.base_url, "https://alternate.invalid/v1"
        )
        self.assertEqual(credentials.official.name, "official")
        self.assertEqual(credentials.official.base_url, OFFICIAL_OPENAI_BASE_URL)
        self.assertNotEqual(
            credentials.alternate.api_key, credentials.official.api_key
        )

    def test_safe_status_never_contains_values(self) -> None:
        credentials = load_credentials(self.write_credentials(VALID_CONTENT))
        status = repr(credentials.safe_status())

        self.assertNotIn("alternate-test-value", status)
        self.assertNotIn("official-test-value", status)
        self.assertNotIn("alternate.invalid", status)

    def test_rejects_permissive_file_mode(self) -> None:
        path = self.write_credentials(VALID_CONTENT, mode=0o644)

        with self.assertRaisesRegex(CredentialError, "group/world"):
            load_credentials(path)

    def test_rejects_missing_official_key(self) -> None:
        content = VALID_CONTENT.replace("OPENAI_API_KEY=official-test-value\n", "")

        with self.assertRaisesRegex(CredentialError, "OPENAI_API_KEY"):
            load_credentials(self.write_credentials(content))

    def test_rejects_non_https_alternate_url(self) -> None:
        content = VALID_CONTENT.replace("https://", "http://")

        with self.assertRaisesRegex(CredentialError, "HTTPS"):
            load_credentials(self.write_credentials(content))


if __name__ == "__main__":
    unittest.main()
