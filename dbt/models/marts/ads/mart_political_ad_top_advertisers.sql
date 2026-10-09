-- Top advertisers by lifetime weekly-reported spend in each report region.
with advertiser_totals as (
    select
        region_code,
        region_name,
        currency,
        advertiser_id,
        max(advertiser_name) as advertiser_name,
        sum(spend) as total_spend,
        count(distinct week_start_date) as weeks_with_spend,
        min(week_start_date) as first_week_start,
        max(week_start_date) as last_week_start
    from {{ ref('fct_political_ad_spend_weekly') }}
    group by region_code, region_name, currency, advertiser_id
),

peaks as (
    select
        advertiser_id,
        currency,
        week_start_date as peak_week_start,
        spend as peak_week_spend,
        row_number() over (partition by advertiser_id, currency order by spend desc, week_start_date) as seq
    from {{ ref('fct_political_ad_spend_weekly') }}
),

ranked as (
    select
        t.*,
        sum(t.total_spend) over (partition by t.region_code, t.currency) as region_total_spend,
        row_number() over (
            partition by t.region_code, t.currency order by t.total_spend desc, t.advertiser_id
        ) as advertiser_rank
    from advertiser_totals as t
)

select
    r.region_code,
    r.region_name,
    r.currency,
    r.advertiser_rank,
    r.advertiser_id,
    r.advertiser_name,
    r.total_spend,
    r.region_total_spend,
    {{ safe_div('r.total_spend', 'r.region_total_spend') }} as share_of_region_spend,
    r.weeks_with_spend,
    r.first_week_start,
    r.last_week_start,
    p.peak_week_start,
    p.peak_week_spend
from ranked as r
inner join peaks as p on r.advertiser_id = p.advertiser_id and r.currency = p.currency and p.seq = 1
where r.advertiser_rank <= {{ var('ads_top_advertisers') }}
