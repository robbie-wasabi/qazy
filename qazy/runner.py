"""Core scenario running logic for Qazy."""

from __future__ import annotations

import concurrent.futures
import glob
import hashlib
import json
import os
import random
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.cookiejar import CookieJar
from pathlib import Path

from .config import (
    AUTH_PROVIDERS,
    DEFAULT_AUTH_BASE_PATH,
    DEFAULT_AUTH_PROVIDER,
    DEFAULT_BETTER_AUTH_COOKIE_PREFIX,
    DEFAULT_LOGS_DIR,
    DEFAULT_RESULTS_DIR,
    ReadyCheck,
    ResolvedTarget,
    ScenarioDefaults,
    TargetDefinition,
    resolve_target,
)
from .reporting import UsageTotals, analyze_log, format_usage_inline
from .runtimes import RuntimeAdapter, get_runtime, invoke_runtime, terminate_process_group


LOG_LOCK = threading.Lock()
PORT_LOCK = threading.Lock()
ALLOCATED_PORTS: set[int] = set()
ANSI_RESET = "\033[0m"
HEADED_VIEWPORT_WIDTH = 1728
HEADED_VIEWPORT_HEIGHT = 1117

COLOR_PALETTE = (
    39, 45, 51, 75, 81, 111, 117, 141, 147, 177,
    190, 208, 214, 220, 154, 118, 48, 49,
)
FRONTMATTER_KEYS = {
    "email",
    "password",
    "start_page",
    "use_cookie",
    "auth_provider",
    "auth_cookie_prefix",
    "auth_base_path",
}

AGENT_BROWSER_GUIDE = """\
## agent-browser Quick Reference

agent-browser is a CLI tool for browser automation.

```bash
agent-browser open <url>
agent-browser snapshot -c             # compact — use this by default
agent-browser snapshot -i             # interactive elements only — use when looking for a button/input to click
agent-browser snapshot                # full snapshot — fallback if -c or -i is missing what you need
agent-browser click @e5
agent-browser fill @e3 "text"         # clear + type
agent-browser type @e3 "text"         # append text
agent-browser press "Enter"
agent-browser select @e4 "value"      # dropdown
agent-browser wait @e5
agent-browser wait 2000
agent-browser screenshot /tmp/qazy.png
agent-browser scroll down 500
agent-browser scrollintoview @e10
agent-browser find role button click --name "Submit"
agent-browser find text "Save" click
```

Snapshot strategy:
- Use `snapshot -c` by default
- Use `snapshot -i` when you only need interactive elements
- Fall back to plain `snapshot` if the compact views are missing what you need
- Always snapshot after navigation or clicks to get fresh refs
- Refs (`@e1`, `@e2`, etc.) change on every snapshot
- Use sleep 1-3 between commands when the page needs render time
"""

WORD_LIST = [
    "amber", "arctic", "autumn", "azure", "birch", "blaze", "bloom", "bolt",
    "brass", "breeze", "brook", "canyon", "cedar", "cliff", "cloud", "coral",
    "crane", "creek", "crest", "crow", "dagger", "dawn", "delta", "dew",
    "drift", "dune", "eagle", "ember", "falcon", "fern", "field", "flare",
    "flint", "forge", "frost", "gale", "ghost", "glacier", "grove", "hail",
    "harbor", "hawk", "hazel", "heath", "heron", "hollow", "iron", "jade",
    "lark", "leaf", "lime", "linden", "maple", "marsh", "mesa", "mist",
    "moss", "night", "noble", "north", "oak", "onyx", "orbit", "otter",
    "peak", "pearl", "pine", "plum", "pond", "prairie", "pulse", "quartz",
    "rain", "raven", "reef", "ridge", "river", "robin", "rose", "rust",
    "sage", "sand", "shadow", "shore", "silk", "slate", "snow", "solar",
    "spark", "steel", "stone", "storm", "swift", "thorn", "tide", "timber",
    "trail", "vale", "vapor", "vine", "violet", "wave", "willow", "wolf",
]


@dataclass(frozen=True)
class Workspace:
    project_root: Path
    scenarios_dir: Path
    results_dir: Path
    logs_dir: Path


@dataclass(frozen=True)
class ScenarioSection:
    index: int
    label: str
    email: str
    password: str
    start_page: str
    use_cookie: bool
    auth_provider: str
    auth_cookie_prefix: str
    auth_base_path: str
    body: str
    provided_keys: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Scenario:
    path: str
    file_path: Path
    email: str
    password: str
    start_page: str
    use_cookie: bool
    auth_provider: str
    auth_cookie_prefix: str
    auth_base_path: str
    body: str
    raw_content: str
    sections: list[ScenarioSection]


@dataclass(frozen=True)
class ScenarioOverrides:
    email: str | None = None
    password: str | None = None
    start_page: str | None = None
    use_cookie: bool | None = None
    auth_provider: str | None = None
    auth_cookie_prefix: str | None = None
    auth_base_path: str | None = None

    def has_overrides(self) -> bool:
        return any(
            value is not None
            for value in (
                self.email,
                self.password,
                self.start_page,
                self.use_cookie,
                self.auth_provider,
                self.auth_cookie_prefix,
                self.auth_base_path,
            )
        )


@dataclass(frozen=True)
class ReportSummary:
    passed: int
    failed: int
    untestable: int
    total: int
    status: str


@dataclass(frozen=True)
class ScenarioRunResult:
    scenario_path: str
    run_id: str
    runtime: str
    base_url: str
    results_file: Path
    log_file: Path
    final_report: str
    report_summary: ReportSummary
    status: str
    usage_totals: UsageTotals | None = None
    screenshots: tuple[Path, ...] = ()


@dataclass(frozen=True)
class AuthSession:
    cookie_name: str
    session_token: str


@dataclass(frozen=True)
class BatchRunResult:
    run_id: str
    runtime: str
    mode: str
    results_dir: Path
    passed: list[str]
    failed: list[str]
    errors: list[str]


def workspace_from_root(
    project_root: Path,
    *,
    scenarios_dir: Path | None = None,
    results_dir: Path | None = None,
    logs_dir: Path | None = None,
) -> Workspace:
    root = project_root.resolve()
    default_logs_dir = (root / DEFAULT_LOGS_DIR).resolve()
    legacy_logs_dir = (root / "qazy" / "logs").resolve()
    resolved_logs_dir = logs_dir.resolve() if logs_dir is not None else default_logs_dir
    if logs_dir is None and legacy_logs_dir.exists() and not default_logs_dir.exists():
        resolved_logs_dir = legacy_logs_dir
    return Workspace(
        project_root=root,
        scenarios_dir=(scenarios_dir or (root / "user-scenarios")).resolve(),
        results_dir=(results_dir or (root / DEFAULT_RESULTS_DIR)).resolve(),
        logs_dir=resolved_logs_dir,
    )


def generate_run_id() -> str:
    return "-".join(random.choices(WORD_LIST, k=3))


def resolve_input_path(project_root: Path, path_arg: str) -> Path:
    raw_path = Path(path_arg).expanduser()
    if raw_path.is_absolute():
        return raw_path.resolve()
    return (project_root / raw_path).resolve()


def scenario_display_path(project_root: Path, scenario_file: Path) -> str:
    resolved = scenario_file.resolve()
    try:
        display = resolved.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        display = resolved.as_posix()
    return display.removesuffix(".scenario.md")


def resolve_scenario_file(project_root: Path, scenario_arg: str) -> Path:
    candidate = resolve_input_path(project_root, scenario_arg)
    candidates = [candidate]
    if not str(candidate).endswith(".scenario.md"):
        candidates.insert(0, Path(f"{candidate}.scenario.md"))

    for scenario_file in candidates:
        if scenario_file.is_file():
            return scenario_file.resolve()

    if candidate.is_dir():
        raise IsADirectoryError(f"Expected a scenario file, got directory: {candidate}")
    raise FileNotFoundError(f"Scenario not found: {scenario_arg}")


def load_scenario(workspace: Workspace, scenario_arg: str) -> Scenario:
    scenario_file = resolve_scenario_file(workspace.project_root, scenario_arg)
    scenario_path = scenario_display_path(workspace.project_root, scenario_file)
    raw_content = scenario_file.read_text(encoding="utf-8")
    raw_sections = parse_sections(raw_content)
    return build_scenario(scenario_path, scenario_file, raw_content, raw_sections)


