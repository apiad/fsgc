.PHONY: all test lint format docs-serve docs-deploy

all: format lint test

test:
	@uv run pytest

lint:
	@uv run ruff check .
	@uv run ruff format --check .
	@uv run mypy src/

format:
	@uv run ruff check --fix .
	@uv run ruff format .

docs-serve:
	@uv run mkdocs serve

docs-deploy:
	@uv run mkdocs gh-deploy --force
