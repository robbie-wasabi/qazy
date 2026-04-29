# Qazy

Qazy is a Python CLI for agent-driven browser QA. It can start a local app or attach to an existing environment, hand the browser to a runtime such as [Claude Code](https://code.claude.com/docs/en/overview) or [Codex](https://developers.openai.com/codex/cli) through [Agent Browser](https://agent-browser.io/), and write markdown results, screenshots, and runtime logs for each run.

Use Qazy when you want a browser agent to walk through user-facing flows such as login, onboarding, checkout, dashboard checks, or smoke tests that are easier to describe in natural language than in deterministic test code.

## Install

With Homebrew:

```bash
brew tap robbie-wasabi/qazy
brew install qazy
```

For development from a clone of this repo:

```bash
make install
```

To install the CLI from a clone so `qazy` is available outside the repo:

```bash
pipx install --editable .
pipx ensurepath
```

## Requirements

- [Agent Browser](https://agent-browser.io/) on `PATH`
- at least one runtime CLI on `PATH`: [Claude Code](https://code.claude.com/docs/en/overview) (`claude`) or [Codex CLI](https://developers.openai.com/codex/cli) (`codex`)
- either a `qazy.config.jsonc`, `qazy.config.json`, or enough CLI flags to use the built-in defaults

Check the installed runtimes with:

```bash
qazy runtimes
qazy runtimes --smoke
```

## Quick Start

Start with prompt mode. It runs one check directly from the command line and does not require a `*.scenario.md` file.

```bash
# Run against an app that is already running.
qazy -p "confirm the landing page loads" \
  --base-url http://127.0.0.1:3000 \
  --no-use-cookie

# Start a local dev server for the check.
qazy -p "confirm the login page loads" \
  --dev-command "pnpm dev" \
  --start-page /login \
  --no-use-cookie

# Run with credentials and let the runtime log in through the browser.
qazy -p "verify the student can sign in and reach the dashboard" \
  --start-page /login \
  --email student@example.com --password secret123 \
  --no-use-cookie

# Run with credentials and Qazy's built-in auth-cookie flow.
qazy -p "verify the student dashboard loads" \
  --start-page /dashboard \
  --email student@example.com --password secret123 \
  --use-cookie

# Use Claude Code.
qazy -p "verify the checkout flow works" \
  --runtime claude \
  --email student@example.com --password secret123 \
  --no-use-cookie

# Use Codex.
qazy -p "verify the checkout flow works" \
  --runtime codex \
  --email student@example.com --password secret123 \
  --no-use-cookie
```

With no config file, Qazy uses these defaults:

- no target flags: attach to `http://127.0.0.1:3000`
- `--base-url URL`: attach to `URL`
- `--dev-command "..."`: start a managed app at `http://127.0.0.1:{appPort}` with `PORT={appPort}`

## Save a Scenario

Prompt mode is good for exploration. For repeatable checks, save the instructions in a scenario file.

Create `user-scenarios/login.scenario.md`:

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
qazy user-scenarios/login \
  --email student@example.com --password secret123
```

Run one scenario, a directory, or a glob:

```bash
qazy user-scenarios/login
qazy batch user-scenarios
qazy "user-scenarios/**/*.scenario.md" --parallel
```

## Add Project Defaults

For repeated use, named environments, and checked-in team defaults, add `qazy.config.jsonc` or `qazy.config.json` to the target project.

To have an agent inspect the project and create or patch `qazy.config.jsonc`, run:

```bash
qazy setup
```

Qazy asks whether to launch Claude Code or Codex, then passes the install prompt to that agent. To skip the chooser:

```bash
qazy setup --runtime codex
qazy setup --runtime claude
```

You can generate a commented config with every supported option shown:

```bash
qazy init
```

A compact strict-JSON config can look like:

```json
{
  "version": 1,
  "defaultTarget": "local",
  "defaultRuntime": "codex",
  "resultsDir": ".qazy/results",
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
      "runtimeDefaults": {
        "codex": {
          "model": "gpt-5.4-mini",
          "reasoningEffort": "low"
        },
        "claude": {
          "model": "claude-sonnet-4-5"
        }
      },
      "parallelSafe": true
    }
  }
}
```

With that config, a scenario can be run with fewer flags:

```bash
qazy user-scenarios/login \
  --email student@example.com --password secret123
```

## Core Concepts

Prompt mode runs one ad hoc check from `-p` or `--prompt`. Use it for exploration, smoke checks, or trying Qazy against a new app.

Scenario files are checked-in markdown files that make a flow repeatable. Use them for team workflows and regression checks.

Targets tell Qazy where the app is. An `attached` target points at an already-running app. A `managed` target starts `devCommand`, waits for the app to respond, runs the check, then stops the process.

Runtimes are the agent CLIs that drive the browser. Qazy currently supports [Claude Code](https://code.claude.com/docs/en/overview) (`claude`) and [Codex CLI](https://developers.openai.com/codex/cli) (`codex`).

Authentication can be handled by Qazy's built-in cookie flow with `--use-cookie`, or by the runtime interacting with the login page in the browser with `--no-use-cookie`.

## Commands

Common commands:

```bash
qazy -p "verify the student can submit an assignment"
qazy user-scenarios/login
qazy run user-scenarios/login
qazy batch user-scenarios
qazy "user-scenarios/**/*.scenario.md" --parallel
qazy tokens
qazy tokens .qazy/results/my-run/logs/claude-login.log
qazy setup
qazy init
qazy config check
qazy rename-scenarios --write
qazy runtimes
qazy runtimes --smoke
qazy help
qazy --version
qazy help run
qazy help config
qazy help auth
```

Useful run options:

- `--project-root` points Qazy at another workspace
- `--config-file` uses a non-default config path
- `--target` picks a named target
- `--base-url` runs without a config file against an existing app URL
- `--dev-command` starts a managed app without requiring a config file
- `--runtime` chooses `claude` or `codex`
- `--model` and `--reasoning-effort` forward runtime-specific tuning
- `--email`, `--password`, `--start-page`, `--use-cookie`, `--no-use-cookie` override scenario values
- `--headed` or `--headless` controls browser visibility
- `--screenshot-strategy` accepts `none`, `error`, `single`, or `checkpoints`
- `--results-dir` overrides the output path
- `--parallel` and `--max-workers` control batch execution

Other command behavior:

- `qazy tokens` summarizes usage from runtime log files
- `qazy setup` launches Claude Code or Codex with Qazy's install prompt to set up `qazy.config.jsonc`
- `qazy init` writes `qazy.config.jsonc` with every supported config field and optional values commented out
- `qazy --version` prints the installed Qazy package version
- `qazy config check` validates `qazy.config.jsonc` or `qazy.config.json`; strict JSON files are also checked for canonical two-space formatting
- `qazy rename-scenarios` migrates legacy scenario layouts to `*.scenario.md`
- `qazy runtimes` checks which runtime CLIs are available
- `qazy runtimes --smoke` sends a trivial prompt through each installed runtime
- `qazy help [topic]` prints agent-friendly usage guidance without needing this README

## Configuration Reference

Config is optional. Qazy looks for `qazy.config.json` first, then `qazy.config.jsonc` in `--project-root`, but if both are missing Qazy can still run with the built-in defaults described in Quick Start.

`qazy init` writes `qazy.config.jsonc` by default. JSONC comments and trailing commas are supported so the generated file can show optional settings inline while remaining loadable by Qazy.

Check a config before a run or in CI:

```bash
qazy config check
qazy config check --config-file qazy.config.jsonc
qazy config check --schema-only
```

Top-level fields:

- `version`: currently `1`
- `defaultTarget`: target used when `--target` is omitted
- `defaultRuntime`: runtime used when `--runtime` is omitted, one of `claude` or `codex`
- `resultsDir`: default directory for result markdown, screenshots, and logs; Qazy defaults to `.qazy/results`
- `screenshotStrategy`: default screenshot capture policy, one of `none`, `error`, `single`, or `checkpoints` (defaults to `error`); overridden by `--screenshot-strategy`
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
- `runtimeDefaults`: default runtime `model` and `reasoningEffort` by runtime name, such as `codex` or `claude`

Target behavior:

- `managed`: Qazy starts `devCommand`, waits for the target to respond, then stops it after the run
- `attached`: Qazy never starts a process and uses `baseUrl` as-is

Notes:

- Relative `resultsDir` values resolve from the config file location
- If `resultsDir` is omitted, results default to `<project-root>/.qazy/results/`
- `--runtime` overrides `defaultRuntime`
- `--model` and `--reasoning-effort` override `runtimeDefaults`
- Runtime and server logs are written to `<resultsDir>/<run-id>/logs/`
- `ready.type` currently only supports `http`

## Scenario Reference

Frontmatter fields:

- `email`
- `password`
- `start_page`
- `use_cookie`
- `auth_provider`: `nextauth` (default) or `better-auth`
- `auth_cookie_prefix`: Better Auth cookie prefix override, default `better-auth`

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

Built-in defaults are `start_page: /dashboard` and `use_cookie: true`. Built-in cookie auth requires both `email` and `password`; browser-driven login with `use_cookie: false` can run without credentials.

## Authentication Reference

Qazy has built-in credentials-cookie login, controlled by `use_cookie` plus `auth_provider`.

- `use_cookie: true`, `auth_provider: nextauth` (default): Qazy requests `/api/auth/csrf`, posts form-encoded credentials to `/api/auth/callback/credentials`, captures the `next-auth.session-token` cookie, and injects it into `agent-browser`
- `use_cookie: true`, `auth_provider: better-auth`: Qazy posts JSON credentials to `/api/auth/sign-in/email` with a matching `Origin` header, captures the `better-auth.session_token` cookie or `__Secure-better-auth.session_token` on HTTPS, and injects it into `agent-browser`
- `use_cookie: false`: Qazy does no pre-authentication. The runtime logs in manually in the browser when credentials are provided. If credentials are omitted, Qazy prints that on startup and instructs the runtime not to search files, environment variables, source code, logs, or config for them.

Credential and provider sources, in precedence order:

1. CLI overrides: `--email`, `--password`, `--auth-provider`, `--auth-cookie-prefix`
2. Scenario frontmatter
3. Target `scenarioDefaults`: `email`, `password`, `authProvider`, `authCookiePrefix`, and related auth fields

Custom auth flows still work, but they are runtime-driven browser flows rather than built-in Qazy auth. That includes SSO, OAuth redirects, magic links, MFA, and custom login forms.

## Screenshots and Outputs

Screenshot strategies:

- `none`: disable screenshots
- `error`: allow named error screenshots and capture a fallback failure screenshot if needed
- `single`: save one final screenshot automatically and still allow named error screenshots
- `checkpoints`: allow named screenshots during the run for important states

During a run, Qazy exposes `qazy-shot <label>` to the runtime so it can save screenshots without calling `agent-browser screenshot` directly.

Outputs:

- results markdown: `<resultsDir>/<run-id>/`, defaulting to `<project-root>/.qazy/results/<run-id>/`
- screenshots: `<resultsDir>/<run-id>/screenshots/`
- runtime and server logs: `<resultsDir>/<run-id>/logs/`
- exit code: `0` on pass, `1` on fail or error

## Runtime Support

Supported runtime adapters today:

- [Claude Code](https://code.claude.com/docs/en/overview) (`claude`)
- [Codex CLI](https://developers.openai.com/codex/cli) (`codex`)

Use `qazy runtimes` to check installation and `qazy runtimes --smoke` to verify a trivial invocation works in the current environment.

## Examples

Repo-local example apps live under `examples/`:

- `examples/student-portal`
- `examples/task-board`

Each example includes a lightweight HTML app, a `qazy.config.json`, and scenario files you can run directly. See `examples/README.md` for commands.

## Development

Run the mocked unit and CLI suite:

```bash
make test-unit
```

Run live runtime integration coverage:

```bash
make test-runtime-integration
```

Run the example-app integration coverage:

```bash
make test-example-integration
```

Run every test tier:

```bash
make test-all
```

The live runtime tests are intentionally skipped unless the relevant runtime CLI is installed locally.

## Limitations

- Built-in auto-auth supports NextAuth and Better Auth credentials-cookie login only
- PASS/FAIL is inferred from runtime output parsed by Qazy
- Ready checks are simple HTTP probes
- Prompt mode is useful for exploration but less repeatable than checked-in scenarios
- Qazy depends on external runtime CLIs and `agent-browser`; it does not install or manage them
- Qazy is not a replacement for deterministic unit or integration tests
