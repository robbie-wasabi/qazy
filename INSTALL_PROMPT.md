# Qazy Install Prompt

You are installing Qazy in the current repository. Your job is to get the user
to a working `qazy.config.jsonc` as fast as possible.

**Default to inferring and writing. Only ask when you genuinely cannot infer.**

## Flow

1. Skim the project — whatever files tell you how it runs (README, package
   manifest, lockfile, Makefile, env example). Use your own tools.
2. If no Qazy config exists, run `qazy init` to create the starter
   `qazy.config.jsonc`. Then read that generated file and patch it with what
   you inferred. Do not hand-write the initial starter config.
3. If a Qazy config already exists, read it, summarize it (redact any
   credentials), and ask once whether to patch it, migrate it to JSONC,
   overwrite it, or leave it alone. Qazy loads `qazy.config.json` before
   `qazy.config.jsonc`, so do not leave both files active unless the user
   understands that the JSON file wins.
4. Ask one short optional credentials question: whether the user wants to add
   safe test `scenarioDefaults.email` / `scenarioDefaults.password` values as
   fallback defaults for scenarios that do not provide their own credentials.
   Make clear they can skip this; omit credentials when they decline or do not
   provide both values.
5. Patch the config, run `qazy config check`, and show the user the result.
6. Add the effective Qazy output directories to `.gitignore`. Use the config's
   `resultsDir` and `logsDir` values when present; otherwise add the defaults
   `.qazy/results/` and `.qazy/logs/`. Preserve existing `.gitignore` content
   and avoid duplicate entries.
7. If something genuinely can't be inferred besides optional credentials, ask
   one short question and patch the config.

When running `qazy init` from a subdirectory, use `--project-root` or `cd` so
the config is created in the repository or app directory the user wants Qazy to
test.

## Defaults (pick these unless the project says otherwise)

- **Mode:** `managed` if the project has a dev script; `attached` otherwise.
- **Dev command:** the documented one. For Node, pick by lockfile:
  `pnpm-lock.yaml` → `pnpm dev`, `yarn.lock` → `yarn dev`,
  `package-lock.json` → `npm run dev`, `bun.lockb` → `bun run dev`. Confirm the
  script exists in `package.json`.
- **Base URL:** `http://127.0.0.1:{appPort}` for managed with
  `ports.appPort: "auto"` and `env.PORT: "{appPort}"`; the documented URL
  (usually `http://127.0.0.1:3000`) for attached.
- **Ready path:** `/` unless the project clearly uses a health route.
- **Runtime:** `claude` (Qazy's default). If only one of `claude`, `codex`,
  `opencode` is installed, pick that.
- **Auth:** `useCookie: false` unless the app is obviously NextAuth or
  Better Auth with credentials login. Ask whether to add safe test fallback
  credentials in `scenarioDefaults`, but leave credentials out by default.
- **Outputs:** omit `resultsDir` / `logsDir` — defaults are fine.
- **`parallelSafe`:** `false` unless the target is clearly isolated.

## Hard rules

- Never read or write real secrets. Don't open `.env*` or credentials files
  unless the user asks. Only store credentials in the config if the user
  confirms they are safe test credentials and provides both values.
- Only use supported fields (see cheat sheet). Screenshots, headed mode, and
  prompt text are CLI flags, not config fields.
- Don't run `qazy runtimes --smoke` or a Qazy browser test without asking —
  those can invoke paid model CLIs.
- Do update `.gitignore` so Qazy-generated result and log directories are not
  committed.

## Config cheat sheet

Top-level: `version` (1), `defaultTarget`, `defaultRuntime`
(`claude` | `codex` | `opencode`), `resultsDir`, `logsDir`, `targets`.

Target: `mode` (`managed` | `attached`), `baseUrl`, `devCommand` (managed
only), `ports` (`appPort`, `mongoPort` — int or `"auto"`), `env`, `ready`,
`parallelSafe`, `scenarioDefaults`, `runtimeDefaults`.

`ready`: `type: "http"`, `path`, `timeoutSeconds`.

`scenarioDefaults`: `email`, `password`, `startPage`, `useCookie`,
`authProvider` (`nextauth` | `better-auth`), `authCookiePrefix`,
`authBasePath` (default `/api/auth`).

`runtimeDefaults`: per-runtime `model` and (codex) `reasoningEffort`. Omit if
unsure.

## Starter config

Do not copy this by hand when creating a new config. Run `qazy init`, read the
generated file, and patch it. This shape is here only as a reference:

```jsonc
{
  // Target used when --target is omitted.
  "version": 1,
  "defaultTarget": "local",
  "defaultRuntime": "claude",
  "targets": {
    "local": {
      "mode": "managed",
      "baseUrl": "http://127.0.0.1:{appPort}",
      "devCommand": "pnpm dev",
      "ports": { "appPort": "auto" },
      "env": { "PORT": "{appPort}" },
      "ready": { "type": "http", "path": "/", "timeoutSeconds": 60 },
      "parallelSafe": false,
      "scenarioDefaults": { "startPage": "/", "useCookie": false }
    }
  }
}
```

For attached: drop `devCommand`, `ports`, `env`; set `mode: "attached"` and a
concrete `baseUrl`.

For built-in cookie auth, add `useCookie: true` plus
`authProvider: "nextauth"` or `"better-auth"`. Store `email`/`password` only
if the user confirms they're safe test credentials.

## Finishing

After `qazy config check` passes, tell the user:

- The config path.
- The `.gitignore` entries added or confirmed.
- One line on mode, base URL, dev command (if any), ready path, runtime.
- How to run the first check:
  ```bash
  qazy -p "confirm the app loads"
  ```
- Anything you assumed that they should verify.

Keep it short.
