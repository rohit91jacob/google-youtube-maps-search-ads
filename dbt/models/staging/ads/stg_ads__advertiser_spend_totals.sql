select advertiser_id, currency, spend
from {{ source('raw', 'ads_advertiser_spend_totals') }}
