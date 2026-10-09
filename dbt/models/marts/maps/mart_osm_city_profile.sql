-- One row per city: amenity density overall and by category (features per km2 of the box).
select
    city,
    city_name,
    country,
    max(area_km2) as area_km2,
    sum(total) as total_amenities,
    sum(total) / max(area_km2) as amenities_per_km2,
    sum(case when category = 'food_drink' then total else 0 end) / max(area_km2) as food_drink_per_km2,
    sum(case when category = 'health' then total else 0 end) / max(area_km2) as health_per_km2,
    sum(case when category = 'education' then total else 0 end) / max(area_km2) as education_per_km2,
    sum(case when category = 'transport' then total else 0 end) / max(area_km2) as transport_per_km2,
    sum(case when category = 'finance' then total else 0 end) / max(area_km2) as finance_per_km2,
    sum(case when category = 'civic' then total else 0 end) / max(area_km2) as civic_per_km2,
    {{ safe_div(
        "sum(case when amenity = 'cafe' then total else 0 end)",
        "sum(case when amenity = 'restaurant' then total else 0 end)"
    ) }} as cafes_per_restaurant,
    max(osm_base_timestamp) as osm_base_timestamp,
    max(fetched_at) as fetched_at
from {{ ref('stg_osm__amenity_counts') }}
group by city, city_name, country
