-- Every term of every poll, with the poll's predecessor (for poll-to-poll comparisons).
select
    p.geo,
    p.polled_at,
    p.previous_polled_at,
    p.poll_seq,
    i.term_key,
    i.term,
    i.trend_rank,
    i.approx_traffic_min,
    i.started_at
from {{ ref('stg_trends__polls') }} as p
inner join {{ ref('stg_trends__items') }} as i
    on p.geo = i.geo and p.polled_at = i.polled_at
