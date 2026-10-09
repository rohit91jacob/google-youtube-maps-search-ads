terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.6"
    }
  }

  # State lives in a GCS bucket created out of band (see docs/runbook.md#infrastructure):
  #   terraform init -backend-config="bucket=<state-bucket>" -backend-config="prefix=geco"
  # CI only validates, with `terraform init -backend=false`.
  backend "gcs" {}
}
