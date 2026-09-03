"""Runtime configuration for the GenisysPro/LiteCore Telegram bot.

Everything is read from environment variables (see .env.example).
"""

from __future__ import annotations

import os
import pathlib
import re
from dataclasses import dataclass


def env_str(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def env_int(name: str, default: int) -> int:
    raw = env_str(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    raw = env_str(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = env_str(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "t", "yes", "y", "on", "da"}


def env_ids(name: str) -> frozenset[int]:
    ids: set[int] = set()
    raw = env_str(name).replace(";", ",").replace(" ", ",")
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if chunk.lstrip("-").isdigit():
            ids.add(int(chunk))
    return frozenset(ids)


# <6-12 digits>:<30+ chars> - the shape of a Telegram bot token.
TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")


def read_token() -> str:
    """Read the Telegram token from BOT_TOKEN or from BOT_TOKEN_FILE.

    BOT_TOKEN_FILE lets the secret live outside the repository (a Docker/Podman
    secret, /run/secrets/bot_token, a file in your home directory), so no
    tracked file ever contains it and nothing secret can be committed.
    """
    token = env_str("BOT_TOKEN")
    if not token:
        path = env_str("BOT_TOKEN_FILE")
        if path:
            try:
                token = pathlib.Path(path).read_text(encoding="utf-8")
            except OSError as error:
                raise SystemExit(f"BOT_TOKEN_FILE={path} is not readable: {error}") from error
    token = token.strip().strip('"').strip("'")
    if token and not TOKEN_RE.match(token):
        print(
            f"[config] warning: the token looks malformed ({len(token)} chars); "
            "check .env or re-issue it with /token in @BotFather",
            flush=True,
        )
    return token


@dataclass(frozen=True)
class Config:
    """Immutable snapshot of the bot configuration."""

    bot_token: str
    admin_ids: frozenset[int]
    open_for_everyone: bool

    server_image: str
    container_prefix: str
    volume_prefix: str
    server_network_mode: str
    bind_ip: str

    port_start: int
    port_end: int

    address_mode: str
    public_host: str
    upnp_lease_seconds: int
    playit_secret: str
    playit_image: str

    server_ttl_minutes: int
    max_servers_per_user: int
    memory_limit_mb: int
    cpu_limit: float
    php_memory_limit_mb: int

    default_motd: str
    default_max_players: int
    default_gamemode: int
    default_difficulty: int

    db_path: str
    log_level: str
    timezone: str
    worker_threads: int

    # ---------------------------------------------------------------- helpers

    @property
    def ttl_seconds(self) -> int:
        return max(0, self.server_ttl_minutes) * 60

    @property
    def port_pool(self) -> range:
        start = min(self.port_start, self.port_end)
        end = max(self.port_start, self.port_end)
        return range(start, end + 1)

    def is_allowed(self, user_id: int) -> bool:
        """Who may talk to the bot."""
        if self.open_for_everyone:
            return True
        if not self.admin_ids:
            # No admins configured and not public -> lock everything down.
            return False
        return user_id in self.admin_ids

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids

    # ------------------------------------------------------------------ build

    @classmethod
    def load(cls) -> "Config":
        token = read_token()
        if not token:
            raise SystemExit(
                "No token. Put the @BotFather token into .env (BOT_TOKEN=...) "
                "or point BOT_TOKEN_FILE at a file that contains it, then restart."
            )

        address_mode = env_str("ADDRESS_MODE", "auto").lower()
        if address_mode not in {"auto", "upnp", "natpmp", "playit", "direct", "manual"}:
            address_mode = "auto"

        network_mode = env_str("SERVER_NETWORK_MODE", "host").lower()
        if network_mode not in {"host", "bridge"}:
            network_mode = "host"

        return cls(
            bot_token=token,
            admin_ids=env_ids("ADMIN_IDS"),
            open_for_everyone=env_bool("OPEN_FOR_EVERYONE", False),
            server_image=env_str("SERVER_IMAGE", "genisyspro:litecore-1.1.5"),
            container_prefix=env_str("CONTAINER_PREFIX", "mcpe-srv-"),
            volume_prefix=env_str("VOLUME_PREFIX", "mcpe-data-"),
            server_network_mode=network_mode,
            bind_ip=env_str("BIND_IP", "0.0.0.0"),
            port_start=env_int("PORT_RANGE_START", 19132),
            port_end=env_int("PORT_RANGE_END", 19160),
            address_mode=address_mode,
            public_host=env_str("PUBLIC_HOST", ""),
            upnp_lease_seconds=env_int("UPNP_LEASE_SECONDS", 3600),
            playit_secret=env_str("PLAYIT_SECRET", ""),
            playit_image=env_str("PLAYIT_IMAGE", "ghcr.io/playit-cloud/playit-agent:0.15"),
            server_ttl_minutes=env_int("SERVER_TTL_MINUTES", 240),
            max_servers_per_user=env_int("MAX_SERVERS_PER_USER", 2),
            memory_limit_mb=env_int("SERVER_MEMORY_MB", 1024),
            cpu_limit=env_float("SERVER_CPU_LIMIT", 1.0),
            php_memory_limit_mb=env_int("PHP_MEMORY_LIMIT_MB", 768),
            default_motd=env_str("DEFAULT_MOTD", "GenisysPro 1.1.5"),
            default_max_players=env_int("DEFAULT_MAX_PLAYERS", 20),
            default_gamemode=env_int("DEFAULT_GAMEMODE", 0),
            default_difficulty=env_int("DEFAULT_DIFFICULTY", 2),
            db_path=env_str("DB_PATH", "/data/bot.sqlite3"),
            log_level=env_str("LOG_LEVEL", "INFO").upper(),
            timezone=env_str("TZ", "UTC"),
            worker_threads=env_int("WORKER_THREADS", 4),
        )
