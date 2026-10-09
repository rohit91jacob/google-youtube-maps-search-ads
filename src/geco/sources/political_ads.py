"""Google Political Ads transparency bundle (Ads): the CSVs behind BigQuery's google_political_ads."""

from __future__ import annotations

import io
import logging
import zipfile

import polars as pl

from geco.http import FetchError
from geco.lake import Lake, Manifest, ManifestEntry
from geco.sources.base import ExtractContext

log = logging.getLogger(__name__)

SOURCE = "political_ads"
# Bundle member -> bronze dataset name. The 2.9 GB creative-level file is deliberately not taken.
MEMBERS = {
    "google-political-ads-updated.csv": "report_updated",
    "google-political-ads-advertiser-stats.csv": "advertiser_stats",
    "google-political-ads-advertiser-weekly-spend.csv": "advertiser_weekly_spend",
    "google-political-ads-advertiser-geo-spend.csv": "advertiser_geo_spend",
    "google-political-ads-geo-spend.csv": "geo_spend",
}
BUNDLE_DATASET = "bundle"


def extract(ctx: ExtractContext) -> list[ManifestEntry]:
    url = ctx.settings.political_ads_url
    previous = ctx.manifest.latest(SOURCE, BUNDLE_DATASET, "bundle")
    try:
        result = ctx.http.get(
            url,
            etag=previous.etag if previous else None,
            last_modified=previous.last_modified if previous else None,
            stream_to_disk=not url.startswith("file://"),
        )
    except FetchError as exc:
        return [ctx.failed(source=SOURCE, dataset=BUNDLE_DATASET, key="bundle", url=url, error=str(exc))]
    if result.not_modified and previous is not None:
        members = [ctx.manifest.latest(SOURCE, ds, ds) for ds in MEMBERS.values()]
        return [ctx.not_modified(previous, url)] + [ctx.not_modified(m, url) for m in members if m]

    entries = []
    source = result.path if result.path is not None else io.BytesIO(result.read())
    try:
        with zipfile.ZipFile(source) as bundle:
            names = set(bundle.namelist())
            missing = [m for m in MEMBERS if m not in names]
            if missing:
                raise FetchError(f"bundle is missing {missing}")
            for member, dataset in MEMBERS.items():
                data = bundle.read(member)
                entries.append(
                    ctx.store(
                        source=SOURCE,
                        dataset=dataset,
                        key=dataset,
                        url=f"{url}#{member}",
                        data=data,
                        ext="csv",
                        compress=True,
                    )
                )
    except (zipfile.BadZipFile, FetchError) as exc:
        return [ctx.failed(source=SOURCE, dataset=BUNDLE_DATASET, key="bundle", url=url, error=str(exc))]
    finally:
        if result.path is not None:
            result.path.unlink(missing_ok=True)
    # The bundle entry records ETag / Last-Modified for the next conditional request.
    bundle_entry = ManifestEntry(
        run_id=ctx.run_id,
        source=SOURCE,
        dataset=BUNDLE_DATASET,
        key="bundle",
        fetched_at=ctx.now_iso,
        status="new" if any(e.status == "new" for e in entries) else "unchanged",
        source_url=url,
        sha256="+".join(e.sha256 or "" for e in entries)[:64],
        bronze_key=entries[0].bronze_key,
        etag=result.etag,
        last_modified=result.last_modified,
        detail={"members": list(MEMBERS)},
    )
    return [bundle_entry, *entries]


# ---------------------------------------------------------------------- raw tables
def _read_csv(data: bytes) -> pl.DataFrame:
    return pl.read_csv(io.BytesIO(data), infer_schema=False, encoding="utf8-lossy")


def _spend_long(frame: pl.DataFrame, keys: list[str]) -> pl.DataFrame:
    """Wide Spend_<CUR> columns -> one row per non-zero (keys, currency).

    The wide files carry a column for every currency, zero outside the advertiser's region, so
    zeros and blanks are dropped. Anything else is kept as published: a negative value stays
    negative and an unparseable one becomes null, so the raw DQ gate (`ads_spend_non_negative`,
    `ads_spend_parsed`) sees and stops it instead of the loader silently discarding it.
    """
    spend_cols = [c for c in frame.columns if c.startswith("Spend_") and c != "Spend_Range"]
    long = frame.unpivot(index=keys, on=spend_cols, variable_name="currency", value_name="raw_spend")
    raw = pl.col("raw_spend").str.strip_chars().str.replace_all(",", "")
    return (
        long.with_columns(
            pl.col("currency").str.strip_prefix("Spend_"),
            raw.cast(pl.Float64, strict=False).round(0).cast(pl.Int64).alias("spend"),
            raw.alias("raw_spend"),
        )
        .filter(pl.col("raw_spend").is_not_null() & (pl.col("raw_spend") != ""))
        .filter(pl.col("spend").is_null() | (pl.col("spend") != 0))
        .drop("raw_spend")
    )


