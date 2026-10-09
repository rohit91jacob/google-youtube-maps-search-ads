"""End-to-end: fixtures -> bronze -> Pub/Sub -> raw -> DQ -> dbt build -> DQ -> site, then a rerun."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from collections import Counter
from pathlib import Path

import duckdb
import pytest

from geco.cli import run_fixture_pipeline
from geco.fixtures import FIXTURE_POLL_TIMES
from geco.pipeline import run_id_for
from tests.conftest import FIXTURES, SYNTHETIC

pytestmark = pytest.mark.e2e


def q(db: Path, sql: str) -> list[tuple]:
    with duckdb.connect(str(db)) as con:
        return con.execute(sql).fetchall()


def test_every_mart_is_built_and_populated(fixture_run):
    db = fixture_run["pipe"].db_path
    marts = [
        r[0] for r in q(db, "select table_name from information_schema.tables where table_schema = 'marts'")
    ]
    expected = {
        "mart_trends_churn_by_geo",
        "mart_trends_term_longevity",
        "mart_trends_geo_summary",
        "fct_trends_events",
        "dim_political_advertiser",
        "fct_political_ad_spend_weekly",
        "mart_political_ad_spend_region_week",
        "mart_political_ad_top_advertisers",
        "mart_political_ad_spend_us_state",
        "mart_netflix_title_longevity",
        "mart_netflix_language_mix_weekly",
        "mart_netflix_country_reach",
        "mart_osm_amenity_density",
        "mart_osm_city_profile",
        "mart_youtube_upload_cadence",
        "mart_video_attention_weekly",
    }
    assert expected <= set(marts)
    for mart in expected - {"mart_video_attention_weekly"}:
        assert q(db, f"select count(*) from marts.{mart}")[0][0] > 0, mart


def test_trends_churn_reconciles_with_pubsub_stream(fixture_run):
    db = fixture_run["pipe"].db_path
    rows = q(
        db,
        """
        select c.geo, c.polled_at, c.new_terms, c.dropped_terms,
               count(*) filter (where e.event_type = 'entered'), count(*) filter (where e.event_type = 'exited')
        from marts.mart_trends_churn_by_geo c
        left join marts.fct_trends_events e on e.geo = c.geo and e.polled_at = c.polled_at
        group by all order by 1, 2""",
    )
    assert len(rows) == 3 * 2  # 3 geos x 2 polls with a predecessor
    for _, _, new, dropped, entered, exited in rows:
        assert new == entered and dropped == exited and new > 0
    summary = q(
        db, "select geo, polls, avg_hours_between_polls from marts.mart_trends_geo_summary order by geo"
    )
    assert summary == [("GB", 3, 3.0), ("IN", 3, 3.0), ("US", 3, 3.0)]


def test_ad_spend_marts_carry_every_dollar(fixture_run):
    db = fixture_run["pipe"].db_path
    bundle = SYNTHETIC / "political_ads" / "google-political-ads-transparency-bundle.zip"
    with zipfile.ZipFile(bundle) as z:
        rows = list(
            csv.DictReader(io.TextIOWrapper(z.open("google-political-ads-advertiser-weekly-spend.csv")))
        )
    by_currency = {}
    for r in rows:
        for cur in ("USD", "INR", "BRL"):
            by_currency[cur] = by_currency.get(cur, 0) + int(r[f"Spend_{cur}"])
    mart = dict(
        q(db, "select currency, sum(spend) from marts.mart_political_ad_spend_region_week group by 1")
    )
    assert mart == by_currency
    regions = {r[0] for r in q(db, "select distinct region_code from marts.fct_political_ad_spend_weekly")}
    assert regions == {"US", "IN", "BR"}
    shares = q(db, "select sum(share_of_us_spend) from marts.mart_political_ad_spend_us_state")[0][0]
    assert shares == pytest.approx(1.0)


def test_netflix_marts(fixture_run):
    db = fixture_run["pipe"].db_path
    assert (
        q(db, "select count(*) from marts.mart_netflix_title_longevity where not weeks_match_netflix")[0][0]
        == 0
    )
    weekly = q(
        db,
        "select week_end_date, round(sum(share_of_hours), 9) from marts.mart_netflix_language_mix_weekly group by 1",
    )
    assert len(weekly) == 6 and {s for _, s in weekly} == {1.0}


def test_osm_density_uses_real_counts(fixture_run):
    db = fixture_run["pipe"].db_path
    london = json.loads((FIXTURES / "osm" / "london.json").read_text(encoding="utf-8"))
    cafes = int(london["elements"][1]["tags"]["total"])  # 2nd statement is amenity=cafe
    row = q(
        db,
        "select total, per_km2, area_km2 from marts.mart_osm_amenity_density where city = 'london' and amenity = 'cafe'",
    )
    total, per_km2, area = row[0]
    assert total == cafes and per_km2 == pytest.approx(cafes / area) and 99 < area < 101
    assert q(db, "select count(*) from marts.mart_osm_city_profile")[0][0] == 8


def test_youtube_cadence(fixture_run):
    db = fixture_run["pipe"].db_path
    rows = dict(q(db, "select channel_id, median_gap_hours from marts.mart_youtube_upload_cadence"))
    # The synthetic channels upload every ~8 h, ~24 h and ~72 h.
    assert (
        rows["UCsyntheticFixture00000001"]
        < rows["UCsyntheticFixture00000002"]
        < rows["UCsyntheticFixture00000003"]
    )


def test_site_has_real_numbers(fixture_run):
    site = fixture_run["site"]
    summary = json.loads((site / "summary.json").read_text(encoding="utf-8"))
    head = summary["headline"]
    assert head["trends_polls"] == 9 and head["osm_cities"] == 8 and head["youtube_channels"] == 3
    page = (site / "index.html").read_text(encoding="utf-8")
    for section in ("Search", "Ads", "Streaming", "Maps", "Video"):
        assert f'id="{section.lower()}"' in page
    assert "OpenStreetMap contributors" in page
    assert (site / "data" / "osm_city_profile.csv").exists()


def _snapshot(db: Path) -> dict[str, list]:
    tables = [
        r[0]
        for r in q(
            db, "select table_name from information_schema.tables where table_schema = 'marts' order by 1"
        )
    ]
    return {t: q(db, f"select * from marts.{t} order by all") for t in tables}


def test_rerun_is_idempotent(fixture_run):
    """Re-running every stage on the same inputs adds no bronze objects and changes no mart row."""
    pipe = fixture_run["pipe"]
    bronze_before = pipe.lake.list("bronze")
    marts_before = _snapshot(pipe.db_path)
    raw_before = {
        t: pipe.lake.read_bytes(f"raw/{t}/data.parquet") for t in ("ads_weekly_spend", "osm_amenity_counts")
    }

    again = run_fixture_pipeline(fixture_run["data_dir"], FIXTURES)

    bronze_after = again.lake.list("bronze")
    # Only the event-drain object of the re-run may be new (and the subscription is already empty).
    assert [k for k in bronze_after if k not in bronze_before] == []
    # The frozen clock gives both runs the same run ids: each static object is "new" once (first
    # run) and "unchanged" once (re-run), never stored twice.
    first_run = run_id_for(FIXTURE_POLL_TIMES[0])
    statuses = Counter(
        e.status for e in again.manifest.entries() if e.run_id == first_run and e.source != "trends"
    )
    assert set(statuses) == {"new", "unchanged"} and statuses["new"] == statuses["unchanged"]
    assert _snapshot(again.db_path) == marts_before
    for table, data in raw_before.items():
        assert again.lake.read_bytes(f"raw/{table}/data.parquet") == data
