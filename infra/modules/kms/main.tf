# Customer-managed encryption keys (CMEK) for every data store. Keys rotate every 90 days and are
# protected from `terraform destroy` (destroying a key makes its data unreadable).

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

variable "project_number" {
  description = "Project number (service agent emails are derived from it)."
  type        = string
}

variable "name_prefix" {
  description = "Resource name prefix."
  type        = string
}

variable "region" {
  description = "Region of the regional key ring (Pub/Sub, Artifact Registry, Workflows, Secret Manager)."
  type        = string
}

variable "multi_region" {
  description = "Multi-region of the key ring for GCS and BigQuery (eu or us)."
  type        = string
}

variable "rotation_period" {
  description = "Key rotation period."
  type        = string
  default     = "7776000s" # 90 days
}

data "google_storage_project_service_account" "gcs" {
  project = var.project_id
}

data "google_bigquery_default_service_account" "bq" {
  project = var.project_id
}

resource "google_kms_key_ring" "multi_region" {
  name     = "${var.name_prefix}-${var.multi_region}"
  project  = var.project_id
  location = var.multi_region
}

resource "google_kms_key_ring" "regional" {
  name     = "${var.name_prefix}-${var.region}"
  project  = var.project_id
  location = var.region
}

locals {
  keys = {
    storage   = { ring = google_kms_key_ring.multi_region.id, agent = data.google_storage_project_service_account.gcs.email_address }
    bigquery  = { ring = google_kms_key_ring.multi_region.id, agent = data.google_bigquery_default_service_account.bq.email }
    pubsub    = { ring = google_kms_key_ring.regional.id, agent = "service-${var.project_number}@gcp-sa-pubsub.iam.gserviceaccount.com" }
    artifact  = { ring = google_kms_key_ring.regional.id, agent = "service-${var.project_number}@gcp-sa-artifactregistry.iam.gserviceaccount.com" }
    workflows = { ring = google_kms_key_ring.regional.id, agent = "service-${var.project_number}@gcp-sa-workflows.iam.gserviceaccount.com" }
    secrets   = { ring = google_kms_key_ring.regional.id, agent = "service-${var.project_number}@gcp-sa-secretmanager.iam.gserviceaccount.com" }
  }
}

resource "google_kms_crypto_key" "this" {
  for_each        = local.keys
  name            = "${var.name_prefix}-${each.key}"
  key_ring        = each.value.ring
  rotation_period = var.rotation_period
  purpose         = "ENCRYPT_DECRYPT"

  version_template {
    algorithm        = "GOOGLE_SYMMETRIC_ENCRYPTION"
    protection_level = "SOFTWARE"
  }

  lifecycle {
    prevent_destroy = true
  }
}

# Each Google service agent may use only its own key.
resource "google_kms_crypto_key_iam_member" "agent" {
  for_each      = local.keys
  crypto_key_id = google_kms_crypto_key.this[each.key].id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${each.value.agent}"
}

output "storage_key_id" {
  description = "Key for the lake bucket."
  value       = google_kms_crypto_key.this["storage"].id
  depends_on  = [google_kms_crypto_key_iam_member.agent]
}

output "bigquery_key_id" {
  description = "Default key for the BigQuery datasets."
  value       = google_kms_crypto_key.this["bigquery"].id
  depends_on  = [google_kms_crypto_key_iam_member.agent]
}

output "pubsub_key_id" {
  description = "Key for the Pub/Sub topics."
  value       = google_kms_crypto_key.this["pubsub"].id
  depends_on  = [google_kms_crypto_key_iam_member.agent]
}

output "artifact_key_id" {
  description = "Key for Artifact Registry."
  value       = google_kms_crypto_key.this["artifact"].id
  depends_on  = [google_kms_crypto_key_iam_member.agent]
}

output "workflows_key_id" {
  description = "Key for Workflows execution data."
  value       = google_kms_crypto_key.this["workflows"].id
  depends_on  = [google_kms_crypto_key_iam_member.agent]
}

output "secrets_key_id" {
  description = "Key for Secret Manager."
  value       = google_kms_crypto_key.this["secrets"].id
  depends_on  = [google_kms_crypto_key_iam_member.agent]
}
