"""Usage reporting helpers for runtime log files."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class UsageTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    messages: int = 0
    total_cost_usd: float = 0.0
    has_cost: bool = False

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )

    def add(self, other: "UsageTotals") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens
        self.messages += other.messages
        self.total_cost_usd += other.total_cost_usd
        self.has_cost = self.has_cost or other.has_cost


def analyze_log(log_path: Path) -> UsageTotals | None:
    totals = UsageTotals()
    seen_claude_message_ids: set[str] = set()

    for raw_line in log_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        if not isinstance(event, dict):
            continue

        event_type = event.get("type")

        if event_type == "assistant":
            message = event.get("message", {})
            if not isinstance(message, dict):
                continue
            usage = message.get("usage", {})
            message_id = message.get("id")
            if not isinstance(usage, dict) or not isinstance(message_id, str):
                continue
            if message_id in seen_claude_message_ids:
                continue
            seen_claude_message_ids.add(message_id)
            totals.input_tokens += int(usage.get("input_tokens", 0))
            totals.output_tokens += int(usage.get("output_tokens", 0))
            totals.cache_creation_input_tokens += int(usage.get("cache_creation_input_tokens", 0))
            totals.cache_read_input_tokens += int(usage.get("cache_read_input_tokens", 0))
            totals.messages += 1
            continue

        if event_type == "turn.completed":
            usage = event.get("usage", {})
            if not isinstance(usage, dict):
                continue
            totals.input_tokens += int(usage.get("input_tokens", 0))
            totals.output_tokens += int(usage.get("output_tokens", 0))
            totals.cache_read_input_tokens += int(usage.get("cached_input_tokens", 0))
            totals.messages += 1
            continue

        if event_type == "result":
            if "total_cost_usd" in event:
                totals.total_cost_usd += float(event.get("total_cost_usd", 0.0))
                totals.has_cost = True
            continue

    if totals.messages == 0 and not totals.has_cost:
        return None
    return totals


def format_usage(totals: UsageTotals) -> str:
    lines = [
        f"  Input:       {totals.input_tokens:>10,} tokens",
        f"  Output:      {totals.output_tokens:>10,} tokens",
        f"  Cache write: {totals.cache_creation_input_tokens:>10,} tokens",
        f"  Cache read:  {totals.cache_read_input_tokens:>10,} tokens",
        f"  Messages:    {totals.messages:>10,}",
    ]
    if totals.has_cost:
        lines.append(f"  Total cost:  ${totals.total_cost_usd:.4f}")
    return "\n".join(lines)


def format_usage_inline(totals: UsageTotals) -> str:
    return (
        f"{totals.total_tokens:,} total "
        f"({totals.input_tokens:,} input, {totals.output_tokens:,} output, "
        f"{totals.cache_creation_input_tokens:,} cache write, "
        f"{totals.cache_read_input_tokens:,} cache read)"
    )
