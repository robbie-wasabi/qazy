# Qazy

Qazy is a Python CLI for agent-driven browser QA. It can start a local app or attach to an existing environment, hand the browser to a runtime such as Claude or Codex through `agent-browser`, and write markdown results, screenshots, and runtime logs for each run.

## Install

```bash
python3 -m pip install -e .
```

Qazy expects:

- `agent-browser` on `PATH`
- at least one runtime CLI on `PATH`: `claude`, `codex`, or `opencode`
- a `qazy.config.json` in the app workspace you want to test

## Quick Start

Create `qazy.config.json` in the target project:

```json
{
  "version": 1,
  "defaultTarget": "local",
  "targets": {
    "local": {
      "mode": "managed",
      "baseUrl": "http://localhost:{appPort}",
      "devCommand": "pnpm dev",
      "ports": {
        "appPort": "auto"
      },
      "env": {
        "PORT": "{appPort}"
      },
      "scenarioDefaults": {
        "startPage": "/login",
        "useCookie": false
      },
      "parallelSafe": true
    }
  }
}
```

Create a scenario file such as `user-scenarios/login.scenario.md`:

```md
---
start_page: /login
use_cookie: false
---

# Student Login

## Notes

Use the seeded student account.

## List

- [ ] Sign in and confirm the dashboard loads.
- [ ] Open the profile menu and confirm the signed-in email is shown.
```

Run it:

```bash
qazy user-scenarios/login
```

Ad hoc prompt mode works when you want a quick one-off check instead of a checked-in scenario:

```bash
qazy -p "test login flow for student" --start-page /login --no-use-cookie
```

## Commands

Scenario execution:

```bash
qazy user-scenarios/login
qazy "user-scenarios/**/*.scenario.md" --parallel
qazy run user-scenarios/login
qazy batch user-scenarios
qazy -p "verify the student can submit an assignment"
```

Useful options for runs:

- `--project-root` to point Qazy at another workspace
- `--config-file` to use a non-default config path
- `--target` to pick a named target
- `--runtime` to choose `claude`, `codex`, or `opencode`
- `--model` and `--reasoning-effort` to forward runtime-specific tuning
- `--email`, `--password`, `--start-page`, `--use-cookie`, `--no-use-cookie` to override scenario values
- `--headed` or `--headless` to control browser visibility
- `--screenshot-strategy` with `none`, `error`, `single`, or `checkpoints`
- `--results-dir` and `--logs-dir` to override output paths
- `--parallel` and `--max-workers` for batch execution

Other commands:

```bash
qazy tokens
qazy tokens .qazy/logs/claude-login.log
qazy rename-scenarios --write
qazy runtimes
qazy runtimes --smoke
qazy help
qazy help run
qazy help config
qazy help auth
```

What they do:

- `qazy tokens` summarizes usage from runtime log files
- `qazy rename-scenarios` migrates legacy scenario layouts to `*.scenario.md`
- `qazy runtimes` checks which runtime CLIs are available
- `qazy runtimes --smoke` sends a trivial prompt through each installed runtime
- `qazy help [topic]` prints agent-friendly usage guidance without needing this README

## Config

Qazy looks for `qazy.config.json` in `--project-root`. If that file is missing and `qazy.config.example.json` exists, Qazy tells you to copy it.

Top-level fields:

- `version`: currently `1`
- `defaultTarget`: target used when `--target` is omitted
- `resultsDir`: default results directory
- `targets`: named target definitions

Target fields:

- `mode`: `managed` or `attached`
- `baseUrl`: may include `{appPort}` and `{mongoPort}` placeholders
- `devCommand`: required for `managed` targets
- `ports`: `appPort` and `mongoPort`, each fixed or `"auto"`
- `env`: environment variables for managed targets
- `ready`: HTTP readiness probe with `type`, `path`, and `timeoutSeconds`
- `parallelSafe`: required for batch `--parallel`
- `scenarioDefaults`: default `email`, `password`, `startPage`, and `useCookie`

Target behavior:

- `managed`: Qazy starts `devCommand`, waits for the target to respond, then stops it after the run
- `attached`: Qazy never starts a process and uses `baseUrl` as-is

