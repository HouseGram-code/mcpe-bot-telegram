"""Helpers that answer "what is my real address?" without extra dependencies."""

from __future__ import annotations

import ipaddress
import logging
import socket
import urllib.error
import urllib.request
from typing import Optional

LOG = logging.getLogger("netinfo")

IP_ENDPOINTS = (
    "https://api.ipify.org",
    "https://ipv4.icanhazip.com",
    "https://ifconfig.me/ip",
    "https://checkip.amazonaws.com",
)


def local_ip() -> str:
    """LAN address of the host running the bot."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("1.1.1.1", 80))
        return str(sock.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def is_private(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return parsed.is_private or parsed.is_loopback or parsed.is_link_local


def public_ip(timeout: float = 6.0) -> Optional[str]:
    """Ask a few public echo services for our egress IPv4 address."""
    for url in IP_ENDPOINTS:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                text = response.read(64).decode("utf-8", "ignore").strip()
        except (urllib.error.URLError, OSError, ValueError) as exc:
            LOG.debug("%s failed: %s", url, exc)
            continue
        try:
            ipaddress.ip_address(text)
        except ValueError:
            continue
        return text
    return None


def udp_port_free(port: int, host: str = "0.0.0.0") -> bool:
    """True when nothing on the host currently listens on this UDP port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def bedrock_deeplink(name: str, address: str) -> str:
    """minecraft:// link that adds the server straight into the game client."""
    host, _, port = address.partition(":")
    port = port or "19132"
    safe_name = name.replace("|", "-").replace(" ", "%20")
    return f"minecraft://?addExternalServer={safe_name}|{host}:{port}"
