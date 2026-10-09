select country, subdivision, subdivision_secondary, currency, spend
from {{ source('raw', 'ads_geo_spend') }}
