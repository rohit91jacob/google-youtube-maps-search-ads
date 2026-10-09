select
    geo,
    polled_at,
    item_count,
    sha256 as payload_sha256,
    row_number() over (partition by geo order by polled_at) as poll_seq,
    lag(polled_at) over (partition by geo order by polled_at) as previous_polled_at
from {{ source('raw', 'trends_polls') }}
