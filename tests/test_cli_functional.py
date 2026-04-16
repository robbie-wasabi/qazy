from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from qazy.cli import main
from qazy.runner import browser_session_name, parse_sections


FAKE_PNPM = """#!/usr/bin/env python3
import json
import os
import signal
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

port = int(os.environ["PORT"])
expected_email = os.environ.get("QAZY_FAKE_AUTH_EMAIL", "tester@example.com")
expected_password = os.environ.get("QAZY_FAKE_AUTH_PASSWORD", "tester123")
session_token = os.environ.get("QAZY_FAKE_SESSION", "fake-session")

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/api/auth/csrf":
            body = json.dumps({"csrfToken": "csrf-token"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def do_POST(self):
        if self.path != "/api/auth/callback/credentials":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = urllib.parse.parse_qs(self.rfile.read(length).decode())
        email = payload.get("email", [""])[0]
        password = payload.get("password", [""])[0]
        if email == expected_email and password == expected_password:
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Set-Cookie", f"next-auth.session-token={session_token}; Path=/")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(401)
        self.end_headers()

server = HTTPServer(("127.0.0.1", port), Handler)

def shutdown(*_args):
    server.shutdown()

signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)
print(f"http://localhost:{port}", flush=True)
server.serve_forever()
"""


FAKE_AGENT_BROWSER = """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

log_path = os.environ.get("QAZY_FAKE_AGENT_BROWSER_LOG")
if log_path:
    with Path(log_path).open("a", encoding="utf-8") as handle:
        headed = os.environ.get("AGENT_BROWSER_HEADED")
        if headed is not None:
            handle.write(f"ENV AGENT_BROWSER_HEADED={headed}\\n")
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
import os
import subprocess
import sys

prompt = sys.stdin.read()
label = os.environ.get("QAZY_FAKE_TAKE_SCREENSHOT_LABEL", "").strip()
if label:
    result = subprocess.run(["qazy-shot", label], capture_output=True, text=True)
    if result.returncode != 0:
        print(json.dumps({"type": "error", "error": result.stderr.strip() or result.stdout.strip() or "screenshot failed"}))
        sys.exit(result.returncode or 1)
if "EXPECT_FAIL" in prompt:
    report = "FAIL — EXPECT_FAIL\\n  Simulated failure\\n0 passed, 1 failed, 0 untestable out of 1"
else:
    report = os.environ.get(
        "QAZY_FAKE_CLAUDE_REPORT",
        "PASS — happy path\\n  Verified\\n1 passed, 0 failed, 0 untestable out of 1",
    )

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
            "cache_creation_input_tokens": 100,
            "cache_read_input_tokens": 50,
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
    "total_cost_usd": 0.1234,
}))
"""


FAKE_CODEX = """#!/usr/bin/env python3
import json
import sys

prompt = sys.stdin.read()
if "EXPECT_FAIL" in prompt:
    report = "FAIL — EXPECT_FAIL\\n  Simulated failure\\n0 passed, 1 failed, 0 untestable out of 1"
else:
    report = "PASS — codex path\\n  Verified through Codex\\n1 passed, 0 failed, 0 untestable out of 1"

print(json.dumps({"type": "thread.started", "thread_id": "fake-thread"}))
print(json.dumps({"type": "turn.started"}))
print(json.dumps({
    "type": "item.completed",
    "item": {
        "id": "item-1",
        "type": "agent_message",
        "text": report,
    },
}))
print(json.dumps({
    "type": "turn.completed",
    "usage": {
        "input_tokens": 21,
        "cached_input_tokens": 7,
        "output_tokens": 4,
    },
}))
"""


