"""Runtime settings. Every value has a safe default and can be overridden with a GECO_* variable."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

CommaList = Annotated[list[str], NoDecode]

DEFAULT_TRENDS_GEOS = ["US", "GB", "IN", "BR", "JP", "DE"]

# Official channels whose public RSS feeds are polled (no API key needed). The feed returns the
# 15 most recent uploads of each channel.
DEFAULT_YOUTUBE_CHANNELS = [
    "UCK8sQmJBp8GCxrOtXWBpyEA",  # Google
    "UCBR8-60-B28hp2BmDPdntcQ",  # YouTube
    "UC_x5XG1OV2P6uZZ5FSM9Ttw",  # Google for Developers
    "UCJS9pqu9BzkAMNTmzNMNhvg",  # Google Cloud Tech
    "UCP7jMXSY2xbc3KCAE0MHQ-A",  # Google DeepMind
    "UCVHFbqXqoYvEWM1Ddxl0QDg",  # Android Developers
    "UCWOA1ZGywLbqmigxE4Qlvuw",  # Netflix
]

DEFAULT_OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GECO_", env_file=".env", extra="ignore", populate_by_name=True
    )

    # ------------------------------------------------------------------ storage
    data_dir: Path = Path("data")
    # Root of the object store that stands in for GCS (bronze/, raw/, ops/). Any fsspec URL:
    # file:///..., memory://..., or gs://bucket/prefix with the `gcp` extra installed.
    lake_uri: str | None = None
    warehouse_path: Path | None = None
    dbt_project_dir: Path = Path("dbt")
    site_dir: Path = Path("site")

    # ------------------------------------------------------------------ http
    http_timeout_seconds: float = 120.0
    http_retries: int = 5
    http_backoff_seconds: float = 2.0
    user_agent: str = "google-youtube-maps-search-ads/0.1 (+https://github.com/rohit91jacob)"

    # ------------------------------------------------------------------ sources
    trends_geos: CommaList = Field(default_factory=lambda: list(DEFAULT_TRENDS_GEOS))
    trends_url_template: str = "https://trends.google.com/trending/rss?geo={geo}"

    political_ads_url: str = (
        "https://storage.googleapis.com/political-csv/google-political-ads-transparency-bundle.zip"
    )

    netflix_base_url: str = "https://www.netflix.com/tudum/top10/data"

    overpass_endpoints: CommaList = Field(default_factory=lambda: list(DEFAULT_OVERPASS_ENDPOINTS))
    osm_max_age_days: int = 7
    osm_box_half_size_km: float = 5.0

    youtube_channels: CommaList = Field(default_factory=lambda: list(DEFAULT_YOUTUBE_CHANNELS))
    youtube_rss_url_template: str = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    # Accepts YOUTUBE_API_KEY (the conventional name) or GECO_YOUTUBE_API_KEY.
    youtube_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("YOUTUBE_API_KEY", "GECO_YOUTUBE_API_KEY")
    )
    youtube_api_base_url: str = "https://www.googleapis.com/youtube/v3"

    # ------------------------------------------------------------------ Pub/Sub (trends events)
    pubsub_backend: Literal["local", "gcp"] = "local"
    gcp_project: str = "geco-local"
    trends_topic: str = "trends-events"
    trends_subscription: str = "trends-events-loader"

    # ------------------------------------------------------------------ quality / runtime
    dq_fail_on_error: bool = True
    # Freeze "now" (ISO 8601) for deterministic fixture runs and tests.
    as_of: datetime | None = None
    log_format: Literal["text", "json"] = "text"

    @field_validator("trends_geos", "youtube_channels", "overpass_endpoints", mode="before")
    @classmethod
    def _split_commas(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("youtube_api_key", mode="before")
    @classmethod
    def _blank_key_is_none(cls, value: object) -> object:
        # CI passes `${{ secrets.YOUTUBE_API_KEY }}`, which is "" when the secret is not set.
        return None if isinstance(value, str) and not value.strip() else value

    @property
    def resolved_lake_uri(self) -> str:
        if self.lake_uri:
            return self.lake_uri
        return (self.data_dir / "lake").resolve().as_uri()

    @property
    def resolved_warehouse_path(self) -> Path:
        return self.warehouse_path or self.data_dir / "warehouse" / "geco.duckdb"

    def now(self) -> datetime:
        if self.as_of is not None:
            return self.as_of if self.as_of.tzinfo else self.as_of.replace(tzinfo=UTC)
        return datetime.now(UTC).replace(microsecond=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
