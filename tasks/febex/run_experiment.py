#!/usr/bin/env python3
"""
FeBEx Experiment Orchestrator
==============================
Automates a single experiment run:
  1. Generate coverage matrix (if needed)
  2. Start Mininet with FeBEx topology (headless)
  3. Pre-populate ARP tables
  4. Launch LNS receivers + cloud receiver
  5. Start controller (installs rules, begins epoch rotation)
  6. Wait warmup
  7. Launch traffic generators on all gateways simultaneously
  8. Wait for traffic to finish + drain period
  9. Kill receivers, stop controller, stop Mininet
  10. Collect logs to results directory

Each experiment point needs two runs: dedup OFF (Configuration A)
and dedup ON (Configuration B).

Usage:
    sudo python3 run_experiment.py \\
        --config configs/medium_city.yaml \\
        --results-dir results/E1/avg3.2 \\
        --dedup         # or --no-dedup for baseline
"""

import argparse
import json
import math
import os
import shutil
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

import yaml
from networks import FeBExTopology  # noqa: E402
from generate_coverage import generate  # noqa: E402

PYTHON      = sys.executable
CTRL_SCRIPT = SCRIPT_DIR / "p4rt_controller" / "controller.py"
TGEN_SCRIPT = SCRIPT_DIR / "traffic_gen.py"
LNS_SCRIPT  = SCRIPT_DIR / "lns_receiver.py"
CLOUD_SCRIPT = SCRIPT_DIR / "cloud_receiver.py"
BUILD_DIR   = REPO_DIR / "build" / "p4"

GRPC_PORT   = 50051
THRIFT_PORT = 9090


