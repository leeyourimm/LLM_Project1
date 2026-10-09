.PHONY: install up down migrate collect test lint

install:
	pip install -e ".[dev]"

up:
	docker compose -f infra/docker-compose.yml up -d

down:
	docker compose -f infra/docker-compose.yml down

migrate:
	dartrag migrate

collect:
	dartrag collect

test:
	pytest -q

lint:
	ruff check . && ruff format --check .
