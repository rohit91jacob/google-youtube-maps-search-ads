"""Static results site built from the marts: `geco report --out site` -> index.html, summary.json, CSVs.

Charts are inline SVG (no JavaScript dependencies). Each chart has a hover tooltip per mark (SVG
<title>) and a table view next to it, and the page follows the OS light/dark preference.
"""

from __future__ import annotations

import csv
import html
import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb

from geco import __version__
from geco.config import Settings
from geco.warehouse import connect

log = logging.getLogger(__name__)

SERIES = ["--series-1", "--series-2", "--series-3", "--series-4"]


def _rows(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    cursor = con.execute(sql)
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, r, strict=True)) for r in cursor.fetchall()]


def _fmt(value: Any, digits: int = 0) -> str:
    if value is None:
        return "–"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _compact(value: float | None) -> str:
    if value is None:
        return "–"
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= div:
            return f"{value / div:,.1f}{suffix}"
    return f"{value:,.0f}"


def _e(value: Any) -> str:
    return html.escape(str(value))


def table(rows: list[dict], columns: list[tuple[str, str]], digits: dict[str, int] | None = None) -> str:
    digits = digits or {}
    head = "".join(f"<th>{_e(label)}</th>" for _, label in columns)
    body = "".join(
        "<tr>"
        + "".join(f"<td>{_e(_fmt(r.get(key), digits.get(key, 0)))}</td>" for key, _ in columns)
        + "</tr>"
        for r in rows
    )
    return f'<div class="tbl"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def hbar(rows: list[tuple[str, float, str]], title: str, value_fmt=_compact) -> str:
    """Single-series horizontal bars: one hue, value labels in text ink, tooltip per bar."""
    if not rows:
        return '<p class="muted">No data yet.</p>'
    width, label_w, bar_h, gap = 640, 170, 18, 8
    vmax = max(v for _, v, _ in rows) or 1
    height = len(rows) * (bar_h + gap) + 4
    plot_w = width - label_w - 70
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{_e(title)}" class="chart">']
    for i, (label, value, tip) in enumerate(rows):
        y = i * (bar_h + gap) + 2
        w = max(2.0, plot_w * value / vmax)
        parts.append(
            f'<g class="mark"><title>{_e(tip)}</title>'
            f'<rect x="{label_w}" y="{y - 3}" width="{plot_w + 60}" height="{bar_h + 6}" fill="transparent"/>'
            f'<text x="{label_w - 8}" y="{y + bar_h * 0.72}" text-anchor="end" class="lbl">{_e(label[:26])}</text>'
            f'<rect x="{label_w}" y="{y}" width="{w:.1f}" height="{bar_h}" rx="4" fill="var(--series-1)"/>'
            f'<text x="{label_w + w + 6:.1f}" y="{y + bar_h * 0.72}" class="val">{_e(value_fmt(value))}</text></g>'
        )
    parts.append("</svg>")
    return "".join(parts)


