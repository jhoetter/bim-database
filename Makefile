PORT   := 2200
VENV   := .venv
PYTHON := $(VENV)/bin/python
PIP    := $(VENV)/bin/pip
UV     := $(VENV)/bin/uvicorn

.PHONY: install dev

install:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip -q
	$(PIP) install -r requirements.txt -q
	@echo "✓ installed – run 'make dev' to start on http://localhost:$(PORT)"

dev:
	$(UV) api.main:app --reload --port $(PORT)
