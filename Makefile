PORT     ?= 2500
WEB_PORT ?= 5173

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

.PHONY: install dev dev-forwarded dev-api dev-web kill-ports build cleanup-houses

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
	@for p in $(PORT) $(WEB_PORT); do \
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
