from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from fuzzynth.docker_executor import DockerExecutor, WorkerLimits


IMAGE_ID = "sha256:" + "a" * 64


class DockerExecutorConfigurationTests(unittest.TestCase):
    def test_requires_immutable_image_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "immutable"):
            DockerExecutor("fuzzynth/d8:latest")

    def test_rejects_invalid_limits(self) -> None:
        with self.assertRaisesRegex(ValueError, "pids"):
            WorkerLimits(pids=0)

    def test_create_command_contains_isolation_and_exact_image(self) -> None:
        executor = DockerExecutor(IMAGE_ID)
        source = Path("/state/artifacts/aa/program")

        command = executor._create_command(
            "fuzzynth-test", source, ("--allow-natives-syntax",)
        )

        self.assertEqual(command[:2], ["docker", "create"])
        self.assertIn("none", command)
        self.assertIn("--read-only", command)
        self.assertIn("ALL", command)
        self.assertIn("no-new-privileges", command)
        self.assertIn("core=0:0", command)
        self.assertIn(IMAGE_ID, command)
        self.assertEqual(command[-2:], ["--allow-natives-syntax", "/input/program.js"])

    def test_rejects_non_option_d8_argument(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "program.js"
            source.write_text("0;", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "--options"):
                DockerExecutor(IMAGE_ID).run(source, ("not-a-flag",))

    def test_support_file_mount_is_bounded_to_input_javascript(self) -> None:
        executor = DockerExecutor(IMAGE_ID)
        command = executor._create_command(
            "fuzzynth-test",
            Path("/state/program.js"),
            ("--fuzzing",),
            ((Path("/v8/wasm-module-builder.js"), "/input/wasm-module-builder.js"),),
        )

        self.assertIn(
            "type=bind,src=/v8/wasm-module-builder.js,dst=/input/wasm-module-builder.js,readonly",
            command,
        )


if __name__ == "__main__":
    unittest.main()
