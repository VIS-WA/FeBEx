#!/usr/bin/env python3
"""
FeBEx Singapore Interactive Visualization
==========================================
Generates a self-contained HTML showing the FeBEx IXP switch in action
on a real Singapore city map.

Features:
  - 4 configs: Small / Medium / Large / Singapore (realistic density)
  - Animated LoRaWAN uplink events: sensor → hotspots → P4 switch → LNS
  - In-network deduplication shown in real-time
  - Receipt mirroring to Helium cloud
  - Live savings counter

Usage:
    python3 tasks/febex/visualize_singapore.py
    python3 tasks/febex/visualize_singapore.py --output plots/singapore_map.html

Requirements: none (HTML uses Leaflet.js CDN — internet needed to view)
"""

import json
import math
import random
import argparse
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent.parent

# ── Singapore geography ───────────────────────────────────────────────────────

# Sensor density regions: (lat, lon, lat_std, lon_std, weight, name)
DENSITY_REGIONS = [
    (1.2793, 103.8478, 0.010, 0.012, 4.0, "CBD"),
    (1.3048, 103.8318, 0.007, 0.009, 3.0, "Orchard"),
    (1.2834, 103.8607, 0.007, 0.009, 2.5, "Marina Bay"),
    (1.3200, 103.8635, 0.009, 0.011, 1.8, "Geylang"),
    (1.3333, 103.7436, 0.013, 0.016, 1.5, "Jurong East"),
    (1.3644, 103.9915, 0.016, 0.018, 2.0, "Changi Airport"),
    (1.4370, 103.7867, 0.013, 0.016, 1.0, "Woodlands"),
    (1.3496, 103.9568, 0.013, 0.013, 1.5, "Tampines"),
    (1.4043, 103.9022, 0.013, 0.013, 1.0, "Punggol"),
    (1.3800, 103.8450, 0.011, 0.011, 1.2, "Ang Mo Kio"),
    (1.2942, 103.7861, 0.009, 0.009, 1.0, "Queenstown"),
    (1.3700, 103.7500, 0.013, 0.013, 0.8, "Choa Chu Kang"),
]

# Strategic LoRaWAN gateway locations across Singapore (use first K)
GATEWAY_POSITIONS = [
    [1.2793, 103.8478],  # 0  CBD
    [1.2834, 103.8607],  # 1  Marina Bay
    [1.3048, 103.8318],  # 2  Orchard
    [1.3644, 103.9915],  # 3  Changi Airport
    [1.3333, 103.7436],  # 4  Jurong East
    [1.4370, 103.7867],  # 5  Woodlands
    [1.3496, 103.9568],  # 6  Tampines
    [1.4043, 103.9022],  # 7  Punggol
    [1.3800, 103.8450],  # 8  Ang Mo Kio
    [1.2942, 103.7861],  # 9  Queenstown
    [1.3200, 103.8635],  # 10 Geylang/Paya Lebar
    [1.3700, 103.7500],  # 11 Choa Chu Kang
    [1.3150, 103.8900],  # 12 Paya Lebar
    [1.3400, 103.7200],  # 13 Jurong West
    [1.4200, 103.8380],  # 14 Yishun
    [1.3300, 103.9300],  # 15 Bedok
    [1.3600, 103.8200],  # 16 Bishan
    [1.2600, 103.8200],  # 17 Harbourfront
    [1.3050, 103.9000],  # 18 Katong
    [1.3560, 103.9820],  # 19 Changi T3
]

# FeBEx IXP switch at Equinix SG1, one-north / Ayer Rajah
SWITCH_POS = [1.2980, 103.7890]

# LNS servers per tenant
LNS_POSITIONS = [
    [1.2590, 103.8200],  # Tenant 0 — Mapletree / Helium
    [1.2650, 103.8950],  # Tenant 1 — Changi Business Park / TTN
    [1.2800, 103.7650],  # Tenant 2 — JTC LaunchPad / AWS IoT
    [1.2700, 103.9050],  # Tenant 3 — East / Actility
]

CLOUD_POS = [1.2380, 103.8300]  # Helium cloud (south of island)

