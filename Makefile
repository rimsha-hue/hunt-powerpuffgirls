.PHONY: install dev frontend test lint format audit build run clean

IMAGE ?= power-hunt

install:
	pip install -e ".[dev]"
	cd frontend && npm install

dev:
	uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

test:
	pytest --cov=app --cov-report=term-missing

lint:
	ruff check app tests
	ruff format --check app tests
	mypy app

format:
	ruff format app tests
	ruff check --fix app tests

audit:
	bandit -c pyproject.toml -r app -ll
	pip-audit --skip-editable

build:
	docker build -t $(IMAGE) .

run: build
	docker run --rm -p 8000:8000 -e SECRET_KEY=local-dev-only-change-me-0123456789 $(IMAGE)

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage frontend/dist
