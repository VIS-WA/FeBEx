#!/usr/bin/env python3
"""
FeBEx Network Visualization
=============================
Generates plots from coverage matrix JSON files:
  1. Bipartite graph: EDs (left) <-> Hotspots (right)
  2. Coverage heatmap: N x K matrix (rows sorted by tenant)
  3. Coverage distribution histogram: how many EDs covered by 1..K hotspots
  4. Tenant distribution pie chart

Usage:
    python3 visualize_network.py --coverage coverage.json
    python3 visualize_network.py --coverage coverage.json --output-dir plots/
"""

import argparse
import json
import math
import sys
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib.colors import ListedColormap
    import numpy as np
except ImportError:
    print("ERROR: matplotlib and numpy required. Install with:", file=sys.stderr)
    print("  pip install matplotlib numpy", file=sys.stderr)
    sys.exit(1)

try:
    import networkx as nx
    HAS_NX = True
except ImportError:
    HAS_NX = False

# Tenant color palette
TENANT_COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
    "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
    "#bcbd22", "#17becf", "#aec7e8", "#ffbb78",
]


def load_coverage(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def save_fig(fig, output_dir: Path, name: str):
    output_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_dir / f"{name}.png", dpi=150, bbox_inches="tight")
    fig.savefig(output_dir / f"{name}.pdf", bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {name}.png/pdf")


# ═══════════════════════════════════════════════════════════════════════
#  Plot 1: Bipartite graph
# ═══════════════════════════════════════════════════════════════════════

def plot_bipartite(cov: dict, output_dir: Path):
    """Bipartite graph: EDs on left, hotspots on right, edges = coverage."""
    if not HAS_NX:
        print("  Skipping bipartite graph (networkx not installed)")
        return

    N = cov["num_edge_devices"]
    K = cov["num_hotspots"]
    M = cov.get("num_tenants", 1)
    matrix = cov["coverage_matrix"]
    tenant_map = cov.get("device_tenant_map", [0] * N)

    # Limit nodes for readability
    max_eds = min(N, 50)
    max_gws = min(K, 20)

    G = nx.Graph()
    ed_nodes = [f"ED{i}" for i in range(max_eds)]
    gw_nodes = [f"GW{j}" for j in range(max_gws)]

    for n in ed_nodes:
        G.add_node(n, bipartite=0)
    for n in gw_nodes:
        G.add_node(n, bipartite=1)

    for i in range(max_eds):
        for j in range(max_gws):
            if j < len(matrix[i]) and matrix[i][j] == 1:
                G.add_edge(f"ED{i}", f"GW{j}")

    # Layout
    pos = {}
    for idx, n in enumerate(ed_nodes):
        pos[n] = (0, -idx * 2)
    for idx, n in enumerate(gw_nodes):
        pos[n] = (4, -idx * (2 * max_eds / max(max_gws, 1)))

    fig, ax = plt.subplots(figsize=(10, max(6, max_eds * 0.3)))

    # Color EDs by tenant
    ed_colors = [TENANT_COLORS[tenant_map[i] % len(TENANT_COLORS)]
                 for i in range(max_eds)]

    nx.draw_networkx_nodes(G, pos, nodelist=ed_nodes, node_color=ed_colors,
                           node_size=80, ax=ax)
    nx.draw_networkx_nodes(G, pos, nodelist=gw_nodes, node_color="lightgray",
                           node_size=150, node_shape="s", ax=ax)
    nx.draw_networkx_edges(G, pos, alpha=0.2, ax=ax)

    # Legend
    handles = [mpatches.Patch(color=TENANT_COLORS[t % len(TENANT_COLORS)],
                              label=f"Tenant {t}") for t in range(M)]
    handles.append(mpatches.Patch(color="lightgray", label="Hotspot"))
    ax.legend(handles=handles, loc="upper right", fontsize=8)

    ax.set_title(f"Coverage Bipartite Graph (N={N}, K={K})"
                 + (f" [showing {max_eds}/{N} EDs]" if max_eds < N else ""))
    ax.axis("off")
    save_fig(fig, output_dir, "bipartite_graph")


# ═══════════════════════════════════════════════════════════════════════
#  Plot 2: Coverage heatmap
# ═══════════════════════════════════════════════════════════════════════

def plot_heatmap(cov: dict, output_dir: Path):
    """Coverage heatmap: N x K matrix, rows sorted by tenant."""
    N = cov["num_edge_devices"]
    K = cov["num_hotspots"]
    M = cov.get("num_tenants", 1)
    matrix = np.array(cov["coverage_matrix"])
    tenant_map = cov.get("device_tenant_map", [0] * N)

    # Sort rows by tenant
    sorted_indices = sorted(range(N), key=lambda i: tenant_map[i])
    sorted_matrix = matrix[sorted_indices]
    sorted_tenants = [tenant_map[i] for i in sorted_indices]

    # Limit display for very large matrices
    max_rows = min(N, 200)
    if N > max_rows:
        step = N // max_rows
        display_indices = list(range(0, N, step))[:max_rows]
        sorted_matrix = sorted_matrix[display_indices]

    fig, ax = plt.subplots(figsize=(max(6, K * 0.4), max(4, max_rows * 0.05)))
    cmap = ListedColormap(["white", "steelblue"])
    ax.imshow(sorted_matrix, aspect="auto", cmap=cmap, interpolation="nearest")

    # Tenant separators
    boundaries = []
    for t in range(M - 1):
        count = sum(1 for x in sorted_tenants[:max_rows] if x <= t)
        if count > 0:
            boundaries.append(count - 0.5)
    for b in boundaries:
        ax.axhline(y=b, color="red", linewidth=1, linestyle="--", alpha=0.7)

    ax.set_xlabel("Hotspot index")
    ax.set_ylabel("Edge device (sorted by tenant)")
    ax.set_title(f"Coverage Heatmap (N={N}, K={K}, M={M})")
    save_fig(fig, output_dir, "coverage_heatmap")


# ═══════════════════════════════════════════════════════════════════════
#  Plot 3: Coverage distribution histogram
# ═══════════════════════════════════════════════════════════════════════

def plot_coverage_distribution(cov: dict, output_dir: Path):
    """Histogram: how many EDs are covered by 1, 2, ... K hotspots."""
    matrix = cov["coverage_matrix"]
    coverages = [sum(row) for row in matrix]

    fig, ax = plt.subplots(figsize=(8, 5))
    max_cov = max(coverages) if coverages else 1
    bins = range(0, max_cov + 2)
    ax.hist(coverages, bins=bins, color="steelblue", edgecolor="black", alpha=0.8,
            align="left")
    ax.set_xlabel("Number of covering hotspots")
    ax.set_ylabel("Number of edge devices")
    ax.set_title(f"Coverage Distribution (avg={sum(coverages)/len(coverages):.2f})")
    ax.axvline(x=sum(coverages)/len(coverages), color="red", linestyle="--",
               alpha=0.7, label=f"Mean={sum(coverages)/len(coverages):.2f}")
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")
    save_fig(fig, output_dir, "coverage_distribution")


# ═══════════════════════════════════════════════════════════════════════
#  Plot 4: Tenant distribution pie chart
# ═══════════════════════════════════════════════════════════════════════

def plot_tenant_distribution(cov: dict, output_dir: Path):
    """Pie chart showing ED distribution across tenants."""
    M = cov.get("num_tenants", 1)
    tenant_map = cov.get("device_tenant_map", [0] * cov["num_edge_devices"])

    counts = [0] * M
    for t in tenant_map:
        if t < M:
            counts[t] += 1

    fig, ax = plt.subplots(figsize=(6, 6))
    colors = [TENANT_COLORS[i % len(TENANT_COLORS)] for i in range(M)]
    labels = [f"Tenant {i}\n({counts[i]} EDs)" for i in range(M)]
    ax.pie(counts, labels=labels, colors=colors, autopct="%1.0f%%",
           startangle=90, pctdistance=0.85)
    ax.set_title(f"Tenant Distribution (M={M}, N={sum(counts)})")
    save_fig(fig, output_dir, "tenant_distribution")


# ═══════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="FeBEx network visualization")
    parser.add_argument("--coverage", type=str, required=True,
                        help="Path to coverage JSON file")
    parser.add_argument("--output-dir", type=str, default="plots",
                        help="Directory for output plots (default: plots/)")
    args = parser.parse_args()

    cov = load_coverage(args.coverage)
    output_dir = Path(args.output_dir)

    N = cov["num_edge_devices"]
    K = cov["num_hotspots"]
    M = cov.get("num_tenants", 1)
    stats = cov.get("stats", {})

    print(f"\nFeBEx Network Visualization")
    print(f"  N={N} edge devices, K={K} hotspots, M={M} tenants")
    print(f"  Coverage: avg={stats.get('avg_coverage', '?')}, "
          f"min={stats.get('min_coverage', '?')}, "
          f"max={stats.get('max_coverage', '?')}")
    print()

    plot_bipartite(cov, output_dir)
    plot_heatmap(cov, output_dir)
    plot_coverage_distribution(cov, output_dir)
    plot_tenant_distribution(cov, output_dir)

    print(f"\n  All plots saved to {output_dir}/")


if __name__ == "__main__":
    main()
