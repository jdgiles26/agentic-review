"""CLI entry point – `review` command."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from graphs.review_graph import run_review
from schemas.models import ReviewCategory, ReviewRequest, ReviewResult

app = typer.Typer(
    name="review",
    help="Agentic code & PR review CLI",
    add_completion=False,
)
console = Console()

FocusOption = typer.Option(
    None,
    "--focus",
    "-f",
    help=f"Focus categories: {', '.join(c.value for c in ReviewCategory)}",
)
MaxFindingsOption = typer.Option(50, "--max-findings", min=1, max=200)


def _parse_focus(focus: Optional[list[str]]) -> list[ReviewCategory] | None:
    if not focus:
        return None
    try:
        return [ReviewCategory(c) for c in focus]
    except ValueError as e:
        raise typer.BadParameter(str(e), param_hint="--focus") from e


def _emit(result: ReviewResult, json_out: bool, fail_on_changes: bool) -> None:
    if json_out:
        typer.echo(result.model_dump_json(indent=2))
    else:
        _print_result(result)
    if fail_on_changes and result.recommendation == "request_changes":
        raise typer.Exit(2)


@app.command("diff")
def review_diff(
    path: Path = typer.Argument(..., help="Path to a unified diff file or '-' for stdin"),
    focus: Optional[list[str]] = FocusOption,
    max_findings: int = MaxFindingsOption,
    json_out: bool = typer.Option(False, "--json", help="Emit JSON"),
    fail_on_changes: bool = typer.Option(
        False, "--fail-on-changes", help="Exit 2 when the recommendation is request_changes"
    ),
):
    """Review a local diff file."""
    if str(path) == "-":
        diff = sys.stdin.read()
    elif not path.is_file():
        raise typer.BadParameter(f"{path} is not a file", param_hint="PATH")
    else:
        diff = path.read_text(encoding="utf-8")

    req = ReviewRequest(diff=diff, focus=_parse_focus(focus), max_findings=max_findings)
    _emit(run_review(req), json_out, fail_on_changes)


@app.command("pr")
def review_pr(
    owner: str = typer.Argument(...),
    repo: str = typer.Argument(...),
    number: int = typer.Argument(...),
    focus: Optional[list[str]] = FocusOption,
    max_findings: int = MaxFindingsOption,
    json_out: bool = typer.Option(False, "--json"),
    fail_on_changes: bool = typer.Option(False, "--fail-on-changes"),
):
    """Review a GitHub pull request (requires GITHUB_TOKEN)."""
    from tools.github.client import GitHubClient
    from tools.github.pr_context import build_pr_review_request

    client = GitHubClient()
    if not client.available:
        console.print("[red]GITHUB_TOKEN not set[/red]")
        raise typer.Exit(1)

    try:
        req = build_pr_review_request(client, owner, repo, number, focus=_parse_focus(focus))
    except typer.BadParameter:
        raise
    except Exception as e:
        console.print(f"[red]GitHub error:[/red] {e}")
        raise typer.Exit(1) from e
    req.max_findings = max_findings
    _emit(run_review(req), json_out, fail_on_changes)


@app.command("history")
def history(limit: int = typer.Option(10, "--limit", min=1, max=200)):
    """List recently stored reviews."""
    from memory.store import list_reviews

    rows = list_reviews(limit=limit)
    if not rows:
        console.print("No stored reviews")
        return
    table = Table(title="Recent reviews")
    for col in ("Id", "Recommendation", "Score", "Findings", "Summary"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            r["request_id"][:8],
            r["recommendation"] or "-",
            "-" if r["score"] is None else str(r["score"]),
            str(r["findings"]),
            (r["summary"] or "")[:60],
        )
    console.print(table)


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    reload: bool = typer.Option(False, "--reload"),
):
    """Start the FastAPI server."""
    import uvicorn

    uvicorn.run("api.main:app", host=host, port=port, reload=reload)


def _print_result(result: ReviewResult) -> None:
    console.print(
        Panel(
            f"[bold]{result.summary}[/bold]\n\n"
            f"Recommendation: [cyan]{result.recommendation}[/cyan]  "
            f"Score: {result.score}  "
            f"Status: {result.status}  "
            f"Duration: {result.duration_ms} ms",
            title=f"Review {result.request_id[:8]}",
        )
    )
    for action in result.actions:
        console.print(f"[yellow]PR action[/yellow] {action.action}: {action.body or action.labels}")
    if not result.findings:
        console.print("[green]No findings[/green]")
        return

    table = Table(title="Findings")
    table.add_column("Sev")
    table.add_column("Category")
    table.add_column("Title")
    table.add_column("Location")
    for f in result.findings:
        where = f.file_path or "-"
        if f.file_path and f.line_start:
            where = f"{f.file_path}:{f.line_start}"
        table.add_row(f.severity.value, f.category.value, f.title, where)
    console.print(table)


if __name__ == "__main__":
    app()