def build_scenario(
    scenario_path: str,
    scenario_file: Path,
    raw_content: str,
    raw_sections: list[dict[str, object]],
) -> Scenario:
    sections = [
        ScenarioSection(
            index=i,
            label=str(s["email"]) or f"section-{i}",
            email=str(s["email"]),
            password=str(s["password"]),
            start_page=str(s["start_page"]),
            use_cookie=bool(s["use_cookie"]),
            auth_provider=str(s["auth_provider"]),
            auth_cookie_prefix=str(s["auth_cookie_prefix"]),
            auth_base_path=str(s["auth_base_path"]),
            body=str(s["body"]),
            provided_keys=frozenset(s.get("_provided_keys", frozenset())),
        )
        for i, s in enumerate(raw_sections)
    ]

    first = raw_sections[0]
    return Scenario(
        path=scenario_path,
        file_path=scenario_file,
        email=str(first["email"]),
        password=str(first["password"]),
        start_page=str(first["start_page"]),
        use_cookie=bool(first["use_cookie"]),
        auth_provider=str(first["auth_provider"]),
        auth_cookie_prefix=str(first["auth_cookie_prefix"]),
        auth_base_path=str(first["auth_base_path"]),
        body=str(first["body"]),
        raw_content=raw_content,
        sections=sections,
    )


def override_section(section: ScenarioSection, overrides: ScenarioOverrides) -> ScenarioSection:
    email = overrides.email if overrides.email is not None else section.email
    password = overrides.password if overrides.password is not None else section.password
    start_page = overrides.start_page if overrides.start_page is not None else section.start_page
    use_cookie = overrides.use_cookie if overrides.use_cookie is not None else section.use_cookie
    auth_provider = (
        overrides.auth_provider if overrides.auth_provider is not None else section.auth_provider
    )
    auth_cookie_prefix = (
        overrides.auth_cookie_prefix
        if overrides.auth_cookie_prefix is not None
        else section.auth_cookie_prefix
    )
    auth_base_path = (
        overrides.auth_base_path
        if overrides.auth_base_path is not None
        else section.auth_base_path
    )
    return ScenarioSection(
        index=section.index,
        label=email or f"section-{section.index}",
        email=email,
        password=password,
        start_page=start_page,
        use_cookie=use_cookie,
        auth_provider=auth_provider,
        auth_cookie_prefix=auth_cookie_prefix,
        auth_base_path=auth_base_path,
        body=section.body,
        provided_keys=section.provided_keys,
    )


def apply_scenario_overrides(scenario: Scenario, overrides: ScenarioOverrides | None) -> Scenario:
    if overrides is None or not overrides.has_overrides():
        return scenario

    sections = [override_section(section, overrides) for section in scenario.sections]
    first = sections[0]
    return Scenario(
        path=scenario.path,
        file_path=scenario.file_path,
        email=first.email,
        password=first.password,
        start_page=first.start_page,
        use_cookie=first.use_cookie,
        auth_provider=first.auth_provider,
        auth_cookie_prefix=first.auth_cookie_prefix,
        auth_base_path=first.auth_base_path,
        body=first.body,
        raw_content=scenario.raw_content,
        sections=sections,
    )


def apply_target_defaults_to_section(section: ScenarioSection, defaults: ScenarioDefaults) -> ScenarioSection:
    if not defaults.has_values():
        return section

    email = section.email
    if "email" not in section.provided_keys and defaults.email is not None:
        email = defaults.email

    password = section.password
    if "password" not in section.provided_keys and defaults.password is not None:
        password = defaults.password

    start_page = section.start_page
    if "start_page" not in section.provided_keys and defaults.start_page is not None:
        start_page = defaults.start_page

    use_cookie = section.use_cookie
    if "use_cookie" not in section.provided_keys and defaults.use_cookie is not None:
        use_cookie = defaults.use_cookie

    auth_provider = section.auth_provider
    if "auth_provider" not in section.provided_keys and defaults.auth_provider is not None:
        auth_provider = defaults.auth_provider

    auth_cookie_prefix = section.auth_cookie_prefix
    if (
        "auth_cookie_prefix" not in section.provided_keys
        and defaults.auth_cookie_prefix is not None
    ):
        auth_cookie_prefix = defaults.auth_cookie_prefix

    auth_base_path = section.auth_base_path
    if (
        "auth_base_path" not in section.provided_keys
        and defaults.auth_base_path is not None
    ):
        auth_base_path = defaults.auth_base_path

    return ScenarioSection(
        index=section.index,
        label=email or f"section-{section.index}",
        email=email,
        password=password,
        start_page=start_page,
        use_cookie=use_cookie,
        auth_provider=auth_provider,
        auth_cookie_prefix=auth_cookie_prefix,
        auth_base_path=auth_base_path,
        body=section.body,
        provided_keys=section.provided_keys,
    )


def apply_target_defaults(scenario: Scenario, defaults: ScenarioDefaults) -> Scenario:
    if not defaults.has_values():
        return scenario

    sections = [apply_target_defaults_to_section(section, defaults) for section in scenario.sections]
    first = sections[0]
    return Scenario(
        path=scenario.path,
        file_path=scenario.file_path,
        email=first.email,
        password=first.password,
        start_page=first.start_page,
        use_cookie=first.use_cookie,
        auth_provider=first.auth_provider,
        auth_cookie_prefix=first.auth_cookie_prefix,
        auth_base_path=first.auth_base_path,
        body=first.body,
        raw_content=scenario.raw_content,
        sections=sections,
    )


def parse_scenario(content: str) -> dict[str, object]:
    result: dict[str, object] = {
        "email": "",
        "password": "",
        "start_page": "/dashboard",
        "use_cookie": True,
        "auth_provider": DEFAULT_AUTH_PROVIDER,
        "auth_cookie_prefix": DEFAULT_BETTER_AUTH_COOKIE_PREFIX,
        "auth_base_path": DEFAULT_AUTH_BASE_PATH,
        "body": "",
    }
    provided_keys: set[str] = set()

    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?", content, re.DOTALL)
    if not match:
        raise RuntimeError("Scenario file missing frontmatter (---)")

    for line in match.group(1).splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if key == "email":
            result["email"] = value
            provided_keys.add("email")
        elif key == "password":
            result["password"] = value
            provided_keys.add("password")
        elif key == "start_page":
            result["start_page"] = value
            provided_keys.add("start_page")
        elif key == "use_cookie":
            result["use_cookie"] = value.lower() == "true"
            provided_keys.add("use_cookie")
        elif key == "auth_provider":
            if value not in AUTH_PROVIDERS:
                joined = ", ".join(AUTH_PROVIDERS)
                raise RuntimeError(
                    f"Scenario frontmatter auth_provider must be one of: {joined}"
                )
            result["auth_provider"] = value
            provided_keys.add("auth_provider")
        elif key == "auth_cookie_prefix":
            if not value:
                raise RuntimeError("Scenario frontmatter auth_cookie_prefix must be non-empty")
            result["auth_cookie_prefix"] = value
            provided_keys.add("auth_cookie_prefix")
        elif key == "auth_base_path":
            if not value or not value.startswith("/"):
                raise RuntimeError(
                    "Scenario frontmatter auth_base_path must be a path starting with '/'"
                )
            result["auth_base_path"] = value
            provided_keys.add("auth_base_path")

    result["body"] = content[match.end():].strip()
    result["_provided_keys"] = frozenset(provided_keys)
    return result


