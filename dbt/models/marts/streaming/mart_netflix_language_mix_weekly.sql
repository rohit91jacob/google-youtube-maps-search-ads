-- Share of the global Top 10's hours (and views) by language and content type, per week.
with lists as (
    select
        week_end_date,
        week_start_date,
        content_type,
        language,
        count(*) as titles,
        sum(weekly_hours_viewed) as hours_viewed,
        sum(weekly_views) as views
    from {{ ref('stg_netflix__global_weekly') }}
    group by week_end_date, week_start_date, content_type, language
)

select
    week_end_date,
    week_start_date,
    content_type,
    language,
    titles,
    hours_viewed,
    views,
    hours_viewed / sum(hours_viewed) over (partition by week_end_date) as share_of_hours,
    {{ safe_div('views', 'sum(views) over (partition by week_end_date)') }} as share_of_views,
    hours_viewed / sum(hours_viewed) over (partition by week_end_date, content_type) as share_of_type_hours
from lists
