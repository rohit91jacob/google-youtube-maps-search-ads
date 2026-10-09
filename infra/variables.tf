variable "project_id" {
  description = "GCP project that hosts the pipeline."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run jobs, Workflows, Scheduler, Pub/Sub storage and Artifact Registry."
  type        = string
  default     = "europe-west1"
}

variable "bigquery_location" {
  description = "BigQuery and GCS multi-region (must contain var.region)."
  type        = string
  default     = "EU"
}

variable "environment" {
  description = "Deployment environment label (dev or prod)."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "The environment must be dev or prod."
  }
}

variable "name_prefix" {
  description = "Prefix for resource names."
  type        = string
  default     = "geco"
}

variable "image" {
  description = "Container image for the Cloud Run jobs (built from the repository Dockerfile). Null = the Artifact Registry repository created here, tag latest."
  type        = string
  default     = null
}

variable "github_repository" {
  description = "owner/name of the GitHub repository allowed to deploy through Workload Identity Federation."
  type        = string
  default     = "rohit91jacob/google-youtube-maps-search-ads"
}

variable "billing_account_id" {
  description = "Billing account for the budget (XXXXXX-XXXXXX-XXXXXX). Null skips the budget."
  type        = string
  default     = null
}

variable "monthly_budget_amount" {
  description = "Monthly budget in the billing account currency. Alerts at 50, 90 and 100 percent of actual spend and 100 percent of forecast."
  type        = number
  default     = 10
}

variable "budget_alert_email" {
  description = "Email for budget alerts (Cloud Monitoring notification channel). Null = billing admins only."
  type        = string
  default     = null
}

variable "bronze_nearline_after_days" {
  description = "Move bronze objects to Nearline after this many days."
  type        = number
  default     = 30
}

variable "noncurrent_version_retention_days" {
  description = "Delete overwritten or deleted object versions after this many days."
  type        = number
  default     = 30
}

variable "staging_table_expiration_days" {
  description = "Default table expiration for the staging and intermediate datasets (dbt rebuilds them on every run)."
  type        = number
  default     = 7
}

variable "trends_poll_schedule" {
  description = "Cron (UTC) for the Trends-only poll. The feed lists only the ~10 searches trending right now, so it is polled often."
  type        = string
  default     = "17 */3 * * *"
}

variable "full_pipeline_schedule" {
  description = "Cron (UTC) for the daily full pipeline."
  type        = string
  default     = "30 6 * * *"
}

variable "enable_youtube_api" {
  description = "Create a Secret Manager secret for YOUTUBE_API_KEY and expose it to the extract job."
  type        = bool
  default     = false
}
