#!/usr/bin/env python3
"""
Task 2 — Route Alteration: Automated Test Suite
================================================

Tests:
  0. Basic L2 reachability (after BGP converges)
  1. Controller loads route-alteration JSON (log check)
  2. RTT drop — match-ICMP vs non-match-ICMP
  3. Per-rule altered egress verification (Scapy + tcpdump)
  4. Non-match traffic is NOT altered (negative test)

How to run:
  1. Compile the P4 program:
       make build-task-2

  2. Run the test (as root, since Mininet requires root):
       sudo /opt/p4/p4dev-python-venv/bin/python3 tasks/2/test_switch.py
"""

import json
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
sys.path.insert(0, str(REPO_DIR / "networks" / "2" / "mininet"))

from networks import Topology  # noqa: E402

# ── Configuration ───────────────────────────────────────────────────────

PYTHON = sys.executable
BUILD_DIR = REPO_DIR / "build" / "p4"
CONTROLLER_DIR = SCRIPT_DIR / "p4rt_controller"

LOG_DIR = REPO_DIR / "temp" / "p4rt_controller"
LOG1_PATH = LOG_DIR / "ixp1s1" / "ixp1s1_controller-stdout.log"
LOG2_PATH = LOG_DIR / "ixp2s1" / "ixp2s1_controller-stdout.log"

IXP1_JSON = REPO_DIR / "networks" / "2" / "ixp_switch" / "ixp1s1-route-alterations.json"
IXP2_JSON = REPO_DIR / "networks" / "2" / "ixp_switch" / "ixp2s1-route-alterations.json"

SWITCHES = [
    ("ixp1s1", LOG1_PATH, IXP1_JSON),
    ("ixp2s1", LOG2_PATH, IXP2_JSON),
]

# BGP needs time to converge across 4 ASes
BGP_CONVERGE_WAIT_S = 90
CONTROLLER_READY_TIMEOUT_S = 30

RTT_ABS_MAX_MS = 50.0

# ── Terminal colours ────────────────────────────────────────────────────

GREEN = "\033[92m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
_PASS = f"{GREEN}PASS{RESET}"
_FAIL = f"{RED}FAIL{RESET}"


# ═══════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════


def read_log(path):
    try:
        return Path(path).read_text()
    except FileNotFoundError:
        return ""


def log_snapshot():
    return len(read_log(LOG1_PATH)), len(read_log(LOG2_PATH))


def log_since(snapshot):
    l1, l2 = snapshot
    return read_log(LOG1_PATH)[l1:], read_log(LOG2_PATH)[l2:]


def ping_ok(result):
    return "0% packet loss" in result


def parse_ping_avg_ms(ping_out: str):
    m = re.search(r"rtt .* = ([0-9.]+)/([0-9.]+)/([0-9.]+)/([0-9.]+) ms", ping_out)
    return float(m.group(2)) if m else None


def wait_for_controllers(timeout=CONTROLLER_READY_TIMEOUT_S):
    start = time.time()
    while time.time() - start < timeout:
        if ("Controller ready" in read_log(LOG1_PATH) and
                "Controller ready" in read_log(LOG2_PATH)):
            return True
        time.sleep(0.5)
    return False


def wait_for_bgp(net, timeout=BGP_CONVERGE_WAIT_S):
    """Wait until as1r1 has a BGP route to 8.1.2.0/24."""
    print(f"      {DIM}Waiting up to {timeout}s for BGP convergence...{RESET}", flush=True)
    as1r1 = net.get("as1r1")
    start = time.time()
    while time.time() - start < timeout:
        routes = as1r1.cmd("ip route")
        if "8.1.2.0" in routes:
            elapsed = time.time() - start
            print(f"      {DIM}BGP converged after {elapsed:.0f}s{RESET}")
            return True
        time.sleep(3)
    return False