def stacked_shares(weeks: list[str], series: dict[str, list[float]], title: str) -> str:
    """100% stacked columns, fixed categorical order, 2px surface gaps, legend + tooltip per segment."""
    if not weeks:
        return '<p class="muted">No data yet.</p>'
    width, height, top, bottom, left = 640, 230, 10, 34, 34
    plot_h = height - top - bottom
    col_w = (width - left - 10) / len(weeks)
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{_e(title)}" class="chart">']
    for tick in (0, 0.5, 1):
        y = top + plot_h * (1 - tick)
        parts.append(f'<line x1="{left}" x2="{width - 10}" y1="{y}" y2="{y}" class="grid"/>')
        parts.append(
            f'<text x="{left - 4}" y="{y + 4}" text-anchor="end" class="axis">{int(tick * 100)}%</text>'
        )
    names = list(series)
    for i, week in enumerate(weeks):
        x = left + i * col_w + 1
        y = top + plot_h
        for j, name in enumerate(names):
            share = series[name][i]
            h = plot_h * share
            y -= h
            parts.append(
                f'<g class="mark"><title>{_e(week)} · {_e(name)}: {share:.1%}</title>'
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{col_w - 2:.1f}" height="{max(0.0, h - 2):.1f}" '
                f'fill="var({SERIES[j % len(SERIES)]})"/></g>'
            )
        if i % max(1, len(weeks) // 6) == 0:
            parts.append(
                f'<text x="{x + col_w / 2:.1f}" y="{height - 16}" text-anchor="middle" class="axis">{_e(week[5:])}</text>'
            )
    parts.append("</svg>")
    legend = "".join(
        f'<span class="key"><i style="background:var({SERIES[j % len(SERIES)]})"></i>{_e(n)}</span>'
        for j, n in enumerate(names)
    )
    return f'<div class="legend">{legend}</div>' + "".join(parts)


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        if not rows:
            return
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


EXPORTS = {
    "trends_geo_summary": "select * from marts.mart_trends_geo_summary order by geo",
    "trends_churn_by_geo": "select * from marts.mart_trends_churn_by_geo order by geo, polled_at",
    "political_ad_spend_region_week": "select * from marts.mart_political_ad_spend_region_week order by region_code, week_start_date",
    "political_ad_top_advertisers": "select * from marts.mart_political_ad_top_advertisers order by region_code, advertiser_rank",
    "political_ad_spend_us_state": "select * from marts.mart_political_ad_spend_us_state order by spend_usd desc",
    "netflix_language_mix_weekly": "select * from marts.mart_netflix_language_mix_weekly order by week_end_date, content_type, language",
    "netflix_title_longevity_top": "select * from marts.mart_netflix_title_longevity order by weeks_in_top10 desc, total_hours_viewed desc limit 500",
    "osm_city_profile": "select * from marts.mart_osm_city_profile order by amenities_per_km2 desc",
    "osm_amenity_density": "select * from marts.mart_osm_amenity_density order by city, category, amenity",
    "youtube_upload_cadence": "select * from marts.mart_youtube_upload_cadence order by channel_title",
    "video_attention_weekly": "select * from marts.mart_video_attention_weekly order by week_end_date",
}


def build_site(db_path: Path, out: Path, settings: Settings) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as con:
        q = lambda sql: _rows(con, sql)  # noqa: E731
        geo = q("select * from marts.mart_trends_geo_summary order by geo")
        latest_terms = q("""
            select i.geo, i.trend_rank, i.term, i.approx_traffic
            from staging.stg_trends__items i
            join (select geo, max(polled_at) p from staging.stg_trends__polls group by 1) l
              on i.geo = l.geo and i.polled_at = l.p
            where i.trend_rank <= 3 order by i.geo, i.trend_rank""")
        longest_terms = q("""
            select geo, term, polls_seen, observed_hours, best_rank, still_trending
            from marts.mart_trends_term_longevity order by observed_hours desc, polls_seen desc, geo, term limit 10""")
        events = q("""select event_type, count(*) n from marts.fct_trends_events group by 1 order by 1""")
        ads_updated = q("select max(report_data_updated_pt) u from raw.ads_report_updated")[0]["u"]
        us_weeks = q("""
            select week_start_date, spend, advertisers_with_spend, top_advertiser_name
            from marts.mart_political_ad_spend_region_week where region_code = 'US'
            order by week_start_date desc limit 12""")[::-1]
        us_top = q("""select advertiser_name, total_spend, share_of_region_spend, weeks_with_spend, last_week_start
                      from marts.mart_political_ad_top_advertisers where region_code = 'US' order by advertiser_rank limit 10""")
        regions = q("""
            select region_code, region_name, currency, sum(spend) total_spend, count(distinct advertiser_id) advertisers,
                   min(week_start_date) first_week, max(week_start_date) last_week
            from marts.fct_political_ad_spend_weekly group by 1, 2, 3 order by advertisers desc""")
        states = q(
            "select subdivision, spend_usd, share_of_us_spend, top_advertiser_name from marts.mart_political_ad_spend_us_state order by spend_usd desc limit 10"
        )
        nf_latest = q("""
            select week_end_date, category, weekly_rank, title, weekly_views, weekly_hours_viewed, cumulative_weeks_in_top_10
            from staging.stg_netflix__global_weekly
            where week_end_date = (select max(week_end_date) from staging.stg_netflix__global_weekly) and weekly_rank <= 3
            order by category, weekly_rank""")
        mix = q("""
            select week_end_date, content_type, language, share_of_hours from marts.mart_netflix_language_mix_weekly
            where week_end_date > (select max(week_end_date) from marts.mart_netflix_language_mix_weekly) - interval 182 day
            order by week_end_date""")
        nf_long = q("""select title, category, weeks_in_top10, best_rank, weeks_at_number_one, total_views, weeks_match_netflix
                       from marts.mart_netflix_title_longevity order by weeks_in_top10 desc, total_hours_viewed desc limit 10""")
        nf_match = q("""select count(*) n, sum(case when weeks_match_netflix then 1 else 0 end) as matched,
                               sum(case when not weeks_match_netflix and not charting_before_data_start then 1 else 0 end) as unexplained
                        from marts.mart_netflix_title_longevity""")[0]
        cities = q("select * from marts.mart_osm_city_profile order by amenities_per_km2 desc")
        yt = q("select * from marts.mart_youtube_upload_cadence order by uploads_per_week desc nulls last")
        attention = q("select * from marts.mart_video_attention_weekly order by week_end_date desc limit 8")
        counts = {
            t: q(f"select count(*) n from raw.{t}")[0]["n"]
            for t in (
                "trends_items",
                "ads_weekly_spend",
                "netflix_global_weekly",
                "netflix_country_weekly",
                "osm_amenity_counts",
                "youtube_videos",
            )
        }
        for name, sql in EXPORTS.items():
            _write_csv(out / "data" / f"{name}.csv", q(sql))

    generated = settings.now()
    mix_weeks = sorted({str(r["week_end_date"]) for r in mix})
    mix_names = {
        ("film", "english"): "Films (English)",
        ("film", "non_english"): "Films (Non-English)",
        ("tv", "english"): "TV (English)",
        ("tv", "non_english"): "TV (Non-English)",
    }
    mix_series = {label: [0.0] * len(mix_weeks) for label in mix_names.values()}
    for r in mix:
        mix_series[mix_names[(r["content_type"], r["language"])]][
            mix_weeks.index(str(r["week_end_date"]))
        ] = r["share_of_hours"]

    headline = {
        "generated_at": generated.isoformat(),
        "trends_polls": sum(r["polls"] for r in geo),
        "trends_geos": len(geo),
        "trends_events": {r["event_type"]: r["n"] for r in events},
        "political_ads_report_updated_pt": ads_updated,
        "political_ads_us_spend_usd_weekly_total": next(
            (r["total_spend"] for r in regions if r["region_code"] == "US"), None
        ),
        "political_ads_advertisers": sum(r["advertisers"] for r in regions),
        "netflix_latest_week": str(nf_latest[0]["week_end_date"]) if nf_latest else None,
        "netflix_titles": nf_match["n"],
        "netflix_weeks_match_published": nf_match["matched"],
        "osm_cities": len(cities),
        "youtube_channels": len(yt),
        "raw_rows": counts,
    }
    summary = {"headline": headline, "version": __version__}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    sections = []
    churn_rows = [
        (
            f"{r['geo']} · {r['geo_name'] or ''}",
            r["avg_churn_rate"] or 0.0,
            f"{r['geo']}: average churn {_fmt(r['avg_churn_rate'], 3)} over {r['polls']} polls",
        )
        for r in geo
        if r["avg_churn_rate"] is not None
    ]
    sections.append(f"""
<section id="search"><h2>Search · Google Trends trending now</h2>
<p>{headline["trends_polls"]} polls of the trending-now RSS feed across {len(geo)} geos. Events through Pub/Sub:
{_e(", ".join(f"{k} {v:,}" for k, v in headline["trends_events"].items()) or "none")}.
Churn = share of a poll's list that was not on the previous poll's list.</p>
<div class="grid2"><div><h3>Average poll-to-poll churn</h3>{hbar(churn_rows, "Average churn by geo", lambda v: f"{v:.0%}")}</div>
<div><h3>Per geo</h3>{table(geo, [("geo", "Geo"), ("polls", "Polls"), ("distinct_terms", "Terms"), ("avg_churn_rate", "Avg churn"), ("avg_hours_between_polls", "Hours between polls"), ("avg_completed_observed_hours", "Avg observed h (completed)")], {"avg_churn_rate": 3, "avg_hours_between_polls": 1, "avg_completed_observed_hours": 1})}</div></div>
<h3>Top 3 now</h3>{table(latest_terms, [("geo", "Geo"), ("trend_rank", "#"), ("term", "Search"), ("approx_traffic", "Approx. searches")])}
<h3>Longest observed on the list</h3>{table(longest_terms, [("geo", "Geo"), ("term", "Search"), ("polls_seen", "Polls"), ("observed_hours", "Observed h"), ("best_rank", "Best rank"), ("still_trending", "Still trending")], {"observed_hours": 1})}
</section>""")

    sections.append(f"""
<section id="ads"><h2>Ads · Google political ads transparency</h2>
<p>Report data updated {_e(ads_updated)} (Pacific time). Spend is published by Google in each region's own currency,
rounded to 100 units; weeks run Sunday to Saturday.</p>
<div class="grid2"><div><h3>US weekly spend, last 12 weeks (USD)</h3>{hbar([(str(r["week_start_date"]), r["spend"], f"week of {r['week_start_date']}: ${r['spend']:,} by {r['advertisers_with_spend']:,} advertisers; top: {r['top_advertiser_name']}") for r in us_weeks], "US weekly political ad spend")}</div>
<div><h3>Top US advertisers (lifetime)</h3>{table(us_top, [("advertiser_name", "Advertiser"), ("total_spend", "USD"), ("share_of_region_spend", "Share"), ("last_week_start", "Last week")], {"share_of_region_spend": 3})}</div></div>
<div class="grid2"><div><h3>Regions (native currency)</h3>{table(regions, [("region_name", "Region"), ("currency", "Cur."), ("total_spend", "Spend"), ("advertisers", "Advertisers"), ("first_week", "From"), ("last_week", "To")])}</div>
<div><h3>Top US states (lifetime, USD)</h3>{table(states, [("subdivision", "State"), ("spend_usd", "USD"), ("share_of_us_spend", "Share"), ("top_advertiser_name", "Top advertiser")], {"share_of_us_spend": 3})}</div></div>
</section>""")

    sections.append(f"""
<section id="streaming"><h2>Streaming · Netflix global Top 10</h2>
<p>Latest week ending {_e(headline["netflix_latest_week"])}. Netflix's own cumulative-weeks counter matches the
pipeline's recount for {nf_match["matched"]:,} of {nf_match["n"]:,} titles; {nf_match["n"] - nf_match["matched"]:,} differ. On 9 Oct 2026 all
differences were different films sharing a title, which the public files cannot tell apart (see docs/verification.md).</p>
<h3>Share of Top 10 hours by list, last 26 weeks</h3>{stacked_shares(mix_weeks, mix_series, "Netflix Top 10 hours by language and type")}
<div class="grid2"><div><h3>This week's top 3 per list</h3>{table(nf_latest, [("category", "List"), ("weekly_rank", "#"), ("title", "Title"), ("weekly_views", "Views"), ("cumulative_weeks_in_top_10", "Weeks")])}</div>
<div><h3>Longest in the Top 10</h3>{table(nf_long, [("title", "Title"), ("category", "List"), ("weeks_in_top10", "Weeks"), ("best_rank", "Best"), ("weeks_at_number_one", "Weeks #1")])}</div></div>
</section>""")

    sections.append(f"""
<section id="maps"><h2>Maps · OpenStreetMap amenity density</h2>
<p>Features tagged <code>amenity=*</code> (24 values in 6 groups) inside a {2 * settings.osm_box_half_size_km:.0f} km × {2 * settings.osm_box_half_size_km:.0f} km box centred on
a fixed point in each city. Boxes include water and parks; OSM completeness varies by city, so density measures mapping as much as the city.</p>
<div class="grid2"><div><h3>Amenities per km²</h3>{hbar([(r["city_name"], r["amenities_per_km2"], f"{r['city_name']}: {r['total_amenities']:,} amenities, {r['amenities_per_km2']:.1f}/km²") for r in cities], "Amenities per km2", lambda v: f"{v:.1f}")}</div>
<div><h3>By group (per km²)</h3>{table(cities, [("city_name", "City"), ("food_drink_per_km2", "Food"), ("health_per_km2", "Health"), ("education_per_km2", "Educ."), ("transport_per_km2", "Transp."), ("finance_per_km2", "Finance"), ("cafes_per_restaurant", "Cafés/rest.")], dict.fromkeys(("food_drink_per_km2", "health_per_km2", "education_per_km2", "transport_per_km2", "finance_per_km2"), 1) | {"cafes_per_restaurant": 2})}</div></div>
</section>""")

    sections.append(f"""
<section id="video"><h2>Video · YouTube upload cadence and YouTube vs Netflix</h2>
<p>{len(yt)} official channels via their public RSS feeds (15 latest uploads per poll; history grows with every poll).
{"The YouTube Data API source ran." if any(r.get("subscriber_count") for r in yt) else "The optional YouTube Data API source is off (no key), so channel totals are empty."}</p>
{table(yt, [("channel_title", "Channel"), ("uploads_observed", "Uploads seen"), ("uploads_per_week", "Per week"), ("median_gap_hours", "Median gap h"), ("uploads_last_7d", "Last 7d"), ("median_views", "Median views"), ("max_views", "Max views")], {"uploads_per_week": 1, "median_gap_hours": 1})}
<h3>Same week, different measures</h3>
<p class="muted">Netflix "views" = hours viewed ÷ runtime within the week. YouTube = lifetime views to date of the tracked channels' uploads published that week.</p>
{table(attention, [("week_end_date", "Week to"), ("netflix_top_title", "Netflix #1 by views"), ("netflix_top_weekly_views", "Netflix views"), ("youtube_top_video_title", "Top tracked YouTube upload"), ("youtube_top_channel", "Channel"), ("youtube_top_video_views_to_date", "YouTube views to date")])}
</section>""")

    page = _PAGE.format(
        generated=_e(_fmt(generated)),
        version=_e(__version__),
        sections="".join(sections),
        rows=_e(", ".join(f"{k} {v:,}" for k, v in counts.items())),
    )
    (out / "index.html").write_text(page, encoding="utf-8")
    log.info("site written to %s", out)
    return summary


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Google ecosystem analytics: Search, Ads, YouTube vs Netflix, Maps</title>
<style>
.viz-root {{ color-scheme: light; --surface-1:#fcfcfb; --surface-2:#f3f2ef; --text-primary:#0b0b0b; --text-secondary:#52514e;
  --grid:#e2e1dc; --series-1:#2a78d6; --series-2:#eb6834; --series-3:#1baf7a; --series-4:#eda100; }}
@media (prefers-color-scheme: dark) {{ .viz-root {{ color-scheme: dark; --surface-1:#1a1a19; --surface-2:#242422;
  --text-primary:#ffffff; --text-secondary:#c3c2b7; --grid:#383835; --series-1:#3987e5; --series-2:#d95926; --series-3:#199e70; --series-4:#c98500; }} }}
body {{ margin:0; font:15px/1.5 system-ui, -apple-system, Segoe UI, sans-serif; }}
.viz-root {{ background:var(--surface-1); color:var(--text-primary); min-height:100vh; }}
main {{ max-width:1100px; margin:0 auto; padding:24px; }}
h1 {{ font-size:26px; margin:0 0 4px; }} h2 {{ margin-top:40px; border-bottom:1px solid var(--grid); padding-bottom:4px; }}
h3 {{ font-size:15px; color:var(--text-secondary); margin:18px 0 6px; }}
p, .muted {{ color:var(--text-secondary); }} code {{ background:var(--surface-2); padding:0 4px; border-radius:4px; }}
nav a {{ margin-right:14px; color:var(--series-1); }}
.grid2 {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(420px, 1fr)); gap:24px; }}
.tbl {{ overflow-x:auto; }} table {{ border-collapse:collapse; width:100%; font-size:13px; }}
th, td {{ text-align:left; padding:4px 8px; border-bottom:1px solid var(--grid); }} th {{ color:var(--text-secondary); font-weight:600; }}
.chart {{ width:100%; height:auto; }} .chart .lbl, .chart .val {{ font-size:12px; fill:var(--text-primary); }}
.chart .axis {{ font-size:11px; fill:var(--text-secondary); }} .chart .grid {{ stroke:var(--grid); stroke-width:1; }}
.chart .mark:hover rect:not([fill=transparent]) {{ opacity:.8; }}
.legend {{ display:flex; gap:16px; flex-wrap:wrap; font-size:13px; }} .key i {{ display:inline-block; width:12px; height:12px; border-radius:3px; margin-right:6px; vertical-align:-1px; }}
footer {{ margin-top:48px; font-size:13px; color:var(--text-secondary); border-top:1px solid var(--grid); padding-top:12px; }}
</style></head>
<body><div class="viz-root"><main>
<h1>Google ecosystem analytics</h1>
<p>Search trends, political ad spend, YouTube vs Netflix streaming and Maps POI density, built from real public data by
<a href="https://github.com/rohit91jacob/google-youtube-maps-search-ads">google-youtube-maps-search-ads</a> v{version}.
Generated {generated}. Runs on an emulated GCP stack (filesystem for GCS, DuckDB for BigQuery, a local Pub/Sub emulator); nothing here is simulated data.</p>
<nav><a href="#search">Search</a><a href="#ads">Ads</a><a href="#streaming">Streaming</a><a href="#maps">Maps</a><a href="#video">Video</a><a href="summary.json">summary.json</a><a href="data/">CSV</a></nav>
{sections}
<footer>
<p>Raw rows: {rows}.</p>
<p>Sources: Google Trends trending-now RSS (Google Terms of Service); Political Advertising on Google transparency report bundle;
Netflix Top 10 (netflix.com/tudum/top10); YouTube channel RSS feeds (YouTube Terms of Service);
map data © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>, available under the Open Database License (ODbL) 1.0, via the Overpass API.
This project has no affiliation with Google, YouTube, Netflix or the OpenStreetMap Foundation. See DATA_LICENSE.md.</p>
</footer></main></div></body></html>
"""
