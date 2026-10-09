select
    category,
    case when category like 'Films%' then 'film' else 'tv' end as content_type,
    case when category like '%Non-English%' then 'non_english' else 'english' end as language,
    show_title,
    season_title,
    coalesce(season_title, show_title) as title,
    rank as list_rank,
    hours_viewed_first_91_days,
    runtime_hours,
    views_first_91_days
from {{ source('raw', 'netflix_most_popular') }}
