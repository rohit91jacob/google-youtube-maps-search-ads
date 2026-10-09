-- The Pub/Sub stream and the snapshot tables are two independent paths to the same facts: for every
-- poll after the event stream started, 'entered' events = new terms and 'exited' = dropped terms.
with stream_start as (
    select
        geo,
        min(polled_at) as first_event_poll
    from {{ ref('fct_trends_events') }}
    group by geo
),

events as (
    select
        geo,
        polled_at,
        sum(case when event_type = 'entered' then 1 else 0 end) as entered,
        sum(case when event_type = 'exited' then 1 else 0 end) as exited
    from {{ ref('fct_trends_events') }}
    where not is_first_poll_of_geo
    group by geo, polled_at
)

select
    c.geo,
    c.polled_at,
    c.new_terms,
    coalesce(e.entered, 0) as entered,
    c.dropped_terms,
    coalesce(e.exited, 0) as exited
from {{ ref('mart_trends_churn_by_geo') }} as c
inner join stream_start as s on c.geo = s.geo and c.polled_at >= s.first_event_poll
left join events as e on c.geo = e.geo and c.polled_at = e.polled_at
where c.new_terms <> coalesce(e.entered, 0) or c.dropped_terms <> coalesce(e.exited, 0)
