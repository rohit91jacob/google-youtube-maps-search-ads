"""Generate the SYNTHETIC test fixtures under tests/fixtures/synthetic/ (deterministic, seeded).

Google Trends, the political-ads bundle, Netflix Top 10 and YouTube feeds are real public data, but
their terms do not grant redistribution, so this repository does not commit samples of them.
Instead this script writes files with the *exact shape* of each source (same file formats, XML
namespaces, CSV headers and xlsx columns as the real downloads on 9 Oct 2026) filled with made-up
values. Every synthetic name says so. OpenStreetMap fixtures are real (ODbL) and live elsewhere.

    uv run python scripts/make_synthetic_fixtures.py
"""

from __future__ import annotations

import csv
import io
import json
import random
import zipfile
from datetime import UTC, date, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

import polars as pl

SEED = 20261009
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "synthetic"
GEOS = ["US", "GB", "IN"]
CURRENCIES = [
    "USD", "EUR", "INR", "BGN", "CZK", "DKK", "HUF", "PLN", "RON", "SEK",
    "GBP", "NZD", "ILS", "AUD", "TWD", "BRL", "ARS", "ZAR", "CLP", "MXN",
]  # fmt: skip
CHANNELS = ["UCsyntheticFixture00000001", "UCsyntheticFixture00000002", "UCsyntheticFixture00000003"]
NOTICE = "SYNTHETIC FIXTURE - made-up values in the real source's shape; not real data."


def trends_feed(geo: str, terms: list[tuple[str, int]], poll_at: datetime) -> str:
    items = []
    for i, (term, traffic) in enumerate(terms):
        started = poll_at - timedelta(minutes=10 * (i + 1))
        items.append(f"""		<item>
			<title>{escape(term)}</title>
			<ht:approx_traffic>{traffic}+</ht:approx_traffic>
			<description/>
			<link>https://trends.google.com/trending/rss?geo={geo}</link>
			<pubDate>{format_datetime(started)}</pubDate>
			<ht:picture>https://example.invalid/synthetic.png</ht:picture>
			<ht:picture_source>Synthetic News</ht:picture_source>
			<ht:news_item>
				<ht:news_item_title>{escape(term)}: synthetic headline</ht:news_item_title>
				<ht:news_item_snippet/>
				<ht:news_item_url>https://example.invalid/{geo.lower()}/{i}</ht:news_item_url>
				<ht:news_item_picture>https://example.invalid/synthetic.png</ht:news_item_picture>
				<ht:news_item_source>Synthetic News</ht:news_item_source>
			</ht:news_item>
		</item>""")
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<!-- {NOTICE} -->
<rss xmlns:atom="http://www.w3.org/2005/Atom" xmlns:ht="https://trends.google.com/trending/rss" version="2.0">
	<channel>
		<title>Daily Search Trends</title>
		<description>{NOTICE}</description>
		<link>https://trends.google.com/trending/rss?geo={geo}</link>
		<atom:link href="https://trends.google.com/trending/rss?geo={geo}" rel="self" type="application/rss+xml"/>
{chr(10).join(items)}
	</channel>
