with totals as (
    select
        advertiser_id,
        currency,
        spend,
        row_number() over (partition by advertiser_id order by spend desc, currency) as currency_seq
    from {{ ref('stg_ads__advertiser_spend_totals') }}
),

activity as (
    select
        advertiser_id,
        min(week_start_date) as first_week_start,
        max(week_start_date) as last_week_start,
        count(distinct week_start_date) as weeks_with_spend
    from {{ ref('stg_ads__weekly_spend') }}
    group by advertiser_id
)

select
    a.advertiser_id,
    a.advertiser_name,
    a.public_ids_list,
    a.regions,
    a.primary_region,
    a.elections,
    a.total_creatives,
    t.currency as spend_currency,
    t.spend as lifetime_spend,
    w.first_week_start,
    w.last_week_start,
    coalesce(w.weeks_with_spend, 0) as weeks_with_spend
from {{ ref('stg_ads__advertisers') }} as a
left join totals as t on a.advertiser_id = t.advertiser_id and t.currency_seq = 1
left join activity as w on a.advertiser_id = w.advertiser_id
