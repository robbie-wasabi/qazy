"""Configuration loading and target resolution for Qazy."""

from __future__ import annotations

import json
import shlex
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
import re


PLACEHOLDER_RE = re.compile(r"\{([A-Za-z][A-Za-z0-9]*)\}")


@dataclass(frozen=True)
class ReadyCheck:
    type: str
    path: str
    timeout_seconds: int


AUTH_PROVIDERS = ("nextauth", "better-auth")
DEFAULT_AUTH_PROVIDER = "nextauth"
DEFAULT_BETTER_AUTH_COOKIE_PREFIX = "better-auth"
DEFAULT_AUTH_BASE_PATH = "/api/auth"
RUNTIME_NAMES = ("claude", "codex", "opencode")
DEFAULT_RUNTIME = "claude"
SCREENSHOT_STRATEGIES = ("none", "error", "single", "checkpoints")
DEFAULT_SCREENSHOT_STRATEGY = "error"


@dataclass(frozen=True)
class ScenarioDefaults:
    email: str | None = None
    password: str | None = None
    start_page: str | None = None
    use_cookie: bool | None = None
    auth_provider: str | None = None
    auth_cookie_prefix: str | None = None
    auth_base_path: str | None = None

    def has_values(self) -> bool:
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
class RuntimeDefaults:
    model: str | None = None
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class TargetDefinition:
    name: str
    mode: str
    base_url: str
    dev_command: str | None
    env: dict[str, str]
    app_port: int | str | None
    mongo_port: int | str | None
    ready: ReadyCheck
    parallel_safe: bool
    scenario_defaults: ScenarioDefaults
    runtime_defaults: dict[str, RuntimeDefaults] = field(default_factory=dict)


@dataclass(frozen=True)
class QazyConfig:
    source: Path | None
    results_dir: Path | None
    default_target: str
    default_runtime: str
    default_screenshot_strategy: str
    targets: dict[str, TargetDefinition]


@dataclass(frozen=True)
class ResolvedTarget:
    name: str
    mode: str
    base_url: str
    dev_command: tuple[str, ...] | None
    env: dict[str, str]
    app_port: int | None
    mongo_port: int | None
    ready: ReadyCheck
    parallel_safe: bool
    scenario_defaults: ScenarioDefaults
    runtime_defaults: dict[str, RuntimeDefaults] = field(default_factory=dict)


DEFAULT_TARGET_NAME = "default"
DEFAULT_ATTACHED_BASE_URL = "http://127.0.0.1:3000"
DEFAULT_MANAGED_BASE_URL = "http://127.0.0.1:{appPort}"
DEFAULT_READY_CHECK = ReadyCheck(type="http", path="/", timeout_seconds=60)
DEFAULT_RESULTS_DIR = ".qazy/results"
DEFAULT_CONFIG_TEMPLATE_FILE = "qazy.config.jsonc"
CONFIG_FILE_NAMES = ("qazy.config.json", "qazy.config.jsonc")


def build_default_target(*, base_url: str | None = None, managed: bool = False) -> TargetDefinition:
    if managed:
        return TargetDefinition(
            name=DEFAULT_TARGET_NAME,
            mode="managed",
            base_url=base_url or DEFAULT_MANAGED_BASE_URL,
            dev_command=None,
            env={"PORT": "{appPort}"} if base_url is None else {},
            app_port="auto" if base_url is None else None,
            mongo_port=None,
            ready=DEFAULT_READY_CHECK,
            parallel_safe=False,
            scenario_defaults=ScenarioDefaults(),
        )

    return TargetDefinition(
        name=DEFAULT_TARGET_NAME,
        mode="attached",
        base_url=base_url or DEFAULT_ATTACHED_BASE_URL,
        dev_command=None,
        env={},
        app_port=None,
        mongo_port=None,
        ready=DEFAULT_READY_CHECK,
        parallel_safe=False,
        scenario_defaults=ScenarioDefaults(),
    )


