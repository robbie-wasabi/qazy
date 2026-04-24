from __future__ import annotations

import unittest
from pathlib import Path

from qazy.runtimes import ClaudeRuntime, CodexRuntime


class RuntimeCommandTests(unittest.TestCase):
    def test_claude_runtime_uses_strict_mcp_config(self) -> None:
        runtime = ClaudeRuntime()
        command = runtime.build_command("prompt", cwd=Path("/tmp/workspace"))

        self.assertIn("--strict-mcp-config", command.argv)
        self.assertNotIn("--bare", command.argv)

    def test_codex_runtime_default_command_pins_default_model(self) -> None:
        runtime = CodexRuntime()
        command = runtime.build_command("prompt", cwd=Path("/tmp/workspace"))

        self.assertIn("-m", command.argv)
        self.assertIn(runtime.default_model, command.argv)
        self.assertIn("--ignore-user-config", command.argv)
        self.assertNotIn("-c", command.argv)
        self.assertEqual(runtime.effective_model(None), runtime.default_model)

    def test_codex_runtime_accepts_explicit_model_and_reasoning(self) -> None:
        runtime = CodexRuntime()
        command = runtime.build_command(
            "prompt",
            cwd=Path("/tmp/workspace"),
            model="gpt-5.4-mini",
            reasoning_effort="low",
        )

        self.assertIn("-m", command.argv)
        self.assertIn("gpt-5.4-mini", command.argv)
        self.assertIn("-c", command.argv)
        self.assertIn('model_reasoning_effort="low"', command.argv)
