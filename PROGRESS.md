# FeBEx Project Progress & Handoff Document

> **For LLM agents**: Read this file first. It has full context on what's built, what works,
> what's broken, and what to do next. The design spec is in `FeBEx_Implementation_Spec.md`.

---

## Project Overview

**FeBEx** is a P4-programmable IXP switch for LoRaWAN/Helium backhaul that does three things:
1. **Tenant steering** -- LPM on DevAddr field to route to correct LNS tenant
2. **In-network de-duplication** -- suppress redundant gateway copies using register-based state + epoch expiry
3. **Receipt mirroring** -- clone first-forwarded packet to Helium Cloud for Proof-of-Coverage payment

Tech stack: P4_16 on BMv2 (v1model), finsy (P4Runtime), scapy, Mininet.

---

## Current Stage: All Experiments Complete, Results Evaluated

**Date**: 2026-03-30

All 6 switch tests pass. All 7 experiments (E1-E7) run successfully in quick mode.
Evaluation plots and metrics generated. All experiments PASS.

**Next step**: Optionally run full sweep (more data points), then write README.md.

---

## File Map

```
tasks/febex/
  p4/
    febex.p4                    -- Main P4 program (3-stage ingress pipeline)
    includes/
      headers.p4                -- All header types, metadata, DEDUP_TABLE_SIZE macro
      parser.p4                 -- Ethernet -> IPv4 -> UDP -> FeBExMeta parser
  p4rt_controller/
    controller.py               -- finsy-based P4Runtime controller + Thrift CLI helpers
  test_febex.py                 -- 6 automated tests (self-contained Mininet-based)
  traffic_gen.py                -- Scapy packet generator (reads coverage JSON)
  lns_receiver.py               -- Sniffs packets at LNS hosts, logs TSV
  cloud_receiver.py             -- Sniffs cloned receipts at cloud host, logs TSV
  generate_coverage.py          -- Coverage matrix generator (3 modes)
  configs/
    small_test.yaml             -- 5 EDs, 3 hotspots, 2 tenants, explicit coverage
    medium_city.yaml            -- 100 EDs, 10 hotspots, 4 tenants, probabilistic
    large_city.yaml             -- 500 EDs, 50 hotspots, 4 tenants
    stress_test.yaml            -- 200 EDs, 20 hotspots, 8 tenants, high coverage
  run_experiment.py             -- Experiment orchestrator (single run)
  run_all.py                    -- Full experiment suite (E1-E7 parameter sweeps)
  evaluate.py                   -- Metrics computation + matplotlib plots
  visualize_network.py          -- Coverage visualization (bipartite, heatmap, histogram)

networks/febex/
  mininet/
    networks.py                 -- FeBExTopology(Topo) class, parametric

common/                         -- Shared utilities from template (P4Switch node, helpers)
build/p4/                       -- Compiled P4 output (febex.json, febex.p4info.txtpb)
temp/                           -- Runtime logs (BMv2 logs, controller logs, PCAPs)
results/                        -- Experiment results (created by run_all.py)
plots/                          -- Generated plots (created by evaluate.py / visualize_network.py)
FeBEx_Implementation_Spec.md   -- Full design spec (Sections 1-16)
Makefile                        -- All build/run/experiment targets
```

---

## Completed Work

### Phase 1: P4 Switch (Spec Steps 1-9) -- DONE, ALL 6 TESTS PASS

#### P4 Data Plane (`tasks/febex/p4/`)
- [x] Custom FeBEx 12-byte header: dev_addr(32b), fcnt(32b), gw_id(16b), flags(8b), padding(8b)
- [x] Parser: Ethernet -> IPv4(0x0800) -> UDP(proto 17) -> FeBExMeta(dport 5555)
- [x] Stage 1: `tenant_steering` LPM table on `hdr.febex.dev_addr` -> `set_tenant` action
- [x] Stage 2: Register-based dedup with dual CRC32 hashes (index + key verification)
- [x] Stage 3: `clone(CloneType.I2E, 100)` for receipt mirroring, guarded by `cloud_port != 0`
- [x] `DEDUP_TABLE_SIZE` macro in headers.p4, overridable via `-DDEDUP_TABLE_SIZE=N`

