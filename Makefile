SHELL := /bin/bash
COMPOSE ?= docker compose

.PHONY: help install build up down restart logs bot-logs ps test firewall hooks check-secrets clean

help:
	@echo "make install   - всё сам: Docker, .env, сборка образов, порты, старт"
	@echo "make build     - собрать образ сервера GenisysPro и образ бота"
	@echo "make up        - запустить бота"
	@echo "make down      - остановить бота"
	@echo "make restart   - перезапустить бота"
	@echo "make logs      - логи бота"
	@echo "make ps        - список контейнеров бота и серверов"
	@echo "make firewall  - открыть UDP-порты в локальном фаерволле"
	@echo "make test      - оффлайн-тесты (без Docker и сети)"
	@echo "make hooks     - включить git-хук, который не даст закоммитить токен"
	@echo "make check-secrets - проверить весь код на секреты (как на GitHub)"

install:
	@bash scripts/install.sh

build:
	$(COMPOSE) --profile build build server-image
	$(COMPOSE) build bot

up:
	$(COMPOSE) up -d bot

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart bot

logs bot-logs:
	$(COMPOSE) logs -f --tail=200 bot

ps:
	@docker ps --filter "label=mcpe-bot=1" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
	@$(COMPOSE) ps

firewall:
	@sudo bash scripts/firewall.sh

test:
	@python3 tests/selftest.py

hooks:
	@git rev-parse --git-dir >/dev/null 2>&1 || git init -q
	@chmod +x .githooks/pre-commit scripts/*.sh
	@git config core.hooksPath .githooks
	@echo "git-хук включён: коммит с токеном, .env или базой будет отклонён"

check-secrets:
	@bash scripts/check-secrets.sh all

clean:
	$(COMPOSE) down -v
	@docker ps -aq --filter "label=mcpe-bot=1" | xargs -r docker rm -f
	@docker volume ls -q --filter "label=mcpe-bot=1" | xargs -r docker volume rm
