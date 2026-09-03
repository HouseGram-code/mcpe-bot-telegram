#!/usr/bin/env bash
# Диагностика "сервер запустился, но подключиться не получается".
# Запуск:  bash scripts/diag.sh [порт]
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
DB="$ROOT/data/bot.sqlite3"
PY="$(command -v python3 || true)"
PORT="${1:-}"

sec()  { printf '\n=== %s ===\n' "$*"; }
ok()   { printf '  [ok] %s\n' "$*"; }
bad()  { printf '  [!!] %s\n' "$*"; }
note() { printf '  [ .] %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

env_get() {
  [ -f "$ROOT/.env" ] || return 0
  sed -n "s/^$1=//p" "$ROOT/.env" | head -n1 | tr -d '\r"'
}

NET_MODE="$(env_get SERVER_NETWORK_MODE)"; NET_MODE="${NET_MODE:-host}"
ADDR_MODE="$(env_get ADDRESS_MODE)";       ADDR_MODE="${ADDR_MODE:-auto}"
PUBLIC_HOST="$(env_get PUBLIC_HOST)"
PLAYIT_SECRET="$(env_get PLAYIT_SECRET)"
LOCAL_OK=0; PUB_IP=""; PUB_KIND=""; DB_ADDR=""; DB_KIND=""; CTR=""; BEHIND_NAT=0

sec "Конфигурация"
note "режим сети контейнера : $NET_MODE"
note "режим адреса          : $ADDR_MODE"
[ -n "$PUBLIC_HOST" ] && note "PUBLIC_HOST           : $PUBLIC_HOST"
if [ -n "$PLAYIT_SECRET" ]; then
  ok "PLAYIT_SECRET задан - туннель доступен"
else
  note "PLAYIT_SECRET пуст - туннель playit.gg выключен"
fi

sec "Контейнеры сервера"
if have docker; then
  RUNNING="$(docker ps --format '{{.Names}}' 2>/dev/null | grep '^mcpe-srv-' || true)"
  if [ -z "$RUNNING" ]; then
    bad "запущенных контейнеров mcpe-srv-* нет"
    docker ps -a --format '{{.Names}} {{.Status}}' 2>/dev/null | grep 'mcpe-srv-' | sed 's/^/  [ .] /' || true
  else
    while read -r c; do
      [ -n "$c" ] || continue
      CTR="${CTR:-$c}"
      cmode="$(docker inspect -f '{{.HostConfig.NetworkMode}}' "$c" 2>/dev/null || echo '?')"
      cst="$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null || echo '?')"
      cports="$(docker port "$c" 2>/dev/null | tr '\n' ' ')"
      cenvp="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$c" 2>/dev/null | sed -n 's/^SERVER_PORT=//p' | head -n1)"
      [ -n "$cenvp" ] && PORT="${PORT:-$cenvp}"
      ok "$c  статус=$cst  сеть=$cmode  порт=${cenvp:-?}  проброс=${cports:-нет (host-режим)}"
      if [ "$cmode" != "host" ] && [ -z "$cports" ]; then
        bad "контейнер в режиме $cmode и не пробрасывает UDP-порт наружу"
      fi
    done <<< "$RUNNING"
  fi
else
  bad "docker не найден"
fi
PORT="${PORT:-19132}"

sec "Слушает ли кто-то UDP $PORT на хосте"
LIST=""
if have ss; then
  LIST="$(ss -lunp 2>/dev/null | grep -E "[:.]$PORT([[:space:]]|$)" || true)"
  if [ -z "$LIST" ] && have sudo; then
    LIST="$(sudo -n ss -lunp 2>/dev/null | grep -E "[:.]$PORT([[:space:]]|$)" || true)"
  fi
elif have netstat; then
  LIST="$(netstat -lunp 2>/dev/null | grep -E "[:.]$PORT([[:space:]]|$)" || true)"
fi
if [ -n "$LIST" ]; then
  ok "порт занят процессом:"
  printf '%s\n' "$LIST" | sed 's/^/       /'
else
  bad "на хосте никто не слушает UDP $PORT"
fi

sec "RakNet-пинг с самой машины (127.0.0.1:$PORT)"
if [ -n "$PY" ] && [ -f "$ROOT/scripts/raknet_ping.py" ]; then
  if "$PY" "$ROOT/scripts/raknet_ping.py" 127.0.0.1 "$PORT" --timeout 3 --attempts 2; then
    LOCAL_OK=1
  fi
else
  note "нет python3 или scripts/raknet_ping.py"
fi

sec "Адрес, который выдал бот"
if [ -n "$PY" ] && [ -f "$DB" ]; then
  while IFS='|' read -r tag sid sname sport saddr skind sstat; do
    case "$tag" in
      ROW)
        ok "#$sid $sname  порт=$sport  адрес=$saddr  способ=$skind  статус=$sstat"
        DB_ADDR="${DB_ADDR:-$saddr}"; DB_KIND="${DB_KIND:-$skind}"
        ;;
      ERR) note "база не читается: $sid" ;;
    esac
  done < <("$PY" - "$DB" <<'PYDB'
import sqlite3, sys
try:
    con = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True)
    rows = con.execute(
        "SELECT id, name, port, address, publish_kind, status FROM servers "
        "WHERE status != 'deleted' ORDER BY id DESC LIMIT 5"
    ).fetchall()
except Exception as exc:
    print("ERR|%s|||||" % exc)
    raise SystemExit(0)
for row in rows:
    print("ROW|%s|%s|%s|%s|%s|%s" % tuple("" if v is None else v for v in row))