def header(num, title):
    print(f"\n{BOLD}{CYAN}{'═' * 60}")
    print(f"  Test {num}: {title}")
    print(f"{'═' * 60}{RESET}")


def check(label, passed):
    print(f"    {_PASS if passed else _FAIL}  {label}")
    return passed


def load_rules(path):
    with open(path, "r") as f:
        return json.load(f)


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


def count_pcap(host, pcap, bpf_filter=""):
    """Count matching packets in a pcap using tcpdump from the host."""
    out = host.cmd(f"tcpdump -nn -r {pcap} {bpf_filter} 2>/dev/null | wc -l").strip()
    try:
        return int(out)
    except ValueError:
        return 0


def build_ip_to_host(net):
    m = {}
    for h in net.hosts:
        ip = h.IP()
        if ip:
            m[ip] = h
    return m


# Topology mapping: which router injects into which IXP switch, on which interface
# ixp1s1-eth0 <-> as1r1-eth1,  ixp2s1-eth0 <-> as2r1-eth1
IXP_INJECT = {
    "ixp1s1": {"node": "as1r1", "intf": "as1r1-eth1", "mac": "f0:00:0d:01:01:01"},
    "ixp2s1": {"node": "as2r1", "intf": "as2r1-eth1", "mac": "f0:00:0d:01:02:01"},
}


def scapy_send_rule(inject_host, inject_intf, inject_mac, rule,
                    nonmatch=False, count=3):
    """
    Inject packets *directly* into the IXP switch from the router
    connected to it.  This bypasses OVS and avoids the problem of
    routers dropping broadcast-dst-MAC IP packets.

    inject_host : the Mininet node connected to the IXP switch port 1
    inject_intf : the interface name on that node (e.g. as1r1-eth1)
    inject_mac  : the MAC address of that interface (used as src MAC)
    """
    src_ip = rule["src_addr"]
    dst_ip = rule["dst_addr"]
    proto = int(rule["protocol"])
    sport = int(rule.get("src_port", 0))
    dport = int(rule.get("dst_port", 0))

    if nonmatch and proto in (6, 17):
        sport += 111  # shift to avoid matching

    if proto == 1:
        l4 = "ICMP()"
    elif proto == 6:
        l4 = f"TCP(sport={sport}, dport={dport}, flags='S')"
    elif proto == 17:
        l4 = f"UDP(sport={sport}, dport={dport})"
    else:
        raise ValueError(f"Unsupported proto {proto}")

    script = (
        "from scapy.all import sendp, Ether, IP, ICMP, TCP, UDP\n"
        f'pkt = Ether(src="{inject_mac}", dst="ff:ff:ff:ff:ff:ff")'
        f'/IP(src="{src_ip}", dst="{dst_ip}")/{l4}\n'
        f'sendp(pkt, iface="{inject_intf}", count={count}, inter=0.1, verbose=0)\n'
        'print("SENT")\n'
    )
    inject_host.cmd(f"cat > /tmp/t2_send.py << 'PY'\n{script}\nPY")
    inject_host.cmd(f"{PYTHON} /tmp/t2_send.py 2>&1")


# ═══════════════════════════════════════════════════════════════════════
#  Test 0 — Basic reachability (post-BGP convergence)
# ═══════════════════════════════════════════════════════════════════════

def test_basic_reachability(net):
    header("0", "Basic reachability (post-BGP convergence)")

    # Direct IXP subnet pings (no BGP needed)
    as1r1 = net.get("as1r1")
    ok_direct = ping_ok(as1r1.cmd("ping -c 2 -W 3 8.2.1.2"))
    check("as1r1 -> as3r1 (IXP1 direct, 8.2.1.2)", ok_direct)

    ok_direct2 = ping_ok(as1r1.cmd("ping -c 2 -W 3 8.2.1.3"))
    check("as1r1 -> as4r1 (IXP1 direct, 8.2.1.3)", ok_direct2)

    # Cross-AS end-to-end (requires BGP)
    as1h1 = net.get("as1h1")
    out = as1h1.cmd("ping -c 2 -W 5 8.1.2.101")
    ok_e2e = ping_ok(out)
    check("as1h1 -> as2h1 (end-to-end, 8.1.2.101)", ok_e2e)

    return ok_direct and ok_direct2 and ok_e2e


