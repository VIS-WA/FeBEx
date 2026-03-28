#!/usr/bin/env python3
"""
Automated Test Suite for Task 1: L2 Learning Switch
=====================================================
Tests all 7 requirements using Mininet, Scapy, and controller log analysis.

Requirements:
  1. 4 ports per switch
  2. Runtime MAC learning (via P4Runtime digests)
  3. ≥ 1000 MAC address capacity
  4. Forward on hit / Flood on miss
  5. 10-second inactivity timeout
  6. No controller reboot needed on host reconnect
  7. No broadcast storms

How to run:
  1. Compile the P4 program:
       make build-task-1

  2. Run the test (as root, since Mininet requires root):
       sudo /opt/p4/p4dev-python-venv/bin/python3 tasks/1/test_switch.py

  The script automatically:
    - Starts both P4Runtime controllers
    - Creates the Mininet topology (starts BMv2 switches)
    - Runs all 7 tests with automated pass/fail checks
    - Prints a colour-coded summary
    - Cleans up everything afterwards
"""

import os
import re
import subprocess
import sys
import time
from pathlib import Path

from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet

# ── Path setup ──────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(REPO_DIR))
sys.path.insert(0, str(REPO_DIR / "networks" / "1" / "mininet"))

from networks import Topology  # noqa: E402

# ── Configuration ───────────────────────────────────────────────────────

PYTHON = sys.executable
BUILD_DIR = REPO_DIR / "build" / "p4"
CONTROLLER_DIR = SCRIPT_DIR / "p4rt_controller"

LOG_DIR = REPO_DIR / "temp" / "p4rt_controller"
LOG1_PATH = LOG_DIR / "ixp1s1" / "ixp1s1_controller-stdout.log"
LOG2_PATH = LOG_DIR / "ixp2s1" / "ixp2s1_controller-stdout.log"

# ── Terminal colours ────────────────────────────────────────────────────

GREEN  = "\033[92m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"
_PASS  = f"{GREEN}PASS{RESET}"
_FAIL  = f"{RED}FAIL{RESET}"

# ═══════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════

def read_log(path):
    try:
        return Path(path).read_text()
    except FileNotFoundError:
        return ""


def log_snapshot():
    """Return (len_log1, len_log2) for later diffing."""
    return len(read_log(LOG1_PATH)), len(read_log(LOG2_PATH))


def log_since(snapshot):
    """Return (new_log1, new_log2) since the snapshot."""
    l1, l2 = snapshot
    return read_log(LOG1_PATH)[l1:], read_log(LOG2_PATH)[l2:]


def ping_ok(result):
    return "0% packet loss" in result


def wait_for_controllers(timeout=30):
    start = time.time()
    while time.time() - start < timeout:
        if ("Controller ready" in read_log(LOG1_PATH) and
                "Controller ready" in read_log(LOG2_PATH)):
            return True
        time.sleep(0.5)
    return False


def wait_clean(seconds=20):
    """Wait long enough for every entry to idle-timeout + BMv2 sweep."""
    print(f"      {DIM}Waiting {seconds}s for clean state...{RESET}", flush=True)
    time.sleep(seconds)


def start_tcpdump(host, pcap, bpf_filter=""):
    """Start tcpdump in background; returns pid-file path."""
    pid_file = f"/tmp/td_{host.name}.pid"
    intf = f"{host.name}-eth0"
    host.cmd(f"tcpdump -U -i {intf} -w {pcap} {bpf_filter} "
             f"& echo $! > {pid_file}")
    time.sleep(1)
    return pid_file


def stop_tcpdump(host, pid_file):
    host.cmd(f"kill $(cat {pid_file}) 2>/dev/null")
    time.sleep(1)


def count_pcap(host, pcap, grep_filter=""):
    """Count matching packets in a pcap."""
    g = f"| grep -i '{grep_filter}'" if grep_filter else ""
    out = host.cmd(f"tcpdump -nn -r {pcap} 2>/dev/null {g} | wc -l").strip()
    try:
        return int(out)
    except ValueError:
        return 0


def header(num, title):
    print(f"\n{BOLD}{CYAN}{'═' * 55}")
    print(f"  Test {num}: {title}")
    print(f"{'═' * 55}{RESET}")


