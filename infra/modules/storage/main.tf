# The lake bucket (bronze/, raw/, ops/) plus a bucket for its access logs.

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

variable "location" {
  description = "Bucket location (multi-region matching BigQuery)."
  type        = string
}

variable "kms_key_id" {
  description = "CMEK for both buckets."
  type        = string
}

variable "bronze_nearline_after_days" {
  description = "Age at which bronze objects move to Nearline."
  type        = number
}

variable "noncurrent_version_retention_days" {
  description = "Days to keep noncurrent object versions."
  type        = number
}

variable "writer_service_accounts" {
  description = "Accounts that create and read objects (extract and load jobs)."
  type        = list(string)
}

variable "reader_service_accounts" {
  description = "Accounts that only read objects (BigQuery external tables via the transform job)."
  type        = list(string)
}

resource "google_storage_bucket" "logs" {
  # checkov:skip=CKV_GCP_62:This is the access-log sink; logging a log bucket into itself is circular.
  name                        = "${var.project_id}-${var.name_prefix}-access-logs"
  project                     = var.project_id
  location                    = var.location
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    enabled = true
  }

  encryption {
    default_kms_key_name = var.kms_key_id
  }

  lifecycle_rule {
    condition {
      age = 90
    }
    action {
      type = "Delete"
    }
  }

  lifecycle_rule {
    condition {
      days_since_noncurrent_time = 7
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket" "lake" {
  name                        = "${var.project_id}-${var.name_prefix}-lake"
  project                     = var.project_id
  location                    = var.location
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    enabled = true
  }

  encryption {
    default_kms_key_name = var.kms_key_id
  }

  logging {
    log_bucket = google_storage_bucket.logs.name
  }

  soft_delete_policy {
    retention_duration_seconds = 604800 # 7 days
  }

  # Bronze is immutable and read rarely after the first load: cheaper storage after 30 days.
  lifecycle_rule {
    condition {
      age            = var.bronze_nearline_after_days
      matches_prefix = ["lake/bronze/"]
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  # raw/ is rewritten on every load, so old versions pile up; keep them only briefly.
  lifecycle_rule {
    condition {
      days_since_noncurrent_time = var.noncurrent_version_retention_days
    }
    action {
      type = "Delete"
    }
  }

  # Data-quality results and local Pub/Sub emulator state are operational, not data.
  lifecycle_rule {
    condition {
      age            = 90
      matches_prefix = ["lake/ops/dq/", "lake/ops/pubsub/"]
    }
    action {
      type = "Delete"
    }
  }

  lifecycle_rule {
    condition {
      age = 1
    }
    action {
      type = "AbortIncompleteMultipartUpload"
    }
  }
}

resource "google_storage_bucket_iam_member" "writers" {
  for_each = toset(var.writer_service_accounts)
  bucket   = google_storage_bucket.lake.name
  # objectUser = create/read/overwrite objects; no bucket-level or IAM permissions.
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${each.value}"
}

resource "google_storage_bucket_iam_member" "readers" {
  for_each = toset(var.reader_service_accounts)
  bucket   = google_storage_bucket.lake.name
  role     = "roles/storage.objectViewer"
  member   = "serviceAccount:${each.value}"
}

output "lake_bucket" {
  description = "Lake bucket name."
  value       = google_storage_bucket.lake.name
}

output "log_bucket" {
  description = "Access-log bucket name."
  value       = google_storage_bucket.logs.name
}
