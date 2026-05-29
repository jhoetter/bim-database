PORT     ?= 2500
WEB_PORT ?= 5173
FORM_PORT     ?= 2600
FORM_WEB_PORT ?= 5174

# When viewing this dev box through SSH tunnels, override the port pair so
# multiple sister apps on the same machine don't collide. Mirrors bim-ai's
# `make dev-forwarded` ergonomics.
#   ssh -L $(FORWARDED_WEB_PORT):127.0.0.1:$(WEB_PORT) \
#       -L $(FORWARDED_PORT):127.0.0.1:$(PORT) <host>
FORWARDED_PORT     ?= 12500
FORWARDED_WEB_PORT ?= 15173

VENV   := .venv
PYTHON := $(VENV)/bin/python
PIP    := $(VENV)/bin/pip
UV     := $(VENV)/bin/uvicorn
CONCURRENTLY := ./ui/node_modules/.bin/concurrently

.PHONY: install dev dev-forwarded dev-api dev-web kill-ports build cleanup-houses \
        ingest form-api form-ui-install form-ui-dev form-ui-build test

install:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip -q
	$(PIP) install -r requirements.txt -q
	cd ui && npm install
	@echo "✓ installed – run 'make dev' (API on :$(PORT), Vite on :$(WEB_PORT))"

# Runs FastAPI + Vite side-by-side under `concurrently`. Logs are prefixed
# [api] / [web]; Ctrl-C kills both.
# - API serves /datasets, /labels, /static, /pdfs and the built bundle at /.
# - Vite serves the React app with HMR and proxies API paths back to FastAPI.
# Open http://127.0.0.1:$(WEB_PORT) — Vite is the day-to-day entry.
dev: kill-ports
	@echo "→ API   http://127.0.0.1:$(PORT)"
	@echo "→ Web   http://127.0.0.1:$(WEB_PORT)  (proxies /datasets, /labels, /static, /pdfs)"
	@test -x $(CONCURRENTLY) || (echo "missing ui/node_modules — run 'make install' first" && exit 1)
	API_PORT=$(PORT) WEB_PORT=$(WEB_PORT) $(CONCURRENTLY) -k -n api,web -c blue,magenta \
	  "$(MAKE) dev-api PORT=$(PORT)" \
	  "$(MAKE) dev-web WEB_PORT=$(WEB_PORT) PORT=$(PORT)"

dev-forwarded:
	$(MAKE) dev PORT=$(FORWARDED_PORT) WEB_PORT=$(FORWARDED_WEB_PORT)

dev-api:
	$(UV) api.main:app --reload --host 127.0.0.1 --port $(PORT)

dev-web:
	cd ui && API_PORT=$(PORT) WEB_PORT=$(WEB_PORT) npm run dev

kill-ports:
	@for p in $(PORT) $(WEB_PORT) $(FORM_PORT) $(FORM_WEB_PORT); do \
	  pids=$$(lsof -ti :$$p 2>/dev/null); \
	  [ -n "$$pids" ] && kill -9 $$pids 2>/dev/null || true; \
	done; true

# Build the React bundle into ui/dist/. The FastAPI root route serves
# ui/dist/index.html, so this only matters for single-port testing.
build:
	cd ui && npm run build

# R0.6 — one-shot deletion of the legacy data/houses/ tree. Already run
# during the R0 cleanup; idempotent for repeat invocations.
cleanup-houses:
	$(PYTHON) scripts/cleanup_houses_legacy.py

# ── Ingestion pipeline ──────────────────────────────────────────────────

# Batch ingestion entry point. Usage:
#   make ingest INPUTS="path/to/*.pdf path/to/photos/*.heic"
#   make ingest INPUTS=foo.pdf HOUSE_KEY=house-42 PROFILE=lenient-scrape
INPUTS    ?=
HOUSE_KEY ?=
PROFILE   ?=
NOTES     ?=
SRC_TYPE  ?= batch
ingest:
	@test -n "$(INPUTS)" || (echo "usage: make ingest INPUTS='path/to/*.pdf' [HOUSE_KEY=house-N] [PROFILE=default|lenient-scrape|strict-form] [NOTES='...']" && exit 1)
	$(PYTHON) -m ingestion.cli $(INPUTS) \
	  $(if $(HOUSE_KEY),--house-key $(HOUSE_KEY)) \
	  $(if $(PROFILE),--profile $(PROFILE)) \
	  --source-type $(SRC_TYPE) \
	  $(if $(NOTES),--notes "$(NOTES)")

# Customer-submission form FastAPI app. Runs on :$(FORM_PORT) by default,
# separate from the developer-facing API. Reads FORM_API_KEY from the env;
# refuses to handle submissions without one.
form-api:
	@test -n "$$FORM_API_KEY" || (echo "set FORM_API_KEY=… first — refusing to start an un-auth'd public surface" && exit 1)
	$(UV) form_api.main:app --host 127.0.0.1 --port $(FORM_PORT)

# Customer SPA: minimal Vite app under form-ui/. Talks to form-api on
# :$(FORM_PORT) by default; override with VITE_FORM_API_BASE.
form-ui-install:
	cd form-ui && npm install

form-ui-dev:
	cd form-ui && FORM_UI_PORT=$(FORM_WEB_PORT) npm run dev

form-ui-build:
	cd form-ui && npm run build

# Test suite. Runs the ingestion package tests (CPU-only, no API keys).
test:
	$(PYTHON) -m pytest tests/ -q
