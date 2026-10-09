locals {
  labels = {
    app         = var.name_prefix
    environment = var.environment
    managed_by  = "terraform"
    repository  = "google-youtube-maps-search-ads"
  }
  image = coalesce(var.image, "${var.region}-docker.pkg.dev/${var.project_id}/${var.name_prefix}/pipeline:latest")
}

data "google_project" "this" {
  project_id = var.project_id
}

resource "google_project_service" "apis" {
  for_each = toset([
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudkms.googleapis.com",
    "cloudscheduler.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "monitoring.googleapis.com",
    "pubsub.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "sts.googleapis.com",
    "storage.googleapis.com",
    "workflows.googleapis.com",
  ])
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

module "kms" {
  source         = "./modules/kms"
  project_id     = var.project_id
  project_number = data.google_project.this.number
  name_prefix    = var.name_prefix
  region         = var.region
  multi_region   = lower(var.bigquery_location)
  depends_on     = [google_project_service.apis]
}

module "iam" {
  source      = "./modules/iam"
  project_id  = var.project_id
  name_prefix = var.name_prefix
  depends_on  = [google_project_service.apis]
}

module "storage" {
  source                            = "./modules/storage"
  project_id                        = var.project_id
  name_prefix                       = var.name_prefix
  location                          = var.bigquery_location
  kms_key_id                        = module.kms.storage_key_id
  bronze_nearline_after_days        = var.bronze_nearline_after_days
  noncurrent_version_retention_days = var.noncurrent_version_retention_days
  writer_service_accounts           = [module.iam.emails["extractor"], module.iam.emails["loader"]]
  reader_service_accounts           = [module.iam.emails["transformer"]]
  depends_on                        = [module.kms]
}

module "bigquery" {
  source                        = "./modules/bigquery"
  project_id                    = var.project_id
  location                      = var.bigquery_location
  kms_key_id                    = module.kms.bigquery_key_id
  lake_bucket                   = module.storage.lake_bucket
  staging_table_expiration_days = var.staging_table_expiration_days
  transformer_service_account   = module.iam.emails["transformer"]
  depends_on                    = [module.kms]
}

module "pubsub" {
  source             = "./modules/pubsub"
  project_id         = var.project_id
  project_number     = data.google_project.this.number
  region             = var.region
  kms_key_id         = module.kms.pubsub_key_id
  publisher_account  = module.iam.emails["extractor"]
  subscriber_account = module.iam.emails["extractor"]
  depends_on         = [module.kms]
}

module "cloud_run_jobs" {
  source              = "./modules/cloud_run_jobs"
  project_id          = var.project_id
  region              = var.region
  name_prefix         = var.name_prefix
  image               = local.image
  kms_key_id          = module.kms.artifact_key_id
  lake_uri            = "gs://${module.storage.lake_bucket}/lake"
  bigquery_location   = var.bigquery_location
  service_accounts    = module.iam.emails
  trends_topic        = module.pubsub.topic_name
  trends_subscription = module.pubsub.subscription_name
  enable_youtube_api  = var.enable_youtube_api
  secrets_kms_key_id  = module.kms.secrets_key_id
  depends_on          = [module.kms]
}

module "orchestration" {
  source                 = "./modules/orchestration"
  project_id             = var.project_id
  region                 = var.region
  name_prefix            = var.name_prefix
  kms_key_id             = module.kms.workflows_key_id
  workflow_source        = file("${path.module}/workflows/pipeline.yaml")
  orchestrator_account   = module.iam.emails["orchestrator"]
  scheduler_account      = module.iam.emails["scheduler"]
  job_names              = module.cloud_run_jobs.job_names
  trends_poll_schedule   = var.trends_poll_schedule
  full_pipeline_schedule = var.full_pipeline_schedule
}

module "wif" {
  source            = "./modules/wif"
  project_id        = var.project_id
  name_prefix       = var.name_prefix
  github_repository = var.github_repository
  deployer_account  = module.iam.service_account_ids["deployer"]
  depends_on        = [google_project_service.apis]
}

module "budget" {
  count              = var.billing_account_id == null ? 0 : 1
  source             = "./modules/budget"
  project_number     = data.google_project.this.number
  project_id         = var.project_id
  billing_account_id = var.billing_account_id
  amount             = var.monthly_budget_amount
  alert_email        = var.budget_alert_email
  name_prefix        = var.name_prefix
  depends_on         = [google_project_service.apis]
}
