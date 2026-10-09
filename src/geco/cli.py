"""`geco` command line. Every stage is idempotent; `geco run` chains them."""

from __future__ import annotations

import json
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Annotated

import typer

from geco import logs
from geco.config import Settings
from geco.pipeline import SOURCES, Pipeline, StageError, run_dbt
from geco.quality.checks import has_errors, run_checks

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)
log = logging.getLogger("geco")

SourceOpt = Annotated[
    list[str] | None,
    typer.Option("--source", "-s", help=f"Limit to these sources ({', '.join(SOURCES)}). Repeatable."),
]


def _pipeline() -> Pipeline:
    settings = Settings()
    logs.setup(settings.log_format)
    return Pipeline(settings)


def _fail(exc: StageError) -> None:
    log.error("%s", exc)
    raise typer.Exit(exc.exit_code)


@app.command()
def extract(
    source: SourceOpt = None,
    force_osm: Annotated[bool, typer.Option(help="Re-count OSM even if counted recently.")] = False,
) -> None:
    """Fetch sources into bronze (content-addressed) and publish trends events to Pub/Sub."""
    pipe = _pipeline()
    entries = pipe.extract(source, force_osm=force_osm)
    statuses = Counter(f"{e.source}:{e.status}" for e in entries)
    typer.echo(json.dumps(dict(sorted(statuses.items())), indent=2))


@app.command("consume-events")
def consume_events() -> None:
    """Drain the trends-events subscription into bronze."""
    entry = _pipeline().consume_trends_events()
    typer.echo(json.dumps(entry.detail if entry else {"messages": 0}))


@app.command()
def load(source: SourceOpt = None) -> None:
    """Bronze -> raw parquet -> DuckDB raw views, then the raw data-quality gate."""
    pipe = _pipeline()
    try:
        counts, _ = pipe.load(source)
    except StageError as exc:
        _fail(exc)
    typer.echo(json.dumps(counts, indent=2))


@app.command()
def transform(
    select: Annotated[str | None, typer.Option(help="dbt --select expression.")] = None,
) -> None:
    """dbt build (models + tests) on the warehouse, then the marts data-quality gate."""
    pipe = _pipeline()
    try:
        pipe.transform(["--select", select] if select else None)
    except StageError as exc:
        _fail(exc)


@app.command()
def dq(layer: Annotated[str, typer.Option(help="raw or marts")] = "raw") -> None:
    """Run the data-quality checks for one layer (exit 3 on error-level failures)."""
    pipe = _pipeline()
    results = run_checks(pipe.db_path, layer, pipe.now, pipe.lake, pipe.run_id)  # type: ignore[arg-type]
    failed = [r for r in results if not r.passed]
    typer.echo(f"{len(results)} checks, {len(failed)} failed")
    if has_errors(results):
        raise typer.Exit(3)


@app.command()
def dbt(args: Annotated[list[str], typer.Argument(help="Arguments passed to dbt.")]) -> None:
    """Run any dbt command with the pipeline's environment (e.g. `geco dbt -- docs generate`)."""
    pipe = _pipeline()
    try:
        run_dbt(pipe.settings, args)
    except StageError as exc:
        _fail(exc)


@app.command()
def report(out: Annotated[Path, typer.Option(help="Output directory.")] = Path("site")) -> None:
    """Write the static results site (index.html + summary.json + CSVs)."""
    from geco.report import build_site

    pipe = _pipeline()
    summary = build_site(pipe.db_path, out, pipe.settings)
    typer.echo(json.dumps(summary["headline"], indent=2, default=str))


@app.command()
def prune(keep: Annotated[int, typer.Option(help="Versions to keep per snapshot dataset.")] = 3) -> None:
    """Bound bronze history of the snapshot sources (political ads, Netflix)."""
    deleted = _pipeline().prune(keep)
    typer.echo(json.dumps({"deleted": len(deleted)}))


@app.command("poll-trends")
def poll_trends() -> None:
    """Lightweight scheduled step: poll Trends, publish events, consume them into bronze."""
    pipe = _pipeline()
    pipe.extract(["trends"])
    pipe.consume_trends_events()


@app.command()
def run(
    source: SourceOpt = None,
    skip_extract: Annotated[bool, typer.Option(help="Rebuild from bronze only (no network).")] = False,
    site: Annotated[Path | None, typer.Option(help="Also write the results site here.")] = None,
    force_osm: bool = False,
) -> None:
    """extract -> consume events -> load (+DQ) -> dbt build (+DQ) [-> report]."""
    pipe = _pipeline()
    try:
        if not skip_extract:
            pipe.extract(source, force_osm=force_osm)
        pipe.consume_trends_events()
        pipe.load()
        pipe.transform()
    except StageError as exc:
        _fail(exc)
    if site is not None:
        from geco.report import build_site

        build_site(pipe.db_path, site, pipe.settings)
    log.info("run %s complete", pipe.run_id)


def run_fixture_pipeline(data_dir: Path, fixtures_dir: Path, site: Path | None = None) -> Pipeline:
    """Three Trends polls 3 h apart + every other source once, all from committed fixtures."""
    import os

    from geco.fixtures import FIXTURE_POLL_TIMES, fixture_env

    pipe: Pipeline | None = None
    for poll in range(1, len(FIXTURE_POLL_TIMES) + 1):
        os.environ.update(fixture_env(fixtures_dir, data_dir, poll))
        settings = Settings()
        pipe = Pipeline(settings)
        pipe.extract(None if poll == 1 else ["trends"], force_osm=poll == 1)
        pipe.consume_trends_events()
    if pipe is None:  # pragma: no cover - FIXTURE_POLL_TIMES is never empty
        raise RuntimeError("no fixture polls")
    pipe.load()
    pipe.transform()
    if site is not None:
        from geco.report import build_site

        build_site(pipe.db_path, site, pipe.settings)
    return pipe


@app.command("fixtures-run")
def fixtures_run(
    data_dir: Annotated[Path, typer.Option(help="Throwaway data directory.")] = Path("data/fixtures"),
    fixtures_dir: Annotated[Path, typer.Option(help="Fixture root.")] = Path("tests/fixtures"),
    site: Annotated[Path | None, typer.Option(help="Also write the results site here.")] = None,
) -> None:
    """Run the whole pipeline offline on the committed fixtures (what CI runs)."""
    logs.setup(Settings().log_format)
    try:
        pipe = run_fixture_pipeline(data_dir.resolve(), fixtures_dir.resolve(), site)
    except StageError as exc:
        _fail(exc)
    log.info("fixture run complete: %s", pipe.db_path)


@app.command()
def sql(query: str) -> None:
    """Ad-hoc read-only SQL against the warehouse."""
    from geco.warehouse import query as run_query

    pipe = _pipeline()
    rows = run_query(pipe.db_path, query)
    for row in rows:
        typer.echo(json.dumps(row, default=str, ensure_ascii=False))


@app.command()
def status() -> None:
    """Latest manifest entry per source/dataset/key."""
    pipe = _pipeline()
    latest: dict[tuple[str, str, str], dict] = {}
    for e in pipe.manifest.entries():
        latest[(e.source, e.dataset, e.key)] = {
            "fetched_at": e.fetched_at,
            "status": e.status,
            "bytes": e.bytes,
        }
    for key, value in sorted(latest.items()):
        typer.echo(f"{'/'.join(key):60s} {value['status']:13s} {value['fetched_at']}")


def main() -> None:  # pragma: no cover
    sys.exit(app())


if __name__ == "__main__":  # pragma: no cover
    main()
