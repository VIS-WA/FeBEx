#!/usr/bin/env python3
"""
FeBEx Full Experiment Suite (E1-E7)
====================================
Runs all seven experiments described in the spec (Section 11).
Each experiment sweeps one or more parameters and runs the orchestrator
for dedup ON and dedup OFF configurations.

Results are saved to results/E{N}/... directories.

Usage:
    # Run all experiments:
    sudo python3 run_all.py

    # Run specific experiments:
    sudo python3 run_all.py --experiments E1 E2 E3

    # Quick mode (fewer sweep points for testing):
    sudo python3 run_all.py --quick
"""

import argparse
import copy
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

SCRIPT_DIR  = Path(__file__).resolve().parent
REPO_DIR    = SCRIPT_DIR.parent.parent
RESULTS_DIR = REPO_DIR / "results"
PYTHON      = sys.executable
MAKE        = "make"

sys.path.insert(0, str(SCRIPT_DIR))
from generate_coverage import generate as generate_coverage  # noqa: E402


def make_cfg(N=100, K=10, M=2, avg_cov=3.0, uplinks=50, inter_ms=100,
             register_size=65536, epoch_s=5.0, seed=42, min_cov=1, max_cov=None):
    """Build a config dict programmatically."""
    if max_cov is None:
        max_cov = K
    return {
        "topology": {
            "num_edge_devices": N,
            "num_hotspots": K,
            "num_tenants": M,
        },
        "coverage": {
            "mode": "probabilistic",
            "avg_hotspots_per_device": avg_cov,
            "min_coverage": min_cov,
            "max_coverage": min(max_cov, K),
            "distribution": "poisson",
        },
        "workload": {
            "uplinks_per_device": uplinks,
            "inter_arrival_ms": inter_ms,
            "payload_size_bytes": 20,
        },
        "dedup": {
            "enabled": True,
            "register_size": register_size,
            "epoch_interval_s": epoch_s,
        },
        "experiment": {
            "seed": seed,
        },
    }


def run_orchestrator(cfg, results_dir, dedup, with_cloud=True, epoch_interval=None,
                     variant=1):
    """Run a single experiment via run_experiment.py."""
    # Write temp config
    cfg_path = Path(f"/tmp/febex_cfg_{os.getpid()}.yaml")
    cfg_path.write_text(yaml.dump(cfg, default_flow_style=False))

    cmd = [
        "sudo", PYTHON, str(SCRIPT_DIR / "run_experiment.py"),
        "--config",      str(cfg_path),
        "--results-dir", str(results_dir),
        "--variant",     str(variant),
    ]
    if not dedup:
        cmd.append("--no-dedup")
    if not with_cloud:
        cmd.append("--no-cloud")
    if epoch_interval is not None:
        cmd += ["--epoch-interval", str(epoch_interval)]

    print(f"\n{'─'*50}")
    print(f"  Running: dedup={'ON' if dedup else 'OFF'}, variant=V{variant}")
    print(f"  Results: {results_dir}")
    print(f"{'─'*50}", flush=True)

    result = subprocess.run(cmd, timeout=600)

    try:
        cfg_path.unlink()
    except OSError:
        pass

    return result.returncode == 0


