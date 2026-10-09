"""The local BigQuery stand-in: a DuckDB file whose ``raw`` schema is views over the lake's parquet."""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb

from geco.lake import Lake
from geco.raw import existing_tables, raw_key

log = logging.getLogger(__name__)


def connect(path: Path, read_only: bool = False, lake: Lake | None = None) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path), read_only=read_only)
    con.execute("set TimeZone = 'UTC'")  # BigQuery semantics: timestamps are UTC
    if lake is not None and lake.local_path("") is None:
        # Object-store lake (gs://, fake-gcs-server): DuckDB reads it through the same fsspec
        # filesystem, so the raw gate also runs on Cloud Run against real GCS.
        con.register_filesystem(lake.fs)
    return con


def register_raw(lake: Lake, path: Path) -> list[str]:
    """(Re)create raw.<table> views. Equivalent to the BigQuery external tables in Terraform."""
    tables = existing_tables(lake)
    with connect(path, lake=lake) as con:
        con.execute("create schema if not exists raw")
        for table in tables:
            local = lake.local_path(raw_key(table))
            location = Path(local).as_posix() if local is not None else lake.url(raw_key(table))
            escaped = location.replace("'", "''")
            con.execute(f"create or replace view raw.{table} as select * from read_parquet('{escaped}')")
    log.info("registered %d raw views in %s", len(tables), path)
    return tables


def query(path: Path, sql: str) -> list[dict]:
    with connect(path, read_only=True) as con:
        cursor = con.execute(sql)
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
