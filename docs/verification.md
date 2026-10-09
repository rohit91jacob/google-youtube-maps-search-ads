# Verification

Everything here was run on **9 October 2026** against real downloads (not fixtures), in
`~/projects/_data/google-youtube-maps-search-ads/live`, with
`uv run python scripts/verify_published.py --overpass`. Re-run it the same way to reproduce.
Where a source publishes a figure of its own, the pipeline output is compared with it. Mismatches
are reported as found.

## Pipeline runs

| Run | Result |
|---|---|
| First live extract, all 5 sources | Trends 6 geos in 6.1 s; YouTube 7 feeds in 3.4 s; Netflix 3 files (15.9 MB) in 38.9 s; political-ads bundle (309.6 MB zip, 5 of its 12 files kept) in 43.9 s; OSM 8 cities in 362.7 s, because Overpass answered HTTP 504 "too busy" until endpoint failover and retries got through. |
| Load + dbt build | 15 raw tables (1.4 M rows; 512,200 of them Netflix per-country rows). dbt: 2 seeds, 16 tables, 16 views, 97 tests, **PASS=131, ERROR=0**. Raw DQ gate: 25 checks, 1 warning (see political ads below). Marts DQ gate: 4/4. |
| Second full run, 4.7 h later | Political ads: HTTP 304 via ETag (no download). Netflix: downloaded, byte-identical, so no new bronze objects. OSM: skipped (counted less than 7 days ago). Whole run 1 min 57 s. |
| Fixture pipeline (`geco fixtures-run`) + rerun | Same 131 dbt nodes pass. A full re-run adds no bronze object and changes no mart row (`tests/e2e/test_pipeline_fixtures.py::test_rerun_is_idempotent`). |

## Netflix Top 10: against Netflix's own published numbers

**1. Netflix's running counter.** Every row of the global file carries Netflix's
`cumulative_weeks_in_top_10`. The pipeline recounts weeks per title and list independently.

* They agree for **3,992 of 3,998** titles.
* All 6 differences are **different films with the same title** in the same list, each charting in
  two separate runs (for example *Kingdom*, Films (Non-English): 5 weeks by title, Netflix 3 for
  the later film). The public files have no title id, so the pipeline cannot separate them.
  Affected: *Aftermath*, *Dhamaka*, *Dragon*, *Kingdom*, *Noise*, *The Mummy*.