TENANT_COLORS = ["#38bdf8", "#fbbf24", "#4ade80", "#f87171"]
TENANT_NAMES  = ["Helium", "TTN", "AWS IoT", "Actility"]


# ── Position generation ───────────────────────────────────────────────────────

def haversine_km(a, b):
    R = 6371.0
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(h))


def sample_sensors(n, seed=42):
    rng = random.Random(seed)
    weights = [r[4] for r in DENSITY_REGIONS]
    total = sum(weights)
    sensors = []
    for _ in range(n):
        rv = rng.random() * total
        cumul = 0.0
        region = DENSITY_REGIONS[-1]
        for reg in DENSITY_REGIONS:
            cumul += reg[4]
            if rv <= cumul:
                region = reg
                break
        lat = max(1.15, min(1.47, rng.gauss(region[0], region[2])))
        lon = max(103.60, min(104.05, rng.gauss(region[1], region[3])))
        sensors.append([lat, lon])
    return sensors


def assign_tenants(sensors, n_tenants):
    """Assign tenants geographically by longitude band."""
    lon_min = min(s[1] for s in sensors)
    lon_max = max(s[1] for s in sensors)
    span = lon_max - lon_min + 1e-9
    return [min(int((s[1] - lon_min) / span * n_tenants), n_tenants - 1) for s in sensors]


def build_coverage(sensors, hotspots, radius_km=3.5, seed=42):
    """Distance-based coverage with a randomised radius per sensor."""
    rng = random.Random(seed)
    coverage = []
    for s in sensors:
        dists = [(haversine_km(s, h), j) for j, h in enumerate(hotspots)]
        dists.sort()
        row = [0] * len(hotspots)
        row[dists[0][1]] = 1                          # nearest always covers
        r = radius_km * (0.7 + rng.random() * 0.6)  # slight variation
        for d, j in dists[1:]:
            if d <= r:
                row[j] = 1
        coverage.append(row)
    return coverage


def make_config(label, n, k, m, seed=42, radius_km=3.5):
    sensors_pos = sample_sensors(n, seed=seed)
    tenants = assign_tenants(sensors_pos, m)
    hotspots_pos = GATEWAY_POSITIONS[:k]
    coverage = build_coverage(sensors_pos, hotspots_pos, radius_km=radius_km, seed=seed)
    avg_cov = sum(sum(r) for r in coverage) / max(len(coverage), 1)
    sensors  = [{"lat": round(s[0], 5), "lon": round(s[1], 5), "tenant": t}
                for s, t in zip(sensors_pos, tenants)]
    hotspots = [{"lat": round(h[0], 5), "lon": round(h[1], 5)} for h in hotspots_pos]
    lns      = [{"lat": LNS_POSITIONS[i][0], "lon": LNS_POSITIONS[i][1], "tenant": i}
                for i in range(m)]
    return {
        "label": label,
        "sensors": sensors,
        "hotspots": hotspots,
        "coverage": coverage,
        "switch": {"lat": SWITCH_POS[0], "lon": SWITCH_POS[1]},
        "lns": lns,
        "cloud": {"lat": CLOUD_POS[0], "lon": CLOUD_POS[1]},
        "stats": {"N": n, "K": k, "M": m, "avg_coverage": round(avg_cov, 2)},
    }


def build_all_configs():
    specs = [
        # radius_km is LoRaWAN effective range: larger for fewer hotspots (less competition)
        # Singapore island is ~50×27 km; 20 hotspots → ~8 km avg spacing → 10 km radius needed
        ("small",     "Small",      30,  5, 2, 42, 12.0),
        ("medium",    "Medium",    100, 10, 4, 42,  9.0),
        ("large",     "Large",     500, 20, 4, 42,  7.0),
        ("singapore", "Singapore", 150, 20, 4, 99,  8.0),
    ]
    configs = {}
    for key, label, n, k, m, seed, radius in specs:
        print(f"  Building {label} config (N={n}, K={k}, M={m})...")
        configs[key] = make_config(label, n, k, m, seed=seed, radius_km=radius)
    return configs


# ── HTML template ─────────────────────────────────────────────────────────────

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FeBEx — Singapore LoRaWAN IXP</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;display:flex;height:100vh;background:#111827}

