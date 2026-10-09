"""OpenStreetMap amenity counts via the Overpass API (Maps). Data (c) OpenStreetMap contributors, ODbL.

Each city is measured inside the same-sized box (default 10 km x 10 km) centred on a fixed,
documented point, so densities are comparable between cities. The box is a box: it includes
water and parkland where a city has them (see docs/data_dictionary.md).
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import polars as pl

from geco.http import FetchError
from geco.lake import Lake, Manifest, ManifestEntry
from geco.sources.base import ExtractContext

log = logging.getLogger(__name__)

SOURCE = "osm"
DATASET = "amenity_counts"
EARTH_RADIUS_KM = 6371.0088
KM_PER_DEGREE_LAT = 111.32


@dataclass(frozen=True)
class City:
    slug: str
    name: str
    country: str
    lat: float
    lon: float
    centre: str


CITIES = [
    City("london", "London", "GB", 51.5073, -0.1276, "Charing Cross"),
    City("paris", "Paris", "FR", 48.8534, 2.3488, "Point zéro, Notre-Dame"),
    City("berlin", "Berlin", "DE", 52.5163, 13.3777, "Brandenburg Gate"),
    City("new-york", "New York", "US", 40.7580, -73.9855, "Times Square"),
    City("tokyo", "Tokyo", "JP", 35.6812, 139.7671, "Tokyo Station"),
    City("delhi", "Delhi", "IN", 28.6315, 77.2167, "Connaught Place"),
    City("bengaluru", "Bengaluru", "IN", 12.9757, 77.6011, "MG Road"),
    City("sao-paulo", "São Paulo", "BR", -23.5503, -46.6339, "Praça da Sé"),
]

AMENITY_GROUPS: dict[str, list[str]] = {
    "food_drink": ["restaurant", "cafe", "fast_food", "bar", "pub"],
    "health": ["pharmacy", "hospital", "clinic", "doctors", "dentist"],
    "education": ["school", "university", "college", "kindergarten", "library"],
    "transport": ["fuel", "charging_station", "bicycle_rental", "parking"],
    "finance": ["bank", "atm"],
    "civic": ["police", "post_office", "toilets"],
}
AMENITIES = [(group, amenity) for group, values in AMENITY_GROUPS.items() for amenity in values]


def bbox(city: City, half_size_km: float) -> tuple[float, float, float, float]:
    """(south, west, north, east) of a square box of side 2 * half_size_km."""
    dlat = half_size_km / KM_PER_DEGREE_LAT
    dlon = half_size_km / (KM_PER_DEGREE_LAT * math.cos(math.radians(city.lat)))
    return (
        round(city.lat - dlat, 6),
        round(city.lon - dlon, 6),
        round(city.lat + dlat, 6),
        round(city.lon + dlon, 6),
    )


def bbox_area_km2(south: float, west: float, north: float, east: float) -> float:
    """Exact area of a lat/lon rectangle on a sphere."""
    return (
        EARTH_RADIUS_KM**2
        * math.radians(east - west)
        * (math.sin(math.radians(north)) - math.sin(math.radians(south)))
    )


def build_query(box: tuple[float, float, float, float]) -> str:
    south, west, north, east = box
    statements = "".join(f'nwr["amenity"="{amenity}"];out count;' for _, amenity in AMENITIES)
    return f"[out:json][timeout:180][bbox:{south},{west},{north},{east}];{statements}"


def parse_counts(response: dict) -> list[dict]:
    remark = response.get("remark") or ""
    if "error" in remark.lower():
        raise ValueError(f"Overpass error: {remark[:200]}")
    counts = [e for e in response.get("elements", []) if e.get("type") == "count"]
    if len(counts) != len(AMENITIES):
        raise ValueError(f"expected {len(AMENITIES)} count elements, got {len(counts)}")
    rows = []
    for (group, amenity), element in zip(AMENITIES, counts, strict=True):
        tags = element.get("tags", {})
        rows.append(
            {
                "category": group,
                "amenity": amenity,
                "nodes": int(tags.get("nodes", 0)),
                "ways": int(tags.get("ways", 0)),
                "relations": int(tags.get("relations", 0)),
                "total": int(tags.get("total", 0)),
            }
        )
    return rows


def _is_fresh(entry: ManifestEntry | None, now: datetime, max_age_days: int) -> bool:
    if entry is None:
        return False
    fetched = datetime.fromisoformat(entry.fetched_at.replace("Z", "+00:00"))
    return now - fetched < timedelta(days=max_age_days)


def extract(ctx: ExtractContext, *, force: bool = False, rounds: int = 3) -> list[ManifestEntry]:
    entries = []
    half = ctx.settings.osm_box_half_size_km
    for city in CITIES:
        previous = ctx.manifest.latest(SOURCE, DATASET, city.slug)
        if not force and _is_fresh(previous, ctx.now, ctx.settings.osm_max_age_days):
            log.info("osm[%s]: fresh (fetched %s), skipped", city.slug, previous.fetched_at)
            continue
        box = bbox(city, half)
        query = build_query(box)
        response, endpoint, error = None, None, ""
        for attempt in range(rounds):
            for endpoint in ctx.settings.overpass_endpoints:
                url = endpoint if not endpoint.startswith("file://") else endpoint.format(city=city.slug)
                try:
                    result = ctx.http.post_form(url, {"data": query})
                    response = json.loads(result.read())
                    parse_counts(response)
                    break
                except (FetchError, ValueError) as exc:
                    error = str(exc)
                    response = None
                    log.info("osm[%s]: %s failed (%s)", city.slug, endpoint, error[:120])
            if response is not None:
                break
            if not endpoint.startswith("file://"):
                time.sleep(min(60, 10 * (attempt + 1)))
        if response is None:
            entries.append(
                ctx.failed(source=SOURCE, dataset=DATASET, key=city.slug, url=str(endpoint), error=error)
            )
            continue
        envelope = {
            "city": city.slug,
            "bbox": box,
            "half_size_km": half,
            "amenities": [a for _, a in AMENITIES],
            "query": query,
            "endpoint": endpoint,
            "response": response,
        }
        data = json.dumps(envelope, sort_keys=True, ensure_ascii=False).encode()
        entries.append(
            ctx.store(
                source=SOURCE,
                dataset=DATASET,
                key=city.slug,
                url=str(endpoint),
                data=data,
                ext="json",
                detail={"osm_base": response.get("osm3s", {}).get("timestamp_osm_base")},
            )
        )
    return entries


SCHEMA = {
    "city": pl.String,
    "city_name": pl.String,
    "country": pl.String,
    "centre": pl.String,
    "south": pl.Float64,
    "west": pl.Float64,
    "north": pl.Float64,
    "east": pl.Float64,
    "area_km2": pl.Float64,
    "category": pl.String,
    "amenity": pl.String,
    "nodes": pl.Int64,
    "ways": pl.Int64,
    "relations": pl.Int64,
    "total": pl.Int64,
    "osm_base_timestamp": pl.Datetime("us", "UTC"),
    "fetched_at": pl.Datetime("us", "UTC"),
}


def build_raw(lake: Lake, manifest: Manifest) -> dict[str, pl.DataFrame]:
    by_slug = {c.slug: c for c in CITIES}
    rows = []
    for entry in manifest.ok_entries(SOURCE, DATASET):
        envelope = json.loads(lake.get_object(entry.bronze_key))
        city = by_slug.get(envelope["city"])
        if city is None:
            continue
        south, west, north, east = envelope["bbox"]
        base = envelope["response"].get("osm3s", {}).get("timestamp_osm_base")
        for row in parse_counts(envelope["response"]):
            rows.append(
                {
                    "city": city.slug,
                    "city_name": city.name,
                    "country": city.country,
                    "centre": city.centre,
                    "south": south,
                    "west": west,
                    "north": north,
                    "east": east,
                    "area_km2": round(bbox_area_km2(south, west, north, east), 4),
                    **row,
                    "osm_base_timestamp": datetime.fromisoformat(base.replace("Z", "+00:00"))
                    if base
                    else None,
                    "fetched_at": datetime.fromisoformat(entry.fetched_at.replace("Z", "+00:00")),
                }
            )
    frame = pl.DataFrame(rows, schema=SCHEMA)
    return {"osm_amenity_counts": frame.sort("city", "fetched_at", "category", "amenity")}
