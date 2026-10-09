select
    advertiser_id,
    advertiser_name,
    week_start_date,
    {{ add_days('week_start_date', 6) }} as week_end_date,
    currency,
    spend
from {{ source('raw', 'ads_weekly_spend') }}