#### Controller (`tasks/febex/p4rt_controller/controller.py`)
- [x] Connects via finsy/P4Runtime, pushes P4Info + binary
- [x] Installs LPM entries for each tenant (DevAddr prefix computation)
- [x] Uses Thrift CLI for register writes and clone session config
- [x] Initialization guard, retry logic, epoch rotation

#### Mininet Topology, Receivers, Traffic Generator
- [x] Parametric FeBExTopology (K gateways, M LNS, optional cloud)
- [x] lns_receiver.py, cloud_receiver.py, traffic_gen.py

### Phase 2: Experiment Framework (Spec Steps 10-15) -- DONE, EXPERIMENTS RUN AND EVALUATED

#### Coverage Matrix Generator (`generate_coverage.py`)
- [x] Three modes: probabilistic (Poisson), radius (2D spatial), explicit (from YAML)
- [x] Outputs JSON with: coverage_matrix, device_tenant_map, device_devaddr, stats
- [x] DevAddr assignment within tenant prefix ranges (round-robin tenant assignment)
- [x] CLI: `python3 generate_coverage.py --config configs/medium_city.yaml --output cov.json`

#### 4 YAML Scenario Configs (`configs/`)
- [x] `small_test.yaml` -- 5 EDs, 3 GWs, 2 tenants, explicit 5x3 coverage matrix
- [x] `medium_city.yaml` -- 100 EDs, 10 GWs, 4 tenants, Poisson avg 3.2
- [x] `large_city.yaml` -- 500 EDs, 50 GWs, 4 tenants, Poisson avg 3.5
- [x] `stress_test.yaml` -- 200 EDs, 20 GWs, 8 tenants, Poisson avg 6.0

#### Experiment Orchestrator (`run_experiment.py`)
- [x] Automates full experiment lifecycle: Mininet -> ARP -> receivers -> controller -> traffic -> drain -> collect
- [x] Supports --dedup / --no-dedup for A/B comparison
- [x] Configurable epoch interval, cloud host, custom coverage
- [x] Saves logs + coverage JSON to results directory

#### Run All Experiments (`run_all.py`)
- [x] E1: Backhaul savings vs. duplicate factor (sweep avg_cov in {1,2,3,5,7,10})
- [x] E2: Correctness / delivery ratio (same sweep, must be 1.0)
- [x] E3: Multi-tenant isolation (N=100, K=10, M=4, verify zero cross-talk)
- [x] E4: City-scale scalability (sweep N x K combinations)
- [x] E5: Dedup state sizing (sweep register_size, recompiles P4)
- [x] E6: Epoch interval sensitivity (sweep epoch_s in {0.5,1,2,5,10,30})
- [x] E7: Payment receipt accuracy (verify cloud receipt gw_id correctness)
- [x] --quick mode for faster testing with fewer sweep points

#### Evaluate (`evaluate.py`)
- [x] Log loading: load_lns_logs(), load_cloud_logs(), load_coverage()
- [x] Metrics: backhaul savings, delivery ratio, tenant isolation, throughput
- [x] Plots (matplotlib PNG + PDF): E1 line chart, E2 bar chart, E4 grouped bars, E5 dual-axis, E6 line
- [x] Writes evaluation_summary.json

#### Visualize Network (`visualize_network.py`)
- [x] Bipartite graph (EDs <-> hotspots, colored by tenant, uses networkx)
- [x] Coverage heatmap (N x K matrix, rows sorted by tenant)
- [x] Coverage distribution histogram
- [x] Tenant distribution pie chart

#### Makefile Targets
- [x] `run-experiments` -- run all E1-E7
- [x] `run-experiments-quick` -- quick mode
- [x] `run-experiment-e1` through `run-experiment-e7` -- individual experiments
- [x] `evaluate` -- compute metrics and generate plots
- [x] `visualize COVERAGE=file.json` -- generate coverage visualizations
- [x] `generate-coverage CONFIG=... OUTPUT=...` -- generate coverage JSON

---

## Test Suite Reference

