from __future__ import annotations

import json
import logging
import math

import pytest
import responses
from pydantic import SecretStr

from geco.lake import Manifest
from geco.sources import netflix, osm, youtube
from tests.conftest import FIXTURES, SYNTHETIC, make_ctx


# ---------------------------------------------------------------- netflix
def test_netflix_extract_and_build(ctx):
    c = make_ctx(ctx, settings={"netflix_base_url": (SYNTHETIC / "netflix").as_uri()})
    entries = netflix.extract(c)
    assert {e.dataset for e in entries} == set(netflix.FILES.values())
    Manifest(ctx.lake).write_run(c.run_id, entries)
    tables = netflix.build_raw(ctx.lake, Manifest(ctx.lake))
    glob = tables["netflix_global_weekly"]
    assert glob.height == 6 * 4 * 10
    assert glob["season_title"].null_count() == glob.filter(glob["category"].str.starts_with("Films")).height
    assert glob["week_end_date"].dt.weekday().unique().to_list() == [7]
    assert tables["netflix_country_weekly"]["country_iso2"].n_unique() == 3
    assert tables["netflix_most_popular"].height == 40


@responses.activate
def test_netflix_html_instead_of_xlsx_is_rejected(ctx):
    base = "https://example.invalid/top10"
    for name in netflix.FILES:
        responses.get(f"{base}/{name}", body=b"<!doctype html><html>unsupported browser</html>")
    entries = netflix.extract(make_ctx(ctx, settings={"netflix_base_url": base}))
    assert {e.status for e in entries} == {"failed"}
    assert "not an xlsx" in entries[0].detail["error"]


# ---------------------------------------------------------------- osm
def test_bbox_is_ten_by_ten_km():
    city = osm.CITIES[0]
    south, west, north, east = osm.bbox(city, 5.0)
    assert math.isclose(osm.bbox_area_km2(south, west, north, east), 100.0, rel_tol=0.01)


def test_query_has_one_count_per_amenity():
    query = osm.build_query((1, 2, 3, 4))
    assert query.count("out count;") == len(osm.AMENITIES)
    assert "[bbox:1,2,3,4]" in query


def test_parse_counts_real_fixture():
    rows = osm.parse_counts(json.loads((FIXTURES / "osm" / "london.json").read_text(encoding="utf-8")))
    assert len(rows) == len(osm.AMENITIES)
    assert all(r["total"] == r["nodes"] + r["ways"] + r["relations"] for r in rows)
    cafes = next(r for r in rows if r["amenity"] == "cafe")
    assert cafes["total"] > 100


@pytest.mark.parametrize(
    "payload",
    [
        {"remark": "runtime error: Query timed out", "elements": []},
        {"elements": [{"type": "count", "tags": {}}]},
    ],
)
def test_parse_counts_rejects_errors(payload):
    with pytest.raises(ValueError, match=r"(error|expected)"):
        osm.parse_counts(payload)


@responses.activate
def test_overpass_fails_over_to_next_endpoint(ctx):
    good = (FIXTURES / "osm" / "london.json").read_bytes()
    responses.post("https://a.invalid/api/interpreter", status=504, body="busy")
    responses.post("https://b.invalid/api/interpreter", body=good)
    c = make_ctx(
        ctx,
        settings={
            "overpass_endpoints": ["https://a.invalid/api/interpreter", "https://b.invalid/api/interpreter"]
        },
    )
    entries = osm.extract(c, rounds=1)
    assert len(entries) == len(osm.CITIES)
    assert {e.status for e in entries} == {"new"}  # the envelope names the city, so each differs
    assert all(e.source_url.startswith("https://b.invalid") for e in entries)


def test_osm_fresh_counts_are_skipped(ctx):
    c = make_ctx(ctx, settings={"overpass_endpoints": [(FIXTURES / "osm").as_uri() + "/{city}.json"]})
    first = osm.extract(c)
    Manifest(ctx.lake).write_run(c.run_id, first)
    assert len(first) == 8
    assert osm.extract(c) == []  # within osm_max_age_days
    assert len(osm.extract(c, force=True)) == 8
    tables = osm.build_raw(ctx.lake, Manifest(ctx.lake))["osm_amenity_counts"]
    assert tables.height == 8 * len(osm.AMENITIES)
    assert tables["area_km2"].min() > 99


# ---------------------------------------------------------------- youtube
CHANNEL = "UCsyntheticFixture00000001"


def test_parse_feed():
    channel, videos = youtube.parse_feed((SYNTHETIC / "youtube" / f"{CHANNEL}.xml").read_bytes())
    assert channel["channel_id"] == CHANNEL  # feed-level id lacks the UC prefix
    assert len(videos) == 15
    assert all(v["views"] > 0 and v["published_at"].tzinfo for v in videos)


def test_rss_extract_without_key_skips_api(ctx, caplog):
    caplog.set_level(logging.INFO, logger="geco.sources.youtube")
    c = make_ctx(
        ctx,
        settings={
            "youtube_channels": [CHANNEL],
            "youtube_rss_url_template": (SYNTHETIC / "youtube").as_uri() + "/{channel_id}.xml",
        },
    )
    entries = youtube.extract(c)
    assert [e.dataset for e in entries] == ["channel_rss"]
    assert "not set" in caplog.text


@responses.activate
def test_data_api_with_synthetic_recorded_shapes(ctx):
    base = "https://youtube.invalid/v3"
    responses.get(f"{base}/channels", body=(SYNTHETIC / "youtube" / "api_channels.json").read_bytes())
    responses.get(
        f"{base}/playlistItems", body=(SYNTHETIC / "youtube" / "api_playlist_items.json").read_bytes()
    )
    c = make_ctx(
        ctx,
        settings={
            "youtube_channels": [CHANNEL],
            "youtube_rss_url_template": (SYNTHETIC / "youtube").as_uri() + "/{channel_id}.xml",
            "youtube_api_key": SecretStr("test-key-not-real"),
            "youtube_api_base_url": base,
        },
    )
    entries = youtube.extract(c)
    assert [e.dataset for e in entries] == ["channel_rss", "data_api"]
    assert responses.calls[0].request.params["key"] == "test-key-not-real"
    Manifest(ctx.lake).write_run(c.run_id, entries)
    tables = youtube.build_raw(ctx.lake, Manifest(ctx.lake))
    api = tables["youtube_channels"].filter(tables["youtube_channels"]["source"] == "data_api")
    assert api.height == 3 and api["subscriber_count"].null_count() == 0
    # The same recorded playlist page answers for every channel, so its 20 videos dedupe to 20.
    assert tables["youtube_videos"].filter(tables["youtube_videos"]["source"] == "data_api").height == 20


@responses.activate
def test_data_api_error_never_leaks_the_key(ctx):
    base = "https://youtube.invalid/v3"
    responses.get(f"{base}/channels", status=403, body="quotaExceeded for key=secret-123")
    c = make_ctx(
        ctx,
        settings={
            "youtube_channels": [],
            "youtube_api_key": SecretStr("secret-123"),
            "youtube_api_base_url": base,
        },
    )
    (entry,) = youtube.extract(c)
    assert entry.status == "failed"
    assert "secret-123" not in json.dumps(entry.detail)
