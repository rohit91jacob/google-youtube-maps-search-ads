from __future__ import annotations

from datetime import UTC, datetime

import pytest

from geco.lake import Manifest
from geco.pubsub import LocalPubSub
from geco.sources import trends
from tests.conftest import SYNTHETIC, make_ctx


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("200+", 200),
        ("2K+", 2000),
        ("1M+", 1_000_000),
        ("1,000+", 1000),
        ("5000+", 5000),
        ("", None),
        ("lots", None),
    ],
)
def test_parse_traffic(raw, expected):
    assert trends.parse_traffic(raw) == expected


def test_term_key_normalises_case_and_spaces():
    assert trends.term_key("  Dak   Prescott ") == trends.term_key("dak prescott")


def test_parse_rss_shape():
    items = trends.parse_rss((SYNTHETIC / "trends" / "poll1" / "US.xml").read_bytes())
    assert len(items) == 10
    assert [i["rank"] for i in items] == list(range(1, 11))
    first = items[0]
    assert first["term"].startswith("synthetic us term")
    assert first["approx_traffic_min"] == int(first["approx_traffic"].rstrip("+"))
    assert first["started_at"].tzinfo is not None
    assert first["news_item_count"] == 1
    assert first["top_news_source"] == "Synthetic News"


def test_parse_rss_rejects_other_xml():
    with pytest.raises(ValueError, match="channel"):
        trends.parse_rss(b"<feed/>")


def test_diff_events_entered_and_exited():
    prev = [
        {"term": "a", "term_key": "a", "rank": 1, "approx_traffic_min": 100},
        {"term": "b", "term_key": "b", "rank": 2, "approx_traffic_min": 100},
    ]
    cur = [
        {"term": "B", "term_key": "b", "rank": 1, "approx_traffic_min": 200},
        {"term": "c", "term_key": "c", "rank": 2, "approx_traffic_min": 100},
    ]
    events = trends.diff_events("US", prev, "2026-10-05T06:00:00Z", cur, "2026-10-05T09:00:00Z")
    assert {(e["event_type"], e["term_key"]) for e in events} == {("entered", "c"), ("exited", "a")}
    again = trends.diff_events("US", prev, "2026-10-05T06:00:00Z", cur, "2026-10-05T09:00:00Z")
    assert [e["event_id"] for e in events] == [e["event_id"] for e in again]  # deterministic ids


def test_first_poll_emits_entered_for_everything():
    cur = [{"term": "x", "term_key": "x", "rank": 1, "approx_traffic_min": 1}]
    (event,) = trends.diff_events("GB", None, None, cur, "2026-10-05T06:00:00Z")
    assert event["event_type"] == "entered"
    assert event["previous_polled_at"] is None


def _poll(ctx, poll: int, hour: int):
    url = (SYNTHETIC / "trends" / f"poll{poll}").as_uri() + "/{geo}.xml"
    now = datetime(2026, 10, 5, hour, 0, tzinfo=UTC)
    c = make_ctx(
        ctx,
        settings={"trends_geos": ["US", "GB"], "trends_url_template": url},
        now=now,
        run_id=now.strftime("%Y%m%dT%H%M%SZ"),
    )
    pubsub = LocalPubSub(ctx.lake, "trends-events", "loader")
    entries = trends.extract(c, pubsub)
    Manifest(ctx.lake).write_run(c.run_id, entries)
    return c, pubsub, entries


def test_extract_publish_consume_and_build(ctx):
    c1, pubsub, entries = _poll(ctx, 1, 6)
    assert [e.status for e in entries] == ["new", "new"]
    first = trends.consume_events(c1, pubsub)
    assert first.detail == {"messages": 20, "distinct_events": 20}
    assert pubsub.pull() == []  # acked
    Manifest(ctx.lake).write_run(c1.run_id, [first])

    c2, pubsub, _ = _poll(ctx, 2, 9)
    Manifest(ctx.lake).write_run(c2.run_id, [trends.consume_events(c2, pubsub)])

    tables = trends.build_raw(ctx.lake, Manifest(ctx.lake))
    assert tables["trends_polls"].height == 4
    assert tables["trends_items"].height == 40
    events = tables["trends_events"]
    second = events.filter(events["previous_polled_at"].is_not_null())
    assert second.height > 0
    assert set(second["event_type"]) == {"entered", "exited"}
    entered = second.filter(second["event_type"] == "entered").height
    exited = second.filter(second["event_type"] == "exited").height
    assert entered == exited  # lists stay at 10 terms


def test_failed_geo_is_recorded_not_raised(ctx, tmp_path):
    url = (tmp_path / "missing").as_uri() + "/{geo}.xml"
    c = make_ctx(ctx, settings={"trends_geos": ["US"], "trends_url_template": url})
    (entry,) = trends.extract(c, None)
    assert entry.status == "failed"
    assert "no such file" in entry.detail["error"]


def test_same_poll_rerun_collapses(ctx):
    _poll(ctx, 1, 6)
    _poll(ctx, 1, 6)  # a retried job with the same frozen clock
    tables = trends.build_raw(ctx.lake, Manifest(ctx.lake))
    assert tables["trends_polls"].height == 2