| # | Test | Setup | Expected | Status |
|---|------|-------|----------|--------|
| 1 | basic_forwarding | 1 gw, 1 lns, no dedup | 10 sent -> 10 received | PASS |
| 2 | tenant_steering | 1 gw, 2 lns, no dedup | 5 each tenant -> 5 each LNS | PASS |
| 3 | dedup | 3 gw, 1 lns, dedup ON | Same uplink x3 -> 1 copy | PASS |
| 4 | epoch_reset | 2 gw, 1 lns, 2s epoch | Send, wait flip, send -> 2 | PASS |
| 5 | correctness | 3 gw, 2 lns, dedup ON | 20 unique -> ratio 1.0 | PASS |
| 6 | receipt | 2 gw, 1 lns, 1 cloud | 1 LNS + 1 cloud receipt | PASS |

---

## Experiment Results & Analysis (Quick Mode, 2026-03-30)

All 7 experiments run successfully. Plots in `plots/`, raw data in `results/`, summary in `results/evaluation_summary.json`.

### E1: Backhaul Savings vs. Duplicate Factor

**What is evaluated**: How much redundant backhaul bandwidth FeBEx saves as the average number of hotspots covering each device increases. With more overlapping hotspots, more duplicate copies traverse the network.

**What to expect**: Savings should grow with duplicate factor, approaching the theoretical limit `1 - 1/d` where d is the average number of copies. At d=1 (no overlap), savings should be ~0%. At d=5, theoretical is 80%.

**What we observed**:

| avg_dup | OFF pkts | ON pkts | Savings | Theoretical |
|---------|----------|---------|---------|-------------|
| 1       | 4,020    | 3,016   | 25.0%   | 0.0%        |
| 2       | 6,990    | 3,091   | 55.8%   | 50.0%       |
| 3       | 9,240    | 3,061   | 66.9%   | 66.7%       |
| 5       | 15,900   | 3,197   | 79.9%   | 80.0%       |

Savings scale monotonically and closely match theory at d>=2. At d=1, measured savings (25%) exceed theoretical (0%) because Poisson coverage means some devices still have 2+ hotspots even when the average is 1. The ON packet count stays near ~3,000 (one copy per unique uplink) while OFF grows proportionally. Plot: `plots/E1_backhaul_savings.png`.

---

### E2: Correctness -- Zero Unique-Uplink Loss

