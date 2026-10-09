-- Weekly political-ad spend per report region and currency, with the week's top advertiser.
with advertiser_week as (
    select
        region_code,
        region_name,
        currency,
        week_start_date,
        week_end_date,
        advertiser_id,
        advertiser_name,
        sum(spend) as spend
    from {{ ref('fct_political_ad_spend_weekly') }}
    group by region_code, region_name, currency, week_start_date, week_end_date, advertiser_id, advertiser_name
),

ranked as (
    select
        *,
        row_number() over (
            partition by region_code, currency, week_start_date order by spend desc, advertiser_id
        ) as spend_rank
    from advertiser_week
),

weekly as (
    select
        region_code,
        region_name,
        currency,
        week_start_date,
        week_end_date,
        count(*) as advertisers_with_spend,
        sum(spend) as spend
    from advertiser_week
    group by region_code, region_name, currency, week_start_date, week_end_date
)

select
    w.region_code,
    w.region_name,
    w.currency,
    w.week_start_date,
    w.week_end_date,
    w.advertisers_with_spend,
    w.spend,
    lag(w.spend) over (partition by w.region_code, w.currency order by w.week_start_date) as previous_week_spend,
    r.advertiser_id as top_advertiser_id,
    r.advertiser_name as top_advertiser_name,
    r.spend as top_advertiser_spend,
    {{ safe_div('r.spend', 'w.spend') }} as top_advertiser_share
from weekly as w
inner join ranked as r
    on
        w.region_code = r.region_code
        and w.currency = r.currency
        and w.week_start_date = r.week_start_date
        and r.spend_rank = 1
