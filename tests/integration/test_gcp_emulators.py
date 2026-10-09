"""Against the official Pub/Sub emulator and fake-gcs-server (started by the CI `emulators` job).

    PUBSUB_EMULATOR_HOST=localhost:8085 GECO_TEST_GCS_ENDPOINT=http://localhost:4443 \
        uv run pytest -m integration tests/integration
"""

from __future__ import annotations

import os
import uuid

import pytest

from geco.config import Settings
from geco.lake import Lake, Manifest
from geco.pipeline import Pipeline
from geco.pubsub import GcpPubSub
from geco.sources import trends
from tests.conftest import SYNTHETIC, make_ctx

pytestmark = pytest.mark.integration


@pytest.fixture
def pubsub_host(monkeypatch: pytest.MonkeyPatch) -> str:
    host = os.environ.get("PUBSUB_EMULATOR_HOST") or os.environ.get("GECO_TEST_PUBSUB_EMULATOR_HOST")
    if not host:
        pytest.skip("PUBSUB_EMULATOR_HOST not set")
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", host)
    return host


@pytest.fixture
def gcs_endpoint() -> str:
    endpoint = os.environ.get("GECO_TEST_GCS_ENDPOINT")
    if not endpoint:
        pytest.skip("GECO_TEST_GCS_ENDPOINT not set")
    return endpoint


def test_trends_events_round_trip_through_pubsub_emulator(pubsub_host, ctx):
    suffix = uuid.uuid4().hex[:8]
    bus = GcpPubSub("geco-emulator", f"trends-events-{suffix}", f"loader-{suffix}")
    url = (SYNTHETIC / "trends" / "poll1").as_uri() + "/{geo}.xml"
    c = make_ctx(ctx, settings={"trends_geos": ["US", "GB"], "trends_url_template": url})
    entries = trends.extract(c, bus)
    Manifest(ctx.lake).write_run(c.run_id, entries)
    drained = trends.consume_events(c, bus)
    assert drained is not None
    assert drained.detail["distinct_events"] == 20  # 10 'entered' per geo on the first poll
    assert bus.pull(max_messages=10) == []  # everything was acked


def test_lake_on_fake_gcs(gcs_endpoint):
    import gcsfs

    bucket = f"geco-it-{uuid.uuid4().hex[:8]}"
    fs = gcsfs.GCSFileSystem(endpoint_url=gcs_endpoint, token="anon")
    fs.mkdir(bucket)
    lake = Lake(f"gs://{bucket}/lake", endpoint_url=gcs_endpoint, token="anon")
    key, _, created = lake.put_object("trends", "trending_rss", b"<rss/>", "xml")
    assert created and lake.exists(key)
    assert lake.put_object("trends", "trending_rss", b"<rss/>", "xml")[2] is False
    assert lake.get_object(key) == b"<rss/>"
    assert lake.list("bronze/trends") == [key]
    assert lake.local_path(key) is None  # DuckDB needs a local lake; BigQuery reads gs:// directly


def test_extract_into_fake_gcs(gcs_endpoint, tmp_path, monkeypatch):
    import gcsfs

    bucket = f"geco-it-{uuid.uuid4().hex[:8]}"
    gcsfs.GCSFileSystem(endpoint_url=gcs_endpoint, token="anon").mkdir(bucket)
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        lake_uri=f"gs://{bucket}/lake",
        youtube_channels=["UCsyntheticFixture00000001"],
        youtube_rss_url_template=(SYNTHETIC / "youtube").as_uri() + "/{channel_id}.xml",
    )
    monkeypatch.setattr("geco.pipeline.Lake", lambda uri: Lake(uri, endpoint_url=gcs_endpoint, token="anon"))
    pipe = Pipeline(settings)
    entries = pipe.extract(["youtube"])
    assert [e.status for e in entries] == ["new"]
    assert Manifest(pipe.lake).latest("youtube", "channel_rss", "UCsyntheticFixture00000001") is not None
    # Raw parquet goes back to GCS and DuckDB reads it from there through gcsfs.
    pipe.settings.dq_fail_on_error = False  # only the YouTube tables exist in this lake
    counts, results = pipe.load(["youtube"])
    assert counts["youtube_videos"] == 15
    assert next(r for r in results if r.name == "youtube_video_keys").passed
