"""Argument parsing and command dispatch for Qazy."""

from __future__ import annotations

import argparse
import glob
from importlib.metadata import PackageNotFoundError, version as package_version
import shlex
import shutil
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

from .config import (
    AUTH_PROVIDERS,
    DEFAULT_RUNTIME,
    DEFAULT_SCREENSHOT_STRATEGY,
    DEFAULT_TARGET_NAME,
    SCREENSHOT_STRATEGIES,
    TargetDefinition,
    build_default_target,
    config_file_is_formatted,
    get_target,
    load_config,
    write_config_template,
)
from .reporting import UsageTotals, analyze_log, format_usage
from .runner import ScenarioOverrides, rename_scenarios, run_batch, run_prompt, run_scenario, workspace_from_root
from .runtimes import list_runtimes, probe_runtime


LEGACY_SCENARIO_COMMANDS = {"run", "batch"}
HELP_TOPICS = {
    "run",
    "batch",
    "init",
    "setup",
    "scenario",
    "prompt",
    "tokens",
    "rename",
    "rename-scenarios",
    "runtimes",
    "config",
    "auth",
    "limitations",
}


def get_version() -> str:
    try:
        return package_version("qazy")
    except PackageNotFoundError:
        pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
        try:
            payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return "unknown"
        project = payload.get("project")
        if not isinstance(project, dict):
            return "unknown"
        value = project.get("version")
        return value if isinstance(value, str) else "unknown"


def build_main_help() -> str:
    runtimes = ", ".join(runtime.name for runtime in list_runtimes())
    return textwrap.dedent(
        f"""\
        Qazy
        =====

        Agent-driven browser QA runner for scenario-based acceptance tests.

        Usage:
          qazy <scenario-path|dir|glob> [options]
          qazy -p "ad hoc prompt" [options]
          qazy --version
          qazy setup [options]
          qazy init [options]
          qazy config check [options]
          qazy tokens [logs...] [options]
          qazy rename-scenarios [options]
          qazy runtimes [options]
          qazy help [command]

        What Qazy Needs:
          - qazy.config.jsonc or qazy.config.json, or use the built-in no-config target defaults
          - agent-browser on PATH
          - a runtime CLI installed ({runtimes})

        Core Flows:
          qazy init
          qazy setup
          qazy user-scenarios/login
          qazy user-scenarios/login --base-url http://127.0.0.1:3000
          qazy "user-scenarios/**/*.scenario.md" --parallel
          qazy -p "test login flow for student" --start-page /login --no-use-cookie \\
            --email student@example.com --password secret123
          qazy config check
          qazy tokens
          qazy runtimes --smoke

        Key Run Options:
          --target NAME                  Pick a target from qazy.config.jsonc / qazy.config.json
          --base-url URL                 Use an attached target without a config file
          --runtime NAME                 Runtime CLI to use
          --email/--password             Override scenario credentials
          --start-page                   Override scenario start_page
          --use-cookie/--no-use-cookie   Control built-in auth behavior
          --auth-provider                nextauth | better-auth
          --auth-cookie-prefix           Better Auth cookie prefix override
          --headed/--headless            Control browser visibility
          --screenshot-strategy          none | error | single | checkpoints
          --results-dir                  Override output location
          --parallel / --max-workers     Batch execution controls

        Minimal qazy.config.jsonc:
          {{
            "version": 1,
            "defaultTarget": "local",
            "defaultRuntime": "codex",
            "resultsDir": ".qazy/results",
            "targets": {{
              "local": {{
                "mode": "managed",
                "baseUrl": "http://localhost:{{appPort}}",
                "devCommand": "pnpm dev",
                "ports": {{"appPort": "auto"}}
              }}
            }}
          }}

        Targets:
          managed   start and stop a local process from devCommand
          attached  run against an existing baseUrl without starting a server
          no-config default attached target is http://127.0.0.1:3000
          no-config --dev-command target starts locally with PORT={{appPort}}

        Scenario Sources:
          file      single scenario run
          dir/glob  batch run
          --prompt  ad hoc single run without a scenario file

        Scenario Fields:
          email, password, start_page, use_cookie, auth_provider, auth_cookie_prefix
          email/password are required for use_cookie=true. For use_cookie=false,
          omitted credentials are reported and the runtime is told not to search for them.
          CLI overrides win; target.scenarioDefaults can fill missing values.

        Authentication:
          use_cookie=true   built-in credentials-cookie login; pick with auth_provider
                            - nextauth (default):   GET /api/auth/csrf then POST /api/auth/callback/credentials
                            - better-auth:          POST /api/auth/sign-in/email (JSON)
          use_cookie=false  runtime logs in manually in the browser when credentials are provided

        Outputs:
          results and logs   .qazy/results/<run-id>/ by default
          exit code          0 on pass, 1 on fail/error

        Limitations:
          - built-in auto-auth supports NextAuth and Better Auth credentials-cookie login only
          - PASS/FAIL comes from runtime output parsed by Qazy, not deterministic DOM assertions
          - managed target readiness is a simple HTTP probe
          - prompt mode is best for exploration; checked-in scenario files are more repeatable

        Help Topics:
          qazy help init
          qazy help setup
          qazy help run
          qazy help config
          qazy help auth
          qazy help tokens
          qazy help rename-scenarios
          qazy help runtimes
          qazy help limitations
        """
    ).rstrip()


