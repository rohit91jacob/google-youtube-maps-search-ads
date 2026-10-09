-- YouTube vs Netflix, side by side per Netflix week (Monday-Sunday). The two numbers are NOT the
-- same measure: Netflix "views" are hours viewed / runtime within the week; YouTube views are the
-- lifetime view count of videos published that week, read from the RSS feed at the latest poll.
with netflix_ranked as (
    select
        week_start_date,
        week_end_date,
        title,
        category,
        weekly_views,
        row_number() over (partition by week_end_date order by weekly_views desc, title) as seq,
        sum(weekly_views) over (partition by week_end_date) as top10_views_all_lists
    from {{ ref('stg_netflix__global_weekly') }}
    where weekly_views is not null
),

netflix as (
    select * from netflix_ranked where seq = 1
),

youtube_ranked as (
    select
        n.week_end_date,
        v.video_id,
        v.title,
        v.channel_id,
        v.latest_views,
        row_number() over (partition by n.week_end_date order by v.latest_views desc, v.video_id) as seq,
        count(*) over (partition by n.week_end_date) as videos_published,
        sum(v.latest_views) over (partition by n.week_end_date) as views_to_date_all_videos
    from {{ ref('stg_youtube__videos') }} as v
    inner join netflix as n
        on cast(v.published_at as date) between n.week_start_date and n.week_end_date
    where v.latest_views is not null
)

select
    n.week_start_date,
    n.week_end_date,
    n.title as netflix_top_title,
    n.category as netflix_top_category,
    n.weekly_views as netflix_top_weekly_views,
    n.top10_views_all_lists as netflix_top10_weekly_views,
    y.videos_published as youtube_tracked_videos_published,
    y.video_id as youtube_top_video_id,
    y.title as youtube_top_video_title,
    c.channel_title as youtube_top_channel,
    y.latest_views as youtube_top_video_views_to_date,
    y.views_to_date_all_videos as youtube_tracked_views_to_date
from netflix as n
inner join youtube_ranked as y on n.week_end_date = y.week_end_date and y.seq = 1
left join {{ ref('stg_youtube__channels') }} as c on y.channel_id = c.channel_id
