"""Command-line interface: ``run`` and ``verify-log``. Exit 0 success, 1 refused or failed check."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Annotated

import typer

from trust_decision.chain import verify_log
from trust_decision.pipeline import run as run_pipeline

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)
DEFAULT_SIGNAL_PROCESSING = Path(__file__).resolve().parents[3] / "signal-processing"
ExistingFile = Annotated[Path, typer.Option(exists=True, dir_okay=False)]


@app.command()
def run(
    db: ExistingFile,
    scores: Annotated[Path, typer.Option(exists=True, dir_okay=False, help="real-time manifest")],
    features_params: ExistingFile,
    out: Annotated[Path, typer.Option(file_okay=False)],
    era5_scores: Annotated[
        Path | None, typer.Option(exists=True, dir_okay=False, help="delayed-confirmation manifest")
    ] = None,
    signal_processing: Annotated[Path, typer.Option(file_okay=False)] = DEFAULT_SIGNAL_PROCESSING,
    manifest_sha256: Annotated[
        str | None, typer.Option(help="Optional pin of the real-time manifest itself.")
    ] = None,
) -> None:
    """Apply the decision table to every gate decision and write outputs and the chained log."""
    try:
        summary = run_pipeline(
            db, scores, features_params, out, signal_processing, era5_scores, manifest_sha256
        )
    except (ValueError, KeyError, OSError, RuntimeError, sqlite3.Error) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(summary, indent=2))


@app.command("verify-log")
def verify_log_command(
    log: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
) -> None:
    """Verify a decision log; any edited line makes this exit 1."""
    ok, message = verify_log(log)
    typer.echo(message, err=not ok)
    if not ok:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