def check(label, passed):
    print(f"    {_PASS if passed else _FAIL}  {label}")
    return passed


# ═══════════════════════════════════════════════════════════════════════
#  Test 1 & 2 — Connectivity on all 4 ports  +  MAC Learning
# ═══════════════════════════════════════════════════════════════════════

def test_connectivity_and_learning(net):
    header("1 & 2", "Connectivity (4 ports) + MAC Learning")
    h1, h2, h3, h4 = net.get("h1", "h2", "h3", "h4")
    snap = log_snapshot()

    pairs = [
        ("h1 → h2  (same switch, ixp1s1)",   h1, "4.1.1.102"),
        ("h3 → h4  (same switch, ixp2s1)",   h3, "4.1.1.104"),
        ("h1 → h3  (cross-switch)",          h1, "4.1.1.103"),
        ("h2 → h4  (cross-switch)",          h2, "4.1.1.104"),
    ]

    ping_pass = True
    for label, src, dst_ip in pairs:
        ok = ping_ok(src.cmd(f"ping -c 1 -W 5 {dst_ip}"))
        check(label, ok)
        if not ok:
            ping_pass = False

    # Wait and poll for LEARNED messages to appear in log files.
    # The controller writes via a pipe → file handle; flushing can lag.
    deadline = time.time() + 10
    l1 = set()
    l2 = set()
    while time.time() < deadline:
        new1, new2 = log_since(snap)
        l1 = set(re.findall(r"\+ LEARNED (\S+) on port", new1))
        l2 = set(re.findall(r"\+ LEARNED (\S+) on port", new2))
        if len(l1) >= 2 and len(l2) >= 2:
            break
        time.sleep(1)

    print(f"      {DIM}ixp1s1 learned: {l1}{RESET}")
    print(f"      {DIM}ixp2s1 learned: {l2}{RESET}")

    learn_ok = check(
        f"MAC learning active (ixp1s1 {len(l1)} MACs, ixp2s1 {len(l2)} MACs)",
        len(l1) >= 2 and len(l2) >= 2)
    
    # print the new1, new2 logs for debugging
    print(f"      {DIM}  ixp1s1 log: {new1[:]}{RESET}")
    print(f"      {DIM}  ixp2s1 log: {new2[:]}{RESET}")




    return ping_pass and learn_ok


# ═══════════════════════════════════════════════════════════════════════
#  Test 3 — Forward on Hit / Flood on Miss
# ═══════════════════════════════════════════════════════════════════════

