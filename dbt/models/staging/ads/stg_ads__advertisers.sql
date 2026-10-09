select
    advertiser_id,
    advertiser_name,
    public_ids_list,
    regions,
    elections,
    total_creatives,
    -- Almost every advertiser is verified in exactly one region; the few multi-region ones are
    -- labelled MULTI rather than double-counted.
    case when regions like '%,%' then 'MULTI' else trim(regions) end as primary_region
from {{ source('raw', 'ads_advertisers') }}