def _looks_like_frontmatter(text: str) -> bool:
    """Check if a text block looks like section frontmatter.

    A block counts as frontmatter if it contains at least one recognized
    frontmatter field. Credentials are optional for `use_cookie: false`, so
    `email:` alone is too strict.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        key, sep, _ = stripped.partition(":")
        return bool(sep and key.strip() in FRONTMATTER_KEYS)
    return False


def parse_sections(content: str) -> list[dict[str, object]]:
    """Split scenario content into one or more frontmatter+body sections.

    Uses ``email:`` presence to distinguish section boundaries from markdown
    horizontal rules.  Single-section files return a list of length 1.
    """
    parts = re.split(r"^---\s*$", content, flags=re.MULTILINE)

    # Drop empty leading part (content before first ---)
    if parts and not parts[0].strip():
        parts = parts[1:]

    if not parts:
        raise RuntimeError("Scenario file missing frontmatter (---)")

    sections: list[dict[str, object]] = []
    i = 0

    while i < len(parts):
        if not _looks_like_frontmatter(parts[i]):
            if not sections:
                raise RuntimeError("Scenario file missing frontmatter (---)")
            break

        fm_text = parts[i]
        i += 1

        # Collect body segments until the next frontmatter block
        body_segments: list[str] = []
        while i < len(parts) and not _looks_like_frontmatter(parts[i]):
            body_segments.append(parts[i])
            i += 1

        body = "\n---\n".join(body_segments).strip()

        result: dict[str, object] = {
            "email": "",
            "password": "",
            "start_page": "/dashboard",
            "use_cookie": True,
            "auth_provider": DEFAULT_AUTH_PROVIDER,
            "auth_cookie_prefix": DEFAULT_BETTER_AUTH_COOKIE_PREFIX,
            "auth_base_path": DEFAULT_AUTH_BASE_PATH,
            "body": body,
        }
        provided_keys: set[str] = set()
        for line in fm_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            if key == "email":
                result["email"] = value
                provided_keys.add("email")
            elif key == "password":
                result["password"] = value
                provided_keys.add("password")
            elif key == "start_page":
                result["start_page"] = value
                provided_keys.add("start_page")
            elif key == "use_cookie":
                result["use_cookie"] = value.lower() == "true"
                provided_keys.add("use_cookie")
            elif key == "auth_provider":
                if value not in AUTH_PROVIDERS:
                    joined = ", ".join(AUTH_PROVIDERS)
                    raise RuntimeError(
                        f"Scenario frontmatter auth_provider must be one of: {joined}"
                    )
                result["auth_provider"] = value
                provided_keys.add("auth_provider")
            elif key == "auth_cookie_prefix":
                if not value:
                    raise RuntimeError("Scenario frontmatter auth_cookie_prefix must be non-empty")
                result["auth_cookie_prefix"] = value
                provided_keys.add("auth_cookie_prefix")
            elif key == "auth_base_path":
                if not value or not value.startswith("/"):
                    raise RuntimeError(
                        "Scenario frontmatter auth_base_path must be a path starting with '/'"
                    )
                result["auth_base_path"] = value
                provided_keys.add("auth_base_path")

        result["_provided_keys"] = frozenset(provided_keys)
        sections.append(result)

    return sections


def collect_scenarios_from_directory(directory: Path) -> list[Path]:
    return sorted(path.resolve() for path in directory.rglob("*.scenario.md") if "_skip" not in path.parts)


def collect_scenarios_from_glob(project_root: Path, pattern: str) -> list[Path]:
    expanded_pattern = resolve_input_path(project_root, pattern)
    scenario_files: list[Path] = []
    for match in sorted(glob.glob(str(expanded_pattern), recursive=True)):
        path = Path(match).resolve()
        if path.is_dir():
            scenario_files.extend(collect_scenarios_from_directory(path))
            continue
        if path.is_file() and path.name.endswith(".scenario.md") and "_skip" not in path.parts:
            scenario_files.append(path)
    return scenario_files


def dedupe_scenario_files(files: list[Path]) -> list[Path]:
    unique: dict[str, Path] = {}
    for path in files:
        unique[str(path.resolve())] = path.resolve()
    return [unique[key] for key in sorted(unique)]


def find_scenarios(workspace: Workspace, target: str) -> list[str]:
    if glob.has_magic(target):
        scenario_files = collect_scenarios_from_glob(workspace.project_root, target)
    else:
        resolved_target = resolve_input_path(workspace.project_root, target)
        if resolved_target.is_dir():
            scenario_files = collect_scenarios_from_directory(resolved_target)
        else:
            scenario_files = [resolve_scenario_file(workspace.project_root, target)]

    return [scenario_display_path(workspace.project_root, path) for path in dedupe_scenario_files(scenario_files)]


def reserve_port() -> int:
    with PORT_LOCK:
        while True:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
                handle.bind(("127.0.0.1", 0))
                port = int(handle.getsockname()[1])
            if port not in ALLOCATED_PORTS:
                ALLOCATED_PORTS.add(port)
                return port


def release_ports(*ports: int) -> None:
    with PORT_LOCK:
        for port in ports:
            ALLOCATED_PORTS.discard(port)


def start_managed_target(
    workspace: Workspace,
    target: ResolvedTarget,
    *,
    prefix: str = "",
    color: str = "",
) -> subprocess.Popen[str]:
    if target.dev_command is None:
        raise RuntimeError(f"Target '{target.name}' is not managed")

    env = {**os.environ, **target.env}

    log_suffix = str(target.app_port) if target.app_port is not None else slugify(target.name)
    server_log = workspace.logs_dir / f"server-{log_suffix}-{int(time.time())}.log"
    server_log.parent.mkdir(parents=True, exist_ok=True)

    details: list[str] = []
    if target.app_port is not None:
        details.append(f"app {target.app_port}")
    if target.mongo_port is not None:
        details.append(f"mongo {target.mongo_port}")
    detail_text = f" ({', '.join(details)})" if details else ""
    log(f"starting target '{target.name}'{detail_text}...", prefix=prefix, color=color)

    process = subprocess.Popen(
        list(target.dev_command),
        cwd=workspace.project_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    assert process.stdout is not None

    log_handle = server_log.open("w", encoding="utf-8")
    process._server_log_handle = log_handle  # type: ignore[attr-defined]

    def drain() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            log_handle.write(line)
            log_handle.flush()

    thread = threading.Thread(target=drain, daemon=True)
    thread.start()
    return process


def stop_managed_target(process: subprocess.Popen[str] | None, *, prefix: str = "", color: str = "") -> None:
    if process is None:
        return
    log("stopping target...", prefix=prefix, color=color)
    terminate_process_group(process)
    log_handle = getattr(process, "_server_log_handle", None)
    if log_handle and not log_handle.closed:
        log_handle.close()


def wait_for_target_ready(
    base_url: str,
    ready: ReadyCheck,
    *,
    process: subprocess.Popen[str] | None = None,
    prefix: str = "",
    color: str = "",
) -> None:
    if ready.type != "http":
        raise RuntimeError(f"Unsupported ready check type: {ready.type}")

    probe_url = urllib.parse.urljoin(f"{base_url.rstrip('/')}/", ready.path.lstrip("/"))
    deadline = time.time() + ready.timeout_seconds
    while time.time() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"Target exited before becoming ready (exit {process.returncode})")
        try:
            urllib.request.urlopen(probe_url, timeout=3)
            log(f"target ready at {base_url}", prefix=prefix, color=color)
            return
        except Exception:
            time.sleep(1)
    fix = (
        "The app may not be running. Start it at that URL, configure a managed target "
        "in qazy.config.json, or pass --dev-command so Qazy can start it."
    )
    raise RuntimeError(f"Target at {base_url} not responding after {ready.timeout_seconds}s. {fix}")


def authenticate(
    base_url: str,
    email: str,
    password: str,
    *,
    provider: str = DEFAULT_AUTH_PROVIDER,
    cookie_prefix: str = DEFAULT_BETTER_AUTH_COOKIE_PREFIX,
    base_path: str = DEFAULT_AUTH_BASE_PATH,
) -> AuthSession:
    if provider == "nextauth":
        return authenticate_nextauth(base_url, email, password, base_path=base_path)
    if provider == "better-auth":
        return authenticate_better_auth(
            base_url, email, password, cookie_prefix=cookie_prefix, base_path=base_path
        )
    joined = ", ".join(AUTH_PROVIDERS)
    raise RuntimeError(f"Unknown auth provider '{provider}'. Expected one of: {joined}")


def _auth_url(base_url: str, base_path: str, suffix: str) -> str:
    return f"{base_url.rstrip('/')}{base_path.rstrip('/')}{suffix}"


def authenticate_nextauth(
    base_url: str,
    email: str,
    password: str,
    *,
    base_path: str = DEFAULT_AUTH_BASE_PATH,
) -> AuthSession:
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    csrf_resp = opener.open(_auth_url(base_url, base_path, "/csrf"), timeout=10)
    csrf_data = json.loads(csrf_resp.read().decode())
    csrf_token = csrf_data.get("csrfToken", "")

    form_data = urllib.parse.urlencode(
        {
            "email": email,
            "password": password,
            "csrfToken": csrf_token,
            "json": "true",
        }
    ).encode()
    request = urllib.request.Request(
        _auth_url(base_url, base_path, "/callback/credentials"),
        data=form_data,
        method="POST",
    )
    request.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        opener.open(request, timeout=10)
    except urllib.error.HTTPError:
        pass

    for cookie in jar:
        if "session-token" in cookie.name:
            return AuthSession(cookie_name=cookie.name, session_token=cookie.value)

    raise RuntimeError(f"Authentication failed for {email} — no session token returned")


def authenticate_better_auth(
    base_url: str,
    email: str,
    password: str,
    *,
    cookie_prefix: str = DEFAULT_BETTER_AUTH_COOKIE_PREFIX,
    base_path: str = DEFAULT_AUTH_BASE_PATH,
) -> AuthSession:
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    payload = json.dumps({"email": email, "password": password}).encode()
    request = urllib.request.Request(
        _auth_url(base_url, base_path, "/sign-in/email"),
        data=payload,
        method="POST",
    )
    request.add_header("Content-Type", "application/json")
    request.add_header("Origin", base_url.rstrip("/"))

    try:
        opener.open(request, timeout=10)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"Authentication failed for {email} — Better Auth returned HTTP {exc.code}"
        ) from exc

    expected_suffix = f"{cookie_prefix}.session_token"
    for cookie in jar:
        if cookie.name == expected_suffix or cookie.name == f"__Secure-{expected_suffix}":
            return AuthSession(cookie_name=cookie.name, session_token=cookie.value)

    raise RuntimeError(
        f"Authentication failed for {email} — no '{expected_suffix}' cookie returned"
    )


def browser_session_name(run_id: str, scenario_path: str, section_index: int | None = None) -> str:
    run_slug = slugify(run_id)[:20]
    scenario_slug = slugify(Path(scenario_path).name)[:18]
    digest = hashlib.blake2b(scenario_path.encode("utf-8"), digest_size=4).hexdigest()
    section_suffix = f"-s{section_index}" if section_index is not None else ""
    return f"qz-{run_slug}-{scenario_slug}-{digest}{section_suffix}"


def merge_browser_args(existing: str | None, *args: str) -> str:
    values: list[str] = []
    if existing:
        values.extend(part.strip() for part in existing.replace("\n", ",").split(",") if part.strip())
    for arg in args:
        if arg not in values:
            values.append(arg)
    return ",".join(values)


def browser_env(session_name: str, *, headed: bool | None = None) -> dict[str, str]:
    env = {**os.environ, "AGENT_BROWSER_SESSION": session_name}
    if headed is not None:
        env["AGENT_BROWSER_HEADED"] = "true" if headed else "false"
    if headed:
        env["AGENT_BROWSER_ARGS"] = merge_browser_args(
            os.environ.get("AGENT_BROWSER_ARGS"),
            "--start-maximized",
            "--window-position=0,0",
        )
    return env


def slugify(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower() or "qazy"


def build_prompt_scenario(prompt: str) -> Scenario:
    slug = slugify(prompt)[:48] or "ad-hoc"
    scenario_path = f"prompt/{slug}"
    return build_scenario(
        scenario_path,
        Path("<prompt>"),
        prompt,
        [
            {
                "email": "",
                "password": "",
                "start_page": "/dashboard",
                "use_cookie": True,
                "auth_provider": DEFAULT_AUTH_PROVIDER,
                "auth_cookie_prefix": DEFAULT_BETTER_AUTH_COOKIE_PREFIX,
                "auth_base_path": DEFAULT_AUTH_BASE_PATH,
                "body": prompt.strip(),
                "_provided_keys": frozenset(),
            }
        ],
    )


def join_url(base_url: str, path: str) -> str:
    return urllib.parse.urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/"))


def has_complete_credentials(section: ScenarioSection) -> bool:
    return bool(section.email.strip() and section.password.strip())


def missing_credentials_message(section: ScenarioSection, *, scenario_path: str) -> str:
    base = (
        f"No complete scenario credentials provided for '{scenario_path}' section "
        f"{section.index} ({section.label}). "
    )
    if section.use_cookie:
        return (
            base
            + "use_cookie=true requires credentials for built-in auth. Add them to the scenario "
            "frontmatter, set target.scenarioDefaults.email/password, pass --email and --password, "
            "or set use_cookie: false for browser-driven login."
        )
    return (
        base
        + "The runtime will not receive credentials and must not search project files, "
        "environment variables, source code, logs, or config files for them."
    )


def validate_cookie_auth_credentials(section: ScenarioSection, *, scenario_path: str) -> None:
    if has_complete_credentials(section):
        return
    raise RuntimeError(
        f"Scenario '{scenario_path}' section {section.index} ({section.label}) requires email "
        "and password because use_cookie is true. Add them to the scenario frontmatter, "
        "set target.scenarioDefaults.email/password, pass --email and --password, "
        "or set use_cookie: false for browser-driven login."
    )


def validate_cookie_auth_credentials_for_scenario(scenario: Scenario) -> None:
    for section in scenario.sections:
        if section.use_cookie:
            validate_cookie_auth_credentials(section, scenario_path=scenario.path)


def log_missing_credentials(scenario: Scenario, *, prefix: str = "", color: str = "") -> None:
    for section in scenario.sections:
        if not has_complete_credentials(section):
            log(missing_credentials_message(section, scenario_path=scenario.path), prefix=prefix, color=color)


def prime_browser(
    base_url: str,
    start_page: str,
    auth_session: AuthSession,
    *,
    session_name: str,
    headed: bool | None = None,
    prefix: str = "",
    color: str = "",
) -> None:
    env = browser_env(session_name, headed=headed)
    start_url = join_url(base_url, start_page)

    def run_ab(*args: str) -> None:
        result = subprocess.run(
            ["agent-browser", *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"agent-browser {' '.join(args)} failed: {result.stderr.strip()}")

    log("priming browser...", prefix=prefix, color=color)
    run_ab("open", base_url)
    run_ab("cookies", "set", auth_session.cookie_name, auth_session.session_token)
    run_ab("open", start_url)
    if headed:
        run_ab("set", "viewport", str(HEADED_VIEWPORT_WIDTH), str(HEADED_VIEWPORT_HEIGHT))
    time.sleep(2)
    log(f"browser ready at {start_url}", prefix=prefix, color=color)


def prime_browser_no_auth(
    base_url: str,
    start_page: str,
    *,
    session_name: str,
    headed: bool | None = None,
    prefix: str = "",
    color: str = "",
) -> None:
    start_url = join_url(base_url, start_page)
    env = browser_env(session_name, headed=headed)

    def run_ab(*args: str) -> None:
        result = subprocess.run(
            ["agent-browser", *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"agent-browser {' '.join(args)} failed: {result.stderr.strip()}")

    run_ab("open", start_url)
    if headed:
        run_ab("set", "viewport", str(HEADED_VIEWPORT_WIDTH), str(HEADED_VIEWPORT_HEIGHT))
    time.sleep(2)
    log(f"browser ready at {start_url}", prefix=prefix, color=color)


def cleanup_browser_session(session_name: str, *, headed: bool | None = None) -> None:
    subprocess.run(
        ["agent-browser", "close"],
        env=browser_env(session_name, headed=headed),
        capture_output=True,
        text=True,
        timeout=10,
    )


def create_screenshot_helper() -> tempfile.TemporaryDirectory[str]:
    tempdir = tempfile.TemporaryDirectory(prefix="qazy-shot-")
    helper_path = Path(tempdir.name) / "qazy-shot"
    package_root = Path(__file__).resolve().parents[1]
    helper_path.write_text(
        "\n".join(
            [
                f"#!{sys.executable}",
                "import sys",
                f"sys.path.insert(0, {package_root.as_posix()!r})",
                "from qazy.screenshot_helper import main",
                "raise SystemExit(main())",
                "",
            ]
        ),
        encoding="utf-8",
    )
    helper_path.chmod(0o755)
    return tempdir


def screenshot_helper_enabled(strategy: str) -> bool:
    return strategy != "none"


def auto_error_screenshots_enabled(strategy: str) -> bool:
    return strategy in {"error", "single", "checkpoints"}


def auto_final_screenshots_enabled(strategy: str) -> bool:
    return strategy == "single"


def build_screenshot_env(
    helper_dir: Path,
    *,
    screenshot_dir: Path,
    manifest_path: Path,
    prefix: str,
) -> dict[str, str]:
    path_value = os.environ.get("PATH", "")
    helper_path = str(helper_dir)
    full_path = f"{helper_path}{os.pathsep}{path_value}" if path_value else helper_path
    return {
        "PATH": full_path,
        "QAZY_SCREENSHOT_DIR": str(screenshot_dir),
        "QAZY_SCREENSHOT_MANIFEST": str(manifest_path),
        "QAZY_SCREENSHOT_PREFIX": prefix,
    }


def next_screenshot_path(screenshot_dir: Path, *, prefix: str, label: str) -> Path:
    slug = slugify(label) or "shot"
    index = 1 + sum(1 for path in screenshot_dir.glob(f"{prefix}-*.png") if path.is_file())
    return screenshot_dir / f"{prefix}-{index:02d}-{slug}.png"


def append_screenshot(manifest_path: Path, output_path: Path) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("a", encoding="utf-8") as handle:
        handle.write(str(output_path.resolve()) + "\n")


def load_screenshots(manifest_path: Path) -> tuple[Path, ...]:
    if not manifest_path.exists():
        return ()
    screenshots: list[Path] = []
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value:
            continue
        screenshots.append(Path(value))
    return tuple(screenshots)


def format_result_path(path: Path, *, base_dir: Path) -> str:
    try:
        return path.relative_to(base_dir).as_posix()
    except ValueError:
        return path.as_posix()


def capture_browser_screenshot(session_name: str, output_path: Path, *, label: str, headed: bool | None = None) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["agent-browser", "screenshot", str(output_path)],
        env=browser_env(session_name, headed=headed),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"agent-browser screenshot failed for {label}: {result.stderr.strip() or result.stdout.strip()}")
    return output_path


def build_prompt(
    body: str,
    *,
    base_url: str,
    start_page: str,
    email: str,
    password: str,
    primed: bool,
    screenshot_strategy: str,
) -> str:
    start_url = join_url(base_url, start_page)
    if primed:
        setup = f"""\