def build_scenario_help_epilog() -> str:
    return textwrap.dedent(
        """\
        Examples:
          qazy user-scenarios/login
          qazy user-scenarios/login --base-url http://127.0.0.1:3000
          qazy "user-scenarios/**/*.scenario.md" --parallel
          qazy -p "test login flow for student" --start-page /login --no-use-cookie \\
            --email student@example.com --password secret123
          qazy user-scenarios/login --target staging --runtime codex

        Scenario Sources:
          - file path: single scenario run
          - directory or glob: batch run
          - --prompt: ad hoc single run with no scenario file

        Scenario Fields:
          email, password, start_page, use_cookie, auth_provider, auth_cookie_prefix
          CLI overrides apply to every section in a multi-section scenario file.
          email/password are required for use_cookie=true. For use_cookie=false,
          omitted credentials are reported and the runtime is told not to search for them.
          target.scenarioDefaults can fill missing values before CLI overrides are applied.

        No-config mode:
          - with no config file, Qazy defaults to attached http://127.0.0.1:3000
          - --base-url points Qazy at another existing app
          - --dev-command starts a managed app and uses http://127.0.0.1:{appPort}

        Minimal Scenario File:
          ---
          start_page: /login
          use_cookie: false
          ---

          # Login

          ## Notes
          Use email/password from the app under test.

          ## List
          - [ ] Sign in and confirm the dashboard loads.

        Multi-section Scenarios:
          Repeat the frontmatter block to create multiple sections in one file.
          Qazy runs those sections in order against one shared target lifecycle.

        Authentication:
          use_cookie=true   built-in credentials-cookie login (nextauth | better-auth)
          use_cookie=false  runtime logs in manually in the browser when credentials are provided

        Outputs:
          - results markdown under .qazy/results/<run-id>/ by default
          - runtime and server logs under .qazy/results/<run-id>/logs/
          - exit code 0 on pass, 1 on fail/error

        Limitation:
          PASS/FAIL is parsed from runtime output, so treat Qazy as a high-level browser check,
          not a replacement for deterministic unit or integration tests.
        """
    ).rstrip()


def build_tokens_help_epilog() -> str:
    return textwrap.dedent(
        """\
        Examples:
          qazy tokens
          qazy tokens .qazy/results/my-run/logs/claude-login.log

        Notes:
          - Reads runtime logs, not result markdown files.
          - With no log paths, scans resultsDir recursively and skips server-*.log files.
          - If a config file exists, its resultsDir is used unless --results-dir is passed.
        """
    ).rstrip()


def build_init_help_epilog() -> str:
    return textwrap.dedent(
        """\
        Examples:
          qazy init
          qazy init --force
          qazy init --output qazy.config.jsonc

        Notes:
          - Writes qazy.config.jsonc by default.
          - Includes every supported config field, with optional fields commented out.
          - The generated file is a usable config; edit and uncomment only what you need.
        """
    ).rstrip()


def build_setup_help_epilog() -> str:
    return textwrap.dedent(
        """\
        Examples:
          qazy setup
          qazy setup --runtime codex
          qazy setup --runtime claude --project-root ../my-app

        Notes:
          - Starts Claude Code or Codex with Qazy's install prompt.
          - When --runtime is omitted, Qazy asks which setup agent to use.
          - The selected agent reviews the target project and creates or patches
            qazy.config.jsonc after asking the user setup questions.
          - Qazy passes the install prompt as the initial prompt argument so the
            agent can keep using the terminal for follow-up interaction.
        """
    ).rstrip()


def build_config_command_epilog() -> str:
    return textwrap.dedent(
        """\
        Examples:
          qazy config check
          qazy config check --config-file qazy.config.jsonc
          qazy config check --schema-only

        Notes:
          - Validates that the config parses as a supported Qazy config.
          - JSONC comments and trailing commas are supported.
          - For strict .json files, the default check also requires canonical two-space formatting.
          - Use --schema-only when you only want schema validation.
        """
    ).rstrip()


