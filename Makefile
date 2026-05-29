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
FORWARDED_FORM_PORT     ?= 12600
FORWARDED_FORM_WEB_PORT ?= 15174

VENV   := .venv
PYTHON := $(VENV)/bin/python
PIP    := $(VENV)/bin/pip
UV     := $(VENV)/bin/uvicorn
CONCURRENTLY := ./ui/node_modules/.bin/concurrently

.PHONY: install dev dev-forwarded dev-api dev-web kill-ports build cleanup-houses \
        ingest form-api form-ui-install form-ui-dev form-ui-build form-dev \
        form-dev-forwarded test mcp

install:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip -q
	$(PIP) install -r requirements.txt -q
	cd ui && npm install
	@echo "✓ installed – run 'make dev' (API on :$(PORT), Vite on :$(WEB_PORT))"

# Runs FastAPI + Vite side-by-side under `concurrently`. Logs are prefixed
# [api] / [web]; Ctrl-C kills both.
# - API serves /datasets, /labels, /static, /pdfs and the built bundle at /.
# - Vite (HMR) serves the React app on :$(WEB_PORT) for fast iteration.
# - A third process keeps ui/dist/ in sync via `vite build --watch` so the
#   FastAPI port also serves a fresh SPA — either URL works during dev.
# Open http://127.0.0.1:$(WEB_PORT) for HMR, or :$(PORT) for the built path.
dev: kill-ports
	@echo "→ API   http://127.0.0.1:$(PORT)        (serves built ui/dist/, kept fresh by vite build --watch)"
	@echo "→ Web   http://127.0.0.1:$(WEB_PORT)  (Vite HMR, proxies API paths back to :$(PORT))"
	@test -x $(CONCURRENTLY) || (echo "missing ui/node_modules — run 'make install' first" && exit 1)
	@# One-shot build before launching uvicorn so the /assets StaticFiles mount
	@# exists at app-load time (StaticFiles binds the directory at mount, the
	@# directory must be present already). After this the watcher keeps it fresh.
	@test -d ui/dist/assets || (echo "→ Pre-building ui/dist/ so the /assets mount comes up populated…"; cd ui && ./node_modules/.bin/vite build --logLevel error)
	API_PORT=$(PORT) WEB_PORT=$(WEB_PORT) $(CONCURRENTLY) -k -n api,web,build -c blue,magenta,gray \
	  "$(MAKE) dev-api PORT=$(PORT)" \
	  "$(MAKE) dev-web WEB_PORT=$(WEB_PORT) PORT=$(PORT)" \
	  "$(MAKE) dev-web-build-watch"

dev-forwarded:
	$(MAKE) dev PORT=$(FORWARDED_PORT) WEB_PORT=$(FORWARDED_WEB_PORT)

dev-api:
	$(UV) api.main:app --reload --host 127.0.0.1 --port $(PORT) \
	  --reload-dir api --reload-dir form_api --reload-dir ingestion \
	  --reload-exclude '*/ui/dist/*' --reload-exclude '*/tmp/*' \
	  --reload-exclude '*/data/*' --reload-exclude '*/.venv/*'

dev-web:
	cd ui && API_PORT=$(PORT) WEB_PORT=$(WEB_PORT) npm run dev

# Keep ui/dist/ in sync so http://127.0.0.1:$(PORT) (the FastAPI mount) shows
# the latest SPA without a manual `make build`. tsc -b is skipped — esbuild
# still catches real errors; co-agent WIPs with stray unused locals don't
# block the watcher.
dev-web-build-watch:
	cd ui && ./node_modules/.bin/vite build --watch --logLevel warn

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
	cd form-ui && FORM_UI_PORT=$(FORM_WEB_PORT) \
	  VITE_FORM_API_BASE=$${VITE_FORM_API_BASE:-http://127.0.0.1:$(FORM_PORT)} \
	  VITE_FORM_API_KEY=$${VITE_FORM_API_KEY:-$$FORM_API_KEY} \
	  npm run dev

form-ui-build:
	cd form-ui && npm run build

# Run form-api + form-ui-dev side-by-side under `concurrently`. Same pattern
# as `make dev`. Requires FORM_API_KEY in the env; the form API refuses to
# start without one.
form-dev: kill-ports
	@test -n "$$FORM_API_KEY" || (echo "set FORM_API_KEY=… first — refusing to start an un-auth'd public surface" && exit 1)
	@test -x $(CONCURRENTLY) || (echo "missing ui/node_modules — run 'make install' first" && exit 1)
	@test -d form-ui/node_modules || (echo "missing form-ui/node_modules — run 'make form-ui-install' first" && exit 1)
	@echo "→ Form API   http://127.0.0.1:$(FORM_PORT)"
	@echo "→ Form Web   http://127.0.0.1:$(FORM_WEB_PORT)"
	$(CONCURRENTLY) -k -n form-api,form-web -c green,yellow \
	  "$(MAKE) form-api FORM_PORT=$(FORM_PORT)" \
	  "$(MAKE) form-ui-dev FORM_PORT=$(FORM_PORT) FORM_WEB_PORT=$(FORM_WEB_PORT)"

# Forwarded variant for the SSH-tunneled dev box. Mirrors dev-forwarded.
#   ssh -L $(FORWARDED_FORM_WEB_PORT):127.0.0.1:$(FORM_WEB_PORT) \
#       -L $(FORWARDED_FORM_PORT):127.0.0.1:$(FORM_PORT) <host>
form-dev-forwarded:
	$(MAKE) form-dev FORM_PORT=$(FORWARDED_FORM_PORT) FORM_WEB_PORT=$(FORWARDED_FORM_WEB_PORT)

# Test suite. Runs the ingestion package tests (CPU-only, no API keys).
test:
	$(PYTHON) -m pytest tests/ -q

# ── Agentic-labeling MCP server (stdio) ─────────────────────────────────
# Normally launched by Claude Code via ~/.claude.json; this target is for
# manual probing. Logs to tmp/mcp-server.log; stdout/stderr is reserved
# for the MCP transport so the human side runs the prompt loop blind.
mcp:
	BIM_DATABASE_API_BASE=$${BIM_DATABASE_API_BASE:-http://127.0.0.1:$(FORWARDED_PORT)} \
	  $(PYTHON) mcp_server.py