def build_example_config_payload() -> dict[str, object]:
    return {
        "version": 1,
        "defaultTarget": "local",
        "defaultRuntime": DEFAULT_RUNTIME,
        "screenshotStrategy": DEFAULT_SCREENSHOT_STRATEGY,
        "resultsDir": DEFAULT_RESULTS_DIR,
        "targets": {
            "local": {
                "mode": "managed",
                "baseUrl": DEFAULT_MANAGED_BASE_URL,
                "devCommand": "pnpm dev",
                "ports": {
                    "appPort": "auto",
                    "mongoPort": None,
                },
                "env": {
                    "PORT": "{appPort}",
                },
                "ready": {
                    "type": "http",
                    "path": DEFAULT_READY_CHECK.path,
                    "timeoutSeconds": DEFAULT_READY_CHECK.timeout_seconds,
                },
                "parallelSafe": False,
                "scenarioDefaults": {
                    "email": None,
                    "password": None,
                    "startPage": "/login",
                    "useCookie": False,
                    "authProvider": DEFAULT_AUTH_PROVIDER,
                    "authCookiePrefix": None,
                    "authBasePath": DEFAULT_AUTH_BASE_PATH,
                },
                "runtimeDefaults": {
                    "codex": {
                        "model": "gpt-5.4-mini",
                        "reasoningEffort": "low",
                    },
                    "claude": {
                        "model": None,
                        "reasoningEffort": None,
                    },
                    "opencode": {
                        "model": None,
                        "reasoningEffort": None,
                    },
                },
            },
            "attached-local": {
                "mode": "attached",
                "baseUrl": DEFAULT_ATTACHED_BASE_URL,
                "ready": {
                    "type": "http",
                    "path": DEFAULT_READY_CHECK.path,
                    "timeoutSeconds": DEFAULT_READY_CHECK.timeout_seconds,
                },
                "parallelSafe": True,
            }
        },
    }


def build_config_template_text() -> str:
    return textwrap.dedent(
        f"""\
        {{
          // Schema version. Keep this at 1 until Qazy documents a new version.
          "version": 1,

          // Target used when --target is omitted.
          "defaultTarget": "local",

          // Runtime used when --runtime is omitted: "claude", "codex", or "opencode".
          "defaultRuntime": "{DEFAULT_RUNTIME}",

          // Screenshot capture policy used when --screenshot-strategy is omitted:
          // "none", "error", "single", or "checkpoints".
          "screenshotStrategy": "{DEFAULT_SCREENSHOT_STRATEGY}",

          // Where result markdown, screenshots, and logs are written.
          "resultsDir": "{DEFAULT_RESULTS_DIR}",

          "targets": {{
            "local": {{
              // mode: "managed" starts devCommand; "attached" uses baseUrl as-is.
              "mode": "managed",

              // Use {{appPort}} and/or {{mongoPort}} placeholders with ports below.
              "baseUrl": "{DEFAULT_MANAGED_BASE_URL}",

              // Required for managed targets. Qazy starts and later stops this command.
              "devCommand": "pnpm dev",

              "ports": {{
                // Use "auto" to reserve a free port, or use a fixed positive integer.
                "appPort": "auto",
                // "mongoPort": "auto"
              }},

              "env": {{
                // Managed target environment variables. Placeholders are rendered before start.
                "PORT": "{{appPort}}",
                // "MONGODB_URI": "mongodb://127.0.0.1:{{mongoPort}}/qazy"
              }},

              "ready": {{
                // Currently only "http" is supported.
                "type": "http",
                "path": "/",
                "timeoutSeconds": 60
              }},

              // Required for batch --parallel. Keep false unless this target can run concurrently.
              "parallelSafe": false,

              "scenarioDefaults": {{
                // "email": "student@example.com",
                // "password": "secret123",
                "startPage": "/login",
                "useCookie": false,

                // authProvider: "nextauth" or "better-auth".
                "authProvider": "{DEFAULT_AUTH_PROVIDER}",
                // "authCookiePrefix": "{DEFAULT_BETTER_AUTH_COOKIE_PREFIX}",
                "authBasePath": "{DEFAULT_AUTH_BASE_PATH}"
              }},

              "runtimeDefaults": {{
                "codex": {{
                  "model": "gpt-5.4-mini",
                  "reasoningEffort": "low"
                }},
                "claude": {{
                  // "model": "claude-sonnet-4-5",
                  // "reasoningEffort": "default"
                }},
                "opencode": {{
                  // "model": "openai/gpt-5.4-mini",
                  // "reasoningEffort": "standard"
                }}
              }}
            }}

            // Alternate target shape for an already-running app:
            // ,"attached-local": {{
            //   "mode": "attached",
            //   "baseUrl": "{DEFAULT_ATTACHED_BASE_URL}",
            //   "ready": {{
            //     "type": "http",
            //     "path": "/",
            //     "timeoutSeconds": 60
            //   }},
            //   "parallelSafe": true
            // }}
          }}
        }}
        """
    )


