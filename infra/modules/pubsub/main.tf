# The trends event stream: entered/exited events published by the extractor and drained into
# bronze by `geco consume-events`. Undeliverable messages go to a dead-letter topic.

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
  description = "Project number (Pub/Sub service agent)."
  type        = string
}

variable "region" {
  description = "Only region allowed to store messages."
  type        = string
}

variable "kms_key_id" {
  description = "CMEK for both topics."
  type        = string
}

variable "publisher_account" {
  description = "Account that publishes events."
  type        = string
}

variable "subscriber_account" {
  description = "Account that pulls and acks events."
  type        = string
}

locals {
  pubsub_agent = "serviceAccount:service-${var.project_number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_pubsub_topic" "dead_letter" {
  name                       = "trends-events-dead-letter"
  project                    = var.project_id
  kms_key_name               = var.kms_key_id
  message_retention_duration = "604800s"

  message_storage_policy {
    allowed_persistence_regions = [var.region]
  }
}

resource "google_pubsub_topic" "trends" {
  name                       = "trends-events"
  project                    = var.project_id
  kms_key_name               = var.kms_key_id
  message_retention_duration = "604800s" # 7 days: a missed consumer run can be replayed

  message_storage_policy {
    allowed_persistence_regions = [var.region]
  }
}

resource "google_pubsub_subscription" "loader" {
  name                         = "trends-events-loader"
  project                      = var.project_id
  topic                        = google_pubsub_topic.trends.id
  ack_deadline_seconds         = 60
  message_retention_duration   = "604800s"
  enable_message_ordering      = true
  enable_exactly_once_delivery = false # the consumer dedupes by event_id instead

  expiration_policy {
    ttl = "" # never expire
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter.id
    max_delivery_attempts = 10
  }
}

resource "google_pubsub_subscription" "dead_letter" {
  name                       = "trends-events-dead-letter-inspect"
  project                    = var.project_id
  topic                      = google_pubsub_topic.dead_letter.id
  message_retention_duration = "604800s"

  expiration_policy {
    ttl = ""
  }
}

resource "google_pubsub_topic_iam_member" "publisher" {
  project = var.project_id
  topic   = google_pubsub_topic.trends.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${var.publisher_account}"
}

resource "google_pubsub_subscription_iam_member" "subscriber" {
  project      = var.project_id
  subscription = google_pubsub_subscription.loader.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${var.subscriber_account}"
}

# Dead-lettering requires the Pub/Sub service agent to publish to the DLQ and subscribe to the source.
resource "google_pubsub_topic_iam_member" "dlq_publisher" {
  project = var.project_id
  topic   = google_pubsub_topic.dead_letter.name
  role    = "roles/pubsub.publisher"
  member  = local.pubsub_agent
}

resource "google_pubsub_subscription_iam_member" "dlq_subscriber" {
  project      = var.project_id
  subscription = google_pubsub_subscription.loader.name
  role         = "roles/pubsub.subscriber"
  member       = local.pubsub_agent
}

output "topic_id" {
  description = "Trends topic id."
  value       = google_pubsub_topic.trends.id
}

output "topic_name" {
  description = "Trends topic name."
  value       = google_pubsub_topic.trends.name
}

output "subscription_name" {
  description = "Loader subscription name."
  value       = google_pubsub_subscription.loader.name
}