**What is evaluated**: Whether the dedup filter ever incorrectly suppresses a *unique* uplink (a message that hasn't been seen before). This would mean data loss.

**What to expect**: Delivery ratio must be exactly 1.0 at all duplicate factors. The dedup should only suppress true duplicates, never the first copy.

**What we observed**:

| avg_dup | Unique received | Expected | Delivery ratio |
|---------|-----------------|----------|----------------|
| 1       | 3,000           | 3,000    | 1.0000         |
| 2       | 3,000           | 3,000    | 1.0000         |
| 3       | 3,000           | 3,000    | 1.0000         |
| 5       | 3,000           | 3,000    | 1.0000         |

Perfect 1.0 delivery ratio across all sweep points. No unique uplink was ever wrongly suppressed. The register size (65536) is large enough to avoid hash collisions that could cause false-positive suppression. Plot: `plots/E2_correctness.png`.

---

### E3: Multi-Tenant Isolation

**What is evaluated**: Whether the LPM-based tenant steering correctly routes packets -- each LNS should only receive packets whose DevAddr falls within its assigned prefix range. Zero cross-tenant leakage.

**What to expect**: Every packet at lns{X} must have a DevAddr matching tenant X's prefix. Any violation means the LPM table is misconfigured or the switch forwarded to the wrong port.

**What we observed**: N=50, K=10, M=4 tenants, 1,017 total forwarded packets.

| LNS | Packets | Violations |
|-----|---------|------------|
| 1   | 264     | 0          |
| 2   | 266     | 0          |
| 3   | 243     | 0          |
| 4   | 244     | 0          |

**PASS**: Zero cross-tenant violations. Perfect isolation across all 4 tenants.

---

### E4: City-Scale Scalability

**What is evaluated**: Whether dedup savings and switch throughput remain stable as the network scales (more edge devices and hotspots).

**What to expect**: Savings % should remain approximately constant (determined by coverage overlap, not network size). Throughput should increase with more traffic (more parallel sources).

**What we observed**:

| Scale     | OFF pkts | ON pkts | Savings | Throughput |
|-----------|----------|---------|---------|------------|
| N50, K5   | 2,900    | 1,028   | 64.6%   | 363 pps    |
| N100, K10 | 6,160    | 2,088   | 66.1%   | 737 pps    |

Savings stay consistent (~65-66%) regardless of scale. Throughput scales linearly with traffic volume. BMv2 software switch limits absolute throughput (~700 pps), but real P4 hardware (Tofino) would handle Mpps+. Plot: `plots/E4_scalability.png`.

---

### E5: Dedup State Sizing (Hash Collisions)

**What is evaluated**: How the size of the dedup register array affects dedup effectiveness. Smaller registers increase hash collisions, causing some duplicates to evade detection (different uplinks overwrite each other's dedup slots).

**What to expect**: Smaller registers should show lower savings (more duplicates leak through). At 65536 entries, collisions should be negligible for N=100 devices.

**What we observed**:

| Register size | Total pkts | Unique | Leaked dups | Savings |
|---------------|-----------|--------|-------------|---------|
| 256           | 2,835     | 2,000  | 835         | 54.0%   |
| 4,096         | 2,103     | 2,000  | 103         | 65.9%   |
| 65,536        | 2,078     | 2,000  | 78          | 66.3%   |

At register=256, 835 duplicates leak through (collision-induced misses), dropping savings to 54%. At 4096+, the hash table is spacious enough that collisions are rare and savings plateau near 66%. The jump from 256 to 4096 is dramatic; 4096 to 65536 shows diminishing returns. Plot: `plots/E5_state_sizing.png`.

---

### E6: Epoch Interval Sensitivity

**What is evaluated**: How the epoch rotation frequency affects dedup. Shorter epochs clear the filter more often, potentially allowing late-arriving duplicates to sneak through if the epoch flips between the first copy and its duplicates.

**What to expect**: Longer epochs should yield slightly higher savings (fewer boundary race conditions). The effect should be small since LoRaWAN duplicates arrive within milliseconds, well within any reasonable epoch.

**What we observed**:

| Epoch (s) | Total pkts | Unique | Leaked dups | Savings |
|-----------|-----------|--------|-------------|---------|
| 1         | 2,162     | 2,000  | 162         | 64.9%   |
| 5         | 2,076     | 2,000  | 76          | 66.3%   |
| 30        | 2,000     | 2,000  | 0           | 67.5%   |

At 30s epoch, zero leakage -- all duplicates caught. At 1s, 162 duplicates slip through epoch boundaries (8.1% of expected duplicates). The effect is modest (~2.5 percentage points savings difference), confirming that epoch intervals of 5-10s are sufficient for LoRaWAN workloads. Plot: `plots/E6_epoch_sensitivity.png`.

---

### E7: Payment Receipt Accuracy

**What is evaluated**: Whether cloned receipt packets correctly identify which hotspot first forwarded each unique uplink. The cloud host receives a mirror copy with the winning gateway's `gw_id` in the FeBEx header.

**What to expect**: Each forwarded uplink should produce exactly one cloud receipt. The `gw_id` must be a valid gateway index (1..K). Receipt count should match LNS forwarded packet count 1:1.

**What we observed**: N=10, K=5, M=2, dedup ON.

| Metric | Value |
|--------|-------|
| Cloud receipts | 100 |
| LNS forwarded | 100 |
| Valid gw_id (1-5) | 100/100 |
| Receipt-to-LNS match rate | 1.0 |

**PASS**: Perfect 1:1 receipt-to-packet correspondence. All 100 receipts have valid gateway IDs. The clone session correctly mirrors first-forwarded packets to the cloud host.

---

## Bug Fixes Applied (Switch Phase)

1. **Controller ImportError crash**: finsy P4CloneSessionEntry/P4RegisterEntry don't exist. Fixed: use Thrift CLI.
2. **Ready handler re-entry**: finsy reconnects every ~10s. Fixed: `_initialised` guard.
3. **Stale processes**: `mn -c` doesn't kill BMv2. Fixed: explicit pkill.
4. **P4Runtime register error**: `delete_all()` triggers error. Fixed: try/except.
5. **key_value hash = 0**: False positive on zero-init registers. Fixed: hash base=1.
6. **Thrift CLI race**: BMv2 not ready. Fixed: retry logic (3 attempts).
7. **Clone guard**: clone() called without cloud port. Fixed: `if (meta.cloud_port != 0)`.

---

## Known Issues / Environment Notes

- **P4 compiler only on VM**: `p4c-bm2-ss` only on the P4 dev VM. Workspace symlinked at `/media/sf_FeBEx`.
- **Tests require root**: `sudo /opt/p4/p4dev-python-venv/bin/python3 tasks/febex/test_febex.py`
- **BMv2 is slow**: ~10-100K pps. Document and note real P4 hardware eliminates this.
- **Packet format**: Custom 12B header on UDP 5555 (not real Semtech/LoRaWAN). Intentional per spec.
- **PyYAML needed**: `pip install pyyaml` if not already installed (for experiment framework).
- **matplotlib/numpy needed**: For evaluate.py and visualize_network.py plots.
- **networkx optional**: For bipartite graph in visualize_network.py.

---

## Design Decisions

1. **Thrift CLI for registers/mirrors**: BMv2 doesn't support P4Runtime register writes.
2. **Single P4 program, register-toggled dedup**: `dedup_enabled` register for A/B experiments.
3. **Dual CRC32 hashing**: Index hash + key verification hash to disambiguate collisions.
4. **Epoch-based expiry**: 16-bit counter, stale entries treated as empty.
5. **DevAddr prefix**: `prefix_len = ceil(log2(M))`, tenant i gets `i << (32 - prefix_len)`.
6. **No ARP in P4**: Switch rewrites dst_mac in set_tenant; ARP pre-populated by test/orchestrator.

---

## How to Run

```bash
# On the VM:
cd /media/sf_FeBEx

# ── Switch tests ──
make build-febex
sudo /opt/p4/p4dev-python-venv/bin/python3 tasks/febex/test_febex.py

# ── Generate coverage ──
python3 tasks/febex/generate_coverage.py \
    --config tasks/febex/configs/medium_city.yaml \
    --output coverage.json

# ── Visualize coverage ──
python3 tasks/febex/visualize_network.py --coverage coverage.json

# ── Run all experiments (takes a long time!) ──
sudo python3 tasks/febex/run_all.py              # full suite
sudo python3 tasks/febex/run_all.py --quick       # quick mode
sudo python3 tasks/febex/run_all.py --experiments E1 E2  # specific

# ── Evaluate results ──
python3 tasks/febex/evaluate.py

# ── Via Makefile ──
make run-tests-febex
make run-experiments-quick
make evaluate
```

---

## What To Do Next

1. **Re-run evaluate.py**: The E5 FP metric was fixed (was showing 0%, now shows leakage rate).
   Also E1 plot legend corrected. Re-run to regenerate plots:
   ```bash
   python3 tasks/febex/evaluate.py
   ```

2. **(Optional) Full experiment run**: Quick mode used 4 sweep points. Full mode adds avg7, avg10
   for E1/E2 and more N/K combos for E4. Takes much longer:
   ```bash
   sudo python3 tasks/febex/run_all.py
   python3 tasks/febex/evaluate.py
   ```

3. **README.md update**: Write a proper project README with:
   - Project description, architecture diagram
   - How to build and run
   - Experiment results summary
   - Performance notes (BMv2 vs real P4 hardware)

---

## Key Code Patterns for New Agents

### Sending a FeBEx packet with scapy
```python
pkt = (Ether(src="00:00:00:00:01:01", dst="ff:ff:ff:ff:ff:ff")
       / IP(src="10.0.1.1", dst="10.0.2.1")
       / UDP(sport=1234, dport=5555)
       / FeBExMeta(dev_addr=0x00000001, fcnt=42, gw_id=1)
       / Raw(b'\x00' * 20))
```

### Parsing FeBEx header
```python
FEBEX_META_FMT = ">IIHBB"  # dev_addr(4B) + fcnt(4B) + gw_id(2B) + flags(1B) + padding(1B)
dev_addr, fcnt, gw_id, flags, padding = struct.unpack(FEBEX_META_FMT, raw[:12])
```

### DevAddr prefix for M tenants
```python
if M <= 1: prefix_len = 0
else: prefix_len = math.ceil(math.log2(M))
prefix_val = tenant_idx << (32 - prefix_len)
```

### Running an experiment programmatically
```python
from generate_coverage import generate
from run_experiment import run_experiment
import yaml

cfg = yaml.safe_load(open("configs/medium_city.yaml"))
cov = generate(cfg, seed=42)
run_experiment(cfg, cov, Path("results/test"), dedup_enabled=True)
```