def build_rename_help_epilog() -> str:
    return textwrap.dedent(
        """\
        Examples:
          qazy rename-scenarios
          qazy rename-scenarios --write

        Notes:
          - Dry-run by default.
          - Intended for migrating legacy scenario layouts to *.scenario.md files.
        """
    ).rstrip()


def build_runtimes_help_epilog() -> str:
    return textwrap.dedent(
        """\
        Examples:
          qazy runtimes
          qazy runtimes --smoke

        Notes:
          - Without --smoke, Qazy only checks whether the runtime executable responds to --help.
          - With --smoke, Qazy sends a trivial prompt through each installed runtime.
        """
    ).rstrip()


def build_config_help() -> str:
    return textwrap.dedent(
        """\
        qazy help config
        ================

        Qazy looks for qazy.config.json first, then qazy.config.jsonc in the project root.
        You can override that with --config-file. If no config exists, Qazy can still run
        with its built-in default target behavior.

        Commands:
          qazy config check              Validate schema and JSON/JSONC parsing
          qazy config check --schema-only
                                         Validate schema only

        Root Fields:
          version         config schema version; currently 1
          defaultTarget   target used when --target is omitted
          defaultRuntime  runtime used when --runtime is omitted
          resultsDir      optional default results directory; default .qazy/results
          targets         named target definitions

        Target Fields:
          mode            managed | attached
          baseUrl         target base URL; may use {appPort} / {mongoPort}
          devCommand      required for managed targets
          ports           appPort / mongoPort values or "auto"
          env             environment variables for managed targets
          ready           HTTP readiness probe; default path "/" timeout 60s
          parallelSafe    required for batch --parallel
          scenarioDefaults
                          default email/password/startPage/useCookie/authProvider/authCookiePrefix values
          runtimeDefaults default runtime model/reasoningEffort values by runtime name

        Minimal Managed Target:
          {
            "version": 1,
            "defaultTarget": "local",
            "defaultRuntime": "codex",
            "targets": {
              "local": {
                "mode": "managed",
                "baseUrl": "http://localhost:{appPort}",
                "devCommand": "pnpm dev",
                "ports": {"appPort": "auto"},
                "env": {"PORT": "{appPort}"},
                "runtimeDefaults": {
                  "codex": {"model": "gpt-5.4-mini", "reasoningEffort": "low"},
                  "claude": {"model": "claude-sonnet-4-5"}
                }
              }
            }
          }

        Managed vs Attached:
          managed   Qazy starts devCommand, waits for ready, then stops it
          attached  Qazy uses baseUrl as-is and never starts a process

        Scenario Defaults:
          target.scenarioDefaults fills missing scenario fields and prompt-mode defaults.
          Precedence is:
            CLI overrides > scenario frontmatter > target.scenarioDefaults > built-in defaults

        Notes:
          - resultsDir is resolved relative to the config file when relative
          - --runtime overrides defaultRuntime
          - --model and --reasoning-effort override target.runtimeDefaults
          - logs are written under each run's results directory
          - ready.type currently only supports "http"
          - without a config file, Qazy defaults to attached http://127.0.0.1:3000
          - without a config file, --dev-command creates a managed local target
        """
    ).rstrip()


def build_auth_help() -> str:
    return textwrap.dedent(
        """\
        qazy help auth
        ==============

        Qazy has built-in credentials-cookie login controlled by use_cookie and
        auth_provider.

        use_cookie=true, auth_provider=nextauth (default)
          1. GET /api/auth/csrf
          2. POST /api/auth/callback/credentials  (form-encoded)
          3. capture the next-auth session cookie
          4. inject that cookie into agent-browser
          5. open start_page

        use_cookie=true, auth_provider=better-auth
          1. POST /api/auth/sign-in/email  (JSON body, Origin header)
          2. capture better-auth.session_token (or __Secure-…) cookie
          3. inject that cookie into agent-browser
          4. open start_page

        use_cookie=false
          Qazy does no pre-authentication. The runtime must log in manually in the
          browser when credentials are provided. If credentials are omitted, Qazy
          prints that on startup and instructs the runtime not to search for them.

        Credential Sources:
          - scenario frontmatter: email, password, auth_provider, auth_cookie_prefix
          - target.scenarioDefaults
          - CLI overrides: --email --password --start-page --use-cookie/--no-use-cookie
                           --auth-provider --auth-cookie-prefix

        Important Limits:
          - built-in auth only covers NextAuth and Better Auth credentials-cookie login
          - SSO, OAuth, magic links, MFA, and custom login flows must be browser-driven
          - email/password are required for use_cookie=true built-in auth
          - use_cookie=false can run without credentials, but the runtime is instructed
            not to search files, env vars, source code, logs, or config for them
          - auth_cookie_prefix is only used by better-auth (matches Better Auth's
            advanced.cookiePrefix; defaults to "better-auth")
        """
    ).rstrip()


