from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from qazy.config import get_target, load_config, resolve_target


class ConfigTests(unittest.TestCase):
    def test_load_config_requires_repo_config(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with self.assertRaisesRegex(FileNotFoundError, "Create qazy.config.json"):
                load_config(Path(tempdir))

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
