-- Lifetime US political-ad spend per state (advertiser-geo-spend file, USD).
with by_advertiser as (
    select
        subdivision,
        advertiser_id,
        sum(spend) as spend
    from {{ ref('stg_ads__advertiser_geo_spend') }}
    where country = 'US' and currency = 'USD' and subdivision is not null
    group by subdivision, advertiser_id
),

ranked as (
    select
        *,
        row_number() over (partition by subdivision order by spend desc, advertiser_id) as seq
    from by_advertiser
),

states as (
    select
        subdivision,
        count(*) as advertisers_with_spend,
        sum(spend) as spend_usd
    from by_advertiser
    group by subdivision
)

select
    s.subdivision,
    s.advertisers_with_spend,
    s.spend_usd,
    {{ safe_div('s.spend_usd', 'sum(s.spend_usd) over ()') }} as share_of_us_spend,
    r.advertiser_id as top_advertiser_id,
    a.advertiser_name as top_advertiser_name,
    r.spend as top_advertiser_spend_usd
from states as s
inner join ranked as r on s.subdivision = r.subdivision and r.seq = 1
left join {{ ref('stg_ads__advertisers') }} as a on r.advertiser_id = a.advertiser_id
