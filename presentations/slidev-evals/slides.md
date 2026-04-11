---
theme: default
title: FeBEx Evaluations
layout: cover
class: text-center
transition: slide-left
mdc: true
---

# FeBEx Evaluation Results

Slidev deck for evaluation-only section (E1–E8)

<style>
.eval-grid {
  display: grid;
  grid-template-columns: 42% 58%;
  gap: 18px;
  align-items: start;
}
.plot-wrap {
  position: relative;
  width: 100%;
}
.plot-wrap img {
  width: 100%;
  border-radius: 8px;
}
.takeaway {
  font-size: 0.95rem;
  color: #1d4ed8;
  background: #eff6ff;
  border: 1px solid #93c5fd;
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 10px;
}
.params {
  font-size: 0.68rem;
  color: #6b7280;
  margin-top: 10px;
}
.callout {
  position: absolute;
  font-size: 0.62rem;
  font-weight: 700;
  background: #fff;
  border-radius: 6px;
  padding: 2px 6px;
  border: 2px solid;
}
.c-red { color: #b91c1c; border-color: #ef4444; }
.c-green { color: #166534; border-color: #22c55e; }
.c-blue { color: #1d4ed8; border-color: #3b82f6; }
.c-orange { color: #9a3412; border-color: #f59e0b; }
</style>

---
layout: default
---

# Live Visualization Demo

<div class="takeaway">Main takeaway: We will run the network visualizer during presentation.</div>

<div style="height: 55vh; border: 2px dashed #60a5fa; border-radius: 12px; display: flex; align-items: center; justify-content: center; color: #2563eb; font-size: 1.5rem; font-weight: 700;">
  [Intentionally left blank for live visualizer run]
</div>

<div class="params">Command during talk: <code>python tasks/febex/visualize_network.py</code></div>

---
layout: default
---

# E1: Backhaul Savings vs Duplicate Factor

<div class="eval-grid">
<div>
  <div class="takeaway">Main takeaway: Savings scales strongly with overlap in hotspot coverage.</div>
  <ul>
    <li>Savings rises from <b>25.2%</b> at avg_dup=1 to <b>86.2%</b> at avg_dup=10.</li>
    <li>Trend follows expected behavior as duplicate factor increases.</li>
    <li>FeBEx retains one useful uplink copy in backhaul.</li>
  </ul>
  <div class="params">Params: N=100, K=10, M=2 | uplinks/device=50 | avg_dup sweep 1→10</div>
</div>
<div class="plot-wrap">
  <img src="../../plots/E1_backhaul_savings.png" alt="E1 backhaul savings" />
  <div class="callout c-red" style="left: 8%; top: 83%;">25.2%</div>
  <div class="callout c-green" style="left: 78%; top: 18%;">86.2%</div>
</div>
</div>

---
layout: default
---

# E2: Correctness — Unique Uplink Delivery

<div class="eval-grid">
<div>
  <div class="takeaway">Main takeaway: Unique uplink delivery is perfect in practical overlap ranges.</div>
  <ul>
    <li>Delivery ratio is <b>1.000</b> for avg_dup = 1, 2, 3, 5, 7.</li>
    <li>At stress point avg_dup=10, observed ratio is <b>0.890</b>.</li>
    <li>Short reason: BMv2 egress-buffer overflow can drop first-arriving copies.</li>
  </ul>
  <div class="params">Params: N=100, K=10, M=2 | duplicate-factor sweep as in E1</div>
</div>
<div class="plot-wrap">
  <img src="../../plots/E2_correctness.png" alt="E2 correctness" />
  <div class="callout c-green" style="left: 13%; top: 20%;">1.000</div>
  <div class="callout c-red" style="left: 78%; top: 78%;">0.890</div>
</div>
</div>

---
layout: default
---

# E3 & E7: Verified from Logs (No Plot)

<div class="takeaway">Main takeaway: Both isolation and receipt-accounting checks pass from logs.</div>

- **E3 Multi-tenant isolation:** 0 cross-tenant violations over 5,071 packets (**PASS**).
- **E7 Payment receipt accuracy:** 1,021 cloud receipts = 1,021 forwarded packets; valid gw_id 1,021/1,021; match rate = 1.0.

<div class="params">Verified from logs: results/**/lns*.tsv and cloud_receipts.tsv | Params: E3(N=100,K=10,M=4), E7(N=50,K=5,M=2), dedup ON</div>

---
layout: default
---

# E4: City-Scale Scalability

<div class="eval-grid">
<div>
  <div class="takeaway">Main takeaway: Dedup savings remain stable as city size increases.</div>
  <ul>
    <li>Savings stays around <b>62–67%</b> across tested scales.</li>
    <li>Medium case can exceed theory slightly due to Poisson coverage randomness.</li>
    <li>Throughput rises to <b>1034 pps</b>, then soft-saturates on BMv2 software limits.</li>
  </ul>
  <div class="params">Params: (N,K)=(50,5),(100,10),(200,20),(500,50) | M=4 | avg_cov≈3</div>
</div>
<div class="plot-wrap">
  <img src="../../plots/E4_scalability.png" alt="E4 scalability" />
  <div class="callout c-orange" style="left: 11%; top: 84%;">345 pps</div>
  <div class="callout c-green" style="left: 46%; top: 16%;">1034 pps</div>
  <div class="callout c-blue" style="left: 73%; top: 47%;">62–67%</div>
</div>
</div>

---
layout: default
---

# E5: Dedup State Sizing (Hash Collisions)

<div class="eval-grid">
<div>
  <div class="takeaway">Main takeaway: Undersized dedup state significantly reduces suppression quality.</div>
  <ul>
    <li>At 256 entries, leakage reaches <b>39.0%</b> and savings drops to <b>41.2%</b>.</li>
    <li>At 4,096+ entries, collision effect becomes small and savings stabilizes.</li>
    <li>State sizing directly controls practical dedup effectiveness.</li>
  </ul>
  <div class="params">Params: N=100, K=10, M=2 | register sweep 256→65,536</div>
</div>
<div class="plot-wrap">
  <img src="../../plots/E5_state_sizing.png" alt="E5 state sizing" />
  <div class="callout c-red" style="left: 10%; top: 18%;">39.0% leakage</div>
  <div class="callout c-green" style="left: 72%; top: 82%;">~67% plateau</div>
</div>
</div>

---
layout: default
---

# E6: Epoch Interval Sensitivity

<div class="eval-grid">
<div>
  <div class="takeaway">Main takeaway: Longer epochs improve dedup effectiveness in this workload.</div>
  <ul>
    <li>0.5s epoch gives <b>56.8%</b> savings.</li>
    <li>10–30s epochs reach top observed savings of <b>~67.5%</b>.</li>
    <li>Observed practical operating range: 5–10 seconds.</li>
  </ul>
  <div class="params">Params: N=100, K=10, M=2 | epoch sweep 0.5,1,2,5,10,30 s</div>
</div>
<div class="plot-wrap">
  <img src="../../plots/E6_epoch_sensitivity.png" alt="E6 epoch sensitivity" />
  <div class="callout c-red" style="left: 11%; top: 80%;">56.8%</div>
  <div class="callout c-green" style="left: 80%; top: 18%;">67.5%</div>
</div>
</div>

---
layout: default
---

# E8: Epoch-Boundary Leakage — Variant Comparison

<div style="display:grid;grid-template-columns:36% 64%;gap:16px;align-items:start;">
<div>
  <div class="takeaway">Main takeaway: V2 sliding-window is strongest under boundary stress.</div>
  <ul>
    <li>V2: <b>81.1%</b> savings, 0 leaked duplicates.</li>
    <li>V1/V3: 77.7% / 78.3% due to residual leakage.</li>
    <li>Best variant in this stress profile: <b>V2</b>.</li>
  </ul>
  <div class="params">Stress params: N=100,K=10,M=2 | epoch=1s | inter-arrival=500ms | avg_cov=5.3</div>
</div>
<div class="plot-wrap">
  <img src="../../plots/E8_variant_comparison.png" alt="E8 variant comparison" />
  <div class="callout c-red" style="left: 7%; top: 84%;">V1: 77.7%</div>
  <div class="callout c-green" style="left: 42%; top: 10%;">V2: 81.1%</div>
  <div class="callout c-orange" style="left: 74%; top: 82%;">V3: 78.3%</div>
</div>
</div>

---
layout: center
class: text-center
---

# End of Evaluation Deck

Ready to export to PPTX and merge with the main presentation.
