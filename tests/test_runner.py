from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qazy.config import ReadyCheck, ResolvedTarget, ScenarioDefaults, TargetDefinition
from qazy.runner import (
    Scenario,
    ScenarioSection,
    Workspace,
    _run_prepared_scenario,
    _run_single_section,
    build_prompt,
    wait_for_target_ready,
    workspace_from_root,
)
from qazy.runtimes import RuntimeAdapter, RuntimeInvocation


class StubRuntime(RuntimeAdapter):
    name = "stub"
    executable = "stub"

    def build_command(
        self,
        prompt: str,
        *,
        cwd: Path,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ):
        raise AssertionError("build_command should not be called in this test")


class RunnerPromptTests(unittest.TestCase):
    def test_workspace_defaults_use_qazy_output_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)

            workspace = workspace_from_root(root)

        self.assertEqual(workspace.results_dir, (root / ".qazy" / "results").resolve())
        self.assertEqual(workspace.logs_dir, (root / ".qazy" / "logs").resolve())

    def test_wait_for_target_ready_explains_how_to_fix_missing_app(self) -> None:
        ready = ReadyCheck(type="http", path="/", timeout_seconds=0)

        with self.assertRaises(RuntimeError) as error:
            wait_for_target_ready("http://localhost:3000", ready)

        error_text = str(error.exception)
        self.assertIn("Target at http://localhost:3000 not responding after 0s", error_text)
        self.assertIn("The app may not be running", error_text)
        self.assertIn("configure a managed target in qazy.config.json", error_text)
        self.assertIn("pass --dev-command", error_text)

    def test_build_prompt_includes_manual_login_credentials(self) -> None:
        prompt = build_prompt(
            "## list\n- [ ] verify account settings",
            base_url="http://127.0.0.1:3000",
            start_page="/login",
            email="tester@example.com",
            password="tester123",
            primed=False,
            screenshot_strategy="none",
        )

        self.assertIn("Use these scenario credentials", prompt)
        self.assertIn("Email: tester@example.com", prompt)
        self.assertIn("Password: tester123", prompt)
        self.assertIn("## Scenario Credentials", prompt)
        self.assertLess(
            prompt.index("## Scenario Credentials"),
            prompt.index("## list"),
        )

    def test_build_prompt_instructs_runtime_not_to_search_for_missing_credentials(self) -> None:
        prompt = build_prompt(
            "## list\n- [ ] verify account settings",
            base_url="http://127.0.0.1:3000",
            start_page="/login",
            email="",
            password="",
            primed=False,
            screenshot_strategy="none",
        )

        self.assertIn("No complete scenario credentials were provided", prompt)
        self.assertIn("Do not search project files", prompt)
        self.assertIn("environment variables", prompt)
        self.assertIn("report the item as UNTESTABLE", prompt)
        self.assertIn("## Scenario Credentials", prompt)
        self.assertLess(
            prompt.index("## Scenario Credentials"),
            prompt.index("## list"),
        )

    def test_build_prompt_does_not_include_credentials_when_primed(self) -> None:
        prompt = build_prompt(
            "## list\n- [ ] verify account settings",
            base_url="http://127.0.0.1:3000",
            start_page="/dashboard",
            email="tester@example.com",
            password="tester123",
            primed=True,
            screenshot_strategy="none",
        )

        self.assertNotIn("## Scenario Credentials", prompt)
        self.assertNotIn("tester@example.com", prompt)
        self.assertNotIn("tester123", prompt)

    def test_run_single_section_threads_credentials_into_build_prompt(self) -> None:
        section = ScenarioSection(
            index=0,
            label="tester@example.com",
            email="tester@example.com",
            password="tester123",
            start_page="/login",
            use_cookie=False,
            auth_provider="nextauth",
            auth_cookie_prefix="better-auth",
            auth_base_path="/api/auth",
            body="## list\n- [ ] verify manual login",
        )
        scenario = Scenario(
            path="user-scenarios/manual-login",
            file_path=Path("/tmp/manual-login.scenario.md"),
            email=section.email,
            password=section.password,
            start_page=section.start_page,
            use_cookie=section.use_cookie,
            auth_provider=section.auth_provider,
            auth_cookie_prefix=section.auth_cookie_prefix,
            auth_base_path=section.auth_base_path,
            body=section.body,
            raw_content="",
            sections=[section],
        )
        target = ResolvedTarget(
            name="default",
            mode="attached",
            base_url="http://127.0.0.1:3000",
            dev_command=None,
            env={},
            app_port=None,
            mongo_port=None,
            ready=ReadyCheck(type="http", path="/", timeout_seconds=60),
            parallel_safe=False,
            scenario_defaults=ScenarioDefaults(),
        )
        runtime = StubRuntime()

        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            workspace = Workspace(
                project_root=root,
                scenarios_dir=root / "user-scenarios",
                results_dir=root / ".qazy/results",
                logs_dir=root / ".qazy" / "logs",
            )

            with (
                patch("qazy.runner.build_prompt", return_value="prompt") as build_prompt_mock,
                patch("qazy.runner.prime_browser_no_auth"),
                patch("qazy.runner.cleanup_browser_session"),
                patch("qazy.runner.invoke_runtime") as invoke_runtime_mock,
                patch("qazy.runner.log"),
            ):
                invoke_runtime_mock.return_value = RuntimeInvocation(
                    runtime=runtime.name,
                    final_text="PASS manual login",
                    transcript=["PASS manual login"],
                    log_path=workspace.logs_dir / "stub.log",
                )

                result = _run_single_section(
                    workspace=workspace,
                    scenario=scenario,
                    section=section,
                    target=target,
                    runtime=runtime,
                    run_id="run-123",
                    base_url=target.base_url,
                    results_dir=workspace.results_dir,
                    prefix="test",
                    color="",
                    screenshot_strategy="none",
                )

        build_prompt_mock.assert_called_once_with(
            section.body,
            base_url=target.base_url,
            start_page=section.start_page,
            email=section.email,
            password=section.password,
            primed=False,
            screenshot_strategy="none",
        )
        self.assertEqual(result.status, "passed")

    def test_cookie_auth_fails_before_starting_server_without_credentials(self) -> None:
        section = ScenarioSection(
            index=0,
            label="section-0",
            email="",
            password="",
            start_page="/login",
            use_cookie=True,
            auth_provider="nextauth",
            auth_cookie_prefix="better-auth",
            auth_base_path="/api/auth",
            body="## list\n- [ ] verify manual login",
        )
        scenario = Scenario(
            path="user-scenarios/missing-creds",
            file_path=Path("/tmp/missing-creds.scenario.md"),
            email=section.email,
            password=section.password,
            start_page=section.start_page,
            use_cookie=section.use_cookie,
            auth_provider=section.auth_provider,
            auth_cookie_prefix=section.auth_cookie_prefix,
            auth_base_path=section.auth_base_path,
            body=section.body,
            raw_content="",
            sections=[section],
        )
        target = TargetDefinition(
            name="local",
            mode="managed",
            base_url="http://127.0.0.1:{appPort}",
            dev_command="pnpm dev",
            env={"PORT": "{appPort}"},
            app_port="auto",
            mongo_port=None,
            ready=ReadyCheck(type="http", path="/", timeout_seconds=60),
            parallel_safe=False,
            scenario_defaults=ScenarioDefaults(),
        )

        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            workspace = Workspace(
                project_root=root,
                scenarios_dir=root / "user-scenarios",
                results_dir=root / ".qazy/results",
                logs_dir=root / ".qazy" / "logs",
            )

            with (
                patch("qazy.runner.start_managed_target") as start_managed_target_mock,
                patch("qazy.runner.wait_for_target_ready") as wait_for_target_ready_mock,
                patch("qazy.runner._run_single_section") as run_single_section_mock,
                patch("qazy.runner.log") as log_mock,
            ):
                result = _run_prepared_scenario(
                    workspace,
                    scenario,
                    target=target,
                    runtime_name="codex",
                    run_id="run-123",
                    screenshot_strategy="none",
                )
                error_text = result.results_file.read_text(encoding="utf-8")

        self.assertEqual(result.status, "error")
        logged_lines = [call.args[0] for call in log_mock.call_args_list if call.args]
        self.assertIn("Model:      gpt-5.4-mini", logged_lines)
        start_managed_target_mock.assert_not_called()
        wait_for_target_ready_mock.assert_not_called()
        run_single_section_mock.assert_not_called()
        self.assertIn("requires email and password because use_cookie is true", error_text)
        self.assertIn("set target.scenarioDefaults.email/password", error_text)
        self.assertIn("pass --email and --password", error_text)
        self.assertIn("**Model**: gpt-5.4-mini", error_text)


if __name__ == "__main__":
    unittest.main()
