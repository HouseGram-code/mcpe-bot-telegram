#!/usr/bin/env bash
# Настройка туннеля playit.gg - единственный рабочий путь, когда у машины
# нет публичного IP (NAT/CGNAT провайдера, облачные песочницы, microVM).
#
# Запуск:
#   bash scripts/playit-setup.sh                        # спросит ключ
#   bash scripts/playit-setup.sh <secret> [addr:port]   # без вопросов
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
ENV_FILE="$ROOT/.env"
COMPOSE="${COMPOSE:-docker compose}"
PORT="${PORT:-19132}"

say()  { printf '\n>> %s\n' "$*"; }
ok()   { printf '   [ok] %s\n' "$*"; }
bad()  { printf '   [!!] %s\n' "$*"; }
note() { printf '   [ .] %s\n' "$*"; }

set_env() {
  python3 - "$ENV_FILE" "$1" "$2" <<'PY'
import pathlib, sys

path, key, value = sys.argv[1], sys.argv[2], sys.argv[3]
file = pathlib.Path(path)
lines = file.read_text().splitlines() if file.exists() else []
out, done = [], False
for line in lines:
    if line.startswith(key + "="):
        if not done:
            out.append("%s=%s" % (key, value))
            done = True
        continue
    out.append(line)
if not done:
    out.append("%s=%s" % (key, value))
file.write_text("\n".join(out) + "\n")
PY
  chmod 600 "$ENV_FILE" 2>/dev/null || true
}

SECRET="${1:-}"
ADDRESS="${2:-}"

say "Шаг 1. Ключ агента playit"
note "открой https://playit.gg/account/agents/new-docker (войди или зарегистрируйся)"
note "страница сразу выдаст SECRET_KEY для docker - скопируй его"
if [ -z "$SECRET" ]; then
  if [ -t 0 ]; then
    printf '   вставь secret key (не отображается): '
    read -rs SECRET
    printf '\n'
  else
    bad "нет ключа: запусти  bash scripts/playit-setup.sh <secret key>"
    exit 1
  fi
fi
SECRET="$(printf '%s' "$SECRET" | tr -d '[:space:]')"
if [ "${#SECRET}" -lt 20 ]; then
  bad "ключ подозрительно короткий (${#SECRET} символов) - скопируй целиком"
  exit 1
fi
set_env PLAYIT_SECRET "$SECRET"
set_env ADDRESS_MODE playit
grep -qE '^PLAYIT_IMAGE=' "$ENV_FILE" || set_env PLAYIT_IMAGE ghcr.io/playit-cloud/playit-agent:latest
ok "ключ записан в .env (файл в .gitignore, права 600)"

say "Шаг 2. Запуск агента"
if ! $COMPOSE --profile playit up -d playit; then
  bad "не удалось запустить агента"
  exit 1
fi
sleep 6
docker logs --tail 15 playit 2>&1 | sed 's/^/       /'
if docker logs --tail 200 playit 2>&1 | grep -qiE 'invalid|unauthor|forbidden'; then
  bad "агент не принял ключ - проверь Шаг 1 (ключ именно для docker-агента)"
else
  ok "агент запущен (дальше смотреть: docker logs -f playit)"
fi

say "Шаг 3. Туннель в панели playit"
note "playit.gg -> Tunnels -> Add Tunnel"
note "  тип   : Minecraft Bedrock (это UDP; плагин-вариант UDP не умеет)"
note "  сеть  : Free"
note "  local : 127.0.0.1 порт $PORT"
note "панель выдаст адрес вида xxxx.ply.gg и порт - это и есть адрес сервера"
if [ -z "$ADDRESS" ] && [ -t 0 ]; then
  printf '   вставь адрес туннеля (например happy-fox.ply.gg:41234, Enter - пропустить): '
  read -r ADDRESS
fi
ADDRESS="$(printf '%s' "$ADDRESS" | tr -d '[:space:]')"
if [ -n "$ADDRESS" ]; then
  set_env PLAYIT_TUNNEL_ADDRESS "$ADDRESS"
  ok "адрес записан - бот будет выдавать его игрокам"
else
  note "адрес не задан - бот попробует вытащить его из логов агента сам"
fi

say "Шаг 4. Перезапуск бота"
if $COMPOSE restart bot >/dev/null 2>&1; then
  ok "бот перезапущен"
else
  note "перезапусти вручную: make restart"
fi

if [ -n "$ADDRESS" ]; then
  say "Шаг 5. Проверка туннеля"
  thost="${ADDRESS%%:*}"
  tport="${ADDRESS##*:}"
  [ "$thost" = "$tport" ] && tport="$PORT"
  if ! python3 "$ROOT/scripts/raknet_ping.py" "$thost" "$tport" --timeout 6; then
    note "туннель ещё поднимается или тип туннеля не Bedrock/UDP - подожди минуту и повтори"
  fi
fi

say "Готово"
note "в боте: удали старый сервер кнопкой и нажми Создать сервер - он выдаст адрес туннеля"
note "в игре 1.1.5: Play -> Servers -> Add Server, адрес и порт - в разные поля"
