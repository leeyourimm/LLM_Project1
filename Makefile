.PHONY: install up up-search down migrate collect test lint

install:
	pip install -e ".[dev]"

up:
	docker compose -f infra/docker-compose.yml up -d

up-search:
	docker compose -f infra/docker-compose.yml --profile search up -d

down:
	docker compose -f infra/docker-compose.yml --profile search down

migrate:
	dartrag migrate

collect:
	dartrag collect

test:
	pytest -q

lint:
	ruff check . && ruff format --check .