### Getting Started

The browser is already open, authenticated, and on {start_url}.
Start by running `agent-browser snapshot -c` to inspect the page."""
    else:
        if email.strip() and password.strip():
            credentials_text = f"""\
Use these scenario credentials:
Email: {email}
Password: {password}"""
            scenario_credentials = f"""\
## Scenario Credentials

Use these credentials if the scenario requires login:
Email: {email}
Password: {password}
"""
        else:
            credentials_text = """\
No complete scenario credentials were provided.
Do not search project files, source code, environment variables, logs, config files, or browser storage for credentials.
If login requires credentials and none are present in the scenario text, report the item as UNTESTABLE.
Do not sign up or create an account unless the scenario explicitly asks you to."""
            scenario_credentials = """\
## Scenario Credentials

No complete scenario credentials were provided.
Do not search project files, source code, environment variables, logs, config files, or browser storage for credentials.
If login requires credentials and none are present in the scenario text, report the item as UNTESTABLE.
Do not sign up or create an account unless the scenario explicitly asks you to.
"""
        setup = f"""\
### Getting Started

The browser is open at {start_url}. {credentials_text}
Then begin testing."""
    if primed:
        scenario_credentials = ""

    screenshot_instructions = ""
    if screenshot_strategy == "error":
        screenshot_instructions = """\

