-- A term can (rarely) appear twice in one feed; keep its best position.
with ranked as (
    select
        geo,
        polled_at,
        rank as trend_rank,
        term,
        term_key,
        approx_traffic,
        approx_traffic_min,
        started_at,
        news_item_count,
        top_news_source,
        row_number() over (partition by geo, polled_at, term_key order by rank) as dup_seq
    from {{ source('raw', 'trends_items') }}
    where term_key is not null and term_key <> ''
)

select
    geo,
    polled_at,
    trend_rank,
    term,
    term_key,
    approx_traffic,
    approx_traffic_min,
    started_at,
    news_item_count,
    top_news_source
from ranked
where dup_seq = 1