def cleanup_stale():
    """Kill stale BMv2/controller processes."""
    subprocess.run(["mn", "-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run("pkill -9 -f simple_switch_grpc 2>/dev/null; true",
                   shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run("pkill -9 -f 'controller.py' 2>/dev/null; true",
                   shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)


def populate_arp(net, K, M, with_cloud):
    """Pre-populate ARP on all hosts."""
    gw_entries  = [(f"10.0.1.{i}", f"00:00:00:00:01:{i:02x}") for i in range(1, K + 1)]
    lns_entries = [(f"10.0.2.{i}", f"00:00:00:00:02:{i:02x}") for i in range(1, M + 1)]
    cloud_entry = [("10.0.3.1", "00:00:00:00:03:01")] if with_cloud else []
    all_entries = gw_entries + lns_entries + cloud_entry

    all_hosts = (
        [net.get(f"gw{i}")  for i in range(1, K + 1)] +
        [net.get(f"lns{i}") for i in range(1, M + 1)] +
        ([net.get("cloud1")] if with_cloud else [])
    )
    for host in all_hosts:
        for ip, mac in all_entries:
            host.cmd(f"arp -s {ip} {mac}")


def run_experiment(
    cfg: dict,
    coverage_json: dict,
    results_dir: Path,
    dedup_enabled: bool,
    with_cloud: bool = True,
    register_size: int = None,
    epoch_interval: float = None,
):
    """
    Run a single experiment (dedup ON or OFF) and collect logs.

    Parameters
    ----------
    cfg            : parsed YAML config
    coverage_json  : coverage matrix dict (from generate_coverage)
    results_dir    : where to write logs
    dedup_enabled  : True = FeBEx dedup, False = routing-only baseline
    with_cloud     : include cloud host for receipt mirroring
    register_size  : override DEDUP_TABLE_SIZE (requires recompile, caller's job)
    epoch_interval : override epoch interval in seconds
    """
    N = cfg["topology"]["num_edge_devices"]
    K = cfg["topology"]["num_hotspots"]
    M = cfg["topology"]["num_tenants"]

    workload = cfg.get("workload", {})
    uplinks     = workload.get("uplinks_per_device", 50)
    inter_ms    = workload.get("inter_arrival_ms", 100)
    payload_sz  = workload.get("payload_size_bytes", 20)

    dedup_cfg = cfg.get("dedup", {})
    if epoch_interval is None:
        epoch_interval = dedup_cfg.get("epoch_interval_s", 5.0)

    results_dir.mkdir(parents=True, exist_ok=True)
    log_dir = str(results_dir / "logs")
    os.makedirs(log_dir, exist_ok=True)

    # Write coverage JSON to results for reference
    cov_path = results_dir / "coverage.json"
    cov_path.write_text(json.dumps(coverage_json, indent=2))

    # ── 1. Check P4 build ──────────────────────────────────────────────
    if not (BUILD_DIR / "febex.json").exists():
        print("ERROR: P4 not compiled. Run 'make build-febex' first.", file=sys.stderr)
        return False

    # ── 2. Start Mininet ───────────────────────────────────────────────
    print(f"  Starting Mininet (K={K}, M={M}, cloud={with_cloud})...", flush=True)
    cleanup_stale()
    topo = FeBExTopology(
        num_gateways=K, num_lns=M, with_cloud=with_cloud,
        grpc_port=GRPC_PORT, thrift_port=THRIFT_PORT,
    )
    net = Mininet(topo=topo, link=TCLink, autoSetMacs=False)
    net.start()
    time.sleep(3)

    try:
        # ── 3. ARP ────────────────────────────────────────────────────
        populate_arp(net, K, M, with_cloud)

        # ── 4. Launch receivers ───────────────────────────────────────
        recv_procs = []
        recv_timeout = uplinks * (inter_ms / 1000.0) + 60  # generous timeout

        for i in range(1, M + 1):
            lns = net.get(f"lns{i}")
            cmd = (f"{PYTHON} -u {LNS_SCRIPT} "
                   f"--lns-id {i} --log-dir {log_dir} "
                   f"--iface lns{i}-eth0 --timeout {recv_timeout}")
            recv_procs.append(lns.popen(cmd, shell=False))

        if with_cloud:
            cloud = net.get("cloud1")
            cmd = (f"{PYTHON} -u {CLOUD_SCRIPT} "
                   f"--log-dir {log_dir} --iface cloud1-eth0 "
                   f"--timeout {recv_timeout}")
            recv_procs.append(cloud.popen(cmd, shell=False))

        time.sleep(1)

        # ── 5. Start controller ───────────────────────────────────────
        cloud_port = K + M + 1 if with_cloud else 0
        ctrl_log = results_dir / "controller.log"
        ctrl_fh = open(ctrl_log, "w")

        ctrl_cmd = [
            PYTHON, "-u", str(CTRL_SCRIPT),
            "--gateways",       str(K),
            "--tenants",        str(M),
            "--epoch-interval", str(epoch_interval),
            "--grpc-addr",      f"127.0.0.1:{GRPC_PORT}",
            "--thrift-port",    str(THRIFT_PORT),
            "--device-id",      "1",
        ]
        if not with_cloud:
            ctrl_cmd.append("--no-cloud")
        if not dedup_enabled:
            ctrl_cmd.append("--no-dedup")
        if cloud_port > 0:
            ctrl_cmd += ["--cloud-port", str(cloud_port)]

        ctrl_proc = subprocess.Popen(
            ctrl_cmd, stdout=ctrl_fh, stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )

        # Wait for controller ready
        print("  Waiting for controller...", flush=True)
        start = time.time()
        ready = False
        while time.time() - start < 40:
            try:
                log_text = ctrl_log.read_text()
                if "Controller ready" in log_text:
                    ready = True
                    break
            except FileNotFoundError:
                pass
            time.sleep(0.5)

        if not ready:
            print("  ERROR: Controller did not become ready!", file=sys.stderr)
            return False

        # ── 6. Warmup ─────────────────────────────────────────────────
        print("  Warmup (2s)...", flush=True)
        time.sleep(2)

        # ── 7. Launch traffic generators ──────────────────────────────
        # Write coverage JSON to /tmp for traffic_gen to read
        tmp_cov = f"/tmp/febex_coverage_{os.getpid()}.json"
        Path(tmp_cov).write_text(json.dumps(coverage_json))

        tgen_procs = []
        print(f"  Launching {K} traffic generators ({uplinks} uplinks, "
              f"{inter_ms}ms interval)...", flush=True)
        for gw_i in range(1, K + 1):
            gw = net.get(f"gw{gw_i}")
            cmd = (
                f"{PYTHON} -u {TGEN_SCRIPT} "
                f"--gw-id {gw_i} "
                f"--coverage {tmp_cov} "
                f"--uplinks {uplinks} "
                f"--inter-arrival-ms {inter_ms} "
                f"--iface gw{gw_i}-eth0 "
                f"--src-ip 10.0.1.{gw_i} "
                f"--src-mac 00:00:00:00:01:{gw_i:02x} "
                f"--payload-size {payload_sz} "
                f"--num-tenants {M}"
            )
            tgen_procs.append(gw.popen(cmd, shell=False))

        # ── 8. Wait for traffic gens to finish ────────────────────────
        est_duration = uplinks * (inter_ms / 1000.0)
        wait_time = est_duration + 10
        print(f"  Waiting ~{wait_time:.0f}s for traffic...", flush=True)

        for p in tgen_procs:
            try:
                p.wait(timeout=wait_time)
            except subprocess.TimeoutExpired:
                p.terminate()

        # ── 9. Drain ──────────────────────────────────────────────────
        drain = 2 * epoch_interval
        print(f"  Draining ({drain:.0f}s)...", flush=True)
        time.sleep(drain)

        # ── 10. Cleanup ──────────────────────────────────────────────
        for p in recv_procs:
            try:
                p.terminate()
            except Exception:
                pass

        ctrl_proc.terminate()
        try:
            ctrl_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ctrl_proc.kill()
        ctrl_fh.close()

        # Clean up tmp coverage
        try:
            os.unlink(tmp_cov)
        except OSError:
            pass

        print(f"  Logs saved to {results_dir}/logs/", flush=True)
        return True

    finally:
        net.stop()
        cleanup_stale()


def main():
    if os.geteuid() != 0:
        print(f"Must run as root: sudo {PYTHON} {__file__}", file=sys.stderr)
        sys.exit(1)

    setLogLevel("warning")

    parser = argparse.ArgumentParser(description="FeBEx experiment orchestrator")
    parser.add_argument("--config",      type=str, required=True,
                        help="YAML scenario config file")
    parser.add_argument("--results-dir", type=str, default="results/default",
                        help="Directory for output logs")
    parser.add_argument("--dedup",       action="store_true", default=True,
                        help="Enable deduplication (default)")
    parser.add_argument("--no-dedup",    action="store_true",
                        help="Disable deduplication (routing-only baseline)")
    parser.add_argument("--no-cloud",    action="store_true",
                        help="Skip cloud host / receipt mirroring")
    parser.add_argument("--seed",        type=int, default=None,
                        help="Override random seed")
    parser.add_argument("--epoch-interval", type=float, default=None,
                        help="Override epoch interval (seconds)")
    parser.add_argument("--coverage",    type=str, default=None,
                        help="Pre-generated coverage JSON (skip generation)")
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    seed = args.seed if args.seed is not None else cfg.get("experiment", {}).get("seed", 42)

    # Generate or load coverage
    if args.coverage:
        with open(args.coverage) as f:
            coverage_json = json.load(f)
    else:
        coverage_json = generate(cfg, seed)

    dedup = not args.no_dedup
    with_cloud = not args.no_cloud

    results = Path(args.results_dir)
    mode = "dedup_ON" if dedup else "dedup_OFF"
    run_dir = results / mode

    print(f"\n{'='*60}")
    print(f"  FeBEx Experiment: {mode}")
    print(f"  Config: {args.config}")
    print(f"  N={cfg['topology']['num_edge_devices']}, "
          f"K={cfg['topology']['num_hotspots']}, "
          f"M={cfg['topology']['num_tenants']}")
    print(f"  Coverage avg: {coverage_json['stats']['avg_coverage']}")
    print(f"{'='*60}\n")

    ok = run_experiment(
        cfg, coverage_json, run_dir,
        dedup_enabled=dedup,
        with_cloud=with_cloud,
        epoch_interval=args.epoch_interval,
    )

    if ok:
        print(f"\n  Experiment complete: {run_dir}")
    else:
        print(f"\n  Experiment FAILED: {run_dir}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