### Screenshots

Use `qazy-shot <short-label>` if you encounter a failure or unexpected UI state.
Name the screenshot for the error you are documenting.
Do not use `agent-browser screenshot` directly."""
    elif screenshot_strategy == "single":
        screenshot_instructions = """\

### Screenshots

Qazy will save one final screenshot automatically.
If you encounter a failure or unexpected UI state, use `qazy-shot <short-label>` to save a named screenshot of it.
Do not use `agent-browser screenshot` directly."""
    elif screenshot_strategy == "checkpoints":
        screenshot_instructions = """\

### Screenshots

Use `qazy-shot <short-label>` to document key checkpoints.
Take screenshots for the initial page-under-test state, major navigations or redirects, key mutations, and any failure or unexpected UI state.
Name each screenshot for the state it captures.
Do not use `agent-browser screenshot` directly."""

    return f"""\
You are a QA tester. Test features in the browser using agent-browser CLI via Bash.

{AGENT_BROWSER_GUIDE}

### Server

The target app is already available at {base_url}.
Do not start or stop it yourself.

{setup}
{screenshot_instructions}

### Scenario

{scenario_credentials}
{body}

### Instructions

1. Test each checklist item one at a time.
2. Always snapshot after actions. Refs change on every snapshot.
3. Do not read or write project files. Use browser automation only.
4. Only create or modify data when the scenario requires it.
5. If one item fails, navigate back to `/dashboard` and continue with the next one.
6. Output the final report as plain text.

### Report Format

```
PASS — scenario description
  Detail of what was verified
FAIL — scenario description
  What went wrong
UNTESTABLE — scenario description
  Reason
```

