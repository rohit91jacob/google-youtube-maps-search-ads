-- The Pub/Sub event stream (entered / exited) as a fact table.
select
    event_id,
    event_type,
    geo,
    term,
    term_key,
    trend_rank,
    approx_traffic_min,
    polled_at,
    previous_polled_at,
    message_id,
    previous_polled_at is null as is_first_poll_of_geo
from {{ ref('stg_trends__events') }}
