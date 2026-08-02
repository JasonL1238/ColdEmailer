PYTHON := backend/venv/bin/python
TEST ?=

.PHONY: help check-agent-docs lint typecheck test-backend test-frontend build-frontend validate

help:
	@printf '%s\n' \
	  'make test-backend TEST=unit/backend/test_pipeline.py' \
	  'make test-frontend TEST=tests/unit/pipeline.test.jsx' \
	  'make lint | typecheck | build-frontend | validate'

check-agent-docs:
	@$(PYTHON) scripts/check_agent_docs.py

lint:
	@$(PYTHON) -m ruff check backend tests scripts

typecheck:
	@$(PYTHON) -m mypy --ignore-missing-imports --follow-imports=skip \
	  --allow-untyped-globals --disable-error-code=var-annotated \
	  backend/text_cleaner.py backend/analytics.py backend/pipeline.py \
	  backend/contact_verify.py backend/response_checker.py

test-backend:
	@cd tests && ../$(PYTHON) -m pytest -q $(TEST)

test-frontend:
	@cd frontend && npm test -- $(TEST)

build-frontend:
	@cd frontend && npm run build

validate: check-agent-docs lint typecheck test-backend test-frontend build-frontend
