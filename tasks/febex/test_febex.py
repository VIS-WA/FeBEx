#!/usr/bin/env python3
"""
FeBEx Automated Test Suite
===========================
Tests all core switch behaviours:
  1. test_basic_forwarding    — 1 gw, 1 lns, no dedup; 10 packets → 10 received
  2. test_tenant_steering     — 1 gw, 2 lns, no dedup; 5 pkts/tenant → each LNS gets 5
  3. test_dedup               — 3 gw, 1 lns, dedup ON; same uplink × 3 gws → 1 copy
  4. test_epoch_reset         — 2 gw, 1 lns, 2s epoch; send→wait flip→send → 2 copies
  5. test_correctness         — 3 gw, 2 lns, dedup ON; 20 unique uplinks → ratio 1.0
  6. test_receipt             — 2 gw, 1 lns, 1 cloud, dedup ON; 1 receipt correct gw_id

Run:
    sudo /opt/p4/p4dev-python-venv/bin/python3 tasks/febex/test_febex.py

Requires:
    make build-febex
"""

import math
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet

# ── Path setup ─────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR   = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(REPO_DIR))
sys.path.insert(0, str(REPO_DIR / "networks" / "febex" / "mininet"))

from networks import FeBExTopology  # noqa: E402

PYTHON       = sys.executable
BUILD_DIR    = REPO_DIR / "build" / "p4"
CTRL_SCRIPT  = SCRIPT_DIR / "p4rt_controller" / "controller.py"
LOG_DIR      = REPO_DIR / "temp" / "p4rt_controller" / "s1"
CTRL_LOG     = LOG_DIR / "s1_controller-stdout.log"

# Default topology ports
GRPC_PORT    = 50051
THRIFT_PORT  = 9090

# ── Terminal colours ────────────────────────────────────────────────────
GREEN = "\033[92m"
RED   = "\033[91m"
CYAN  = "\033[96m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
RESET = "\033[0m"
_PASS = f"{GREEN}PASS{RESET}"
_FAIL = f"{RED}FAIL{RESET}"


# ═══════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════

def header(title: str):
    print(f"\n{BOLD}{CYAN}{'═' * 60}")
    print(f"  {title}")
    print(f"{'═' * 60}{RESET}")


def check(label: str, passed: bool) -> bool:
    print(f"    {_PASS if passed else _FAIL}  {label}")
    return passed


def read_ctrl_log() -> str:
    try:
        return CTRL_LOG.read_text()
    except FileNotFoundError:
        return ""


def ctrl_log_snapshot() -> int:
    return len(read_ctrl_log())


def ctrl_log_since(snap: int) -> str:
    return read_ctrl_log()[snap:]