def recompile_p4_variant(variant: int, dedup_size=None, key_hash_max=None):
    """Compile a specific dedup variant (1/2/3) into build/p4/febex.json."""
    src_map = {
        1: "tasks/febex/p4/febex.p4",
        2: "tasks/febex/p4/febex_v2.p4",
        3: "tasks/febex/p4/febex_v3.p4",
    }
    src = src_map.get(variant, src_map[1])
    print(f"\n  Compiling P4 variant V{variant} ({src})"
          f"{f' KEY_HASH_MAX={key_hash_max}' if key_hash_max else ''}...", flush=True)
    cmd = [MAKE, "-C", str(REPO_DIR), "build-clean", "build-init"]
    subprocess.run(cmd, capture_output=True)

    p4c_args = [
        "p4c-bm2-ss", "--p4v", "16",
        "--p4runtime-files", str(REPO_DIR / "build/p4/febex.p4info.txtpb"),
        "-o", str(REPO_DIR / "build/p4/febex.json"),
        str(REPO_DIR / src),
    ]
    if dedup_size:
        p4c_args.insert(1, f"-DDEDUP_TABLE_SIZE={dedup_size}")
    if key_hash_max:
        p4c_args.insert(1, f"-DKEY_HASH_MAX={key_hash_max}")
    result = subprocess.run(p4c_args, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  Compile FAILED: {result.stderr}", file=sys.stderr)
        return False
    return True


def recompile_p4(dedup_size=None):
    """Recompile P4 with optional custom DEDUP_TABLE_SIZE."""
    if dedup_size:
        print(f"\n  Recompiling P4 with DEDUP_TABLE_SIZE={dedup_size}...", flush=True)
        result = subprocess.run(
            [MAKE, "-C", str(REPO_DIR), "build-febex-size", f"DEDUP_SIZE={dedup_size}"],
            capture_output=True, text=True,
        )
    else:
        print("\n  Recompiling P4 (default size)...", flush=True)
        result = subprocess.run(
            [MAKE, "-C", str(REPO_DIR), "build-febex"],
            capture_output=True, text=True,
        )
    if result.returncode != 0:
        print(f"  Compile FAILED: {result.stderr}", file=sys.stderr)
        return False
    return True


# ═══════════════════════════════════════════════════════════════════════
#  E1: Backhaul savings vs. duplicate factor
# ═══════════════════════════════════════════════════════════════════════

def run_e1(quick=False):
    """Sweep avg_hotspots_per_device, measure savings %."""
    print("\n" + "=" * 60)
    print("  E1: Backhaul Savings vs. Duplicate Factor")
    print("=" * 60)

    sweep = [1, 2, 3, 5] if quick else [1, 2, 3, 5, 7, 10]
    base_dir = RESULTS_DIR / "E1"

    for avg_dup in sweep:
        cfg = make_cfg(N=100, K=10, M=2, avg_cov=avg_dup, uplinks=30 if quick else 50)
        point_dir = base_dir / f"avg{avg_dup}"

        # Save config for evaluate.py
        (point_dir).mkdir(parents=True, exist_ok=True)
        (point_dir / "config.yaml").write_text(yaml.dump(cfg))

        # Coverage (shared between both runs)
        cov = generate_coverage(cfg, seed=42)
        (point_dir / "coverage.json").write_text(json.dumps(cov, indent=2))

        # Run baseline (dedup OFF) then FeBEx (dedup ON)
        run_orchestrator(cfg, point_dir, dedup=False, with_cloud=False)
        run_orchestrator(cfg, point_dir, dedup=True, with_cloud=False)


# ═══════════════════════════════════════════════════════════════════════
#  E2: Correctness — zero unique-uplink loss
# ═══════════════════════════════════════════════════════════════════════

def run_e2(quick=False):
    """Same sweep as E1, measure delivery ratio (must be 1.0)."""
    print("\n" + "=" * 60)
    print("  E2: Correctness — Zero Unique-Uplink Loss")
    print("=" * 60)

    sweep = [1, 2, 3, 5] if quick else [1, 2, 3, 5, 7, 10]
    base_dir = RESULTS_DIR / "E2"

    for avg_dup in sweep:
        cfg = make_cfg(N=100, K=10, M=2, avg_cov=avg_dup, uplinks=30 if quick else 50)
        point_dir = base_dir / f"avg{avg_dup}"
        (point_dir).mkdir(parents=True, exist_ok=True)
        (point_dir / "config.yaml").write_text(yaml.dump(cfg))

        cov = generate_coverage(cfg, seed=42)
        (point_dir / "coverage.json").write_text(json.dumps(cov, indent=2))

        run_orchestrator(cfg, point_dir, dedup=True, with_cloud=False)


# ═══════════════════════════════════════════════════════════════════════
#  E3: Multi-tenant isolation
# ═══════════════════════════════════════════════════════════════════════

def run_e3(quick=False):
    """Single run: verify zero cross-tenant packets."""
    print("\n" + "=" * 60)
    print("  E3: Multi-Tenant Isolation")
    print("=" * 60)

    cfg = make_cfg(N=50 if quick else 100, K=10, M=4, avg_cov=3.0,
                   uplinks=20 if quick else 50)
    point_dir = RESULTS_DIR / "E3"
    (point_dir).mkdir(parents=True, exist_ok=True)
    (point_dir / "config.yaml").write_text(yaml.dump(cfg))

    cov = generate_coverage(cfg, seed=42)
    (point_dir / "coverage.json").write_text(json.dumps(cov, indent=2))

    run_orchestrator(cfg, point_dir, dedup=True, with_cloud=False)


# ═══════════════════════════════════════════════════════════════════════
#  E4: City-scale scalability
# ═══════════════════════════════════════════════════════════════════════

def run_e4(quick=False):
    """Sweep N x K, measure throughput and savings."""
    print("\n" + "=" * 60)
    print("  E4: City-Scale Scalability")
    print("=" * 60)

    if quick:
        sweep = [(50, 5), (100, 10)]
    else:
        sweep = [(50, 5), (100, 10), (200, 20), (500, 50)]

    base_dir = RESULTS_DIR / "E4"

    for N, K in sweep:
        cfg = make_cfg(N=N, K=K, M=2, avg_cov=3.0, uplinks=20 if quick else 50)
        point_dir = base_dir / f"N{N}_K{K}"
        (point_dir).mkdir(parents=True, exist_ok=True)
        (point_dir / "config.yaml").write_text(yaml.dump(cfg))

        cov = generate_coverage(cfg, seed=42)
        (point_dir / "coverage.json").write_text(json.dumps(cov, indent=2))

        run_orchestrator(cfg, point_dir, dedup=False, with_cloud=False)
        run_orchestrator(cfg, point_dir, dedup=True, with_cloud=False)


# ═══════════════════════════════════════════════════════════════════════
#  E5: Dedup state sizing (hash collisions)
# ═══════════════════════════════════════════════════════════════════════

def run_e5(quick=False):
    """Sweep register_size, requires P4 recompilation per point."""
    print("\n" + "=" * 60)
    print("  E5: Dedup State Sizing (Hash Collisions)")
    print("=" * 60)

    sweep = [256, 4096, 65536] if quick else [256, 1024, 4096, 16384, 65536]
    base_dir = RESULTS_DIR / "E5"

    for reg_size in sweep:
        cfg = make_cfg(N=100, K=10, M=2, avg_cov=3.0, register_size=reg_size,
                       uplinks=20 if quick else 50)
        point_dir = base_dir / f"regsize{reg_size}"
        (point_dir).mkdir(parents=True, exist_ok=True)
        (point_dir / "config.yaml").write_text(yaml.dump(cfg))

        cov = generate_coverage(cfg, seed=42)
        (point_dir / "coverage.json").write_text(json.dumps(cov, indent=2))

        # Recompile P4 with this register size
        if not recompile_p4(dedup_size=reg_size):
            print(f"  Skipping regsize={reg_size} due to compile failure")
            continue

        run_orchestrator(cfg, point_dir, dedup=True, with_cloud=False)

    # Restore default build
    recompile_p4()


# ═══════════════════════════════════════════════════════════════════════
#  E6: Epoch interval sensitivity
# ═══════════════════════════════════════════════════════════════════════

def run_e6(quick=False):
    """Sweep epoch_interval_s, measure dedup effectiveness."""
    print("\n" + "=" * 60)
    print("  E6: Epoch Interval Sensitivity")
    print("=" * 60)

    sweep = [1, 5, 30] if quick else [0.5, 1, 2, 5, 10, 30]
    base_dir = RESULTS_DIR / "E6"

    cfg = make_cfg(N=100, K=10, M=2, avg_cov=3.0, uplinks=20 if quick else 50)

    for epoch_s in sweep:
        point_dir = base_dir / f"epoch{epoch_s}s"
        (point_dir).mkdir(parents=True, exist_ok=True)

        cfg_copy = copy.deepcopy(cfg)
        cfg_copy["dedup"]["epoch_interval_s"] = epoch_s
        (point_dir / "config.yaml").write_text(yaml.dump(cfg_copy))

        cov = generate_coverage(cfg_copy, seed=42)
        (point_dir / "coverage.json").write_text(json.dumps(cov, indent=2))

        run_orchestrator(cfg_copy, point_dir, dedup=True, with_cloud=False,
                         epoch_interval=epoch_s)


# ═══════════════════════════════════════════════════════════════════════
#  E7: Payment receipt accuracy
# ═══════════════════════════════════════════════════════════════════════

def run_e7(quick=False):
    """Single run with cloud, verify receipt gw_id correctness."""
    print("\n" + "=" * 60)
    print("  E7: Payment Receipt Accuracy")
    print("=" * 60)

    # Use explicit coverage for deterministic verification
    N, K, M = 10 if quick else 50, 5, 2
    cfg = make_cfg(N=N, K=K, M=M, avg_cov=2.5, uplinks=10 if quick else 20)

    point_dir = RESULTS_DIR / "E7"
    (point_dir).mkdir(parents=True, exist_ok=True)
    (point_dir / "config.yaml").write_text(yaml.dump(cfg))

    cov = generate_coverage(cfg, seed=42)
    (point_dir / "coverage.json").write_text(json.dumps(cov, indent=2))

    run_orchestrator(cfg, point_dir, dedup=True, with_cloud=True)


# ═══════════════════════════════════════════════════════════════════════
#  E8: Epoch-boundary leakage — variant comparison
# ═══════════════════════════════════════════════════════════════════════

def run_e8(quick=False):
    """
    Stress-test epoch-boundary leakage and compare three dedup variants:

      V1 — Single epoch (baseline, original FeBEx):
           Packets that arrive just after an epoch rotation are treated as
           fresh and forwarded — boundary leakage.

      V2 — Sliding two-epoch window:
           Packets match if stored epoch == current OR == previous epoch.
           Controller writes prev_epoch before advancing current_epoch.
           Eliminates boundary leakage at the cost of slightly longer
           entry lifetime (2 × epoch_interval instead of 1 ×).

      V3 — Dual-register Bloom guard:
           Two independent register arrays; packet is a duplicate only if
           it matches in BOTH.  Epoch boundary behaviour is the same as V1
           (both arrays expire simultaneously), but false-positive
           suppression (collision-caused unique-uplink loss) is ≈ 1/N^2.

    Stress conditions:
      - Short epoch interval (1 s) to maximise boundary crossings
      - Slow inter-arrival (500 ms) so many packets span epoch boundaries
        (with 50 uplinks × 100 ms = 5 s total traffic and 1 s epochs,
         boundary crossings are practically guaranteed)
      - avg_cov = 5 (each uplink has 5 copies) for high duplicate volume

    Metrics collected per variant:
      - Backhaul savings %          (duplicate suppression effectiveness)
      - Delivery ratio              (correctness — no unique-uplink loss)
      - Leakage count               (boundary duplicates that slipped through)
    """
    print("\n" + "=" * 60)
    print("  E8: Epoch-Boundary Leakage — Variant Comparison")
    print("=" * 60)

    base_dir = RESULTS_DIR / "E8"

    # Stress workload: short epoch, slow inter-arrival, high coverage
    epoch_s  = 1 if quick else 1      # 1 s epoch → many boundary crossings
    inter_ms = 500                     # 500 ms between FCnt rounds
    avg_cov  = 5                       # 5 copies per unique uplink
    uplinks  = 10 if quick else 30     # 10–30 uplinks per device

    cfg = make_cfg(N=100, K=10, M=2, avg_cov=avg_cov,
                   uplinks=uplinks, inter_ms=inter_ms, epoch_s=epoch_s)

    # Shared coverage matrix so all three variants see identical traffic
    cov = generate_coverage(cfg, seed=42)

    for variant in [1, 2, 3]:
        label = {1: "V1_single_epoch", 2: "V2_sliding_window",
                 3: "V3_dual_register"}[variant]
        point_dir = base_dir / label
        point_dir.mkdir(parents=True, exist_ok=True)

        cfg_copy = copy.deepcopy(cfg)
        cfg_copy["dedup"]["epoch_interval_s"] = epoch_s
        (point_dir / "config.yaml").write_text(yaml.dump(cfg_copy))
        (point_dir / "coverage.json").write_text(json.dumps(cov, indent=2))

        # Compile the correct P4 variant into build/p4/febex.json
        if not recompile_p4_variant(variant):
            print(f"  Skipping V{variant} due to compile failure")
            continue

        # dedup OFF baseline (V1 compile is sufficient — no dedup logic runs)
        if variant == 1:
            run_orchestrator(cfg_copy, point_dir, dedup=False, with_cloud=False,
                             epoch_interval=epoch_s, variant=1)

        # dedup ON with this variant
        run_orchestrator(cfg_copy, point_dir, dedup=True, with_cloud=False,
                         epoch_interval=epoch_s, variant=variant)

    # Restore default V1 build
    recompile_p4()


# ═══════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════
#  E9: False-positive suppression — V1 vs V3 at small register sizes
# ═══════════════════════════════════════════════════════════════════════

def run_e9(quick=False):
    """
    False-positive suppression: V1 (single array) vs V3 (Bloom AND).

    A false positive occurs when a UNIQUE uplink is incorrectly suppressed
    because its key_value hash collides with a different device's entry in
    the same register slot.

    With the production 32-bit key_value (KEY_HASH_MAX=0xFFFFFFFE), collisions
    are astronomically rare.  To make false positives empirically observable,
    this experiment compiles with a deliberately narrow KEY_HASH_MAX — reducing
    the key space so collisions happen frequently.

    V1: suppress if stored_key == key_value  (single 32-bit check)
        False positive prob per packet ~ N / KEY_HASH_MAX

    V3: suppress if BOTH arrays match (AND logic, two independent key hashes)
        False positive prob per packet ~ (N / KEY_HASH_MAX)^2
        This is the measurable benefit of V3.

    Sweep KEY_HASH_MAX from 8 (3-bit, very frequent) to 65536 (16-bit, rare).
    Register size is fixed large (65536) so slot overwrites are not a confound —
    only key_value collisions drive false positives here.

    Expected trend:
      KEY_HASH_MAX=8:   V1 ~N/8 fp rate (high), V3 ~(N/8)^2 (much higher separation)
      KEY_HASH_MAX=256: V1 moderate, V3 near-zero
      KEY_HASH_MAX=65536: both converge to zero
    """
    print("\n" + "=" * 60)
    print("  E9: False-Positive Suppression — V1 vs V3 (Bloom AND)")
    print("=" * 60)

    # Sweep key hash space size (smaller = more collisions = more false positives)
    sweep = [8, 64, 256] if quick else [8, 16, 32, 64, 128, 256, 1024, 65536]
    base_dir = RESULTS_DIR / "E9"

    # Large register (no slot-overwrite confound), high N to maximise collision rate,
    # long epoch (no boundary-leakage confound), moderate traffic
    cfg = make_cfg(N=100, K=10, M=2, avg_cov=3.0,
                   uplinks=10 if quick else 30, inter_ms=100,
                   register_size=65536, epoch_s=60.0)

    cov = generate_coverage(cfg, seed=42)

    for khmax in sweep:
        for variant in [1, 3]:
            label = f"khmax{khmax}_V{variant}"
            point_dir = base_dir / label
            point_dir.mkdir(parents=True, exist_ok=True)

            cfg_copy = copy.deepcopy(cfg)
            (point_dir / "config.yaml").write_text(yaml.dump(cfg_copy))
            (point_dir / "coverage.json").write_text(json.dumps(cov, indent=2))

            if not recompile_p4_variant(variant, key_hash_max=khmax):
                print(f"  Skipping khmax={khmax} V{variant} due to compile failure")
                continue

            run_orchestrator(cfg_copy, point_dir, dedup=True, with_cloud=False,
                             variant=variant)

    # Restore default V1 build (full 32-bit key)
    recompile_p4()


EXPERIMENTS = {
    "E1": run_e1,
    "E2": run_e2,
    "E3": run_e3,
    "E4": run_e4,
    "E5": run_e5,
    "E6": run_e6,
    "E7": run_e7,
    "E8": run_e8,
    "E9": run_e9,
}


def main():
    if os.geteuid() != 0:
        print(f"Must run as root: sudo {PYTHON} {__file__}", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="FeBEx full experiment suite (E1-E9)")
    parser.add_argument("--experiments", nargs="+",
                        choices=list(EXPERIMENTS.keys()),
                        default=list(EXPERIMENTS.keys()),
                        help="Which experiments to run (default: all)")
    parser.add_argument("--quick", action="store_true",
                        help="Fewer sweep points, fewer uplinks (for testing)")
    args = parser.parse_args()

    # Ensure P4 is compiled
    if not (REPO_DIR / "build" / "p4" / "febex.json").exists():
        print("P4 not compiled. Running make build-febex...", flush=True)
        if not recompile_p4():
            sys.exit(1)

    print("\n" + "=" * 60)
    print("  FeBEx Experiment Suite")
    print(f"  Experiments: {', '.join(args.experiments)}")
    print(f"  Quick mode:  {args.quick}")
    print(f"  Results dir: {RESULTS_DIR}")
    print("=" * 60)

    t0 = time.time()
    for name in args.experiments:
        EXPERIMENTS[name](quick=args.quick)

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"  All experiments complete ({elapsed:.0f}s)")
    print(f"  Results in: {RESULTS_DIR}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
