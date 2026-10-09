"""Reconcile pipeline outputs with figures the sources publish themselves (docs/verification.md).

Runs against a warehouse built from REAL downloads (not fixtures):

    GECO_DATA_DIR=~/projects/_data/google-youtube-maps-search-ads/live \
        uv run python scripts/verify_published.py [--overpass]

--overpass also re-counts two London amenities by downloading the element ids (a second,
independent counting method) and compares them with the stored `out count` result.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys

from geco.config import Settings
from geco.warehouse import connect


def rows(con, sql: str, params: list | None = None) -> list[tuple]:
    return con.execute(sql, params or []).fetchall()


def netflix(con) -> None:
    print("## Netflix\n")
    n, matched = rows(
        con,
        """select count(*), sum(case when weeks_match_netflix then 1 else 0 end)
                              from marts.mart_netflix_title_longevity""",
    )[0]
    print(
        f"* cumulative_weeks_in_top_10 (published by Netflix) = pipeline recount for {matched:,} of {n:,} titles"
    )
    for title, category, ours, theirs, first in rows(
        con,
        """
            select title, category, weeks_in_top10, netflix_cumulative_weeks, first_week_end
            from marts.mart_netflix_title_longevity where not weeks_match_netflix order by title""",
    ):
        runs = rows(
            con,
            """select count(*) from (select week_end_date, lag(week_end_date) over (order by week_end_date) p
                              from staging.stg_netflix__global_weekly where title = ? and category = ?)
               where p is null or week_end_date - p > 7""",
            [title, category],
        )[0][0]
        print(
            f"  * mismatch: {title} ({category}) ours {ours} vs Netflix {theirs}, first charted {first}, {runs} separate runs"
        )
    pairs = rows(
        con,
        """
        with w as (select title, category, week_end_date, weekly_hours_viewed,
                          min(week_end_date) over (partition by title, category) as first_week
                   from staging.stg_netflix__global_weekly)
        select mp.title, mp.category, mp.hours_viewed_first_91_days,
               sum(case when w.week_end_date < w.first_week + interval 91 day then w.weekly_hours_viewed end) as h13
        from staging.stg_netflix__most_popular mp
        left join w on w.title = mp.title and w.category = mp.category
        where w.first_week > (select min(week_end_date) from staging.stg_netflix__global_weekly)
        group by all""",
    )
    ratios = [h / pub for _, _, pub, h in pairs if h]
    over = [(t, c) for t, c, pub, h in pairs if h and h > pub]
    print(
        f"* most-popular list (40 titles, Netflix's published hours in the first 91 days): {len(ratios)} titles debuted after "
        f"the weekly files start; Top-10 hours in their first 13 weeks = {min(ratios):.1%} to {max(ratios):.1%} "
        f"of the published 91-day hours (median {statistics.median(ratios):.1%}); {len(over)} exceed it"
    )
    for t, c, pub, h in sorted(pairs, key=lambda r: -r[2])[:5]:
        if h:
            print(f"  * {t} ({c}): published {pub:,} h, weekly Top-10 sum {h:,} h ({h / pub:.1%})")
    print()


def ads(con) -> None:
    print("## Political ads\n")
    totals = {
        "advertiser-weekly-spend (sum of weeks)": rows(
            con, "select sum(spend) from raw.ads_weekly_spend where currency='USD'"
        )[0][0],
        "advertiser-stats (Google's lifetime totals)": rows(
            con, "select sum(spend) from raw.ads_advertiser_spend_totals where currency='USD'"
        )[0][0],
        "geo-spend (by state/district)": rows(
            con, "select sum(spend) from raw.ads_geo_spend where currency='USD'"
        )[0][0],
        "advertiser-geo-spend (advertiser x state)": rows(
            con, "select sum(spend) from raw.ads_advertiser_geo_spend where currency='USD'"
        )[0][0],
    }
    base = totals["advertiser-stats (Google's lifetime totals)"]
    for name, value in totals.items():
        print(f"* USD {name}: ${value:,} ({value / base - 1:+.2%} vs advertiser-stats)")
    per = rows(
        con,
        """
        with w as (select advertiser_id, currency, sum(spend) s, count(*) n from raw.ads_weekly_spend group by all)
        select count(*), sum(case when w.s = t.spend then 1 else 0 end),
               sum(case when abs(w.s - t.spend) <= 100 * w.n then 1 else 0 end)
        from w join raw.ads_advertiser_spend_totals t using (advertiser_id, currency)""",
    )[0]
    print(
        f"* per advertiser x currency: {per[0]:,} pairs; {per[1]:,} identical; {per[2]:,} within 100 x (weeks with spend), "
        "i.e. within weekly rounding to 100 units"
    )
    for name, ours, theirs in rows(
        con,
        """
            select d.advertiser_name, m.total_spend, t.spend
            from marts.mart_political_ad_top_advertisers m
            join raw.ads_advertiser_spend_totals t on t.advertiser_id = m.advertiser_id and t.currency = 'USD'
            join marts.dim_political_advertiser d on d.advertiser_id = m.advertiser_id
            where m.region_code = 'US' and m.advertiser_rank <= 5 order by m.advertiser_rank""",
    ):
        print(f"  * {name}: weekly sum ${ours:,} vs published total ${theirs:,} (diff ${ours - theirs:+,})")
    for cur, w, t in rows(
        con,
        """
            with w as (select currency, sum(spend) s from raw.ads_weekly_spend group by 1),
                 t as (select currency, sum(spend) s from raw.ads_advertiser_spend_totals group by 1)
            select currency, w.s, t.s from w join t using (currency) order by currency""",
    ):
        flag = "" if abs(w / t - 1) <= 0.02 else "  <- outside 2%"
        print(f"  * {cur}: weekly {w:,} vs totals {t:,} ({w / t - 1:+.2%}){flag}")
    print()


def overpass(con, settings: Settings) -> None:
    from geco.http import HttpClient
    from geco.sources.osm import CITIES, bbox

    print("## OpenStreetMap (two counting methods)\n")
    s, w, n, e = bbox(CITIES[0], settings.osm_box_half_size_km)
    http = HttpClient(settings)
    for amenity in ("cafe", "pharmacy"):
        query = f'[out:json][timeout:120][bbox:{s},{w},{n},{e}];nwr["amenity"="{amenity}"];out ids;'
        for endpoint in settings.overpass_endpoints * 3:
            try:
                data = json.loads(http.post_form(endpoint, {"data": query}).read())
                break
            except Exception as exc:  # busy servers: try the next one
                print(f"  ({endpoint}: {str(exc)[:60]})", file=sys.stderr)
        else:
            print(f"* {amenity}: Overpass unavailable")
            continue
        ids = {(el["type"], el["id"]) for el in data["elements"]}
        stored = rows(
            con,
            "select total, fetched_at from raw.osm_amenity_counts where city = 'london' and amenity = ? "
            "order by fetched_at desc limit 1",
            [amenity],
        )[0]
        print(
            f"* London {amenity}: {len(ids):,} distinct element ids downloaded vs stored `out count` {stored[0]:,} "
            f"(counted {stored[1]:%Y-%m-%d %H:%M} UTC; OSM base {data['osm3s']['timestamp_osm_base']})"
        )
    print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--overpass", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    with connect(settings.resolved_warehouse_path) as con:
        netflix(con)
        ads(con)
        if args.overpass:
            overpass(con, settings)


if __name__ == "__main__":
    main()
