# Developer entrypoints. `make help` lists targets. Settings come from .env when present.
-include .env
export

UV ?= uv
SITE ?= site
FIXTURE_DATA ?= data/fixtures

.DEFAULT_GOAL := help
.PHONY: help install lint format test test-unit test-e2e test-integration test-live fixtures \
        pipeline-fixtures extract poll-trends load transform run report dq status dbt-docs \
        dbt-compile-bq infra-fmt infra-validate infra-lint infra-checkov infra-check precommit clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

install: ## Create the virtualenv (dev + dbt groups + GCP clients)
	$(UV) sync --frozen --all-extras

lint: ## Ruff lint + format check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format: ## Auto-fix lint and format
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

test: ## Unit + end-to-end tests (no network, no emulators)
	$(UV) run pytest --cov

test-unit: ## Fast tests only
	$(UV) run pytest tests/unit

test-e2e: ## Full pipeline on fixtures incl. dbt build and the rerun/idempotency test
	$(UV) run pytest tests/e2e

test-integration: ## Pub/Sub emulator + fake-gcs-server (set PUBSUB_EMULATOR_HOST, GECO_TEST_GCS_ENDPOINT)
	$(UV) run pytest -m integration tests/integration

test-live: ## Smoke test against the real Trends feed (network)
	$(UV) run pytest -m live

fixtures: ## Regenerate the SYNTHETIC fixtures (seeded, deterministic)
	$(UV) run python scripts/make_synthetic_fixtures.py

pipeline-fixtures: ## Whole pipeline offline on the committed fixtures -> $(FIXTURE_DATA)
	$(UV) run geco fixtures-run --data-dir $(FIXTURE_DATA) --site $(FIXTURE_DATA)/site

extract: ## Fetch every real source into bronze (network)
	$(UV) run geco extract

poll-trends: ## Poll Google Trends, publish + consume the event stream
	$(UV) run geco poll-trends

load: ## Bronze -> raw parquet -> DuckDB + raw DQ gate (exit 3 on failure)
	$(UV) run geco load

transform: ## dbt build + marts DQ gate
	$(UV) run geco transform

run: ## extract -> events -> load -> transform -> site
	$(UV) run geco run --site $(SITE)

report: ## Write the static results site to ./site from the current warehouse
	$(UV) run geco report --out $(SITE)

dq: ## Raw-layer data-quality checks only
	$(UV) run geco dq --layer raw

status: ## Latest fetch per source
	$(UV) run geco status

dbt-docs: ## dbt docs for the local warehouse
	$(UV) run geco dbt -- docs generate

dbt-compile-bq: ## Compile every model for BigQuery offline and parse the SQL
	cd dbt && $(UV) run dbt compile --target offline --no-populate-cache --target-path target-bq --profiles-dir .
	cd dbt && $(UV) run sqlfluff parse --dialect bigquery --templater raw target-bq/compiled/geco/models > /dev/null

infra-fmt: ## terraform fmt -check
	terraform -chdir=infra fmt -check -recursive

infra-validate: ## terraform init -backend=false + validate
	terraform -chdir=infra init -backend=false -input=false
	terraform -chdir=infra validate

infra-lint: ## tflint (terraform + google rulesets)
	cd infra && tflint --init && tflint --recursive

infra-checkov: ## checkov on the Terraform (skips are justified inline)
	$(UV)x --from checkov checkov -d infra --framework terraform --quiet --compact --skip-path .terraform

infra-check: infra-fmt infra-validate infra-lint infra-checkov ## All infrastructure checks

precommit: ## Every pre-commit hook on every file
	$(UV) run pre-commit run --all-files

clean: ## Remove caches and build output (not data/)
	rm -rf .pytest_cache .ruff_cache .coverage coverage.xml htmlcov site dbt/target dbt/target-bq dbt/logs
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
