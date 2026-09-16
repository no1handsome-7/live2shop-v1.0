SHELL := /bin/bash

.PHONY: help up down logs ps proto db smoke test lint train-docs

help:
	@echo "make up        - start infrastructure + services"
	@echo "make down      - stop stack"
	@echo "make logs      - follow service logs"
	@echo "make proto     - generate protobuf code (requires protoc plugins)"
	@echo "make smoke     - run API/infrastructure smoke checks"
	@echo "make test      - run lightweight unit tests"
	@echo "make db        - show database tables"
	@echo "make train-docs- print training guide location"

up:
	docker compose -f infra/docker-compose.yml --env-file .env up -d --build

down:
	docker compose -f infra/docker-compose.yml --env-file .env down

logs:
	docker compose -f infra/docker-compose.yml --env-file .env logs -f --tail=200

ps:
	docker compose -f infra/docker-compose.yml --env-file .env ps

proto:
	bash scripts/generate_proto.sh

db:
	docker compose -f infra/docker-compose.yml exec postgres \
		psql -U $${POSTGRES_USER:-live2shop} -d $${POSTGRES_DB:-live2shop} -c '\dt'

smoke:
	python scripts/smoke_test.py

test:
	PYTHONPATH=ml/commerce_vlm:ml/rec_model python -m pytest tests -q

train-docs:
	@echo docs/TRAINING_GUIDE.md
