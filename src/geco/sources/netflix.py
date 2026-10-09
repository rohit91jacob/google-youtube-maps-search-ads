"""Netflix Top 10 weekly lists (streaming): the official downloadable files on netflix.com/tudum/top10."""

from __future__ import annotations

import io
import logging

import polars as pl

from geco.http import FetchError
from geco.lake import Lake, Manifest, ManifestEntry
from geco.sources.base import ExtractContext

log = logging.getLogger(__name__)

SOURCE = "netflix"
FILES = {
    "all-weeks-global.xlsx": "global_weekly",
    "all-weeks-countries.xlsx": "country_weekly",
    "most-popular.xlsx": "most_popular",
}
XLSX_MAGIC = b"PK\x03\x04"


def extract(ctx: ExtractContext) -> list[ManifestEntry]:
    entries = []
    base = ctx.settings.netflix_base_url.rstrip("/")
    for filename, dataset in FILES.items():
        url = f"{base}/{filename}"
        previous = ctx.manifest.latest(SOURCE, dataset, dataset)
        try:
            result = ctx.http.get(
                url,
                etag=previous.etag if previous else None,
                last_modified=previous.last_modified if previous else None,
            )
            if result.not_modified and previous is not None:
                entries.append(ctx.not_modified(previous, url))
                continue
            data = result.read()
            if not data.startswith(XLSX_MAGIC):
                # netflix.com answers some user agents with an HTML "unsupported browser" page.
                raise FetchError(f"{url}: response is not an xlsx file ({data[:40]!r})")
        except FetchError as exc:
            entries.append(ctx.failed(source=SOURCE, dataset=dataset, key=dataset, url=url, error=str(exc)))
            continue
        entries.append(
            ctx.store(
                source=SOURCE,
                dataset=dataset,
                key=dataset,
                url=url,
                data=data,
                ext="xlsx",
                etag=result.etag,
                last_modified=result.last_modified,
            )
        )
    return entries


def _read(lake: Lake, manifest: Manifest, dataset: str) -> pl.DataFrame | None:
    entry = manifest.latest(SOURCE, dataset, dataset)
    if entry is None:
        return None
    # Text columns are declared so calamine does not guess (season_title is often all "N/A").
    text = {"show_title": "string", "season_title": "string", "week": "string", "category": "string"}
    return pl.read_excel(
        io.BytesIO(lake.get_object(entry.bronze_key)), engine="calamine", read_options={"dtypes": text}
    )


def _common(frame: pl.DataFrame) -> list[pl.Expr]:
    return [
        pl.col("category").str.strip_chars().alias("category"),
        pl.col("show_title").cast(pl.String).str.strip_chars().alias("show_title"),
        pl.when(pl.col("season_title").cast(pl.String).is_in(["N/A", ""]))
        .then(None)
        .otherwise(pl.col("season_title").cast(pl.String).str.strip_chars())
        .alias("season_title"),
    ]


def _week(col: str = "week") -> pl.Expr:
    return pl.col(col).cast(pl.String).str.slice(0, 10).str.to_date("%Y-%m-%d").alias("week_end_date")


def build_raw(lake: Lake, manifest: Manifest) -> dict[str, pl.DataFrame]:
    tables: dict[str, pl.DataFrame] = {}
    glob = _read(lake, manifest, "global_weekly")
    if glob is not None:
        tables["netflix_global_weekly"] = glob.select(
            _week(),
            *_common(glob),
            pl.col("weekly_rank").cast(pl.Int64),
            pl.col("weekly_hours_viewed").cast(pl.Int64, strict=False),
            pl.col("runtime").cast(pl.Float64, strict=False).alias("runtime_hours"),
            pl.col("weekly_views").cast(pl.Int64, strict=False),
            pl.col("cumulative_weeks_in_top_10").cast(pl.Int64, strict=False),
        ).sort("week_end_date", "category", "weekly_rank")
    countries = _read(lake, manifest, "country_weekly")
    if countries is not None:
        tables["netflix_country_weekly"] = countries.select(
            pl.col("country_name").cast(pl.String),
            pl.col("country_iso2").cast(pl.String),
            _week(),
            *_common(countries),
            pl.col("weekly_rank").cast(pl.Int64),
            pl.col("cumulative_weeks_in_top_10").cast(pl.Int64, strict=False),
        ).sort("country_iso2", "week_end_date", "category", "weekly_rank")
    popular = _read(lake, manifest, "most_popular")
    if popular is not None:
        tables["netflix_most_popular"] = popular.select(
            *_common(popular),
            pl.col("rank").cast(pl.Int64),
            pl.col("hours_viewed_first_91_days").cast(pl.Int64, strict=False),
            pl.col("runtime").cast(pl.Float64, strict=False).alias("runtime_hours"),
            pl.col("views_first_91_days").cast(pl.Int64, strict=False),
        ).sort("category", "rank")
    return tables