def make_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class QazyCliFunctionalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        (self.root / "user-scenarios").mkdir()
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        make_executable(self.bin_dir / "pnpm", FAKE_PNPM)
        make_executable(self.bin_dir / "agent-browser", FAKE_AGENT_BROWSER)
        make_executable(self.bin_dir / "claude", FAKE_CLAUDE)
        make_executable(self.bin_dir / "codex", FAKE_CODEX)
        self.browser_log = self.root / "agent-browser.log"
        self.env_patch = patch.dict(
            os.environ,
            {
                "PATH": f"{self.bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                "QAZY_FAKE_AGENT_BROWSER_LOG": str(self.browser_log),
            },
            clear=False,
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.write_config(self.default_config_payload())

    def default_config_payload(self) -> dict[str, object]:
        return {
            "version": 1,
            "defaultTarget": "local-mem",
            "targets": {
                "local-mem": {
                    "mode": "managed",
                    "baseUrl": "http://localhost:{appPort}",
                    "devCommand": "pnpm dev:mem",
                    "ports": {"appPort": "auto", "mongoPort": "auto"},
                    "env": {
                        "PORT": "{appPort}",
                        "MONGO_PORT": "{mongoPort}",
                    },
                    "parallelSafe": True,
                }
            },
        }

    def write_scenario(self, relative_path: str, content: str) -> Path:
        path = self.root / "user-scenarios" / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
        return path

    def write_config(self, payload: dict[str, object]) -> Path:
        path = self.root / "qazy.config.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def start_attached_server(self, port: int) -> subprocess.Popen[str]:
        process = subprocess.Popen(
            [str(self.bin_dir / "pnpm")],
            env={**os.environ, "PORT": str(port)},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        assert process.stdout is not None
        process.stdout.readline()

        def cleanup() -> None:
            if process.stdout is not None and not process.stdout.closed:
                process.stdout.close()
            if process.poll() is not None:
                return
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

        self.addCleanup(cleanup)
        return process

    def free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
            handle.bind(("127.0.0.1", 0))
            return int(handle.getsockname()[1])

    def test_run_command_writes_results(self) -> None:
        self.write_scenario(
            "login-test.scenario.md",
            """
            ---
            email: tester@example.com
            password: tester123
            start_page: /login
            use_cookie: false
            ---

            # Login Test

            ## list
            - [ ] user can log in
            """,
        )

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["run", "--project-root", str(self.root), "user-scenarios/login-test"])

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("Starting run", output)
        self.assertIn("[done]", output)
        self.assertIn("Total tokens: 180 total", output)
        result_dirs = list((self.root / "user-scenarios-results").iterdir())
        self.assertEqual(len(result_dirs), 1)
        result_file = result_dirs[0] / "user-scenarios--login-test.md"
        self.assertTrue(result_file.exists())
        content = result_file.read_text(encoding="utf-8")
        self.assertIn("**Target**: local-mem (managed)", content)
        self.assertIn("**Runtime**: claude", content)
        self.assertIn("**Tokens**: 180 total", content)
        self.assertIn("PASS — happy path", content)
        logs = list((self.root / ".qazy" / "logs").glob("claude-*.log"))
        self.assertEqual(len(logs), 1)

    def test_direct_scenario_invocation_writes_results(self) -> None:
        self.write_scenario(
            "direct-login.scenario.md",
            """
            ---
            email: tester@example.com
            password: tester123
            start_page: /login
            use_cookie: false
            ---

            ## list
            - [ ] direct invocation works
            """,
        )

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["user-scenarios/direct-login", "--project-root", str(self.root)])

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("Scenario:   user-scenarios/direct-login", output)
        result_file = next((self.root / "user-scenarios-results").iterdir()) / "user-scenarios--direct-login.md"
        self.assertTrue(result_file.exists())

    def test_run_command_supports_results_dir_override(self) -> None:
        self.write_scenario(
            "custom-results.scenario.md",
            """
            ---
            email: tester@example.com
            password: tester123
            start_page: /login
            use_cookie: false
            ---

            ## list
            - [ ] custom results dir is used
            """,
        )
        custom_results_dir = self.root / "tmp-results"

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(
                [
                    "run",
                    "--project-root",
                    str(self.root),
                    "--results-dir",
                    str(custom_results_dir),
                    "user-scenarios/custom-results",
                ]
            )

        self.assertEqual(exit_code, 0)
        result_dirs = list(custom_results_dir.iterdir())
        self.assertEqual(len(result_dirs), 1)
        result_file = result_dirs[0] / "user-scenarios--custom-results.md"
        self.assertTrue(result_file.exists())
        self.assertFalse((self.root / "user-scenarios-results").exists())

    def test_run_command_uses_results_dir_from_config(self) -> None:
        self.write_scenario(
            "config-results.scenario.md",
            """
            ---
            email: tester@example.com
            password: tester123
            start_page: /login
            use_cookie: false
            ---

            ## list
            - [ ] config results dir is used
            """,
        )
        self.write_config(
            {
                "version": 1,
                "resultsDir": "configured-results",
                "defaultTarget": "local-mem",
                "targets": self.default_config_payload()["targets"],
            }
        )

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["run", "--project-root", str(self.root), "user-scenarios/config-results"])

        self.assertEqual(exit_code, 0)
        result_dirs = list((self.root / "configured-results").iterdir())
        self.assertEqual(len(result_dirs), 1)
        result_file = result_dirs[0] / "user-scenarios--config-results.md"
        self.assertTrue(result_file.exists())
        self.assertFalse((self.root / "user-scenarios-results").exists())

    def test_run_command_cli_results_dir_overrides_config(self) -> None:
        self.write_scenario(
            "config-results-override.scenario.md",
            """
            ---
            email: tester@example.com
            password: tester123
            start_page: /login
            use_cookie: false
            ---

            ## list
            - [ ] cli results dir wins over config
            """,
        )
        self.write_config(
            {
                "version": 1,
                "resultsDir": "configured-results",
                "defaultTarget": "local-mem",
                "targets": self.default_config_payload()["targets"],
            }
        )
        custom_results_dir = self.root / "cli-results"

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(
                [
                    "run",
                    "--project-root",
                    str(self.root),
                    "--results-dir",
                    str(custom_results_dir),
                    "user-scenarios/config-results-override",
                ]
            )

        self.assertEqual(exit_code, 0)
        result_dirs = list(custom_results_dir.iterdir())
        self.assertEqual(len(result_dirs), 1)
        result_file = result_dirs[0] / "user-scenarios--config-results-override.md"
        self.assertTrue(result_file.exists())
        self.assertFalse((self.root / "configured-results").exists())

    def test_run_command_authenticates_and_sets_cookie(self) -> None:
        self.write_scenario(
            "auth-test.scenario.md",
            """
            ---
            email: tester@example.com
            password: tester123
            start_page: /dashboard
            use_cookie: true
            ---

            # Auth Test

            ## list
            - [ ] dashboard loads
            """,
        )

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["run", "--project-root", str(self.root), "user-scenarios/auth-test"])

        self.assertEqual(exit_code, 0)
        browser_commands = self.browser_log.read_text(encoding="utf-8")
        self.assertIn("cookies set next-auth.session-token fake-session", browser_commands)

    def test_prompt_mode_uses_target_scenario_defaults(self) -> None:
        config = self.default_config_payload()
        config["targets"]["local-mem"]["scenarioDefaults"] = {
            "email": "tester@example.com",
            "password": "tester123",
            "startPage": "/student-login",
            "useCookie": True,
        }
        self.write_config(config)

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["--project-root", str(self.root), "-p", "test login flow for student"])

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("Scenario:   prompt/test-login-flow-for-student", output)
        browser_commands = self.browser_log.read_text(encoding="utf-8")
        self.assertIn("cookies set next-auth.session-token fake-session", browser_commands)
        self.assertIn("/student-login", browser_commands)
        result_file = next(next((self.root / "user-scenarios-results").iterdir()).glob("prompt--*.md"))
        content = result_file.read_text(encoding="utf-8")
        self.assertIn("**Email**: tester@example.com", content)

    def test_prompt_mode_cli_overrides_target_scenario_defaults(self) -> None:
        config = self.default_config_payload()
        config["targets"]["local-mem"]["scenarioDefaults"] = {
            "email": "wrong@example.com",
            "password": "wrong-password",
            "startPage": "/wrong",
            "useCookie": True,
        }
        self.write_config(config)

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(
                [
                    "--project-root",
                    str(self.root),
                    "-p",
                    "test login flow for student",
                    "--email",
                    "tester@example.com",
                    "--password",
                    "tester123",
                    "--start-page",
                    "/override",
                    "--use-cookie",
                ]
            )

        self.assertEqual(exit_code, 0)
        browser_commands = self.browser_log.read_text(encoding="utf-8")
        self.assertIn("cookies set next-auth.session-token fake-session", browser_commands)
        self.assertIn("/override", browser_commands)
        result_file = next(next((self.root / "user-scenarios-results").iterdir()).glob("prompt--*.md"))
        content = result_file.read_text(encoding="utf-8")
        self.assertIn("**Email**: tester@example.com", content)

    def test_target_scenario_defaults_apply_without_overriding_explicit_frontmatter(self) -> None:
        self.write_scenario(
            "target-defaults.scenario.md",
            """
            ---
            use_cookie: false
            start_page: /frontmatter
            ---

            ## list
            - [ ] target defaults fill missing creds only
            """,
        )
        config = self.default_config_payload()
        config["targets"]["local-mem"]["scenarioDefaults"] = {
            "email": "student@example.com",
            "password": "tester123",
            "startPage": "/configured",
            "useCookie": True,
        }
        self.write_config(config)

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["run", "--project-root", str(self.root), "user-scenarios/target-defaults"])

        self.assertEqual(exit_code, 0)
        browser_commands = self.browser_log.read_text(encoding="utf-8")
        self.assertNotIn("cookies set next-auth.session-token fake-session", browser_commands)
        self.assertIn("/frontmatter", browser_commands)
        self.assertNotIn("/configured", browser_commands)
        result_file = next((self.root / "user-scenarios-results").iterdir()) / "user-scenarios--target-defaults.md"
        content = result_file.read_text(encoding="utf-8")
        self.assertIn("**Email**: student@example.com", content)

    def test_run_command_frontmatter_overrides_apply_from_cli(self) -> None:
        self.write_scenario(
            "override-test.scenario.md",
            """
            ---
            email: wrong@example.com
            password: wrong-password
            start_page: /frontmatter
            use_cookie: false
            ---

            ## list
            - [ ] cli overrides win
            """,
        )

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(
                [
                    "run",
                    "--project-root",
                    str(self.root),
                    "--email",
                    "tester@example.com",
                    "--password",
                    "tester123",
                    "--start-page",
                    "/override",
                    "--use-cookie",
                    "user-scenarios/override-test",
                ]
            )

        self.assertEqual(exit_code, 0)
        browser_commands = self.browser_log.read_text(encoding="utf-8")
        self.assertIn("cookies set next-auth.session-token fake-session", browser_commands)
        self.assertIn("open http://localhost:", browser_commands)
        self.assertIn("/override", browser_commands)
        result_file = next((self.root / "user-scenarios-results").iterdir()) / "user-scenarios--override-test.md"
        content = result_file.read_text(encoding="utf-8")
        self.assertIn("**Email**: tester@example.com", content)

    def test_run_command_frontmatter_overrides_apply_to_all_sections(self) -> None:
        self.write_scenario(
            "multi-override.scenario.md",
            """
            ---
            email: wrong@example.com
            password: wrong-password
            start_page: /frontmatter-a
            use_cookie: false
            ---

            ## list
            - [ ] first section

            ---
            email: also-wrong@example.com
            password: also-wrong-password
            start_page: /frontmatter-b
            use_cookie: false
            ---

            ## list
            - [ ] second section
            """,
        )

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(
                [
                    "run",
                    "--project-root",
                    str(self.root),
                    "--email",
                    "tester@example.com",
                    "--password",
                    "tester123",
                    "--start-page",
                    "/override",
                    "--use-cookie",
                    "user-scenarios/multi-override",
                ]
            )

        self.assertEqual(exit_code, 0)
        browser_commands = self.browser_log.read_text(encoding="utf-8")
        self.assertEqual(browser_commands.count("cookies set next-auth.session-token fake-session"), 2)
        self.assertGreaterEqual(browser_commands.count("/override"), 2)

    def test_run_command_supports_codex_runtime(self) -> None:
        self.write_scenario(
            "codex-test.scenario.md",
            """
            ---
            email: tester@example.com
            password: tester123
            start_page: /login
            use_cookie: false
            ---

            ## list
            - [ ] codex can run the scenario
            """,
        )

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["run", "--project-root", str(self.root), "--runtime", "codex", "user-scenarios/codex-test"])

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("init (thread=fake-thread)", output)
        self.assertIn("PASS — codex path", output)
        self.assertIn("[done]", output)
        self.assertIn("Total tokens: 32 total", output)
        result_dirs = list((self.root / "user-scenarios-results").iterdir())
        self.assertEqual(len(result_dirs), 1)
        result_file = result_dirs[0] / "user-scenarios--codex-test.md"
        content = result_file.read_text(encoding="utf-8")
        self.assertIn("**Target**: local-mem (managed)", content)
        self.assertIn("**Runtime**: codex", content)
        self.assertIn("**Tokens**: 32 total", content)
        self.assertIn("PASS — codex path", content)
        logs = list((self.root / ".qazy" / "logs").glob("codex-*.log"))
        self.assertEqual(len(logs), 1)

    def test_run_command_default_error_strategy_allows_named_error_screenshots(self) -> None:
        self.write_scenario(
            "shot-test.scenario.md",
            """
            ---
            email: tester@example.com
            password: tester123
            start_page: /login
            use_cookie: false
            ---

            ## notes
            - Take a screenshot after login.

            ## list
            - [ ] screenshot can be captured
            """,
        )

        with patch.dict(os.environ, {"QAZY_FAKE_TAKE_SCREENSHOT_LABEL": "after-login"}, clear=False):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "run",
                        "--project-root",
                        str(self.root),
                        "user-scenarios/shot-test",
                    ]
                )

        self.assertEqual(exit_code, 0)
        result_dir = next((self.root / "user-scenarios-results").iterdir())
        screenshot_dir = result_dir / "screenshots"
        screenshots = sorted(screenshot_dir.glob("*.png"))
        self.assertEqual(len(screenshots), 1)
        self.assertIn("after-login", screenshots[0].name)
        content = (result_dir / "user-scenarios--shot-test.md").read_text(encoding="utf-8")
        self.assertIn("## Screenshots", content)
        self.assertIn(f"- screenshots/{screenshots[0].name}", content)
        self.assertIn("Screenshots: 1 saved", stdout.getvalue())

    def test_run_command_single_strategy_saves_one_final_screenshot(self) -> None:
        self.write_scenario(
            "single-shot.scenario.md",
            """
            ---
            email: tester@example.com
            password: tester123
            start_page: /login
            use_cookie: false
            ---

            ## list
            - [ ] single screenshot mode captures final state
            """,
        )

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(
                [
                    "run",
                    "--project-root",
                    str(self.root),
                    "--screenshot-strategy",
                    "single",
                    "user-scenarios/single-shot",
                ]
            )

        self.assertEqual(exit_code, 0)
        result_dir = next((self.root / "user-scenarios-results").iterdir())
        screenshot_dir = result_dir / "screenshots"
        screenshots = sorted(screenshot_dir.glob("*.png"))
        self.assertEqual(len(screenshots), 1)
        self.assertIn("final", screenshots[0].name)
        content = (result_dir / "user-scenarios--single-shot.md").read_text(encoding="utf-8")
        self.assertIn(f"- screenshots/{screenshots[0].name}", content)
        self.assertIn("Screenshots: 1 saved", stdout.getvalue())

    def test_run_command_headed_sets_agent_browser_env(self) -> None:
        self.write_scenario(
            "headed.scenario.md",
            """
            ---
            email: tester@example.com
            password: tester123
            start_page: /login
            use_cookie: false
            ---

            ## list
            - [ ] headed mode is enabled
            """,
        )

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(
                [
                    "run",
                    "--project-root",
                    str(self.root),
                    "--headed",
                    "user-scenarios/headed",
                ]
            )

        self.assertEqual(exit_code, 0)
        browser_commands = self.browser_log.read_text(encoding="utf-8")
        self.assertIn("ENV AGENT_BROWSER_HEADED=true", browser_commands)

    def test_batch_marks_failed_reports_as_failures(self) -> None:
        self.write_scenario(
            "pass.scenario.md",
            """
            ---
            email: tester@example.com
            password: tester123
            start_page: /login
            use_cookie: false
            ---

            ## list
            - [ ] pass item
            """,
        )
        self.write_scenario(
            "fail.scenario.md",
            """
            ---
            email: tester@example.com
            password: tester123
            start_page: /login
            use_cookie: false
            ---

            ## list
            - [ ] EXPECT_FAIL
            """,
        )

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["batch", "--project-root", str(self.root), "user-scenarios"])

        self.assertEqual(exit_code, 1)
        summary_file = next((self.root / "user-scenarios-results").iterdir()) / "summary.md"
        summary = summary_file.read_text(encoding="utf-8")
        self.assertIn("- user-scenarios/pass", summary)
        self.assertIn("- user-scenarios/fail", summary)
        self.assertIn("Failed: 1", summary)

    def test_direct_directory_invocation_runs_batch(self) -> None:
        self.write_scenario(
            "direct-pass.scenario.md",
            """
            ---
            email: tester@example.com
            password: tester123
            start_page: /login
            use_cookie: false
            ---

            ## list
            - [ ] direct batch pass item
            """,
        )
        self.write_scenario(
            "direct-fail.scenario.md",
            """
            ---
            email: tester@example.com
            password: tester123
            start_page: /login
            use_cookie: false
            ---

            ## list
            - [ ] EXPECT_FAIL
            """,
        )

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["user-scenarios", "--project-root", str(self.root)])

        self.assertEqual(exit_code, 1)
        output = stdout.getvalue()
        self.assertIn("Found 2 scenarios matching 'user-scenarios' (sequential):", output)
        summary_file = next((self.root / "user-scenarios-results").iterdir()) / "summary.md"
        self.assertTrue(summary_file.exists())

    def test_batch_command_supports_results_dir_override(self) -> None:
        self.write_scenario(
            "batch-results.scenario.md",
            """
            ---
            email: tester@example.com
            password: tester123
            start_page: /login
            use_cookie: false
            ---

            ## list
            - [ ] batch results dir is used
            """,
        )
        custom_results_dir = self.root / "tmp-batch-results"

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(
                [
                    "batch",
                    "--project-root",
                    str(self.root),
                    "--results-dir",
                    str(custom_results_dir),
                    "user-scenarios/batch-results",
                ]
            )

        self.assertEqual(exit_code, 0)
        result_dirs = list(custom_results_dir.iterdir())
        self.assertEqual(len(result_dirs), 1)
        summary_file = result_dirs[0] / "summary.md"
        self.assertTrue(summary_file.exists())
        self.assertFalse((self.root / "user-scenarios-results").exists())

    def test_rename_scenarios_dry_run_and_write(self) -> None:
        legacy = self.root / "user-scenarios" / "pages" / "work-orders" / "tsi-user"
        legacy.mkdir(parents=True)
        (legacy / "list.md").write_text("# legacy", encoding="utf-8")

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            dry_run_exit = main(["rename-scenarios", "--project-root", str(self.root)])
        self.assertEqual(dry_run_exit, 0)
        self.assertTrue((legacy / "list.md").exists())

        with contextlib.redirect_stdout(io.StringIO()):
            write_exit = main(["rename-scenarios", "--project-root", str(self.root), "--write"])
        self.assertEqual(write_exit, 0)
        self.assertFalse((legacy / "list.md").exists())
        self.assertTrue((self.root / "user-scenarios" / "pages" / "work-orders.tsi-user.scenario.md").exists())

    def test_main_help_is_agent_friendly(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["--help"])

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("Agent-driven browser QA runner", output)
        self.assertIn("Minimal qazy.config.json", output)
        self.assertIn("Authentication:", output)
        self.assertIn("Limitations:", output)
        self.assertIn("qazy help config", output)

    def test_help_run_includes_scenario_format_guidance(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["help", "run"])

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("--screenshot-strategy", output)
        self.assertIn("Minimal Scenario File:", output)
        self.assertIn("Multi-section Scenarios:", output)
        self.assertIn("target.scenarioDefaults can fill missing values", output)

    def test_help_config_covers_target_defaults_and_modes(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["help", "config"])

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("Qazy looks for qazy.config.json", output)
        self.assertIn("managed | attached", output)
        self.assertIn("scenarioDefaults", output)
        self.assertIn("CLI overrides > scenario frontmatter > target.scenarioDefaults", output)

    def test_unknown_help_topic_lists_available_topics(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["help", "wat"])

        self.assertEqual(exit_code, 1)
        output = stdout.getvalue()
        self.assertIn("Unknown help topic: wat", output)
        self.assertIn("Available topics:", output)
        self.assertIn("rename-scenarios", output)

    def test_run_multi_section_shared_server_all_pass(self) -> None:
        self.write_scenario(
            "multi-pass.scenario.md",
            """
            ---
            email: tester@example.com
            password: tester123
            start_page: /dashboard
            use_cookie: true
            ---

            # Section 1

            ## list
            - [ ] section 1 item

            ---
            email: tester@example.com
            password: tester123
            start_page: /dashboard
            use_cookie: true
            ---

            # Section 2

            ## list
            - [ ] section 2 item
            """,
        )

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["run", "--project-root", str(self.root), "user-scenarios/multi-pass"])

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()

        # Shared target: only one startup message
        server_starts = [line for line in output.splitlines() if "starting target" in line]
        self.assertEqual(len(server_starts), 1, f"Expected 1 server start, got {len(server_starts)}")

        # Result files: per-section + combined
        result_dirs = list((self.root / "user-scenarios-results").iterdir())
        self.assertEqual(len(result_dirs), 1)
        result_dir = result_dirs[0]
        self.assertTrue((result_dir / "user-scenarios--multi-pass-s0.md").exists())
        self.assertTrue((result_dir / "user-scenarios--multi-pass-s1.md").exists())
        self.assertTrue((result_dir / "user-scenarios--multi-pass.md").exists())

        combined = (result_dir / "user-scenarios--multi-pass.md").read_text(encoding="utf-8")
        self.assertIn("PASSED", combined)
        self.assertIn("**Tokens**: 360 total", combined)

    def test_run_multi_section_failure_continues(self) -> None:
        self.write_scenario(
            "multi-fail.scenario.md",
            """
            ---
            email: tester@example.com
            password: tester123
            start_page: /dashboard
            use_cookie: true
            ---

            # Section 1 — should fail

            ## list
            - [ ] EXPECT_FAIL

            ---
            email: tester@example.com
            password: tester123
            start_page: /dashboard
            use_cookie: true
            ---

            # Section 2 — should pass

            ## list
            - [ ] section 2 item
            """,
        )

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["run", "--project-root", str(self.root), "user-scenarios/multi-fail"])

        self.assertEqual(exit_code, 1)

        result_dirs = list((self.root / "user-scenarios-results").iterdir())
        result_dir = result_dirs[0]

        # Both sections ran despite section 0 failing
        s0 = result_dir / "user-scenarios--multi-fail-s0.md"
        s1 = result_dir / "user-scenarios--multi-fail-s1.md"
        self.assertTrue(s0.exists())
        self.assertTrue(s1.exists())

        self.assertIn("FAILED", s0.read_text(encoding="utf-8"))
        self.assertIn("PASSED", s1.read_text(encoding="utf-8"))

        # Combined status is failed
        combined = (result_dir / "user-scenarios--multi-fail.md").read_text(encoding="utf-8")
        self.assertIn("FAILED", combined)

    def test_run_command_uses_attached_target_without_starting_server(self) -> None:
        self.write_scenario(
            "attached.scenario.md",
            """
            ---
            email: tester@example.com
            password: tester123
            start_page: /login
            use_cookie: false
            ---

            ## list
            - [ ] attached target can run
            """,
        )
        port = self.free_port()
        self.start_attached_server(port)
        self.write_config(
            {
                "version": 1,
                "defaultTarget": "local",
                "targets": {
                    "local": {
                        "mode": "managed",
                        "baseUrl": "http://localhost:{appPort}",
                        "devCommand": "pnpm dev:mem",
                        "ports": {"appPort": "auto", "mongoPort": "auto"},
                        "env": {
                            "PORT": "{appPort}",
                            "MONGO_PORT": "{mongoPort}",
                        },
                        "parallelSafe": True,
                    },
                    "dev-remote": {
                        "mode": "attached",
                        "baseUrl": f"http://localhost:{port}",
                        "parallelSafe": False,
                    },
                },
            }
        )

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(
                [
                    "run",
                    "--project-root",
                    str(self.root),
                    "--target",
                    "dev-remote",
                    "user-scenarios/attached",
                ]
            )

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertNotIn("starting target", output)
        server_logs = list((self.root / ".qazy" / "logs").glob("server-*.log"))
        self.assertEqual(server_logs, [])
        result_file = next((self.root / "user-scenarios-results").iterdir()) / "user-scenarios--attached.md"
        content = result_file.read_text(encoding="utf-8")
        self.assertIn("**Target**: dev-remote (attached)", content)
        self.assertIn(f"**Server**: http://localhost:{port}", content)

    def test_batch_parallel_rejected_for_non_parallel_safe_target(self) -> None:
        self.write_scenario(
            "parallel.scenario.md",
            """
            ---
            email: tester@example.com
            password: tester123
            start_page: /login
            use_cookie: false
            ---

            ## list
            - [ ] batch item
            """,
        )
        self.write_config(
            {
                "version": 1,
                "defaultTarget": "dev-remote",
                "targets": {
                    "dev-remote": {
                        "mode": "attached",
                        "baseUrl": "https://dev.complora.com",
                        "parallelSafe": False,
                    }
                },
            }
        )

        with self.assertRaisesRegex(RuntimeError, "does not support parallel"):
            main(["batch", "--project-root", str(self.root), "--parallel", "user-scenarios"])

    def test_run_command_requires_config_file(self) -> None:
        self.write_scenario(
            "no-config.scenario.md",
            """
            ---
            email: tester@example.com
            password: tester123
            start_page: /login
            use_cookie: false
            ---

            ## list
            - [ ] config is required
            """,
        )
        (self.root / "qazy.config.json").unlink()

        with self.assertRaisesRegex(FileNotFoundError, "Create qazy.config.json"):
            main(["run", "--project-root", str(self.root), "user-scenarios/no-config"])