# ═══════════════════════════════════════════════════════════════════════
#  Test 1 — Controller loaded JSON rules (log-based)
# ═══════════════════════════════════════════════════════════════════════

def test_controller_loaded_json():
    header("1", "Controller loaded route-alteration JSON")
    ok_all = True
    for sw, log_path, json_path in SWITCHES:
        expected = load_rules(json_path)
        log_txt = read_log(log_path)

        # Our controller logs: "Route alteration: X -> Y proto=P -> port N"
        installed = re.findall(r"Route alteration:.*proto=", log_txt)
        got = len(installed)
        want = len(expected)
        ok_all &= check(
            f"[{sw}] route-alteration rules installed: {got} (expected {want})",
            got == want
        )
    return ok_all


# ═══════════════════════════════════════════════════════════════════════
#  Test 2 — RTT drop (matched ICMP vs non-matched ICMP)
# ═══════════════════════════════════════════════════════════════════════

def test_rtt_drop(net, ixp1_rules):
    header("2", "RTT drop — matched ICMP vs non-matched ICMP")

    icmp_rules = [r for r in ixp1_rules if int(r.get("protocol", 0)) == 1]
    if not icmp_rules:
        return check("ixp1s1 has an ICMP rule in JSON", False)

    r = icmp_rules[0]
    src_ip, dst_ip = r["src_addr"], r["dst_addr"]

    ip_to_host = build_ip_to_host(net)
    src = ip_to_host.get(src_ip)   # as1h1 (8.1.1.101) — matched by ICMP rule
    dst = ip_to_host.get(dst_ip)   # as2h1 (8.1.2.101)
    if src is None or dst is None:
        return check(f"hosts exist for {src_ip} and {dst_ip}", False)

    # Find a host on the same subnet that does NOT have a route-alter rule
    # as1h2 (8.1.1.102) — no ICMP rule for this source
    alt_src = None
    pref = ".".join(src_ip.split(".")[:3]) + "."
    for hip, h in ip_to_host.items():
        if hip != src_ip and hip.startswith(pref):
            alt_src = h
            break
    if alt_src is None:
        return check("found baseline host on same subnet", False)

    # Matched path: as1h1 -> as2h1 (ICMP rule redirects via fast AS 400 path)
    out1 = src.cmd(f"ping -c 6 -W 3 {dst_ip}")
    avg1 = parse_ping_avg_ms(out1)

    # Non-matched path: as1h2 -> as2h1 (no rule, goes via slow BGP-preferred AS 300)
    out2 = alt_src.cmd(f"ping -c 6 -W 3 {dst_ip}")
    avg2 = parse_ping_avg_ms(out2)

    print(f"      {DIM}Matched   src={src.name}({src_ip}) -> {dst_ip}: avg={avg1} ms{RESET}")
    print(f"      {DIM}Unmatched src={alt_src.name}({alt_src.IP()}) -> {dst_ip}: avg={avg2} ms{RESET}")

    if avg1 is None or avg2 is None:
        return check("parsed ping RTT averages", False)

    # Matched traffic should be MUCH faster (fast path ~10ms vs slow path ~1000ms)
    abs_ok = check(
        f"Matched RTT is low ({avg1:.1f}ms <= {RTT_ABS_MAX_MS}ms)",
        avg1 <= RTT_ABS_MAX_MS)
    ratio_ok = check(
        f"Matched RTT << unmatched ({avg1:.1f}ms vs {avg2:.1f}ms)",
        avg1 < avg2 * 0.5)

    return abs_ok and ratio_ok