def format_config_payload(payload: object) -> str:
    return f"{json.dumps(payload, indent=2)}\n"


def strip_jsonc_comments(value: str) -> str:
    result: list[str] = []
    index = 0
    in_string = False
    escaped = False
    length = len(value)

    while index < length:
        char = value[index]
        next_char = value[index + 1] if index + 1 < length else ""

        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue

        if char == "/" and next_char == "/":
            result.extend((" ", " "))
            index += 2
            while index < length and value[index] not in "\r\n":
                result.append(" ")
                index += 1
            continue

        if char == "/" and next_char == "*":
            result.extend((" ", " "))
            index += 2
            while index < length:
                if value[index] == "*" and index + 1 < length and value[index + 1] == "/":
                    result.extend((" ", " "))
                    index += 2
                    break
                result.append(value[index] if value[index] in "\r\n" else " ")
                index += 1
            continue

        result.append(char)
        index += 1

    return "".join(result)


def strip_jsonc_trailing_commas(value: str) -> str:
    result: list[str] = []
    index = 0
    in_string = False
    escaped = False
    length = len(value)

    while index < length:
        char = value[index]

        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue

        if char == ",":
            lookahead = index + 1
            while lookahead < length and value[lookahead].isspace():
                lookahead += 1
            if lookahead < length and value[lookahead] in "}]":
                index += 1
                continue

        result.append(char)
        index += 1

    return "".join(result)


def parse_jsonc(value: str) -> object:
    return json.loads(strip_jsonc_trailing_commas(strip_jsonc_comments(value)))


