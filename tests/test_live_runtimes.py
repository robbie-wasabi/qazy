from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from qazy.runtimes import probe_runtime


class LiveRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cwd = Path(__file__).resolve().parents[1]

    @unittest.skipUnless(shutil.which("claude"), "claude CLI not installed")
    def test_claude_smoke_prompt_executes(self) -> None:
        probe = probe_runtime("claude", cwd=self.cwd, smoke=True)
        self.assertTrue(probe.installed)
        self.assertTrue(probe.smoke_ok, probe.detail)
        self.assertEqual(probe.detail, "OK")

    @unittest.skipUnless(shutil.which("codex"), "codex CLI not installed")
    def test_codex_smoke_prompt_executes(self) -> None:
        probe = probe_runtime("codex", cwd=self.cwd, smoke=True)
        self.assertTrue(probe.installed)
        self.assertTrue(probe.smoke_ok, probe.detail)
        self.assertEqual(probe.detail, "OK")

    @unittest.skipUnless(shutil.which("opencode"), "opencode CLI not installed")
    def test_opencode_smoke_probe_reports_real_status(self) -> None:
        probe = probe_runtime("opencode", cwd=self.cwd, smoke=True)
        self.assertTrue(probe.installed)
        self.assertFalse(probe.smoke_ok)
        self.assertTrue(probe.detail)
