from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from qazy.config import (
    build_config_template_text,
    build_default_target,
    config_file_is_formatted,
    format_config_payload,
    get_target,
    load_config,
    read_config_payload,
    resolve_target,
    write_config_template,
)


class ConfigTests(unittest.TestCase):
    def test_load_config_requires_repo_config(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with self.assertRaisesRegex(FileNotFoundError, "qazy init"):
                load_config(Path(tempdir))

    def test_build_default_attached_target_uses_localhost_3000(self) -> None:
        target = build_default_target()

        self.assertEqual(target.name, "default")
        self.assertEqual(target.mode, "attached")
        self.assertEqual(target.base_url, "http://127.0.0.1:3000")
        self.assertEqual(target.ready.path, "/")
        self.assertEqual(target.ready.timeout_seconds, 60)
        self.assertFalse(target.parallel_safe)

    def test_build_default_managed_target_uses_local_port_template(self) -> None:
        target = build_default_target(managed=True)

        self.assertEqual(target.name, "default")
        self.assertEqual(target.mode, "managed")
        self.assertEqual(target.base_url, "http://127.0.0.1:{appPort}")
        self.assertEqual(target.app_port, "auto")
        self.assertEqual(target.env["PORT"], "{appPort}")
        self.assertEqual(target.ready.path, "/")
        self.assertEqual(target.ready.timeout_seconds, 60)

    def test_write_config_template_creates_commented_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)

            path = write_config_template(root)

            self.assertEqual(path, (root / "qazy.config.jsonc").resolve())
            self.assertTrue(path.exists())
            content = path.read_text(encoding="utf-8")
            self.assertIn("// Schema version", content)
            self.assertIn('// "email": "student@example.com"', content)
            self.assertIn('// ,"attached-local"', content)

            payload = read_config_payload(path)
            self.assertEqual(payload["defaultTarget"], "local")
            self.assertEqual(payload["defaultRuntime"], "claude")
            self.assertEqual(payload["screenshotStrategy"], "error")
            self.assertEqual(payload["resultsDir"], ".qazy/results")
            self.assertNotIn("logsDir", payload)
            self.assertEqual(payload["targets"]["local"]["mode"], "managed")
            self.assertEqual(payload["targets"]["local"]["ready"]["path"], "/")
            self.assertEqual(payload["targets"]["local"]["ports"]["appPort"], "auto")
            self.assertEqual(payload["targets"]["local"]["scenarioDefaults"]["authProvider"], "nextauth")
            self.assertIn("codex", payload["targets"]["local"]["runtimeDefaults"])
            self.assertIn("claude", payload["targets"]["local"]["runtimeDefaults"])
            self.assertNotIn("attached-local", payload["targets"])

            config = load_config(root)
            self.assertEqual(config.results_dir, (root / ".qazy/results").resolve())
            self.assertEqual(config.default_runtime, "claude")
            self.assertEqual(config.default_screenshot_strategy, "error")

    def test_config_template_text_parses_as_jsonc(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "qazy.config.jsonc"
            path.write_text(build_config_template_text(), encoding="utf-8")

            payload = read_config_payload(path)

        self.assertEqual(payload["targets"]["local"]["env"]["PORT"], "{appPort}")

    def test_config_file_is_formatted_checks_canonical_json(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            path = root / "qazy.config.json"
            payload = {
                "version": 1,
                "defaultTarget": "local",
                "targets": {
                    "local": {
                        "mode": "attached",
                        "baseUrl": "http://127.0.0.1:3000",
                    }
                },
            }

            path.write_text(format_config_payload(payload), encoding="utf-8")
            self.assertTrue(config_file_is_formatted(path))

            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertFalse(config_file_is_formatted(path))

    def test_config_file_is_formatted_skips_jsonc_files(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "qazy.config.jsonc"
            path.write_text(build_config_template_text(), encoding="utf-8")

            self.assertTrue(config_file_is_formatted(path))

    def test_load_config_parses_jsonc_comments_and_trailing_commas(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "qazy.config.jsonc").write_text(
                """
                {
                  // comment before a top-level field
                  "version": 1,
                  "defaultTarget": "local",
                  "targets": {
                    "local": {
                      "mode": "attached",
                      "baseUrl": "http://127.0.0.1:3000", // trailing comment
                    },
                  },
                }
                """,
                encoding="utf-8",
            )

            config = load_config(root)

        target = get_target(config, None)
        self.assertEqual(target.base_url, "http://127.0.0.1:3000")

    def test_load_config_mentions_repo_root_example_file_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "qazy.config.example.json").write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(FileNotFoundError, "qazy.config.example.json"):
                load_config(root)

    def test_load_config_still_mentions_legacy_example_file_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "qazy").mkdir()
            (root / "qazy" / "qazy.config.example.json").write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(FileNotFoundError, "qazy/qazy.config.example.json"):
                load_config(root)

    def test_load_config_parses_default_target(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "qazy.config.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaultTarget": "dev-remote",
                        "targets": {
                            "local": {
                                "mode": "managed",
                                "baseUrl": "http://localhost:{appPort}",
                                "devCommand": "pnpm dev:mem",
                                "ports": {"appPort": "auto"},
                            },
                            "dev-remote": {
                                "mode": "attached",
                                "baseUrl": "https://dev.complora.com",
                                "ready": {"type": "http", "path": "/healthz", "timeoutSeconds": 15},
                                "parallelSafe": False,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(root)

        target = get_target(config, None)
        self.assertEqual(target.name, "dev-remote")
        self.assertEqual(target.ready.path, "/healthz")
        self.assertEqual(target.ready.timeout_seconds, 15)
        self.assertIsNone(config.results_dir)
        self.assertEqual(config.default_runtime, "claude")

    def test_load_config_parses_default_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "qazy.config.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaultTarget": "local",
                        "defaultRuntime": "codex",
                        "targets": {
                            "local": {
                                "mode": "attached",
                                "baseUrl": "http://127.0.0.1:3000",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(root)

        self.assertEqual(config.default_runtime, "codex")

    def test_load_config_defaults_screenshot_strategy_to_error(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "qazy.config.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaultTarget": "local",
                        "targets": {
                            "local": {
                                "mode": "attached",
                                "baseUrl": "http://127.0.0.1:3000",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(root)

        self.assertEqual(config.default_screenshot_strategy, "error")

    def test_load_config_parses_screenshot_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "qazy.config.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaultTarget": "local",
                        "screenshotStrategy": "checkpoints",
                        "targets": {
                            "local": {
                                "mode": "attached",
                                "baseUrl": "http://127.0.0.1:3000",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(root)

        self.assertEqual(config.default_screenshot_strategy, "checkpoints")

    def test_load_config_rejects_unknown_screenshot_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "qazy.config.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaultTarget": "local",
                        "screenshotStrategy": "polaroid",
                        "targets": {
                            "local": {
                                "mode": "attached",
                                "baseUrl": "http://127.0.0.1:3000",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "screenshotStrategy.*polaroid"):
                load_config(root)

    def test_load_config_rejects_unknown_default_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "qazy.config.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaultTarget": "local",
                        "defaultRuntime": "made-up-runtime",
                        "targets": {
                            "local": {
                                "mode": "attached",
                                "baseUrl": "http://127.0.0.1:3000",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "defaultRuntime.*made-up-runtime"):
                load_config(root)

    def test_load_config_parses_results_dir_relative_to_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "qazy.config.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaultTarget": "dev-remote",
                        "resultsDir": "tmp/qazy-results",
                        "targets": {
                            "dev-remote": {
                                "mode": "attached",
                                "baseUrl": "https://dev.complora.com",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(root)

        self.assertEqual(config.results_dir, (root / "tmp" / "qazy-results").resolve())

    def test_load_config_parses_target_scenario_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "qazy.config.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaultTarget": "local",
                        "targets": {
                            "local": {
                                "mode": "managed",
                                "baseUrl": "http://localhost:{appPort}",
                                "devCommand": "pnpm dev:mem",
                                "ports": {"appPort": "auto"},
                                "scenarioDefaults": {
                                    "email": "student@example.com",
                                    "password": "tester123",
                                    "startPage": "/login",
                                    "useCookie": False,
                                },
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(root)

        target = get_target(config, None)
        self.assertEqual(target.scenario_defaults.email, "student@example.com")
        self.assertEqual(target.scenario_defaults.password, "tester123")
        self.assertEqual(target.scenario_defaults.start_page, "/login")
        self.assertFalse(target.scenario_defaults.use_cookie)

    def test_load_config_rejects_invalid_results_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "qazy.config.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaultTarget": "dev-remote",
                        "resultsDir": {},
                        "targets": {
                            "dev-remote": {
                                "mode": "attached",
                                "baseUrl": "https://dev.complora.com",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "'resultsDir' must be a non-empty string"):
                load_config(root)

    def test_load_config_rejects_logs_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "qazy.config.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaultTarget": "dev-remote",
                        "logsDir": "tmp/qazy-logs",
                        "targets": {
                            "dev-remote": {
                                "mode": "attached",
                                "baseUrl": "https://dev.complora.com",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "'logsDir' is no longer supported"):
                load_config(root)

    def test_load_config_parses_auth_provider_scenario_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "qazy.config.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaultTarget": "local",
                        "targets": {
                            "local": {
                                "mode": "managed",
                                "baseUrl": "http://localhost:{appPort}",
                                "devCommand": "pnpm dev",
                                "ports": {"appPort": "auto"},
                                "scenarioDefaults": {
                                    "authProvider": "better-auth",
                                    "authCookiePrefix": "myapp",
                                },
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(root)

        target = get_target(config, None)
        self.assertEqual(target.scenario_defaults.auth_provider, "better-auth")
        self.assertEqual(target.scenario_defaults.auth_cookie_prefix, "myapp")

    def test_load_config_parses_runtime_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "qazy.config.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaultTarget": "local",
                        "targets": {
                            "local": {
                                "mode": "managed",
                                "baseUrl": "http://localhost:{appPort}",
                                "devCommand": "pnpm dev",
                                "ports": {"appPort": "auto"},
                                "runtimeDefaults": {
                                    "claude": {
                                        "model": "claude-sonnet-4-5",
                                    },
                                    "codex": {
                                        "model": "gpt-5.4-mini",
                                        "reasoningEffort": "low",
                                    },
                                },
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(root)

        target = get_target(config, None)
        self.assertEqual(target.runtime_defaults["claude"].model, "claude-sonnet-4-5")
        self.assertIsNone(target.runtime_defaults["claude"].reasoning_effort)
        self.assertEqual(target.runtime_defaults["codex"].model, "gpt-5.4-mini")
        self.assertEqual(target.runtime_defaults["codex"].reasoning_effort, "low")

    def test_load_config_rejects_unknown_runtime_default(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "qazy.config.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaultTarget": "local",
                        "targets": {
                            "local": {
                                "mode": "attached",
                                "baseUrl": "https://dev.example.com",
                                "runtimeDefaults": {
                                    "made-up-runtime": {"model": "x"},
                                },
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "runtimeDefaults.*made-up-runtime"):
                load_config(root)

    def test_load_config_rejects_unknown_auth_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "qazy.config.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaultTarget": "local",
                        "targets": {
                            "local": {
                                "mode": "managed",
                                "baseUrl": "http://localhost:{appPort}",
                                "devCommand": "pnpm dev",
                                "ports": {"appPort": "auto"},
                                "scenarioDefaults": {"authProvider": "made-up-auth"},
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "authProvider"):
                load_config(root)

    def test_load_config_parses_auth_base_path(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "qazy.config.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaultTarget": "local",
                        "targets": {
                            "local": {
                                "mode": "managed",
                                "baseUrl": "http://localhost:{appPort}",
                                "devCommand": "pnpm dev",
                                "ports": {"appPort": "auto"},
                                "scenarioDefaults": {"authBasePath": "/auth"},
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(root)

        target = get_target(config, None)
        self.assertEqual(target.scenario_defaults.auth_base_path, "/auth")

    def test_load_config_rejects_auth_base_path_without_leading_slash(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "qazy.config.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaultTarget": "local",
                        "targets": {
                            "local": {
                                "mode": "managed",
                                "baseUrl": "http://localhost:{appPort}",
                                "devCommand": "pnpm dev",
                                "ports": {"appPort": "auto"},
                                "scenarioDefaults": {"authBasePath": "auth"},
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "authBasePath"):
                load_config(root)

    def test_load_config_rejects_empty_auth_cookie_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "qazy.config.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaultTarget": "local",
                        "targets": {
                            "local": {
                                "mode": "managed",
                                "baseUrl": "http://localhost:{appPort}",
                                "devCommand": "pnpm dev",
                                "ports": {"appPort": "auto"},
                                "scenarioDefaults": {"authCookiePrefix": "   "},
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "authCookiePrefix"):
                load_config(root)

    def test_load_config_rejects_invalid_target_scenario_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "qazy.config.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaultTarget": "local",
                        "targets": {
                            "local": {
                                "mode": "managed",
                                "baseUrl": "http://localhost:{appPort}",
                                "devCommand": "pnpm dev:mem",
                                "ports": {"appPort": "auto"},
                                "scenarioDefaults": {
                                    "startPage": {},
                                },
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "scenarioDefaults.startPage"):
                load_config(root)

    def test_resolve_managed_target_allocates_ports_and_renders_templates(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "qazy.config.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaultTarget": "local",
                        "targets": {
                            "local": {
                                "mode": "managed",
                                "baseUrl": "http://localhost:{appPort}",
                                "devCommand": "pnpm dev:mem",
                                "ports": {"appPort": "auto", "mongoPort": "auto"},
                                "env": {
                                    "PORT": "{appPort}",
                                    "MONGO_PORT": "{mongoPort}",
                                },
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            config = load_config(root)
            target = get_target(config, "local")
            allocated = iter([3100, 27017])

            resolved = resolve_target(
                target,
                allocate_port=lambda: next(allocated),
            )

        self.assertEqual(resolved.base_url, "http://localhost:3100")
        self.assertEqual(resolved.dev_command, ("pnpm", "dev:mem"))
        self.assertEqual(resolved.env["PORT"], "3100")
        self.assertEqual(resolved.env["MONGO_PORT"], "27017")

    def test_resolve_attached_target_uses_existing_base_url(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "qazy.config.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaultTarget": "dev-remote",
                        "targets": {
                            "dev-remote": {
                                "mode": "attached",
                                "baseUrl": "https://dev.complora.com",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            config = load_config(root)
            target = get_target(config, None)

            resolved = resolve_target(target)

        self.assertIsNone(resolved.dev_command)
        self.assertEqual(resolved.base_url, "https://dev.complora.com")

    def test_resolve_attached_target_requires_explicit_placeholder_values(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "qazy.config.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaultTarget": "attached-local",
                        "targets": {
                            "attached-local": {
                                "mode": "attached",
                                "baseUrl": "http://localhost:{appPort}",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            config = load_config(root)
            target = get_target(config, None)

            with self.assertRaisesRegex(RuntimeError, "requires appPort"):
                resolve_target(target)
