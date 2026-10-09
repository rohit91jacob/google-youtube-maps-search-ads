-- Upload cadence per tracked channel, from every upload the RSS polls have observed.
-- The feed only ever shows a channel's 15 latest uploads, so history grows with polling.
with uploads as (
    select
        channel_id,
        video_id,
        published_at,
        latest_views,
        {{ hours_between('previous_published_at', 'published_at') }} as gap_hours,
        {{ hours_between('published_at', as_of_ts()) }} as age_hours
    from {{ ref('int_youtube__uploads') }}
),

per_channel as (
    select
        channel_id,
        count(*) as uploads_observed,
        min(published_at) as first_observed_upload_at,
        max(published_at) as last_upload_at,
        {{ median('gap_hours') }} as median_gap_hours,
        avg(gap_hours) as mean_gap_hours,
        sum(case when age_hours <= 24 * 7 then 1 else 0 end) as uploads_last_7d,
        sum(case when age_hours <= 24 * 30 then 1 else 0 end) as uploads_last_30d,
        min(age_hours) as hours_since_last_upload,
        {{ median('latest_views') }} as median_views,
        max(latest_views) as max_views,
        sum(latest_views) as total_views_observed
    from uploads
    group by channel_id
)

select
    p.channel_id,
    c.channel_title,
    p.uploads_observed,
    p.first_observed_upload_at,
    p.last_upload_at,
    round(p.median_gap_hours, 2) as median_gap_hours,
    round(p.mean_gap_hours, 2) as mean_gap_hours,
    round({{ safe_div('24 * 7', 'p.mean_gap_hours') }}, 2) as uploads_per_week,
    p.uploads_last_7d,
    p.uploads_last_30d,
    round(p.hours_since_last_upload, 1) as hours_since_last_upload,
    p.median_views,
    p.max_views,
    p.total_views_observed,
    c.subscriber_count,
    c.channel_view_count,
    c.channel_video_count,
    c.last_polled_at
from per_channel as p
left join {{ ref('stg_youtube__channels') }} as c on p.channel_id = c.channel_id
