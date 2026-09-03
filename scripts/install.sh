#!/usr/bin/env bash
# One-shot installer: Docker + .env + images + firewall + start.
# Usage:
#   bash scripts/install.sh
#   BOT_TOKEN=123:ABC ADMIN_IDS=123456789 bash scripts/install.sh   # non-interactive
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BLUE='\033[1;34m'; GREEN='\033[1;32m'; YELLOW='\033[1;33m'; RED='\033[1;31m'; NC='\033[0m'
step() { printf "${BLUE}==>${NC} %s\n" "$*"; }
ok()   { printf "${GREEN}  ok${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}  ! ${NC} %s\n" "$*"; }
fail() { printf "${RED}  x ${NC} %s\n" "$*"; exit 1; }

SUDO=""
if [[ "$(id -u)" -ne 0 ]]; then
    command -v sudo >/dev/null 2>&1 && SUDO="sudo" || fail "Запусти от root или установи sudo"
fi

# ---------------------------------------------------------------- 1. Docker
step "Проверяю Docker"
if ! command -v docker >/dev/null 2>&1; then
    warn "Docker не найден — устанавливаю через get.docker.com"
    curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
    $SUDO sh /tmp/get-docker.sh
    $SUDO systemctl enable --now docker || warn "systemd недоступен, запусти docker вручную"
    if [[ -n "${SUDO_USER:-$USER}" ]]; then
        $SUDO usermod -aG docker "${SUDO_USER:-$USER}" || true
        warn "Перезайди в систему, чтобы работать с docker без sudo"
    fi
fi
docker version >/dev/null 2>&1 || $SUDO docker version >/dev/null 2>&1 || fail "Docker не работает"
ok "$(docker --version 2>/dev/null || $SUDO docker --version)"

COMPOSE="docker compose"
if ! docker compose version >/dev/null 2>&1; then
    if command -v docker-compose >/dev/null 2>&1; then
        COMPOSE="docker-compose"
    else
        fail "Нужен docker compose plugin (apt install docker-compose-plugin)"
    fi
fi
ok "compose: $($COMPOSE version | head -n1)"

# ------------------------------------------------------------------ 2. .env
step "Готовлю .env"
if [[ ! -f .env ]]; then
    cp .env.example .env
    if [[ -z "${BOT_TOKEN:-}" ]]; then
        read -r -p "  Токен бота от @BotFather: " BOT_TOKEN
    fi
    if [[ -z "${ADMIN_IDS:-}" ]]; then
        read -r -p "  Твой Telegram ID (можно пустым, узнать командой /id): " ADMIN_IDS
    fi
    python3 - "$BOT_TOKEN" "${ADMIN_IDS:-}" <<'PY'
import pathlib, sys
token, admins = sys.argv[1], sys.argv[2]
path = pathlib.Path(".env")
lines = []
for line in path.read_text(encoding="utf-8").splitlines():
    if line.startswith("BOT_TOKEN="):
        line = f"BOT_TOKEN={token}"
    elif line.startswith("ADMIN_IDS="):
        line = f"ADMIN_IDS={admins}"
    lines.append(line)
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
    ok ".env создан"
else
    ok ".env уже есть — не трогаю"
fi
mkdir -p data secrets
chmod 600 .env 2>/dev/null || true
chmod +x scripts/*.sh .githooks/* 2>/dev/null || true
if git rev-parse --git-dir >/dev/null 2>&1; then
    git config core.hooksPath .githooks && ok "git-хук против утечки токена включён"
fi
bash scripts/check-secrets.sh all >/dev/null 2>&1 && ok "в коде нет секретов (safe для GitHub)" \
    || warn "scripts/check-secrets.sh нашёл секреты в файлах проекта"

# ------------------------------------------------------- 3. build the images
step "Собираю образ сервера (PHP 7 + GenisysPro). В первый раз долго: 10-30 мин"
$COMPOSE --profile build build server-image
ok "образ сервера готов"

step "Собираю образ бота"
$COMPOSE build bot
ok "образ бота готов"

# ------------------------------------------------------------- 4. firewall
step "Открываю UDP-порты в локальном фаерволле"
$SUDO bash scripts/firewall.sh || warn "Фаерволл не настроен автоматически"

# ---------------------------------------------------------------- 5. start
step "Запускаю бота"
$COMPOSE up -d bot
sleep 3
$COMPOSE ps

printf "\n${GREEN}Готово!${NC} Напиши своему боту /start и нажми «🚀 Создать сервер».\n"
printf "Логи: %s logs -f bot\n" "$COMPOSE"
