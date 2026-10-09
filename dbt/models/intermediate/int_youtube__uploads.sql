-- Uploads with the gap to the channel's previous observed upload.
select
    v.channel_id,
    v.video_id,
    v.title,
    v.published_at,
    v.latest_views,
    v.first_polled_at,
    lag(v.published_at) over (partition by v.channel_id order by v.published_at, v.video_id) as previous_published_at
from {{ ref('stg_youtube__videos') }} as v