def build_limitations_help() -> str:
    return textwrap.dedent(
        """\
        qazy help limitations
        =====================

        Qazy is a high-level browser QA runner, not a deterministic assertion framework.

        Current limitations:
          - PASS/FAIL is inferred from runtime output parsed by Qazy
          - built-in auto-auth supports NextAuth and Better Auth credentials-cookie login only
          - readiness checks are simple HTTP probes
          - prompt mode is convenient but less repeatable than checked-in scenario files
          - runtime quality depends on the installed agent CLI and its browser behavior
          - screenshots depend on agent-browser availability and runtime cooperation

        Use Qazy for:
          - end-to-end smoke checks
          - exploratory acceptance coverage
          - agent verification after code changes

        Do not treat Qazy as a replacement for:
          - unit tests
          - deterministic integration tests
          - low-level DOM or API contract assertions
        """
    ).rstrip()


def print_main_help() -> None:
    print(build_main_help())


def print_help_topic(topic: str) -> int:
    if topic == "init":
        build_init_parser().print_help()
        return 0
    if topic == "setup":
        build_setup_parser().print_help()
        return 0
    if topic in {"run", "batch"}:
        build_scenario_parser(prog=f"qazy {topic}").print_help()
        return 0
    if topic in {"scenario", "prompt"}:
        build_scenario_parser().print_help()
        return 0
    if topic == "tokens":
        build_tokens_parser().print_help()
        return 0
    if topic in {"rename", "rename-scenarios"}:
        build_rename_parser().print_help()
        return 0
    if topic == "runtimes":
        build_runtimes_parser().print_help()
        return 0
    if topic == "config":
        print(build_config_help())
        return 0
    if topic == "auth":
        print(build_auth_help())
        return 0
    if topic == "limitations":
        print(build_limitations_help())
        return 0

    print(f"Unknown help topic: {topic}\n")
    print(f"Available topics: {', '.join(sorted(HELP_TOPICS))}\n")
    print_main_help()
    return 1


def build_scenario_parser(*, prog: str = "qazy") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        usage=f'{prog} [options] <scenario-path|dir|glob>\n       {prog} [options] -p "ad hoc prompt"',
        description="Run a Qazy browser QA scenario, batch, or ad hoc prompt.",
        epilog=build_scenario_help_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_run_batch_workspace_args(parser)
    add_target_args(parser)
    add_runtime_arg(parser)
    add_frontmatter_override_args(parser)
    parser.add_argument("-p", "--prompt", help="Run an ad hoc prompt instead of loading a scenario file")
    parser.add_argument("target", nargs="?", help="Scenario file, directory, or glob")
    parser.add_argument("--run-id", help="Explicit run id to use for a single scenario")
    parser.add_argument("--app-port", type=int, help="Explicit app port for a single managed scenario run")
    parser.add_argument("--mongo-port", type=int, help="Explicit mongo port for a single managed scenario run")
    parser.add_argument("--parallel", action="store_true", help="Run matching scenarios concurrently")
    parser.add_argument("--max-workers", type=int, help="Maximum workers when running in parallel")
    parser.add_argument("--timeout", type=int, help="Override target readiness timeout in seconds")
    parser.add_argument("--dev-command", help="Override the target dev command for this run")
    add_browser_args(parser)
    parser.add_argument(
        "--screenshot-strategy",
        default=None,
        choices=list(SCREENSHOT_STRATEGIES),
        help=f"Screenshot capture policy (default: config screenshotStrategy or {DEFAULT_SCREENSHOT_STRATEGY})",
    )
    return parser


def build_init_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qazy init",
        description="Write a starter Qazy config file for the current workspace.",
        epilog=build_init_help_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="Workspace root")
    parser.add_argument("--output", type=Path, help="Output path, relative to --project-root by default")
    parser.add_argument("--force", action="store_true", help="Overwrite the output file if it already exists")
    return parser


