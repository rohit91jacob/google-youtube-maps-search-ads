-- OSM amenity counts per km2 inside each city's fixed box, and how cities rank per amenity.
select
    city,
    city_name,
    country,
    category,
    amenity,
    total,
    area_km2,
    total / area_km2 as per_km2,
    {{ safe_div('total', 'sum(total) over (partition by city)') }} as share_of_city_amenities,
    rank() over (partition by amenity order by total desc) as city_rank,
    osm_base_timestamp,
    fetched_at
from {{ ref('stg_osm__amenity_counts') }}