def read_config_payload(path: Path) -> dict[str, object]:
    try:
        payload = parse_jsonc(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Invalid Qazy config at {path}: invalid JSON/JSONC at line {exc.lineno} column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid Qazy config at {path}: expected an object")
    return payload


def config_file_is_formatted(path: Path) -> bool:
    if path.suffix == ".jsonc":
        return True
    return path.read_text(encoding="utf-8") == format_config_payload(read_config_payload(path))


def write_config_template(
    project_root: Path,
    *,
    output: Path | None = None,
    force: bool = False,
) -> Path:
    root = project_root.resolve()
    path = output or Path(DEFAULT_CONFIG_TEMPLATE_FILE)
    resolved = path.expanduser()
    if not resolved.is_absolute():
        resolved = (root / resolved).resolve()
    else:
        resolved = resolved.resolve()

    if resolved.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing file: {resolved}")

    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(build_config_template_text(), encoding="utf-8")
    return resolved


def write_example_config(
    project_root: Path,
    *,
    output: Path | None = None,
    force: bool = False,
) -> Path:
    return write_config_template(project_root, output=output, force=force)


def find_config_file(project_root: Path, explicit_path: Path | None = None) -> Path:
    root = project_root.resolve()
    if explicit_path is not None:
        path = explicit_path.expanduser()
        if not path.is_absolute():
            path = (root / path).resolve()
        else:
            path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Config file not found: {path}")
        return path

    candidates = [(root / name).resolve() for name in CONFIG_FILE_NAMES]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    default_candidate = candidates[0]
    examples = [
        (root / DEFAULT_CONFIG_TEMPLATE_FILE).resolve(),
        (root / "qazy.config.example.json").resolve(),
        (root / "qazy" / "qazy.config.example.json").resolve(),
    ]
    for example in examples:
        if example.is_file():
            try:
                display = example.relative_to(root).as_posix()
            except ValueError:
                display = example.name
            raise FileNotFoundError(
                f"Qazy config not found: {default_candidate}. Create qazy.config.json from {display} "
                f"or pass --config-file."
            )

    raise FileNotFoundError(
        f"Qazy config not found: {default_candidate}. Run qazy init to create {DEFAULT_CONFIG_TEMPLATE_FILE} "
        "or pass --config-file."
    )


def load_config(project_root: Path, *, config_file: Path | None = None) -> QazyConfig:
    path = find_config_file(project_root, config_file)

    payload = read_config_payload(path)

    version = payload.get("version", 1)
    if version != 1:
        raise RuntimeError(f"Invalid Qazy config at {path}: unsupported version {version}")
    if "logsDir" in payload:
        raise RuntimeError(
            f"Invalid Qazy config at {path}: 'logsDir' is no longer supported; "
            "logs are written under resultsDir/<run-id>/logs"
        )

    targets_payload = payload.get("targets")
    if not isinstance(targets_payload, dict) or not targets_payload:
        raise RuntimeError(f"Invalid Qazy config at {path}: 'targets' must be a non-empty object")

    targets = {name: parse_target(path, name, value) for name, value in targets_payload.items()}

    default_target = payload.get("defaultTarget")
    if default_target is None and len(targets) == 1:
        default_target = next(iter(targets))
    if not isinstance(default_target, str) or not default_target:
        raise RuntimeError(f"Invalid Qazy config at {path}: 'defaultTarget' must be a target name")
    if default_target not in targets:
        raise RuntimeError(f"Invalid Qazy config at {path}: unknown defaultTarget '{default_target}'")

    default_runtime = payload.get("defaultRuntime", DEFAULT_RUNTIME)
    if default_runtime not in RUNTIME_NAMES:
        joined = ", ".join(RUNTIME_NAMES)
        raise RuntimeError(
            f"Invalid Qazy config at {path}: unsupported defaultRuntime "
            f"'{default_runtime}'; must be one of: {joined}"
        )

    default_screenshot_strategy = payload.get("screenshotStrategy", DEFAULT_SCREENSHOT_STRATEGY)
    if default_screenshot_strategy not in SCREENSHOT_STRATEGIES:
        joined = ", ".join(SCREENSHOT_STRATEGIES)
        raise RuntimeError(
            f"Invalid Qazy config at {path}: unsupported screenshotStrategy "
            f"'{default_screenshot_strategy}'; must be one of: {joined}"
        )

    results_dir = parse_optional_path(path, "resultsDir", payload.get("resultsDir"))

    return QazyConfig(
        source=path,
        results_dir=results_dir,
        default_target=default_target,
        default_runtime=default_runtime,
        default_screenshot_strategy=default_screenshot_strategy,
        targets=targets,
    )


def parse_optional_path(config_path: Path, field_name: str, value: object) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Invalid Qazy config at {config_path}: '{field_name}' must be a non-empty string")

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (config_path.parent / path).resolve()
    else:
        path = path.resolve()
    return path


def get_target(config: QazyConfig, target_name: str | None) -> TargetDefinition:
    name = target_name or config.default_target
    try:
        return config.targets[name]
    except KeyError as exc:
        raise RuntimeError(f"Unknown target '{name}'") from exc


def resolve_target(
    target: TargetDefinition,
    *,
    dev_command_override: tuple[str, ...] | None = None,
    app_port_override: int | None = None,
    mongo_port_override: int | None = None,
    timeout_override: int | None = None,
    allocate_port: Callable[[], int] | None = None,
) -> ResolvedTarget:
    if target.mode == "attached" and dev_command_override is not None:
        raise RuntimeError("--dev-command cannot be used with an attached target")

    variables: dict[str, int] = {}

    app_port = resolve_port(
        "appPort",
        target=target,
        override=app_port_override,
        allocate_port=allocate_port,
    )
    if app_port is not None:
        variables["appPort"] = app_port

    mongo_port = resolve_port(
        "mongoPort",
        target=target,
        override=mongo_port_override,
        allocate_port=allocate_port,
    )
    if mongo_port is not None:
        variables["mongoPort"] = mongo_port

    base_url = render_template(target.base_url, variables, context=f"target '{target.name}' baseUrl")
    env = {
        key: render_template(value, variables, context=f"target '{target.name}' env.{key}")
        for key, value in target.env.items()
    }

    if target.mode == "attached":
        dev_command = None
    else:
        if dev_command_override is not None:
            dev_command = dev_command_override
        elif target.dev_command:
            dev_command = tuple(shlex.split(target.dev_command))
        else:
            raise RuntimeError(f"Target '{target.name}' is managed but has no devCommand")
        if not dev_command:
            raise RuntimeError(f"Target '{target.name}' has an empty devCommand")

    ready = ReadyCheck(
        type=target.ready.type,
        path=target.ready.path,
        timeout_seconds=timeout_override or target.ready.timeout_seconds,
    )

    return ResolvedTarget(
        name=target.name,
        mode=target.mode,
        base_url=base_url,
        dev_command=dev_command,
        env=env,
        app_port=app_port,
        mongo_port=mongo_port,
        ready=ready,
        parallel_safe=target.parallel_safe,
        scenario_defaults=target.scenario_defaults,
        runtime_defaults=target.runtime_defaults,
    )


def parse_target(config_path: Path, name: object, payload: object) -> TargetDefinition:
    if not isinstance(name, str) or not name:
        raise RuntimeError(f"Invalid Qazy config at {config_path}: target names must be non-empty strings")
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid Qazy config at {config_path}: target '{name}' must be an object")

    mode = payload.get("mode")
    if mode not in {"managed", "attached"}:
        raise RuntimeError(
            f"Invalid Qazy config at {config_path}: target '{name}' mode must be 'managed' or 'attached'"
        )

    base_url = payload.get("baseUrl")
    if not isinstance(base_url, str) or not base_url:
        raise RuntimeError(f"Invalid Qazy config at {config_path}: target '{name}' baseUrl is required")

    dev_command = payload.get("devCommand")
    if dev_command is not None and not isinstance(dev_command, str):
        raise RuntimeError(f"Invalid Qazy config at {config_path}: target '{name}' devCommand must be a string")
    if mode == "managed" and not dev_command:
        raise RuntimeError(f"Invalid Qazy config at {config_path}: target '{name}' devCommand is required")
    if mode == "attached" and dev_command is not None:
        raise RuntimeError(f"Invalid Qazy config at {config_path}: target '{name}' cannot set devCommand")

    ports = payload.get("ports", {})
    if not isinstance(ports, dict):
        raise RuntimeError(f"Invalid Qazy config at {config_path}: target '{name}' ports must be an object")

    env_payload = payload.get("env", {})
    if not isinstance(env_payload, dict):
        raise RuntimeError(f"Invalid Qazy config at {config_path}: target '{name}' env must be an object")
    env: dict[str, str] = {}
    for key, value in env_payload.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise RuntimeError(
                f"Invalid Qazy config at {config_path}: target '{name}' env must be string-to-string"
            )
        env[key] = value

    parallel_safe = payload.get("parallelSafe", False)
    if not isinstance(parallel_safe, bool):
        raise RuntimeError(
            f"Invalid Qazy config at {config_path}: target '{name}' parallelSafe must be true or false"
        )

    validate_placeholders(config_path, name, "baseUrl", base_url)
    for key, value in env.items():
        validate_placeholders(config_path, name, f"env.{key}", value)

    return TargetDefinition(
        name=name,
        mode=mode,
        base_url=base_url,
        dev_command=dev_command,
        env=env,
        app_port=parse_port_spec(config_path, name, "appPort", ports.get("appPort")),
        mongo_port=parse_port_spec(config_path, name, "mongoPort", ports.get("mongoPort")),
        ready=parse_ready(config_path, name, payload.get("ready")),
        parallel_safe=parallel_safe,
        scenario_defaults=parse_scenario_defaults(config_path, name, payload.get("scenarioDefaults")),
        runtime_defaults=parse_runtime_defaults(config_path, name, payload.get("runtimeDefaults")),
    )


def parse_runtime_defaults(config_path: Path, target_name: str, payload: object) -> dict[str, RuntimeDefaults]:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"Invalid Qazy config at {config_path}: target '{target_name}' runtimeDefaults must be an object"
        )

    defaults: dict[str, RuntimeDefaults] = {}
    for runtime_name, runtime_payload in payload.items():
        if runtime_name not in RUNTIME_NAMES:
            joined = ", ".join(RUNTIME_NAMES)
            raise RuntimeError(
                f"Invalid Qazy config at {config_path}: target '{target_name}' runtimeDefaults "
                f"has unsupported runtime '{runtime_name}'; must be one of: {joined}"
            )
        if not isinstance(runtime_payload, dict):
            raise RuntimeError(
                f"Invalid Qazy config at {config_path}: target '{target_name}' "
                f"runtimeDefaults.{runtime_name} must be an object"
            )
        allowed_keys = {"model", "reasoningEffort"}
        unknown_keys = sorted(set(runtime_payload) - allowed_keys)
        if unknown_keys:
            joined = ", ".join(unknown_keys)
            raise RuntimeError(
                f"Invalid Qazy config at {config_path}: target '{target_name}' "
                f"runtimeDefaults.{runtime_name} has unsupported field(s): {joined}"
            )

        model = runtime_payload.get("model")
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise RuntimeError(
                f"Invalid Qazy config at {config_path}: target '{target_name}' "
                f"runtimeDefaults.{runtime_name}.model must be a non-empty string"
            )

        reasoning_effort = runtime_payload.get("reasoningEffort")
        if reasoning_effort is not None and (
            not isinstance(reasoning_effort, str) or not reasoning_effort.strip()
        ):
            raise RuntimeError(
                f"Invalid Qazy config at {config_path}: target '{target_name}' "
                f"runtimeDefaults.{runtime_name}.reasoningEffort must be a non-empty string"
            )

        defaults[runtime_name] = RuntimeDefaults(
            model=model,
            reasoning_effort=reasoning_effort,
        )
    return defaults


