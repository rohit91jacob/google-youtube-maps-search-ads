select
    event_id,
    event_type,
    geo,
    term,
    term_key,
    rank as trend_rank,
    approx_traffic_min,
    polled_at,
    previous_polled_at,
    message_id
from {{ source('raw', 'trends_events') }}