# ═══════════════════════════════════════════════════════════════════════
#  Test 3 — Per-rule altered egress verification (Scapy + tcpdump)
# ═══════════════════════════════════════════════════════════════════════

def test_rule_egress(net, sw, rule, label):
    """Send matching traffic directly into the IXP switch and verify
    it exits on the right port with the dst MAC rewritten to egress_mac."""
    expected_port = int(rule["egress_port"])
    emac = rule["egress_mac"].lower()
    proto = int(rule["protocol"])

    src_ip = rule["src_addr"]
    dst_ip = rule["dst_addr"]
    sport = int(rule.get("src_port", 0))
    dport = int(rule.get("dst_port", 0))

    # BPF filters for the expected traffic
    if proto == 1:
        bpf = f"'icmp and src host {src_ip} and dst host {dst_ip}'"
        mac_bpf = f"'ether dst {emac} and icmp and src host {src_ip} and dst host {dst_ip}'"
    elif proto == 6:
        bpf = (f"'tcp and src host {src_ip} and dst host {dst_ip} "
               f"and src port {sport} and dst port {dport}'")
        mac_bpf = (f"'ether dst {emac} and tcp and src host {src_ip} and dst host {dst_ip} "
                   f"and src port {sport} and dst port {dport}'")
    elif proto == 17:
        bpf = (f"'udp and src host {src_ip} and dst host {dst_ip} "
               f"and src port {sport} and dst port {dport}'")
        mac_bpf = (f"'ether dst {emac} and udp and src host {src_ip} and dst host {dst_ip} "
                   f"and src port {sport} and dst port {dport}'")
    else:
        return check(f"[{sw}] {label}: unsupported proto", False)

    # Topology mapping
    # ixp1s1: eth0=as1r1(port1), eth1=as3r1(port2), eth2=as4r1(port3)
    # ixp2s1: eth0=as2r1(port1), eth1=as3r1(port2), eth2=as4r1(port3)
    if sw == "ixp1s1":
        port_to_node = {1: "as1r1", 2: "as3r1", 3: "as4r1"}
        port_to_intf = {1: "as1r1-eth1", 2: "as3r1-eth0", 3: "as4r1-eth0"}
    else:
        port_to_node = {1: "as2r1", 2: "as3r1", 3: "as4r1"}
        port_to_intf = {1: "as2r1-eth1", 2: "as3r1-eth1", 3: "as4r1-eth1"}

    peer_name = port_to_node.get(expected_port)
    peer_intf = port_to_intf.get(expected_port)
    if peer_name is None:
        return check(f"[{sw}] {label}: known egress port {expected_port}", False)
    peer = net.get(peer_name)

    # Injection point: the router directly connected to the IXP switch port 1
    inj = IXP_INJECT[sw]
    inject_host = net.get(inj["node"])

    # Start tcpdump on the peer's interface — capture ALL traffic (no BPF
    # capture filter) so we never lose packets to a filter-compile race.
    pcap = f"/tmp/t2_{sw}_{label}_{peer_name}.pcap"
    peer.cmd(f"rm -f {pcap}")
    pid_file = f"/tmp/td_{sw}_{label}.pid"
    peer.cmd(f"tcpdump -U -i {peer_intf} -w {pcap} "
             f"& echo $! > {pid_file}")
    time.sleep(1)

    # Send matching packets directly into the IXP switch
    scapy_send_rule(inject_host, inj["intf"], inj["mac"], rule,
                    nonmatch=False, count=3)
    time.sleep(2)

    peer.cmd(f"kill $(cat {pid_file}) 2>/dev/null")
    time.sleep(2)  # extra time for pcap flush

    n_total = count_pcap(peer, pcap, bpf)
    ok_seen = check(
        f"[{sw}] {label}: {n_total} packets at egress port {expected_port} ({peer_name}) (need >0)",
        n_total > 0)

    n_mac = count_pcap(peer, pcap, mac_bpf)
    ok_mac = check(
        f"[{sw}] {label}: {n_mac} packets with dst MAC={emac} (need >0)",
        n_mac > 0)

    return ok_seen and ok_mac