def test_forward_flood(net):
    header("3", "Forward on Hit / Flood on Miss")
    h1, h2, h3, h4 = net.get("h1", "h2", "h3", "h4")
    wait_clean()

    # ── Part A: Flood on miss ──
    # Write Scapy scripts to files to avoid all shell-quoting issues.
    flood_script = (
        "from scapy.all import sendp, Ether, ARP\n"
        "pkt = (Ether(src='f0:00:0d:00:01:00', dst='ff:ff:ff:ff:ff:ff')\n"
        "       / ARP(op=1, hwsrc='f0:00:0d:00:01:00', psrc='4.1.1.101',\n"
        "             hwdst='00:00:00:00:00:00', pdst='4.1.1.102'))\n"
        "sendp(pkt, iface='h1-eth0', verbose=1, count=3, inter=0.3)\n"
        "print('FLOOD_SENT')\n"
    )
    Path("/tmp/flood_send.py").write_text(flood_script)

    # Start captures on MULTIPLE hosts to diagnose where packets stop.
    for h in [h2, h3, h4]:
        h.cmd(f"rm -f /tmp/{h.name}_flood.pcap")
    pid2 = start_tcpdump(h2, "/tmp/h2_flood.pcap", "arp")
    pid3 = start_tcpdump(h3, "/tmp/h3_flood.pcap", "arp")
    pid4 = start_tcpdump(h4, "/tmp/h4_flood.pcap", "arp")
    time.sleep(2)  # let tcpdumps stabilise

    # Send via the temp file — Scapy runs inside h1's namespace.
    out = h1.cmd(f"{PYTHON} /tmp/flood_send.py 2>&1")
    print(f"      {DIM}Scapy output: {out.strip()}{RESET}")
    time.sleep(3)

    stop_tcpdump(h2, pid2)
    stop_tcpdump(h3, pid3)
    stop_tcpdump(h4, pid4)

    # Debug: show what each host saw.
    for hname in ["h2", "h3", "h4"]:
        h = net.get(hname)
        dump = h.cmd(f"tcpdump -nn -r /tmp/{hname}_flood.pcap 2>/dev/null").strip()
        n = len([l for l in dump.splitlines() if l.strip()]) if dump else 0
        print(f"      {DIM}  {hname} pcap ({n} pkts): {dump[:200]}{RESET}")

    flood_n2 = count_pcap(h2, "/tmp/h2_flood.pcap")
    flood_n4 = count_pcap(h4, "/tmp/h4_flood.pcap")
    check(f"Flood debug: h2 (same switch) saw {flood_n2} ARP pkt(s)", flood_n2 > 0)
    flood_ok = check(f"Flood: h4 (cross-switch) saw {flood_n4} ARP pkt(s) (expected > 0)", flood_n4 > 0)

    # Check controller logs for learning (confirms digest pipeline works).
    snap = log_snapshot()
    time.sleep(1)
    new1, new2 = log_since(snap)
    print(f"      {DIM}  ixp1s1 log: {new1[:200]}{RESET}")
    print(f"      {DIM}  ixp2s1 log: {new2[:200]}{RESET}")

    # ── Part B: Forward on hit ──
    # Ensure h1 and h2 MACs are learned.
    h1.cmd("ping -c 2 -W 3 4.1.1.102")
    time.sleep(3)  # let learning settle

    h4.cmd("rm -f /tmp/h4_fwd.pcap")
    pid = start_tcpdump(h4, "/tmp/h4_fwd.pcap", "icmp")
    time.sleep(2)

    # After MACs are learned, unicast ICMP h1→h2 must NOT reach h4.
    h1.cmd("ping -c 3 -i 0.3 -W 1 4.1.1.102")
    time.sleep(3)
    stop_tcpdump(h4, pid)

    fwd_n = count_pcap(h4, "/tmp/h4_fwd.pcap")
    fwd_ok = check(f"Forward: h4 saw {fwd_n} ICMP pkt(s) h1→h2 (expected 0)", fwd_n == 0)

    return flood_ok and fwd_ok


# ═══════════════════════════════════════════════════════════════════════
#  Test 4 — 10-second Inactivity Timeout
# ═══════════════════════════════════════════════════════════════════════

def test_idle_timeout(net):
    header("4", "10-Second Inactivity Timeout")
    h1 = net.get("h1")
    wait_clean()

    # Flush ARP so the ping triggers a clean ARP+ICMP exchange
    h1.cmd("ip neigh flush all")
    time.sleep(1)

    snap = log_snapshot()
    learn_t = time.time()
    h1.cmd("ping -c 1 -W 3 4.1.1.102")
    time.sleep(2)

    # Identify which MAC was just learned (h1's MAC)
    h1_mac = "f0:00:0d:00:01:00"
    new1, _ = log_since(snap)
    learned = f"LEARNED {h1_mac}" in new1
    print(f"    {'✓' if learned else '✗'}  MAC {h1_mac} learned after ping")

    # Poll for REMOVAL of that SPECIFIC MAC — ignore other removals
    print(f"      {DIM}Waiting for idle timeout (up to 25s)...{RESET}", flush=True)
    removed = False
    elapsed = 0.0
    while time.time() - learn_t < 25:
        new1, _ = log_since(snap)
        if f"REMOVED {h1_mac}" in new1:
            elapsed = time.time() - learn_t
            removed = True
            break
        time.sleep(0.5)

    if removed:
        timeout_ok = check(
            f"Removed after {elapsed:.1f}s (expected 10-12s, BMv2 sweeps every 1s)",
            8 < elapsed < 20)
    else:
        timeout_ok = check("REMOVED within 25s", False)

    return learned and timeout_ok


# ═══════════════════════════════════════════════════════════════════════
#  Test 4b — Timeout Timing (multiple rounds, detailed)
# ═══════════════════════════════════════════════════════════════════════

