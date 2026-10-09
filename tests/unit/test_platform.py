"""Lake (GCS stand-in), manifest, Pub/Sub emulator, config, data-quality gate, report helpers."""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl
import pytest

from geco import report
from geco.config import Settings
from geco.lake import Lake, Manifest, ManifestEntry
from geco.pipeline import EXIT_DQ_FAILED, Pipeline, StageError
from geco.pubsub import LocalPubSub
from geco.quality.checks import has_errors, run_checks
from geco.sources import trends
from geco.warehouse import connect, register_raw


@pytest.mark.parametrize("uri", ["memory://geco-test", None])
def test_put_object_is_content_addressed(uri, tmp_path):
    lake = Lake(uri or (tmp_path / "lake").as_uri())
    key, digest, created = lake.put_object("s", "d", b"hello", "txt", compress=True)
    assert created and key == f"bronze/s/d/{digest}.txt.gz"
    assert lake.put_object("s", "d", b"hello", "txt", compress=True) == (key, digest, False)
    assert lake.get_object(key) == b"hello"
    assert lake.list("bronze/s") == [key]
    if uri:
        lake.fs.rm(lake.root, recursive=True)


def test_gzip_payload_is_byte_stable(lake):
    a, _, _ = lake.put_object("s", "d", b"x" * 1000, "csv", compress=True)
    before = lake.read_bytes(a)
    lake.delete(a)
    lake.put_object("s", "d", b"x" * 1000, "csv", compress=True)
    assert lake.read_bytes(a) == before  # mtime=0 -> reproducible bronze objects


def _entry(run, fetched, key="k", status="new"):
    return ManifestEntry(
        run_id=run,
        source="s",
        dataset="d",
        key=key,
        fetched_at=fetched,
        status=status,
        source_url="u",
        sha256="x",
        bronze_key="bronze/s/d/x.txt",
    )


def test_manifest_dedupes_same_poll_and_skips_failures(lake):
    lake.write_bytes("bronze/s/d/x.txt", b"x")
    m = Manifest(lake)
    m.write_run("r1", [_entry("r1", "2026-10-05T06:00:00Z")])
    m.write_run("r1", [_entry("r1", "2026-10-05T06:00:00Z")])  # appended twice to the same run
    m.write_run(
        "r2", [_entry("r2", "2026-10-05T09:00:00Z"), _entry("r2", "2026-10-05T09:00:00Z", "k2", "failed")]
    )
    ok = m.ok_entries("s")
    assert [e.fetched_at for e in ok] == ["2026-10-05T06:00:00Z", "2026-10-05T09:00:00Z"]
    assert m.latest("s", "d", "k").run_id == "r2"
    assert m.latest("s", "d", "k2") is None


def test_local_pubsub_redelivers_until_acked(lake):
    bus = LocalPubSub(lake, "t", "sub")
    ids = bus.publish([{"event_id": "a"}, {"event_id": "b"}], ordering_key="US", attributes={"geo": "US"})
    assert len(set(ids)) == 2
    assert bus.publish([{"event_id": "a"}], "US", {}) == ids[:1]  # same payload, same message id
    first = bus.pull()
    assert {m.json()["event_id"] for m in first} == {"a", "b"}
    assert {m.message_id for m in bus.pull()} == set(ids)  # not acked yet -> redelivered
    bus.ack(first[:1])
    assert [m.json()["event_id"] for m in bus.pull()] == [first[1].json()["event_id"]]
    assert first[0].ordering_key == "US" and first[0].attributes == {"geo": "US"}


def test_consume_is_idempotent_across_redelivery(ctx):
    bus = LocalPubSub(ctx.lake, "t", "sub")
    event = {
        "event_id": "e1",
        "geo": "US",
        "polled_at": "2026-10-05T06:00:00Z",
        "event_type": "entered",
        "term_key": "x",
    }
    bus.publish([event], "US", {})
    first = trends.consume_events(ctx, bus)
    assert first.detail["distinct_events"] == 1
    assert trends.consume_events(ctx, bus) is None


