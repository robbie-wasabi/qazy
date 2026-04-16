# Repository Guidelines

## Project Structure & Module Organization
`qazy/` is the application package. Keep CLI parsing in `qazy/cli.py`, config loading in `qazy/config.py`, execution flow in `qazy/runner.py`, runtime adapters in `qazy/runtimes.py`, reporting in `qazy/reporting.py`, and screenshot helpers in `qazy/screenshot_helper.py`. Put package metadata in `pyproject.toml` and user-facing usage notes in `README.md`.

`tests/` holds the test suite. Follow the existing `test_*.py` pattern, grouped by module or behavior such as `tests/test_config.py` and `tests/test_cli_functional.py`.

## Build, Test, and Development Commands
Install the package in editable mode:

```bash
python3 -m pip install -e .
```

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

Smoke-check runtime integrations when the local CLIs are installed:

```bash
qazy runtimes --smoke
```

## Coding Style & Naming Conventions
Target Python 3.12 and follow the style already in the package: 4-space indentation, `snake_case` for modules/functions, `CapWords` for `unittest.TestCase` classes, and `UPPER_CASE` for constants. Prefer `pathlib.Path`, dataclasses, and explicit type hints. Keep module docstrings short and CLI help text precise.

No formatter or linter is configured in `pyproject.toml`, so keep imports tidy, avoid unused code, and match the surrounding file before introducing a new pattern.

## Testing Guidelines
Use `unittest`, not `pytest`-specific features. Add tests next to the affected behavior in `tests/test_<area>.py`. Favor isolated temp directories, fake binaries, and `unittest.mock.patch` over real external dependencies.

Keep the suite split along these lines:

- mocked unit and CLI behavior in `tests/test_config.py`, `tests/test_reporting.py`, `tests/test_runtimes.py`, and `tests/test_cli_functional.py`
- live runtime integration checks in `tests/test_live_runtimes.py`
- example-app integration coverage in `tests/test_examples.py`

Live CLI checks are intentionally gated with `skipUnless(...)`.

## Commit & Pull Request Guidelines
This repository currently has no commit history, so there is no inherited convention to copy. Start with short, imperative commit subjects such as `Add runtime config validation` or `Cover screenshot fallback path`.

For pull requests, include: the behavior changed, the commands/tests you ran, any config or runtime assumptions, and sample CLI output when user-visible behavior changes. Link the relevant issue when one exists.

## Configuration & Generated Files
Qazy expects a `qazy.config.json` in the target project root. Never commit secrets, machine-specific paths, or generated artifacts such as `.qazy/`, `user-scenarios-results/`, or `__pycache__/`.
