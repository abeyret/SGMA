#!/usr/bin/env python3
"""
Build a new standalone, self-contained HTML story page that reuses the same
embedded ATLAS data from data/clean/sjv_equity_atlas.html.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC_HTML = ROOT / "data/clean/sjv_equity_atlas.html"
OUT_HTML = ROOT / "data/clean/sjv_equity_fluctuating_story.html"


def extract_atlas_json(html_text: str) -> dict:
    # Find "const ATLAS = { ... };" and brace-balance.
    m = re.search(r"const\s+ATLAS\s*=\s*(\{)", html_text)
    if not m:
        raise ValueError("Could not find `const ATLAS = {` in source HTML.")
    start = m.start(1)
    s = html_text[start:]
    depth = 0
    end = None
    for i, ch in enumerate(s):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        raise ValueError("Failed to brace-balance ATLAS JSON.")
    return json.loads(s[:end])


def render_story(atlas: dict) -> str:
    atlas_json = json.dumps(atlas, separators=(",", ":")).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>SGMA Equity Atlas — Fluctuating Story</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
        integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin=""/>
  <style>
    * {{ box-sizing:border-box; }}
    html,body {{ margin:0; height:100%; background:#f6f0e6; color:#2a1a12; font-family: system-ui, Segoe UI, Roboto, sans-serif; }}
    .app {{ min-height:100vh; }}
    .topbar {{
      position: sticky; top: 0; z-index: 50;
      background: rgba(246,240,230,0.92);
      border-bottom: 1px solid rgba(42,26,18,0.10);
      backdrop-filter: blur(6px);
    }}
    .topbar .wrap {{
      max-width: 1200px; margin: 0 auto;
      padding: 12px 16px;
      display:flex; align-items:flex-end; justify-content:space-between; gap: 14px;
    }}
    .title {{
      font-weight: 650; letter-spacing: -0.015em; margin:0;
      font-size: 1.05rem;
    }}
    .sub {{
      margin:4px 0 0 0; color: rgba(42,26,18,0.72); font-size: 0.86rem; max-width: 70ch;
    }}
    .pill {{
      display:inline-flex; gap:8px; align-items:center;
      border: 1px solid rgba(42,26,18,0.18);
      background: rgba(255,255,255,0.55);
      border-radius: 999px;
      padding: 8px 10px;
      font-size: 0.82rem;
      color: rgba(42,26,18,0.82);
      white-space: nowrap;
    }}

    .layout {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 14px 16px 24px 16px;
      display:grid;
      grid-template-columns: 1.1fr 0.9fr;
      gap: 14px;
    }}
    @media (max-width: 980px) {{
      .layout {{ grid-template-columns: 1fr; }}
    }}

    .panel {{
      border: 1px solid rgba(42,26,18,0.14);
      border-radius: 16px;
      background: rgba(255,255,255,0.78);
      box-shadow: 0 10px 22px rgba(42,26,18,0.10);
      overflow: hidden;
      min-height: 0;
      display:flex;
      flex-direction: column;
    }}
    .panel .hdr {{
      padding: 12px 12px;
      border-bottom: 1px solid rgba(42,26,18,0.08);
      display:flex; align-items:center; justify-content:space-between; gap: 10px;
    }}
    .panel .hdr h2 {{ margin:0; font-size: 0.95rem; font-weight: 700; }}
    .panel .body {{ padding: 12px; display:flex; flex-direction:column; gap: 10px; min-height: 0; flex: 1; }}

    #map {{ height: 520px; min-height: 420px; border-radius: 12px; }}
    @media (max-width: 980px) {{ #map {{ height: 440px; }} }}

    .story {{
      display:flex;
      flex-direction: column;
      gap: 10px;
    }}
    .step {{
      border: 1px solid rgba(42,26,18,0.10);
      border-radius: 14px;
      padding: 10px 10px;
      background: rgba(255,255,255,0.55);
      cursor: pointer;
    }}
    .step.active {{
      border-color: rgba(107,63,42,0.32);
      background: rgba(255,255,255,0.72);
    }}
    .step .k {{ font-size: 0.78rem; color: rgba(42,26,18,0.70); }}
    .step .t {{ font-weight: 650; margin-top: 2px; }}
    .step .d {{ margin-top: 6px; color: rgba(42,26,18,0.78); font-size: 0.86rem; line-height: 1.25; }}

    .controls {{
      display:flex;
      flex-direction: column;
      gap: 8px;
      margin-top: 2px;
    }}
    .row {{ display:flex; justify-content:space-between; gap: 10px; align-items:center; }}
    .row label {{ font-size: 0.82rem; color: rgba(42,26,18,0.74); }}
    input[type="range"] {{ width: 100%; }}

    /* Tiered charts (small multiples) */
    .tierGrid {{
      display:grid;
      grid-template-columns: 1fr;
      gap: 10px;
    }}
    .tierCard {{
      border: 1px solid rgba(42,26,18,0.10);
      border-radius: 14px;
      background: rgba(255,255,255,0.55);
      padding: 10px;
    }}
    .tierHdr {{ display:flex; justify-content:space-between; align-items:baseline; gap: 10px; }}
    .tierHdr .h {{ font-weight: 700; font-size: 0.88rem; }}
    .tierHdr .n {{ font-size: 0.80rem; color: rgba(42,26,18,0.68); }}
    .tierWrap {{ height: 160px; margin-top: 8px; }}
    .tierSvg {{ width: 100%; height: 100%; }}
    .note {{ font-size: 0.80rem; color: rgba(42,26,18,0.70); line-height: 1.25; }}
  </style>
</head>
<body>
<div class="app">
  <div class="topbar">
    <div class="wrap">
      <div>
        <div class="title">SGMA Equity Atlas — Fluctuating Map + Tiered Distribution</div>
        <div class="sub">Scroll the story steps or drag the timeline to watch the choropleth “morph” between pre- and post-SGMA conditions while the tiered chart shows how the distribution across regions shifts.</div>
      </div>
      <div class="pill" id="metricPill">Metric: —</div>
    </div>
  </div>

  <div class="layout">
    <div class="panel">
      <div class="hdr">
        <h2>Fluctuating map</h2>
        <div class="pill" id="timePill">t = 0%</div>
      </div>
      <div class="body">
        <div id="map"></div>
        <div class="controls">
          <div class="row">
            <label for="tRange">Timeline (Pre → Post interpolation)</label>
            <div class="pill" id="yrPill">Pre</div>
          </div>
          <input id="tRange" type="range" min="0" max="100" value="0" step="1"/>
          <div class="note">This is a visualization device: values are linearly interpolated from each region’s embedded Pre and Post values. The goal is to make distribution shifts legible, not to claim continuous measurements for every year.</div>
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="hdr">
        <h2>100% stacked distribution (tiers)</h2>
        <div class="pill" id="tierPill">Low / Mid / High (share of counties)</div>
      </div>
      <div class="body">
        <div class="story" id="storySteps"></div>
        <div class="tierGrid" id="tierGrid"></div>
        <div class="note" id="tierNote">Each panel bins counties into low/mid/high tiers for that metric at each timeline step, then stacks the shares to 100% as a stepped area. (Dry wells = well failures issue-start.)</div>
      </div>
    </div>
  </div>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
const ATLAS = {atlas_json};

// -------------------------
// Metric definitions (county mode)
// -------------------------
const METRICS = [
  {{
    id: 'wells',
    label: 'Dry wells (reports, issue start)',
    note: 'Counts binned using Approx. Issue Start Date (pre vs post).',
    getPre: (p) => (p && p.pre) ? p.pre.well_failures_issue_start : null,
    getPost: (p) => (p && p.post) ? p.post.well_failures_issue_start : null,
    fmt: (v) => (v==null || Number.isNaN(v)) ? 'n/a' : Math.round(v).toLocaleString(),
  }},
  {{
    id: 'subsidence',
    label: 'Subsidence (InSAR; change)',
    note: 'If available, uses embedded county subsidence (often missing due to download limits).',
    // Some builds embed an object; some are None. Treat missing as null.
    getPre: (p) => (p && p.subsidence && typeof p.subsidence === 'object') ? p.subsidence.pre_cm_yr : null,
    getPost: (p) => (p && p.subsidence && typeof p.subsidence === 'object') ? p.subsidence.post_cm_yr : null,
    fmt: (v) => (v==null || Number.isNaN(v)) ? 'n/a' : Number(v).toFixed(2) + ' cm/yr',
  }},
  {{
    id: 'gwe',
    label: 'Groundwater elevation (ft)',
    note: 'Best-effort county aggregation.',
    getPre: (p) => (p && p.pre) ? p.pre.gwe_ft : null,
    getPost: (p) => (p && p.post) ? p.post.gwe_ft : null,
    fmt: (v) => (v==null || Number.isNaN(v)) ? 'n/a' : Number(v).toFixed(1),
  }},
  {{
    id: 'fallow',
    label: 'Fallow acres',
    note: 'CDL fallow/idle cropland acres (pre vs post).',
    getPre: (p) => (p && p.pre) ? p.pre.fallow_acres : null,
    getPost: (p) => (p && p.post) ? p.post.fallow_acres : null,
    fmt: (v) => (v==null || Number.isNaN(v)) ? 'n/a' : Math.round(v).toLocaleString(),
  }},
  {{
    id: 'income',
    label: 'Median income',
    note: 'ACS median income (2014 vs 2021).',
    getPre: (p) => (p && p.pre) ? p.pre.median_income : null,
    getPost: (p) => (p && p.post) ? p.post.median_income : null,
    fmt: (v) => (v==null || Number.isNaN(v)) ? 'n/a' : '$' + Math.round(v).toLocaleString(),
  }},
];

// Story steps (click or scroll)
const STEPS = [
  {{ id:'s1', title:'Groundwater', desc:'Groundwater elevation (proxy for overdraft pressure).', metric:'gwe' }},
  {{ id:'s2', title:'Dry wells', desc:'Dry-well reports (issue start) as community harm.', metric:'wells' }},
  {{ id:'s3', title:'Fallowed land', desc:'Fallow/idle cropland as an adjustment margin.', metric:'fallow' }},
  {{ id:'s4', title:'Subsidence', desc:'InSAR subsidence rate (if available in the build).', metric:'subsidence' }},
];

let metricId = 'gwe';
let t = 0; // 0..1

function hasMetricData(id) {{
  const m = METRICS.find(mm => mm.id === id);
  if (!m) return false;
  const feats = (ATLAS.counties && ATLAS.counties.features) ? ATLAS.counties.features : [];
  for (const f of feats) {{
    const p = f.properties || {{}};
    const pre = num(m.getPre(p));
    const post = num(m.getPost(p));
    if (pre != null || post != null) return true;
  }}
  return false;
}}

// -------------------------
// Map
// -------------------------
const map = L.map('map', {{ zoomControl: true, scrollWheelZoom: false }});
L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}.png', {{
  attribution: '&copy; OpenStreetMap &copy; CARTO',
  maxZoom: 18
}}).addTo(map);

function featByFips(fips) {{
  const feats = (ATLAS.counties && ATLAS.counties.features) ? ATLAS.counties.features : [];
  return feats.find(f => f && f.properties && f.properties.fips5 === fips) || null;
}}

function num(v) {{
  if (v === null || v === undefined) return null;
  const x = Number(v);
  return Number.isFinite(x) ? x : null;
}}

function lerp(a, b, tt) {{
  if (a == null || b == null) return null;
  return a + (b - a) * tt;
}}

function currentMetric() {{
  return METRICS.find(m => m.id === metricId) || METRICS[0];
}}

function valuesAt(tt) {{
  const m = currentMetric();
  const feats = (ATLAS.counties && ATLAS.counties.features) ? ATLAS.counties.features : [];
  const out = [];
  for (const f of feats) {{
    const p = f.properties || {{}};
    const pre = num(m.getPre(p));
    const post = num(m.getPost(p));
    const v = lerp(pre, post, tt);
    if (v == null) continue;
    out.push(v);
  }}
  out.sort((a,b)=>a-b);
  return out;
}}

function quantile(sortedVals, q) {{
  if (!sortedVals.length) return null;
  const pos = (sortedVals.length - 1) * q;
  const base = Math.floor(pos);
  const rest = pos - base;
  if (sortedVals[base+1] === undefined) return sortedVals[base];
  return sortedVals[base] + rest * (sortedVals[base+1] - sortedVals[base]);
}}

function rampColor(x) {{
  // warm brown scale
  // x in [0,1]
  const lo = [247, 232, 210];
  const hi = [107, 63, 42];
  const r = Math.round(lo[0] + (hi[0]-lo[0])*x);
  const g = Math.round(lo[1] + (hi[1]-lo[1])*x);
  const b = Math.round(lo[2] + (hi[2]-lo[2])*x);
  return 'rgb(' + r + ',' + g + ',' + b + ')';
}}

function makeStyleFunc() {{
  const m = currentMetric();
  const vals = valuesAt(t);
  const lo = vals.length ? vals[0] : 0;
  const hi = vals.length ? vals[vals.length-1] : 1;
  return (feature) => {{
    const p = (feature && feature.properties) ? feature.properties : {{}};
    const pre = num(m.getPre(p));
    const post = num(m.getPost(p));
    const v = lerp(pre, post, t);
    let x = 0.5;
    if (v != null && hi !== lo) x = (v - lo) / (hi - lo);
    x = Math.max(0, Math.min(1, x));
    return {{
      color: 'rgba(42,26,18,0.55)',
      weight: 1,
      fillOpacity: 0.72,
      fillColor: rampColor(x),
    }};
  }};
}}

let countiesLayer = null;
function drawMap() {{
  if (countiesLayer) countiesLayer.remove();
  if (!ATLAS.counties) return;
  const styleFn = makeStyleFunc();
  countiesLayer = L.geoJSON(ATLAS.counties, {{
    style: styleFn,
    onEachFeature: (feature, layer) => {{
      const p = feature.properties || {{}};
      layer.on('click', () => {{
        const m = currentMetric();
        const pre = num(m.getPre(p));
        const post = num(m.getPost(p));
        const v = lerp(pre, post, t);
        layer.bindPopup('<b>' + (p.name || 'County') + '</b><br/>' + m.label + ': ' + m.fmt(v)).openPopup();
      }});
    }}
  }}).addTo(map);
  map.fitBounds(countiesLayer.getBounds(), {{ padding:[10,10] }});
}}

// -------------------------
// Tiered charts (three tiers, small multiples)
// -------------------------
const W = 800, H = 160;
const pad = {{ l: 36, r: 18, t: 10, b: 20 }};

function computeTierSeries(metricId) {{
  const m = METRICS.find(mm => mm.id === metricId) || METRICS[0];
  const feats = (ATLAS.counties && ATLAS.counties.features) ? ATLAS.counties.features : [];
  const steps = d3.range(0, 41).map(i => i/40);
  const series = steps.map(tt => {{
    const vals = [];
    for (const f of feats) {{
      const p = f.properties || {{}};
      const pre = num(m.getPre(p));
      const post = num(m.getPost(p));
      const v = lerp(pre, post, tt);
      if (v == null) continue;
      vals.push(v);
    }}
    vals.sort((a,b)=>a-b);
    const q1 = quantile(vals, 1/3);
    const q2 = quantile(vals, 2/3);
    let low=0, mid=0, high=0;
    for (const v of vals) {{
      if (v <= q1) low++;
      else if (v <= q2) mid++;
      else high++;
    }}
    const n = vals.length || 1;
    return {{ tt, low: low/n, mid: mid/n, high: high/n }};
  }});
  return series;
}}

function drawTierChartFor(svgSel, metricId) {{
  svgSel.selectAll('*').remove();
  const data = computeTierSeries(metricId);
  const x = d3.scaleLinear().domain([0,1]).range([pad.l, W-pad.r]);
  const y = d3.scaleLinear().domain([0,1]).range([H-pad.b, pad.t]);

  const stack = d3.stack().keys(['low','mid','high']);
  const stacked = stack(data);

  const colors = {{
    low: 'rgba(107,63,42,0.30)',
    mid: 'rgba(107,63,42,0.55)',
    high: 'rgba(107,63,42,0.78)',
  }};

  const area = d3.area()
    .x(d => x(d.data.tt))
    .y0(d => y(d[0]))
    .y1(d => y(d[1]))
    .curve(d3.curveStepAfter);

  // subtle background + 100% reference
  svgSel.append('rect')
    .attr('x', pad.l)
    .attr('y', pad.t)
    .attr('width', (W-pad.l-pad.r))
    .attr('height', (H-pad.t-pad.b))
    .attr('fill', 'rgba(255,255,255,0.18)')
    .attr('stroke', 'rgba(42,26,18,0.10)')
    .attr('stroke-width', 1);

  svgSel.append('g')
    .selectAll('path')
    .data(stacked)
    .join('path')
    .attr('d', area)
    .attr('fill', d => colors[d.key] || 'rgba(0,0,0,0.2)')
    .attr('stroke', 'rgba(255,255,255,0.75)')
    .attr('stroke-width', 2);

  // dashed outline at the top of the stack (100%)
  const topLine = d3.line()
    .x(d => x(d.tt))
    .y(d => y(1))
    .curve(d3.curveStepAfter);
  svgSel.append('path')
    .datum(data)
    .attr('d', topLine)
    .attr('fill', 'none')
    .attr('stroke', 'rgba(42,26,18,0.55)')
    .attr('stroke-width', 2)
    .attr('stroke-dasharray', '6 5');

  // y axis as percentages (0/50/100)
  const yAxis = d3.axisLeft(y)
    .tickValues([0,0.5,1])
    .tickFormat(d => Math.round(d*100) + '%');
  svgSel.append('g')
    .attr('transform', 'translate(' + pad.l + ',0)')
    .call(yAxis)
    .call(g => g.selectAll('text').attr('fill','rgba(42,26,18,0.70)').attr('font-size','11px'))
    .call(g => g.selectAll('path,line').attr('stroke','rgba(42,26,18,0.18)'));

  const axis = d3.axisBottom(x).ticks(5).tickFormat(d => Math.round(d*100) + '%');
  svgSel.append('g')
    .attr('transform', 'translate(0,' + (H-pad.b) + ')')
    .call(axis)
    .call(g => g.selectAll('text').attr('fill','rgba(42,26,18,0.70)').attr('font-size','11px'))
    .call(g => g.selectAll('path,line').attr('stroke','rgba(42,26,18,0.18)'));
}}

function drawTierChartsAll() {{
  const grid = document.getElementById('tierGrid');
  if (!grid) return;
  // create cards once
  if (!grid.dataset.built) {{
    const wanted = ['wells','fallow','gwe','subsidence'];
    const avail = new Set(METRICS.map(m => m.id));
    const list = wanted.filter(id => avail.has(id) && hasMetricData(id));
    if (!list.length) {{
      grid.innerHTML = '<div class=\"note\">No tier charts available (missing data).</div>';
      grid.dataset.built = '1';
      return;
    }}
    grid.innerHTML = list.map(id => {{
      const m = METRICS.find(mm => mm.id === id);
      return (
        '<div class=\"tierCard\" data-mid=\"' + id + '\">' +
          '<div class=\"tierHdr\"><div class=\"h\">' + m.label + '</div><div class=\"n\">Low / Mid / High</div></div>' +
          '<div class=\"tierWrap\"><svg class=\"tierSvg\" viewBox=\"0 0 800 160\" preserveAspectRatio=\"none\"></svg></div>' +
        '</div>'
      );
    }}).join('');
    grid.dataset.built = '1';
  }}

  grid.querySelectorAll('.tierCard').forEach(card => {{
    const id = card.getAttribute('data-mid');
    const svgEl = card.querySelector('svg');
    if (!svgEl) return;
    drawTierChartFor(d3.select(svgEl), id);
  }});
}}

// -------------------------
// Story UI
// -------------------------
const stepsEl = document.getElementById('storySteps');
stepsEl.innerHTML = STEPS.map(s =>
  '<div class="step" data-metric=\"' + s.metric + '\" tabindex=\"0\" role=\"button\" aria-label=\"' + s.title + '\">' +
    '<div class=\"k\">Step</div>' +
    '<div class=\"t\">' + s.title + '</div>' +
    '<div class=\"d\">' + s.desc + '</div>' +
  '</div>'
).join('');

function setMetric(id) {{
  // if chosen metric lacks data, fall back
  if (!hasMetricData(id)) {{
    id = 'gwe';
  }}
  metricId = id;
  const m = currentMetric();
  document.getElementById('metricPill').textContent = 'Metric: ' + m.label;
  drawMap();
  drawTierChartsAll();
}}

function setT(v01) {{
  t = Math.max(0, Math.min(1, v01));
  document.getElementById('timePill').textContent = 't = ' + Math.round(t*100) + '%';
  document.getElementById('yrPill').textContent = (t < 0.02) ? 'Pre' : (t > 0.98) ? 'Post' : ('Mix ' + Math.round(t*100) + '%');
  // restyle map quickly
  if (countiesLayer) {{
    const styleFn = makeStyleFunc();
    countiesLayer.setStyle(styleFn);
  }}
}}

stepsEl.addEventListener('click', (e) => {{
  const step = e.target.closest('.step');
  if (!step) return;
  setMetric(step.dataset.metric);
  document.querySelectorAll('.step').forEach(el => el.classList.toggle('active', el === step));
}});

document.querySelectorAll('.step')[0].classList.add('active');
setMetric('gwe');
drawMap();
drawTierChartsAll();
setT(0);

document.getElementById('tRange').addEventListener('input', (e) => {{
  const v = Number(e.target.value) / 100;
  setT(v);
}});

// Optional: scroll-activate steps similar to "fluctuating" article
const stepNodes = Array.from(document.querySelectorAll('.step'));
const io = new IntersectionObserver((entries) => {{
  // pick the most visible step
  let best = null;
  for (const ent of entries) {{
    if (!ent.isIntersecting) continue;
    if (!best || ent.intersectionRatio > best.intersectionRatio) best = ent;
  }}
  if (best && best.target) {{
    const id = best.target.dataset.metric;
    setMetric(id);
    stepNodes.forEach(el => el.classList.toggle('active', el === best.target));
  }}
}}, {{ root: stepsEl, threshold: [0.2,0.35,0.5,0.65,0.8] }});
stepNodes.forEach(n => io.observe(n));
</script>
</body>
</html>
"""


def main() -> int:
    if not SRC_HTML.is_file():
        raise FileNotFoundError(f"Missing {SRC_HTML}")
    atlas = extract_atlas_json(SRC_HTML.read_text(encoding="utf-8"))
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(render_story(atlas), encoding="utf-8")
    print(f"Wrote {OUT_HTML} ({OUT_HTML.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

