# One service account per job role. No project-level data roles are granted here: each module grants
# its account access to exactly the resources it uses (bucket, dataset, topic, job). The only
# project-level roles are the ones Google offers no narrower scope for (BigQuery job creation and
# writing logs/metrics).

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

variable "name_prefix" {
  description = "Resource name prefix."
  type        = string
}

locals {
  accounts = {
    extractor    = "Extract jobs: fetch sources, write bronze, publish and consume trend events"
    loader       = "Load job: bronze to raw parquet, raw data-quality gate"
    transformer  = "Transform job: dbt build on BigQuery"
    orchestrator = "Workflows: runs the Cloud Run jobs in order"
    scheduler    = "Cloud Scheduler: starts workflow executions"
    deployer     = "GitHub Actions deploy (via Workload Identity Federation)"
  }
  project_roles = {
    "transformer-jobuser" = { account = "transformer", role = "roles/bigquery.jobUser" }
    "extractor-logs"      = { account = "extractor", role = "roles/logging.logWriter" }
    "loader-logs"         = { account = "loader", role = "roles/logging.logWriter" }
    "transformer-logs"    = { account = "transformer", role = "roles/logging.logWriter" }
    "orchestrator-logs"   = { account = "orchestrator", role = "roles/logging.logWriter" }
  }
}

resource "google_service_account" "this" {
  for_each     = local.accounts
  project      = var.project_id
  account_id   = "${var.name_prefix}-${each.key}"
  display_name = "${var.name_prefix} ${each.key}"
  description  = each.value
}

resource "google_project_iam_member" "this" {
  for_each = local.project_roles
  project  = var.project_id
  role     = each.value.role
  member   = "serviceAccount:${google_service_account.this[each.value.account].email}"
}

output "emails" {
  description = "Service account emails by role."
  value       = { for k, sa in google_service_account.this : k => sa.email }
}

output "service_account_ids" {
  description = "Fully qualified service account ids by role (for IAM bindings on the account)."
  value       = { for k, sa in google_service_account.this : k => sa.name }
}