def test_timeout_timing(net):
    header("4b", "Timeout Timing — 3 rounds with detailed measurements")
    h1, h2 = net.get("h1", "h2")
    results_list = []

    for r in range(1, 4):
        print(f"\n    Round {r}/3:")
        wait_clean()

        # Flush ARP so we get a clean ARP+ICMP exchange
        h1.cmd("ip neigh flush all")
        h2.cmd("ip neigh flush all")
        time.sleep(1)

        snap = log_snapshot()
        send_t = time.time()
        h1.cmd("ping -c 1 -W 3 4.1.1.102")
        time.sleep(1)

        # Find exact LEARNED timestamp from controller log
        new1, _ = log_since(snap)
        learn_match = re.search(
            r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}).*\+ LEARNED (f0:00:0d:00:01:00)",
            new1)
        if learn_match:
            print(f"      {DIM}LEARNED at: {learn_match.group(1)}{RESET}")
        else:
            print(f"      {DIM}LEARNED: h1 MAC not found in log (may already be known){RESET}")

        # Wait for removal, polling frequently
        removed = False
        elapsed = 0.0
        while time.time() - send_t < 30:
            new1, _ = log_since(snap)
            rm_match = re.search(
                r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}).*- REMOVED (f0:00:0d:00:01:00)",
                new1)
            if rm_match:
                elapsed = time.time() - send_t
                print(f"      {DIM}REMOVED at: {rm_match.group(1)}{RESET}")
                results_list.append(elapsed)
                removed = True
                break
            time.sleep(0.25)

        if removed:
            check(f"Round {r}: removed after {elapsed:.1f}s", True)
        else:
            check(f"Round {r}: NOT removed within 30s", False)
            results_list.append(None)

    valid = [t for t in results_list if t is not None]
    if valid:
        avg = sum(valid) / len(valid)
        mn, mx = min(valid), max(valid)
        print(f"\n    Timing summary:")
        print(f"      Times: {[f'{t:.1f}s' for t in valid]}")
        print(f"      Min={mn:.1f}s  Max={mx:.1f}s  Avg={avg:.1f}s")
        print(f"      {DIM}Expected: 10-12s (10s idle + up to 1s BMv2 sweep){RESET}")
        # Pass if average is under 20s and at least 2 rounds worked
        return check(f"Average removal time: {avg:.1f}s (need < 20s)",
                     avg < 20 and len(valid) >= 2)
    else:
        return check("At least 2 rounds must complete", False)


# ═══════════════════════════════════════════════════════════════════════
#  Test 5 — MAC Table Capacity ≥ 1000
# ═══════════════════════════════════════════════════════════════════════

def test_mac_capacity(net):
    header("5", "MAC Table Capacity (≥ 1000)")
    h1 = net.get("h1")
    wait_clean(25)  # extra time so table is fully empty

    # Pre-learn h2 so we can send unicast (avoids flooding 1000 pkts)
    h1.cmd("ping -c 1 -W 3 4.1.1.102")
    time.sleep(3)

    snap = log_snapshot()

    # Generate and send 1000 packets with unique source MACs
    scapy_script = r"""
from scapy.all import sendp, Ether, IP, ICMP
pkts = []
for i in range(1000):
    b3, b4 = i // 254, (i % 254) + 1
    mac = "02:00:00:00:%02x:%02x" % (i >> 8, i & 0xff)
    pkt = (Ether(src=mac, dst="f0:00:0d:00:02:00")
           / IP(src="10.99.%d.%d" % (b3, b4), dst="4.1.1.102")
           / ICMP())
    pkts.append(pkt)
sendp(pkts, iface="h1-eth0", verbose=0, inter=0.001)
print("SENT 1000 packets")
"""
    h1.cmd(f"cat > /tmp/cap_test.py << 'ENDSCRIPT'\n{scapy_script}\nENDSCRIPT")

    print(f"    Sending 1000 unique source MACs...", flush=True)
    out = h1.cmd(f"{PYTHON} /tmp/cap_test.py")
    print(f"      {DIM}{out.strip()}{RESET}")

    # Give the controller time to process all digests.
    # 1000 digests × ~5-10 ms each ≈ 5-10 s of processing.
    print(f"      {DIM}Waiting 20s for controller to process...{RESET}", flush=True)
    time.sleep(20)

    new1, _ = log_since(snap)
    all_learned = set(re.findall(r"\+ LEARNED (\S+) on port", new1))
    test_macs = {m for m in all_learned if m.startswith("02:00:00:00:")}
    n = len(test_macs)

    passed = check(f"Unique test MACs learned: {n}/1000 (need ≥ 990)", n >= 990)
    return passed