def parse_scenario_defaults(config_path: Path, target_name: str, payload: object) -> ScenarioDefaults:
    if payload is None:
        return ScenarioDefaults()
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"Invalid Qazy config at {config_path}: target '{target_name}' scenarioDefaults must be an object"
        )

    allowed_keys = {
        "email",
        "password",
        "startPage",
        "useCookie",
        "authProvider",
        "authCookiePrefix",
        "authBasePath",
    }
    unknown_keys = sorted(set(payload) - allowed_keys)
    if unknown_keys:
        joined = ", ".join(unknown_keys)
        raise RuntimeError(
            f"Invalid Qazy config at {config_path}: target '{target_name}' "
            f"scenarioDefaults has unsupported field(s): {joined}"
        )

    email = payload.get("email")
    if email is not None and (not isinstance(email, str) or not email.strip()):
        raise RuntimeError(
            f"Invalid Qazy config at {config_path}: target '{target_name}' scenarioDefaults.email "
            "must be a non-empty string"
        )

    password = payload.get("password")
    if password is not None and (not isinstance(password, str) or not password.strip()):
        raise RuntimeError(
            f"Invalid Qazy config at {config_path}: target '{target_name}' scenarioDefaults.password "
            "must be a non-empty string"
        )

    start_page = payload.get("startPage")
    if start_page is not None and (not isinstance(start_page, str) or not start_page.strip()):
        raise RuntimeError(
            f"Invalid Qazy config at {config_path}: target '{target_name}' scenarioDefaults.startPage "
            "must be a non-empty string"
        )

    use_cookie = payload.get("useCookie")
    if use_cookie is not None and not isinstance(use_cookie, bool):
        raise RuntimeError(
            f"Invalid Qazy config at {config_path}: target '{target_name}' scenarioDefaults.useCookie "
            "must be true or false"
        )

    auth_provider = payload.get("authProvider")
    if auth_provider is not None and (
        not isinstance(auth_provider, str) or auth_provider not in AUTH_PROVIDERS
    ):
        joined = ", ".join(AUTH_PROVIDERS)
        raise RuntimeError(
            f"Invalid Qazy config at {config_path}: target '{target_name}' scenarioDefaults.authProvider "
            f"must be one of: {joined}"
        )

    auth_cookie_prefix = payload.get("authCookiePrefix")
    if auth_cookie_prefix is not None and (
        not isinstance(auth_cookie_prefix, str) or not auth_cookie_prefix.strip()
    ):
        raise RuntimeError(
            f"Invalid Qazy config at {config_path}: target '{target_name}' scenarioDefaults.authCookiePrefix "
            "must be a non-empty string"
        )

    auth_base_path = payload.get("authBasePath")
    if auth_base_path is not None and (
        not isinstance(auth_base_path, str) or not auth_base_path.strip().startswith("/")
    ):
        raise RuntimeError(
            f"Invalid Qazy config at {config_path}: target '{target_name}' scenarioDefaults.authBasePath "
            "must be a non-empty string starting with '/'"
        )

    return ScenarioDefaults(
        email=email,
        password=password,
        start_page=start_page,
        use_cookie=use_cookie,
        auth_provider=auth_provider,
        auth_cookie_prefix=auth_cookie_prefix,
        auth_base_path=auth_base_path,
    )


