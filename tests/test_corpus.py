from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fuzzynth.corpus import CorpusError, CorpusPool, extract_corpus_references


class CorpusPoolTests(unittest.TestCase):
    @staticmethod
    def _build_index(root: Path) -> Path:
        files = root / "files"
        files.mkdir()
        index = root / "index.sqlite"
        with sqlite3.connect(index) as connection:
            connection.executescript(
                """
                CREATE TABLE build_info(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO build_info VALUES ('schema_version', '2');
                CREATE TABLE artifacts(
                  artifact_id TEXT PRIMARY KEY,
                  filename TEXT NOT NULL,
                  sha256 TEXT NOT NULL,
                  size_bytes INTEGER NOT NULL,
                  normalized_sha256 TEXT NOT NULL,
                  preserved_original_available INTEGER NOT NULL,
                  validation_status TEXT NOT NULL,
                  primary_category TEXT NOT NULL,
                  engine_family TEXT NOT NULL
                );
                """
            )
            records = (
                ("a", b"function a(){return 1}\n", "norm-a", 1, "V8", "regression_test"),
                ("b", b"function b(){return 2}\n", "norm-b", 1, "WebAssembly/V8", "poc_or_reproducer"),
                ("c", b"function c(){return 3}\n", "norm-c", 1, "JavaScriptCore", "exploit"),
                ("d", b"function d(){return 4}\n", "norm-d", 1, "V8", "support_harness"),
                ("e", b"function e(){return 5}\n", "norm-a", 0, "V8", "regression_test"),
            )
            for artifact_id, data, normalized, original, engine, category in records:
                filename = f"V8JS-{artifact_id}.js"
                (files / filename).write_bytes(data)
                connection.execute(
                    "INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        artifact_id,
                        filename,
                        hashlib.sha256(data).hexdigest(),
                        len(data),
                        normalized,
                        original,
                        "accepted",
                        category,
                        engine,
                    ),
                )
        return index

    def test_builds_deterministic_diverse_provenance_window(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = []
            for index in range(3):
                path = root / f"sample-{index}.js"
                path.write_text(f"function f{index}() {{ return {index}; }}\n")
                paths.append(path)
            pool = CorpusPool.load(tuple(paths))

            first = pool.build_window(seed=7, size=2)
            second = pool.build_window(seed=7, size=2)

        self.assertEqual(first, second)
        self.assertEqual(first.count(b"<historical-js-example "), 2)
        self.assertIn(b"Do not copy, mutate", first)
        self.assertIn(b"sha256=", first)
        references = extract_corpus_references(first)
        self.assertEqual(len(references), 2)
        self.assertTrue(all(reference.name.endswith(".js") for reference in references))
        self.assertTrue(all(len(reference.sha256) == 64 for reference in references))

    def test_rejects_duplicates_and_non_javascript(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sample.js"
            path.write_text("print(1);\n")
            with self.assertRaises(CorpusError):
                CorpusPool.load((path, path))
            other = Path(temporary) / "sample.txt"
            other.write_text("print(1);\n")
            with self.assertRaises(CorpusError):
                CorpusPool.load((other,))

    def test_loads_validated_diverse_samples_from_sqlite_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            index = self._build_index(Path(temporary))
            with patch("fuzzynth.corpus.MIN_INDEX_POOL_SAMPLES", 2):
                pool = CorpusPool.load_index(index)

        self.assertEqual(len(pool.samples), 2)
        self.assertEqual(
            {sample.name for sample in pool.samples},
            {"V8JS-a.js", "V8JS-b.js"},
        )

    def test_index_loader_rejects_tampered_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = self._build_index(root)
            (root / "files/V8JS-a.js").write_text("tampered\n", encoding="utf-8")
            with patch("fuzzynth.corpus.MIN_INDEX_POOL_SAMPLES", 2):
                with self.assertRaisesRegex(CorpusError, "unexpected size|SHA-256"):
                    CorpusPool.load_index(index)


if __name__ == "__main__":
    unittest.main()