def _latest(lake: Lake, manifest: Manifest, dataset: str) -> pl.DataFrame | None:
    entry = manifest.latest(SOURCE, dataset, dataset)
    return _read_csv(lake.get_object(entry.bronze_key)) if entry else None


def _subdivision(country: pl.Expr, subdivision: pl.Expr) -> pl.Expr:
    """'CO' -> 'US-CO'; already-qualified 'US-GA' stays; empty -> null."""
    sub = subdivision.str.strip_chars()
    return (
        pl.when(sub.is_null() | (sub == ""))
        .then(None)
        .when(sub.str.contains("-"))
        .then(sub)
        .otherwise(country + pl.lit("-") + sub)
    )


def build_raw(lake: Lake, manifest: Manifest) -> dict[str, pl.DataFrame]:
    tables: dict[str, pl.DataFrame] = {}
    updated = _latest(lake, manifest, "report_updated")
    if updated is not None:
        col = updated.columns[0]
        tables["ads_report_updated"] = updated.select(
            pl.col(col).str.strip_chars().alias("report_data_updated_pt"),
            pl.col(col)
            .str.strip_chars()
            .str.to_datetime("%Y-%m-%d %H:%M:%S", strict=False)
            .alias("report_data_updated_at_pt"),
        )

    stats = _latest(lake, manifest, "advertiser_stats")
    if stats is not None:
        tables["ads_advertisers"] = stats.select(
            pl.col("Advertiser_ID").alias("advertiser_id"),
            pl.col("Advertiser_Name").alias("advertiser_name"),
            pl.col("Public_IDs_List").alias("public_ids_list"),
            pl.col("Regions").alias("regions"),
            pl.col("Elections").alias("elections"),
            pl.col("Total_Creatives").cast(pl.Int64, strict=False).alias("total_creatives"),
        ).sort("advertiser_id")
        tables["ads_advertiser_spend_totals"] = (
            _spend_long(stats, ["Advertiser_ID"])
            .rename({"Advertiser_ID": "advertiser_id"})
            .sort("advertiser_id", "currency")
        )

    weekly = _latest(lake, manifest, "advertiser_weekly_spend")
    if weekly is not None:
        tables["ads_weekly_spend"] = (
            _spend_long(weekly, ["Advertiser_ID", "Advertiser_Name", "Week_Start_Date"])
            .select(
                pl.col("Advertiser_ID").alias("advertiser_id"),
                pl.col("Advertiser_Name").alias("advertiser_name"),
                pl.col("Week_Start_Date").str.to_date("%Y-%m-%d").alias("week_start_date"),
                "currency",
                "spend",
            )
            .sort("advertiser_id", "week_start_date", "currency")
        )

    adv_geo = _latest(lake, manifest, "advertiser_geo_spend")
    if adv_geo is not None:
        tables["ads_advertiser_geo_spend"] = (
            _spend_long(adv_geo, ["Advertiser_ID", "Country", "Country_Subdivision_Primary"])
            .select(
                pl.col("Advertiser_ID").alias("advertiser_id"),
                pl.col("Country").alias("country"),
                _subdivision(pl.col("Country"), pl.col("Country_Subdivision_Primary")).alias("subdivision"),
                "currency",
                "spend",
            )
            .sort("advertiser_id", "country", "subdivision", "currency", nulls_last=True)
        )

    geo = _latest(lake, manifest, "geo_spend")
    if geo is not None:
        tables["ads_geo_spend"] = (
            _spend_long(geo, ["Country", "Country_Subdivision_Primary", "Country_Subdivision_Secondary"])
            .select(
                pl.col("Country").alias("country"),
                _subdivision(pl.col("Country"), pl.col("Country_Subdivision_Primary")).alias("subdivision"),
                pl.col("Country_Subdivision_Secondary").alias("subdivision_secondary"),
                "currency",
                "spend",
            )
            .sort("country", "subdivision", "subdivision_secondary", "currency", nulls_last=True)
        )
    return tables