def wait_for_controller(timeout: float = 40.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        if "Controller ready" in read_ctrl_log():
            return True
        time.sleep(0.5)
    return False


def _dump_ctrl_log():
    """Print the last 2000 chars of the controller log for debugging."""
    log = read_ctrl_log()
    if log:
        print(f"\n{DIM}--- Controller log (tail) ---")
        print(log[-2000:])
        print(f"--- end ---{RESET}")
    else:
        print(f"{DIM}  (controller log is empty — process may have crashed on import){RESET}")


def start_controller(
    *,
    gateways: int,
    tenants: int,
    with_cloud: bool = False,
    no_dedup: bool = False,
    epoch_interval: float = 5.0,
    grpc_addr: str = f"127.0.0.1:{GRPC_PORT}",
    thrift_port: int = THRIFT_PORT,
) -> subprocess.Popen:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_fh = open(CTRL_LOG, "w")

    cloud_port = gateways + tenants + 1 if with_cloud else 0

    cmd = [
        PYTHON, "-u", str(CTRL_SCRIPT),
        "--gateways",       str(gateways),
        "--tenants",        str(tenants),
        "--epoch-interval", str(epoch_interval),
        "--grpc-addr",      grpc_addr,
        "--thrift-port",    str(thrift_port),
        "--device-id",      "1",
    ]
    if not with_cloud:
        cmd.append("--no-cloud")
    if no_dedup:
        cmd.append("--no-dedup")
    if cloud_port > 0:
        cmd += ["--cloud-port", str(cloud_port)]

    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(cmd, stdout=log_fh, stderr=subprocess.STDOUT, env=env)
    proc._log_fh = log_fh
    return proc


def stop_controller(proc: subprocess.Popen):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    try:
        proc._log_fh.close()
    except Exception:
        pass


def make_network(
    num_gateways: int,
    num_lns: int,
    with_cloud: bool = False,
) -> Mininet:
    topo = FeBExTopology(
        num_gateways=num_gateways,
        num_lns=num_lns,
        with_cloud=with_cloud,
        grpc_port=GRPC_PORT,
        thrift_port=THRIFT_PORT,
    )
    net = Mininet(topo=topo, link=TCLink, autoSetMacs=False)
    net.start()
    time.sleep(3)   # allow simple_switch_grpc to bind its ports
    return net


def populate_arp(net: Mininet, num_gateways: int, num_lns: int, with_cloud: bool):
    """Pre-populate ARP on all hosts (switch doesn't do ARP)."""
    gw_entries  = [(f"10.0.1.{i}", f"00:00:00:00:01:{i:02x}") for i in range(1, num_gateways + 1)]
    lns_entries = [(f"10.0.2.{i}", f"00:00:00:00:02:{i:02x}") for i in range(1, num_lns + 1)]
    cloud_entry = [("10.0.3.1", "00:00:00:00:03:01")] if with_cloud else []
    all_entries = gw_entries + lns_entries + cloud_entry

    all_hosts = (
        [net.get(f"gw{i}")  for i in range(1, num_gateways + 1)] +
        [net.get(f"lns{i}") for i in range(1, num_lns + 1)] +
        ([net.get("cloud1")] if with_cloud else [])
    )
    for host in all_hosts:
        for ip, mac in all_entries:
            host.cmd(f"arp -s {ip} {mac}")


# ── Scapy send script template ──────────────────────────────────────────

SCAPY_SEND_TEMPLATE = """\
import sys
sys.path.insert(0, '{repo_dir}')
from scapy.all import Ether, IP, UDP, Raw, sendp
from scapy.packet import Packet
from scapy.fields import XIntField, ShortField, ByteField

class FeBExMeta(Packet):
    name = "FeBExMeta"
    fields_desc = [
        XIntField("dev_addr", 0),
        XIntField("fcnt",     0),
        ShortField("gw_id",   0),
        ByteField("flags",    0),
        ByteField("padding",  0),
    ]

pkts = []
for dev_addr, fcnt, gw_id, src_mac, src_ip, dst_ip in {packets!r}:
    p = (Ether(src=src_mac, dst='ff:ff:ff:ff:ff:ff')
         / IP(src=src_ip, dst=dst_ip, ttl=64)
         / UDP(sport=1234, dport=5555)
         / FeBExMeta(dev_addr=dev_addr, fcnt=fcnt, gw_id=gw_id)
         / Raw(b'\\x00' * {payload_size}))
    pkts.append(p)

sendp(pkts, iface='{iface}', verbose=False, inter={inter})
print('SENT', len(pkts), flush=True)
"""


def send_packets(
    host,
    iface: str,
    packets: list,
    inter: float = 0.02,
    payload_size: int = 20,
) -> str:
    script = SCAPY_SEND_TEMPLATE.format(
        repo_dir=str(REPO_DIR),
        packets=packets,
        iface=iface,
        inter=inter,
        payload_size=payload_size,
    )
    path = f"/tmp/febex_send_{host.name}.py"
    Path(path).write_text(script)
    return host.cmd(f"{PYTHON} {path} 2>&1").strip()


def count_log_lines(log_path: str) -> int:
    try:
        lines = Path(log_path).read_text().splitlines()
    except FileNotFoundError:
        return 0
    return sum(1 for l in lines if l.strip() and not l.startswith("timestamp"))


def parse_tsv_log(log_path: str) -> list:
    records = []
    try:
        lines = Path(log_path).read_text().splitlines()
    except FileNotFoundError:
        return records
    if not lines:
        return records
    hdrs = lines[0].split("\t")
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) == len(hdrs):
            records.append(dict(zip(hdrs, parts)))
    return records


