"""Everything that touches Docker: create / start / stop / delete a server."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound

import netinfo
from publisher import Published, Publisher, published_from_row

LOG = logging.getLogger("provisioner")


class ProvisionError(RuntimeError):
    """User-facing failure (message is shown in Telegram as-is)."""


class Provisioner:
    def __init__(self, cfg, database, docker_client: Any = None, publisher: Any = None) -> None:
        self.cfg = cfg
        self.db = database
        self.docker = docker_client if docker_client is not None else docker.from_env()
        self.publisher = publisher if publisher is not None else Publisher(cfg, self.docker)
        self._lock = threading.RLock()

    # ------------------------------------------------------------ diagnostics

    def check_environment(self) -> list[str]:
        """Returns a list of human readable problems (empty list = all good)."""
        problems: list[str] = []
        try:
            self.docker.ping()
        except (DockerException, APIError, OSError) as exc:
            problems.append(
                "Нет доступа к Docker ("
                f"{exc}). Проверь, что /var/run/docker.sock смонтирован в контейнер бота."
            )
            return problems
        try:
            self.docker.images.get(self.cfg.server_image)
        except (ImageNotFound, NotFound):
            problems.append(
                f"Образ {self.cfg.server_image} не собран. Запусти: make build "
                "(или docker compose build mc-server-image)."
            )
        except (APIError, DockerException) as exc:
            problems.append(f"Docker API: {exc}")
        return problems

    # ----------------------------------------------------------------- naming

    def _container_name(self, server_id: int) -> str:
        return f"{self.cfg.container_prefix}{server_id}"

    def _volume_name(self, server_id: int) -> str:
        return f"{self.cfg.volume_prefix}{server_id}"

    def _container(self, row: dict[str, Any]) -> Any:
        try:
            return self.docker.containers.get(row["container"])
        except (NotFound, APIError) as exc:
            raise ProvisionError("Контейнер сервера не найден (возможно, удалён вручную).") from exc

    # ------------------------------------------------------------------ ports

    def allocate_port(self) -> int:
        taken = self.db.used_ports()
        for port in self.cfg.port_pool:
            if port in taken:
                continue
            if not netinfo.udp_port_free(port):
                continue
            return port
        raise ProvisionError(
            "Свободных портов не осталось. Расширь PORT_RANGE_START/PORT_RANGE_END в .env."
        )

    # ----------------------------------------------------------------- create

    def create_server(self, *, owner_id: int, chat_id: int, name: Optional[str] = None) -> dict[str, Any]:
        problems = self.check_environment()
        if problems:
            raise ProvisionError("\n".join(problems))

        if self.cfg.max_servers_per_user and not self.cfg.is_admin(owner_id):
            if self.db.count_by_owner(owner_id) >= self.cfg.max_servers_per_user:
                raise ProvisionError(
                    f"Лимит серверов на пользователя: {self.cfg.max_servers_per_user}. "
                    "Удали старый сервер и попробуй снова."
                )

        with self._lock:
            port = self.allocate_port()
            server_id = self.db.create_server(
                owner_id=owner_id,
                chat_id=chat_id,
                name=name or "GenisysPro",
                container="",
                volume="",
                port=port,
                expires_at=(time.time() + self.cfg.ttl_seconds) if self.cfg.ttl_seconds else None,
            )

        container_name = self._container_name(server_id)
        volume_name = self._volume_name(server_id)
        display_name = f"{name or 'GenisysPro'}-{server_id}"
        self.db.update(
            server_id, container=container_name, volume=volume_name, name=display_name
        )

        try:
            self.docker.volumes.create(name=volume_name, labels={"mcpe-bot": "1"})
            self._remove_container_if_exists(container_name)
            self.docker.containers.run(**self._run_kwargs(
                container_name=container_name,
                volume_name=volume_name,
                port=port,
                owner_id=owner_id,
                display_name=display_name,
            ))
        except (APIError, DockerException) as exc:
            self.db.update(server_id, status="error")
            raise ProvisionError(f"Docker не смог запустить сервер: {exc}") from exc

        published = self.publisher.publish(port, label=f"mcpe{server_id}")
        meta = dict(published.meta)
        meta["note"] = published.note
        self.db.update(
            server_id,
            status="running",
            address=published.address,
            publish_kind=published.kind,
            publish_meta=meta,
        )
        row = self.db.get(server_id)
        assert row is not None
        return row

    def _run_kwargs(
        self,
        *,
        container_name: str,
        volume_name: str,
        port: int,
        owner_id: int,
        display_name: str,
    ) -> dict[str, Any]:
        cfg = self.cfg
        environment = {
            "SERVER_PORT": str(port),
            "SERVER_NAME": display_name,
            "MOTD": f"{cfg.default_motd} #{display_name}",
            "MAX_PLAYERS": str(cfg.default_max_players),
            "GAMEMODE": str(cfg.default_gamemode),
            "DIFFICULTY": str(cfg.default_difficulty),
            "PHP_MEMORY_LIMIT": f"{cfg.php_memory_limit_mb}M",
            "TZ": cfg.timezone,
        }
        kwargs: dict[str, Any] = {
            "image": cfg.server_image,
            "name": container_name,
            "detach": True,
            "environment": environment,
            "volumes": {volume_name: {"bind": "/data", "mode": "rw"}},
            "restart_policy": {"Name": "unless-stopped"},
            "stdin_open": True,
            "tty": False,
            "mem_limit": f"{cfg.memory_limit_mb}m",
            "labels": {
                "mcpe-bot": "1",
                "mcpe-bot.role": "server",
                "mcpe-bot.owner": str(owner_id),
                "mcpe-bot.port": str(port),
            },
        }
        if cfg.cpu_limit > 0:
            kwargs["nano_cpus"] = int(cfg.cpu_limit * 1_000_000_000)
        if cfg.server_network_mode == "host":
            kwargs["network_mode"] = "host"
        else:
            kwargs["ports"] = {f"{port}/udp": (cfg.bind_ip, port)}
        return kwargs

    def _remove_container_if_exists(self, name: str) -> None:
        try:
            existing = self.docker.containers.get(name)
        except (NotFound, APIError):
            return
        try:
            existing.remove(force=True)
        except (APIError, DockerException) as exc:  # pragma: no cover
            LOG.warning("cannot remove stale container %s: %s", name, exc)

    # ------------------------------------------------------ lifecycle actions

    def start(self, row: dict[str, Any]) -> dict[str, Any]:
        container = self._container(row)
        try:
            container.start()
        except (APIError, DockerException) as exc:
            raise ProvisionError(f"Не удалось запустить: {exc}") from exc
        published = self.publisher.publish(int(row["port"]), label=f"mcpe{row['id']}")
        meta = dict(published.meta)
        meta["note"] = published.note
        expires = (time.time() + self.cfg.ttl_seconds) if self.cfg.ttl_seconds else None
        self.db.update(
            int(row["id"]),
            status="running",
            address=published.address,
            publish_kind=published.kind,
            publish_meta=meta,
            expires_at=expires,
        )
        return self.db.get(int(row["id"])) or row

    def stop(self, row: dict[str, Any], release_address: bool = True) -> dict[str, Any]:
        container = self._container(row)
        # Ask PocketMine/Genisys to save the world before killing the process.
        self.console(row, "stop", quiet=True)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            try:
                container.reload()
                if container.status != "running":
                    break
            except (APIError, NotFound):
                break
            time.sleep(1.5)
        try:
            container.stop(timeout=20)
        except (APIError, DockerException) as exc:
            LOG.warning("stop failed for %s: %s", row["container"], exc)
        if release_address:
            self.publisher.release(published_from_row(row), int(row["port"]))
        self.db.update(int(row["id"]), status="stopped", expires_at=None)
        return self.db.get(int(row["id"])) or row

    def restart(self, row: dict[str, Any]) -> dict[str, Any]:
        self.stop(row, release_address=False)
        return self.start(self.db.get(int(row["id"])) or row)

    def delete(self, row: dict[str, Any]) -> None:
        server_id = int(row["id"])
        try:
            container = self.docker.containers.get(row["container"])
            self.console(row, "stop", quiet=True)
            time.sleep(1.0)
            container.remove(force=True)
        except (NotFound, APIError, DockerException) as exc:
            LOG.info("container already gone for #%s: %s", server_id, exc)
        self.publisher.release(published_from_row(row), int(row["port"]))
        try:
            self.docker.volumes.get(row["volume"]).remove(force=True)
        except (NotFound, APIError, DockerException) as exc:
            LOG.info("volume already gone for #%s: %s", server_id, exc)
        self.db.mark_deleted(server_id)

    def refresh_address(self, row: dict[str, Any]) -> dict[str, Any]:
        published = self.publisher.publish(int(row["port"]), label=f"mcpe{row['id']}")
        meta = dict(published.meta)
        meta["note"] = published.note
        self.db.update(
            int(row["id"]),
            address=published.address,
            publish_kind=published.kind,
            publish_meta=meta,
        )
        return self.db.get(int(row["id"])) or row

    # -------------------------------------------------------------- inspect

    def status(self, row: dict[str, Any]) -> dict[str, Any]:
        info: dict[str, Any] = {"state": "unknown", "cpu": None, "memory": None}
        try:
            container = self.docker.containers.get(row["container"])
        except (NotFound, APIError):
            info["state"] = "missing"
            return info
        try:
            container.reload()
            info["state"] = container.status
            if container.status == "running":
                stats = container.stats(stream=False)
                info.update(_read_stats(stats))
        except (APIError, DockerException, KeyError, TypeError) as exc:
            LOG.debug("stats unavailable: %s", exc)
        return info

    def logs(self, row: dict[str, Any], tail: int = 40) -> str:
        container = self._container(row)
        try:
            raw = container.logs(tail=tail)
        except (APIError, DockerException) as exc:
            raise ProvisionError(f"Логи недоступны: {exc}") from exc
        text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else str(raw)
        return text.strip() or "(пусто)"

    def console(self, row: dict[str, Any], command: str, quiet: bool = False) -> bool:
        """Write a line into the server console (stdin of the container)."""
        try:
            container = self.docker.containers.get(row["container"])
            if container.status != "running":
                container.reload()
            socket_wrapper = container.attach_socket(params={"stdin": 1, "stream": 1})
            raw_socket = getattr(socket_wrapper, "_sock", socket_wrapper)
            payload = (command.strip() + "\n").encode("utf-8")
            raw_socket.sendall(payload)
            try:
                socket_wrapper.close()
            except Exception:  # noqa: BLE001
                pass
            return True
        except Exception as exc:  # noqa: BLE001
            if quiet:
                LOG.debug("console command failed: %s", exc)
                return False
            raise ProvisionError(f"Не удалось отправить команду: {exc}") from exc

    # ------------------------------------------------------------ background

    def reconcile(self) -> None:
        """Sync the DB with reality after a bot restart."""
        for row in self.db.list_live():
            state = self.status(row)["state"]
            if state == "missing":
                self.db.update(int(row["id"]), status="error")
            elif state == "running" and row["status"] != "running":
                self.db.update(int(row["id"]), status="running")
            elif state in {"exited", "created", "paused", "dead"} and row["status"] == "running":
                self.db.update(int(row["id"]), status="stopped")

    def renew_addresses(self) -> None:
        for row in self.db.list_live():
            if row["status"] != "running":
                continue
            published = published_from_row(row)
            if published.kind not in {"upnp", "natpmp"}:
                continue
            self.publisher.renew(published, int(row["port"]), label=f"mcpe{row['id']}")

    def expired_servers(self) -> list[dict[str, Any]]:
        now = time.time()
        expired = []
        for row in self.db.list_live():
            if row["status"] != "running":
                continue
            if row["expires_at"] and float(row["expires_at"]) <= now:
                expired.append(row)
        return expired


def _read_stats(stats: dict[str, Any]) -> dict[str, Any]:
    """Convert raw Docker stats into percent / MiB."""
    out: dict[str, Any] = {}
    try:
        cpu = stats["cpu_stats"]
        pre = stats.get("precpu_stats", {})
        cpu_delta = cpu["cpu_usage"]["total_usage"] - pre.get("cpu_usage", {}).get("total_usage", 0)
        system_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
        cpus = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage") or [1]) or 1
        if system_delta > 0 and cpu_delta >= 0:
            out["cpu"] = round(cpu_delta / system_delta * cpus * 100.0, 1)
    except (KeyError, TypeError, ZeroDivisionError):
        out["cpu"] = None
    try:
        memory = stats["memory_stats"]
        usage = memory.get("usage", 0) - memory.get("stats", {}).get("cache", 0)
        out["memory"] = round(max(usage, 0) / 1024 / 1024, 1)
        limit = memory.get("limit")
        if limit:
            out["memory_limit"] = round(limit / 1024 / 1024, 1)
    except (KeyError, TypeError):
        out["memory"] = None
    return out
