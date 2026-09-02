from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from fuzzynth.corpus import CorpusError, CorpusPool, extract_corpus_references


class CorpusPoolTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
