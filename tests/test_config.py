from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from qazy.config import build_default_target, get_target, load_config, resolve_target, write_example_config


class ConfigTests(unittest.TestCase):
    def test_load_config_requires_repo_config(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with self.assertRaisesRegex(FileNotFoundError, "Create qazy.config.json"):
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

    def test_write_example_config_creates_starter_file(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)

            path = write_example_config(root)

            self.assertEqual(path, (root / "qazy.config.example.json").resolve())
            self.assertTrue(path.exists())
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["defaultTarget"], "local")
            self.assertEqual(payload["targets"]["local"]["mode"], "managed")
            self.assertEqual(payload["targets"]["local"]["ready"]["path"], "/")

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
