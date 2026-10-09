provider "google" {
  project = var.project_id
  region  = var.region

  # Applied to every resource that supports labels (the GCP equivalent of tags).
  default_labels = local.labels
}
