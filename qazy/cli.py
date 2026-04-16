"""Argument parsing and command dispatch for Qazy."""

from __future__ import annotations

import argparse
import glob
import shlex
import sys
import textwrap
from pathlib import Path

from .config import get_target, load_config
from .reporting import UsageTotals, analyze_log, format_usage
from .runner import ScenarioOverrides, rename_scenarios, run_batch, run_prompt, run_scenario, workspace_from_root
from .runtimes import list_runtimes, probe_runtime


LEGACY_SCENARIO_COMMANDS = {"run", "batch"}
HELP_TOPICS = {
    "run",
    "batch",
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
          qazy tokens [logs...] [options]
          qazy rename-scenarios [options]
          qazy runtimes [options]
          qazy help [command]

        What Qazy Needs:
          - qazy.config.json in the workspace under test
          - agent-browser on PATH
          - a runtime CLI installed ({runtimes})

        Core Flows:
          qazy user-scenarios/login
          qazy "user-scenarios/**/*.scenario.md" --parallel
          qazy -p "test login flow for student" --start-page /login --no-use-cookie
          qazy tokens
          qazy runtimes --smoke

        Key Run Options:
          --target NAME                  Pick a target from qazy.config.json
          --runtime NAME                 Runtime CLI to use
          --email/--password             Override scenario credentials
          --start-page                   Override scenario start_page
          --use-cookie/--no-use-cookie   Control built-in auth behavior
          --headed/--headless            Control browser visibility
          --screenshot-strategy          none | error | single | checkpoints
          --results-dir / --logs-dir     Override output locations
          --parallel / --max-workers     Batch execution controls

        Minimal qazy.config.json:
          {{
            "version": 1,
            "defaultTarget": "local",
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

        Scenario Sources:
          file      single scenario run
          dir/glob  batch run
          --prompt  ad hoc single run without a scenario file

        Scenario Fields:
          email, password, start_page, use_cookie
          CLI overrides win; target.scenarioDefaults can fill missing values.

        Authentication:
          use_cookie=true   built-in NextAuth credentials flow:
                            GET /api/auth/csrf then POST /api/auth/callback/credentials
          use_cookie=false  runtime logs in manually in the browser

        Outputs:
          results markdown   <resultsDir>/<run-id>/
          runtime logs       .qazy/logs/ by default
          exit code          0 on pass, 1 on fail/error

        Limitations:
          - built-in auto-auth only supports NextAuth credentials-cookie login
          - PASS/FAIL comes from runtime output parsed by Qazy, not deterministic DOM assertions
          - managed target readiness is a simple HTTP probe
          - prompt mode is best for exploration; checked-in scenario files are more repeatable

        Help Topics:
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
          qazy "user-scenarios/**/*.scenario.md" --parallel
          qazy -p "test login flow for student" --start-page /login --no-use-cookie
          qazy user-scenarios/login --target staging --runtime codex

        Scenario Sources:
          - file path: single scenario run
          - directory or glob: batch run
          - --prompt: ad hoc single run with no scenario file

        Scenario Fields:
          email, password, start_page, use_cookie
          CLI overrides apply to every section in a multi-section scenario file.
          target.scenarioDefaults can fill missing values before CLI overrides are applied.

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
          use_cookie=true   built-in NextAuth credentials login
          use_cookie=false  runtime logs in manually in the browser

        Outputs:
          - results markdown under <resultsDir>/<run-id>/
          - runtime logs under .qazy/logs/
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
          qazy tokens .qazy/logs/claude-login.log .qazy/logs/codex-flow.log

        Notes:
          - Reads runtime logs, not result markdown files.
          - Skips server-*.log files by default.
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

        Qazy looks for qazy.config.json in the project root by default.
        You can override that with --config-file.

        Root Fields:
          version         config schema version; currently 1
          defaultTarget   target used when --target is omitted
          resultsDir      optional default results directory
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
                          default email/password/startPage/useCookie values

        Minimal Managed Target:
          {
            "version": 1,
            "defaultTarget": "local",
            "targets": {
              "local": {
                "mode": "managed",
                "baseUrl": "http://localhost:{appPort}",
                "devCommand": "pnpm dev",
                "ports": {"appPort": "auto"},
                "env": {"PORT": "{appPort}"}
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
          - resultsDir is resolved relative to qazy.config.json when relative
          - logs default to .qazy/logs/, with legacy qazy/logs/ still honored if present
          - ready.type currently only supports "http"
        """
    ).rstrip()


def build_auth_help() -> str:
    return textwrap.dedent(
        """\
        qazy help auth
        ==============

        Qazy has one built-in auto-auth flow, controlled by use_cookie.

        use_cookie=true
          Qazy performs a NextAuth credentials-cookie login before handing control
          to the runtime:
            1. GET /api/auth/csrf
            2. POST /api/auth/callback/credentials
            3. capture the returned session cookie
            4. inject that cookie into agent-browser
            5. open start_page

        use_cookie=false
          Qazy does no pre-authentication. The runtime must log in manually in the browser.

        Credential Sources:
          - scenario frontmatter
          - target.scenarioDefaults
          - CLI overrides: --email --password --start-page --use-cookie/--no-use-cookie

        Important Limits:
          - built-in auth is only for NextAuth credentials-cookie login
          - SSO, OAuth, magic links, MFA, and custom login flows must be browser-driven
          - email/password are only required when use_cookie resolves to true
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
          - built-in auto-auth only supports NextAuth credentials-cookie login
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
        default="error",
        choices=["none", "error", "single", "checkpoints"],
        help="Screenshot capture policy",
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
    parser.add_argument("--logs-dir", type=Path, help="Override the log directory")


def add_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target", dest="target_name", help="Named target from qazy.config.json")
    parser.add_argument("--config-file", type=Path, help="Path to a qazy.config.json file")


def add_logs_workspace_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="Workspace root")
    parser.add_argument("--logs-dir", type=Path, help="Override the log directory")


def add_rename_workspace_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="Workspace root")
    parser.add_argument("--scenarios-dir", type=Path, help="Override the scenarios directory")


def add_runtime_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--runtime",
        default="claude",
        choices=[runtime.name for runtime in list_runtimes()],
        help="Agent runtime to execute",
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


def workspace_from_args(args: argparse.Namespace, *, config_results_dir: Path | None = None):
    return workspace_from_root(
        args.project_root,
        scenarios_dir=getattr(args, "scenarios_dir", None),
        results_dir=getattr(args, "results_dir", None) or config_results_dir,
        logs_dir=getattr(args, "logs_dir", None),
    )


def scenario_overrides_from_args(args: argparse.Namespace) -> ScenarioOverrides | None:
    overrides = ScenarioOverrides(
        email=getattr(args, "email", None),
        password=getattr(args, "password", None),
        start_page=getattr(args, "start_page", None),
        use_cookie=getattr(args, "use_cookie", None),
    )
    if not overrides.has_overrides():
        return None
    return overrides


def resolve_cli_target(project_root: Path, target: str) -> bool:
    if glob.has_magic(target):
        return True
    candidate = Path(target).expanduser()
    resolved = candidate.resolve() if candidate.is_absolute() else (project_root / candidate).resolve()
    return resolved.is_dir()


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    if not raw_argv:
        print_main_help()
        return 2

    if raw_argv[0] in {"-h", "--help"}:
        print_main_help()
        return 0

    if raw_argv[0] == "help":
        if len(raw_argv) == 1:
            print_main_help()
            return 0
        return print_help_topic(raw_argv[1])

    command = raw_argv[0]

    if command == "tokens":
        args = build_tokens_parser().parse_args(raw_argv[1:])
        workspace = workspace_from_args(args)
        if args.logs:
            log_files = [Path(item) for item in args.logs]
        else:
            if not workspace.logs_dir.exists():
                print("No log files found")
                return 1
            log_files = sorted(path for path in workspace.logs_dir.glob("*.log") if not path.name.startswith("server-"))
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

    config = load_config(args.project_root, config_file=args.config_file)
    workspace = workspace_from_args(args, config_results_dir=config.results_dir)
    target = get_target(config, args.target_name)
    scenario_overrides = scenario_overrides_from_args(args)

    if args.prompt is not None:
        result = run_prompt(
            workspace,
            args.prompt,
            target=target,
            runtime_name=args.runtime,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            run_id=args.run_id,
            app_port=args.app_port,
            mongo_port=args.mongo_port,
            timeout=args.timeout,
            dev_command=tuple(shlex.split(args.dev_command)) if args.dev_command else None,
            scenario_overrides=scenario_overrides,
            screenshot_strategy=args.screenshot_strategy,
            headed=args.headed,
        )
        print(result.results_file)
        return 0 if result.status == "passed" else 1

    if not is_batch:
        result = run_scenario(
            workspace,
            args.target,
            target=target,
            runtime_name=args.runtime,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            run_id=args.run_id,
            app_port=args.app_port,
            mongo_port=args.mongo_port,
            timeout=args.timeout,
            dev_command=tuple(shlex.split(args.dev_command)) if args.dev_command else None,
            scenario_overrides=scenario_overrides,
            screenshot_strategy=args.screenshot_strategy,
            headed=args.headed,
        )
        print(result.results_file)
        return 0 if result.status == "passed" else 1

    result = run_batch(
        workspace,
        args.target,
        target=target,
        runtime_name=args.runtime,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
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
