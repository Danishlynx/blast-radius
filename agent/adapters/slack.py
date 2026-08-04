"""Thin Slack adapter: incoming webhook with console fallback. Zero business logic."""

from __future__ import annotations

import os

import requests
from rich.console import Console
from rich.panel import Panel


def alert(blocks: list[dict], fallback_text: str) -> str:
    """Send Block Kit blocks to SLACK_WEBHOOK_URL; console fallback when unset.

    Returns "slack" or "console" so callers can report the channel used.
    """
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if webhook:
        resp = requests.post(
            webhook, json={"text": fallback_text, "blocks": blocks}, timeout=15
        )
        resp.raise_for_status()
        return "slack"
    Console(stderr=False).print(
        Panel(fallback_text, title="🔔 owner alert (console fallback — set SLACK_WEBHOOK_URL)",
              border_style="yellow")
    )
    return "console"
