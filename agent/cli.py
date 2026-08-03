"""Blast Radius CLI: scan | audit | watch.

Day 1 ships the skeleton; the commands land with their subsystems
(scan: Day 2, watch: Day 3, audit: Day 6).
"""

from __future__ import annotations

import click
from rich.console import Console

console = Console()


@click.group()
def main() -> None:
    """Autonomous ML supply-chain guardian on the DataHub metadata graph."""


@main.command()
@click.argument("dataset_urn")
def scan(dataset_urn: str) -> None:
    """Scan a dataset for schema changes and walk the blast radius."""
    console.print("[yellow]not implemented yet — scan arrives on Day 2[/yellow]")
    raise SystemExit(1)


@main.command()
@click.argument("model_urn")
def audit(model_urn: str) -> None:
    """Audit a model's features for structural target leakage."""
    console.print("[yellow]not implemented yet — audit arrives on Day 6[/yellow]")
    raise SystemExit(1)


@main.command()
def watch() -> None:
    """Run the sentinel daemon (schema-change poller)."""
    console.print("[yellow]not implemented yet — watch arrives on Day 3[/yellow]")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
