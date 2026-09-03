#!/usr/bin/env python3
"""Вытащить публичный адрес туннеля (host:port) из логов агента.

Читает лог со stdin, печатает адрес или выходит с кодом 1.

    docker logs mcpe-tunnel 2>&1 | python3 scripts/parse_tunnel_address.py 19132
"""
from __future__ import annotations

import re
import sys
from typing import Optional

# udp://host:port или tcp://host:port - самый надёжный вариант
SCHEME = re.compile(r"(?:udp|tcp)://([A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}):(\d{2,5})")

# Знакомые домены туннелей: адрес и порт могут быть разделены текстом
KNOWN = r"(?:pinggy\.(?:link|online|io)|ply\.gg|joinmc\.link|craftmy\.pl|playit\.gg)"
PAIR = re.compile(r"\b([A-Za-z0-9][A-Za-z0-9.-]*\." + KNOWN + r")\b(?:\D{0,16}?(\d{2,5}))?")
PORT_NEAR = re.compile(r"(?:port|порт)\D{0,4}(\d{2,5})", re.IGNORECASE)


def find(text: str, default_port: int = 19132) -> Optional[str]:
    """Вернуть последний найденный адрес: агент переподключается и меняет порт."""
    scheme_hits = SCHEME.findall(text)
    if scheme_hits:
        host, port = scheme_hits[-1]
        return "%s:%s" % (host, port)

    pair_hits = PAIR.findall(text)
    if pair_hits:
        host, port = pair_hits[-1]
        if not port:
            near = PORT_NEAR.findall(text)
            port = near[-1] if near else str(default_port)
        return "%s:%s" % (host, port)

    return None


def main() -> int:
    default_port = int(sys.argv[1]) if len(sys.argv) > 1 else 19132
    address = find(sys.stdin.read(), default_port)
    if not address:
        return 1
    print(address)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
