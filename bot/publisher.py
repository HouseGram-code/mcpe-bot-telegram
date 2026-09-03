"""Turns a local UDP port into a real, working, temporary public address.

Strategies, tried in this order when ADDRESS_MODE=auto:

1. ``upnp``   - ask the router to forward the UDP port (with a lease, so the
                mapping disappears by itself) and use the real WAN IP.
2. ``natpmp`` - same thing over NAT-PMP for routers with UPnP disabled.
3. ``playit`` - start a playit.gg agent container: gives a global address
                even behind CGNAT (requires PLAYIT_SECRET).
4. ``direct`` - the machine already has a public IP (VPS): just use it and
                make sure the local firewall lets the port through.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import natpmp
import netinfo
import upnp

LOG = logging.getLogger("publisher")

PLAYIT_ADDRESS_RE = re.compile(
    r"\b((?:[a-z0-9][a-z0-9\-]*\.)+(?:ply\.gg|joinmc\.link|craftmy\.pl|playit\.gg))(?::(\d{2,5}))?",
    re.IGNORECASE,
)

GATEWAY_CACHE_TTL = 300.0


@dataclass
class Published:
    """The address handed to the player plus everything needed to revoke it."""

    address: str
    kind: str  # upnp | natpmp | playit | direct | manual
    note: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def host(self) -> str:
        return self.address.rsplit(":", 1)[0] if ":" in self.address else self.address

    @property
    def port(self) -> str:
        return self.address.rsplit(":", 1)[1] if ":" in self.address else ""

    def as_dict(self) -> dict[str, Any]:
        return {"address": self.address, "kind": self.kind, "note": self.note, "meta": self.meta}


HUMAN_KIND = {
    "upnp": "UPnP: порт открыт на роутере автоматически",
    "natpmp": "NAT-PMP: порт открыт на роутере автоматически",
    "playit": "playit.gg туннель (работает даже за NAT/CGNAT)",
    "direct": "прямой публичный IP сервера",
    "manual": "адрес задан вручную в PUBLIC_HOST",
    "local": "только локальная сеть",
}


class Publisher:
    def __init__(self, cfg, docker_client: Any = None) -> None:
        self.cfg = cfg
        self.docker = docker_client
        self._lock = threading.RLock()
        self._gateway: Optional[upnp.Gateway] = None
        self._gateway_checked_at = 0.0

    # ------------------------------------------------------------------ utils

    def _get_gateway(self, force: bool = False) -> Optional[upnp.Gateway]:
        with self._lock:
            fresh = time.monotonic() - self._gateway_checked_at < GATEWAY_CACHE_TTL
            if self._gateway and fresh and not force:
                return self._gateway
            self._gateway = upnp.discover(timeout=3.0)
            self._gateway_checked_at = time.monotonic()
            return self._gateway

    def _strategies(self) -> tuple[str, ...]:
        mode = self.cfg.address_mode
        if mode == "auto":
            order = ["upnp", "natpmp"]
            if self.cfg.playit_secret:
                order.append("playit")
            order.append("direct")
            return tuple(order)
        if mode == "manual":
            return ("manual",)
        return (mode, "direct")

    # ---------------------------------------------------------------- publish

    def publish(self, port: int, label: str = "mcpe") -> Published:
        errors: list[str] = []
        for strategy in self._strategies():
            try:
                result = getattr(self, f"_publish_{strategy}")(port, label)
            except Exception as exc:  # noqa: BLE001 - fall through to next strategy
                LOG.warning("strategy %s failed: %s", strategy, exc)
                errors.append(f"{strategy}: {exc}")
                continue
            if result:
                if errors:
                    result.meta["tried"] = errors
                return result
            errors.append(f"{strategy}: недоступно")

        local = f"{netinfo.local_ip()}:{port}"
        return Published(
            address=local,
            kind="local",
            note="Не удалось открыть порт автоматически: " + "; ".join(errors[:3]),
            meta={"tried": errors},
        )

    def _publish_manual(self, port: int, label: str) -> Optional[Published]:
        host = self.cfg.public_host
        if not host:
            return None
        address = host if ":" in host else f"{host}:{port}"
        return Published(address=address, kind="manual")

    def _publish_upnp(self, port: int, label: str) -> Optional[Published]:
        gateway = self._get_gateway()
        if not gateway:
            return None
        upnp.add_port_mapping(
            gateway,
            external_port=port,
            internal_port=port,
            protocol="UDP",
            description=f"{label}-{port}",
            lease_seconds=self.cfg.upnp_lease_seconds,
        )
        wan_ip = upnp.get_external_ip(gateway) or netinfo.public_ip()
        if not wan_ip:
            upnp.delete_port_mapping(gateway, external_port=port, protocol="UDP")
            return None
        if netinfo.is_private(wan_ip):
            # Router itself sits behind the ISP NAT -> mapping is useless.
            upnp.delete_port_mapping(gateway, external_port=port, protocol="UDP")
            probed = netinfo.public_ip()
            raise RuntimeError(
                "роутер за NAT провайдера (WAN IP "
                f"{wan_ip}{', внешний ' + probed if probed else ''}) — нужен туннель"
            )
        return Published(
            address=f"{wan_ip}:{port}",
            kind="upnp",
            note=f"аренда {self.cfg.upnp_lease_seconds} c, продлевается автоматически",
            meta={"lease": self.cfg.upnp_lease_seconds, "lan_ip": gateway.lan_ip},
        )

    def _publish_natpmp(self, port: int, label: str) -> Optional[Published]:
        gateway = natpmp.default_gateway()
        if not gateway:
            return None
        mapping = natpmp.add_mapping(
            internal_port=port,
            external_port=port,
            protocol="UDP",
            lifetime=self.cfg.upnp_lease_seconds,
            gateway=gateway,
        )
        wan_ip = natpmp.external_address(gateway) or netinfo.public_ip()
        if not wan_ip or netinfo.is_private(wan_ip):
            natpmp.delete_mapping(internal_port=port, protocol="UDP", gateway=gateway)
            return None
        return Published(
            address=f"{wan_ip}:{mapping.external_port}",
            kind="natpmp",
            note=f"аренда {mapping.lifetime} c, продлевается автоматически",
            meta={"lease": mapping.lifetime, "gateway": gateway},
        )

    def _publish_direct(self, port: int, label: str) -> Optional[Published]:
        ip = netinfo.public_ip()
        if not ip:
            return None
        local = netinfo.local_ip()
        note = ""
        if local != ip:
            note = (
                "Хост за NAT: если игроки не подключаются, открой "
                f"UDP {port} (scripts/firewall.sh) или включи playit."
            )
        return Published(address=f"{ip}:{port}", kind="direct", note=note)

    # ------------------------------------------------------------- playit.gg

    def _playit_container_name(self, port: int) -> str:
        return f"playit-{port}"

    def _publish_playit(self, port: int, label: str) -> Optional[Published]:
        if not self.cfg.playit_secret or self.docker is None:
            return None
        name = self._playit_container_name(port)
        container = self._ensure_playit_container(name)
        fixed = (self.cfg.playit_tunnel_address or "").strip()
        if fixed:
            address = fixed if ":" in fixed else f"{fixed}:{port}"
            return Published(
                address=address,
                kind="playit",
                note="адрес туннеля playit.gg из настроек",
                meta={"container": name, "source": "PLAYIT_TUNNEL_ADDRESS"},
            )
        address = self._read_playit_address(container, timeout=75.0)
        if not address:
            return Published(
                address="(туннель playit.gg запускается…)",
                kind="playit",
                note=(
                    "Агент запущен, но адрес ещё не выдан. Создай в панели "
                    f"playit.gg туннель Minecraft Bedrock на 127.0.0.1:{port} "
                    "и нажми «Обновить адрес»."
                ),
                meta={"container": name},
            )
        return Published(
            address=address,
            kind="playit",
            note="глобальный адрес без проброса портов",
            meta={"container": name},
        )

    def _ensure_playit_container(self, name: str) -> Any:
        client = self.docker
        try:
            container = client.containers.get(name)
            if container.status != "running":
                container.start()
            return container
        except Exception:  # noqa: BLE001 - not found -> create it
            pass
        return client.containers.run(
            self.cfg.playit_image,
            name=name,
            detach=True,
            environment={"SECRET_KEY": self.cfg.playit_secret},
            network_mode="host",
            restart_policy={"Name": "unless-stopped"},
            labels={"mcpe-bot": "1", "mcpe-bot.role": "tunnel"},
        )

    def _read_playit_address(self, container: Any, timeout: float = 60.0) -> Optional[str]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                raw = container.logs(tail=400)
                text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else str(raw)
            except Exception as exc:  # noqa: BLE001
                LOG.debug("playit logs unavailable: %s", exc)
                text = ""
            match = PLAYIT_ADDRESS_RE.search(text)
            if match:
                host = match.group(1)
                tunnel_port = match.group(2) or "19132"
                return f"{host}:{tunnel_port}"
            time.sleep(3.0)
        return None

    # ------------------------------------------------------- renew / release

    def renew(self, published: Published, port: int, label: str = "mcpe") -> bool:
        """Refresh a leased mapping. Returns True when the address still holds."""
        try:
            if published.kind == "upnp":
                gateway = self._get_gateway()
                if not gateway:
                    return False
                upnp.add_port_mapping(
                    gateway,
                    external_port=port,
                    internal_port=port,
                    protocol="UDP",
                    description=f"{label}-{port}",
                    lease_seconds=self.cfg.upnp_lease_seconds,
                )
                return True
            if published.kind == "natpmp":
                natpmp.add_mapping(
                    internal_port=port,
                    external_port=port,
                    protocol="UDP",
                    lifetime=self.cfg.upnp_lease_seconds,
                    gateway=published.meta.get("gateway"),
                )
                return True
        except Exception as exc:  # noqa: BLE001
            LOG.warning("renew failed for port %s (%s): %s", port, published.kind, exc)
            return False
        return True

    def release(self, published: Published, port: int) -> None:
        try:
            if published.kind == "upnp":
                gateway = self._get_gateway()
                if gateway:
                    upnp.delete_port_mapping(gateway, external_port=port, protocol="UDP")
            elif published.kind == "natpmp":
                natpmp.delete_mapping(
                    internal_port=port, protocol="UDP", gateway=published.meta.get("gateway")
                )
            elif published.kind == "playit" and self.docker is not None:
                name = published.meta.get("container") or self._playit_container_name(port)
                try:
                    container = self.docker.containers.get(name)
                    container.remove(force=True)
                except Exception:  # noqa: BLE001 - already gone
                    pass
        except Exception as exc:  # noqa: BLE001
            LOG.warning("release failed for port %s (%s): %s", port, published.kind, exc)


def published_from_row(row: dict[str, Any]) -> Published:
    meta = row.get("publish_meta") or {}
    if not isinstance(meta, dict):
        meta = {}
    return Published(
        address=row.get("address") or "",
        kind=row.get("publish_kind") or "manual",
        note=str(meta.get("note", "")),
        meta=meta,
    )
