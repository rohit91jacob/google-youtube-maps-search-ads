# Cost guardrail: a monthly budget scoped to this project with email alerts. (Budgets alert; they do
# not cap spend. The hard per-query cap is maximum_bytes_billed in dbt/profiles.yml.)

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
  description = "Project id (for the notification channel)."
  type        = string
}

variable "project_number" {
  description = "Project number the budget is scoped to."
  type        = string
}

variable "billing_account_id" {
  description = "Billing account id."
  type        = string
}

variable "amount" {
  description = "Monthly budget amount (units of the billing currency)."
  type        = number
}

variable "alert_email" {
  description = "Alert email, or null for billing admins only."
  type        = string
  default     = null
}

variable "name_prefix" {
  description = "Resource name prefix."
  type        = string
}

resource "google_monitoring_notification_channel" "email" {
  count        = var.alert_email == null ? 0 : 1
  project      = var.project_id
  display_name = "${var.name_prefix} budget alerts"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }
}

resource "google_billing_budget" "monthly" {
  billing_account = var.billing_account_id
  display_name    = "${var.name_prefix} monthly budget"

  budget_filter {
    projects               = ["projects/${var.project_number}"]
    calendar_period        = "MONTH"
    credit_types_treatment = "INCLUDE_ALL_CREDITS"
  }

  amount {
    specified_amount {
      units = tostring(var.amount)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }

  threshold_rules {
    threshold_percent = 0.9
  }

  threshold_rules {
    threshold_percent = 1.0
  }

  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }

  all_updates_rule {
    monitoring_notification_channels = google_monitoring_notification_channel.email[*].id
    disable_default_iam_recipients   = false
  }
}
