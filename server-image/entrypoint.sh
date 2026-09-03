#!/usr/bin/env bash
# Prepares the data directory and starts GenisysPro / LiteCore 1.1.5.
set -euo pipefail

DATA_DIR="${DATA_DIR:-/data}"
PORT="${SERVER_PORT:-19132}"
SERVER_NAME="${SERVER_NAME:-GenisysPro}"
MOTD_TEXT="${MOTD:-$SERVER_NAME}"
MAX_PLAYERS="${MAX_PLAYERS:-20}"
GAMEMODE="${GAMEMODE:-0}"
DIFFICULTY="${DIFFICULTY:-2}"
LEVEL_NAME="${LEVEL_NAME:-world}"
PHP_MEMORY_LIMIT="${PHP_MEMORY_LIMIT:-768M}"
PHAR="/opt/genisys/GenisysPro.phar"

log() { printf '[entrypoint] %s\n' "$*"; }

mkdir -p "$DATA_DIR"/{players,worlds,plugins,plugin_data,resource_packs}
cd "$DATA_DIR"

# --- server.properties ------------------------------------------------------
if [[ ! -f server.properties ]]; then
    log "creating server.properties"
    cat > server.properties <<PROPS
#Properties Config file
motd=$MOTD_TEXT
server-name=$SERVER_NAME
server-port=$PORT
server-ip=0.0.0.0
white-list=off
announce-player-achievements=on
spawn-protection=16
max-players=$MAX_PLAYERS
allow-flight=off
spawn-animals=on
spawn-mobs=on
gamemode=$GAMEMODE
force-gamemode=off
hardcore=off
pvp=on
difficulty=$DIFFICULTY
generator-settings=
level-name=$LEVEL_NAME
level-type=DEFAULT
enable-query=on
enable-rcon=off
rcon.password=
auto-save=on
view-distance=8
online-mode=off
language=eng
PROPS
else
    log "updating port/motd in existing server.properties"
    sed -i "s/^server-port=.*/server-port=$PORT/" server.properties
    sed -i "s/^motd=.*/motd=$MOTD_TEXT/" server.properties
    sed -i "s/^max-players=.*/max-players=$MAX_PLAYERS/" server.properties
    grep -q '^server-ip=' server.properties || echo "server-ip=0.0.0.0" >> server.properties
fi

# --- pocketmine.yml ---------------------------------------------------------
if [[ ! -f pocketmine.yml ]]; then
    log "creating pocketmine.yml"
    cat > pocketmine.yml <<'YML'
settings:
  language: eng
  force-language: false
  shutdown-message: "Server closed"
  query-plugins: true
  deprecated-verbose: true
  enable-profiling: false
  profile-report-trigger: 20
  async-workers: auto
memory:
  global-limit: 0
  main-limit: 0
  main-hard-limit: 1024
  check-rate: 20
  continuous-trigger: true
  continuous-trigger-rate: 30
  garbage-collection:
    period: 36000
    collect-async-worker: true
    low-memory-trigger: true
  max-chunks:
    trigger-limit: 96
    low-memory-trigger: true
  world-caches:
    disable-chunk-cache: true
    low-memory-trigger: true
network:
  batch-threshold: 256
  compression-level: 6
  async-compression: false
  upnp-forwarding: false
  max-mtu-size: 1492
debug:
  level: 1
level-settings:
  default-format: mcregion
  convert-format: false
  auto-tick-rate: true
  auto-tick-rate-limit: 20
  base-tick-rate: 1
  always-tick-players: false
chunk-sending:
  per-tick: 4
  max-chunks: 192
  spawn-radius: 4
  cache-chunks: false
chunk-ticking:
  per-tick: 40
  tick-radius: 3
  light-updates: false
  clear-tick-list: true
chunk-generation:
  queue-size: 8
  population-queue-size: 8
ticks-per:
  animal-spawns: 400
  monster-spawns: 1
  autosave: 6000
  cache-cleanup: 900
aliases: {}
worlds: {}
plugins:
  legacy-data-dir: true
YML
fi

# --- ops / whitelist / banlists --------------------------------------------
touch ops.txt white-list.txt banned-players.txt banned-ips.txt
if [[ -n "${OPS:-}" ]]; then
    log "granting operator: $OPS"
    printf '%s\n' ${OPS//,/ } >> ops.txt
    sort -u -o ops.txt ops.txt
fi

log "GenisysPro / LiteCore 1.1.5 starting on UDP $PORT (data: $DATA_DIR)"
PHP_BIN="$(command -v php || echo /opt/php/bin/php)"

exec "$PHP_BIN" \
    -dmemory_limit="$PHP_MEMORY_LIMIT" \
    -dopcache.enable_cli=0 \
    -dphar.readonly=0 \
    "$PHAR" \
    --no-wizard \
    --data="$DATA_DIR/" \
    --plugins="$DATA_DIR/plugins/" \
    "$@"
