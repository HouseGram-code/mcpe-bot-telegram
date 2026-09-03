#!/usr/bin/env python3
"""RakNet unconnected ping - проверка, отвечает ли сервер MCPE по UDP.

Это ровно тот запрос, который делает клиент Minecraft PE 1.1.5, когда
показывает сервер в списке. Если пинг проходит - ядро и порт живы, и причина
"не удалось подключиться" лежит дальше: фаерволл, NAT/CGNAT или адрес.
Зависимостей нет, работает на любом python3.

Примеры:
    python3 scripts/raknet_ping.py                     # 127.0.0.1:19132
    python3 scripts/raknet_ping.py 203.0.113.10 19132  # снаружи
    python3 scripts/raknet_ping.py my.ply.gg 41234 --timeout 5
"""
from __future__ import annotations

import argparse
import socket
import struct
import sys
import time

MAGIC = bytes.fromhex("00ffff00fefefefefdfdfdfd12345678")
ID_PING = 0x01
ID_PONG = 0x1C
PONG_HEADER = 1 + 8 + 8 + 16  # id + time + server guid + magic
FIELDS = (
    "edition",
    "motd",
    "protocol",
    "version",
    "players",
    "max_players",
    "server_guid",
    "sub_motd",
    "gamemode",
    "gamemode_id",
    "port_v4",
    "port_v6",
)


def build_ping(ping_time: int = 0, client_guid: int = 0x12345678) -> bytes:
    """Собирает пакет 0x01 Unconnected Ping."""
    return (
        bytes([ID_PING])
        + struct.pack(">q", ping_time & 0x7FFFFFFFFFFFFFFF)
        + MAGIC
        + struct.pack(">q", client_guid)
    )


def parse_pong(payload: bytes) -> dict:
    """Разбирает пакет 0x1c Unconnected Pong в поля MOTD."""
    if len(payload) < PONG_HEADER + 2:
        raise ValueError("ответ короче заголовка RakNet")
    if payload[0] != ID_PONG:
        raise ValueError("это не pong, а пакет 0x%02x" % payload[0])
    if payload[17:33] != MAGIC:
        raise ValueError("в ответе нет RakNet magic (порт занят другой программой?)")
    (length,) = struct.unpack(">H", payload[PONG_HEADER : PONG_HEADER + 2])
    raw = payload[PONG_HEADER + 2 : PONG_HEADER + 2 + length].decode("utf-8", "replace")
    parts = raw.split(";")
    info = {name: parts[i] for i, name in enumerate(FIELDS) if i < len(parts)}
    info["raw"] = raw
    return info


def ping(host: str, port: int = 19132, timeout: float = 3.0, attempts: int = 3) -> dict:
    """Пингует сервер и возвращает поля ответа. Бросает исключение при тишине."""
    last = "нет ответа"
    for _ in range(max(1, attempts)):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        try:
            started = time.monotonic()
            sock.sendto(build_ping(int(started * 1000)), (host, port))
            data, _addr = sock.recvfrom(4096)
            info = parse_pong(data)
            info["rtt_ms"] = "%.0f" % ((time.monotonic() - started) * 1000)
            return info
        except Exception as exc:  # таймаут, DNS, битый ответ
            last = "%s: %s" % (type(exc).__name__, exc)
        finally:
            sock.close()
    raise TimeoutError(last)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="RakNet ping для сервера MCPE")
    parser.add_argument("host", nargs="?", default="127.0.0.1")
    parser.add_argument("port", nargs="?", type=int, default=19132)
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    target = "%s:%d" % (args.host, args.port)
    try:
        info = ping(args.host, args.port, args.timeout, args.attempts)
    except Exception as exc:
        if not args.quiet:
            print("НЕТ ОТВЕТА от %s (%s)" % (target, exc))
            print("Значит: сервер не слушает этот порт, либо пакеты режет фаерволл/NAT.")
        return 1
    if args.quiet:
        print("ok")
        return 0
    print("ОТВЕТ от %s за %s мс" % (target, info.get("rtt_ms", "?")))
    print("  имя сервера : %s" % info.get("motd", "?"))
    print(
        "  версия      : %s (протокол %s)"
        % (info.get("version", "?"), info.get("protocol", "?"))
    )
    print(
        "  игроки      : %s / %s"
        % (info.get("players", "?"), info.get("max_players", "?"))
    )
    print("  строка MOTD : %s" % info.get("raw", ""))
    version = str(info.get("version", ""))
    if version and not version.startswith("1.1"):
        print(
            "  ВНИМАНИЕ: сервер отдаёт версию %s, клиент 1.1.5 к такой не подключится"
            % version
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
