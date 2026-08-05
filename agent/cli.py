"""Blast Radius CLI: scan | watch | audit.

scan: one-shot — diff schema history (or hand-feed a column) and run the
pipeline. watch: sentinel daemon — poll the WATCHLIST and run the pipeline on
every detected change. audit (Day 6) lands with the leakage auditor.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

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

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"

TYPE_ICONS = {
    "dataset": "🗄",
    "mlFeature": "🧬",
    "mlFeatureTable": "🧮",
    "mlModel": "🤖",
    "mlModelDeployment": "🚀",
    "mlModelGroup": "📦",
}

SEVERITY_STYLE = {"P0": "bold red", "P1": "red", "P2": "yellow", "P3": "green"}


@click.group()
def main() -> None:
    """Autonomous ML supply-chain guardian on the DataHub metadata graph."""
    load_dotenv()


@main.command()
@click.argument("dataset_urn")
@click.option("--column", "columns", multiple=True,
              help="Hand-feed changed column(s) instead of diffing schema history.")
def scan(dataset_urn: str, columns: tuple[str, ...]) -> None:
    """Scan a dataset for schema changes and walk + diagnose the blast radius."""
    from agent.adapters import sdk_read
    from agent.models import ChangeEvent, ColumnChange
    from agent.sentinel import synthesize_change_event

    graph = sdk_read.connect()

    console.rule("[bold cyan][1/5] DETECT")
    if columns:
        change = ChangeEvent(
            source="cli",
            entity_urn=dataset_urn,
            change_type="drop",
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

    run_pipeline(graph, change)


@main.command()
def watch() -> None:
    """Run the sentinel daemon: poll the WATCHLIST for schema changes."""
    from agent.adapters import sdk_read
    from agent.sentinel import watch as watch_loop

    graph = sdk_read.connect()
    # URNs contain commas, so a comma-separated WATCHLIST can only be split
    # at boundaries where the next entry begins.
    raw_list = os.environ.get("WATCHLIST", "")
    urns = [u.strip() for u in re.split(r",(?=urn:li:)", raw_list) if u.strip()]
    if not urns:
        console.print("[red]WATCHLIST is empty — run 'make backfill' or set it in .env[/red]")
        raise SystemExit(1)
    interval = int(os.environ.get("POLL_INTERVAL_S", "15"))

    console.print(
        Panel(
            "\n".join(_short(u) for u in urns),
            title=f"sentinel watching {len(urns)} dataset(s) · every {interval}s · Ctrl-C to stop",
        )
    )

    def on_change(change) -> None:
        console.rule("[bold cyan][1/5] DETECT")
        for c in change.columns:
            console.print(
                f"detected [bold red]{change.change_type}[/bold red] on "
                f"[bold]{_short(change.entity_urn)}[/bold]: "
                f"{c.before or '∅'} ({c.type_before or '?'}) → {c.after or '∅'} ({c.type_after or '?'})"
            )
        run_pipeline(graph, change)
        console.print("[dim]sentinel resumes watching…[/dim]")

    try:
        watch_loop(graph, urns, interval, on_change)
    except KeyboardInterrupt:
        console.print("\n[dim]sentinel stopped[/dim]")


def run_pipeline(graph, change) -> None:
    """Stages 2-3 (traverse, diagnose); act/remember land on Day 4."""
    from agent.adapters.mcp_read import run_sync
    from agent.diagnose import diagnose
    from agent.traverse import compute_blast_radius

    console.rule("[bold cyan][2/5] TRAVERSE")
    radius = run_sync(compute_blast_radius(change, graph))
    _render_radius(change.entity_urn, radius)

    console.rule("[bold cyan][3/5] DIAGNOSE")
    diag = diagnose(graph, change, radius)
    style = SEVERITY_STYLE[diag.severity]
    console.print(
        Panel(
            f"verdict: [bold]{diag.verdict}[/bold] — {diag.rationale}\n"
            f"severity: [{style}]{diag.severity}[/{style}]"
            + (f"\n\n[italic]{diag.llm_note}[/italic]" if diag.llm_note else ""),
            title="diagnosis",
            border_style=style,
        )
    )
    if diag.sql_refs:
        table = Table(title="captured SQL evidence")
        table.add_column("asset")
        table.add_column("reason")
        table.add_column("snippet", overflow="fold")
        for r in diag.sql_refs:
            table.add_row(_short(r.asset), r.reason, r.snippet)
        console.print(table)

    evidence_path = _write_evidence(diag.evidence)
    console.print(f"[dim]evidence chain → {evidence_path}[/dim]")

    console.rule("[bold cyan][4/5] ACT")
    from agent.act import act, planned_actions

    report = act(graph, change, radius, diag)
    if not report.results:
        console.print("[green]severity requires no action beyond the doc note[/green]")
    status_icon = {
        "done": "[green]✓[/green]",
        "updated": "[cyan]↻[/cyan]",
        "failed": "[red]✗[/red]",
        "skipped": "[dim]−[/dim]",
        "needs-approval": "[yellow]⏸[/yellow]",
    }
    for r in report.results:
        console.print(f"  {status_icon[r.status]} {r.action:<9} {r.detail}")
    planned = set(planned_actions(diag.severity))
    if report.duplicate_suppressed:
        skipped = planned - {r.action for r in report.results}
        if skipped:
            console.print(f"  [dim]− {', '.join(sorted(skipped))}: unchanged (existing incident covers them)[/dim]")

    console.rule("[bold cyan][5/5] REMEMBER")
    if report.duplicate_suppressed:
        console.print(
            "[bold cyan]duplicate suppressed[/bold cyan] — this break is already in the graph; "
            f"updated {report.incident_urn}"
        )
    if report.run_log:
        console.print(
            f"run record → structured property on {_short(report.target_urn)} "
            f"[dim](run {report.run_log['run_id']}, hash {report.run_log['evidence_hash'][:8]})[/dim]"
        )
    console.print("[dim]the agent keeps no database — its memory is the metadata graph[/dim]")


def _render_radius(root_urn: str, radius) -> None:
    tree = Tree(f"🗄 [bold]{_short(root_urn)}[/bold] (changed)")
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
            if n.owners:
                label += f" [blue]👤 {', '.join(o.split(':')[-1] for o in n.owners)}[/blue]"
            branch.add(label)
        cursor = branch
    console.print(tree)

    if radius.paths:
        console.print("\n[bold]column flow:[/bold]")
        for p in radius.paths:
            console.print("  " + " [dim]→[/dim] ".join(p))

    console.print(
        Panel(
            f"[bold]{len([n for n in radius.nodes if n.entity_type == 'dataset'])}[/bold] datasets · "
            f"[bold]{len([n for n in radius.nodes if n.entity_type == 'mlFeature'])}[/bold] features · "
            f"[bold]{len(radius.models)}[/bold] models · "
            f"[bold red]{len(radius.deployments)}[/bold red] production deployments — "
            f"confidence: {radius.confidence}",
            title="impact",
        )
    )


def _write_evidence(evidence: dict) -> Path:
    EXAMPLES.mkdir(exist_ok=True)
    path = EXAMPLES / f"evidence-{evidence['evidence_hash'][:8]}.json"
    path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    transcript = EXAMPLES / "transcript.md"
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    with transcript.open("a", encoding="utf-8") as fh:
        fh.write(
            f"- {stamp} · {evidence['severity']} {evidence['verdict']} on "
            f"{_short(evidence['change']['entity_urn'])} · hash {evidence['evidence_hash'][:8]} · "
            f"{len(evidence['models'])} model(s), {len(evidence['deployments'])} deployment(s)\n"
        )
    return path


@main.command()
@click.argument("model_urn", required=False)
def audit(model_urn: str | None) -> None:
    """Audit a model's features for structural target leakage (no data access)."""
    from agent import leakage
    from agent.adapters import graphql, mcp_mutate, sdk_read, sdk_write
    from agent.memory import find_existing_incident

    graph = sdk_read.connect()
    if not model_urn:
        models = sorted(
            graph.get_urns_by_filter(entity_types=["mlModel"], query="fraud_model", batch_size=10)
        )
        if not models:
            console.print("[red]no mlModel found — run 'make backfill' first[/red]")
            raise SystemExit(1)
        model_urn = models[-1]

    console.rule("[bold cyan]LEAKAGE AUDIT")
    console.print(f"model: [bold]{model_urn}[/bold]")
    report = leakage.audit(graph, model_urn)

    table = Table(title=f"label={report.label_column} · event time={report.event_ts_column}")
    table.add_column("feature")
    table.add_column("verdict")
    table.add_column("rule")
    table.add_column("why", overflow="fold")
    style = {"LEAK": "bold red", "SUSPECT": "yellow", "CLEAN": "green"}
    for v in report.verdicts:
        table.add_row(v.feature, f"[{style[v.verdict]}]{v.verdict}[/{style[v.verdict]}]",
                      v.rule or "—", v.reason or "no leakage signal")
    console.print(table)
    for v in report.leaks + report.suspects:
        if v.path:
            console.print(f"  [dim]{v.feature}:[/dim] {' [dim]→[/dim] '.join(v.path)}")

    flagged = report.leaks + report.suspects
    if not flagged:
        console.print("[green]no leakage signals — nothing to write back[/green]")
        _write_audit_examples(report)
        return

    console.rule("[bold cyan]WRITE BACK TO THE GRAPH")
    sdk_write.ensure_tag(
        graph, leakage.LEAKAGE_TAG, "leakage-suspect",
        "Structural target-leakage signal found by Blast Radius. See the model docs for the audit.",
        "#f5a623",
    )
    flagged_urns = [
        f"urn:li:mlFeature:(customer_features,{v.feature})" for v in flagged
    ] + [model_urn]
    try:
        mcp_mutate.add_tag(flagged_urns, leakage.LEAKAGE_TAG)
        console.print("  [green]✓[/green] leakage-suspect tag (via MCP)")
    except Exception:
        for urn in flagged_urns:
            sdk_write.add_tag(graph, urn, leakage.LEAKAGE_TAG)
        console.print("  [green]✓[/green] leakage-suspect tag (via SDK fallback)")

    md = leakage.render_markdown(report)
    try:
        mcp_mutate.append_description(model_urn, md)
        console.print("  [green]✓[/green] audit report appended to model docs (via MCP)")
    except Exception:
        sdk_write.append_model_description(graph, model_urn, md)
        console.print("  [green]✓[/green] audit report appended to model docs (via SDK fallback)")

    if report.leaks:
        targets = sorted({
            s for v in report.leaks
            for s in (["urn:li:dataset:(urn:li:dataPlatform:duckdb,warehouse.main.fct_customer_features,PROD)"])
        })
        title = f"[LEAKAGE] {len(report.leaks)} feature(s) leak the target into training data"
        description = (
            "## 🧬 Target leakage detected structurally\n\n"
            + "\n".join(f"- `{v.feature}` — {v.rule}: {v.reason}" for v in report.leaks)
            + f"\n{md}"
        )
        existing = find_existing_incident(targets, report.audit_hash)
        if existing:
            graphql.update_incident(existing["urn"], title, description)
            console.print(f"  [cyan]↻[/cyan] duplicate suppressed — updated {existing['urn']}")
        else:
            urn = graphql.raise_incident(targets, title, description, priority="HIGH",
                                         incident_type="CUSTOM", custom_type="TARGET_LEAKAGE")
            console.print(f"  [green]✓[/green] incident {urn}")

    _write_audit_examples(report)
    console.print("[dim]structural ≠ statistical: every verdict names the rule and the lineage evidence[/dim]")


def _write_audit_examples(report) -> None:
    from agent.leakage import render_markdown

    EXAMPLES.mkdir(exist_ok=True)
    (EXAMPLES / "leakage-report.md").write_text(render_markdown(report), encoding="utf-8")
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    with (EXAMPLES / "transcript.md").open("a", encoding="utf-8") as fh:
        fh.write(
            f"- {stamp} · leakage audit: {len(report.leaks)} LEAK, "
            f"{len(report.suspects)} SUSPECT of {len(report.verdicts)} features · "
            f"hash {report.audit_hash[:8]}\n"
        )


def _short(urn: str) -> str:
    if urn.startswith("urn:li:dataset:"):
        inner = urn.split(",")
        return inner[1] if len(inner) > 1 else urn
    return urn


if __name__ == "__main__":
    main()
