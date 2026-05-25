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

.PHONY: install dev dev-forwarded dev-api dev-web kill-ports build mcp validate new-house refresh-issue-state derive-quality warm-cache clean-cache

install:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip -q
	$(PIP) install -r requirements.txt -q
	cd ui && npm install
	@echo "✓ installed – run 'make dev' (API on :$(PORT), Vite on :$(WEB_PORT))"

# Runs FastAPI + Vite side-by-side under `concurrently`, mirroring bim-ai's
# `make dev`. Logs are prefixed [api] / [web]; Ctrl-C kills both.
# - API serves /houses, /ontology, /static, and the built bundle at /.
# - Vite serves the React app with HMR and proxies API paths back to FastAPI.
# Open http://127.0.0.1:$(WEB_PORT) — Vite is the day-to-day entry.
dev: kill-ports
	@echo "→ API   http://127.0.0.1:$(PORT)"
	@echo "→ Web   http://127.0.0.1:$(WEB_PORT)  (proxies /houses, /ontology, /static)"
	@test -x $(CONCURRENTLY) || (echo "missing ui/node_modules — run 'make install' first" && exit 1)
	API_PORT=$(PORT) WEB_PORT=$(WEB_PORT) $(CONCURRENTLY) -k -n api,web -c blue,magenta \
	  "$(MAKE) dev-api PORT=$(PORT)" \
	  "$(MAKE) dev-web WEB_PORT=$(WEB_PORT) PORT=$(PORT)"

# Same dev profile, shifted to a port pair that survives `ssh -L` forwarding
# without colliding with sister apps. Matches the bim-ai convention.
dev-forwarded:
	$(MAKE) dev PORT=$(FORWARDED_PORT) WEB_PORT=$(FORWARDED_WEB_PORT)

dev-api:
	$(UV) api.main:app --reload --host 127.0.0.1 --port $(PORT)

dev-web:
	cd ui && API_PORT=$(PORT) WEB_PORT=$(WEB_PORT) npm run dev

# Free up the dev ports before starting — survives a previous run that
# didn't shut down cleanly. Quiet on success.
kill-ports:
	@for p in $(PORT) $(WEB_PORT); do \
	  pids=$$(lsof -ti :$$p 2>/dev/null); \
	  [ -n "$$pids" ] && kill -9 $$pids 2>/dev/null || true; \
	done; true

# Build the React bundle into ui/dist/. The FastAPI root route serves
# ui/dist/index.html, so this only matters for single-port testing or
# prod-style runs — day-to-day `make dev` instead.
build:
	cd ui && npm run build

mcp:
	$(PYTHON) mcp_server.py

# Scaffold a new house record. Usage:
#   make new-house ID=24 MODEL="EFH Sonnenhang"
new-house:
	@test -n "$(ID)"    || (echo "usage: make new-house ID=<int> MODEL=\"<name>\""; exit 1)
	@test -n "$(MODEL)" || (echo "usage: make new-house ID=<int> MODEL=\"<name>\""; exit 1)
	$(PYTHON) scripts/new_house.py --id $(ID) --model "$(MODEL)"

# Validate every data/houses/*.json against schema/house.schema.json and
# the ontology in data/ontology.json.
validate:
	$(PYTHON) scripts/validate.py

# Refresh data/.issue_state.json with the current open/closed state of
# every bim_ai_blocking_issues reference. The UI derives each house's
# `modelable_in_bim_ai` flag from this cache. Uses the gh CLI.
refresh-issue-state:
	$(PYTHON) scripts/refresh_issue_state.py

# Auto-derive each house's data_quality from its images array + source.
# Preserves human-set 'fully_specified' / 'wall_buildup' / etc. axes.
derive-quality:
	$(PYTHON) scripts/derive_data_quality.py

# Pre-render every PDF-sourced scene into tmp/scene-cache/. The API renders
# on demand too, so warming is purely for first-request latency. Re-renders
# only when the source JSON is newer than the cached image.
warm-cache:
	$(PYTHON) scripts/render_scene.py --all

clean-cache:
	rm -rf tmp/scene-cache
