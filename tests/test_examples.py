from __future__ import annotations

import contextlib
import io
import os
import stat
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from qazy.cli import main
from qazy.config import get_target, load_config, resolve_target
from qazy.runner import load_scenario, release_ports, reserve_port, start_managed_target, stop_managed_target, wait_for_target_ready, workspace_from_root


FAKE_AGENT_BROWSER = """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

log_path = os.environ.get("QAZY_FAKE_AGENT_BROWSER_LOG")
if log_path:
    with Path(log_path).open("a", encoding="utf-8") as handle:
        handle.write(" ".join(sys.argv[1:]) + "\\n")

if len(sys.argv) > 1 and sys.argv[1] == "snapshot":
    print("(fake snapshot)")
elif len(sys.argv) > 2 and sys.argv[1] == "screenshot":
    target = Path(sys.argv[2])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("fake screenshot", encoding="utf-8")
    print(str(target))
"""


FAKE_CLAUDE = """#!/usr/bin/env python3
import json

report = "PASS — example scenario\\n  Verified through example smoke run\\n1 passed, 0 failed, 0 untestable out of 1"

print(json.dumps({
    "type": "system",
    "subtype": "init",
    "model": "fake-claude",
    "session_id": "fake-session",
}))
print(json.dumps({
    "type": "assistant",
    "message": {
        "id": "msg-1",
        "content": [{"type": "text", "text": "Starting run"}],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 2,
            "cache_creation_input_tokens": 50,
            "cache_read_input_tokens": 20,
        },
    },
}))
print(json.dumps({
    "type": "assistant",
    "message": {
        "id": "msg-2",
        "content": [{"type": "text", "text": report}],
        "usage": {
            "input_tokens": 8,
            "output_tokens": 3,
            "cache_creation_input_tokens": 5,
            "cache_read_input_tokens": 2,
        },
    },
}))
print(json.dumps({
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "duration_ms": 123,
    "result": report,
    "total_cost_usd": 0.0,
}))
"""


def make_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class ExampleProjectsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.examples_dir = self.repo_root / "examples"

    def example_root(self, name: str) -> Path:
        return self.examples_dir / name

    def test_example_projects_have_valid_config_and_scenarios(self) -> None:
        cases = {
            "student-portal": "Student Login",
            "task-board": "Task Board Flow",
        }
        for example_name, expected_title in cases.items():
            root = self.example_root(example_name)
            config = load_config(root)
            target = get_target(config, None)
            workspace = workspace_from_root(root)

            self.assertEqual(target.mode, "managed")
            self.assertTrue((root / "app" / "index.html").exists())

            scenario_paths = sorted((root / "user-scenarios").glob("*.scenario.md"))
            self.assertEqual(len(scenario_paths), 1)
            scenario = load_scenario(workspace, f"user-scenarios/{scenario_paths[0].stem.removesuffix('.scenario')}")
            self.assertEqual(len(scenario.sections), 1)
            self.assertIn(expected_title, scenario.body)

    def test_example_projects_start_with_managed_target(self) -> None:
        expected_text = {
            "student-portal": "Student portal",
            "task-board": "Task board",
        }
        for example_name, title_text in expected_text.items():
            root = self.example_root(example_name)
            with tempfile.TemporaryDirectory() as tempdir:
                temp_root = Path(tempdir)
                workspace = workspace_from_root(
                    root,
                    results_dir=temp_root / "results",
                )
                config = load_config(root)
                target = get_target(config, None)
                resolved = resolve_target(target, allocate_port=reserve_port)
                server = None
                try:
                    server = start_managed_target(workspace, resolved, logs_dir=workspace.results_dir / "logs")
                    wait_for_target_ready(resolved.base_url, resolved.ready, process=server)
                    response = urllib.request.urlopen(f"{resolved.base_url}/index.html", timeout=5)
                    body = response.read().decode()
                    self.assertIn(title_text, body)
                finally:
                    stop_managed_target(server)
                    release_ports(*[port for port in (resolved.app_port, resolved.mongo_port) if port is not None])

    def test_example_project_runs_through_qazy_with_fake_runtime(self) -> None:
        root = self.example_root("student-portal")
        with tempfile.TemporaryDirectory() as tempdir:
            temp_root = Path(tempdir)
            bin_dir = temp_root / "bin"
            bin_dir.mkdir()
            make_executable(bin_dir / "agent-browser", FAKE_AGENT_BROWSER)
            make_executable(bin_dir / "claude", FAKE_CLAUDE)
            browser_log = temp_root / "agent-browser.log"
            results_dir = temp_root / "results"

            env_patch = patch.dict(
                os.environ,
                {
                    "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                    "QAZY_FAKE_AGENT_BROWSER_LOG": str(browser_log),
                },
                clear=False,
            )
            env_patch.start()
            self.addCleanup(env_patch.stop)

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "--project-root",
                        str(root),
                        "--results-dir",
                        str(results_dir),
                        "user-scenarios/login",
                    ]
                )

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn("Scenario:   user-scenarios/login", output)
            self.assertIn("Starting run", output)
            result_dir = next(results_dir.iterdir())
            result_file = result_dir / "user-scenarios--login.md"
            self.assertTrue(result_file.exists())
            result_text = result_file.read_text(encoding="utf-8")
            self.assertIn("PASS — example scenario", result_text)
            browser_commands = browser_log.read_text(encoding="utf-8")
            self.assertIn("open http://127.0.0.1:", browser_commands)
            self.assertIn("/index.html", browser_commands)
