"""Bronze -> raw: parse every source's bronze objects into typed parquet tables in the lake.

Raw tables are rebuilt deterministically from bronze + manifest on every run (sorted rows, fixed
schemas), so re-running never duplicates data. In BigQuery the same parquet files are exposed as
external tables in the ``raw`` dataset (see infra/modules/bigquery).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import polars as pl

from geco.lake import Lake, Manifest
from geco.sources import netflix, osm, political_ads, trends, youtube

log = logging.getLogger(__name__)

BUILDERS: dict[str, Callable[[Lake, Manifest], dict[str, pl.DataFrame]]] = {
    "trends": trends.build_raw,
    "political_ads": political_ads.build_raw,
    "netflix": netflix.build_raw,
    "osm": osm.build_raw,
    "youtube": youtube.build_raw,
}

EXPECTED_TABLES: dict[str, list[str]] = {
    "trends": ["trends_polls", "trends_items", "trends_events"],
    "political_ads": [
        "ads_report_updated",
        "ads_advertisers",
        "ads_advertiser_spend_totals",
        "ads_weekly_spend",
        "ads_advertiser_geo_spend",
        "ads_geo_spend",
    ],
    "netflix": ["netflix_global_weekly", "netflix_country_weekly", "netflix_most_popular"],
    "osm": ["osm_amenity_counts"],
    "youtube": ["youtube_videos", "youtube_channels"],
}
ALL_TABLES = [t for tables in EXPECTED_TABLES.values() for t in tables]


def raw_key(table: str) -> str:
    return f"raw/{table}/data.parquet"


def build(lake: Lake, sources: list[str] | None = None) -> dict[str, int]:
    manifest = Manifest(lake)
    counts: dict[str, int] = {}
    for source in list(BUILDERS) if sources is None else sources:
        tables = BUILDERS[source](lake, manifest)
        for table, frame in tables.items():
            lake.write_parquet(raw_key(table), frame)
            counts[table] = frame.height
            log.info("raw.%s: %d rows", table, frame.height)
    return counts


def existing_tables(lake: Lake) -> list[str]:
    return [t for t in ALL_TABLES if lake.exists(raw_key(t))]
