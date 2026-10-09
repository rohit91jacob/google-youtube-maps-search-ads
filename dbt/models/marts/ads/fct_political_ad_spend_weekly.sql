-- Grain: advertiser x week x currency. The report region is the region whose currency the spend
-- is published in (each advertiser reports only in its region's currency).
select
    w.advertiser_id,
    w.advertiser_name,
    w.week_start_date,
    w.week_end_date,
    c.code as region_code,
    c.name as region_name,
    w.currency,
    w.spend
from {{ ref('stg_ads__weekly_spend') }} as w
left join {{ ref('ref_ads_currencies') }} as c on w.currency = c.currency
