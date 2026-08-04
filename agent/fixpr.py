"""Draft a validated fix PR for the schema change.

Flow: figure out which dbt models read the changed table directly -> patch
them (LLM when configured, deterministic fallback otherwise) -> validate with
a real `dbt build` of the patched models + everything downstream, against the
live (broken) warehouse -> only on green, push a branch and open the PR.

The patch is applied in a temporary git worktree so the developer's working
tree is never touched, and the agent only ever writes files under
demo/dbt/models/ (enforced, not assumed).

Deterministic fallback: a rename is fixed by re-selecting the new column
under the old alias; a float->integer narrowing alongside a rename is treated
as a unit change (dollars -> cents) and divides by 100.0. That rule is
derived from the schema diff itself, so the demo works without any LLM.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from pydantic import BaseModel

from agent.adapters import github
from agent.models import BlastRadius, ChangeEvent

REPO_ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = Path("demo") / "dbt" / "models"
FLOATY = ("DOUBLE", "FLOAT", "REAL", "DECIMAL", "NUMERIC")
INTY = ("BIGINT", "INT", "INTEGER", "SMALLINT", "HUGEINT")


class FixResult(BaseModel):
    status: str  # "opened" | "validation-failed" | "skipped" | "error"
    pr_url: str | None = None
    branch: str | None = None
    detail: str = ""
    patch: str = ""


def _run(cmd: list[str], cwd: Path, env: dict | None = None) -> tuple[int, str]:
    import os

    proc = subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True,
        env={**os.environ, **(env or {})}, timeout=600,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _table_name(entity_urn: str) -> str:
    # urn:li:dataset:(urn:li:dataPlatform:duckdb,warehouse.main.raw_transactions,PROD)
    return entity_urn.split(",")[1].split(".")[-1]


def affected_model_files(root: Path, table: str, columns: list[str]) -> list[Path]:
    """dbt model files that read the changed table directly and use a changed column."""
    hits = []
    source_re = re.compile(rf"source\(\s*['\"]\w+['\"]\s*,\s*['\"]{re.escape(table)}['\"]")
    for path in sorted((root / MODELS_DIR).rglob("*.sql")):
        text = path.read_text(encoding="utf-8")
        if source_re.search(text) and any(re.search(rf"\b{re.escape(c)}\b", text) for c in columns):
            hits.append(path)
    return hits


def deterministic_patch(text: str, change: ChangeEvent) -> str:
    """Re-select renamed columns under their old alias, converting units when
    the type narrowed float -> integer (the cents case)."""
    for col in change.columns:
        if not (col.before and col.after):
            continue
        narrowed = (col.type_before or "").upper().startswith(FLOATY) and (
            col.type_after or ""
        ).upper().startswith(INTY)
        expr = f"{col.after} / 100.0" if narrowed else col.after
        out_lines = []
        item_re = re.compile(rf"^(\s*)((?:\w+\.)?){re.escape(col.before)}(\s*,?\s*)$")
        inline_re = re.compile(rf"\b((?:\w+\.)?){re.escape(col.before)}\b")
        for line in text.splitlines(keepends=False):
            m = item_re.match(line)
            if m:  # bare select-list item: keep the downstream-facing alias
                indent, qual, tail = m.groups()
                qual_expr = f"{qual}{col.after} / 100.0" if narrowed else f"{qual}{col.after}"
                out_lines.append(f"{indent}{qual_expr} as {col.before}{tail.rstrip()} ".rstrip())
            else:
                def repl(m: re.Match, _narrowed=narrowed, _expr=expr, _after=col.after) -> str:
                    return f"({m.group(1)}{_expr})" if _narrowed else f"{m.group(1)}{_after}"

                out_lines.append(inline_re.sub(repl, line))
        text = "\n".join(out_lines) + "\n"
    return text


def llm_patch(files: dict[str, str], change: ChangeEvent, error: str | None) -> dict[str, str] | None:
    """Ask the LLM for corrected file contents; None when no provider."""
    import json

    from agent.adapters import llm

    prompt_path = REPO_ROOT / "prompts" / "fix_pr.md"
    prompt = prompt_path.read_text(encoding="utf-8").format(
        change=json.dumps(change.model_dump(mode="json"), indent=1),
        files="\n\n".join(f"### {name}\n```sql\n{text}\n```" for name, text in files.items()),
        error=error or "none",
    )
    raw = llm.complete(prompt, max_tokens=2000)
    if not raw:
        return None
    patched = {}
    for name in files:
        m = re.search(rf"### {re.escape(name)}\s*```sql\n(.*?)```", raw, re.S)
        if m:
            patched[name] = m.group(1)
    return patched if len(patched) == len(files) else None


def _validate(worktree: Path, model_names: list[str]) -> tuple[bool, str]:
    warehouse = (REPO_ROOT / "demo" / "warehouse.duckdb").resolve()
    selectors = [f"{m}+" for m in model_names]
    code, out = _run(
        ["uv", "run", "dbt", "build", "--select", *selectors,
         "--project-dir", str(worktree / "demo" / "dbt"),
         "--profiles-dir", str(worktree / "demo" / "dbt")],
        cwd=REPO_ROOT,
        env={"BR_WAREHOUSE_PATH": str(warehouse)},
    )
    return code == 0, out[-2000:]


def generate_and_open_pr(change: ChangeEvent, radius: BlastRadius, evidence: dict) -> FixResult:
    import os

    if not (os.environ.get("GITHUB_TOKEN") and github.repo()):
        return FixResult(status="skipped", detail="GITHUB_TOKEN / GITHUB_REPO not configured")

    table = _table_name(change.entity_urn)
    columns = [c.before for c in change.columns if c.before]
    files = affected_model_files(REPO_ROOT, table, columns)
    if not files:
        return FixResult(status="skipped", detail=f"no dbt models read {table} directly")

    ehash8 = evidence["evidence_hash"][:8]
    branch = f"blast-radius/fix-{ehash8}"
    worktree = Path(tempfile.mkdtemp(prefix="br-fix-")) / "wt"

    try:
        _run(["git", "worktree", "remove", "--force", str(worktree)], cwd=REPO_ROOT)
        _run(["git", "branch", "-D", branch], cwd=REPO_ROOT)
        code, out = _run(["git", "worktree", "add", "-b", branch, str(worktree), "HEAD"], cwd=REPO_ROOT)
        if code != 0:
            return FixResult(status="error", detail=f"worktree: {out[-300:]}")

        rel_files = [f.relative_to(REPO_ROOT) for f in files]
        originals = {str(rf): (worktree / rf).read_text(encoding="utf-8") for rf in rel_files}
        model_names = [rf.stem for rf in rel_files]

        error: str | None = None
        used_llm = False
        for attempt in range(3):
            patched = llm_patch(originals, change, error) if attempt < 2 else None
            if patched:
                used_llm = True
            else:
                patched = {name: deterministic_patch(text, change) for name, text in originals.items()}

            for name, text in patched.items():
                target = (worktree / name).resolve()
                if MODELS_DIR.as_posix() not in target.as_posix():
                    return FixResult(status="error", detail=f"refusing to write outside models dir: {name}")
                target.write_text(text, encoding="utf-8", newline="\n")

            ok, log = _validate(worktree, model_names)
            if ok:
                break
            error = log
            if not used_llm:  # deterministic path has no retry ladder
                return FixResult(status="validation-failed", detail=log[-500:])
        else:
            return FixResult(status="validation-failed", detail=(error or "")[-500:])

        code, diff = _run(["git", "diff"], cwd=worktree)
        _run(["git", "add", "-A"], cwd=worktree)
        _run(["git", "-c", "core.safecrlf=false", "commit", "-q", "-m",
              f"fix: adapt features to {table} schema v2\n\nEvidence: {evidence['evidence_hash']}"],
             cwd=worktree)
        push_url = f"https://x-access-token:{os.environ['GITHUB_TOKEN']}@github.com/{github.repo()}.git"
        code, out = _run(["git", "push", "-f", push_url, f"{branch}:{branch}"], cwd=worktree)
        if code != 0:
            return FixResult(status="error", detail=f"push failed: {out[-300:]}", patch=diff)

        title = f"fix: adapt features to {table} schema v2"
        body = (
            f"Automated fix by **Blast Radius** for a {evidence['severity']} {evidence['verdict']} "
            f"schema change on `{table}`.\n\n"
            f"- Patched models: {', '.join(f'`{m}`' for m in model_names)}\n"
            f"- Validated with `dbt build --select {' '.join(f'{m}+' for m in model_names)}` — **green** "
            f"against the post-migration warehouse\n"
            f"- Patch generator: {'LLM (' + os.environ.get('LLM_MODEL', '') + ')' if used_llm else 'deterministic (schema-diff derived)'}\n"
            f"- Evidence hash: `{evidence['evidence_hash']}`\n"
        )
        pr_url = github.open_pull_request(head=branch, base="main", title=title, body=body)
        return FixResult(status="opened", pr_url=pr_url, branch=branch, patch=diff,
                         detail="validated + opened")
    except Exception as exc:  # the fix PR is best-effort; the incident is the record
        return FixResult(status="error", detail=str(exc)[:300])
    finally:
        _run(["git", "worktree", "remove", "--force", str(worktree)], cwd=REPO_ROOT)
        shutil.rmtree(worktree.parent, ignore_errors=True)
