-- In how many countries' weekly Top 10 each title appeared (recent weeks only).
with recent as (
    select max(week_end_date) as latest_week from {{ ref('stg_netflix__country_weekly') }}
)

select
    c.week_end_date,
    c.content_type,
    c.show_title,
    c.title,
    count(distinct c.country_iso2) as countries_in_top10,
    count(distinct case when c.weekly_rank = 1 then c.country_iso2 end) as countries_at_number_one,
    min(c.weekly_rank) as best_rank
from {{ ref('stg_netflix__country_weekly') }} as c
cross join recent as r
where c.week_end_date > {{ add_days('r.latest_week', -7 * var('netflix_country_reach_weeks')) }}
group by c.week_end_date, c.content_type, c.show_title, c.title
