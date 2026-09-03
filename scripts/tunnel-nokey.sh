#!/usr/bin/env bash
# Публичный UDP-адрес БЕЗ ключа и регистрации - Pinggy (free-тариф).
#
# Free-тариф: туннель живёт 60 минут, потом адрес меняется.
# Режим --watch сам следит за адресом и обновляет бота.
#
#   bash scripts/tunnel-nokey.sh                 # поднять туннель один раз
#   bash scripts/tunnel-nokey.sh --watch         # держать адрес свежим
#   PORT=19133 bash scripts/tunnel-nokey.sh      # другой порт сервера
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
ENV_FILE="$ROOT/.env"
PORT="${PORT:-19132}"
CTR="${CTR:-mcpe-tunnel}"
NODE_IMAGE="${NODE_IMAGE:-node:22-alpine}"
COMPOSE="${COMPOSE:-docker compose}"
WATCH=0
[ "${1:-}" = "--watch" ] && WATCH=1

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
}

start_tunnel() {
  docker rm -f "$CTR" >/dev/null 2>&1
  docker run -d --name "$CTR" --network host --restart unless-stopped \
    "$NODE_IMAGE" sh -c "npm install -g pinggy >/tmp/install.log 2>&1 && exec pinggy --type udp -l $PORT --v" >/dev/null
}

read_address() {
  local tail="${1:-2000}" waited=0 addr=""
  while [ "$waited" -lt 180 ]; do
    addr="$(docker logs --tail "$tail" "$CTR" 2>&1 | python3 "$ROOT/scripts/parse_tunnel_address.py" "$PORT")"
    if [ -n "$addr" ]; then
      printf '%s' "$addr"
      return 0
    fi
    sleep 5
    waited=$((waited + 5))
  done
  return 1
}

publish() {
  set_env ADDRESS_MODE manual
  set_env PUBLIC_HOST "$1"
  $COMPOSE restart bot >/dev/null 2>&1 || note "перезапусти бота вручную: make restart"
}

check_address() {
  local addr="$1" host port
  host="${addr%%:*}"
  port="${addr##*:}"
  [ "$host" = "$port" ] && port="$PORT"
  python3 "$ROOT/scripts/raknet_ping.py" "$host" "$port" --timeout 6
}

if ! command -v docker >/dev/null 2>&1; then
  bad "docker не найден"
  exit 1
fi

say "Шаг 1. Запуск туннеля (ключ не нужен)"
note "Pinggy UDP -> 127.0.0.1:$PORT, контейнер $CTR"
start_tunnel || { bad "не удалось запустить контейнер"; exit 1; }
note "ставится pinggy и поднимается туннель, это до 2 минут..."

ADDRESS="$(read_address)"
if [ -z "$ADDRESS" ]; then
  bad "адрес не появился в логах"
  note "смотри: docker logs $CTR"
  exit 1
fi
ok "адрес туннеля: $ADDRESS"

say "Шаг 2. Бот выдаёт этот адрес"
publish "$ADDRESS"
ok "в .env: ADDRESS_MODE=manual, PUBLIC_HOST=$ADDRESS"

say "Шаг 3. Проверка"
if check_address "$ADDRESS"; then
  ok "сервер ответил через туннель - можно заходить"
else
  note "туннель ещё поднимается - подожди минуту и повтори проверку"
fi

say "В игре 1.1.5"
note "Play -> Servers -> Add Server"
note "  адрес: ${ADDRESS%%:*}"
note "  порт : ${ADDRESS##*:}"
note "адрес и порт - в разные поля, двоеточие не пишем"

if [ "$WATCH" -eq 0 ]; then
  say "Важно про free-тариф"
  note "через 60 минут Pinggy выдаст новый порт. Чтобы адрес обновлялся сам:"
  note "  nohup bash scripts/tunnel-nokey.sh --watch > tunnel.log 2>&1 &"
  exit 0
fi

say "Режим --watch: слежу за адресом (Ctrl+C - выход)"
LAST="$ADDRESS"
while true; do
  sleep 30
  if ! docker ps --format '{{.Names}}' | grep -qx "$CTR"; then
    note "туннель упал - поднимаю заново"
    start_tunnel
    sleep 20
  fi
  NOW="$(docker logs --tail 200 "$CTR" 2>&1 | python3 "$ROOT/scripts/parse_tunnel_address.py" "$PORT")"
  if [ -n "$NOW" ] && [ "$NOW" != "$LAST" ]; then
    LAST="$NOW"
    publish "$NOW"
    ok "новый адрес: $NOW (скажи его игрокам)"
  fi
done