def test_settings_lists_alias_and_as_of(monkeypatch):
    monkeypatch.setenv("GECO_TRENDS_GEOS", "US, GB ,IN")
    monkeypatch.setenv("YOUTUBE_API_KEY", "abc")
    monkeypatch.setenv("GECO_AS_OF", "2026-10-05T06:00:00")
    s = Settings(_env_file=None)
    assert s.trends_geos == ["US", "GB", "IN"]
    assert s.youtube_api_key.get_secret_value() == "abc"
    assert "abc" not in repr(s)
    assert s.now() == datetime(2026, 10, 5, 6, tzinfo=UTC)


def test_raw_gate_fails_on_bad_data(settings):
    pipe = Pipeline(settings)
    bad = pl.DataFrame(
        {
            "advertiser_id": ["A", "A"],
            "advertiser_name": ["x", "x"],
            "public_ids_list": [None, None],
            "regions": ["US", "US"],
            "elections": [None, None],
            "total_creatives": [1, 1],
        }
    )
    pipe.lake.write_parquet("raw/ads_advertisers/data.parquet", bad)
    register_raw(pipe.lake, pipe.db_path)
    results = run_checks(pipe.db_path, "raw", pipe.now)
    assert has_errors(results)
    failed = {r.name for r in results if not r.passed}
    assert {"raw_tables_present", "ads_advertisers_unique"} <= failed
    with pytest.raises(StageError) as exc:
        pipe.load(sources=[])
    assert exc.value.exit_code == EXIT_DQ_FAILED


def test_warehouse_uses_utc(tmp_path):
    with connect(tmp_path / "w.duckdb") as con:
        assert con.execute("select current_setting('TimeZone')").fetchone()[0] == "UTC"


def test_report_helpers():
    svg = report.hbar([("a", 10, "tip a"), ("b<", 5, "tip b")], "t")
    assert svg.count("<title>") == 2 and "b&lt;" in svg
    stacked = report.stacked_shares(["2026-10-04"], {"x": [0.25], "y": [0.75]}, "s")
    assert "25.0%" in stacked and "legend" in stacked
    assert report._compact(1_234_567) == "1.2M"
    assert report._fmt(None) == "–"
    assert report.hbar([], "t").startswith("<p")


def test_duckdb_reads_an_object_store_lake(settings):
    """Same path the Cloud Run load job takes against gs:// (here fsspec's memory filesystem)."""
    from tests.conftest import SYNTHETIC

    s = settings.model_copy(
        update={
            "lake_uri": "memory://geco-objstore",
            "dq_fail_on_error": False,
            "youtube_channels": ["UCsyntheticFixture00000002"],
            "youtube_rss_url_template": (SYNTHETIC / "youtube").as_uri() + "/{channel_id}.xml",
        }
    )
    pipe = Pipeline(s)
    try:
        pipe.extract(["youtube"])
        counts, results = pipe.load(["youtube"])
        assert counts["youtube_videos"] == 15
        assert pipe.lake.local_path("raw") is None
        assert next(r for r in results if r.name == "youtube_video_keys").passed
    finally:
        pipe.lake.fs.rm(pipe.lake.root, recursive=True)


def test_prune_keeps_latest_snapshot_versions(settings):
    from geco.lake import ManifestEntry as Entry

    pipe = Pipeline(settings)
    keys = []
    for day in range(1, 6):
        key, digest, _ = pipe.lake.put_object("netflix", "global_weekly", f"v{day}".encode(), "xlsx")
        keys.append(key)
        pipe.manifest.write_run(
            f"r{day}",
            [
                Entry(
                    run_id=f"r{day}",
                    source="netflix",
                    dataset="global_weekly",
                    key="global_weekly",
                    fetched_at=f"2026-10-0{day}T06:00:00Z",
                    status="new",
                    source_url="u",
                    sha256=digest,
                    bronze_key=key,
                )
            ],
        )
    deleted = pipe.prune(keep=2)
    assert sorted(deleted) == sorted(keys[:3])
    assert [pipe.lake.exists(k) for k in keys] == [False, False, False, True, True]
    assert pipe.manifest.latest("netflix", "global_weekly", "global_weekly").bronze_key == keys[-1]
    assert len(Manifest(pipe.lake).ok_entries("netflix")) == 2  # pruned history is not loadable