</rss>
"""


def write_trends(rng: random.Random) -> None:
    base = datetime(2026, 10, 5, 6, 0, tzinfo=UTC)
    pools = {geo: [f"synthetic {geo.lower()} term {n:02d}" for n in range(1, 31)] for geo in GEOS}
    current = {geo: pools[geo][:10] for geo in GEOS}
    for poll in range(3):
        poll_at = base + timedelta(hours=3 * poll)
        folder = OUT / "trends" / f"poll{poll + 1}"
        folder.mkdir(parents=True, exist_ok=True)
        for geo in GEOS:
            if poll > 0:  # replace 3-5 terms per poll, deterministically
                kept = set(rng.sample(current[geo], 10 - rng.randint(3, 5)))
                keep = [t for t in current[geo] if t in kept]
                fresh = [t for t in pools[geo] if t not in current[geo] and t not in keep]
                current[geo] = (fresh[: 10 - len(keep)] + keep)[:10]
            terms = [(t, rng.choice([100, 200, 500, 1000, 2000])) for t in current[geo]]
            (folder / f"{geo}.xml").write_text(trends_feed(geo, terms, poll_at), encoding="utf-8")


def _csv(rows: list[list], header: list[str]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode()


def _spend(currency: str, amount: int) -> list[int]:
    return [amount if c == currency else 0 for c in CURRENCIES]


def write_political_ads(rng: random.Random) -> None:
    spend_cols = [f"Spend_{c}" for c in CURRENCIES]
    advertisers = [
        ("AR00000000000000000001", "SYNTHETIC COMMITTEE ALPHA", "US", "USD"),
        ("AR00000000000000000002", "SYNTHETIC COMMITTEE BRAVO", "US", "USD"),
        ("AR00000000000000000003", "SYNTHETIC PAC CHARLIE", "US", "USD"),
        ("AR00000000000000000004", "SYNTHETIC PARTY DELTA", "IN", "INR"),
        ("AR00000000000000000005", "SYNTHETIC PARTY ECHO", "IN", "INR"),
        ("AR00000000000000000006", "SYNTHETIC CANDIDATE FOXTROT", "BR", "BRL"),
    ]
    first_sunday = date(2026, 8, 9)
    weekly, totals = [], {}
    for adv_id, name, _region, currency in advertisers:
        for week in range(8):
            if rng.random() < 0.2:
                continue
            amount = rng.randint(1, 400) * 100
            weekly.append(
                [
                    adv_id,
                    name,
                    "",
                    (first_sunday + timedelta(weeks=week)).isoformat(),
                    *_spend(currency, amount),
                ]
            )
            totals[adv_id] = totals.get(adv_id, 0) + amount
    stats = [
        [adv_id, name, "", region, "", rng.randint(1, 50), *_spend(currency, totals.get(adv_id, 0))]
        for adv_id, name, region, currency in advertisers
    ]
    states = ["WI", "PA", "GA", "AZ"]
    adv_geo = []
    for adv_id, name, region, _currency in advertisers:
        if region != "US":
            continue
        remaining = totals.get(adv_id, 0)
        for i, state in enumerate(states):
            amount = remaining if i == len(states) - 1 else (remaining // 400) * 100
            remaining -= amount
            adv_geo.append([adv_id, name, "US", state, *_spend("USD", amount)])
    geo = [
        ["US", f"US-{s}", f"{s}-1 (2026 redistricting)", *_spend("USD", rng.randint(10, 900) * 100)]
        for s in states
    ]
    geo.append(["IN", "IN-KA", "", *_spend("INR", rng.randint(10, 900) * 100)])
    members = {
        "google-political-ads-updated.csv": b"Report_Data_Updated_Time (PT)\n2026-10-04 23:58:04\n",
        "google-political-ads-advertiser-stats.csv": _csv(
            stats,
            [
                "Advertiser_ID",
                "Advertiser_Name",
                "Public_IDs_List",
                "Regions",
                "Elections",
                "Total_Creatives",
                *spend_cols,
            ],
        ),
        "google-political-ads-advertiser-weekly-spend.csv": _csv(
            weekly, ["Advertiser_ID", "Advertiser_Name", "Election_Cycle", "Week_Start_Date", *spend_cols]
        ),
        "google-political-ads-advertiser-geo-spend.csv": _csv(
            adv_geo,
            ["Advertiser_ID", "Advertiser_Name", "Country", "Country_Subdivision_Primary", *spend_cols],
        ),
        "google-political-ads-geo-spend.csv": _csv(
            geo, ["Country", "Country_Subdivision_Primary", "Country_Subdivision_Secondary", *spend_cols]
        ),
        "README.txt": f"{NOTICE}\n".encode(),
    }
    folder = OUT / "political_ads"
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        folder / "google-political-ads-transparency-bundle.zip", "w", zipfile.ZIP_DEFLATED
    ) as bundle:
        for name, data in members.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 5, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, data)


# xlsxwriter stamps the creation time into docProps/core.xml and the zip entries; pin it so that
# regenerating the fixtures is byte-identical.
XLSX_CREATED = datetime(2026, 10, 9, 12, 42, tzinfo=UTC)


def _write_xlsx(frame: pl.DataFrame, path: Path, worksheet: str) -> None:
    import xlsxwriter

    workbook = xlsxwriter.Workbook(str(path))
    workbook.set_properties({"created": XLSX_CREATED.replace(tzinfo=None)})
    frame.write_excel(workbook=workbook, worksheet=worksheet, autofit=False)
    workbook.close()


def write_netflix(rng: random.Random) -> None:
    folder = OUT / "netflix"
    folder.mkdir(parents=True, exist_ok=True)
    lists = ["Films (English)", "Films (Non-English)", "TV (English)", "TV (Non-English)"]
    weeks = [date(2026, 10, 4) - timedelta(weeks=w) for w in range(6)][::-1]
    prefix = {
        lists[0]: "Synthetic Film EN",
        lists[1]: "Synthetic Film XX",
        lists[2]: "Synthetic Show EN",
        lists[3]: "Synthetic Show XX",
    }
    pool = {c: [f"{prefix[c]} {i:02d}" for i in range(1, 25)] for c in lists}
    rows, cumulative, charting = [], {}, {c: pool[c][:10] for c in lists}
    for week in weeks:
        for category in lists:
            if week != weeks[0]:
                keep = charting[category][:7]
                new = [t for t in pool[category] if t not in charting[category]][:3]
                charting[category] = keep + new
                pool[category] = [t for t in pool[category] if t not in new] + new
            for rank, show in enumerate(charting[category], start=1):
                is_tv = category.startswith("TV")
                season = f"{show}: Season 1" if is_tv else "N/A"
                key = (category, season if is_tv else show)
                cumulative[key] = cumulative.get(key, 0) + 1
                runtime = round(rng.uniform(1.3, 9.0), 4)
                views = (11 - rank) * 1_000_000 + rng.randint(0, 9) * 100_000
                rows.append(
                    {
                        "week": week.isoformat(),
                        "category": category,
                        "weekly_rank": rank,
                        "show_title": show,
                        "season_title": season,
                        "weekly_hours_viewed": int(views * runtime // 100_000 * 100_000),
                        "runtime": runtime,
                        "weekly_views": views,
                        "cumulative_weeks_in_top_10": cumulative[key],
                    }
                )
    _write_xlsx(
        pl.DataFrame(rows).sort("week", "category", "weekly_rank", descending=[True, False, False]),
        folder / "all-weeks-global.xlsx",
        "Top 10",
    )
    countries = [("Synthetic Land", "XA"), ("Synthetic Republic", "XB"), ("Synthetic Kingdom", "XC")]
    country_rows = []
    for name, iso in countries:
        for week in weeks:
            for category in ("Films", "TV"):
                source = [
                    r for r in rows if r["week"] == week.isoformat() and r["category"].startswith(category)
                ]
                picked = rng.sample(source, 10)
                for rank, r in enumerate(picked, start=1):
                    country_rows.append(
                        {
                            "country_name": name,
                            "country_iso2": iso,
                            "week": week.isoformat(),
                            "category": category,
                            "weekly_rank": rank,
                            "show_title": r["show_title"],
                            "season_title": r["season_title"],
                            "cumulative_weeks_in_top_10": 1,
                        }
                    )
    _write_xlsx(pl.DataFrame(country_rows), folder / "all-weeks-countries.xlsx", "Top 10")
    popular = []
    for category in lists:
        for rank in range(1, 11):
            runtime = round(rng.uniform(1.3, 9.0), 4)
            views = (11 - rank) * 20_000_000
            popular.append(
                {
                    "category": category,
                    "rank": rank,
                    "show_title": f"Synthetic Classic {category[:2]} {rank:02d}",
                    "season_title": "N/A",
                    "hours_viewed_first_91_days": int(views * runtime),
                    "runtime": runtime,
                    "views_first_91_days": views,
                }
            )
    _write_xlsx(pl.DataFrame(popular), folder / "most-popular.xlsx", "Most Popular")


def write_youtube(rng: random.Random) -> None:
    folder = OUT / "youtube"
    folder.mkdir(parents=True, exist_ok=True)
    now = datetime(2026, 10, 5, 6, 0, tzinfo=UTC)
    for c, channel_id in enumerate(CHANNELS, start=1):
        gap_hours = [8, 24, 72][c - 1]
        entries = []
        for i in range(15):
            published = now - timedelta(hours=gap_hours * i + rng.randint(0, 5))
            vid = f"syn{c}{i:02d}fixtr"[:11]
            stamp = published.isoformat()
            entries.append(f""" <entry>
  <id>yt:video:{vid}</id>
  <yt:videoId>{vid}</yt:videoId>
  <yt:channelId>{channel_id}</yt:channelId>
  <title>Synthetic upload {c}-{i:02d}</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v={vid}"/>
  <author><name>Synthetic Channel {c}</name><uri>https://www.youtube.com/channel/{channel_id}</uri></author>
  <published>{stamp}</published>
  <updated>{stamp}</updated>
  <media:group>
   <media:title>Synthetic upload {c}-{i:02d}</media:title>
   <media:description>{NOTICE}</media:description>
   <media:community>
    <media:starRating count="{rng.randint(10, 900)}" average="5.00" min="1" max="5"/>
    <media:statistics views="{rng.randint(1_000, 900_000)}"/>
   </media:community>
  </media:group>
 </entry>""")
        feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<!-- {NOTICE} -->
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
 <link rel="self" href="http://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"/>
 <id>yt:channel:{channel_id[2:]}</id>
 <yt:channelId>{channel_id[2:]}</yt:channelId>
 <title>Synthetic Channel {c}</title>
 <published>2015-01-0{c}T00:00:00+00:00</published>
{chr(10).join(entries)}
</feed>
"""
        (folder / f"{channel_id}.xml").write_text(feed, encoding="utf-8")
    # Recorded-shape YouTube Data API v3 responses (channels.list and playlistItems.list).
    channels = {
        "kind": "youtube#channelListResponse",
        "_synthetic": NOTICE,
        "items": [
            {
                "kind": "youtube#channel",
                "id": channel_id,
                "snippet": {"title": f"Synthetic Channel {c}", "publishedAt": f"2015-01-0{c}T00:00:00Z"},
                "contentDetails": {"relatedPlaylists": {"uploads": "UU" + channel_id[2:]}},
                "statistics": {
                    "viewCount": str(rng.randint(10**6, 10**9)),
                    "subscriberCount": str(rng.randint(10**4, 10**7)),
                    "hiddenSubscriberCount": False,
                    "videoCount": str(rng.randint(100, 5000)),
                },
            }
            for c, channel_id in enumerate(CHANNELS, start=1)
        ],
    }
    (folder / "api_channels.json").write_text(json.dumps(channels, indent=1) + "\n", encoding="utf-8")
    playlist = {
        "kind": "youtube#playlistItemListResponse",
        "_synthetic": NOTICE,
        "items": [
            {
                "kind": "youtube#playlistItem",
                "snippet": {"title": f"Synthetic API upload {i:02d}"},
                "contentDetails": {
                    "videoId": f"synapi{i:02d}xx"[:11],
                    "videoPublishedAt": (now - timedelta(days=i)).isoformat().replace("+00:00", "Z"),
                },
            }
            for i in range(20)
        ],
    }
    (folder / "api_playlist_items.json").write_text(json.dumps(playlist, indent=1) + "\n", encoding="utf-8")


def main() -> None:
    rng = random.Random(SEED)
    write_trends(rng)
    write_political_ads(rng)
    write_netflix(rng)
    write_youtube(rng)
    (OUT / "README.md").write_text(
        "# Synthetic fixtures\n\n"
        f"{NOTICE}\n\n"
        "Generated by `scripts/make_synthetic_fixtures.py` (seed "
        f"{SEED}). They mirror the real formats of Google Trends RSS, the Google political-ads\n"
        "transparency bundle, Netflix Top 10 xlsx files, YouTube channel RSS feeds and YouTube Data API v3\n"
        "responses, because those sources' terms do not permit redistributing samples. Real data is\n"
        "downloaded at runtime; see DATA_LICENSE.md. `tests/fixtures/osm/` holds real OSM data (ODbL).\n",
        encoding="utf-8",
    )
    print(f"wrote synthetic fixtures to {OUT}")


if __name__ == "__main__":
    main()
