-- How long each trending search stayed on a geo's list, as observed by the polls.
-- observed_hours is a lower bound (first to last poll that saw the term). Terms on the latest
-- list are right-censored (still_trending), terms on the first list are left-censored.
with terms as (
    select
        geo,
        term_key,
        max(term) as term,
        min(polled_at) as first_seen_at,
        max(polled_at) as last_seen_at,
        count(*) as polls_seen,
        min(started_at) as started_at,
        min(trend_rank) as best_rank,
        max(approx_traffic_min) as max_approx_traffic
    from {{ ref('int_trends__poll_terms') }}
    group by geo, term_key
),

poll_bounds as (
    select
        geo,
        max(polled_at) as latest_polled_at,
        min(polled_at) as first_polled_at
    from {{ ref('stg_trends__polls') }}
    group by geo
),

window_polls as (
    select
        t.geo,
        t.term_key,
        count(*) as polls_in_window
    from terms as t
    inner join {{ ref('stg_trends__polls') }} as p
        on t.geo = p.geo and p.polled_at between t.first_seen_at and t.last_seen_at
    group by t.geo, t.term_key
)

select
    t.geo,
    t.term_key,
    t.term,
    t.first_seen_at,
    t.last_seen_at,
    t.started_at,
    t.polls_seen,
    w.polls_in_window,
    t.best_rank,
    t.max_approx_traffic,
    round({{ hours_between('t.first_seen_at', 't.last_seen_at') }}, 3) as observed_hours,
    round({{ hours_between('t.started_at', 't.last_seen_at') }}, 3) as hours_since_start_at_last_seen,
    t.last_seen_at = b.latest_polled_at as still_trending,
    t.first_seen_at = b.first_polled_at as seen_in_first_poll
from terms as t
inner join poll_bounds as b on t.geo = b.geo
inner join window_polls as w on t.geo = w.geo and t.term_key = w.term_key
