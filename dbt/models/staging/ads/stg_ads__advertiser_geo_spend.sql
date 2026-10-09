select advertiser_id, country, subdivision, currency, spend
from {{ source('raw', 'ads_advertiser_geo_spend') }}