def test_all_rules_egress(net, ixp1_rules, ixp2_rules):
    header("3", "Per-rule altered egress (Scapy + tcpdump)")
    ok_all = True
    for r in ixp1_rules:
        proto = int(r["protocol"])
        label = {1: "ICMP", 6: "TCP", 17: "UDP"}.get(proto, f"PROTO{proto}")
        ok_all &= test_rule_egress(net, "ixp1s1", r, label)
    for r in ixp2_rules:
        proto = int(r["protocol"])
        label = {1: "ICMP", 6: "TCP", 17: "UDP"}.get(proto, f"PROTO{proto}")
        ok_all &= test_rule_egress(net, "ixp2s1", r, label)
    return ok_all


# ═══════════════════════════════════════════════════════════════════════
#  Test 4 — Non-match traffic is NOT altered (negative test)
# ═══════════════════════════════════════════════════════════════════════

def test_nonmatch_negative(net, sw, rule, label):
    """Send traffic that does NOT match the rule and verify it does NOT
    appear at the route-altered egress port with the egress_mac."""
    proto = int(rule["protocol"])
    if proto == 1:
        # Can't easily make a non-matching ICMP (same src/dst always matches)
        return check(f"[{sw}] {label}: non-match negative (skipped for ICMP)", True)

    expected_port = int(rule["egress_port"])
    emac = rule["egress_mac"].lower()

    src_ip = rule["src_addr"]
    dst_ip = rule["dst_addr"]
    sport = int(rule.get("src_port", 0)) + 111  # shifted = non-matching
    dport = int(rule.get("dst_port", 0))

    if proto == 6:
        mac_bpf = (f"'ether dst {emac} and tcp and src host {src_ip} and dst host {dst_ip} "
                   f"and src port {sport} and dst port {dport}'")
    elif proto == 17:
        mac_bpf = (f"'ether dst {emac} and udp and src host {src_ip} and dst host {dst_ip} "
                   f"and src port {sport} and dst port {dport}'")
    else:
        return check(f"[{sw}] {label}: unsupported proto for negative", False)

    if sw == "ixp1s1":
        port_to_node = {1: "as1r1", 2: "as3r1", 3: "as4r1"}
        port_to_intf = {1: "as1r1-eth1", 2: "as3r1-eth0", 3: "as4r1-eth0"}
    else:
        port_to_node = {1: "as2r1", 2: "as3r1", 3: "as4r1"}
        port_to_intf = {1: "as2r1-eth1", 2: "as3r1-eth1", 3: "as4r1-eth1"}

    peer_name = port_to_node.get(expected_port)
    peer_intf = port_to_intf.get(expected_port)
    peer = net.get(peer_name)

    # Injection point: router directly connected to the IXP switch
    inj = IXP_INJECT[sw]
    inject_host = net.get(inj["node"])

    pcap = f"/tmp/t2_neg_{sw}_{label}_{peer_name}.pcap"
    peer.cmd(f"rm -f {pcap}")
    pid_file = f"/tmp/td_neg_{sw}_{label}.pid"
    peer.cmd(f"tcpdump -U -i {peer_intf} -w {pcap} "
             f"& echo $! > {pid_file}")
    time.sleep(1)

    # Send non-matching packets (shifted src_port) directly into IXP switch
    scapy_send_rule(inject_host, inj["intf"], inj["mac"], rule,
                    nonmatch=True, count=3)
    time.sleep(2)

    peer.cmd(f"kill $(cat {pid_file}) 2>/dev/null")
    time.sleep(2)  # extra time for pcap flush

    n = count_pcap(peer, pcap, mac_bpf)
    ok = check(f"[{sw}] {label}: non-match packets with altered MAC at egress = {n} (expected 0)", n == 0)
    return ok