def build_setup_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qazy setup",
        description="Launch an agent to install or update Qazy config for a project.",
        epilog=build_setup_help_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="Workspace root to set up")
    parser.add_argument(
        "--runtime",
        choices=["claude", "codex"],
        help="Setup agent to launch; omit to choose interactively",
    )
    parser.add_argument(
        "--prompt-file",
        type=Path,
        help="Override the install prompt file, relative to --project-root by default",
    )
    return parser


def build_config_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qazy config",
        description="Inspect and validate Qazy config files.",
        epilog=build_config_command_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="config_command", required=True)
    check = subparsers.add_parser(
        "check",
        description="Validate qazy.config.jsonc or qazy.config.json schema and formatting.",
        help="Validate Qazy config schema and formatting",
    )
    check.add_argument("--project-root", type=Path, default=Path.cwd(), help="Workspace root")
    check.add_argument("--config-file", type=Path, help="Path to a qazy.config.jsonc or qazy.config.json file")
    check.add_argument(
        "--schema-only",
        action="store_true",
        help="Validate the Qazy config schema without checking JSON formatting",
    )
    return parser


def build_tokens_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qazy tokens",
        description="Summarize runtime token and usage data from log files.",
        epilog=build_tokens_help_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_logs_workspace_args(parser)
    parser.add_argument("logs", nargs="*", help="Specific log files to inspect")
    return parser


def build_rename_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qazy rename-scenarios",
        description="Rename legacy scenario files to the current *.scenario.md layout.",
        epilog=build_rename_help_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_rename_workspace_args(parser)
    parser.add_argument("--write", action="store_true", help="Apply the rename instead of dry-run")
    return parser


def build_runtimes_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qazy runtimes",
        description="Inspect available runtime CLIs and optional smoke status.",
        epilog=build_runtimes_help_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="Workspace root for smoke tests")
    parser.add_argument("--smoke", action="store_true", help="Run a trivial prompt through each runtime")
    return parser


def add_run_batch_workspace_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="Workspace root")
    parser.add_argument("--results-dir", type=Path, help="Override the results directory")


def add_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target", dest="target_name", help="Named target from a Qazy config file")
    parser.add_argument("--config-file", type=Path, help="Path to a qazy.config.jsonc or qazy.config.json file")
    parser.add_argument("--base-url", help="Use an attached target URL when no config file is present")


def add_logs_workspace_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="Workspace root")
    parser.add_argument("--config-file", type=Path, help="Path to a qazy.config.jsonc or qazy.config.json file")
    parser.add_argument("--results-dir", type=Path, help="Override the results directory to scan")


def add_rename_workspace_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="Workspace root")
    parser.add_argument("--scenarios-dir", type=Path, help="Override the scenarios directory")


def add_runtime_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--runtime",
        default=None,
        choices=[runtime.name for runtime in list_runtimes()],
        help=f"Agent runtime to execute (default: config defaultRuntime or {DEFAULT_RUNTIME})",
    )
    parser.add_argument("--model", help="Optional runtime model override")
    parser.add_argument("--reasoning-effort", help="Optional runtime reasoning/effort override")


def add_browser_args(parser: argparse.ArgumentParser) -> None:
    headed_group = parser.add_mutually_exclusive_group()
    headed_group.add_argument(
        "--headed",
        dest="headed",
        action="store_true",
        default=None,
        help="Show the browser window during the run",
    )
    headed_group.add_argument(
        "--headless",
        dest="headed",
        action="store_false",
        help="Run the browser headless",
    )


def add_frontmatter_override_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--email", help="Override scenario frontmatter email")
    parser.add_argument("--password", help="Override scenario frontmatter password")
    parser.add_argument("--start-page", help="Override scenario frontmatter start_page")
    cookie_group = parser.add_mutually_exclusive_group()
    cookie_group.add_argument(
        "--use-cookie",
        dest="use_cookie",
        action="store_true",
        default=None,
        help="Override scenario frontmatter use_cookie to true",
    )
    cookie_group.add_argument(
        "--no-use-cookie",
        dest="use_cookie",
        action="store_false",
        help="Override scenario frontmatter use_cookie to false",
    )
    parser.add_argument(
        "--auth-provider",
        dest="auth_provider",
        choices=list(AUTH_PROVIDERS),
        default=None,
        help="Override scenario frontmatter auth_provider",
    )
    parser.add_argument(
        "--auth-cookie-prefix",
        dest="auth_cookie_prefix",
        default=None,
        help="Override Better Auth cookie prefix (default: better-auth)",
    )
    parser.add_argument(
        "--auth-base-path",
        dest="auth_base_path",
        default=None,
        help="Override auth handler base path (default: /api/auth)",
    )