End with: `X passed, Y failed, Z untestable out of N`
"""


def parse_report(report_text: str) -> ReportSummary:
    passed = failed = untestable = 0
    for line in report_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("PASS"):
            passed += 1
        elif stripped.startswith("FAIL"):
            failed += 1
        elif stripped.startswith("UNTESTABLE"):
            untestable += 1

    total = passed + failed + untestable
    if failed:
        status = "failed"
    elif passed:
        status = "passed"
    elif untestable:
        status = "untestable"
    else:
        status = "unknown"
    return ReportSummary(passed=passed, failed=failed, untestable=untestable, total=total, status=status)


def load_usage_totals(log_path: Path) -> UsageTotals | None:
    if not log_path.exists():
        return None
    return analyze_log(log_path)


def aggregate_usage_totals(results: list[ScenarioRunResult]) -> UsageTotals | None:
    combined = UsageTotals()
    has_usage = False
    for result in results:
        if result.usage_totals is None:
            continue
        combined.add(result.usage_totals)
        has_usage = True
    return combined if has_usage else None


def _run_single_section(
    *,
    workspace: Workspace,
    scenario: Scenario,
    section: ScenarioSection,
    target: ResolvedTarget,
    runtime: RuntimeAdapter,
    run_id: str,
    base_url: str,
    results_dir: Path,
    prefix: str,
    color: str,
    model: str | None = None,
    reasoning_effort: str | None = None,
    screenshot_strategy: str = "error",
    headed: bool | None = None,
) -> ScenarioRunResult:
    """Run one section against a pre-running server.  Handles auth, browser, runtime, and results."""
    multi = len(scenario.sections) > 1
    session_name = browser_session_name(run_id, scenario.path, section.index if multi else None)
    section_suffix = f"-s{section.index}" if multi else ""
    section_prefix = f"{prefix}:{section.label}" if multi else prefix
    log_path = workspace.logs_dir / f"{runtime.name}-{scenario.path.replace('/', '-')}{section_suffix}-{int(time.time())}.log"
    results_file = results_dir / f"{scenario.path.replace('/', '--')}{section_suffix}.md"
    usage_totals: UsageTotals | None = None
    screenshots: tuple[Path, ...] = ()
    helper_tempdir: tempfile.TemporaryDirectory[str] | None = None
    manifest_path: Path | None = None
    screenshot_dir: Path | None = None
    screenshot_prefix: str | None = None
    effective_model = runtime.effective_model(model)

    try:
        primed = False
        if section.use_cookie:
            validate_cookie_auth_credentials(section, scenario_path=scenario.path)
            log("authenticating...", prefix=section_prefix, color=color)
            auth_session = authenticate(
                base_url,
                section.email,
                section.password,
                provider=section.auth_provider,
                cookie_prefix=section.auth_cookie_prefix,
                base_path=section.auth_base_path,
            )
            log(f"authenticated as {section.email}", prefix=section_prefix, color=color)
            prime_browser(
                base_url,
                section.start_page,
                auth_session,
                session_name=session_name,
                headed=headed,
                prefix=section_prefix,
                color=color,
            )
            primed = True
        else:
            prime_browser_no_auth(
                base_url,
                section.start_page,
                session_name=session_name,
                headed=headed,
                prefix=section_prefix,
                color=color,
            )

        runtime_env = browser_env(session_name, headed=headed)
        if screenshot_helper_enabled(screenshot_strategy):
            screenshot_dir = results_dir / "screenshots"
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = screenshot_dir / f"{scenario.path.replace('/', '--')}{section_suffix}.manifest"
            screenshot_prefix = f"{scenario.path.replace('/', '--')}{section_suffix}"
            helper_tempdir = create_screenshot_helper()
            runtime_env.update(
                build_screenshot_env(
                    Path(helper_tempdir.name),
                    screenshot_dir=screenshot_dir,
                    manifest_path=manifest_path,
                    prefix=screenshot_prefix,
                )
            )
        elif screenshot_strategy == "single":
            screenshot_dir = results_dir / "screenshots"
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = screenshot_dir / f"{scenario.path.replace('/', '--')}{section_suffix}.manifest"
            screenshot_prefix = f"{scenario.path.replace('/', '--')}{section_suffix}"

        prompt = build_prompt(
            section.body,
            base_url=base_url,
            start_page=section.start_page,
            email=section.email,
            password=section.password,
            primed=primed,
            screenshot_strategy=screenshot_strategy,
        )

        def emit_runtime_progress(message: str) -> None:
            for line in message.splitlines() or [message]:
                if line.strip():
                    log(line, prefix=section_prefix, color=color)

        invocation = invoke_runtime(
            runtime,
            prompt,
            cwd=workspace.project_root,
            log_path=log_path,
            extra_env=runtime_env,
            on_progress=emit_runtime_progress,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        final_report = invocation.final_text.strip()
        report_summary = parse_report(final_report)
        status = report_summary.status if report_summary.status in {"passed", "failed"} else "unknown"
        usage_totals = load_usage_totals(log_path)
        if (
            manifest_path is not None
            and screenshot_dir is not None
            and screenshot_prefix is not None
            and status == "failed"
            and auto_error_screenshots_enabled(screenshot_strategy)
            and not load_screenshots(manifest_path)
        ):
            output_path = next_screenshot_path(screenshot_dir, prefix=screenshot_prefix, label="failed")
            capture_browser_screenshot(session_name, output_path, label="failed", headed=headed)
            append_screenshot(manifest_path, output_path)
        if (
            manifest_path is not None
            and screenshot_dir is not None
            and screenshot_prefix is not None
            and status != "failed"
            and auto_final_screenshots_enabled(screenshot_strategy)
            and not load_screenshots(manifest_path)
        ):
            output_path = next_screenshot_path(screenshot_dir, prefix=screenshot_prefix, label="final")
            capture_browser_screenshot(session_name, output_path, label="final", headed=headed)
            append_screenshot(manifest_path, output_path)
        if manifest_path is not None:
            screenshots = load_screenshots(manifest_path)
        write_result_file(
            results_file=results_file,
            scenario=scenario,
            run_id=run_id,
            target_name=target.name,
            target_mode=target.mode,
            runtime=runtime.name,
            model=effective_model,
            base_url=base_url,
            status=status,
            final_report=final_report,
            report_summary=report_summary,
            error_text=None,
            section=section if multi else None,
            usage_totals=usage_totals,
            screenshots=screenshots,
        )
        log(f"results written to {results_file}", prefix=section_prefix, color=color)
        if usage_totals is not None:
            log(f"Total tokens: {format_usage_inline(usage_totals)}", prefix=section_prefix, color=color)
        if screenshots:
            log(f"Screenshots: {len(screenshots)} saved", prefix=section_prefix, color=color)
        return ScenarioRunResult(
            scenario_path=scenario.path,
            run_id=run_id,
            runtime=runtime.name,
            base_url=base_url,
            results_file=results_file,
            log_file=log_path,
            final_report=final_report,
            report_summary=report_summary,
            status=status,
            usage_totals=usage_totals,
            screenshots=screenshots,
        )
    except Exception as exc:
        error_text = str(exc)
        summary = ReportSummary(passed=0, failed=0, untestable=0, total=0, status="error")
        usage_totals = load_usage_totals(log_path)
        if (
            manifest_path is not None
            and screenshot_dir is not None
            and screenshot_prefix is not None
            and auto_error_screenshots_enabled(screenshot_strategy)
            and not load_screenshots(manifest_path)
        ):
            try:
                output_path = next_screenshot_path(screenshot_dir, prefix=screenshot_prefix, label="error")
                capture_browser_screenshot(session_name, output_path, label="error", headed=headed)
                append_screenshot(manifest_path, output_path)
            except Exception as screenshot_exc:
                log(str(screenshot_exc), prefix=section_prefix, color=color)
        if manifest_path is not None:
            screenshots = load_screenshots(manifest_path)
        write_result_file(
            results_file=results_file,
            scenario=scenario,
            run_id=run_id,
            target_name=target.name,
            target_mode=target.mode,
            runtime=runtime.name,
            model=effective_model,
            base_url=base_url,
            status="error",
            final_report="",
            report_summary=summary,
            error_text=error_text,
            section=section if multi else None,
            usage_totals=usage_totals,
            screenshots=screenshots,
        )
        if usage_totals is not None:
            log(f"Total tokens: {format_usage_inline(usage_totals)}", prefix=section_prefix, color=color)
        if screenshots:
            log(f"Screenshots: {len(screenshots)} saved", prefix=section_prefix, color=color)
        return ScenarioRunResult(
            scenario_path=scenario.path,
            run_id=run_id,
            runtime=runtime.name,
            base_url=base_url,
            results_file=results_file,
            log_file=log_path,
            final_report="",
            report_summary=summary,
            status="error",
            usage_totals=usage_totals,
            screenshots=screenshots,
        )
    finally:
        cleanup_browser_session(session_name, headed=headed)
        if helper_tempdir is not None:
            helper_tempdir.cleanup()


def prepare_scenario(
    scenario: Scenario,
    *,
    target: TargetDefinition,
    scenario_overrides: ScenarioOverrides | None = None,
) -> Scenario:
    return apply_scenario_overrides(
        apply_target_defaults(scenario, target.scenario_defaults),
        scenario_overrides,
    )


def _run_prepared_scenario(
    workspace: Workspace,
    scenario: Scenario,
    *,
    target: TargetDefinition,
    runtime_name: str = "claude",
    model: str | None = None,
    reasoning_effort: str | None = None,
    run_id: str | None = None,
    app_port: int | None = None,
    mongo_port: int | None = None,
    timeout: int | None = None,
    dev_command: tuple[str, ...] | None = None,
    screenshot_strategy: str = "error",
    headed: bool | None = None,
) -> ScenarioRunResult:
    runtime = get_runtime(runtime_name)
    actual_run_id = run_id or generate_run_id()
    resolved_target = resolve_target(
        target,
        dev_command_override=dev_command,
        app_port_override=app_port,
        mongo_port_override=mongo_port,
        timeout_override=timeout,
        allocate_port=reserve_port if target.mode == "managed" else None,
    )
    base_url = resolved_target.base_url
    prefix = scenario.path.replace("/", ":")
    color = color_for_index(0)
    results_dir = workspace.results_dir / actual_run_id

    server: subprocess.Popen[str] | None = None
    log_path = workspace.logs_dir / f"{runtime.name}-{scenario.path.replace('/', '-')}-{int(time.time())}.log"
    results_file = results_dir / f"{scenario.path.replace('/', '--')}.md"

    log(f"Scenario:   {scenario.path}")
    log(f"Scenario file: {scenario.file_path}")
    log(f"Run ID:     {actual_run_id}")
    log(f"Target:     {resolved_target.name} ({resolved_target.mode})")
    log(f"Runtime:    {runtime.name}")
    effective_model = runtime.effective_model(model)
    if effective_model:
        log(f"Model:      {effective_model}")
    log(f"Base URL:   {base_url}")
    if resolved_target.app_port is not None:
        log(f"App port:   {resolved_target.app_port}")
    if resolved_target.mongo_port is not None:
        log(f"Mongo port: {resolved_target.mongo_port}")
    log(f"Session:    {browser_session_name(actual_run_id, scenario.path)}")
    log(f"Results:    {results_file}")
    log(f"Log:        {log_path}")
    log("")
    log_missing_credentials(scenario, prefix=prefix, color=color)

    try:
        validate_cookie_auth_credentials_for_scenario(scenario)
        if len(scenario.sections) > 1:
            return _run_multi_section(
                workspace=workspace,
                scenario=scenario,
                target=resolved_target,
                runtime=runtime,
                run_id=actual_run_id,
                results_dir=results_dir,
                prefix=prefix,
                color=color,
                model=model,
                reasoning_effort=reasoning_effort,
                screenshot_strategy=screenshot_strategy,
                headed=headed,
            )

        if resolved_target.mode == "managed":
            server = start_managed_target(workspace, resolved_target, prefix=prefix, color=color)
        wait_for_target_ready(
            base_url,
            resolved_target.ready,
            process=server,
            prefix=prefix,
            color=color,
        )
        return _run_single_section(
            workspace=workspace,
            scenario=scenario,
            section=scenario.sections[0],
            target=resolved_target,
            runtime=runtime,
            run_id=actual_run_id,
            base_url=base_url,
            results_dir=results_dir,
            prefix=prefix,
            color=color,
            model=model,
            reasoning_effort=reasoning_effort,
            screenshot_strategy=screenshot_strategy,
            headed=headed,
        )
    except Exception as exc:
        error_text = str(exc)
        summary = ReportSummary(passed=0, failed=0, untestable=0, total=0, status="error")
        write_result_file(
            results_file=results_file,
            scenario=scenario,
            run_id=actual_run_id,
            target_name=resolved_target.name,
            target_mode=resolved_target.mode,
            runtime=runtime.name,
            model=effective_model,
            base_url=base_url,
            status="error",
            final_report="",
            report_summary=summary,
            error_text=error_text,
            usage_totals=None,
            screenshots=(),
        )
        return ScenarioRunResult(
            scenario_path=scenario.path,
            run_id=actual_run_id,
            runtime=runtime.name,
            base_url=base_url,
            results_file=results_file,
            log_file=log_path,
            final_report="",
            report_summary=summary,
            status="error",
            usage_totals=None,
            screenshots=(),
        )
    finally:
        stop_managed_target(server, prefix=prefix, color=color)
        release_ports(*[port for port in (resolved_target.app_port, resolved_target.mongo_port) if port is not None])


def run_scenario(
    workspace: Workspace,
    scenario_arg: str,
    *,
    target: TargetDefinition,
    runtime_name: str = "claude",
    model: str | None = None,
    reasoning_effort: str | None = None,
    run_id: str | None = None,
    app_port: int | None = None,
    mongo_port: int | None = None,
    timeout: int | None = None,
    dev_command: tuple[str, ...] | None = None,
    scenario_overrides: ScenarioOverrides | None = None,
    screenshot_strategy: str = "error",
    headed: bool | None = None,
) -> ScenarioRunResult:
    scenario = prepare_scenario(
        load_scenario(workspace, scenario_arg),
        target=target,
        scenario_overrides=scenario_overrides,
    )
    return _run_prepared_scenario(
        workspace,
        scenario,
        target=target,
        runtime_name=runtime_name,
        model=model,
        reasoning_effort=reasoning_effort,
        run_id=run_id,
        app_port=app_port,
        mongo_port=mongo_port,
        timeout=timeout,
        dev_command=dev_command,
        screenshot_strategy=screenshot_strategy,
        headed=headed,
    )


def run_prompt(
    workspace: Workspace,
    prompt: str,
    *,
    target: TargetDefinition,
    runtime_name: str = "claude",
    model: str | None = None,
    reasoning_effort: str | None = None,
    run_id: str | None = None,
    app_port: int | None = None,
    mongo_port: int | None = None,
    timeout: int | None = None,
    dev_command: tuple[str, ...] | None = None,
    scenario_overrides: ScenarioOverrides | None = None,
    screenshot_strategy: str = "error",
    headed: bool | None = None,
) -> ScenarioRunResult:
    scenario = prepare_scenario(
        build_prompt_scenario(prompt),
        target=target,
        scenario_overrides=scenario_overrides,
    )
    return _run_prepared_scenario(
        workspace,
        scenario,
        target=target,
        runtime_name=runtime_name,
        model=model,
        reasoning_effort=reasoning_effort,
        run_id=run_id,
        app_port=app_port,
        mongo_port=mongo_port,
        timeout=timeout,
        dev_command=dev_command,
        screenshot_strategy=screenshot_strategy,
        headed=headed,
    )


def _run_multi_section(
    *,
    workspace: Workspace,
    scenario: Scenario,
    target: ResolvedTarget,
    runtime: RuntimeAdapter,
    run_id: str,
    results_dir: Path,
    prefix: str,
    color: str,
    model: str | None,
    reasoning_effort: str | None,
    screenshot_strategy: str,
    headed: bool | None,
) -> ScenarioRunResult:
    """Start ONE server and run each section sequentially against it."""
    server: subprocess.Popen[str] | None = None
    section_results: list[ScenarioRunResult] = []
    server_error: str | None = None
    base_url = target.base_url

    log(f"Scenario:   {scenario.path} ({len(scenario.sections)} sections)")
    log(f"Scenario file: {scenario.file_path}")
    log(f"Run ID:     {run_id}")
    log(f"Target:     {target.name} ({target.mode})")
    log(f"Runtime:    {runtime.name}")
    effective_model = runtime.effective_model(model)
    if effective_model:
        log(f"Model:      {effective_model}")
    log(f"Base URL:   {base_url}")
    if target.app_port is not None:
        log(f"App port:   {target.app_port}")
    if target.mongo_port is not None:
        log(f"Mongo port: {target.mongo_port}")
    log(f"Results:    {results_dir}")
    log("")

    try:
        if target.mode == "managed":
            server = start_managed_target(workspace, target, prefix=prefix, color=color)
        wait_for_target_ready(
            base_url,
            target.ready,
            process=server,
            prefix=prefix,
            color=color,
        )
        for section in scenario.sections:
            log(f"--- section {section.index}: {section.label} ---", prefix=prefix, color=color)
            result = _run_single_section(
                workspace=workspace,
                scenario=scenario,
                section=section,
                target=target,
                runtime=runtime,
                run_id=run_id,
                base_url=base_url,
                results_dir=results_dir,
                prefix=prefix,
                color=color,
                model=model,
                reasoning_effort=reasoning_effort,
                screenshot_strategy=screenshot_strategy,
                headed=headed,
            )
            section_results.append(result)
    except Exception as exc:
        server_error = str(exc)
    finally:
        stop_managed_target(server, prefix=prefix, color=color)
        release_ports(*[port for port in (target.app_port, target.mongo_port) if port is not None])

    combined_file = results_dir / f"{scenario.path.replace('/', '--')}.md"

    if not section_results:
        summary = ReportSummary(passed=0, failed=0, untestable=0, total=0, status="error")
        write_result_file(
            results_file=combined_file,
            scenario=scenario,
            run_id=run_id,
            target_name=target.name,
            target_mode=target.mode,
            runtime=runtime.name,
            model=effective_model,
            base_url=base_url,
            status="error",
            final_report="",
            report_summary=summary,
            error_text=server_error or "No sections completed",
            usage_totals=None,
            screenshots=(),
        )
        return ScenarioRunResult(
            scenario_path=scenario.path,
            run_id=run_id,
            runtime=runtime.name,
            base_url=base_url,
            results_file=combined_file,
            log_file=workspace.logs_dir / "empty.log",
            final_report="",
            report_summary=summary,
            status="error",
            usage_totals=None,
            screenshots=(),
        )

    # Aggregate
    agg_passed = sum(r.report_summary.passed for r in section_results)
    agg_failed = sum(r.report_summary.failed for r in section_results)
    agg_untestable = sum(r.report_summary.untestable for r in section_results)
    agg_total = sum(r.report_summary.total for r in section_results)

    if any(r.status == "failed" for r in section_results):
        agg_status = "failed"
    elif any(r.status == "error" for r in section_results):
        agg_status = "error"
    elif all(r.status == "passed" for r in section_results):
        agg_status = "passed"
    else:
        agg_status = "unknown"

    agg_summary = ReportSummary(
        passed=agg_passed,
        failed=agg_failed,
        untestable=agg_untestable,
        total=agg_total,
        status=agg_status,
    )
    agg_usage_totals = aggregate_usage_totals(section_results)
    agg_screenshots = tuple(path for result in section_results for path in result.screenshots)

    combined_report = "\n\n".join(
        f"## Section {i}: {scenario.sections[i].label}\n\n{r.final_report}"
        for i, r in enumerate(section_results)
    )

    write_result_file(
        results_file=combined_file,
        scenario=scenario,
        run_id=run_id,
        target_name=target.name,
        target_mode=target.mode,
        runtime=runtime.name,
        model=effective_model,
        base_url=base_url,
        status=agg_status,
        final_report=combined_report,
        report_summary=agg_summary,
        error_text=None,
        usage_totals=agg_usage_totals,
        screenshots=agg_screenshots,
    )
    log(f"results written to {combined_file}", prefix=prefix, color=color)
    if agg_usage_totals is not None:
        log(f"Total tokens: {format_usage_inline(agg_usage_totals)}", prefix=prefix, color=color)
    if agg_screenshots:
        log(f"Screenshots: {len(agg_screenshots)} saved", prefix=prefix, color=color)

    return ScenarioRunResult(
        scenario_path=scenario.path,
        run_id=run_id,
        runtime=runtime.name,
        base_url=base_url,
        results_file=combined_file,
        log_file=section_results[-1].log_file,
        final_report=combined_report,
        report_summary=agg_summary,
        status=agg_status,
        usage_totals=agg_usage_totals,
        screenshots=agg_screenshots,
    )


def run_batch(
    workspace: Workspace,
    pattern: str,
    *,
    target: TargetDefinition,
    runtime_name: str = "claude",
    model: str | None = None,
    reasoning_effort: str | None = None,
    parallel: bool = False,
    max_workers: int | None = None,
    timeout: int | None = None,
    dev_command: tuple[str, ...] | None = None,
    scenario_overrides: ScenarioOverrides | None = None,
    screenshot_strategy: str = "error",
    headed: bool | None = None,
) -> BatchRunResult:
    scenarios = find_scenarios(workspace, pattern)
    if not scenarios:
        raise FileNotFoundError(f"No scenarios matching: {pattern}")
    if parallel and not target.parallel_safe:
        raise RuntimeError(f"Target '{target.name}' does not support parallel batch runs")

    actual_run_id = generate_run_id()
    mode = "parallel" if parallel else "sequential"
    runtime = get_runtime(runtime_name)
    effective_model = runtime.effective_model(model)
    log(f"Run ID: {actual_run_id}")
    log(f"Target: {target.name} ({target.mode})")
    log(f"Runtime: {runtime_name}")
    if effective_model:
        log(f"Model: {effective_model}")
    log(f"Found {len(scenarios)} scenarios matching '{pattern}' ({mode}):")
    for scenario_path in scenarios:
        log(f"  {scenario_path}")
    log("")

    results: list[ScenarioRunResult] = []

    if parallel:
        worker_count = min(max_workers or 4, len(scenarios))
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(
                    run_scenario,
                    workspace,
                    scenario_path,
                    target=target,
                    runtime_name=runtime_name,
                    model=model,
                    reasoning_effort=reasoning_effort,
                    run_id=actual_run_id,
                    timeout=timeout,
                    dev_command=dev_command,
                    scenario_overrides=scenario_overrides,
                    screenshot_strategy=screenshot_strategy,
                    headed=headed,
                ): scenario_path
                for scenario_path in scenarios
            }
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())
    else:
        for scenario_path in scenarios:
            results.append(
                run_scenario(
                    workspace,
                    scenario_path,
                    target=target,
                    runtime_name=runtime_name,
                    model=model,
                    reasoning_effort=reasoning_effort,
                    run_id=actual_run_id,
                    timeout=timeout,
                    dev_command=dev_command,
                    scenario_overrides=scenario_overrides,
                    screenshot_strategy=screenshot_strategy,
                    headed=headed,
                )
            )

    passed = sorted(result.scenario_path for result in results if result.status == "passed")
    failed = sorted(result.scenario_path for result in results if result.status == "failed")
    errors = sorted(result.scenario_path for result in results if result.status == "error")
    results_dir = workspace.results_dir / actual_run_id
    results_dir.mkdir(parents=True, exist_ok=True)
    summary_text = build_batch_summary(
        pattern=pattern,
        run_id=actual_run_id,
        target_name=target.name,
        target_mode=target.mode,
        runtime=runtime_name,
        model=effective_model,
        mode=mode,
        passed=passed,
        failed=failed,
        errors=errors,
    )
    (results_dir / "summary.md").write_text(summary_text, encoding="utf-8")

    log("")
    log("=" * 60)
    log(f"Run ID: {actual_run_id}")
    log(f"Batch complete: {len(passed)} passed, {len(failed)} failed, {len(errors)} errors out of {len(results)}")
    log(f"Results: {results_dir}")
    log("=" * 60)

    return BatchRunResult(
        run_id=actual_run_id,
        runtime=runtime_name,
        mode=mode,
        results_dir=results_dir,
        passed=passed,
        failed=failed,
        errors=errors,
    )


def build_batch_summary(
    *,
    pattern: str,
    run_id: str,
    target_name: str,
    target_mode: str,
    runtime: str,
    model: str | None,
    mode: str,
    passed: list[str],
    failed: list[str],
    errors: list[str],
) -> str:
    lines = [
        f"# Batch QA: {pattern}",
        "",
        f"**Run ID**: {run_id}",
        f"**Target**: {target_name} ({target_mode})",
        f"**Runtime**: {runtime}",
    ]
    if model:
        lines.append(f"**Model**: {model}")
    lines.extend(
        [
            f"**Date**: {time.strftime('%Y-%m-%d')}",
            f"**Mode**: {mode}",
            "",
            "## Summary",
            "",
            f"- Passed: {len(passed)}",
            f"- Failed: {len(failed)}",
            f"- Errors: {len(errors)}",
            f"- Total: {len(passed) + len(failed) + len(errors)}",
            "",
        ]
    )
    if failed:
        lines.extend(["## Failed", "", *[f"- {item}" for item in failed], ""])
    if errors:
        lines.extend(["## Errors", "", *[f"- {item}" for item in errors], ""])
    if passed:
        lines.extend(["## Passed", "", *[f"- {item}" for item in passed], ""])
    return "\n".join(lines).rstrip() + "\n"


def write_result_file(
    *,
    results_file: Path,
    scenario: Scenario,
    run_id: str,
    target_name: str,
    target_mode: str,
    runtime: str,
    model: str | None = None,
    base_url: str,
    status: str,
    final_report: str,
    report_summary: ReportSummary,
    error_text: str | None,
    section: ScenarioSection | None = None,
    usage_totals: UsageTotals | None = None,
    screenshots: tuple[Path, ...] = (),
) -> None:
    results_file.parent.mkdir(parents=True, exist_ok=True)
    title = scenario.path
    email = scenario.email
    if section is not None:
        title = f"{scenario.path} — section {section.index} ({section.label})"
        email = section.email
    lines = [
        f"# Qazy Results: {title}",
        "",
        f"**Run ID**: {run_id}",
        f"**Date**: {time.strftime('%Y-%m-%d')}",
        f"**Target**: {target_name} ({target_mode})",
        f"**Runtime**: {runtime}",
    ]
    if model:
        lines.append(f"**Model**: {model}")
    lines.extend(
        [
            f"**Server**: {base_url}",
            f"**Email**: {email}",
            f"**Status**: {status.upper()}",
        ]
    )
    if usage_totals is not None:
        lines.append(f"**Tokens**: {format_usage_inline(usage_totals)}")
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Passed: {report_summary.passed}",
            f"- Failed: {report_summary.failed}",
            f"- Untestable: {report_summary.untestable}",
            f"- Total: {report_summary.total}",
            "",
        ]
    )
    if screenshots:
        lines.extend(["## Screenshots", ""])
        lines.extend(f"- {format_result_path(path, base_dir=results_file.parent)}" for path in screenshots)
        lines.append("")
    if error_text:
        lines.extend(["## Error", "", error_text, ""])
    if final_report:
        lines.extend(["## Final Report", "", final_report, ""])
    results_file.write_text("\n".join(lines), encoding="utf-8")


def compute_new_path(scenarios_dir: Path, old_path: Path) -> Path:
    rel = old_path.parent.relative_to(scenarios_dir)
    parts = list(rel.parts)

    if len(parts) == 1:
        return scenarios_dir / f"{parts[0]}.scenario.md"

    if parts[0] in {"page", "pages"}:
        head = parts[0]
        remaining = parts[1:]
        if len(remaining) >= 2:
            parent_parts = remaining[:-2]
            page = remaining[-2]
            role = remaining[-1]
            filename = f"{page}.{role}.scenario.md"
            if parent_parts:
                return scenarios_dir / head / Path(*parent_parts) / filename
            return scenarios_dir / head / filename
        if len(remaining) == 1:
            return scenarios_dir / head / f"{remaining[0]}.scenario.md"

    return scenarios_dir / f"{'.'.join(parts)}.scenario.md"


def rename_scenarios(workspace: Workspace, *, write: bool) -> list[tuple[Path, Path]]:
    scenarios_dir = workspace.scenarios_dir
    candidates = sorted(scenarios_dir.rglob("list.md"))
    candidates += sorted(scenarios_dir.rglob("scenario.md"))
    candidates = sorted(set(candidates))

    renames: list[tuple[Path, Path]] = []
    for old_path in candidates:
        if old_path.name.endswith(".scenario.md"):
            continue
        renames.append((old_path, compute_new_path(scenarios_dir, old_path)))

    if write:
        for old_path, new_path in renames:
            new_path.parent.mkdir(parents=True, exist_ok=True)
            old_path.rename(new_path)
        for dirpath in sorted(scenarios_dir.rglob("*"), reverse=True):
            if dirpath.is_dir() and not any(dirpath.iterdir()):
                dirpath.rmdir()

    return renames


def color_for_index(index: int) -> str:
    code = COLOR_PALETTE[index % len(COLOR_PALETTE)]
    return f"\033[38;5;{code}m"


def should_colorize() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return os.isatty(1)


def log(message: str, *, prefix: str | None = None, color: str = "") -> None:
    if prefix and color and should_colorize():
        line = f"{color}[{prefix}]{ANSI_RESET} {message}"
    elif prefix:
        line = f"[{prefix}] {message}"
    else:
        line = message
    with LOG_LOCK:
        print(line, flush=True)