class QazyParseSectionsTests(unittest.TestCase):
    def test_single_section_backward_compat(self) -> None:
        content = textwrap.dedent("""\
            ---
            email: tester@example.com
            password: tester123
            start_page: /dashboard
            use_cookie: true
            ---

            # Single Section

            - [ ] check item
        """)
        sections = parse_sections(content)
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0]["email"], "tester@example.com")
        self.assertEqual(sections[0]["password"], "tester123")
        self.assertEqual(sections[0]["start_page"], "/dashboard")
        self.assertTrue(sections[0]["use_cookie"])
        self.assertIn("check item", str(sections[0]["body"]))

    def test_multi_section_parsing(self) -> None:
        content = textwrap.dedent("""\
            ---
            email: user1@test.com
            password: pass1
            start_page: /page1
            use_cookie: true
            ---

            # Section 1

            - [ ] check 1

            ---
            email: user2@test.com
            password: pass2
            start_page: /page2
            use_cookie: false
            ---

            # Section 2

            - [ ] check 2
        """)
        sections = parse_sections(content)
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0]["email"], "user1@test.com")
        self.assertEqual(sections[0]["password"], "pass1")
        self.assertEqual(sections[0]["start_page"], "/page1")
        self.assertTrue(sections[0]["use_cookie"])
        self.assertIn("check 1", str(sections[0]["body"]))
        self.assertEqual(sections[1]["email"], "user2@test.com")
        self.assertEqual(sections[1]["password"], "pass2")
        self.assertEqual(sections[1]["start_page"], "/page2")
        self.assertFalse(sections[1]["use_cookie"])
        self.assertIn("check 2", str(sections[1]["body"]))

    def test_frontmatter_without_credentials_is_allowed(self) -> None:
        content = textwrap.dedent("""\
            ---
            start_page: /login
            use_cookie: false
            ---

            ## notes

            login with:

            ```
            email: josephine.hamill@example.com
            password: tester123
            ```

            ## list

            - [ ] admin can log in
        """)
        sections = parse_sections(content)
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0]["email"], "")
        self.assertEqual(sections[0]["password"], "")
        self.assertEqual(sections[0]["start_page"], "/login")
        self.assertFalse(sections[0]["use_cookie"])
        self.assertIn("josephine.hamill@example.com", str(sections[0]["body"]))

    def test_horizontal_rule_in_body_not_treated_as_boundary(self) -> None:
        content = textwrap.dedent("""\
            ---
            email: tester@example.com
            password: tester123
            start_page: /dashboard
            use_cookie: true
            ---

            # Section with HR

            Above the rule

            ---

            Below the rule

            - [ ] check item
        """)
        sections = parse_sections(content)
        self.assertEqual(len(sections), 1)
        body = str(sections[0]["body"])
        self.assertIn("Above the rule", body)
        self.assertIn("Below the rule", body)
        self.assertIn("---", body)

    def test_three_section_tenant_isolation_format(self) -> None:
        content = textwrap.dedent("""\
            ---
            email: user1@test.com
            password: tester123
            start_page: /dashboard
            use_cookie: true
            ---

            # Tenant 1

            - [ ] tenant 1 check

            ---
            email: user2@test.com
            password: tester123
            start_page: /dashboard
            use_cookie: true
            ---

            # Tenant 2

            - [ ] tenant 2 check

            ---
            email: user3@test.com
            password: tester123
            start_page: /dashboard
            use_cookie: true
            ---

            # Tenant 3

            - [ ] tenant 3 check
        """)
        sections = parse_sections(content)
        self.assertEqual(len(sections), 3)
        self.assertEqual(sections[0]["email"], "user1@test.com")
        self.assertEqual(sections[1]["email"], "user2@test.com")
        self.assertEqual(sections[2]["email"], "user3@test.com")
        self.assertIn("tenant 1 check", str(sections[0]["body"]))
        self.assertIn("tenant 2 check", str(sections[1]["body"]))
        self.assertIn("tenant 3 check", str(sections[2]["body"]))


class QazyRunnerHelpersTests(unittest.TestCase):
    def test_browser_session_name_stays_short_for_long_scenario_paths(self) -> None:
        name = browser_session_name(
            "heath-drift-trail",
            "user-scenarios/page/work-orders/work-orders.account-admin-no-tools",
        )
        self.assertLessEqual(len(name), 60)
        self.assertTrue(name.startswith("qz-"))
