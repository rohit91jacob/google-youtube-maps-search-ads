# Runbook

## Stages and exit codes

| Stage | Command | Cloud equivalent | Notes |
|---|---|---|---|
| Extract | `geco extract [-s SOURCE]` | Cloud Run job `geco-extract` | Fetches into bronze, content-addressed. Failures are recorded in the manifest (`status=failed`) and do not stop other sources. Trends diffs are published to Pub/Sub. |
| Consume events | `geco consume-events` | same job, args `consume-events` | Drains `trends-events-loader` into one bronze JSONL object, then acks. Safe to re-run. |
| Load | `geco load` | Cloud Run job `geco-load` | Rebuilds every `raw/<table>/data.parquet` from bronze, registers DuckDB views, runs the raw DQ gate. **Exit 3** = an error-level check failed. |
| Transform | `geco transform` | Cloud Run job `geco-transform` (`DBT_TARGET=bigquery`) | `dbt build` (models + 100+ tests). **Exit 4** = dbt failed. Then the marts DQ gate (DuckDB only): **exit 3**. |
| Report | `geco report --out site` | GitHub Actions (`refresh.yml`) | Static site from the marts. |
| Trends poll | `geco poll-trends` | Scheduler every 3 h -> Workflows `mode=trends` | Extract trends + consume events only. |
| Prune | `geco prune --keep 3` | GCS lifecycle rules | Keeps the 3 latest versions of each snapshot file (political ads, Netflix). |

`geco status` shows the latest manifest entry per source/dataset/key. DQ results are JSON files in
`<lake>/ops/dq/<run_id>-<layer>.json` (`violations` and up to 5 sample rows per failed check).

## Common failures

| Symptom | Cause | Action |
|---|---|---|
| `osm/...: failed: ... HTTP 504 ... too busy` | Overpass is overloaded (common). | Nothing: the extractor tries every endpoint in `GECO_OVERPASS_ENDPOINTS` for 3 rounds, keeps the previous counts, and only re-counts weekly. `osm_fresh` warns after 21 days without a successful count. |
| `netflix/...: response is not an xlsx file` | netflix.com answered with an HTML page (it does this for some user agents). | Keep the default `GECO_USER_AGENT`; the previous files stay in use. |
| `raw_tables_present` / `raw_tables_not_empty` fail (exit 3) | A source has never been fetched successfully into this lake. | Run `geco extract -s <source>` and check its manifest entry with `geco status`. |
| `ads_weekly_reconciles_with_totals` warns | Google's weekly file and its lifetime totals disagree by more than 2% for a currency. This is in the source (ZAR, TWD, BRL on 9 Oct 2026, see verification.md). | None; it is a warning so the drift stays visible. |
| `netflix_full_lists` warns | A published weekly list has fewer than 10 titles. | None; Netflix occasionally publishes short lists. |
| dbt test `assert_trend_events_match_snapshots` fails | The Pub/Sub stream and the snapshots disagree, e.g. events were consumed from a different lake, or a poll was deleted. | Inspect `marts.fct_trends_events` vs `marts.mart_trends_churn_by_geo` for the failing geo/poll. Re-running consume-events is safe (events are deduplicated by `event_id`). |
| `Can't open a connection to same database file with a different configuration` | A second process holds the DuckDB file (for example an open DBeaver session). | Close it; one writer at a time. |

## Re-running and backfills

* Every stage is idempotent: bronze objects are content-addressed, raw tables are rebuilt
  deterministically from bronze + manifest, dbt rebuilds every model. `tests/e2e` asserts that a
  full re-run adds no bronze object and changes no mart row.
* Re-running a poll with the same clock (`GECO_AS_OF`) collapses to one poll. Without a frozen
  clock a re-run is a new observation, which is correct for Trends/YouTube polls.
* Rebuild everything offline from bronze: `geco run --skip-extract`.
* Political ads and Netflix republish full history in every file, so a backfill is just the latest
  download. Trends history cannot be backfilled: the feed only shows the present.

## Scheduled refresh (GitHub Actions)

`refresh.yml` runs every 3 hours (Trends poll) and daily at 06:30 UTC (full run + GitHub Pages).
The lake lives in the Actions cache (`geco-lake-<run_id>`, restored from the newest `geco-lake-*`).
GitHub evicts caches unused for 7 days, so a pause longer than that restarts the Trends history.

When a scheduled run fails, the `alert` job opens (or comments on) an issue titled
**"Scheduled refresh is failing"**. Open the linked run, find the failed step:

* **Full pipeline, exit 3**: open the "DQ" log lines (`dq error ... FAIL`), fix the source issue or
  the check, re-run with *Run workflow*.
* **Full pipeline, exit 4**: a dbt model or test failed; the dbt log names it.
* **Pages deploy failed**: Settings -> Pages must be set to "GitHub Actions".

Close the issue once a run is green. `keepalive.yml` re-enables the scheduled workflows twice a
month so GitHub does not disable them after 60 days without commits.

## Infrastructure

`infra/` is Terraform for the production shape on GCP. **It has been validated (fmt, validate,
tflint, checkov) but never applied** by this repository.

To deploy into your own project:

1. Create a state bucket and a billing-enabled project; enable `cloudresourcemanager.googleapis.com`.
2. `cp infra/terraform.tfvars.example infra/terraform.tfvars` and fill in `project_id`
   (and `billing_account_id` for the budget).
3. `terraform -chdir=infra init -backend-config="bucket=<state-bucket>" -backend-config="prefix=geco"`
4. `terraform -chdir=infra plan -out tfplan` and review: KMS keys are created with
   `prevent_destroy`, and buckets/datasets keep their contents on destroy.
5. `terraform -chdir=infra apply tfplan`.
6. In GitHub: create the environment **production** (with required reviewers), and set the
   repository variables `GCP_WORKLOAD_IDENTITY_PROVIDER` (output `workload_identity_provider`),
   `GCP_DEPLOYER_SERVICE_ACCOUNT` (`service_accounts.deployer`), `GCP_PROJECT_ID` and optionally
   `GCP_REGION`. `deploy.yml` then builds the image and updates the Cloud Run jobs. No keys.
7. Optional YouTube Data API: `enable_youtube_api = true`, then add a secret version to
   `geco-youtube-api-key` in Secret Manager.

### Deploying to real GCP

What changes when the emulators are swapped for the real services:

| Setting | Local | GCP |
|---|---|---|
| `GECO_LAKE_URI` | `file://.../data/lake` | `gs://<project>-geco-lake/lake` |
| `GECO_PUBSUB_BACKEND` | `local` | `gcp` (+ `GECO_GCP_PROJECT`) |
| `DBT_TARGET` | `local` (DuckDB) | `bigquery` (+ `GECO_BQ_PROJECT`, `GECO_BQ_LOCATION`) |
| raw tables | DuckDB views over parquet | BigQuery external tables over the same parquet (`infra/modules/bigquery`) |

### Cost guardrails

* `google_billing_budget` with alerts at 50/90/100% actual and 100% forecast (alerts only).
* dbt BigQuery profile: `maximum_bytes_billed` = 10 GB per query (`GECO_BQ_MAX_BYTES_BILLED`); a
  query that would scan more fails.
* Staging/intermediate datasets: 7-day default table expiration. Bronze moves to Nearline after
  30 days; noncurrent object versions are deleted after 30 days; ops data after 90 days.
* Cloud Run jobs: 1 vCPU, 2-4 GiB, 30-minute timeout, 1 retry.
