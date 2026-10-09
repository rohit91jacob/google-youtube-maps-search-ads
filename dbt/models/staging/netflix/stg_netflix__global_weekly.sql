select
    week_end_date,
    {{ add_days('week_end_date', -6) }} as week_start_date,
    category,
    case when category like 'Films%' then 'film' else 'tv' end as content_type,
    case when category like '%Non-English%' then 'non_english' else 'english' end as language,
    show_title,
    season_title,
    coalesce(season_title, show_title) as title,
    weekly_rank,
    weekly_hours_viewed,
    runtime_hours,
    weekly_views,
    cumulative_weeks_in_top_10
from {{ source('raw', 'netflix_global_weekly') }}
