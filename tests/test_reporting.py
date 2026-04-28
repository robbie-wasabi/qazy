from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from qazy.cli import main
from qazy.reporting import analyze_log


class ReportingTests(unittest.TestCase):
    def test_analyze_log_dedupes_claude_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            log_dir = root / ".qazy" / "results" / "run-123" / "logs"
            log_dir.mkdir(parents=True)
            log_path = log_dir / "claude.log"
            log_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "assistant",
                                "message": {
                                    "id": "msg-1",
                                    "usage": {
                                        "input_tokens": 10,
                                        "output_tokens": 2,
                                        "cache_creation_input_tokens": 5,
                                        "cache_read_input_tokens": 1,
                                    },
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "assistant",
                                "message": {
                                    "id": "msg-1",
                                    "usage": {
                                        "input_tokens": 10,
                                        "output_tokens": 2,
                                        "cache_creation_input_tokens": 5,
                                        "cache_read_input_tokens": 1,
                                    },
                                },
                            }
                        ),
                        json.dumps({"type": "result", "total_cost_usd": 1.25}),
                    ]
                ),
                encoding="utf-8",
            )
            totals = analyze_log(log_path)
            assert totals is not None
            self.assertEqual(totals.input_tokens, 10)
            self.assertEqual(totals.output_tokens, 2)
            self.assertEqual(totals.cache_creation_input_tokens, 5)
            self.assertEqual(totals.cache_read_input_tokens, 1)
            self.assertEqual(totals.messages, 1)
            self.assertAlmostEqual(totals.total_cost_usd, 1.25)

    def test_tokens_cli_sums_multiple_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            log_dir = root / ".qazy" / "results" / "run-123" / "logs"
            log_dir.mkdir(parents=True)
            (root / "user-scenarios").mkdir()
            (log_dir / "claude.log").write_text(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "id": "msg-1",
                            "usage": {
                                "input_tokens": 10,
                                "output_tokens": 3,
                                "cache_creation_input_tokens": 2,
                                "cache_read_input_tokens": 1,
                            },
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (log_dir / "codex.log").write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 20,
                            "output_tokens": 4,
                            "cached_input_tokens": 6,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(["tokens", "--project-root", str(root)])

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn("claude.log:", output)
            self.assertIn("codex.log:", output)
            self.assertIn("TOTAL:", output)
            self.assertIn("Input:               30 tokens", output)
