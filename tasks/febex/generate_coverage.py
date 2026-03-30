#!/usr/bin/env python3
"""
FeBEx Coverage Matrix Generator
================================
Reads a YAML scenario config and outputs a JSON coverage file describing
which hotspots hear which edge devices, along with DevAddr assignments.

Three coverage modes:
  - probabilistic: Poisson-sampled coverage count, random hotspot selection
  - radius:        2D spatial placement, coverage = distance < range
  - explicit:      Matrix directly specified in YAML

Output JSON format:
{
  "num_edge_devices": N,
  "num_hotspots": K,
  "num_tenants": M,
  "coverage_matrix": [[1,0,1,...], ...],   # N x K
  "device_tenant_map": [0, 1, 0, ...],     # tenant index per ED (0-based)
  "device_devaddr": [256, 2147483905, ...], # integer DevAddr per ED
  "stats": { "avg_coverage": 3.2, "min_coverage": 1, "max_coverage": 7 }
}

Usage:
    python3 generate_coverage.py --config configs/medium_city.yaml --output coverage.json
    python3 generate_coverage.py --config configs/medium_city.yaml  # prints to stdout
"""

import argparse
import json
import math
import random
import sys

import yaml


def tenant_devaddr_prefix(tenant_idx: int, num_tenants: int):
    """Return (prefix_value, prefix_len) for a tenant (0-based index)."""
    if num_tenants <= 1:
        return (0x00000000, 0)
    prefix_len = math.ceil(math.log2(num_tenants))
    prefix_val = tenant_idx << (32 - prefix_len)
    return (prefix_val, prefix_len)


def random_devaddr(tenant_idx: int, num_tenants: int, rng: random.Random) -> int:
    """Generate a random DevAddr within a tenant's prefix range."""
    prefix_val, prefix_len = tenant_devaddr_prefix(tenant_idx, num_tenants)
    if prefix_len == 0:
        return rng.randint(1, 0xFFFFFFFF)
    host_bits = 32 - prefix_len
    host_part = rng.randint(1, (1 << host_bits) - 1)
    return prefix_val | host_part


def generate_probabilistic(cfg: dict, rng: random.Random) -> list:
    """Generate coverage matrix using Poisson-sampled coverage counts."""
    N = cfg["topology"]["num_edge_devices"]
    K = cfg["topology"]["num_hotspots"]
    cov = cfg.get("coverage", {})
    avg = cov.get("avg_hotspots_per_device", 3.0)
    min_cov = cov.get("min_coverage", 1)
    max_cov = min(cov.get("max_coverage", K), K)

    matrix = []
    for _ in range(N):
        # Sample coverage count
        count = int(rng.gauss(avg, max(1, avg * 0.3)))
        if cov.get("distribution", "poisson") == "poisson":
            count = rng.randint(0, 2 * int(avg))
            # Poisson approximation via numpy-free method
            L = math.exp(-avg)
            k, p = 0, 1.0
            while p > L:
                k += 1
                p *= rng.random()
            count = k - 1

        count = max(min_cov, min(max_cov, count))
        hotspots = rng.sample(range(K), count)
        row = [0] * K
        for h in hotspots:
            row[h] = 1
        matrix.append(row)
    return matrix


def generate_radius(cfg: dict, rng: random.Random) -> list:
    """Generate coverage based on 2D spatial placement."""
    N = cfg["topology"]["num_edge_devices"]
    K = cfg["topology"]["num_hotspots"]
    cov = cfg.get("coverage", {})
    area = cov.get("area_km2", 10.0)
    side = math.sqrt(area)
    range_km = cov.get("hotspot_range_km", 1.0)
    min_cov = cov.get("min_coverage", 1)

    # Place hotspots uniformly
    hotspot_pos = [(rng.uniform(0, side), rng.uniform(0, side)) for _ in range(K)]

    matrix = []
    for _ in range(N):
        # Place ED, ensure at least min_cov hotspots in range
        for _attempt in range(100):
            ex, ey = rng.uniform(0, side), rng.uniform(0, side)
            row = [0] * K
            for j, (hx, hy) in enumerate(hotspot_pos):
                dist = math.sqrt((ex - hx) ** 2 + (ey - hy) ** 2)
                if dist <= range_km:
                    row[j] = 1
            if sum(row) >= min_cov:
                break
        else:
            # Fallback: pick min_cov nearest hotspots
            dists = [(math.sqrt((ex - hx) ** 2 + (ey - hy) ** 2), j)
                     for j, (hx, hy) in enumerate(hotspot_pos)]
            dists.sort()
            row = [0] * K
            for _, j in dists[:min_cov]:
                row[j] = 1
        matrix.append(row)
    return matrix


def generate_explicit(cfg: dict) -> list:
    """Use the matrix directly from the YAML config."""
    return cfg["coverage"]["matrix"]


def generate(cfg: dict, seed: int = 42) -> dict:
    """Main entry point: generate coverage JSON from a config dict."""
    rng = random.Random(seed)

    N = cfg["topology"]["num_edge_devices"]
    K = cfg["topology"]["num_hotspots"]
    M = cfg["topology"]["num_tenants"]

    mode = cfg.get("coverage", {}).get("mode", "probabilistic")
    if mode == "explicit":
        matrix = generate_explicit(cfg)
    elif mode == "radius":
        matrix = generate_radius(cfg, rng)
    else:
        matrix = generate_probabilistic(cfg, rng)

    # Assign tenants round-robin
    device_tenant_map = [i % M for i in range(N)]

    # Generate unique DevAddrs within each tenant's prefix range
    device_devaddr = []
    used = set()
    for i in range(N):
        tenant = device_tenant_map[i]
        for _ in range(1000):
            addr = random_devaddr(tenant, M, rng)
            if addr not in used:
                used.add(addr)
                break
        device_devaddr.append(addr)

    # Stats
    coverages = [sum(row) for row in matrix]
    stats = {
        "avg_coverage": round(sum(coverages) / len(coverages), 2) if coverages else 0,
        "min_coverage": min(coverages) if coverages else 0,
        "max_coverage": max(coverages) if coverages else 0,
    }

    return {
        "num_edge_devices": N,
        "num_hotspots": K,
        "num_tenants": M,
        "coverage_matrix": matrix,
        "device_tenant_map": device_tenant_map,
        "device_devaddr": device_devaddr,
        "stats": stats,
    }


def main():
    parser = argparse.ArgumentParser(description="FeBEx coverage matrix generator")
    parser.add_argument("--config", type=str, required=True,
                        help="Path to YAML scenario config")
    parser.add_argument("--output", type=str, default=None,
                        help="Output JSON file (default: stdout)")
    parser.add_argument("--seed", type=int, default=None,
                        help="Override random seed from config")
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    seed = args.seed if args.seed is not None else cfg.get("experiment", {}).get("seed", 42)
    result = generate(cfg, seed)

    out = json.dumps(result, indent=2)
    if args.output:
        with open(args.output, "w") as f:
            f.write(out + "\n")
        print(f"Coverage written to {args.output} "
              f"(N={result['num_edge_devices']}, K={result['num_hotspots']}, "
              f"avg_cov={result['stats']['avg_coverage']})",
              file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