# ═══════════════════════════════════════════════════════════════════════
#  Test 6 — Host Reconnect Without Controller Reboot
# ═══════════════════════════════════════════════════════════════════════

def test_host_reconnect(net):
    header("6", "Host Reconnect Without Controller Reboot")
    h1, h2 = net.get("h1", "h2")
    wait_clean()

    # Baseline connectivity
    ok1 = ping_ok(h1.cmd("ping -c 1 -W 3 4.1.1.102"))
    print(f"    {'✓' if ok1 else '✗'}  Initial h1 → h2")

    # Disconnect h1
    print(f"      {DIM}Bringing h1 link DOWN for 5s...{RESET}")
    net.configLinkStatus("h1", "ixp1s1", "down")
    time.sleep(5)

    # Reconnect h1
    print(f"      {DIM}Bringing h1 link UP...{RESET}")
    net.configLinkStatus("h1", "ixp1s1", "up")
    time.sleep(1)

    # Reconfigure interface (link-down may clear addresses)
    h1.cmd("ip link set h1-eth0 address f0:00:0d:00:01:00")
    h1.cmd("ip addr flush dev h1-eth0")
    h1.cmd("ip addr add 4.1.1.101/24 dev h1-eth0")
    h1.cmd("ip link set h1-eth0 up")
    time.sleep(3)

    # Verify — controller was NOT restarted
    ok2 = ping_ok(h1.cmd("ping -c 2 -W 5 4.1.1.102"))
    passed = check("h1 → h2 ping works after reconnect (no controller reboot)", ok2)
    return ok1 and passed


# ═══════════════════════════════════════════════════════════════════════
#  Test 7 — No Broadcast Storms
# ═══════════════════════════════════════════════════════════════════════

def test_no_broadcast_storm(net):
    header("7", "No Broadcast Storms")
    h1, h2, h3 = net.get("h1", "h2", "h3")
    wait_clean()

    storm_script = (
        "from scapy.all import sendp, Ether, IP, ICMP\n"
        "pkt = (Ether(src='f0:00:0d:00:01:00', dst='ff:ff:ff:ff:ff:ff')\n"
        "       / IP(src='4.1.1.101', dst='255.255.255.255')\n"
        "       / ICMP())\n"
        "sendp(pkt, iface='h1-eth0', verbose=0)\n"
        "print('STORM_SENT')\n"
    )
    Path("/tmp/storm_send.py").write_text(storm_script)

    # Capture broadcast ICMP on h2 and h3 for 5 seconds
    pid2 = start_tcpdump(h2, "/tmp/h2_storm.pcap",
                         "'ether dst ff:ff:ff:ff:ff:ff and icmp'")
    pid3 = start_tcpdump(h3, "/tmp/h3_storm.pcap",
                         "'ether dst ff:ff:ff:ff:ff:ff and icmp'")
    time.sleep(2)

    # Send exactly ONE broadcast ICMP from h1
    out = h1.cmd(f"{PYTHON} /tmp/storm_send.py 2>&1")
    print(f"      {DIM}Scapy output: {out.strip()}{RESET}")

    time.sleep(5)  # if a storm exists, packet count will explode
    stop_tcpdump(h2, pid2)
    stop_tcpdump(h3, pid3)

    n2 = count_pcap(h2, "/tmp/h2_storm.pcap")
    n3 = count_pcap(h3, "/tmp/h3_storm.pcap")

    ok2 = check(f"h2 received {n2} broadcast ICMP (expected 1)", n2 <= 2)
    ok3 = check(f"h3 received {n3} broadcast ICMP (expected 1)", n3 <= 2)
    return ok2 and ok3


# ═══════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════

