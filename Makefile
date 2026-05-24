PORT   := 2500
VENV   := .venv
PYTHON := $(VENV)/bin/python
PIP    := $(VENV)/bin/pip
UV     := $(VENV)/bin/uvicorn

.PHONY: install dev dev-forwarded mcp

install:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip -q
	$(PIP) install -r requirements.txt -q
	@echo "✓ installed – run 'make dev' to start on http://localhost:$(PORT)"

dev:
	$(UV) api.main:app --reload --port $(PORT)

# Alias for cross-repo convention parity with bim-ai (which uses
# `make dev-forwarded` to start API+web with the bound host its dev
# tunnel expects). bim-database has no separate web service, so this
# is just `dev`.
dev-forwarded: dev

mcp:
	$(PYTHON) mcp_server.py
