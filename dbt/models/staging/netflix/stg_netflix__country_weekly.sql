select
    country_name,
    country_iso2,
    week_end_date,
    category,
    case when category like 'Films%' then 'film' else 'tv' end as content_type,
    show_title,
    season_title,
    coalesce(season_title, show_title) as title,
    weekly_rank,
    cumulative_weeks_in_top_10
from {{ source('raw', 'netflix_country_weekly') }}
