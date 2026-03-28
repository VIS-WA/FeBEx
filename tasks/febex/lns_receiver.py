"""
FeBEx LNS Receiver
==================
Runs on an LNS host inside Mininet.
Sniffs UDP packets on port 5555, parses the FeBEx metadata header,
and logs each received uplink to a TSV file.

Log format (tab-separated):
    timestamp_ns  dev_addr  fcnt  gw_id  src_ip  tenant_id

Usage:
    python3 lns_receiver.py \\
        --lns-id 1 \\
        --log-dir /tmp/logs \\
        --iface lns1-eth0
"""

import argparse
import os
import signal
import struct
import sys
import time

from scapy.all import sniff, Ether, IP, UDP, Raw, Packet, conf

conf.verb = 0  # suppress scapy verbosity


# ── FeBExMeta parser (struct-based, no dependency on traffic_gen) ───────

FEBEX_META_SIZE = 12  # bytes
FEBEX_META_FMT  = "!IIHHBB"  # dev_addr(4) fcnt(4) gw_id(2) flags(1) padding(1)
                              # Actually: dev_addr 4B, fcnt 4B, gw_id 2B, flags 1B, padding 1B
# Correct format: >I I H B B = 4+4+2+1+1 = 12 bytes
FEBEX_META_FMT  = ">IIHBB"


def parse_febex_meta(raw_bytes: bytes):
    """Return (dev_addr, fcnt, gw_id, flags, padding) or None on failure."""
    if len(raw_bytes) < FEBEX_META_SIZE:
        return None
    try:
        return struct.unpack(FEBEX_META_FMT, raw_bytes[:FEBEX_META_SIZE])
    except struct.error:
        return None


# ── Global state ─────────────────────────────────────────────────────────

_running  = True
_log_file = None


def _sigterm_handler(sig, frame):
    global _running
    _running = False
    if _log_file:
        _log_file.flush()


# ── Packet callback ──────────────────────────────────────────────────────

def _make_callback(log_file, lns_id: int):
    def pkt_callback(pkt):
        if not (IP in pkt and UDP in pkt):
            return
        if pkt[UDP].dport != 5555:
            return

        raw = bytes(pkt[UDP].payload)
        fields = parse_febex_meta(raw)
        if fields is None:
            return

        dev_addr, fcnt, gw_id, flags, padding = fields
        src_ip    = pkt[IP].src
        ts_ns     = time.time_ns()
        # Tenant ID is implicit (from which LNS receives this), log as lns_id
        line = f"{ts_ns}\t{dev_addr}\t{fcnt}\t{gw_id}\t{src_ip}\t{lns_id}\n"
        log_file.write(line)
        log_file.flush()

    return pkt_callback


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    global _running, _log_file

    parser = argparse.ArgumentParser(description="FeBEx LNS receiver")
    parser.add_argument("--lns-id",  type=int, required=True,
                        help="LNS tenant index (1-based)")
    parser.add_argument("--log-dir", type=str, default="/tmp/febex_logs",
                        help="Directory to write log files")
    parser.add_argument("--iface",   type=str, required=True,
                        help="Interface to sniff on (e.g. lns1-eth0)")
    parser.add_argument("--timeout", type=float, default=0,
                        help="Stop sniffing after N seconds (0 = run until SIGTERM)")
    args = parser.parse_args()

    os.makedirs(args.log_dir, exist_ok=True)
    log_path = os.path.join(args.log_dir, f"lns{args.lns_id}_received.tsv")

    signal.signal(signal.SIGTERM, _sigterm_handler)
    signal.signal(signal.SIGINT,  _sigterm_handler)

    with open(log_path, "w") as log_file:
        _log_file = log_file
        # Write TSV header
        log_file.write("timestamp_ns\tdev_addr\tfcnt\tgw_id\tsrc_ip\ttenant_id\n")
        log_file.flush()

        callback = _make_callback(log_file, args.lns_id)

        print(f"[lns{args.lns_id}] Sniffing on {args.iface}, logging to {log_path}", flush=True)

        sniff(
            iface=args.iface,
            filter="udp and port 5555",
            prn=callback,
            store=False,
            stop_filter=lambda _: not _running,
            timeout=args.timeout if args.timeout > 0 else None,
        )

    print(f"[lns{args.lns_id}] Receiver stopped.", flush=True)


if __name__ == "__main__":
    main()
