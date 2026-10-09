-- How long each title (season-level for TV) stayed in Netflix's global weekly Top 10.
with weeks as (
    select
        category,
        content_type,
        language,
        show_title,
        title,
        min(week_end_date) as first_week_end,
        max(week_end_date) as last_week_end,
        count(*) as weeks_in_top10,
        min(weekly_rank) as best_rank,
        sum(case when weekly_rank = 1 then 1 else 0 end) as weeks_at_number_one,
        sum(weekly_hours_viewed) as total_hours_viewed,
        sum(weekly_views) as total_views,
        max(cumulative_weeks_in_top_10) as netflix_cumulative_weeks
    from {{ ref('stg_netflix__global_weekly') }}
    group by category, content_type, language, show_title, title
),

bounds as (
    select min(week_end_date) as first_published_week from {{ ref('stg_netflix__global_weekly') }}
)

select
    w.category,
    w.content_type,
    w.language,
    w.show_title,
    w.title,
    w.first_week_end,
    w.last_week_end,
    w.weeks_in_top10,
    cast({{ days_between('w.first_week_end', 'w.last_week_end') }} / 7 as {{ dbt.type_int() }}) + 1 as span_weeks,
    w.best_rank,
    w.weeks_at_number_one,
    w.total_hours_viewed,
    w.total_views,
    w.netflix_cumulative_weeks,
    -- Netflix's own running count must match ours, except for titles already charting when the
    -- public files begin (their earlier weeks are not in the file).
    w.first_week_end = b.first_published_week as charting_before_data_start,
    w.netflix_cumulative_weeks = w.weeks_in_top10 as weeks_match_netflix
from weeks as w
cross join bounds as b
