with polls as (
    select
        geo,
        count(*) as polls,
        min(polled_at) as first_polled_at,
        max(polled_at) as last_polled_at,
        avg(item_count) as avg_terms_per_poll
    from {{ ref('stg_trends__polls') }}
    group by geo
),

churn as (
    select
        geo,
        avg(churn_rate) as avg_churn_rate,
        {{ median('churn_rate') }} as median_churn_rate,
        avg(hours_since_previous) as avg_hours_between_polls
    from {{ ref('mart_trends_churn_by_geo') }}
    group by geo
),

-- "Completed" lifetimes: neither on the first poll's list nor on the latest one.
longevity as (
    select
        geo,
        count(*) as distinct_terms,
        sum(case when not still_trending and not seen_in_first_poll then 1 else 0 end) as completed_terms,
        avg(
            case when not still_trending and not seen_in_first_poll then observed_hours end
        ) as avg_completed_observed_hours,
        avg(
            case
                when still_trending or seen_in_first_poll then null
                when polls_seen = 1 then 1.0
                else 0.0
            end
        ) as single_poll_share
    from {{ ref('mart_trends_term_longevity') }}
    group by geo
)

select
    p.geo,
    g.name as geo_name,
    p.polls,
    p.first_polled_at,
    p.last_polled_at,
    l.distinct_terms,
    l.completed_terms,
    round(p.avg_terms_per_poll, 2) as avg_terms_per_poll,
    round(c.avg_churn_rate, 4) as avg_churn_rate,
    round(c.median_churn_rate, 4) as median_churn_rate,
    round(c.avg_hours_between_polls, 2) as avg_hours_between_polls,
    round(l.avg_completed_observed_hours, 2) as avg_completed_observed_hours,
    round(l.single_poll_share, 4) as single_poll_share
from polls as p
left join churn as c on p.geo = c.geo
left join longevity as l on p.geo = l.geo
left join {{ ref('ref_trends_geos') }} as g on p.geo = g.code
