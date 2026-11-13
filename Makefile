.PHONY: dev test lint format aws-deploy aws-destroy

dev:
	@test -f .env || cp .env.example .env
	docker compose up -d --build

test:
	docker compose --profile test run --rm tests

lint:
	uv run ruff check .

format:
	uv run ruff format .

aws-deploy:
	./scripts/deploy-aws.sh

aws-destroy:
	./scripts/destroy-aws.sh
