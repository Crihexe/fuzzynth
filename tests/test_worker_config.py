from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from fuzzynth.worker_config import load_worker_limits


VALID = """
[profiles.test]
cpus = 1.0
memory_bytes = 1024
pids = 64
wall_seconds = 2
max_output_bytes = 512
network = "none"
read_only_root = true
cap_drop = "all"
no_new_privileges = true
"""


class WorkerConfigTests(unittest.TestCase):
    def write(self, content: str) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "workers.toml"
        path.write_text(content, encoding="utf-8")
        return path

    def test_loads_limits_without_configurable_security_weakening(self) -> None:
        limits = load_worker_limits("test", self.write(VALID))

        self.assertEqual(limits.pids, 64)
        self.assertEqual(limits.max_output_bytes, 512)

    def test_rejects_enabled_network(self) -> None:
        path = self.write(VALID.replace('network = "none"', 'network = "bridge"'))

        with self.assertRaisesRegex(ValueError, "network"):
            load_worker_limits("test", path)

    def test_rejects_unknown_profile(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown"):
            load_worker_limits("missing", self.write(VALID))


if __name__ == "__main__":
    unittest.main()
