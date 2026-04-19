.PHONY: help install fmt lint type test cov all clean

help:
	@echo "Targets:"
	@echo "  install   Install package + dev extras + pre-commit hooks"
	@echo "  fmt       Format code (ruff format)"
	@echo "  lint      Run linter (ruff check)"
	@echo "  type      Run type checker (mypy --strict)"
	@echo "  test      Run tests (pytest)"
	@echo "  cov       Run tests with coverage report"
	@echo "  all       fmt + lint + type + test"
	@echo "  clean     Remove caches and build artifacts"

install:
	pip install -e ".[dev]"
	pre-commit install

fmt:
	ruff format custom_components/hauskosten tests

lint:
	ruff check custom_components/hauskosten tests

type:
	mypy --strict custom_components/hauskosten

test:
	pytest

cov:
	pytest --cov=custom_components.hauskosten --cov-report=term-missing --cov-report=html --cov-branch

all: fmt lint type test

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