def parse_ready(config_path: Path, target_name: str, payload: object) -> ReadyCheck:
    if payload is None:
        return ReadyCheck(type="http", path="/", timeout_seconds=120)
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"Invalid Qazy config at {config_path}: target '{target_name}' ready must be an object"
        )

    ready_type = payload.get("type", "http")
    if ready_type != "http":
        raise RuntimeError(
            f"Invalid Qazy config at {config_path}: target '{target_name}' ready.type must be 'http'"
        )

    path = payload.get("path", "/")
    if not isinstance(path, str) or not path:
        raise RuntimeError(
            f"Invalid Qazy config at {config_path}: target '{target_name}' ready.path must be a string"
        )
    if not path.startswith("/"):
        path = f"/{path}"

    timeout = payload.get("timeoutSeconds", 120)
    if not isinstance(timeout, int) or timeout <= 0:
        raise RuntimeError(
            f"Invalid Qazy config at {config_path}: target '{target_name}' ready.timeoutSeconds must be > 0"
        )

    return ReadyCheck(type=ready_type, path=path, timeout_seconds=timeout)


def parse_port_spec(config_path: Path, target_name: str, port_name: str, value: object) -> int | str | None:
    if value is None:
        return None
    if isinstance(value, int) and value > 0:
        return value
    if value == "auto":
        return "auto"
    raise RuntimeError(
        f"Invalid Qazy config at {config_path}: target '{target_name}' ports.{port_name} must be a positive int or 'auto'"
    )


