"""Stages of the pipeline. Each maps to one Cloud Run job step in infra/workflows/pipeline.yaml."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from geco import raw, warehouse
from geco.config import Settings
from geco.http import HttpClient
from geco.lake import Lake, Manifest, ManifestEntry
from geco.pubsub import get_pubsub
from geco.quality.checks import CheckResult, has_errors, run_checks
from geco.sources import netflix, osm, political_ads, trends, youtube
from geco.sources.base import ExtractContext

log = logging.getLogger(__name__)

SOURCES = ["trends", "political_ads", "netflix", "osm", "youtube"]
PRUNABLE_SOURCES = ["political_ads", "netflix"]

EXIT_OK = 0
EXIT_DQ_FAILED = 3
EXIT_DBT_FAILED = 4


class StageError(RuntimeError):
    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def run_id_for(now: datetime) -> str:
    return now.strftime("%Y%m%dT%H%M%SZ")


@dataclass
class Pipeline:
    settings: Settings
    lake: Lake = field(init=False)
    manifest: Manifest = field(init=False)
    now: datetime = field(init=False)
    run_id: str = field(init=False)

    def __post_init__(self) -> None:
        self.lake = Lake(self.settings.resolved_lake_uri)
        self.manifest = Manifest(self.lake)
        self.now = self.settings.now()
        self.run_id = run_id_for(self.now)

    @property
    def db_path(self) -> Path:
        return self.settings.resolved_warehouse_path

    def context(self) -> ExtractContext:
        return ExtractContext(
            settings=self.settings,
            lake=self.lake,
            manifest=self.manifest,
            http=HttpClient(self.settings),
            run_id=self.run_id,
            now=self.now,
        )

    # ------------------------------------------------------------------ extract (bronze)
    def extract(self, sources: list[str] | None = None, *, force_osm: bool = False) -> list[ManifestEntry]:
        ctx = self.context()
        entries: list[ManifestEntry] = []
        for source in sources or SOURCES:
            started = time.monotonic()
            if source == "trends":
                batch = trends.extract(ctx, get_pubsub(self.settings, self.lake))
            elif source == "political_ads":
                batch = political_ads.extract(ctx)
            elif source == "netflix":
                batch = netflix.extract(ctx)
            elif source == "osm":
                batch = osm.extract(ctx, force=force_osm)
            elif source == "youtube":
                batch = youtube.extract(ctx)
            else:
                raise ValueError(f"unknown source {source!r}; choose from {SOURCES}")
            # Write per source so a later failure does not lose earlier fetches.
            self.manifest.write_run(self.run_id, batch)
            entries.extend(batch)
            log.info("extract %s: %d entries in %.1fs", source, len(batch), time.monotonic() - started)
        return entries

    def prune(self, keep: int = 3) -> list[str]:
        """Delete bronze objects of snapshot sources older than the `keep` latest versions.

        Political ads and Netflix republish whole files (the ads bundle daily), so their history is
        bounded; poll sources (trends, youtube, osm) are tiny and kept forever because churn and
        cadence are computed from every poll. On GCS the bucket lifecycle rules do the same job.
        """
        deleted: list[str] = []
        for source in PRUNABLE_SOURCES:
            # "bundle" only records the ads zip's ETag; it points at a member object.
            datasets = {e.dataset for e in self.manifest.ok_entries(source)} - {"bundle"}
            for dataset in sorted(datasets):
                entries = self.manifest.ok_entries(source, dataset)
                ordered: list[str] = []
                for e in reversed(entries):
                    if e.bronze_key not in ordered:
                        ordered.append(e.bronze_key)
                keep_keys = set(ordered[:keep])
                for key in ordered[keep:]:
                    if key not in keep_keys and self.lake.exists(key):
                        self.lake.delete(key)
                        deleted.append(key)
        self.manifest._exists_cache.clear()
        log.info("prune: deleted %d bronze objects", len(deleted))
        return deleted

    def consume_trends_events(self) -> ManifestEntry | None:
        ctx = self.context()
        entry = trends.consume_events(ctx, get_pubsub(self.settings, self.lake))
        if entry is not None:
            self.manifest.write_run(self.run_id, [entry])
        return entry

    # ------------------------------------------------------------------ load (raw + DQ gate)
    def load(self, sources: list[str] | None = None) -> tuple[dict[str, int], list[CheckResult]]:
        counts = raw.build(self.lake, sources)
        warehouse.register_raw(self.lake, self.db_path)
        results = run_checks(self.db_path, "raw", self.now, self.lake, self.run_id)
        if has_errors(results) and self.settings.dq_fail_on_error:
            failed = [r.name for r in results if not r.passed and r.severity == "error"]
            raise StageError(f"raw data-quality gate failed: {failed}", EXIT_DQ_FAILED)
        return counts, results

    # ------------------------------------------------------------------ transform (dbt + DQ gate)
    def transform(self, dbt_args: list[str] | None = None) -> list[CheckResult]:
        run_dbt(self.settings, ["build", *(dbt_args or [])])
        if os.environ.get("DBT_TARGET", "local") != "local":
            # On BigQuery the dbt tests are the marts gate; these checks are DuckDB SQL.
            log.info("DBT_TARGET=%s: marts DQ checks run as dbt tests only", os.environ["DBT_TARGET"])
            return []
        results = run_checks(self.db_path, "marts", self.now, self.lake, self.run_id)
        if has_errors(results) and self.settings.dq_fail_on_error:
            failed = [r.name for r in results if not r.passed and r.severity == "error"]
            raise StageError(f"marts data-quality gate failed: {failed}", EXIT_DQ_FAILED)
        return results


def dbt_env(settings: Settings) -> dict[str, str]:
    return {
        "GECO_DUCKDB_PATH": str(settings.resolved_warehouse_path.resolve()),
        "GECO_AS_OF": settings.now().isoformat(),
    }


def run_dbt(settings: Settings, args: list[str]) -> None:
    from dbt.cli.main import dbtRunner

    project = settings.dbt_project_dir.resolve()
    os.environ.update(dbt_env(settings))
    os.environ.setdefault("DBT_SEND_ANONYMOUS_USAGE_STATS", "false")
    full = [*args, "--project-dir", str(project), "--profiles-dir", str(project)]
    if "--target" not in args:
        full += ["--target", os.environ.get("DBT_TARGET", "local")]
    log.info("dbt %s", " ".join(full))
    result = dbtRunner().invoke(full)
    if not result.success:
        raise StageError(f"dbt {args[0]} failed: {result.exception or 'see dbt output'}", EXIT_DBT_FAILED)
