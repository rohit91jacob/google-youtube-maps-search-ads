from __future__ import annotations

import csv
import io
import zipfile

import polars as pl
import responses

from geco.lake import Manifest
from geco.sources import political_ads
from tests.conftest import SYNTHETIC, make_ctx

BUNDLE = SYNTHETIC / "political_ads" / "google-political-ads-transparency-bundle.zip"


def _extract(ctx):
    c = make_ctx(ctx, settings={"political_ads_url": BUNDLE.as_uri()})
    entries = political_ads.extract(c)
    Manifest(ctx.lake).write_run(c.run_id, entries)
    return entries


def test_extract_takes_only_the_needed_members(ctx):
    entries = _extract(ctx)
    datasets = {e.dataset for e in entries}
    assert datasets == {"bundle", *political_ads.MEMBERS.values()}
    assert all(e.status == "new" for e in entries)
    assert all(e.bronze_key.endswith(".csv.gz") for e in entries if e.dataset != "bundle")


def test_reextract_is_unchanged(ctx):
    _extract(ctx)
    again = _extract(ctx)
    assert {e.status for e in again} == {"unchanged"}


def test_build_raw_long_format_matches_source(ctx):
    _extract(ctx)
    tables = political_ads.build_raw(ctx.lake, Manifest(ctx.lake))
    weekly = tables["ads_weekly_spend"]
    with zipfile.ZipFile(BUNDLE) as bundle:
        rows = list(
            csv.DictReader(io.TextIOWrapper(bundle.open("google-political-ads-advertiser-weekly-spend.csv")))
        )
    expected = sum(int(r["Spend_USD"]) for r in rows)
    assert weekly.filter(pl.col("currency") == "USD")["spend"].sum() == expected
    assert set(weekly["currency"]) == {"USD", "INR", "BRL"}
    assert (weekly["spend"] > 0).all()
    assert weekly["week_start_date"].dt.weekday().unique().to_list() == [7]  # Sunday
    geo = tables["ads_advertiser_geo_spend"]
    assert set(geo["subdivision"]) <= {"US-WI", "US-PA", "US-GA", "US-AZ"}
    assert tables["ads_report_updated"]["report_data_updated_at_pt"][0].year == 2026


@responses.activate
def test_conditional_get_not_modified(ctx):
    url = "https://example.invalid/bundle.zip"
    responses.get(url, body=BUNDLE.read_bytes(), headers={"ETag": '"v1"'})
    c = make_ctx(ctx, settings={"political_ads_url": url})
    first = political_ads.extract(c)
    Manifest(ctx.lake).write_run(c.run_id, first)
    responses.replace(responses.GET, url, status=304)
    second = political_ads.extract(make_ctx(c, run_id="20261006T060000Z"))
    assert responses.calls[1].request.headers["If-None-Match"] == '"v1"'
    assert {e.status for e in second} == {"not_modified"}
    assert len(second) == len(first)


@responses.activate
def test_corrupt_bundle_is_a_failed_entry(ctx):
    url = "https://example.invalid/bundle.zip"
    responses.get(url, body=b"not a zip")
    (entry,) = political_ads.extract(make_ctx(ctx, settings={"political_ads_url": url}))
    assert entry.status == "failed"
