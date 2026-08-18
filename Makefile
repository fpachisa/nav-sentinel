PROJECT ?= all-things-agentic-hack-fp
REGION  ?= us-central1
PY      := .venv/bin/python

.PHONY: help venv fixtures test lint bootstrap registry demo clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

venv: ## Create the virtual environment and install dependencies
	uv venv --python 3.12 .venv
	uv pip install --python .venv -e ".[dev]"

fixtures: ## Generate synthetic books and records with seeded breaks
	$(PY) fixtures/generate.py

test: ## Run the invariant test suite (offline)
	$(PY) -m pytest tests/ -q

verify: ## Offline gate: lint + diagrams + full invariant suite
	.venv/bin/ruff check src tests fixtures
	$(PY) scripts/check_diagrams.py
	$(PY) -m pytest tests/ -q

diagrams: ## Re-export standalone SVGs and PNGs from docs/architecture.html
	$(PY) scripts/export_diagrams.py

compliance: ## Prove the qualifying stack requirements against live Vertex AI
	$(PY) -m nav_sentinel.compliance
	$(PY) -m pytest tests/ -q -m live

lint: ## Lint
	.venv/bin/ruff check src tests fixtures

bootstrap: ## Provision Google Cloud: APIs, per-agent identities, Model Armor, Pub/Sub
	PROJECT=$(PROJECT) REGION=$(REGION) bash infra/bootstrap.sh

registry: ## Show the published agent fleet and its coverage
	$(PY) -m nav_sentinel.registry.cli

demo: ## Run one full NAV cycle end to end with tracing
	$(PY) -m nav_sentinel.pipeline.orchestrator

clean:
	rm -rf .pytest_cache .ruff_cache **/__pycache__ fixtures/data/*.json