def resolve_port(
    port_name: str,
    *,
    target: TargetDefinition,
    override: int | None,
    allocate_port: Callable[[], int] | None,
) -> int | None:
    if override is not None:
        return override

    spec = target.app_port if port_name == "appPort" else target.mongo_port
    if isinstance(spec, int):
        return spec
    if spec == "auto":
        if allocate_port is None:
            raise RuntimeError(f"Target '{target.name}' requires {port_name} but cannot auto-allocate in attached mode")
        return allocate_port()

    uses_placeholder = template_uses_placeholder(target.base_url, port_name) or any(
        template_uses_placeholder(value, port_name) for value in target.env.values()
    )
    if uses_placeholder:
        if allocate_port is None:
            raise RuntimeError(f"Target '{target.name}' requires {port_name} to render its templates")
        return allocate_port()
    return None


def render_template(value: str, variables: dict[str, int], *, context: str) -> str:
    rendered = value
    for key, replacement in variables.items():
        rendered = rendered.replace(f"{{{key}}}", str(replacement))

    unresolved = sorted(set(PLACEHOLDER_RE.findall(rendered)))
    if unresolved:
        joined = ", ".join(unresolved)
        raise RuntimeError(f"Unresolved template variable(s) in {context}: {joined}")
    return rendered


def template_uses_placeholder(value: str, placeholder: str) -> bool:
    return f"{{{placeholder}}}" in value


def validate_placeholders(config_path: Path, target_name: str, field_name: str, value: str) -> None:
    placeholders = sorted(set(PLACEHOLDER_RE.findall(value)))
    allowed = {"appPort", "mongoPort"}
    unknown = [item for item in placeholders if item not in allowed]
    if unknown:
        joined = ", ".join(unknown)
        raise RuntimeError(
            f"Invalid Qazy config at {config_path}: target '{target_name}' {field_name} has unsupported placeholder(s): {joined}"
        )
