"""Tiny NAT-PMP client (RFC 6886) used as a fallback when UPnP is disabled.

Many routers (Apple, OpenWrt with miniupnpd, some ISP boxes) keep NAT-PMP on
even when UPnP IGD is off, so trying both roughly doubles the chance that the
bot can open the UDP port automatically.
"""

from __future__ import annotations

import logging
import socket
import struct
from dataclasses import dataclass
from typing import Optional

LOG = logging.getLogger("natpmp")

NATPMP_PORT = 5351
OP_EXTERNAL_ADDRESS = 0
OP_MAP_UDP = 1
OP_MAP_TCP = 2

RESULT_CODES = {
    0: "success",
    1: "unsupported version",
    2: "not authorized (NAT-PMP disabled on the router)",
    3: "network failure",
    4: "out of resources",
    5: "unsupported opcode",
}


class NatPmpError(RuntimeError):
    pass


@dataclass(frozen=True)
class Mapping:
    internal_port: int
    external_port: int
    lifetime: int


def default_gateway() -> Optional[str]:
    """Read the IPv4 default gateway from /proc/net/route (Linux)."""
    try:
        with open("/proc/net/route", "r", encoding="ascii") as handle:
            next(handle, None)  # header
            for line in handle:
                fields = line.split()
                if len(fields) < 3:
                    continue
                destination, gateway_hex, flags = fields[1], fields[2], int(fields[3], 16)
                if destination != "00000000" or not flags & 0x2:
                    continue
                packed = struct.pack("<L", int(gateway_hex, 16))
                return socket.inet_ntoa(packed)
    except (OSError, ValueError, StopIteration):
        return None
    return None


def _request(gateway: str, payload: bytes, expect_len: int, timeout: float = 2.0) -> bytes:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        for _attempt in range(3):
            try:
                sock.sendto(payload, (gateway, NATPMP_PORT))
                data, _addr = sock.recvfrom(64)
            except socket.timeout:
                continue
            except OSError as exc:
                raise NatPmpError(str(exc)) from exc
            if len(data) < expect_len:
                continue
            result = struct.unpack("!H", data[2:4])[0]
            if result != 0:
                raise NatPmpError(RESULT_CODES.get(result, f"error {result}"))
            return data
        raise NatPmpError("no answer from the router")
    finally:
        sock.close()


def external_address(gateway: Optional[str] = None, timeout: float = 2.0) -> Optional[str]:
    gateway = gateway or default_gateway()
    if not gateway:
        return None
    data = _request(gateway, struct.pack("!BB", 0, OP_EXTERNAL_ADDRESS), 12, timeout)
    return socket.inet_ntoa(data[8:12])


def add_mapping(
    *,
    internal_port: int,
    external_port: int,
    protocol: str = "UDP",
    lifetime: int = 3600,
    gateway: Optional[str] = None,
    timeout: float = 2.0,
) -> Mapping:
    gateway = gateway or default_gateway()
    if not gateway:
        raise NatPmpError("default gateway not found")
    opcode = OP_MAP_UDP if protocol.upper() == "UDP" else OP_MAP_TCP
    payload = struct.pack(
        "!BBHHHI", 0, opcode, 0, int(internal_port), int(external_port), max(0, int(lifetime))
    )
    data = _request(gateway, payload, 16, timeout)
    internal, external, granted = struct.unpack("!HHI", data[8:16])
    return Mapping(internal_port=internal, external_port=external, lifetime=granted)


def delete_mapping(
    *,
    internal_port: int,
    protocol: str = "UDP",
    gateway: Optional[str] = None,
    timeout: float = 2.0,
) -> None:
    """A mapping with lifetime 0 and external port 0 removes the mapping."""
    add_mapping(
        internal_port=internal_port,
        external_port=0,
        protocol=protocol,
        lifetime=0,
        gateway=gateway,
        timeout=timeout,
    )