**2. The all-time most-popular lists** ([netflix.com/tudum/top10](https://www.netflix.com/tudum/top10),
`most-popular.xlsx`) publish hours viewed in each title's first 91 days. The weekly files only
contain the weeks a title was in the Top 10, so the sum of a title's weekly hours over its first
13 charting weeks must be **at most** the published 91-day figure, and close to it for titles that
charted throughout.

* 37 of the 40 titles debuted after the weekly files begin (27 June 2021) and can be compared.
* **None exceeds the published figure.** The weekly sum is 6.3% to 99.7% of it (median 95.1%).
  The low end is older seasons that were mostly outside the Top 10 in their first 91 days (e.g.
  *Lupin: Part 1*).

| Title (list) | Netflix 91-day hours | Weekly Top 10 sum, 13 weeks | Share |
|---|---|---|---|
| Squid Game: Season 1 (TV, non-English) | 2,205,200,000 | 2,196,420,000 | 99.6% |
| Stranger Things 4 (TV, English) | 1,838,000,000 | 1,793,590,000 | 97.6% |
| Wednesday: Season 1 (TV, English) | 1,718,800,000 | 1,710,530,000 | 99.5% |
| Stranger Things 5 (TV, English) | 1,391,300,000 | 1,315,800,000 | 94.6% |
| Squid Game: Season 2 (TV, non-English) | 1,380,100,000 | 1,353,300,000 | 98.1% |
| KPop Demon Hunters (Films, English) | 541,800,000 | 523,700,000 | 96.7% |

The gap is expected: the 91 days start on the premiere date, while Netflix weeks run Monday to
Sunday, and weeks outside the Top 10 are missing from the weekly file.

Views do **not** reconcile in the same way and are not used for this check. For multi-part
seasons the weekly views (hours ÷ runtime of what was released so far) add up to more than the
91-day views (e.g. *Stranger Things 5*: 189.2 M summed weekly views vs 133.8 M published).

## Political ads: four published aggregates of the same spend

The bundle publishes US spend four ways. The pipeline loads all four, independently:

| Google file | USD total | vs advertiser-stats |
|---|---|---|
| `advertiser-stats` (lifetime total per advertiser) | $2,886,037,800 | – |
| `advertiser-weekly-spend` (sum of all weeks) | $2,890,086,100 | +0.14% |
| `geo-spend` (by state and congressional district) | $2,885,986,700 | −0.00% |
| `advertiser-geo-spend` (advertiser × state) | $2,928,176,800 | +1.46% |

Per advertiser and currency (22,300 pairs), the pipeline's weekly sum equals Google's lifetime
total **exactly for 8,384** and is within 100 × (weeks with spend) for **20,207** (90.6%), which is
what rounding every week to 100 units allows. The largest US advertisers:

| Advertiser | Weekly sum (pipeline) | Google's lifetime total | Difference |
|---|---|---|---|
| BIDEN FOR PRESIDENT | $100,662,200 | $100,662,600 | −$400 |
| DONALD J. TRUMP FOR PRESIDENT, INC. | $81,717,400 | $81,717,500 | −$100 |
| HARRIS VICTORY FUND | $75,946,500 | $75,946,600 | −$100 |
| FF PAC | $62,144,000 | $62,144,000 | $0 |
| MIKE BLOOMBERG 2020 INC | $61,887,600 | $61,887,600 | $0 |

**Mismatches found in the source.** Per currency, weekly sums and lifetime totals are within 2%
except **ZAR (+18.5%: 10,575,000 vs 8,925,000)**, **TWD (+2.2%)** and **BRL (+2.1%)**. The
pipeline reports these as a `warn` (`ads_weekly_reconciles_with_totals`) and does not correct them:
the two files disagree in Google's own publication. `advertiser-geo-spend` is 1.46% above the
other US figures; we have not found out why.

The bundle downloaded on 9 October 2026 has no EU rows at all (no advertiser lists `EU` in
`Regions`, and every EUR and other EU-currency column is zero), so the EU appears in the currency
seed but in no mart. We did not check why Google no longer publishes EU data in this bundle.

## OpenStreetMap: two counting methods

No official body publishes amenity counts for our boxes, so the check is internal: for London, the
element ids were downloaded (`out ids`) and counted in Python, then compared with the stored
`out count` result taken earlier the same day.

| London box, amenity | Element ids downloaded | Stored `out count` |
|---|---|---|
| cafe | 2,666 | 2,666 |
| pharmacy | 294 | 294 |

Independent Overpass instances (maps.mail.ru, private.coffee, kumi.systems) returned HTTP 500/504
on 9 October, so a cross-instance comparison could not be made.

## Google Trends and YouTube: no published reference

* **Trends.** Google publishes no totals for the trending-now feed. Checked instead: every
  `approx_traffic` value parsed (`trends_items_traffic_parsed`), and the Pub/Sub event stream
  matches the snapshot diff for every poll (dbt test `assert_trend_events_match_snapshots`, 204
  events over 24 polls).
* **YouTube.** The RSS feed carries each video's current view count, but channels publish no upload
  count to compare against without the Data API (key-gated, off here). Cadence numbers cover only
  the uploads the polls have seen (15 per channel on the first poll).

## Infrastructure checks (no cloud account used)

| Check | Result |
|---|---|
| `terraform fmt -check -recursive` | clean |
| `terraform init -backend=false` + `validate` (Terraform 1.16.5, google provider 8.6.0) | valid |
| `tflint --recursive` (0.64.0, terraform recommended preset + google ruleset 0.40.0) | 0 issues |
| `checkov -d infra` (3.3.26) | 78 passed, 0 failed, 31 skipped. The skips are justified inline: 30 = CMEK and deletion protection on the 15 BigQuery *external* tables (no data stored in BigQuery), 1 = access logging on the log bucket itself. |
| BigQuery SQL | every model compiles with `--target offline` and the compiled SQL parses with `sqlfluff parse --dialect bigquery` |
