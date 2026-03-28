"""
FeBEx Cloud Receiver
====================
Runs on the Helium Cloud host inside Mininet.
Sniffs cloned receipt packets on UDP port 5555.
The gw_id field in the FeBEx header identifies which hotspot first
forwarded each unique uplink — that miner earns Proof-of-Coverage credit.

Log format (tab-separated):
    timestamp_ns  dev_addr  fcnt  gw_id  src_ip

Usage:
    python3 cloud_receiver.py \\
        --log-dir /tmp/logs \\
        --iface cloud1-eth0
"""

import argparse
import os
import signal
import struct
import sys
import time

from scapy.all import sniff, IP, UDP, conf

conf.verb = 0

FEBEX_META_FMT = ">IIHBB"   # 4+4+2+1+1 = 12 bytes


def parse_febex_meta(raw_bytes: bytes):
    """Return (dev_addr, fcnt, gw_id, flags, padding) or None."""
    if len(raw_bytes) < 12:
        return None
    try:
        return struct.unpack(FEBEX_META_FMT, raw_bytes[:12])
    except struct.error:
        return None


_running  = True
_log_file = None


def _sigterm_handler(sig, frame):
    global _running
    _running = False
    if _log_file:
        _log_file.flush()


def _make_callback(log_file):
    def pkt_callback(pkt):
        if not (IP in pkt and UDP in pkt):
            return
        if pkt[UDP].dport != 5555:
            return

        raw    = bytes(pkt[UDP].payload)
        fields = parse_febex_meta(raw)
        if fields is None:
            return

        dev_addr, fcnt, gw_id, flags, padding = fields
        src_ip = pkt[IP].src
        ts_ns  = time.time_ns()
        line   = f"{ts_ns}\t{dev_addr}\t{fcnt}\t{gw_id}\t{src_ip}\n"
        log_file.write(line)
        log_file.flush()

    return pkt_callback


def main():
    global _running, _log_file

    parser = argparse.ArgumentParser(description="FeBEx cloud receiver")
    parser.add_argument("--log-dir", type=str, default="/tmp/febex_logs",
                        help="Directory to write log files")
    parser.add_argument("--iface",   type=str, required=True,
                        help="Interface to sniff on (e.g. cloud1-eth0)")
    parser.add_argument("--timeout", type=float, default=0,
                        help="Stop sniffing after N seconds (0 = run until SIGTERM)")
    args = parser.parse_args()

    os.makedirs(args.log_dir, exist_ok=True)
    log_path = os.path.join(args.log_dir, "cloud_receipts.tsv")

    signal.signal(signal.SIGTERM, _sigterm_handler)
    signal.signal(signal.SIGINT,  _sigterm_handler)

    with open(log_path, "w") as log_file:
        _log_file = log_file
        log_file.write("timestamp_ns\tdev_addr\tfcnt\tgw_id\tsrc_ip\n")
        log_file.flush()

        callback = _make_callback(log_file)

        print(f"[cloud1] Sniffing on {args.iface}, logging to {log_path}", flush=True)

        sniff(
            iface=args.iface,
            filter="udp and port 5555",
            prn=callback,
            store=False,
            stop_filter=lambda _: not _running,
            timeout=args.timeout if args.timeout > 0 else None,
        )

    print("[cloud1] Receiver stopped.", flush=True)


if __name__ == "__main__":
    main()
