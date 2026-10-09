"""Data-quality gates between layers.

Each check is a SQL query that returns *violating* rows. ``error`` failures stop the pipeline
(exit code 3) before the next layer is built; ``warn`` failures are reported only. Results are
written to ``ops/dq/<run_id>-<layer>.json`` in the lake.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import duckdb

from geco.lake import Lake
from geco.raw import ALL_TABLES
from geco.warehouse import connect

log = logging.getLogger(__name__)

Severity = Literal["error", "warn"]


@dataclass(frozen=True)
class Check:
    name: str
    layer: Literal["raw", "marts"]
    severity: Severity
    sql: str
    description: str


@dataclass
class CheckResult:
    name: str
    layer: str
    severity: str
    passed: bool
    violations: int
    description: str
    sample: list[dict]
    error: str | None = None


def _raw_checks(now: datetime) -> list[Check]:
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    return [
        Check(
            "trends_items_term_present",
            "raw",
            "error",
            "select * from raw.trends_items where term is null or trim(term) = ''",
            "Every trending item has a search term.",
        ),
        Check(
            "trends_items_rank_unique",
            "raw",
            "error",
            "select geo, polled_at, rank, count(*) n from raw.trends_items group by all having count(*) > 1",
            "One item per (geo, poll, rank).",
        ),
        Check(
            "trends_items_traffic_parsed",
            "raw",
            "warn",
            "select * from raw.trends_items where approx_traffic is not null and approx_traffic_min is null",
            "Every approx_traffic string ('200+', '2K+') parsed to a number.",
        ),
        Check(
            "trends_polls_not_empty",
            "raw",
            "warn",
            "select * from raw.trends_polls where item_count = 0",
            "A poll returned no items.",
        ),
        Check(
            "trends_fresh",
            "raw",
            "warn",
            f"select max(polled_at) as last_polled_at from raw.trends_polls having max(polled_at) < timestamptz '{ts}+00' - interval 1 day",
            "Trends were polled within the last day.",
        ),
        Check(
            "trends_events_unique",
            "raw",
            "error",
            "select event_id, count(*) n from raw.trends_events group by 1 having count(*) > 1",
            "Pub/Sub redeliveries collapse to one row per event_id.",
        ),
        Check(
            "ads_advertisers_unique",
            "raw",
            "error",
            "select advertiser_id, count(*) n from raw.ads_advertisers group by 1 having count(*) > 1",
            "One row per advertiser.",
        ),
        Check(
            "ads_weekly_grain",
            "raw",
            "error",
            "select advertiser_id, week_start_date, currency, count(*) n from raw.ads_weekly_spend group by all having count(*) > 1",
            "One row per (advertiser, week, currency).",
        ),
        Check(
            "ads_weeks_start_sunday",
            "raw",
            "error",
            "select * from raw.ads_weekly_spend where dayofweek(week_start_date) <> 0",
            "Google's spend weeks run Sunday to Saturday.",
        ),
        Check(
            "ads_spend_parsed",
            "raw",
            "error",
            """select 'weekly' as file, advertiser_id, currency from raw.ads_weekly_spend where spend is null
               union all
               select 'totals', advertiser_id, currency from raw.ads_advertiser_spend_totals where spend is null""",
            "Every published spend value parsed as a number (none silently dropped).",
        ),
        Check(
            "ads_spend_non_negative",
            "raw",
            "error",
            "select * from raw.ads_weekly_spend where spend < 0",
            "Spend is never negative.",
        ),
        Check(
            "ads_weekly_advertisers_known",
            "raw",
            "warn",
            "select distinct w.advertiser_id from raw.ads_weekly_spend w anti join raw.ads_advertisers a using (advertiser_id)",
            "Every advertiser in the weekly file is in advertiser-stats.",
        ),
        Check(
            "ads_weekly_reconciles_with_totals",
            "raw",
            "warn",
            """with w as (select currency, sum(spend) s from raw.ads_weekly_spend group by 1),
                     t as (select currency, sum(spend) s from raw.ads_advertiser_spend_totals group by 1)
                 select w.currency, w.s weekly, t.s totals from w join t using (currency)
                 where abs(w.s - t.s) > 0.02 * t.s""",
            "Weekly spend sums to within 2% of the advertiser totals (both are published rounded).",
        ),
        Check(
            "ads_report_fresh",
            "raw",
            "warn",
            f"select * from raw.ads_report_updated where report_data_updated_at_pt < timestamp '{ts}' - interval 8 day",
            "The transparency report was refreshed within the last week.",
        ),
        Check(
            "netflix_global_grain",
            "raw",
            "error",
            "select week_end_date, category, weekly_rank, count(*) n from raw.netflix_global_weekly group by all having count(*) > 1",
            "One title per (week, category, rank).",
        ),
        Check(
            "netflix_rank_range",
            "raw",
            "error",
            "select * from raw.netflix_global_weekly where weekly_rank not between 1 and 10",
            "Ranks are 1-10.",
        ),
        Check(
            "netflix_weeks_end_sunday",
            "raw",
            "error",
            "select distinct week_end_date from raw.netflix_global_weekly where dayofweek(week_end_date) <> 0",
            "Netflix weeks run Monday to Sunday and are labelled by the Sunday.",
        ),
        Check(
            "netflix_categories_known",
            "raw",
            "error",
            """select distinct category from raw.netflix_global_weekly where category not in
                 ('Films (English)', 'Films (Non-English)', 'TV (English)', 'TV (Non-English)')""",
            "Only the four published global lists.",
        ),
        Check(
            "netflix_full_lists",
            "raw",
            "warn",
            "select week_end_date, category, count(*) n from raw.netflix_global_weekly group by all having count(*) <> 10",
            "Each weekly list has 10 titles.",
        ),
        Check(
            "netflix_hours_present",
            "raw",
            "error",
            "select * from raw.netflix_global_weekly where weekly_hours_viewed is null or weekly_hours_viewed <= 0",
            "Every global entry has hours viewed.",
        ),
        Check(
            "osm_total_adds_up",
            "raw",
            "error",
            "select * from raw.osm_amenity_counts where total <> nodes + ways + relations or total < 0",
            "Overpass totals equal nodes + ways + relations.",
        ),
        Check(
            "osm_box_area",
            "raw",
            "error",
            "select distinct city, area_km2 from raw.osm_amenity_counts where area_km2 not between 1 and 10000",
            "Box areas are plausible.",
        ),
        Check(
            "osm_fresh",
            "raw",
            "warn",
            f"select city, max(fetched_at) as last_fetched_at from raw.osm_amenity_counts group by 1 having max(fetched_at) < timestamptz '{ts}+00' - interval 21 day",
            "Each city was counted within the last 3 weeks (Overpass is often busy; stale counts are kept).",
        ),
        Check(
            "youtube_video_keys",
            "raw",
            "error",
            "select * from raw.youtube_videos where video_id is null or channel_id is null or published_at is null",
            "Videos have ids and publish times.",
        ),
    ]


def _mart_checks() -> list[Check]:
    return [
        Check(
            "marts_trends_churn_bounds",
            "marts",
            "error",
            "select * from marts.mart_trends_churn_by_geo where churn_rate < 0 or churn_rate > 1",
            "Churn is a share.",
        ),
        Check(
            "marts_ads_spend_reconciles",
            "marts",
            "error",
            """with m as (select currency, sum(spend) s from marts.fct_political_ad_spend_weekly group by 1),
                     r as (select currency, sum(spend) s from raw.ads_weekly_spend group by 1)
                 select * from m full join r using (currency) where m.s is distinct from r.s""",
            "The weekly spend fact carries every raw dollar exactly once.",
        ),
        Check(
            "marts_netflix_language_share_sums",
            "marts",
            "error",
            """select week_end_date, sum(share_of_hours) s from marts.mart_netflix_language_mix_weekly
                 group by 1 having abs(sum(share_of_hours) - 1) > 1e-6""",
            "Language shares of hours sum to 1 per week.",
        ),
        Check(
            "marts_osm_density_positive",
            "marts",
            "error",
            "select * from marts.mart_osm_amenity_density where per_km2 < 0",
            "Densities are non-negative.",
        ),
    ]


def run_checks(
    db_path: Path,
    layer: Literal["raw", "marts"],
    now: datetime,
    lake: Lake | None = None,
    run_id: str | None = None,
) -> list[CheckResult]:
    checks = _raw_checks(now) if layer == "raw" else _mart_checks()
    results: list[CheckResult] = []
    # Read-write handle: dbt-duckdb keeps its in-process connection open after `dbt build`, and
    # DuckDB only shares a database between handles opened with the same configuration.
    with connect(db_path, lake=lake) as con:
        present = {
            r[0]
            for r in con.execute(
                "select table_name from information_schema.tables where table_schema = 'raw'"
            ).fetchall()
        }
        if layer == "raw":
            missing = [t for t in ALL_TABLES if t not in present]
            results.append(
                CheckResult(
                    "raw_tables_present",
                    "raw",
                    "error",
                    not missing,
                    len(missing),
                    "Every raw table was built.",
                    [{"table": t} for t in missing],
                )
            )
            empty = [
                t
                for t in ALL_TABLES
                if t in present and con.execute(f"select count(*) from raw.{t}").fetchone()[0] == 0
            ]
            results.append(
                CheckResult(
                    "raw_tables_not_empty",
                    "raw",
                    "error",
                    not empty,
                    len(empty),
                    "Every raw table has rows.",
                    [{"table": t} for t in empty],
                )
            )
        for check in checks:
            try:
                cursor = con.execute(f"select * from ({check.sql}) limit 1000")
                columns = [d[0] for d in cursor.description]
                rows = [dict(zip(columns, r, strict=True)) for r in cursor.fetchall()]
                results.append(
                    CheckResult(
                        check.name,
                        check.layer,
                        check.severity,
                        not rows,
                        len(rows),
                        check.description,
                        [_jsonable(r) for r in rows[:5]],
                    )
                )
            except duckdb.Error as exc:
                results.append(
                    CheckResult(
                        check.name,
                        check.layer,
                        "error",
                        False,
                        -1,
                        check.description,
                        [],
                        error=str(exc).splitlines()[0],
                    )
                )
    for r in results:
        level = logging.INFO if r.passed else (logging.ERROR if r.severity == "error" else logging.WARNING)
        log.log(
            level, "dq %-5s %-40s %s (%d)", r.severity, r.name, "ok" if r.passed else "FAIL", r.violations
        )
    if lake is not None and run_id is not None:
        lake.write_json(f"ops/dq/{run_id}-{layer}.json", [asdict(r) for r in results])
    return results


def has_errors(results: list[CheckResult]) -> bool:
    return any(not r.passed and r.severity == "error" for r in results)


def _jsonable(row: dict) -> dict:
    return {k: (v if isinstance(v, int | float | str | type(None)) else str(v)) for k, v in row.items()}