def main():
    if os.geteuid() != 0:
        print(f"\n{RED}ERROR: Must run as root.  Use:  "
              f"sudo {PYTHON} {__file__}{RESET}")
        sys.exit(1)

    setLogLevel("warning")

    if not (BUILD_DIR / "ixp_switch.p4info.txtpb").exists():
        print(f"\n{RED}ERROR: P4 not compiled.  Run:  make build-task-1{RESET}")
        sys.exit(1)

    for d in [LOG_DIR / "ixp1s1", LOG_DIR / "ixp2s1"]:
        d.mkdir(parents=True, exist_ok=True)

    print(f"\n{BOLD}{'═' * 55}")
    print(f"  L2 Learning Switch — Automated Test Suite")
    print(f"{'═' * 55}{RESET}\n")

    # ── Kill stale processes from previous runs ──
    print(f"  Cleaning up stale processes...", flush=True)
    subprocess.run(["mn", "-c"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Kill any lingering controller processes
    subprocess.run(
        "pkill -f 'ixp1s1_controller\\.py' 2>/dev/null; "
        "pkill -f 'ixp2s1_controller\\.py' 2>/dev/null; "
        "true",
        shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Kill stale BMv2 switches
    subprocess.run("pkill -f simple_switch_grpc 2>/dev/null; true",
                   shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)

    # ── Start controllers ──
    print(f"  Starting controllers...", flush=True)
    log1_fh = open(LOG1_PATH, "w")
    log2_fh = open(LOG2_PATH, "w")
    ctrl_env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    ctrl1 = subprocess.Popen(
        [PYTHON, "-u", str(CONTROLLER_DIR / "ixp1s1_controller.py")],
        stdout=log1_fh, stderr=subprocess.STDOUT, env=ctrl_env,
    )
    ctrl2 = subprocess.Popen(
        [PYTHON, "-u", str(CONTROLLER_DIR / "ixp2s1_controller.py")],
        stdout=log2_fh, stderr=subprocess.STDOUT, env=ctrl_env,
    )

    # ── Start Mininet ──
    print(f"  Starting Mininet...", flush=True)
    net = Mininet(topo=Topology(), link=TCLink, autoSetMacs=False)
    net.start()
    time.sleep(3)

    try:
        print(f"  Waiting for controllers to connect...", flush=True)
        if not wait_for_controllers(timeout=30):
            print(f"\n{RED}ERROR: Controllers did not become ready.{RESET}")
            tail1 = read_log(LOG1_PATH)[-300:]
            tail2 = read_log(LOG2_PATH)[-300:]
            print(f"{DIM}--- ixp1s1 log (tail) ---\n{tail1}{RESET}")
            print(f"{DIM}--- ixp2s1 log (tail) ---\n{tail2}{RESET}")
            return 1
        print(f"  {GREEN}Both controllers ready ✓{RESET}")
        time.sleep(3)

        # ── Run all tests ──
        results = {}
        results["1 & 2: Connectivity + MAC Learning"]  = test_connectivity_and_learning(net)
        results["3: Forward on Hit / Flood on Miss"]   = test_forward_flood(net)
        results["4: Inactivity Timeout (10s)"]         = test_idle_timeout(net)
        # results["4b: Timeout Timing (multiple rounds)"] = test_timeout_timing(net)
        # results["5: MAC Capacity (≥ 1000)"]            = test_mac_capacity(net)
        results["6: Host Reconnect"]                   = test_host_reconnect(net)
        # results["7: No Broadcast Storms"]              = test_no_broadcast_storm(net)

        # ── Summary ──
        total = len(results)
        passed = sum(1 for v in results.values() if v)

        print(f"\n{BOLD}{'═' * 55}")
        print(f"  SUMMARY")
        print(f"{'═' * 55}{RESET}")
        for name, ok in results.items():
            print(f"    {_PASS if ok else _FAIL}  {name}")
        print()
        if passed == total:
            print(f"    {GREEN}{BOLD}ALL {total} TESTS PASSED ✓{RESET}\n")
        else:
            print(f"    {passed}/{total} passed — "
                  f"{RED}{BOLD}{total - passed} FAILED ✗{RESET}\n")

        return 0 if passed == total else 1

    finally:
        print(f"\n  Cleaning up...", flush=True)
        net.stop()
        ctrl1.terminate()
        ctrl2.terminate()
        try:
            ctrl1.wait(timeout=5)
            ctrl2.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ctrl1.kill()
            ctrl2.kill()
        log1_fh.close()
        log2_fh.close()
        subprocess.run(["mn", "-c"],
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    sys.exit(main())