Notes:

- Relative `resultsDir` values resolve from the config file location
- Runtime logs default to `<project-root>/.qazy/logs/`
- If `<project-root>/qazy/logs/` already exists and `.qazy/logs/` does not, Qazy keeps using the legacy path
- `ready.type` currently only supports `http`

## Scenario Format

Frontmatter fields:

- `email`
- `password`
- `start_page`
- `use_cookie`

Single-section scenarios are the common case. Multi-section scenarios work by repeating the frontmatter block:

```md
---
email: user1@example.com
password: tester123
start_page: /dashboard
use_cookie: true
---

# Tenant 1

- [ ] Verify tenant 1 state.

---
email: user2@example.com
password: tester123
start_page: /dashboard
use_cookie: true
---

# Tenant 2

- [ ] Verify tenant 2 state.
```

Qazy runs those sections in order against one shared target lifecycle.

Value precedence is:

1. CLI overrides
2. Scenario frontmatter
3. Target `scenarioDefaults`
4. Built-in defaults

Built-in defaults are `start_page: /dashboard` and `use_cookie: true`. Credentials may be omitted when `use_cookie` resolves to `false`, or when they are supplied by `scenarioDefaults` or CLI overrides.

## Authentication

Qazy has one built-in auto-auth flow, controlled by `use_cookie`.

- `use_cookie: true`: Qazy performs a NextAuth credentials-cookie login before handing control to the runtime. It requests `/api/auth/csrf`, posts to `/api/auth/callback/credentials`, captures the returned session cookie, injects it into `agent-browser`, and opens `start_page`.
- `use_cookie: false`: Qazy does no pre-authentication. The runtime logs in manually in the browser.

Credential sources, in precedence order:

1. CLI overrides such as `--email` and `--password`
2. Scenario frontmatter
3. Target `scenarioDefaults`

Custom auth flows still work, but they are runtime-driven browser flows rather than built-in Qazy auth. That includes SSO, OAuth redirects, magic links, MFA, and custom login forms.

## Screenshots and Outputs

Screenshot strategies:

- `none`: disable screenshots
- `error`: allow named error screenshots and capture a fallback failure screenshot if needed
- `single`: save one final screenshot automatically and still allow named error screenshots
- `checkpoints`: allow named screenshots during the run for important states

During a run, Qazy exposes `qazy-shot <label>` to the runtime so it can save screenshots without calling `agent-browser screenshot` directly.

Outputs:

- results markdown: `<resultsDir>/<run-id>/`
- screenshots: `<resultsDir>/<run-id>/screenshots/`
- runtime logs: `<project-root>/.qazy/logs/` by default
- exit code: `0` on pass, `1` on fail or error

## Runtime Support

Supported runtime adapters today:

- `claude`
- `codex`
- `opencode`

Use `qazy runtimes` to check installation and `qazy runtimes --smoke` to verify a trivial invocation works in the current environment.

## Tests

Qazy now has three test tiers plus an aggregate target:

```bash
make test-unit
make test-runtime-integration
make test-example-integration
make test-all
```

What each tier covers:

- `test-unit`: pure unit coverage plus mocked CLI and runner behavior
- `test-runtime-integration`: runs the live runtime smoke tests in `tests/test_live_runtimes.py`
- `test-example-integration`: runs the repo-local example app integration tests in `tests/test_examples.py`
- `test-all`: runs every tier

The live runtime tests are intentionally skipped unless the relevant runtime CLI is installed locally.

## Examples

Repo-local example apps live under `examples/`:

- `examples/student-portal`
- `examples/task-board`

Each example includes a lightweight HTML app, a `qazy.config.json`, and scenario files you can run directly. See `examples/README.md` for commands.

## Limitations

- Built-in auto-auth only supports NextAuth credentials-cookie login
- PASS/FAIL is inferred from runtime output parsed by Qazy
- Ready checks are simple HTTP probes
- Prompt mode is useful for exploration but less repeatable than checked-in scenarios
- Qazy depends on external runtime CLIs and `agent-browser`; it does not install or manage them
- Qazy is not a replacement for deterministic unit or integration tests
