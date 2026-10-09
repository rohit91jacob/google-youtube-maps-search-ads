-- Latest successful count per city (Overpass is polled weekly; older counts stay in raw).
with latest as (
    select city, max(fetched_at) as fetched_at
    from {{ source('raw', 'osm_amenity_counts') }}
    group by city
)

select
    c.city,
    c.city_name,
    c.country,
    c.centre,
    c.south,
    c.west,
    c.north,
    c.east,
    c.area_km2,
    c.category,
    c.amenity,
    c.nodes,
    c.ways,
    c.relations,
    c.total,
    c.osm_base_timestamp,
    c.fetched_at
from {{ source('raw', 'osm_amenity_counts') }} as c
inner join latest as l
    on c.city = l.city and c.fetched_at = l.fetched_at
