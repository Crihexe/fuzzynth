from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from fuzzynth.artifacts import ArtifactIntegrityError, ArtifactRef, ArtifactStore


class ArtifactStoreTests(unittest.TestCase):
    def test_preserves_exact_bytes_and_uses_private_file_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ArtifactStore(Path(temporary))
            payload = b"\x00raw-stream\r\n\xff"

            reference = store.put(payload)

            self.assertEqual(store.read(reference), payload)
            path = Path(temporary) / reference.relative_path
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_deduplicates_identical_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ArtifactStore(Path(temporary))

            first = store.put(b"same")
            second = store.put(b"same")

            self.assertEqual(first, second)
            files = [path for path in Path(temporary).rglob("*") if path.is_file()]
            self.assertEqual(len(files), 1)

    def test_rejects_tampered_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = ArtifactStore(root)
            reference = store.put(b"original")
            (root / reference.relative_path).write_bytes(b"tampered")

            with self.assertRaisesRegex(ArtifactIntegrityError, "integrity"):
                store.read(reference)

    def test_rejects_noncanonical_reference_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ArtifactStore(Path(temporary))
            valid = store.put(b"payload")
            invalid = ArtifactRef(valid.sha256, valid.size, "../escape")

            with self.assertRaisesRegex(ArtifactIntegrityError, "canonical"):
                store.read(invalid)

    def test_requires_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ArtifactStore(Path(temporary))

            with self.assertRaisesRegex(TypeError, "bytes"):
                store.put("not bytes")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
