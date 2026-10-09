output "lake_bucket" {
  description = "GCS bucket that holds bronze/, raw/ and ops/ (GECO_LAKE_URI=gs://<bucket>/lake)."
  value       = module.storage.lake_bucket
}

output "bigquery_datasets" {
  description = "BigQuery dataset ids by layer."
  value       = module.bigquery.dataset_ids
}

output "trends_topic" {
  description = "Pub/Sub topic for trend entered/exited events."
  value       = module.pubsub.topic_id
}

output "cloud_run_jobs" {
  description = "Cloud Run job names by pipeline stage."
  value       = module.cloud_run_jobs.job_names
}

output "workflow" {
  description = "Workflows workflow that chains the jobs."
  value       = module.orchestration.workflow_id
}

output "service_accounts" {
  description = "Service account emails by role."
  value       = module.iam.emails
}

output "workload_identity_provider" {
  description = "Set as the repository variable GCP_WORKLOAD_IDENTITY_PROVIDER to enable deploy.yml."
  value       = module.wif.provider_name
}

output "artifact_repository" {
  description = "Artifact Registry repository for the pipeline image."
  value       = module.cloud_run_jobs.repository_url
}