def workspace_from_args(
    args: argparse.Namespace,
    *,
    config_results_dir: Path | None = None,
):
    return workspace_from_root(
        args.project_root,
        scenarios_dir=getattr(args, "scenarios_dir", None),
        results_dir=getattr(args, "results_dir", None) or config_results_dir,
    )


def scenario_overrides_from_args(args: argparse.Namespace) -> ScenarioOverrides | None:
    overrides = ScenarioOverrides(
        email=getattr(args, "email", None),
        password=getattr(args, "password", None),
        start_page=getattr(args, "start_page", None),
        use_cookie=getattr(args, "use_cookie", None),
        auth_provider=getattr(args, "auth_provider", None),
        auth_cookie_prefix=getattr(args, "auth_cookie_prefix", None),
        auth_base_path=getattr(args, "auth_base_path", None),
    )
    if not overrides.has_overrides():
        return None
    return overrides


def runtime_defaults_from_target(
    target: TargetDefinition,
    runtime_name: str,
    *,
    model_override: str | None = None,
    reasoning_effort_override: str | None = None,
) -> tuple[str | None, str | None]:
    defaults = target.runtime_defaults.get(runtime_name)
    return (
        model_override if model_override is not None else (defaults.model if defaults else None),
        (
            reasoning_effort_override
            if reasoning_effort_override is not None
            else (defaults.reasoning_effort if defaults else None)
        ),
    )


def discover_runtime_logs(results_dir: Path) -> list[Path]:
    if not results_dir.exists():
        return []
    return sorted(path for path in results_dir.rglob("*.log") if not path.name.startswith("server-"))


def resolve_cli_target(project_root: Path, target: str) -> bool:
    if glob.has_magic(target):
        return True
    candidate = Path(target).expanduser()
    resolved = candidate.resolve() if candidate.is_absolute() else (project_root / candidate).resolve()
    return resolved.is_dir()


def resolve_run_target(args: argparse.Namespace) -> tuple[Path | None, TargetDefinition, str, str]:
    try:
        config = load_config(args.project_root, config_file=args.config_file)
    except FileNotFoundError:
        if args.config_file is not None:
            raise
        if args.target_name not in {None, DEFAULT_TARGET_NAME}:
            raise RuntimeError("--target requires a config file when selecting a named target")
        if args.base_url is not None and args.dev_command is not None:
            raise RuntimeError("--base-url and --dev-command cannot be combined without a config file")
        return (
            None,
            build_default_target(base_url=args.base_url, managed=args.dev_command is not None),
            args.runtime or DEFAULT_RUNTIME,
            DEFAULT_SCREENSHOT_STRATEGY,
        )

    if args.base_url is not None:
        raise RuntimeError("--base-url is only supported when no config file is present")
    return (
        config.results_dir,
        get_target(config, args.target_name),
        args.runtime or config.default_runtime,
        config.default_screenshot_strategy,
    )


def run_config_check(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.project_root, config_file=args.config_file)
        if config.source is None:
            raise RuntimeError("No config file was loaded")
        if not args.schema_only and not config_file_is_formatted(config.source):
            print(f"Config formatting check failed: {config.source}")
            print("Expected canonical JSON formatting: two-space indentation and a trailing newline.")
            return 1
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        print(f"Config check failed: {exc}")
        return 1

    print(f"Config OK: {config.source}")
    if args.schema_only:
        print("Schema: valid")
    else:
        print("Schema: valid")
        if config.source.suffix == ".jsonc":
            print("Formatting: skipped for JSONC")
        else:
            print("Formatting: canonical")
    return 0


SETUP_RUNTIME_LABELS = {
    "claude": "Claude Code",
    "codex": "Codex",
}


