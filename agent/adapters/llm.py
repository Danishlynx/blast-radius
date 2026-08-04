"""Thin LLM adapter: Anthropic or Ollama, temperature 0. Zero business logic.

Every prompt lives in prompts/ as a versioned file. When no provider is
configured (no ANTHROPIC_API_KEY and Ollama unreachable), calls return None
and callers degrade gracefully — the rule-based pipeline never blocks on an
LLM.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import requests

PROMPTS = Path(__file__).resolve().parents[2] / "prompts"


def _prompt(name: str, **kwargs: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8").format(**kwargs)


def complete(prompt: str, max_tokens: int = 400) -> str | None:
    """One-shot completion via the configured provider; None if unavailable."""
    provider = os.environ.get("LLM_PROVIDER", "anthropic")
    if provider == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic

        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=os.environ.get("LLM_MODEL", "claude-sonnet-5"),
            max_tokens=max_tokens,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in msg.content if b.type == "text").strip()
    if provider == "ollama":
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        try:
            resp = requests.post(
                f"{host}/api/generate",
                json={
                    "model": os.environ.get("LLM_MODEL", "llama3.1"),
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0},
                },
                timeout=120,
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
        except requests.RequestException:
            return None
    return None


def semantic_note(change: object, sql_refs: list[dict]) -> str | None:
    """Short expert note on the semantic impact of a schema change."""
    try:
        prompt = _prompt(
            "diagnose_semantic.md",
            change=json.dumps(change.model_dump(mode="json") if hasattr(change, "model_dump") else change, indent=1),
            sql_refs=json.dumps(sql_refs, indent=1),
        )
        return complete(prompt)
    except Exception:
        return None  # the LLM is an enrichment, never a dependency
