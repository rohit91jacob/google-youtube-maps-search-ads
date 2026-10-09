with ranked as (
    select
        channel_id,
        channel_title,
        channel_created_at,
        subscriber_count,
        view_count,
        video_count,
        source,
        polled_at,
        row_number() over (partition by channel_id order by polled_at desc, source) as seq,
        row_number() over (
            partition by channel_id
            order by case when source = 'data_api' then 0 else 1 end, polled_at desc
        ) as api_seq
    from {{ source('raw', 'youtube_channels') }}
)

select
    latest.channel_id,
    latest.channel_title,
    latest.channel_created_at,
    latest.polled_at as last_polled_at,
    case when api.source = 'data_api' then api.subscriber_count end as subscriber_count,
    case when api.source = 'data_api' then api.view_count end as channel_view_count,
    case when api.source = 'data_api' then api.video_count end as channel_video_count
from ranked as latest
left join ranked as api
    on latest.channel_id = api.channel_id and api.api_seq = 1
where latest.seq = 1