def resolve_setup_prompt_file(project_root: Path, prompt_file: Path | None) -> Path:
    if prompt_file is not None:
        path = prompt_file.expanduser()
        if not path.is_absolute():
            path = (project_root / path).resolve()
        else:
            path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Setup prompt not found: {path}")
        return path

    candidates = [
        (Path(__file__).resolve().parent.parent / "INSTALL_PROMPT.md").resolve(),
        (Path(__file__).resolve().parent / "INSTALL_PROMPT.md").resolve(),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("Setup prompt not found. Expected INSTALL_PROMPT.md in the Qazy install.")


def read_setup_prompt(project_root: Path, prompt_file: Path | None) -> str:
    path = resolve_setup_prompt_file(project_root, prompt_file)
    prompt = path.read_text(encoding="utf-8")
    if not prompt.strip():
        raise RuntimeError(f"Setup prompt is empty: {path}")
    return prompt


def prompt_for_setup_runtime() -> str:
    print("Choose a setup agent:")
    for index, runtime_name in enumerate(("claude", "codex"), start=1):
        executable = shutil.which(runtime_name)
        status = f"found at {executable}" if executable else "not found on PATH"
        print(f"  {index}. {SETUP_RUNTIME_LABELS[runtime_name]} ({runtime_name}) - {status}")

    choices = {
        "1": "claude",
        "claude": "claude",
        "claude code": "claude",
        "2": "codex",
        "codex": "codex",
    }
    while True:
        try:
            answer = input("Use Claude Code or Codex? [claude/codex]: ").strip().lower()
        except EOFError as exc:
            raise RuntimeError("qazy setup requires --runtime when input is not interactive") from exc
        if answer in choices:
            return choices[answer]
        print("Please enter claude, codex, 1, or 2.")


def build_setup_command(runtime_name: str, project_root: Path, prompt: str) -> list[str]:
    if runtime_name == "claude":
        return ["claude", prompt]
    if runtime_name == "codex":
        return ["codex", "-C", str(project_root), prompt]
    raise RuntimeError(f"Unsupported setup runtime: {runtime_name}")


def run_setup(args: argparse.Namespace) -> int:
    project_root = args.project_root.expanduser().resolve()
    if not project_root.is_dir():
        print(f"Setup failed: project root not found: {project_root}")
        return 1
    try:
        prompt = read_setup_prompt(project_root, args.prompt_file)
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        print(f"Setup failed: {exc}")
        return 1

    try:
        runtime_name = args.runtime or prompt_for_setup_runtime()
    except RuntimeError as exc:
        print(f"Setup failed: {exc}")
        return 2

    executable = shutil.which(runtime_name)
    if executable is None:
        label = SETUP_RUNTIME_LABELS[runtime_name]
        print(f"Setup failed: {label} executable '{runtime_name}' was not found on PATH.")
        return 1

    print(f"Launching {SETUP_RUNTIME_LABELS[runtime_name]} to set up Qazy in {project_root}")
    command = build_setup_command(runtime_name, project_root, prompt)
    try:
        return subprocess.run(command, cwd=project_root).returncode
    except KeyboardInterrupt:
        print("\nSetup interrupted")
        return 130


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    if not raw_argv:
        print_main_help()
        return 2

    if raw_argv[0] in {"-h", "--help"}:
        print_main_help()
        return 0

    if raw_argv[0] in {"--version", "-V", "version"}:
        print(f"qazy {get_version()}")
        return 0

    if raw_argv[0] == "help":
        if len(raw_argv) == 1:
            print_main_help()
            return 0
        return print_help_topic(raw_argv[1])

    command = raw_argv[0]

    if command == "tokens":
        args = build_tokens_parser().parse_args(raw_argv[1:])
        config_results_dir = None
        if args.results_dir is None and not args.logs:
            try:
                config_results_dir = load_config(args.project_root, config_file=args.config_file).results_dir
            except FileNotFoundError:
                pass
        workspace = workspace_from_args(args, config_results_dir=config_results_dir)
        if args.logs:
            log_files = [Path(item) for item in args.logs]
        else:
            log_files = discover_runtime_logs(workspace.results_dir)
        if not log_files:
            print("No log files found")
            return 1
        grand_totals = UsageTotals()
        printed = 0
        for log_file in log_files:
            totals = analyze_log(log_file)
            if not totals:
                continue
            printed += 1
            print(f"{log_file.name}:")
            print(format_usage(totals))
            print()
            grand_totals.add(totals)
        if printed == 0:
            print("No usage data found")
            return 1
        if printed > 1:
            print("=" * 50)
            print("TOTAL:")
            print(format_usage(grand_totals))
        return 0

    if command == "init":
        args = build_init_parser().parse_args(raw_argv[1:])
        path = write_config_template(args.project_root, output=args.output, force=args.force)
        print(path)
        return 0

    if command == "setup":
        args = build_setup_parser().parse_args(raw_argv[1:])
        return run_setup(args)

    if command == "config":
        args = build_config_parser().parse_args(raw_argv[1:])
        if args.config_command == "check":
            return run_config_check(args)
        raise RuntimeError(f"Unsupported config command: {args.config_command}")

    if command == "rename-scenarios":
        args = build_rename_parser().parse_args(raw_argv[1:])
        workspace = workspace_from_args(args)
        renames = rename_scenarios(workspace, write=args.write)
        if not renames:
            print("Nothing to rename")
            return 0
        header = "Applying renames" if args.write else "DRY RUN"
        print(header)
        for old_path, new_path in renames:
            print(f"  {old_path.relative_to(workspace.scenarios_dir)} -> {new_path.relative_to(workspace.scenarios_dir)}")
        print(f"\nCount: {len(renames)}")
        return 0

    if command == "runtimes":
        args = build_runtimes_parser().parse_args(raw_argv[1:])
        exit_code = 0
        for runtime in list_runtimes():
            probe = probe_runtime(runtime.name, cwd=args.project_root, smoke=args.smoke)
            installed = "yes" if probe.installed else "no"
            smoke = "-"
            if probe.smoke_ok is True:
                smoke = "ok"
            elif probe.smoke_ok is False:
                smoke = "fail"
                exit_code = 1
            print(f"{probe.name:8} installed={installed:3} smoke={smoke:4} {probe.detail}")
        return exit_code

    legacy_mode: str | None = None
    scenario_argv = raw_argv
    if command in LEGACY_SCENARIO_COMMANDS:
        legacy_mode = command
        scenario_argv = raw_argv[1:]

    parser = build_scenario_parser(prog=f"qazy {legacy_mode}" if legacy_mode else "qazy")
    args = parser.parse_args(scenario_argv)

    if args.prompt is not None and args.target is not None:
        parser.error("scenario target is not used with --prompt")
    if args.prompt is None and not args.target:
        parser.error("scenario target is required unless --prompt is used")
    if args.prompt is not None and not args.prompt.strip():
        parser.error("--prompt must be a non-empty string")
    if legacy_mode == "batch" and args.prompt is not None:
        parser.error("--prompt is only supported for single-scenario runs")

    auto_batch = False if args.prompt is not None else resolve_cli_target(args.project_root, args.target)
    is_batch = auto_batch
    if legacy_mode == "run":
        is_batch = False
    elif legacy_mode == "batch":
        is_batch = True

    if is_batch and args.run_id:
        parser.error("--run-id is only supported for single-scenario runs")
    if is_batch and args.app_port is not None:
        parser.error("--app-port is only supported for single-scenario runs")
    if is_batch and args.mongo_port is not None:
        parser.error("--mongo-port is only supported for single-scenario runs")
    if not is_batch and args.parallel:
        parser.error("--parallel requires a directory or glob target")
    if not is_batch and args.max_workers is not None:
        parser.error("--max-workers requires a directory or glob target")

    config_results_dir, target, runtime_name, default_screenshot_strategy = resolve_run_target(args)
    workspace = workspace_from_args(
        args,
        config_results_dir=config_results_dir,
    )
    scenario_overrides = scenario_overrides_from_args(args)
    model, reasoning_effort = runtime_defaults_from_target(
        target,
        runtime_name,
        model_override=args.model,
        reasoning_effort_override=args.reasoning_effort,
    )
    screenshot_strategy = args.screenshot_strategy or default_screenshot_strategy

    if args.prompt is not None:
        result = run_prompt(
            workspace,
            args.prompt,
            target=target,
            runtime_name=runtime_name,
            model=model,
            reasoning_effort=reasoning_effort,
            run_id=args.run_id,
            app_port=args.app_port,
            mongo_port=args.mongo_port,
            timeout=args.timeout,
            dev_command=tuple(shlex.split(args.dev_command)) if args.dev_command else None,
            scenario_overrides=scenario_overrides,
            screenshot_strategy=screenshot_strategy,
            headed=args.headed,
        )
        print(result.results_file)
        return 0 if result.status == "passed" else 1

    if not is_batch:
        result = run_scenario(
            workspace,
            args.target,
            target=target,
            runtime_name=runtime_name,
            model=model,
            reasoning_effort=reasoning_effort,
            run_id=args.run_id,
            app_port=args.app_port,
            mongo_port=args.mongo_port,
            timeout=args.timeout,
            dev_command=tuple(shlex.split(args.dev_command)) if args.dev_command else None,
            scenario_overrides=scenario_overrides,
            screenshot_strategy=screenshot_strategy,
            headed=args.headed,
        )
        print(result.results_file)
        return 0 if result.status == "passed" else 1

    result = run_batch(
        workspace,
        args.target,
        target=target,
        runtime_name=runtime_name,
        model=model,
        reasoning_effort=reasoning_effort,
        parallel=args.parallel,
        max_workers=args.max_workers,
        timeout=args.timeout,
        dev_command=tuple(shlex.split(args.dev_command)) if args.dev_command else None,
        scenario_overrides=scenario_overrides,
        screenshot_strategy=args.screenshot_strategy,
        headed=args.headed,
    )
    print(result.results_dir)
    return 0 if not result.failed and not result.errors else 1
