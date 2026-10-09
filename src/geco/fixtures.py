"""Point every source at the committed fixtures (file:// URLs) for offline, deterministic runs.

Used by the end-to-end tests, `make pipeline-fixtures` and CI. Three Trends polls three hours apart
exercise churn, longevity and the Pub/Sub event stream.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

FIXTURE_POLL_TIMES = [datetime(2026, 10, 5, 6, 0, tzinfo=UTC) + timedelta(hours=3 * i) for i in range(3)]
FIXTURE_CHANNELS = ["UCsyntheticFixture00000001", "UCsyntheticFixture00000002", "UCsyntheticFixture00000003"]


def fixture_env(fixtures_dir: Path, data_dir: Path, poll: int = 1) -> dict[str, str]:
    synthetic = (fixtures_dir / "synthetic").resolve()
    osm = (fixtures_dir / "osm").resolve()
    return {
        "GECO_DATA_DIR": str(data_dir),
        "GECO_TRENDS_GEOS": "US,GB,IN",
        "GECO_TRENDS_URL_TEMPLATE": (synthetic / "trends" / f"poll{poll}").as_uri() + "/{geo}.xml",
        "GECO_POLITICAL_ADS_URL": (
            synthetic / "political_ads" / "google-political-ads-transparency-bundle.zip"
        ).as_uri(),
        "GECO_NETFLIX_BASE_URL": (synthetic / "netflix").as_uri(),
        "GECO_OVERPASS_ENDPOINTS": osm.as_uri() + "/{city}.json",
        "GECO_YOUTUBE_CHANNELS": ",".join(FIXTURE_CHANNELS),
        "GECO_YOUTUBE_RSS_URL_TEMPLATE": (synthetic / "youtube").as_uri() + "/{channel_id}.xml",
        "GECO_AS_OF": FIXTURE_POLL_TIMES[poll - 1].isoformat(),
        "GECO_PUBSUB_BACKEND": "local",
    }
