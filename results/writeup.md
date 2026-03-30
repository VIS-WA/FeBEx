# FeBEx Experiment Results & Analysis

> Quick mode run, 2026-03-30. Plots in `plots/`, raw data in `results/`, summary in `results/evaluation_summary.json`.

---

## E1: Backhaul Savings vs. Duplicate Factor

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

## E2: Correctness -- Zero Unique-Uplink Loss

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

## E3: Multi-Tenant Isolation

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

## E4: City-Scale Scalability

**What is evaluated**: Whether dedup savings and switch throughput remain stable as the network scales (more edge devices and hotspots).

**What to expect**: Savings % should remain approximately constant (determined by coverage overlap, not network size). Throughput should increase with more traffic (more parallel sources).

**What we observed**:

| Scale     | OFF pkts | ON pkts | Savings | Throughput |
|-----------|----------|---------|---------|------------|
| N50, K5   | 2,900    | 1,028   | 64.6%   | 363 pps    |
| N100, K10 | 6,160    | 2,088   | 66.1%   | 737 pps    |

Savings stay consistent (~65-66%) regardless of scale. Throughput scales linearly with traffic volume. BMv2 software switch limits absolute throughput (~700 pps), but real P4 hardware (Tofino) would handle Mpps+. Plot: `plots/E4_scalability.png`.

---

## E5: Dedup State Sizing (Hash Collisions)

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

## E6: Epoch Interval Sensitivity

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

## E7: Payment Receipt Accuracy

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
