PROJECT ?= all-things-agentic-hack-fp
REGION  ?= us-central1
PY      := .venv/bin/python

.PHONY: help venv fixtures fixtures-live test verify diagrams compliance lint bootstrap deploy teardown registry demo clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

venv: ## Create the virtual environment and install dependencies
	uv venv --python 3.12 .venv
	uv pip install --python .venv -e ".[dev]"

fixtures-live: ## Re-record ECB rates from the live API, then regenerate
	$(PY) fixtures/generate.py --refresh-rates

fixtures: ## Generate synthetic books and records with seeded breaks (offline, from the cassette)
	$(PY) fixtures/generate.py

test: ## Run the invariant test suite (offline)
	$(PY) -m pytest tests/ -q

verify: ## Offline gate: lint + diagrams + full invariant suite
	.venv/bin/ruff check src tests fixtures scripts
	$(PY) scripts/check_diagrams.py
	$(PY) -m pytest tests/ -q

diagrams: ## Re-export standalone SVGs and PNGs from docs/architecture.html
	$(PY) scripts/export_diagrams.py

compliance: ## Prove the qualifying stack requirements against live Vertex AI
	$(PY) -m nav_sentinel.compliance
	$(PY) -m pytest tests/ -q -m live

lint: ## Lint
	.venv/bin/ruff check src tests fixtures scripts

bootstrap: ## Provision Google Cloud: APIs, per-agent identities, Model Armor, Pub/Sub
	PROJECT=$(PROJECT) REGION=$(REGION) bash infra/bootstrap.sh

registry: ## Show the published agent fleet and its coverage
	$(PY) -m nav_sentinel.fleet_cli

deploy: ## Build and deploy to Cloud Run, and wire Pub/Sub push
	bash infra/deploy.sh

teardown: ## Remove the deployed service and subscription (keeps identities and fixtures)
	bash infra/teardown.sh

demo: ## Run one reconciliation cycle: detect, score, band, route, trace
	$(PY) -m nav_sentinel.pipeline.cycle_runner

investigate: ## One case, investigated by the fleet. NEEDS a live model, unlike `demo`
	$(PY) -m nav_sentinel.pipeline.investigate_cli

clean:
	rm -rf .pytest_cache .ruff_cache **/__pycache__ fixtures/data/*.json
