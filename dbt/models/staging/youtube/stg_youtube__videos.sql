-- One row per video: when it was first seen, and the most recent view count.
with polls as (
    select
        video_id,
        channel_id,
        title,
        published_at,
        views,
        polled_at,
        source,
        row_number() over (partition by video_id order by case when views is null then 1 else 0 end, polled_at desc) as latest_seq
    from {{ source('raw', 'youtube_videos') }}
),

first_seen as (
    select video_id, min(polled_at) as first_polled_at, count(distinct polled_at) as polls_seen
    from {{ source('raw', 'youtube_videos') }}
    group by video_id
)

select
    p.video_id,
    p.channel_id,
    p.title,
    p.published_at,
    p.views as latest_views,
    p.polled_at as views_polled_at,
    p.source as latest_source,
    f.first_polled_at,
    f.polls_seen
from polls as p
inner join first_seen as f on p.video_id = f.video_id
where p.latest_seq = 1
