PY ?= .venv/bin/python
.DEFAULT_GOAL := help

help:            ## show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

venv:            ## create .venv and install the project + dev tools
	python3 -m venv .venv
	$(PY) -m pip install -e '.[dev]'

run:             ## run the main app on :8000 (auto-reload)
	$(PY) -m uvicorn fxlab.main:app --reload --port 8000

run-cp:          ## run the fake counterparties service on :8001
	$(PY) -m uvicorn fxlab.counterparty.app:app --port 8001

test:            ## run the test suite
	$(PY) -m pytest

cov:             ## tests with coverage report
	$(PY) -m pytest --cov=fxlab --cov-report=term-missing

lint:            ## ruff lint + format check
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

fmt:             ## auto-format and auto-fix
	$(PY) -m ruff format .
	$(PY) -m ruff check --fix .

typecheck:       ## mypy
	$(PY) -m mypy fxlab

check: lint typecheck test   ## everything CI runs

up:              ## start the full stack with docker compose
	docker compose up --build -d

down:            ## stop the stack
	docker compose down

.PHONY: help venv run run-cp test cov lint fmt typecheck check up down
