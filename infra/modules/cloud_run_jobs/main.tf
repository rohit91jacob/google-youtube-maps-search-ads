# Artifact Registry + the three Cloud Run jobs. Each job runs the same image (`geco <stage>`) under
# its own service account; Workflows overrides the args per step.

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
  description = "Region for the jobs and the registry."
  type        = string
}

variable "name_prefix" {
  description = "Resource name prefix."
  type        = string
}

variable "image" {
  description = "Container image."
  type        = string
}

variable "kms_key_id" {
  description = "CMEK for Artifact Registry."
  type        = string
}

variable "lake_uri" {
  description = "GECO_LAKE_URI for every job."
  type        = string
}

variable "bigquery_location" {
  description = "BigQuery location for dbt."
  type        = string
}

variable "service_accounts" {
  description = "Service account emails by role (from the iam module)."
  type        = map(string)
}

variable "trends_topic" {
  description = "Pub/Sub topic name."
  type        = string
}

variable "trends_subscription" {
  description = "Pub/Sub subscription name."
  type        = string
}

variable "enable_youtube_api" {
  description = "Mount the YOUTUBE_API_KEY secret into the extract job."
  type        = bool
}

variable "secrets_kms_key_id" {
  description = "CMEK for the Secret Manager secret (null = Google-managed)."
  type        = string
  default     = null
}

resource "google_artifact_registry_repository" "this" {
  project       = var.project_id
  location      = var.region
  repository_id = var.name_prefix
  description   = "Pipeline images (built by .github/workflows/deploy.yml)."
  format        = "DOCKER"
  kms_key_name  = var.kms_key_id

  docker_config {
    immutable_tags = false # `latest` moves; deploys pin the digest
  }

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }

  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      older_than = "2592000s" # 30 days
    }
  }
}

resource "google_secret_manager_secret" "youtube" {
  count     = var.enable_youtube_api ? 1 : 0
  project   = var.project_id
  secret_id = "${var.name_prefix}-youtube-api-key"

  replication {
    user_managed {
      replicas {
        location = var.region
        dynamic "customer_managed_encryption" {
          for_each = var.secrets_kms_key_id == null ? [] : [var.secrets_kms_key_id]
          content {
            kms_key_name = customer_managed_encryption.value
          }
        }
      }
    }
  }
}

resource "google_secret_manager_secret_iam_member" "youtube" {
  count     = var.enable_youtube_api ? 1 : 0
  project   = var.project_id
  secret_id = google_secret_manager_secret.youtube[0].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.service_accounts["extractor"]}"
}

locals {
  common_env = {
    GECO_LAKE_URI            = var.lake_uri
    GECO_DATA_DIR            = "/tmp/geco"
    GECO_LOG_FORMAT          = "json"
    GECO_PUBSUB_BACKEND      = "gcp"
    GECO_GCP_PROJECT         = var.project_id
    GECO_TRENDS_TOPIC        = var.trends_topic
    GECO_TRENDS_SUBSCRIPTION = var.trends_subscription
  }
  jobs = {
    extract = {
      account = "extractor"
      args    = ["extract"]
      timeout = "1800s"
      memory  = "2Gi"
      env     = {}
    }
    load = {
      account = "loader"
      args    = ["load"]
      timeout = "1800s"
      memory  = "4Gi"
      env     = {}
    }
    transform = {
      account = "transformer"
      args    = ["transform"]
      timeout = "1800s"
      memory  = "2Gi"
      env = {
        DBT_TARGET           = "bigquery"
        GECO_BQ_PROJECT      = var.project_id
        GECO_BQ_LOCATION     = var.bigquery_location
        DBT_PROFILES_DIR     = "/app/dbt"
        GECO_DBT_PROJECT_DIR = "/app/dbt"
      }
    }
  }
}

resource "google_cloud_run_v2_job" "this" {
  for_each            = local.jobs
  project             = var.project_id
  name                = "${var.name_prefix}-${each.key}"
  location            = var.region
  deletion_protection = false

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account = var.service_accounts[each.value.account]
      timeout         = each.value.timeout
      max_retries     = 1

      containers {
        image = var.image
        args  = each.value.args

        resources {
          limits = {
            cpu    = "1"
            memory = each.value.memory
          }
        }

        dynamic "env" {
          for_each = merge(local.common_env, each.value.env)
          content {
            name  = env.key
            value = env.value
          }
        }

        dynamic "env" {
          for_each = each.key == "extract" && var.enable_youtube_api ? [1] : []
          content {
            name = "YOUTUBE_API_KEY"
            value_source {
              secret_key_ref {
                secret  = google_secret_manager_secret.youtube[0].secret_id
                version = "latest"
              }
            }
          }
        }
      }
    }
  }

  lifecycle {
    # deploy.yml rolls new image digests; Terraform owns everything else.
    ignore_changes = [template[0].template[0].containers[0].image]
  }
}

resource "google_artifact_registry_repository_iam_member" "deployer" {
  project    = var.project_id
  location   = google_artifact_registry_repository.this.location
  repository = google_artifact_registry_repository.this.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${var.service_accounts["deployer"]}"
}

# Workflows may start each job with per-step arguments, and nothing else.
resource "google_cloud_run_v2_job_iam_member" "orchestrator" {
  for_each = google_cloud_run_v2_job.this
  project  = var.project_id
  location = each.value.location
  name     = each.value.name
  role     = "roles/run.jobsExecutorWithOverrides"
  member   = "serviceAccount:${var.service_accounts["orchestrator"]}"
}

# The deploy workflow updates job images; it must be able to act as the runtime accounts.
resource "google_cloud_run_v2_job_iam_member" "deployer" {
  for_each = google_cloud_run_v2_job.this
  project  = var.project_id
  location = each.value.location
  name     = each.value.name
  role     = "roles/run.developer"
  member   = "serviceAccount:${var.service_accounts["deployer"]}"
}

resource "google_service_account_iam_member" "deployer_act_as" {
  for_each           = toset(["extractor", "loader", "transformer"])
  service_account_id = "projects/${var.project_id}/serviceAccounts/${var.service_accounts[each.value]}"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.service_accounts["deployer"]}"
}

output "job_names" {
  description = "Job names by stage."
  value       = { for k, j in google_cloud_run_v2_job.this : k => j.name }
}

output "repository_url" {
  description = "Docker repository URL."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.this.repository_id}"
}
