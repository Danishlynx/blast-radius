"""Blast Radius CLI: scan | audit | watch.

scan (Day 2): synthesize a ChangeEvent from DataHub's schema history and walk
the blast radius. audit (Day 6) and watch (Day 3) land with their subsystems.
"""

from __future__ import annotations

import sys

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

# Windows consoles default to cp1252, which can't render the tree icons.
for stream in (sys.stdout, sys.stderr):
    if stream.encoding and stream.encoding.lower() not in ("utf-8", "utf8"):
        stream.reconfigure(encoding="utf-8")

console = Console()

TYPE_ICONS = {
    "dataset": "🗄",
    "mlFeature": "🧬",
    "mlFeatureTable": "🧮",
    "mlModel": "🤖",
    "mlModelDeployment": "🚀",
    "mlModelGroup": "📦",
}


@click.group()
def main() -> None:
    """Autonomous ML supply-chain guardian on the DataHub metadata graph."""
    load_dotenv()


@main.command()
@click.argument("dataset_urn")
@click.option("--column", "columns", multiple=True,
              help="Hand-feed changed column(s) instead of diffing schema history.")
def scan(dataset_urn: str, columns: tuple[str, ...]) -> None:
    """Scan a dataset for schema changes and walk the blast radius."""
    from agent.adapters import sdk_read
    from agent.adapters.mcp_read import run_sync
    from agent.models import ChangeEvent, ColumnChange
    from agent.sentinel import synthesize_change_event
    from agent.traverse import compute_blast_radius

    graph = sdk_read.connect()

    console.rule("[bold cyan][1/5] DETECT")
    if columns:
        change = ChangeEvent(
            source="cli",
            entity_urn=dataset_urn,
            change_type="other",
            columns=[ColumnChange(before=c) for c in columns],
            raw={"hand_fed": True},
        )
        console.print(f"hand-fed change on [bold]{_short(dataset_urn)}[/bold]: {', '.join(columns)}")
    else:
        change = synthesize_change_event(graph, dataset_urn)
        if change is None:
            console.print("[green]no schema change detected between the last two versions[/green]")
            raise SystemExit(0)
        for c in change.columns:
            console.print(
                f"detected [bold red]{change.change_type}[/bold red]: "
                f"{c.before or '∅'} ({c.type_before or '?'}) → {c.after or '∅'} ({c.type_after or '?'})"
            )

    console.rule("[bold cyan][2/5] TRAVERSE")
    radius = run_sync(compute_blast_radius(change, graph))

    tree = Tree(f"🗄 [bold]{_short(dataset_urn)}[/bold] (changed)")
    by_hop: dict[int, list] = {}
    for n in radius.nodes:
        by_hop.setdefault(n.hop, []).append(n)
    cursor = tree
    for hop in sorted(by_hop):
        branch = cursor.add(f"[dim]hop {hop}[/dim]")
        for n in by_hop[hop]:
            icon = TYPE_ICONS.get(n.entity_type, "•")
            label = f"{icon} {n.name or _short(n.urn)} [dim]({n.platform or n.entity_type})[/dim]"
            if n.entity_type == "mlModel":
                label += " [bold red]● deployed[/bold red]" if n.deployed else " [dim]not deployed[/dim]"
            if n.env:
                label += f" [red]env={n.env}[/red]"
            branch.add(label)
        cursor = branch
    console.print(tree)

    if radius.paths:
        console.print("\n[bold]column flow:[/bold]")
        for p in radius.paths:
            console.print("  " + " [dim]→[/dim] ".join(p))

    table = Table(title=f"blast radius — confidence: {radius.confidence}")
    table.add_column("entity")
    table.add_column("type")
    table.add_column("hop", justify="right")
    table.add_column("owners")
    for n in radius.nodes:
        table.add_row(
            n.name or _short(n.urn),
            n.entity_type,
            str(n.hop),
            ", ".join(o.split(":")[-1] for o in n.owners) or "[dim]—[/dim]",
        )
    console.print(table)

    models = radius.models
    deployments = radius.deployments
    console.print(
        Panel(
            f"[bold]{len([n for n in radius.nodes if n.entity_type == 'dataset'])}[/bold] datasets · "
            f"[bold]{len([n for n in radius.nodes if n.entity_type == 'mlFeature'])}[/bold] features · "
            f"[bold]{len(models)}[/bold] models · "
            f"[bold red]{len(deployments)}[/bold red] production deployments in the blast radius",
            title="impact",
        )
    )
    console.print("[dim]stages 3-5 (diagnose / act / remember) land on Days 3-4[/dim]")


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


def _short(urn: str) -> str:
    if urn.startswith("urn:li:dataset:"):
        inner = urn.split(",")
        return inner[1] if len(inner) > 1 else urn
    return urn


if __name__ == "__main__":
    main()
