# BigQuery datasets by layer. raw.* are external tables over the parquet files that `geco load`
# writes to gs://<lake>/lake/raw/<table>/; dbt builds staging/intermediate (views) and marts (tables).

terraform {
  required_version = ">= 1.9.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 8.6, < 9.0"
    }
  }
}

variable "project_id" {
  description = "Project id."
  type        = string
}

variable "location" {
  description = "Dataset location."
  type        = string
}

variable "kms_key_id" {
  description = "Default CMEK for every dataset."
  type        = string
}

variable "lake_bucket" {
  description = "Bucket holding raw parquet."
  type        = string
}

variable "staging_table_expiration_days" {
  description = "Default table expiration for staging and intermediate."
  type        = number
}

variable "transformer_service_account" {
  description = "The dbt service account."
  type        = string
}

variable "raw_tables" {
  description = "Raw tables written by `geco load` (keep in sync with geco.raw.ALL_TABLES; a unit test checks)."
  type        = list(string)
  default = [
    "trends_polls", "trends_items", "trends_events",
    "ads_report_updated", "ads_advertisers", "ads_advertiser_spend_totals", "ads_weekly_spend",
    "ads_advertiser_geo_spend", "ads_geo_spend",
    "netflix_global_weekly", "netflix_country_weekly", "netflix_most_popular",
    "osm_amenity_counts",
    "youtube_videos", "youtube_channels",
  ]
}

locals {
  datasets = {
    raw          = { description = "External tables over raw parquet in the lake bucket.", expiration_days = null, writer = false }
    staging      = { description = "dbt staging views (rebuilt every run).", expiration_days = var.staging_table_expiration_days, writer = true }
    intermediate = { description = "dbt intermediate views (rebuilt every run).", expiration_days = var.staging_table_expiration_days, writer = true }
    marts        = { description = "dbt marts: the published tables.", expiration_days = null, writer = true }
    seeds        = { description = "dbt seeds (reference data).", expiration_days = null, writer = true }
  }
}

resource "google_bigquery_dataset" "this" {
  for_each                    = local.datasets
  project                     = var.project_id
  dataset_id                  = each.key
  location                    = var.location
  description                 = each.value.description
  default_table_expiration_ms = each.value.expiration_days == null ? null : each.value.expiration_days * 86400000
  delete_contents_on_destroy  = false

  default_encryption_configuration {
    kms_key_name = var.kms_key_id
  }
}

resource "google_bigquery_table" "raw" {
  # checkov:skip=CKV_GCP_80:External tables store no data in BigQuery; the parquet they read is CMEK-encrypted in GCS.
  # checkov:skip=CKV_GCP_121:External tables are recreated from the lake at no cost; deletion protection would block schema changes.
  for_each            = toset(var.raw_tables)
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.this["raw"].dataset_id
  table_id            = each.value
  description         = "External parquet table written by `geco load`."
  deletion_protection = false

  external_data_configuration {
    source_format = "PARQUET"
    autodetect    = true
    source_uris   = ["gs://${var.lake_bucket}/lake/raw/${each.value}/*.parquet"]
  }
}

resource "google_bigquery_dataset_iam_member" "writer" {
  for_each   = { for k, v in local.datasets : k => v if v.writer }
  project    = var.project_id
  dataset_id = google_bigquery_dataset.this[each.key].dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${var.transformer_service_account}"
}

resource "google_bigquery_dataset_iam_member" "raw_reader" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.this["raw"].dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${var.transformer_service_account}"
}

output "dataset_ids" {
  description = "Dataset ids by layer."
  value       = { for k, d in google_bigquery_dataset.this : k => d.dataset_id }
}
