#!/usr/bin/env bash
# Opens the UDP port pool in the local firewall (ufw / firewalld / nftables /
# iptables). Router-side forwarding is done by the bot itself via UPnP/NAT-PMP.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -f "$ROOT/.env" ]] && set -a && . "$ROOT/.env" && set +a || true

START="${PORT_RANGE_START:-19132}"
END="${PORT_RANGE_END:-19160}"
echo "==> открываю UDP ${START}-${END}"

if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi active; then
    ufw allow "${START}:${END}/udp" comment "mcpe-bot" && echo "  ufw: ok"
    exit 0
fi

if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    firewall-cmd --permanent --add-port="${START}-${END}/udp"
    firewall-cmd --reload
    echo "  firewalld: ok"
    exit 0
fi

if command -v nft >/dev/null 2>&1 && nft list tables >/dev/null 2>&1; then
    nft list table inet mcpe >/dev/null 2>&1 || nft add table inet mcpe
    nft list chain inet mcpe input >/dev/null 2>&1 || \
        nft add chain inet mcpe input '{ type filter hook input priority 0; policy accept; }'
    nft add rule inet mcpe input udp dport "${START}-${END}" accept 2>/dev/null || true
    echo "  nftables: ok"
    exit 0
fi

if command -v iptables >/dev/null 2>&1; then
    iptables -C INPUT -p udp --dport "${START}:${END}" -j ACCEPT 2>/dev/null || \
        iptables -I INPUT -p udp --dport "${START}:${END}" -j ACCEPT
    command -v netfilter-persistent >/dev/null 2>&1 && netfilter-persistent save || true
    echo "  iptables: ok"
    exit 0
fi

echo "  Фаерволл не найден — вероятно, ничего открывать не нужно."