/* ── Sidebar ── */
#sidebar{
  width:220px;min-width:220px;background:#111827;color:#e2e8f0;
  display:flex;flex-direction:column;padding:18px 14px;
  border-right:1px solid #1f2937;overflow-y:auto;
}
.logo{font-size:1.15rem;font-weight:800;color:#60a5fa;letter-spacing:-0.02em}
.logo span{color:#34d399}
.tagline{font-size:0.68rem;color:#6b7280;margin-top:3px;line-height:1.5;margin-bottom:18px}
.sec{font-size:0.6rem;text-transform:uppercase;letter-spacing:.1em;color:#374151;
     margin-top:14px;margin-bottom:7px}

/* Config buttons */
.cfg-btn{
  display:block;width:100%;padding:7px 10px;margin-bottom:5px;
  border:1px solid #1f2937;border-radius:6px;background:#1f2937;
  color:#9ca3af;cursor:pointer;text-align:left;font-size:0.8rem;
  transition:all .18s;
}
.cfg-btn:hover{background:#374151;color:#e5e7eb}
.cfg-btn.active{background:#1e3a5f;border-color:#3b82f6;color:#93c5fd}
.cfg-meta{font-size:0.65rem;color:#6b7280;display:block;margin-top:1px}
.cfg-btn.active .cfg-meta{color:#60a5fa}

/* Play/speed */
#play-btn{
  width:100%;padding:9px;margin-top:2px;border:none;border-radius:6px;
  background:#065f46;color:#6ee7b7;cursor:pointer;
  font-size:0.85rem;font-weight:700;transition:background .2s;
}
#play-btn:hover{background:#047857}
#play-btn.playing{background:#7f1d1d;color:#fca5a5}
.speed-row{display:flex;gap:4px;margin-top:7px}
.spd{
  flex:1;padding:4px 2px;border:1px solid #1f2937;border-radius:4px;
  background:#1f2937;color:#9ca3af;cursor:pointer;font-size:0.68rem;
  text-align:center;transition:all .15s;
}
.spd:hover{background:#374151}
.spd.active{background:#312e81;border-color:#818cf8;color:#c7d2fe}

/* Legend */
.leg{display:flex;align-items:center;gap:7px;font-size:0.73rem;color:#9ca3af;margin-bottom:4px}
.leg-dot{width:9px;height:9px;border-radius:50%;flex-shrink:0}
.leg-ico{width:13px;height:13px;flex-shrink:0;font-size:11px;
         display:flex;align-items:center;justify-content:center}

/* Stats */
.stat{display:flex;justify-content:space-between;font-size:0.73rem;color:#9ca3af;margin-bottom:5px}
.sv{color:#34d399;font-weight:700;font-variant-numeric:tabular-nums}
.sv.dim{color:#6b7280}

/* ── Map area ── */
#wrap{flex:1;position:relative}
#map{width:100%;height:100%}

/* Status bar */
#status{
  position:absolute;bottom:0;left:0;right:0;
  background:rgba(17,24,39,.88);color:#9ca3af;
  padding:7px 14px;font-size:0.72rem;backdrop-filter:blur(6px);
  border-top:1px solid #1f2937;z-index:1000;
  display:flex;align-items:center;gap:8px;
}
#status-dot{width:7px;height:7px;border-radius:50%;background:#374151;flex-shrink:0}
#status-dot.active{background:#34d399;animation:blink .9s ease infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
#status-txt{color:#d1d5db}

/* Dedup popup — appears near the switch on the map */
#dedup-pop{
  position:absolute;display:none;
  background:rgba(127,29,29,.93);color:#fca5a5;
  padding:5px 11px;border-radius:6px;font-size:0.75rem;font-weight:700;
  pointer-events:none;z-index:2000;white-space:nowrap;
  border:1px solid #dc2626;
  transform:translate(-50%, -110%);
}

/* Sensor pulse ring (divIcon) */
.pulse-ring{
  width:40px;height:40px;border-radius:50%;border:3px solid;
  animation:ripple 1.2s ease-out forwards;
}
@keyframes ripple{
  0%{transform:scale(.1);opacity:1}
  100%{transform:scale(1.8);opacity:0}
}

/* Leaflet tweaks */
.leaflet-container{background:#1e293b}
.leaflet-tooltip{
  background:rgba(17,24,39,.9);border:1px solid #374151;
  color:#d1d5db;font-size:0.7rem;padding:4px 8px;border-radius:4px;
}
</style>
</head>
<body>

<div id="sidebar">
  <div class="logo">Fe<span>B</span>Ex</div>
  <div class="tagline">LoRaWAN in-network<br>dedup on P4 · Singapore IXP</div>

  <div class="sec">City Scale</div>
  <button class="cfg-btn" id="btn-small"     onclick="selectCfg('small')">
    Small City <span class="cfg-meta">N=30, K=5, M=2</span>
  </button>
  <button class="cfg-btn" id="btn-medium"    onclick="selectCfg('medium')">
    Medium City <span class="cfg-meta">N=100, K=10, M=4</span>
  </button>
  <button class="cfg-btn" id="btn-large"     onclick="selectCfg('large')">
    Large City <span class="cfg-meta">N=500, K=20, M=4</span>
  </button>
  <button class="cfg-btn active" id="btn-singapore" onclick="selectCfg('singapore')">
    Singapore ★ <span class="cfg-meta">N=150, K=20, M=4</span>
  </button>

  <div class="sec">Playback</div>
  <button id="play-btn" onclick="togglePlay()">▶ Play</button>
  <div class="speed-row">
    <button class="spd"       onclick="setSpeed(0.25,this)">¼×</button>
    <button class="spd"       onclick="setSpeed(0.5,this)">½×</button>
    <button class="spd active" onclick="setSpeed(1,this)">1×</button>
    <button class="spd"       onclick="setSpeed(2,this)">2×</button>
    <button class="spd"       onclick="setSpeed(4,this)">4×</button>
  </div>

  <div class="sec">Legend</div>
  <div id="leg-tenants"></div>
  <div class="leg" style="margin-top:5px">
    <div class="leg-ico">
      <svg width="13" height="13" viewBox="0 0 13 13">
        <polygon points="6.5,1 11,3.5 11,9.5 6.5,12 2,9.5 2,3.5"
          fill="#374151" stroke="#9ca3af" stroke-width="1.2"/>
      </svg>
    </div>Hotspot (gateway)
  </div>
  <div class="leg">
    <div class="leg-ico" style="color:#60a5fa;font-size:10px;font-weight:800">◆</div>
    P4 FeBEx Switch
  </div>
  <div class="leg">
    <div class="leg-ico" style="font-size:9px">▬</div>LNS Server
  </div>
  <div class="leg">
    <div class="leg-ico">☁</div>Helium Cloud
  </div>

  <div class="sec">Live Stats</div>
  <div class="stat"><span>Events</span>      <span class="sv dim" id="s-ev">0</span></div>
  <div class="stat"><span>Forwarded</span>   <span class="sv dim" id="s-fwd">0</span></div>
  <div class="stat"><span>Suppressed</span>  <span class="sv dim" id="s-sup">0</span></div>
  <div class="stat"><span>Savings</span>     <span class="sv dim" id="s-sav">—</span></div>
</div>

<div id="wrap">
  <div id="map"></div>
  <div id="dedup-pop" id="dedup-pop"></div>
  <div id="status">
    <div id="status-dot"></div>
    <span id="status-txt">Select a configuration and press Play.</span>
  </div>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
// ── Injected config data ──────────────────────────────────────────────────────
const CONFIGS = %%CONFIGS_JSON%%;
const COLORS  = ["#38bdf8","#fbbf24","#4ade80","#f87171"];
const TNAMES  = ["Helium","TTN","AWS IoT","Actility"];

// ── Map ───────────────────────────────────────────────────────────────────────
const map = L.map('map',{center:[1.3521,103.8198],zoom:12});
// CartoDB light — no labels, very faint: keeps focus on nodes
L.tileLayer('https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png',{
  attribution:'© OpenStreetMap contributors, © CARTO',opacity:.28,maxZoom:19
}).addTo(map);
// Faint label overlay just for major area names
L.tileLayer('https://{s}.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png',{
  attribution:'',opacity:.18,maxZoom:19,pane:'overlayPane'
}).addTo(map);

const baseLayer  = L.layerGroup().addTo(map);
const animLayer  = L.layerGroup().addTo(map);
const pulseLayer = L.layerGroup().addTo(map);

// ── State ─────────────────────────────────────────────────────────────────────
let cfg=null, playing=false, spd=1, timer=null;
let stats={ev:0,fwd:0,sup:0};

// ── UI ────────────────────────────────────────────────────────────────────────
function selectCfg(key){
  ['small','medium','large','singapore'].forEach(k=>{
    document.getElementById('btn-'+k).classList.toggle('active',k===key);
  });
  cfg=CONFIGS[key];
  renderBase();
  renderLegend();
  setStatus('loaded','Config: '+cfg.label+
    ` — N=${cfg.stats.N}, K=${cfg.stats.K}, M=${cfg.stats.M}, avg coverage=${cfg.stats.avg_coverage}×`);
  if(playing) stopAnim();
  resetStats();
}

function togglePlay(){
  if(!cfg){setStatus('','Select a config first.');return;}
  playing?stopAnim():startAnim();
}

function startAnim(){
  playing=true;
  document.getElementById('play-btn').textContent='⏸ Pause';
  document.getElementById('play-btn').classList.add('playing');
  document.getElementById('status-dot').classList.add('active');
  next();
}

function stopAnim(){
  playing=false;
  clearTimeout(timer);
  document.getElementById('play-btn').textContent='▶ Play';
  document.getElementById('play-btn').classList.remove('playing');
  document.getElementById('status-dot').classList.remove('active');
  animLayer.clearLayers();
  pulseLayer.clearLayers();
  hideDedup();
}

function setSpeed(s,btn){
  spd=s;
  document.querySelectorAll('.spd').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
}

function setStatus(state,msg){
  document.getElementById('status-txt').textContent=msg;
}

function resetStats(){
  stats={ev:0,fwd:0,sup:0};
  ['ev','fwd','sup'].forEach(k=>{
    const el=document.getElementById('s-'+k);
    el.textContent='0'; el.classList.add('dim');
  });
  const sv=document.getElementById('s-sav');
  sv.textContent='—'; sv.classList.add('dim');
}

function tick(){
  stats.ev++;
  const total=stats.fwd+stats.sup;
  const pct=total>0?(stats.sup/total*100).toFixed(1)+'%':'—';
  document.getElementById('s-ev').textContent=stats.ev;
  document.getElementById('s-fwd').textContent=stats.fwd;
  document.getElementById('s-sup').textContent=stats.sup;
  document.getElementById('s-sav').textContent=pct;
  ['ev','fwd','sup','sav'].forEach(k=>
    document.getElementById('s-'+k).classList.remove('dim'));
}

function renderLegend(){
  const m=cfg.lns.length;
  document.getElementById('leg-tenants').innerHTML=
    Array.from({length:m},(_,i)=>
      `<div class="leg">
        <div class="leg-dot" style="background:${COLORS[i]}"></div>
        Tenant ${i} (${TNAMES[i]||'LNS-'+i})
      </div>`
    ).join('');
}

// ── Dedup popup ───────────────────────────────────────────────────────────────
function showDedup(n){
  const sw=cfg.switch;
  const pt=map.latLngToContainerPoint([sw.lat,sw.lon]);
  const el=document.getElementById('dedup-pop');
  el.style.left=pt.x+'px';
  el.style.top=pt.y+'px';
  el.textContent='×'+n+' suppressed';
  el.style.display='block';
  clearTimeout(el._t);
  el._t=setTimeout(hideDedup, 900/spd);
}
function hideDedup(){
  document.getElementById('dedup-pop').style.display='none';
}

// ── Base layer ────────────────────────────────────────────────────────────────
function mkIcon(html,w,h){
  return L.divIcon({className:'',html,iconSize:[w,h],iconAnchor:[w/2,h/2]});
}

function renderBase(){
  baseLayer.clearLayers();

  // Sensors — larger circles
  const sR = cfg.stats.N > 200 ? 5 : 8;
  cfg.sensors.forEach((s,i)=>{
    const c=COLORS[s.tenant%COLORS.length];
    L.circleMarker([s.lat,s.lon],{
      radius:sR, color:'#0f172a', fillColor:c,
      fillOpacity:.9, weight:1.2, opacity:1,
    }).bindTooltip(`ED ${i} · Tenant ${s.tenant} (${TNAMES[s.tenant]||'?'})`,{sticky:true})
      .addTo(baseLayer);
  });

  // Hotspots — larger hexagon
  cfg.hotspots.forEach((h,i)=>{
    L.marker([h.lat,h.lon],{icon:mkIcon(
      `<svg width="26" height="26" viewBox="0 0 26 26">
        <polygon points="13,2 22,7 22,19 13,24 4,19 4,7"
          fill="#1e293b" stroke="#94a3b8" stroke-width="2"/>
        <circle cx="13" cy="13" r="4" fill="#cbd5e1"/>
        <text x="13" y="17" text-anchor="middle" fill="#1e293b"
          font-size="7" font-weight="800" font-family="monospace">GW</text>
      </svg>`,26,26)})
      .bindTooltip(`GW ${i}`,{sticky:true})
      .addTo(baseLayer);
  });

  // P4 Switch — larger diamond
  const sw=cfg.switch;
  L.marker([sw.lat,sw.lon],{icon:mkIcon(
    `<div style="position:relative;width:46px;height:46px">
      <div style="position:absolute;inset:4px;background:#1e3a5f;border:3px solid #3b82f6;
        border-radius:6px;transform:rotate(45deg);box-shadow:0 0 12px #3b82f688"></div>
      <div style="position:absolute;inset:0;display:flex;align-items:center;
        justify-content:center;color:#60a5fa;font-size:11px;font-weight:900;
        letter-spacing:-0.03em;text-shadow:0 0 8px #3b82f6">P4</div>
    </div>`,46,46),zIndexOffset:1000})
    .bindTooltip('<b>FeBEx IXP Switch</b><br>P4 dedup · tenant steering · receipt mirror',{sticky:true})
    .addTo(baseLayer);

  // LNS servers — bigger badges
  cfg.lns.forEach((l,i)=>{
    const c=COLORS[i%COLORS.length];
    L.marker([l.lat,l.lon],{icon:mkIcon(
      `<div style="background:${c}25;border:2px solid ${c};border-radius:4px;
        width:52px;height:24px;display:flex;align-items:center;justify-content:center;
        font-size:10px;color:${c};font-weight:800;letter-spacing:0.03em;
        box-shadow:0 0 8px ${c}44">LNS-${i}</div>`,52,24),zIndexOffset:500})
      .bindTooltip(`LNS ${i}: ${TNAMES[i]||'Tenant '+i}`,{sticky:true})
      .addTo(baseLayer);
  });

  // Cloud — larger icon
  L.marker([cfg.cloud.lat,cfg.cloud.lon],{icon:mkIcon(
    `<div style="font-size:30px;line-height:1;filter:drop-shadow(0 0 8px #a855f7)">☁</div>`,
    36,28),zIndexOffset:500})
    .bindTooltip('Helium Cloud (PoC receipts)',{sticky:true})
    .addTo(baseLayer);
}

// ── Animation helpers ─────────────────────────────────────────────────────────
function wait(ms){
  return new Promise(r=>{timer=setTimeout(r,Math.round(ms/spd))});
}

function drawLine(from,to,color,weight,opacity,dash){
  const line=L.polyline([[from.lat,from.lon],[to.lat,to.lon]],{
    color,weight,opacity:0,dashArray:dash||null,lineCap:'round',
  }).addTo(animLayer);

  requestAnimationFrame(()=>{
    const p=line._path;
    if(p){
      try{
        const len=p.getTotalLength();
        p.style.strokeDasharray=len+' '+len;
        p.style.strokeDashoffset=len;
        const dur=Math.round(380/spd);
        p.style.transition=`stroke-dashoffset ${dur}ms ease, opacity ${Math.round(180/spd)}ms`;
        requestAnimationFrame(()=>{
          line.setStyle({opacity});
          p.style.strokeDashoffset=0;
        });
      }catch(e){line.setStyle({opacity});}
    }else{line.setStyle({opacity});}
  });
  return line;
}

function fadeLines(lines){
  lines.forEach(l=>{
    const p=l._path;
    if(p){p.style.transition=`opacity ${Math.round(300/spd)}ms`;p.style.opacity=0;}
  });
  return wait(320).then(()=>lines.forEach(l=>{
    try{animLayer.removeLayer(l);}catch(e){}
  }));
}

function pulse(lat,lon,color){
  const m=L.marker([lat,lon],{
    icon:L.divIcon({className:'',
      html:`<div class="pulse-ring" style="border-color:${color};box-shadow:0 0 8px ${color}99"></div>`,
      iconSize:[40,40],iconAnchor:[20,20]}),
    zIndexOffset:2000,
  }).addTo(pulseLayer);
  setTimeout(()=>{try{pulseLayer.removeLayer(m);}catch(e){}},1350);
}

// ── Event loop ────────────────────────────────────────────────────────────────
async function runEvent(){
  if(!playing||!cfg) return;

  // Pick a random sensor with at least one covering hotspot
  const n=cfg.sensors.length;
  let si, covered;
  for(let t=0;t<30;t++){
    si=Math.floor(Math.random()*n);
    covered=cfg.coverage[si].map((v,i)=>v?i:-1).filter(i=>i>=0);
    if(covered.length>0) break;
  }
  if(!covered||covered.length===0){next();return;}

  const s=cfg.sensors[si];
  const sc=COLORS[s.tenant%COLORS.length];
  const sw=cfg.switch;

  // 1 — Sensor emits
  pulse(s.lat,s.lon,sc);
  setStatus('active',`ED ${si} transmitting (Tenant ${s.tenant} · ${TNAMES[s.tenant]||'?'}) — ${covered.length} GW(s) in range`);
  await wait(420);
  if(!playing) return;

  // 2 — Sensor → Hotspots
  const gwLines=covered.map(gi=>{
    const g=cfg.hotspots[gi];
    return drawLine(s,g,'#94a3b8',2.5,.7,'7 4');
  });
  await wait(480);
  if(!playing) return;

  // 3 — Hotspots → Switch
  const swLines=covered.map(gi=>{
    const g=cfg.hotspots[gi];
    return drawLine(g,sw,'#60a5fa',4,.9);
  });
  await wait(480);
  if(!playing) return;

  // 4 — Dedup at switch
  const nSup=covered.length-1;
  if(nSup>0){
    showDedup(nSup);
    swLines.slice(1).forEach(l=>l.setStyle({color:'#ef4444',opacity:.45,dashArray:'5 8',weight:2.5}));
    stats.sup+=nSup;
    await wait(380);
    if(!playing) return;
  }

  // 5 — Switch → correct LNS
  const lns=cfg.lns[s.tenant%cfg.lns.length];
  const lnsLine=drawLine(sw,lns,'#4ade80',5,.95);

  // 6 — Receipt → cloud (dashed bright violet)
  const rcLine=drawLine(sw,cfg.cloud,'#c084fc',3,.8,'5 5');

  stats.fwd++;
  tick();

  setStatus('active',
    `Switch: ${covered.length} copies → ${nSup>0?nSup+' suppressed, ':''}1 forwarded → `+
    `LNS-${s.tenant} + receipt to cloud`);

  await wait(750);
  if(!playing) return;

  await fadeLines([...gwLines,...swLines,lnsLine,rcLine]);
  next();
}

function next(){
  if(!playing) return;
  timer=setTimeout(runEvent,Math.round(180/spd));
}

// ── Boot ──────────────────────────────────────────────────────────────────────
selectCfg('singapore');
</script>
</body>
</html>
"""


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="FeBEx Singapore interactive visualization")
    parser.add_argument("--output", default=str(REPO_DIR / "plots" / "singapore_map.html"),
                        help="Output HTML path (default: plots/singapore_map.html)")
    args = parser.parse_args()

    print("FeBEx Singapore Visualization")
    print("=" * 40)
    configs = build_all_configs()

    configs_json = json.dumps(configs, separators=(',', ':'))
    html = HTML_TEMPLATE.replace("%%CONFIGS_JSON%%", configs_json)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"\nSaved: {out}")
    print(f"Open in browser (internet required for map tiles):")
    print(f"  firefox {out}  OR  xdg-open {out}")


if __name__ == "__main__":
    main()