def test_all_nonmatch(net, ixp1_rules, ixp2_rules):
    header("4", "Non-match does NOT trigger alteration (negative)")
    ok_all = True
    for r in ixp1_rules:
        proto = int(r["protocol"])
        label = {1: "ICMP", 6: "TCP", 17: "UDP"}.get(proto, f"PROTO{proto}")
        ok_all &= test_nonmatch_negative(net, "ixp1s1", r, label)
    for r in ixp2_rules:
        proto = int(r["protocol"])
        label = {1: "ICMP", 6: "TCP", 17: "UDP"}.get(proto, f"PROTO{proto}")
        ok_all &= test_nonmatch_negative(net, "ixp2s1", r, label)
    return ok_all


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
        print(f"\n{RED}ERROR: P4 not compiled.  Run:  make build-task-2{RESET}")
        sys.exit(1)

    for d in [LOG_DIR / "ixp1s1", LOG_DIR / "ixp2s1"]:
        d.mkdir(parents=True, exist_ok=True)

    print(f"\n{BOLD}{'═' * 60}")
    print(f"  Task 2 — Route Alteration: Automated Test Suite")
    print(f"{'═' * 60}{RESET}\n")

    # ── Kill stale processes from previous runs ──
    print(f"  Cleaning up stale processes...", flush=True)
    subprocess.run(["mn", "-c"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(
        "pkill -f 'ixp1s1_controller\\.py' 2>/dev/null; "
        "pkill -f 'ixp2s1_controller\\.py' 2>/dev/null; "
        "pkill -f simple_switch_grpc 2>/dev/null; "
        "true",
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
    print(f"  Starting Mininet (network 2 topology)...", flush=True)
    net = Mininet(topo=Topology(), link=TCLink, autoSetMacs=False)
    net.start()
    time.sleep(3)

    try:
        # ── Wait for controllers ──
        print(f"  Waiting for controllers to connect...", flush=True)
        if not wait_for_controllers():
            print(f"\n{RED}ERROR: Controllers did not become ready.{RESET}")
            tail1 = read_log(LOG1_PATH)[-500:]
            tail2 = read_log(LOG2_PATH)[-500:]
            print(f"{DIM}--- ixp1s1 log (tail) ---\n{tail1}{RESET}")
            print(f"{DIM}--- ixp2s1 log (tail) ---\n{tail2}{RESET}")
            return 1
        print(f"  {GREEN}Both controllers ready ✓{RESET}")

        # ── Wait for BGP convergence ──
        print(f"  Waiting for BGP convergence...", flush=True)
        if not wait_for_bgp(net):
            print(f"  {RED}WARNING: BGP may not have fully converged!{RESET}")
            print(f"  {DIM}Continuing tests anyway...{RESET}")
        else:
            print(f"  {GREEN}BGP converged ✓{RESET}")

        # ── Load rules ──
        ixp1_rules = load_rules(IXP1_JSON)
        ixp2_rules = load_rules(IXP2_JSON)

        # ── Run tests ──
        results = {}
        results["0: Basic reachability"]            = test_basic_reachability(net)
        results["1: Controller loads JSON"]          = test_controller_loaded_json()
        results["2: RTT drop (match vs non-match)"] = test_rtt_drop(net, ixp1_rules)
        results["3: Altered egress per rule"]        = test_all_rules_egress(net, ixp1_rules, ixp2_rules)
        results["4: Non-match negative test"]        = test_all_nonmatch(net, ixp1_rules, ixp2_rules)

        # ── Summary ──
        total = len(results)
        passed = sum(1 for v in results.values() if v)

        print(f"\n{BOLD}{'═' * 60}")
        print(f"  SUMMARY")
        print(f"{'═' * 60}{RESET}")
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