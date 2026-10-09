-- Poll-to-poll churn of the trending-now list per geo. churn_rate = share of the current list
-- that was not on the previous poll's list.
with polls as (
    select
        geo,
        polled_at,
        previous_polled_at
    from {{ ref('stg_trends__polls') }}
    where previous_polled_at is not null
),

current_terms as (
    select
        p.geo,
        p.polled_at,
        count(*) as terms,
        sum(case when prev.term_key is null then 1 else 0 end) as new_terms
    from polls as p
    inner join {{ ref('stg_trends__items') }} as cur
        on p.geo = cur.geo and p.polled_at = cur.polled_at
    left join {{ ref('stg_trends__items') }} as prev
        on
            p.geo = prev.geo
            and p.previous_polled_at = prev.polled_at
            and cur.term_key = prev.term_key
    group by p.geo, p.polled_at
),

dropped as (
    select
        p.geo,
        p.polled_at,
        count(*) as dropped_terms
    from polls as p
    inner join {{ ref('stg_trends__items') }} as prev
        on p.geo = prev.geo and p.previous_polled_at = prev.polled_at
    left join {{ ref('stg_trends__items') }} as cur
        on
            p.geo = cur.geo
            and p.polled_at = cur.polled_at
            and prev.term_key = cur.term_key
    where cur.term_key is null
    group by p.geo, p.polled_at
)

select
    p.geo,
    p.polled_at,
    p.previous_polled_at,
    round({{ hours_between('p.previous_polled_at', 'p.polled_at') }}, 3) as hours_since_previous,
    coalesce(c.terms, 0) as terms,
    coalesce(c.new_terms, 0) as new_terms,
    coalesce(c.terms, 0) - coalesce(c.new_terms, 0) as retained_terms,
    coalesce(d.dropped_terms, 0) as dropped_terms,
    {{ safe_div('coalesce(c.new_terms, 0)', 'c.terms') }} as churn_rate
from polls as p
left join current_terms as c on p.geo = c.geo and p.polled_at = c.polled_at
left join dropped as d on p.geo = d.geo and p.polled_at = d.polled_at
