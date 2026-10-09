# Workflows chains the Cloud Run jobs; Cloud Scheduler starts it daily (full) and every 3 hours
# (Trends poll only).

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

variable "region" {
  description = "Region."
  type        = string
}

variable "name_prefix" {
  description = "Resource name prefix."
  type        = string
}

variable "kms_key_id" {
  description = "CMEK for workflow execution data."
  type        = string
}

variable "workflow_source" {
  description = "Workflows YAML."
  type        = string
}

variable "orchestrator_account" {
  description = "Service account the workflow runs as."
  type        = string
}

variable "scheduler_account" {
  description = "Service account Cloud Scheduler authenticates as."
  type        = string
}

variable "job_names" {
  description = "Cloud Run job names by stage."
  type        = map(string)
}

variable "trends_poll_schedule" {
  description = "Cron for the Trends-only run."
  type        = string
}

variable "full_pipeline_schedule" {
  description = "Cron for the full run."
  type        = string
}

resource "google_workflows_workflow" "pipeline" {
  project             = var.project_id
  name                = "${var.name_prefix}-pipeline"
  region              = var.region
  description         = "extract -> consume events -> load (+DQ) -> dbt build on BigQuery"
  service_account     = var.orchestrator_account
  crypto_key_name     = var.kms_key_id
  call_log_level      = "LOG_ERRORS_ONLY"
  deletion_protection = false
  source_contents     = var.workflow_source
  user_env_vars = {
    PROJECT_ID    = var.project_id
    REGION        = var.region
    EXTRACT_JOB   = var.job_names["extract"]
    LOAD_JOB      = var.job_names["load"]
    TRANSFORM_JOB = var.job_names["transform"]
  }
}

# Workflows has no per-workflow IAM resource; a condition limits the grant to this workflow.
resource "google_project_iam_member" "scheduler_invoker" {
  project = var.project_id
  role    = "roles/workflows.invoker"
  member  = "serviceAccount:${var.scheduler_account}"

  condition {
    title      = "only-${google_workflows_workflow.pipeline.name}"
    expression = "resource.name == \"projects/${var.project_id}/locations/${var.region}/workflows/${google_workflows_workflow.pipeline.name}\""
  }
}

locals {
  schedules = {
    full   = { cron = var.full_pipeline_schedule, mode = "full", description = "Daily: every source, load, dbt build" }
    trends = { cron = var.trends_poll_schedule, mode = "trends", description = "Every 3 h: poll Google Trends and drain the event stream" }
  }
}

resource "google_cloud_scheduler_job" "this" {
  for_each         = local.schedules
  project          = var.project_id
  region           = var.region
  name             = "${var.name_prefix}-${each.key}"
  description      = each.value.description
  schedule         = each.value.cron
  time_zone        = "Etc/UTC"
  attempt_deadline = "180s"

  retry_config {
    retry_count          = 2
    min_backoff_duration = "60s"
  }

  http_target {
    http_method = "POST"
    uri         = "https://workflowexecutions.googleapis.com/v1/${google_workflows_workflow.pipeline.id}/executions"
    body        = base64encode(jsonencode({ argument = jsonencode({ mode = each.value.mode }), callLogLevel = "LOG_ERRORS_ONLY" }))
    headers = {
      "Content-Type" = "application/json"
    }

    oauth_token {
      service_account_email = var.scheduler_account
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }
}

output "workflow_id" {
  description = "Workflow id."
  value       = google_workflows_workflow.pipeline.id
}