PYDB
)
else
  note "базы data/bot.sqlite3 нет - бот ещё ничего не создавал"
fi

sec "Внешний адрес машины"
if have curl; then
  PUB_IP="$(curl -fsS --max-time 6 https://api.ipify.org 2>/dev/null || curl -fsS --max-time 6 https://ipv4.icanhazip.com 2>/dev/null || true)"
  PUB_IP="$(printf '%s' "$PUB_IP" | tr -d '[:space:]')"
fi
if [ -n "$PUB_IP" ] && [ -n "$PY" ]; then
  PUB_KIND="$("$PY" - "$PUB_IP" <<'PYIP'
import ipaddress, sys
try:
    ip = ipaddress.ip_address(sys.argv[1])
except ValueError:
    print("nonip")
    raise SystemExit(0)
low = ipaddress.ip_address("100.64.0.0")
high = ipaddress.ip_address("100.127.255.255")
if ip.is_loopback:
    print("loopback")
elif ip.version == 4 and low <= ip <= high:
    print("cgnat")
elif ip.is_private:
    print("private")
else:
    print("public")
PYIP
)"
fi
case "$PUB_KIND" in
  public)  ok "$PUB_IP - обычный публичный IP" ;;
  cgnat)   bad "$PUB_IP - CGNAT (100.64.0.0/10): порт снаружи открыть невозможно" ;;
  private) bad "$PUB_IP - приватный адрес: машина не видна из интернета" ;;
  loopback) bad "внешний адрес определился как loopback" ;;
  *)       note "внешний IP определить не удалось (нет сети или curl)" ;;
esac
if have ip; then
  note "локальные адреса: $(ip -4 -o addr show scope global 2>/dev/null | awk '{print $2"="$4}' | tr '\n' ' ')"
  if ip -4 -o addr show scope global 2>/dev/null | grep -qE 'inet 169\.254\.'; then
    BEHIND_NAT=1
    bad "на интерфейсе link-local 169.254.x.x - машина за NAT провайдера (песочница/microVM)"
  fi
  if [ -n "$PUB_IP" ]; then
    if ip -4 -o addr show scope global 2>/dev/null | grep -q "inet $PUB_IP/"; then
      ok "внешний IP настроен прямо на интерфейсе - NAT нет"
    else
      BEHIND_NAT=1
      bad "внешнего IP нет ни на одном интерфейсе - машина за NAT, входящий UDP невозможен"
    fi
  fi
fi

sec "Фаерволл"
UFW_OUT=""
if have ufw; then
  UFW_OUT="$(ufw status 2>/dev/null || sudo -n ufw status 2>/dev/null || true)"
fi
if [ -n "$UFW_OUT" ]; then
  printf '%s\n' "$UFW_OUT" | sed 's/^/       /'
  if printf '%s' "$UFW_OUT" | grep -qi 'inactive'; then
    ok "ufw выключен - локально ничего не блокирует"
  elif printf '%s' "$UFW_OUT" | grep -q "$PORT"; then
    ok "правило для порта $PORT есть"
  else
    bad "ufw включён, правила для UDP $PORT не видно - запусти: sudo make firewall"
  fi
else
  note "ufw недоступен без sudo - проверь вручную: sudo ufw status"
fi

sec "Туннель playit.gg"
if have docker; then
  PL="$(docker ps --format '{{.Names}} {{.Status}}' 2>/dev/null | grep -i playit || true)"
  if [ -n "$PL" ]; then
    ok "агент запущен: $PL"
    docker logs --tail 5 "$(printf '%s' "$PL" | awk 'NR==1{print $1}')" 2>&1 | sed 's/^/       /'
  else
    note "агент playit не запущен"
  fi
fi

sec "Вердикт"
if [ "$LOCAL_OK" != "1" ]; then
  bad "Сервер не отвечает даже на локальный пинг - причина внутри машины, не в интернете."
  note "логи ядра:  docker logs --tail 80 ${CTR:-mcpe-srv-XXXX}"
  note "если сеть контейнера не host: поставь SERVER_NETWORK_MODE=host в .env и пересоздай сервер"
else
  ok "Ядро GenisysPro живо и отвечает по UDP $PORT на самой машине."
  if [ "$BEHIND_NAT" = "1" ] || [ "$PUB_KIND" != "public" ]; then
    bad "Прямое подключение невозможно: публичного IP на машине нет (NAT/CGNAT провайдера)."
    note "Проброс портов, ufw и UPnP тут не помогут - нужен туннель:"
    note "    bash scripts/playit-setup.sh"
    note "Скрипт сам впишет ключ в .env, поднимет агента и проверит адрес."
  else
    note "1) Проверь снаружи (с телефона по мобильному интернету):"
    note "     python3 scripts/raknet_ping.py $PUB_IP $PORT"
    note "   или открой https://api.mcsrvstat.us/bedrock/3/$PUB_IP:$PORT"
    note "2) Тишина - UDP $PORT закрыт: открой его в панели провайдера и локально: sudo make firewall"
    note "3) Есть ответ - в игре: Play - Servers - Add Server, адрес $PUB_IP, порт $PORT"
  fi
  [ -n "$DB_ADDR" ] && note "бот выдавал адрес: $DB_ADDR (способ: ${DB_KIND:-?})"
fi
printf '\n'
note "клиент 1.1.5: Play - Servers - Add Server (внешний сервер), адрес и порт - из пунктов выше"
note "из той же Wi-Fi сети подключайся по локальному адресу машины: hairpin NAT часто не работает"
