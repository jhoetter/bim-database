PORT   := 2500
VENV   := .venv
PYTHON := $(VENV)/bin/python
PIP    := $(VENV)/bin/pip
UV     := $(VENV)/bin/uvicorn

.PHONY: install dev dev-forwarded web build mcp validate new-house

install:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip -q
	$(PIP) install -r requirements.txt -q
	cd ui && npm install
	@echo "✓ installed – run 'make dev' (API on :$(PORT)) and 'make web' (Vite on :5173)"

dev:
	$(UV) api.main:app --reload --port $(PORT)

# Vite dev server for the React UI. Proxies /houses, /ontology, /static
# to the FastAPI on :$(PORT) — run `make dev` in a separate shell first.
web:
	cd ui && npm run dev

# Build the React bundle into ui/dist/. The FastAPI root route serves
# ui/dist/index.html, so this only matters for prod or single-port testing —
# day-to-day use `make web` instead.
build:
	cd ui && npm run build

# Alias for cross-repo convention parity with bim-ai (which uses
# `make dev-forwarded` to start API+web with the bound host its dev
# tunnel expects). bim-database has no separate web service, so this
# is just `dev`.
dev-forwarded: dev

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
