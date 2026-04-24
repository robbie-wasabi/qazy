"""Runtime adapters for external agent CLIs."""

from __future__ import annotations

from collections.abc import Callable
import json
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RuntimeCommand:
    argv: list[str]
    stdin_text: str | None = None
    env: dict[str, str] | None = None


@dataclass
class RuntimeInvocation:
    runtime: str
    final_text: str
    transcript: list[str]
    log_path: Path


@dataclass
class RuntimeProbe:
    name: str
    executable: str
    installed: bool
    smoke_ok: bool | None
    detail: str


@dataclass
class RuntimeState:
    final_text: str = ""
    transcript: list[str] = field(default_factory=list)
    error: str | None = None


class RuntimeInvocationError(RuntimeError):
    """Raised when a runtime fails to execute successfully."""


class RuntimeAdapter:
    name: str
    executable: str

    def build_command(
        self,
        prompt: str,
        *,
        cwd: Path,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> RuntimeCommand:
        raise NotImplementedError

    def help_command(self) -> list[str]:
        return [self.executable, "--help"]

    def effective_model(self, model: str | None) -> str | None:
        return model

    def consume_line(self, line: str, *, state: RuntimeState, cwd: Path) -> list[str]:
        return [line] if line.strip() else []


class ClaudeRuntime(RuntimeAdapter):
    name = "claude"
    executable = "claude"

    def build_command(
        self,
        prompt: str,
        *,
        cwd: Path,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> RuntimeCommand:
        argv = [
            self.executable,
            "-p",
            "--verbose",
            "--output-format",
            "stream-json",
            "--no-session-persistence",
            "--strict-mcp-config",
            "--disallowedTools",
            "Read Write Grep Glob Edit",
            "--permission-mode",
            "bypassPermissions",
        ]
        if model:
            argv.extend(["--model", model])
        if reasoning_effort:
            argv.extend(["--effort", reasoning_effort])
        return RuntimeCommand(argv=argv, stdin_text=prompt)

    def consume_line(self, line: str, *, state: RuntimeState, cwd: Path) -> list[str]:
        event = try_parse_json(line)
        if not event:
            return [line] if line.strip() else []

        event_type = event.get("type")

        if event_type == "system" and event.get("subtype") == "init":
            details = []
            model = event.get("model")
            session_id = event.get("session_id")
            if model:
                details.append(f"model={model}")
            if session_id:
                details.append(f"session={session_id}")
            return ["init" + (f" ({', '.join(details)})" if details else "")]

        if event_type == "assistant":
            message = event.get("message", {})
            if not isinstance(message, dict):
                return []
            lines: list[str] = []
            for item in message.get("content", []):
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text":
                    text = str(item.get("text", "")).strip()
                    if text:
                        state.transcript.append(text)
                        state.final_text = text
                        lines.append(text)
                elif item.get("type") == "tool_use" and item.get("name"):
                    lines.append(format_tool_use(item, cwd=cwd))
            return lines

        if event_type == "user":
            lines: list[str] = []
            payload = event.get("tool_use_result")
            if isinstance(payload, dict):
                rendered = format_tool_result(payload, cwd=cwd)
                if rendered:
                    lines.append(rendered)
            return lines

        if event_type == "result":
            result_text = str(event.get("result", "")).strip()
            if result_text:
                state.final_text = result_text
            if event.get("is_error"):
                state.error = result_text or "Claude reported an error"
            duration_ms = event.get("duration_ms")
            if duration_ms is None:
                return ["[done]"]
            return [f"[done] ({int(duration_ms / 1000)}s)"]

        if event_type == "error":
            state.error = str(event.get("error", line))
            return [state.error]

        return []


class CodexRuntime(RuntimeAdapter):
    name = "codex"
    executable = "codex"
    default_model = "gpt-5.4-mini"

    def effective_model(self, model: str | None) -> str | None:
        return model or self.default_model

    def build_command(
        self,
        prompt: str,
        *,
        cwd: Path,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> RuntimeCommand:
        argv = [
            self.executable,
            "--dangerously-bypass-approvals-and-sandbox",
            "exec",
            "--ignore-user-config",
            "--json",
        ]
        argv.extend(["-m", model or self.default_model])
        if reasoning_effort:
            argv.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
        argv.extend(
            [
                "-C",
                str(cwd),
                "--skip-git-repo-check",
                "-",
            ]
        )
        return RuntimeCommand(argv=argv, stdin_text=prompt)

    def consume_line(self, line: str, *, state: RuntimeState, cwd: Path) -> list[str]:
        event = try_parse_json(line)
        if not event:
            return [line] if line.strip() else []

        event_type = event.get("type")

        if event_type == "thread.started":
            return [f"init (thread={event.get('thread_id', 'unknown')})"]

        if event_type == "item.completed":
            item = event.get("item", {})
            if not isinstance(item, dict):
                return []
            if item.get("type") == "agent_message":
                text = str(item.get("text", "")).strip()
                if text:
                    state.transcript.append(text)
                    state.final_text = text
                    return [text]
            return []

        if event_type == "item.started":
            item = event.get("item", {})
            if not isinstance(item, dict):
                return []
            if item.get("type") == "command_execution":
                command = str(item.get("command", "")).strip()
                if command:
                    return [f"[Bash] {truncate(command, 120)}"]
            return []

        if event_type == "turn.completed":
            return ["[done]"]

        if event_type == "error":
            state.error = str(event.get("message") or event.get("error") or line)
            return [state.error]

        return []


class OpenCodeRuntime(RuntimeAdapter):
    name = "opencode"
    executable = "opencode"

    def build_command(
        self,
        prompt: str,
        *,
        cwd: Path,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> RuntimeCommand:
        argv = [
            self.executable,
            "run",
            "--format",
            "json",
            "--dir",
            str(cwd),
        ]
        if model:
            argv.extend(["--model", model])
        if reasoning_effort:
            argv.extend(["--variant", reasoning_effort])
        argv.append(prompt)
        return RuntimeCommand(argv=argv)

    def consume_line(self, line: str, *, state: RuntimeState, cwd: Path) -> list[str]:
        event = try_parse_json(line)
        if not event:
            cleaned = strip_ansi(line)
            return [cleaned] if cleaned.strip() else []

        event_type = event.get("type")
        if event_type == "error":
            error = event.get("error", {})
            detail = ""
            if isinstance(error, dict):
                detail = extract_text(error)
            state.error = detail or "OpenCode reported an error"
            return [state.error]

        text = extract_text(event)
        if text:
            state.transcript.append(text)
            state.final_text = text
            return [text]
        return []


def list_runtimes() -> list[RuntimeAdapter]:
    return [ClaudeRuntime(), CodexRuntime(), OpenCodeRuntime()]


def get_runtime(name: str) -> RuntimeAdapter:
    for runtime in list_runtimes():
        if runtime.name == name:
            return runtime
    raise ValueError(f"Unknown runtime: {name}")


def probe_runtime(name: str, *, cwd: Path, smoke: bool = False) -> RuntimeProbe:
    runtime = get_runtime(name)
    executable_path = shutil.which(runtime.executable)
    if not executable_path:
        return RuntimeProbe(name=name, executable=runtime.executable, installed=False, smoke_ok=None, detail="not installed")

    if not smoke:
        result = subprocess.run(runtime.help_command(), capture_output=True, text=True, timeout=15)
        detail = result.stdout.splitlines()[0].strip() if result.stdout.strip() else "help available"
        if result.returncode != 0:
            detail = result.stderr.strip() or detail or f"exit {result.returncode}"
        return RuntimeProbe(name=name, executable=runtime.executable, installed=True, smoke_ok=None, detail=detail)

    with tempfile.TemporaryDirectory(prefix="qazy-probe-") as tempdir:
        temp_log = Path(tempdir) / f"{name}.log"
        try:
            invocation = invoke_runtime(runtime, "Reply with OK and nothing else.", cwd=cwd, log_path=temp_log)
        except RuntimeInvocationError as exc:
            return RuntimeProbe(name=name, executable=runtime.executable, installed=True, smoke_ok=False, detail=str(exc))

        detail = invocation.final_text.strip() or "no final output"
        return RuntimeProbe(name=name, executable=runtime.executable, installed=True, smoke_ok=True, detail=detail)


def invoke_runtime(
    runtime: RuntimeAdapter,
    prompt: str,
    *,
    cwd: Path,
    log_path: Path,
    extra_env: dict[str, str] | None = None,
    on_progress: Callable[[str], None] | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> RuntimeInvocation:
    command = runtime.build_command(
        prompt,
        cwd=cwd,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    env = {**os.environ, **(command.env or {}), **(extra_env or {})}
    process = subprocess.Popen(
        command.argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE if command.stdin_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    assert process.stdout is not None

    if command.stdin_text is not None:
        assert process.stdin is not None
        process.stdin.write(command.stdin_text)
        process.stdin.close()

    state = RuntimeState()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("w", encoding="utf-8") as log_handle:
        try:
            for raw_line in process.stdout:
                log_handle.write(raw_line if raw_line.endswith("\n") else raw_line + "\n")
                log_handle.flush()
                emitted_lines = runtime.consume_line(raw_line.rstrip("\n"), state=state, cwd=cwd)
                if on_progress:
                    for line in emitted_lines:
                        if line:
                            on_progress(line)
        except BaseException:
            terminate_process_group(process)
            raise
        finally:
            process.stdout.close()

    return_code = process.wait()
    if state.error:
        raise RuntimeInvocationError(state.error)
    if return_code != 0:
        raise RuntimeInvocationError(f"{runtime.name} failed (exit {return_code}): {shlex.join(command.argv)}")
    final_text = state.final_text.strip()
    if not final_text and state.transcript:
        final_text = state.transcript[-1].strip()
    return RuntimeInvocation(runtime=runtime.name, final_text=final_text, transcript=state.transcript, log_path=log_path)


def format_tool_use(block: dict[str, Any], *, cwd: Path) -> str:
    name = str(block.get("name") or "Tool")
    payload = block.get("input")
    if not isinstance(payload, dict):
        return f"[{name}]"
    if name == "Bash":
        for key in ("command", "cmd"):
            command = payload.get(key)
            if isinstance(command, str) and command.strip():
                return f"[{name}] {truncate(command.replace(chr(10), ' '), 120)}"
    if name == "Write":
        file_path = payload.get("file_path") or payload.get("path")
        if isinstance(file_path, str) and file_path.strip():
            return f"[{name}] {format_path(file_path, cwd)}"
    return f"[{name}]"


def format_tool_result(payload: dict[str, Any], *, cwd: Path) -> str:
    file_path = payload.get("filePath")
    result_type = payload.get("type")
    if isinstance(file_path, str) and file_path.strip():
        rendered = format_path(file_path, cwd)
        if result_type == "create":
            return f"[write-result] created {rendered}"
        if result_type == "update":
            return f"[write-result] updated {rendered}"
    return ""


def format_path(value: str, cwd: Path) -> str:
    try:
        return str(Path(value).resolve().relative_to(cwd.resolve()))
    except ValueError:
        return value


def truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."


def try_parse_json(value: str) -> dict[str, Any] | None:
    value = strip_ansi(value).strip()
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def terminate_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGINT)
        process.wait(timeout=3)
        return
    except (ProcessLookupError, PermissionError):
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=3)
    except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
        pass


def strip_ansi(value: str) -> str:
    return (
        value.replace("\u001b[0m", "")
        .replace("\u001b[32m", "")
        .replace("\u001b[38;5;235m", "")
        .replace("\u001b[48;5;235m", "")
        .replace("\u001b[48;5;238m", "")
    )


def extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("text", "message", "content", "error", "data"):
            if key in value:
                extracted = extract_text(value[key])
                if extracted:
                    return extracted
        return ""
    if isinstance(value, list):
        parts = [extract_text(item) for item in value]
        joined = " ".join(part for part in parts if part)
        return joined.strip()
    return ""