def start_receiver(host, iface: str, lns_id: int, log_dir: str, timeout: float = 30.0):
    script = SCRIPT_DIR / "lns_receiver.py"
    cmd = (f"{PYTHON} -u {script} "
           f"--lns-id {lns_id} --log-dir {log_dir} "
           f"--iface {iface} --timeout {timeout}")
    return host.popen(cmd, shell=False)


def start_cloud_receiver(host, iface: str, log_dir: str, timeout: float = 30.0):
    script = SCRIPT_DIR / "cloud_receiver.py"
    cmd = (f"{PYTHON} -u {script} "
           f"--log-dir {log_dir} --iface {iface} --timeout {timeout}")
    return host.popen(cmd, shell=False)


def stop_procs(*procs):
    for p in procs:
        if p is not None:
            try:
                p.terminate()
            except Exception:
                pass


def _cleanup_stale():
    """Kill any stale BMv2 / controller processes and free ports."""
    subprocess.run(["mn", "-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run("pkill -9 -f simple_switch_grpc 2>/dev/null; true",
                   shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run("pkill -9 -f 'controller.py' 2>/dev/null; true",
                   shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)   # wait for TCP ports to be released


def _run_test(title: str, fn) -> bool:
    """Wrap a test function: start/stop Mininet bookkeeping + full cleanup."""
    header(title)
    result = False
    try:
        result = fn()
    except Exception as exc:
        print(f"    {RED}EXCEPTION: {exc}{RESET}")
        import traceback; traceback.print_exc()
    finally:
        _cleanup_stale()
    return result


# ═══════════════════════════════════════════════════════════════════════
#  Test 1 — Basic Forwarding
# ═══════════════════════════════════════════════════════════════════════

def _test_basic_forwarding() -> bool:
    K, M = 1, 1
    net  = make_network(K, M)
    ctrl = start_controller(gateways=K, tenants=M, no_dedup=True)
    try:
        if not wait_for_controller(40):
            _dump_ctrl_log()
            return False
        print(f"  {GREEN}Controller ready ✓{RESET}")
        time.sleep(1)
        populate_arp(net, K, M, False)

        log_dir = "/tmp/febex_t1"
        os.makedirs(log_dir, exist_ok=True)
        recv = start_receiver(net.get("lns1"), "lns1-eth0", 1, log_dir, timeout=15)
        time.sleep(1)

        packets = [(0x00000001 + i, 0, 1,
                    "00:00:00:00:01:01", "10.0.1.1", "10.0.2.1")
                   for i in range(10)]
        out = send_packets(net.get("gw1"), "gw1-eth0", packets)
        print(f"    {DIM}{out}{RESET}")
        time.sleep(3)
        stop_procs(recv)
        time.sleep(0.5)

        n = count_log_lines(f"{log_dir}/lns1_received.tsv")
        return check(f"LNS1 received {n}/10 packets", n == 10)
    finally:
        net.stop()
        stop_controller(ctrl)


# ═══════════════════════════════════════════════════════════════════════
#  Test 2 — Tenant Steering
# ═══════════════════════════════════════════════════════════════════════

def _test_tenant_steering() -> bool:
    K, M = 1, 2
    net  = make_network(K, M)
    ctrl = start_controller(gateways=K, tenants=M, no_dedup=True)
    try:
        if not wait_for_controller(40):
            _dump_ctrl_log()
            return False
        print(f"  {GREEN}Controller ready ✓{RESET}")
        time.sleep(1)
        populate_arp(net, K, M, False)

        log_dir = "/tmp/febex_t2"
        os.makedirs(log_dir, exist_ok=True)
        recv1 = start_receiver(net.get("lns1"), "lns1-eth0", 1, log_dir, timeout=20)
        recv2 = start_receiver(net.get("lns2"), "lns2-eth0", 2, log_dir, timeout=20)
        time.sleep(1)

        # M=2 → prefix_len=1: tenant0 = top bit 0, tenant1 = top bit 1
        pkts = []
        for i in range(5):
            pkts.append((0x00000001 + i, 0, 1, "00:00:00:00:01:01", "10.0.1.1", "10.0.2.1"))
        for i in range(5):
            pkts.append((0x80000001 + i, 0, 1, "00:00:00:00:01:01", "10.0.1.1", "10.0.2.2"))

        out = send_packets(net.get("gw1"), "gw1-eth0", pkts)
        print(f"    {DIM}{out}{RESET}")
        time.sleep(3)
        stop_procs(recv1, recv2)
        time.sleep(0.5)

        n1 = count_log_lines(f"{log_dir}/lns1_received.tsv")
        n2 = count_log_lines(f"{log_dir}/lns2_received.tsv")
        ok1 = check(f"LNS1 received {n1}/5 (tenant 0)", n1 == 5)
        ok2 = check(f"LNS2 received {n2}/5 (tenant 1)", n2 == 5)
        return ok1 and ok2
    finally:
        net.stop()
        stop_controller(ctrl)


# ═══════════════════════════════════════════════════════════════════════
#  Test 3 — Deduplication
# ═══════════════════════════════════════════════════════════════════════

def _test_dedup() -> bool:
    K, M = 3, 1
    net  = make_network(K, M)
    ctrl = start_controller(gateways=K, tenants=M, no_dedup=False, epoch_interval=60.0)
    try:
        if not wait_for_controller(40):
            _dump_ctrl_log()
            return False
        print(f"  {GREEN}Controller ready ✓{RESET}")
        time.sleep(1)
        populate_arp(net, K, M, False)

        log_dir = "/tmp/febex_t3"
        os.makedirs(log_dir, exist_ok=True)
        recv = start_receiver(net.get("lns1"), "lns1-eth0", 1, log_dir, timeout=20)
        time.sleep(1)

        dev_addr, fcnt = 0x00000001, 42
        for gw_i, (gw_mac, gw_ip) in enumerate([
            ("00:00:00:00:01:01", "10.0.1.1"),
            ("00:00:00:00:01:02", "10.0.1.2"),
            ("00:00:00:00:01:03", "10.0.1.3"),
        ], start=1):
            out = send_packets(net.get(f"gw{gw_i}"), f"gw{gw_i}-eth0",
                               [(dev_addr, fcnt, gw_i, gw_mac, gw_ip, "10.0.2.1")])
            print(f"    {DIM}gw{gw_i}: {out}{RESET}")
            time.sleep(0.15)

        time.sleep(4)
        stop_procs(recv)
        time.sleep(0.5)

        n = count_log_lines(f"{log_dir}/lns1_received.tsv")
        return check(f"LNS1 received {n} copy/copies (expected 1)", n == 1)
    finally:
        net.stop()
        stop_controller(ctrl)


# ═══════════════════════════════════════════════════════════════════════
#  Test 4 — Epoch Reset
# ═══════════════════════════════════════════════════════════════════════

def _test_epoch_reset() -> bool:
    K, M = 2, 1
    net  = make_network(K, M)
    ctrl = start_controller(gateways=K, tenants=M, no_dedup=False, epoch_interval=2.0)
    try:
        if not wait_for_controller(40):
            _dump_ctrl_log()
            return False
        print(f"  {GREEN}Controller ready ✓{RESET}")
        time.sleep(1)
        populate_arp(net, K, M, False)

        log_dir = "/tmp/febex_t4"
        os.makedirs(log_dir, exist_ok=True)
        recv = start_receiver(net.get("lns1"), "lns1-eth0", 1, log_dir, timeout=30)
        time.sleep(1)

        dev_addr, fcnt = 0x00000001, 99

        out1 = send_packets(net.get("gw1"), "gw1-eth0",
                            [(dev_addr, fcnt, 1, "00:00:00:00:01:01", "10.0.1.1", "10.0.2.1")])
        print(f"    {DIM}gw1: {out1}{RESET}")
        time.sleep(1)

        print(f"    {DIM}Waiting 3s for epoch flip (interval=2s)...{RESET}", flush=True)
        time.sleep(3)

        out2 = send_packets(net.get("gw2"), "gw2-eth0",
                            [(dev_addr, fcnt, 2, "00:00:00:00:01:02", "10.0.1.2", "10.0.2.1")])
        print(f"    {DIM}gw2 (new epoch): {out2}{RESET}")
        time.sleep(3)

        stop_procs(recv)
        time.sleep(0.5)

        n = count_log_lines(f"{log_dir}/lns1_received.tsv")
        return check(f"LNS1 received {n} copies (expected 2: one per epoch)", n == 2)
    finally:
        net.stop()
        stop_controller(ctrl)


# ═══════════════════════════════════════════════════════════════════════
#  Test 5 — Correctness (zero unique-uplink loss)
# ═══════════════════════════════════════════════════════════════════════

def _test_correctness() -> bool:
    K, M     = 3, 2
    N_UPLINKS = 20
    net  = make_network(K, M)
    ctrl = start_controller(gateways=K, tenants=M, no_dedup=False, epoch_interval=30.0)
    try:
        if not wait_for_controller(40):
            _dump_ctrl_log()
            return False
        print(f"  {GREEN}Controller ready ✓{RESET}")
        time.sleep(1)
        populate_arp(net, K, M, False)

        log_dir = "/tmp/febex_t5"
        os.makedirs(log_dir, exist_ok=True)
        recv1 = start_receiver(net.get("lns1"), "lns1-eth0", 1, log_dir, timeout=60)
        recv2 = start_receiver(net.get("lns2"), "lns2-eth0", 2, log_dir, timeout=60)
        time.sleep(1)

        t0 = [(0x00000001 + i, i) for i in range(N_UPLINKS)]  # tenant 0
        t1 = [(0x80000001 + i, i) for i in range(N_UPLINKS)]  # tenant 1

        for gw_i, (gw_mac, gw_ip) in enumerate([
            ("00:00:00:00:01:01", "10.0.1.1"),
            ("00:00:00:00:01:02", "10.0.1.2"),
            ("00:00:00:00:01:03", "10.0.1.3"),
        ], start=1):
            pkts = ([(da, fc, gw_i, gw_mac, gw_ip, "10.0.2.1") for da, fc in t0] +
                    [(da, fc, gw_i, gw_mac, gw_ip, "10.0.2.2") for da, fc in t1])
            out = send_packets(net.get(f"gw{gw_i}"), f"gw{gw_i}-eth0", pkts, inter=0.005)
            print(f"    {DIM}gw{gw_i}: {out}{RESET}")

        time.sleep(6)
        stop_procs(recv1, recv2)
        time.sleep(0.5)

        recs1 = parse_tsv_log(f"{log_dir}/lns1_received.tsv")
        recs2 = parse_tsv_log(f"{log_dir}/lns2_received.tsv")
        u1 = {(r["dev_addr"], r["fcnt"]) for r in recs1}
        u2 = {(r["dev_addr"], r["fcnt"]) for r in recs2}

        r1 = len(u1) / N_UPLINKS if N_UPLINKS else 0
        r2 = len(u2) / N_UPLINKS if N_UPLINKS else 0

        ok1 = check(f"LNS1 delivery {len(u1)}/{N_UPLINKS} = {r1:.4f} (need 1.0)", r1 == 1.0)
        ok2 = check(f"LNS2 delivery {len(u2)}/{N_UPLINKS} = {r2:.4f} (need 1.0)", r2 == 1.0)

        t1_addr_strs = {str(d) for d, _ in t1}
        t0_addr_strs = {str(d) for d, _ in t0}
        ok3 = check("No cross-tenant at LNS1", not any(r["dev_addr"] in t1_addr_strs for r in recs1))
        ok4 = check("No cross-tenant at LNS2", not any(r["dev_addr"] in t0_addr_strs for r in recs2))
        return ok1 and ok2 and ok3 and ok4
    finally:
        net.stop()
        stop_controller(ctrl)


# ═══════════════════════════════════════════════════════════════════════
#  Test 6 — Receipt Mirroring
# ═══════════════════════════════════════════════════════════════════════

def _test_receipt() -> bool:
    K, M = 2, 1
    net  = make_network(K, M, with_cloud=True)
    ctrl = start_controller(gateways=K, tenants=M, with_cloud=True,
                             no_dedup=False, epoch_interval=60.0)
    try:
        if not wait_for_controller(40):
            _dump_ctrl_log()
            return False
        print(f"  {GREEN}Controller ready ✓{RESET}")
        time.sleep(1)
        populate_arp(net, K, M, with_cloud=True)

        log_dir = "/tmp/febex_t6"
        os.makedirs(log_dir, exist_ok=True)
        recv_lns   = start_receiver(net.get("lns1"),   "lns1-eth0",   1, log_dir, timeout=20)
        recv_cloud = start_cloud_receiver(net.get("cloud1"), "cloud1-eth0", log_dir, timeout=20)
        time.sleep(1)

        dev_addr, fcnt = 0x00000001, 7

        out1 = send_packets(net.get("gw1"), "gw1-eth0",
                            [(dev_addr, fcnt, 1, "00:00:00:00:01:01", "10.0.1.1", "10.0.2.1")])
        print(f"    {DIM}gw1: {out1}{RESET}")
        time.sleep(0.3)

        out2 = send_packets(net.get("gw2"), "gw2-eth0",
                            [(dev_addr, fcnt, 2, "00:00:00:00:01:02", "10.0.1.2", "10.0.2.1")])
        print(f"    {DIM}gw2 (dup): {out2}{RESET}")
        time.sleep(4)

        stop_procs(recv_lns, recv_cloud)
        time.sleep(0.5)

        n_lns   = count_log_lines(f"{log_dir}/lns1_received.tsv")
        n_cloud = count_log_lines(f"{log_dir}/cloud_receipts.tsv")
        ok_lns   = check(f"LNS1 received {n_lns} (expected 1)", n_lns == 1)
        ok_cloud = check(f"Cloud received {n_cloud} receipt(s) (expected 1)", n_cloud == 1)

        cloud_recs = parse_tsv_log(f"{log_dir}/cloud_receipts.tsv")
        if cloud_recs:
            gw_id_s = cloud_recs[0].get("gw_id", "?")
            ok_gw = check(f"Receipt gw_id={gw_id_s} (expected 1)", gw_id_s == "1")
        else:
            ok_gw = check("Receipt gw_id (no records)", False)

        return ok_lns and ok_cloud and ok_gw
    finally:
        net.stop()
        stop_controller(ctrl)


# ═══════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════

def main():
    if os.geteuid() != 0:
        print(f"\n{RED}Must run as root: sudo {PYTHON} {__file__}{RESET}")
        sys.exit(1)

    setLogLevel("warning")

    if not (BUILD_DIR / "febex.p4info.txtpb").exists():
        print(f"\n{RED}P4 not compiled. Run: make build-febex{RESET}")
        sys.exit(1)

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n{BOLD}{'═' * 60}")
    print("  FeBEx Switch — Automated Test Suite")
    print(f"{'═' * 60}{RESET}\n")

    print("  Cleaning up stale processes...", flush=True)
    _cleanup_stale()

    tests = [
        ("Test 1: Basic Forwarding (1 gw, 1 lns, no dedup)",         _test_basic_forwarding),
        ("Test 2: Tenant Steering (1 gw, 2 lns, no dedup)",          _test_tenant_steering),
        ("Test 3: Deduplication (3 gw, 1 lns, dedup ON)",            _test_dedup),
        ("Test 4: Epoch Reset (2 gw, 1 lns, 2s epoch)",              _test_epoch_reset),
        ("Test 5: Correctness — Zero Uplink Loss (3 gw, 2 lns)",     _test_correctness),
        ("Test 6: Receipt Mirroring (2 gw, 1 lns, 1 cloud)",         _test_receipt),
    ]

    results = {}
    for title, fn in tests:
        results[title] = _run_test(title, fn)

    total  = len(results)
    passed = sum(1 for v in results.values() if v)

    print(f"\n{BOLD}{'═' * 60}")
    print("  SUMMARY")
    print(f"{'═' * 60}{RESET}")
    for name, ok in results.items():
        print(f"    {_PASS if ok else _FAIL}  {name}")
    print()
    if passed == total:
        print(f"    {GREEN}{BOLD}ALL {total} TESTS PASSED ✓{RESET}\n")
        return 0
    print(f"    {passed}/{total} passed — {RED}{BOLD}{total - passed} FAILED ✗{RESET}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
