"""Minimal dependency-free UPnP IGD client.

Just enough to:
  * find the LAN router (SSDP M-SEARCH),
  * add / refresh / delete a UDP (or TCP) port mapping with a lease,
  * read the router's real external IP address.

The lease is what makes the published address *temporary*: if the bot dies,
the router drops the mapping by itself.
"""

from __future__ import annotations

import logging
import re
import socket
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlparse

LOG = logging.getLogger("upnp")

SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900

SEARCH_TARGETS = (
    "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
    "urn:schemas-upnp-org:service:WANIPConnection:1",
    "urn:schemas-upnp-org:service:WANIPConnection:2",
    "urn:schemas-upnp-org:service:WANPPPConnection:1",
)

# Preference order: IPv6-capable v2 service first, PPP last.
WAN_SERVICES = (
    "urn:schemas-upnp-org:service:WANIPConnection:2",
    "urn:schemas-upnp-org:service:WANIPConnection:1",
    "urn:schemas-upnp-org:service:WANPPPConnection:1",
)

LOCATION_RE = re.compile(rb"^location:\s*(\S+)", re.IGNORECASE | re.MULTILINE)


class UpnpError(RuntimeError):
    """Raised when the router refuses or does not answer a request."""


@dataclass(frozen=True)
class Gateway:
    location: str
    control_url: str
    service_type: str
    lan_ip: str


def _local_ip_towards(host: str) -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((host, 80))
        return str(sock.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _msearch(timeout: float) -> list[str]:
    """Broadcast SSDP discovery and collect device description URLs."""
    locations: list[str] = []
    seen: set[str] = set()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(min(1.0, timeout))
        for target in SEARCH_TARGETS:
            payload = (
                "M-SEARCH * HTTP/1.1\r\n"
                f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
                'MAN: "ssdp:discover"\r\n'
                "MX: 2\r\n"
                f"ST: {target}\r\n"
                "\r\n"
            ).encode("ascii")
            try:
                sock.sendto(payload, (SSDP_ADDR, SSDP_PORT))
            except OSError as exc:  # pragma: no cover - depends on host network
                LOG.debug("SSDP send failed: %s", exc)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, _addr = sock.recvfrom(65507)
            except socket.timeout:
                continue
            except OSError:
                break
            match = LOCATION_RE.search(data)
            if not match:
                continue
            location = match.group(1).decode("utf-8", "ignore").strip()
            if location and location not in seen:
                seen.add(location)
                locations.append(location)
    finally:
        sock.close()
    return locations


def _http_get(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "mcpe-bot/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read()


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_device_description(xml_bytes: bytes, location: str) -> Optional[tuple[str, str]]:
    """Return ``(control_url, service_type)`` for the best WAN service."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    base = location
    for node in root.iter():
        if _localname(node.tag) == "urlbase" and (node.text or "").strip():
            base = node.text.strip()
            break

    found: dict[str, str] = {}
    for node in root.iter():
        if _localname(node.tag) != "service":
            continue
        service_type = ""
        control_url = ""
        for child in node:
            name = _localname(child.tag)
            text = (child.text or "").strip()
            if name == "servicetype":
                service_type = text
            elif name == "controlurl":
                control_url = text
        if service_type in WAN_SERVICES and control_url:
            found.setdefault(service_type, control_url)

    for service_type in WAN_SERVICES:
        if service_type in found:
            return urljoin(base, found[service_type]), service_type
    return None


def discover(timeout: float = 3.0) -> Optional[Gateway]:
    """Find an Internet Gateway Device on the LAN. Returns None if there is none."""
    for location in _msearch(timeout):
        try:
            xml_bytes = _http_get(location, timeout=timeout)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            LOG.debug("cannot fetch %s: %s", location, exc)
            continue
        parsed = parse_device_description(xml_bytes, location)
        if not parsed:
            continue
        control_url, service_type = parsed
        host = urlparse(location).hostname or SSDP_ADDR
        gateway = Gateway(
            location=location,
            control_url=control_url,
            service_type=service_type,
            lan_ip=_local_ip_towards(host),
        )
        LOG.info("UPnP gateway found: %s (%s)", gateway.control_url, gateway.service_type)
        return gateway
    LOG.info("no UPnP gateway answered SSDP discovery")
    return None


def _soap(gateway: Gateway, action: str, arguments: list[tuple[str, object]], timeout: float = 6.0) -> str:
    args_xml = "".join(
        f"<{name}>{'' if value is None else value}</{name}>" for name, value in arguments
    )
    body = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        "<s:Body>"
        f'<u:{action} xmlns:u="{gateway.service_type}">{args_xml}</u:{action}>'
        "</s:Body></s:Envelope>"
    ).encode("utf-8")

    request = urllib.request.Request(
        gateway.control_url,
        data=body,
        method="POST",
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction": f'"{gateway.service_type}#{action}"',
            "User-Agent": "mcpe-bot/1.0",
            "Connection": "close",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")
        code = re.search(r"<errorCode>(\d+)</errorCode>", detail)
        raise UpnpError(
            f"{action} failed: HTTP {exc.code}"
            + (f", UPnP error {code.group(1)}" if code else "")
        ) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise UpnpError(f"{action} failed: {exc}") from exc


def add_port_mapping(
    gateway: Gateway,
    *,
    external_port: int,
    internal_port: int,
    internal_client: str = "",
    protocol: str = "UDP",
    description: str = "mcpe-bot",
    lease_seconds: int = 3600,
) -> None:
    """Open a port on the router. Re-calling it refreshes the lease."""
    client = internal_client or gateway.lan_ip
    _soap(
        gateway,
        "AddPortMapping",
        [
            ("NewRemoteHost", ""),
            ("NewExternalPort", int(external_port)),
            ("NewProtocol", protocol.upper()),
            ("NewInternalPort", int(internal_port)),
            ("NewInternalClient", client),
            ("NewEnabled", 1),
            ("NewPortMappingDescription", description),
            ("NewLeaseDuration", max(0, int(lease_seconds))),
        ],
    )


def delete_port_mapping(gateway: Gateway, *, external_port: int, protocol: str = "UDP") -> None:
    _soap(
        gateway,
        "DeletePortMapping",
        [
            ("NewRemoteHost", ""),
            ("NewExternalPort", int(external_port)),
            ("NewProtocol", protocol.upper()),
        ],
    )


def get_external_ip(gateway: Gateway) -> Optional[str]:
    response = _soap(gateway, "GetExternalIPAddress", [])
    match = re.search(r"<NewExternalIPAddress>([^<]*)</NewExternalIPAddress>", response)
    if not match:
        return None
    ip = match.group(1).strip()
    return ip or None
