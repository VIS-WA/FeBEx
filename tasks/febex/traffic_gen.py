"""
FeBEx Traffic Generator
=======================
Runs on a gateway host inside Mininet.  Reads a coverage JSON file and
sends FeBEx-encapsulated UDP packets for every edge device this gateway
hears, for each uplink frame counter (FCnt).

Custom scapy layer FeBExMeta (12 bytes):
    dev_addr  : 32b
    fcnt      : 32b
    gw_id     : 16b
    flags     :  8b  (set 0)
    padding   :  8b  (set 0)

Usage (from within a Mininet host):
    python3 traffic_gen.py \\
        --gw-id 1 \\
        --coverage coverage.json \\
        --uplinks 50 \\
        --inter-arrival-ms 100 \\
        --iface gw1-eth0 \\
        --src-ip 10.0.1.1 \\
        --src-mac 00:00:00:00:01:01
"""

import argparse
import json
import struct
import sys
import time

from scapy.all import (
    Ether,
    IP,
    UDP,
    Raw,
    Packet,
    BitField,
    sendp,
)
from scapy.fields import XIntField, ShortField, ByteField


# ── Custom scapy layer ──────────────────────────────────────────────────

class FeBExMeta(Packet):
    """12-byte FeBEx metadata header (UDP payload prefix)."""
    name = "FeBExMeta"
    fields_desc = [
        XIntField("dev_addr", 0),   # 32b DevAddr
        XIntField("fcnt",     0),   # 32b Frame Counter
        ShortField("gw_id",   0),   # 16b Gateway ID
        ByteField("flags",    0),   # 8b reserved
        ByteField("padding",  0),   # 8b reserved
    ]

    def guess_payload_class(self, payload):
        return Raw


# ── Tenant IP helper ─────────────────────────────────────────────────────

def lns_ip_for_devaddr(dev_addr: int, num_tenants: int) -> str:
    """
    Derive the target LNS IP from a DevAddr using the same prefix
    assignment as the controller:  tenant_idx = dev_addr >> (32 - prefix_len)
    """
    import math
    if num_tenants <= 1:
        return "10.0.2.1"
    prefix_len = math.ceil(math.log2(num_tenants))
    tenant_idx = (dev_addr >> (32 - prefix_len)) & ((1 << prefix_len) - 1)
    # Clamp to [0, num_tenants)
    tenant_idx = min(tenant_idx, num_tenants - 1)
    return f"10.0.2.{tenant_idx + 1}"


# ── Main ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="FeBEx traffic generator")
    parser.add_argument("--gw-id",           type=int,   required=True,
                        help="Gateway index (1-based), stored as gw_id in header")
    parser.add_argument("--coverage",         type=str,   required=True,
                        help="Path to coverage JSON file")
    parser.add_argument("--uplinks",          type=int,   default=50,
                        help="Number of uplink frames per device (FCnt range)")
    parser.add_argument("--inter-arrival-ms", type=float, default=100.0,
                        help="Sleep between FCnt rounds (ms)")
    parser.add_argument("--iface",            type=str,   required=True,
                        help="Network interface to send on (e.g. gw1-eth0)")
    parser.add_argument("--src-ip",           type=str,   default="10.0.1.1",
                        help="Source IP address")
    parser.add_argument("--src-mac",          type=str,   default="00:00:00:00:01:01",
                        help="Source MAC address")
    parser.add_argument("--payload-size",     type=int,   default=20,
                        help="Dummy payload size in bytes")
    parser.add_argument("--num-tenants",      type=int,   default=1,
                        help="Number of tenants (for IP routing)")
    args = parser.parse_args()

    # Load coverage
    with open(args.coverage) as f:
        cov = json.load(f)

    gw_idx          = args.gw_id - 1   # convert to 0-based
    coverage_matrix = cov["coverage_matrix"]   # [ed_idx][gw_idx] = 0|1
    device_devaddr  = cov["device_devaddr"]     # dev_addr per ED
    num_eds         = cov["num_edge_devices"]
    num_tenants     = args.num_tenants

    # Determine which EDs this gateway covers
    covered_eds = [
        ed for ed in range(num_eds)
        if ed < len(coverage_matrix) and
           gw_idx < len(coverage_matrix[ed]) and
           coverage_matrix[ed][gw_idx] == 1
    ]

    print(
        f"[gw{args.gw_id}] Covering {len(covered_eds)} EDs, "
        f"{args.uplinks} uplinks each",
        flush=True
    )

    payload = b"\x00" * args.payload_size
    inter   = args.inter_arrival_ms / 1000.0

    for fcnt in range(args.uplinks):
        burst = []
        for ed_idx in covered_eds:
            dev_addr = device_devaddr[ed_idx]
            dst_ip   = lns_ip_for_devaddr(dev_addr, num_tenants)

            pkt = (
                Ether(src=args.src_mac, dst="ff:ff:ff:ff:ff:ff")
                / IP(src=args.src_ip, dst=dst_ip)
                / UDP(sport=1234, dport=5555)
                / FeBExMeta(dev_addr=dev_addr, fcnt=fcnt, gw_id=args.gw_id)
                / Raw(payload)
            )
            burst.append(pkt)

        if burst:
            sendp(burst, iface=args.iface, verbose=False)

        if inter > 0 and fcnt < args.uplinks - 1:
            time.sleep(inter)

    print(f"[gw{args.gw_id}] Done — sent {len(covered_eds) * args.uplinks} packets", flush=True)


if __name__ == "__main__":
    main()
