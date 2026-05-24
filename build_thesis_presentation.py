"""Build comprehensive thesis presentation with maps, GIF, data charts, SGMA verdict."""
from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT_HTML = ROOT / "vercel_site" / "thesis_presentation.html"
OUT_TEMPLATE = ROOT / "vercel_site" / "thesis_presentation_template.html"
OUT_DATA = ROOT / "vercel_site" / "thesis_presentation_data.js"
COUNTIES_GEO = ROOT / "vercel_site" / "thesis_counties.geojson"
ASSET = "./assets/ppt"
DATA_MARKER = "<!-- THESIS_DATA_INLINE -->"


def load_data() -> dict:
    atlas = json.loads(
        re.search(
            r"const ATLAS = (\{.*\});",
            (ROOT / "vercel_site" / "atlas_data.js").read_text(encoding="utf-8"),
            re.DOTALL,
        ).group(1)
    )

    counties = []
    for feat in atlas["counties"]["features"]:
        p = feat["properties"]
        d = p.get("delta") or {}
        counties.append(
            {
                "name": p["name"],
                "well_pre": p["pre"]["well_failures_issue_start"],
                "well_post": p["post"]["well_failures_issue_start"],
                "well_total": p["post"]["well_failures_total"],
                "gwe_delta": round(d.get("gwe_ft", p["post"]["gwe_ft"] - p["pre"]["gwe_ft"]), 1),
                "small_loss": int(d.get("small_farms", 0) or 0),
                "small_pre": p["pre"]["small_farms"],
                "small_post": p["post"]["small_farms"],
                "large_pre": p["pre"]["large_farms"],
                "large_post": p["post"]["large_farms"],
                "fallow_delta": int(d.get("fallow_acres", 0) or 0),
            }
        )

    subbasins = []
    for sb in atlas.get("subbasins", []):
        subbasins.append(
            {
                "name": sb["subbasin_name"].replace("SAN JOAQUIN VALLEY - ", ""),
                "gwe_delta": round(sb.get("gwe_delta_mnm") or 0, 1),
                "well_delta_adj": round(sb.get("well_delta_adj") or 0, 3),
                "fallow_delta": int(sb.get("fallow_delta_acres") or 0),
                "n_gsp": sb.get("n_gsp", 0),
            }
        )

    gsp_status = dict(Counter(r["Status"] for r in csv.DictReader(open(ROOT / "sjv_gsp_status.csv", encoding="utf-8"))))

    regressions = []
    seen = set()
    for r in csv.DictReader(open(ROOT / "data/clean/sjv_regressions_results.csv", encoding="utf-8")):
        if r["model"] in seen:
            continue
        seen.add(r["model"])
        rows = [x for x in csv.DictReader(open(ROOT / "data/clean/sjv_regressions_results.csv", encoding="utf-8")) if x["model"] == r["model"]]
        xrow = next(x for x in rows if x["term"] != "Intercept")
        regressions.append(
            {
                "model": r["model"],
                "y": r["y"],
                "x": r["x"],
                "r2": float(r["r2"]),
                "coef_x": float(xrow["coef"]),
            }
        )

    pre_w = sum(c["well_pre"] for c in counties)
    post_w = sum(c["well_post"] for c in counties)
    small_loss = sum(abs(c["small_loss"]) for c in counties)
    gw_decline = sum(1 for c in counties if c["gwe_delta"] < 0)
    large_gain = sum(c["large_post"] - c["large_pre"] for c in counties)

    counties_geo = json.loads(COUNTIES_GEO.read_text(encoding="utf-8"))

    return {
        "counties": counties,
        "counties_geo": counties_geo,
        "subbasins": subbasins,
        "gsp_status": gsp_status,
        "regressions": regressions,
        "summary": {
            "well_pre": pre_w,
            "well_post": post_w,
            "well_ratio": round(post_w / max(1, pre_w), 1),
            "small_loss": small_loss,
            "gw_decline_counties": gw_decline,
            "large_farm_gain": large_gain,
            "gsp_total": sum(gsp_status.values()),
            "gsp_approved": gsp_status.get("Approved", 0),
        },
        "repair_costs": {
            "federal_2026_m": 889,
            "housing_2025_b": 1.87,
            "cumulative_b_min": 1,
            "long_term_aqueduct_b": 3,
        },
        "quotes": [
            {
                "type": "pro",
                "text": "SGMA finally forces us to live within our water means. Better to plan now than face mandatory cutbacks later.",
                "author": "Westlands Water District grower · public comment, 2019",
            },
            {
                "type": "pro",
                "text": "We need sustainable groundwater for the long term — for communities, not just agriculture.",
                "author": "Karen Ross, CA Secretary of Food & Agriculture · SGMA outreach",
            },
            {
                "type": "con",
                "text": "Small farmers weren't at the table when these plans were written. We're the ones who'll lose access first.",
                "author": "Community Water Center · small farmer clinic, 2023",
            },
            {
                "type": "con",
                "text": "It's David and Goliath — a handful of large pumpers can keep going while family farms dry up.",
                "author": "Brenton Kelly, Cuyama Valley farmer · CalMatters, 2024",
            },
        ],
        "atlas_page": "index.html",
        "sources": [
            "TRE Altamira vertical displacement (CNRA) · subsidence map",
            "DWR Bulletin 118 critically overdrafted basins",
            "DWR Household Water Supply Shortage Reporting System",
            "DWR GSP Monitoring Network (CASGEM / MNM)",
            "USDA NASS farm operations by size",
            "CDL fallow acreage · Knight & Lee (2024) · Faunt et al. (2016)",
        ],
    }


def build_html_legacy() -> str:
    a = ASSET
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>ECON 30 Thesis — SGMA &amp; San Joaquin Valley Equity</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
  <link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap" rel="stylesheet"/>
  <script src="https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"></script>
  <script src="thesis_presentation_data.js"></script>
  <link rel="stylesheet" href="thesis_presentation.css"/>
</head>
<body>
  <div class="asset-warn" id="assetWarn">Images require opening from the <strong>vercel_site</strong> folder — run <code>open_thesis_presentation.bat</code> or <code>python -m http.server</code> inside vercel_site.</div>
  <div class="progress"><span id="progressBar"></span></div>
  <header class="chrome">
    <span>Alexandra Beyret · ECON 30</span>
    <span id="slideNum">1 / 11</span>
    <span class="hint">← → · Space · F</span>
  </header>

  <main class="deck">

    <!-- 0 Title -->
    <section class="slide active slide-title" data-n="0">
      <img class="slide-photo" src="{a}/image8.png" alt=""/>
      <div class="slide-shade"></div>
      <div class="slide-body center">
        <p class="kicker">Senior thesis presentation · May 2026</p>
        <h1>Groundwater overdraft, subsidence &amp; equity under SGMA</h1>
        <p class="lead">San Joaquin Central Valley · Understanding impacts of regulation on residents and farms</p>
      </div>
    </section>

    <!-- 1 Subsidence map -->
    <section class="slide slide-split" data-n="1">
      <div class="text-panel">
        <p class="kicker">01 · Physical cost · TRE Altamira / DWR</p>
        <h2>The San Joaquin Central Valley is sinking</h2>
        <div class="rule"></div>
        <p>Land subsidence is measured by InSAR vertical displacement and USGS benchmarks. Hotspots near <strong>Corcoran</strong> and <strong>El Nido</strong> show sustained compaction from long-term overdraft.</p>
        <ul class="data-list">
          <li><strong>&gt;30 cm/yr</strong> peak rates in parts of the valley (Faunt et al., 2016)</li>
          <li><strong>14 km³</strong> subsidence volume, 2006–2022 (Knight &amp; Lee, 2024)</li>
          <li><strong>9 m</strong> historic subsidence, 1925–1977 (USGS benchmark S661)</li>
          <li><strong>6.2 ft</strong> additional subsidence, 1988–2016 (Benchmark H1251)</li>
        </ul>
        <p class="source">Source: TRE Altamira InSAR (CNRA); USGS/DWR benchmark surveys</p>
      </div>
      <div class="media-panel">
        <img src="{a}/image4.png" alt="Subsidence rate map of the San Joaquin Valley"/>
        <p class="fig-cap">InSAR-derived subsidence intensity — purple/pink = highest vertical displacement</p>
      </div>
    </section>

    <!-- 2 Overdraft basins -->
    <section class="slide slide-split" data-n="2">
      <div class="text-panel">
        <p class="kicker">02 · Overdraft · DWR Bulletin 118</p>
        <h2>Critically overdrafted groundwater basins</h2>
        <div class="rule"></div>
        <p>California designates basins where pumping chronically exceeds recharge. The San Joaquin Valley contains the largest cluster of <strong>critically overdrafted</strong> basins — triggering mandatory SGMA sustainability plans.</p>
        <ul class="data-list">
          <li>Overdraft = extraction &gt; natural + artificial recharge</li>
          <li><strong>45 GSPs</strong> cover the San Joaquin Valley basin under SGMA</li>
          <li>Most counties still show <strong>groundwater decline</strong> post-2014 (CASGEM / MNM)</li>
        </ul>
        <div class="chart-sm" id="chartGwe"></div>
        <p class="source">Basin map: DWR Bulletin 118 · Bar chart: county Δ groundwater (ft), pre vs post SGMA</p>
      </div>
      <div class="media-panel">
        <img src="{a}/image2.png" alt="Critically overdrafted basins in California"/>
        <p class="fig-cap">DWR critically overdrafted basins — San Joaquin Valley highlighted in red</p>
      </div>
    </section>

    <!-- 3 Mechanism GIF -->
    <section class="slide slide-split" data-n="3">
      <div class="media-panel gif-panel">
        <img src="{a}/image9.gif" alt="Animation of aquifer compaction causing subsidence"/>
        <p class="fig-cap">Pumping lowers pore-water pressure → clay compacts → land subsides (irreversible)</p>
      </div>
      <div class="text-panel">
        <p class="kicker">03 · Mechanism</p>
        <h2>Subsidence stems from overdraft</h2>
        <div class="rule"></div>
        <p>When hydraulic head drops, effective stress on aquifer grains increases. Fine-grained layers compact permanently — reducing storage and sinking infrastructure built on a fixed grade.</p>
        <img class="inline-diagram" src="{a}/image11.png" alt="Before and after aqueduct subsidence cross-section"/>
        <ul class="data-list compact">
          <li>Aqueduct grade disrupted → <strong>choke points</strong>, ↓ conveyance capacity</li>
          <li>Well casings appear to <strong> protrude</strong> as land settles around them</li>
        </ul>
      </div>
    </section>

    <!-- 4 Costs -->
    <section class="slide slide-split" data-n="4">
      <div class="text-panel">
        <p class="kicker">04 · Economic damages</p>
        <h2>Subsidence costs billions in repairs</h2>
        <div class="rule"></div>
        <div class="metric-grid">
          <div class="metric"><span class="val">$889M</span><span class="lbl">Federal SJV canal repair funding (Mar 2026)</span></div>
          <div class="metric"><span class="val">$1.87B</span><span class="lbl">Housing value at risk — subsidence flooding (Jul 2025)</span></div>
          <div class="metric"><span class="val">$10–30k</span><span class="lbl">Household well repair/replacement (DWR dry-well DB)</span></div>
        </div>
        <p>Rigid infrastructure — canals, bridges, levees — requires continuous maintenance as land settles. Costs transfer overdraft externalities to agencies, irrigators, and households.</p>
        <p class="source">Infrastructure: federal appropriations · Wells: DWR HWSSRS</p>
      </div>
      <div class="media-panel">
        <img src="./assets/intro_slide1.png" alt="Canal repair"/>
        <p class="fig-cap">Canal embankment repair — Central Valley conveyance corridor</p>
      </div>
    </section>

    <!-- 5 Other impacts -->
    <section class="slide slide-content" data-n="5">
      <p class="kicker">05 · Externalities of overdraft</p>
      <h2>Subsidence is only one consequence</h2>
      <div class="rule"></div>
      <div class="impact-cols">
        <article class="impact-box">
          <h3>Flow patterns</h3>
          <p>Gravity-fed canals lose slope as land sinks → ↓ cfs capacity, ↑ energy and cost to deliver the same water.</p>
        </article>
        <article class="impact-box">
          <h3>Water quantity</h3>
          <p>Shallow domestic wells fail first. Dry-well reports (issue start): <strong id="sWellPre">—</strong> → <strong id="sWellPost">—</strong> across 8 counties (<strong id="sWellRatio">—</strong>×).</p>
        </article>
        <article class="impact-box">
          <h3>Water quality</h3>
          <p>Deeper pumping mobilizes salinity and legacy contaminants → ↓ drinking water quality, ↑ treatment costs.</p>
        </article>
        <article class="impact-box">
          <h3>Farm structure</h3>
          <p><strong id="sSmallLoss">—</strong> net small-farm loss (&lt;180 ac); large farms <strong id="sLargeGain">—</strong> — consolidation under water stress.</p>
        </article>
      </div>
      <div class="chart-row">
        <div class="chart-box"><h4>Dry-well reports by county</h4><div id="chartWells"></div></div>
        <div class="chart-box"><h4>Small vs large farms (net change)</h4><div id="chartFarms"></div></div>
      </div>
      <p class="source">DWR dry-well reporting · USDA NASS farm operations 2012 vs 2022</p>
    </section>

    <!-- 6 SGMA -->
    <section class="slide slide-split" data-n="6">
      <div class="text-panel">
        <p class="kicker">06 · Policy · SGMA 2014</p>
        <h2>Sustainable Groundwater Management Act</h2>
        <div class="rule"></div>
        <p>California's first statewide groundwater law requires local <strong>Groundwater Sustainability Agencies</strong> to adopt <strong>Groundwater Sustainability Plans</strong> achieving sustainable yield by 2040 (2042 for critically overdrafted basins).</p>
        <ul class="data-list">
          <li><strong id="sGspApproved">24</strong> approved · 14 under review · 6 post state intervention · 1 incomplete</li>
          <li>DWR evaluates GSPs; state intervention if plans are inadequate</li>
          <li>Kings County basin probation (2024) — first enforcement under SGMA</li>
        </ul>
        <div class="chart-sm" id="chartGsp"></div>
      </div>
      <div class="media-panel stack">
        <img src="{a}/image3.png" alt="DWR GSP decisions news"/>
        <img src="{a}/image7.png" alt="SGMA impact on growers news"/>
      </div>
    </section>

    <!-- 7 GSA / GSP / Basins -->
    <section class="slide slide-content" data-n="7">
      <p class="kicker">07 · SGMA institutions</p>
      <h2>Basins, subbasins, GSAs &amp; GSPs — how they relate</h2>
      <div class="rule"></div>
      <div class="inst-layout">
        <div class="inst-defs">
          <article><h3>Groundwater basin</h3><p>DWR Bulletin 118 hydrologic unit — e.g. the <strong>San Joaquin Valley</strong> is one major basin containing many subbasins.</p></article>
          <article><h3>Subbasin</h3><p>Smaller hydrologic subdivision (<strong>18</strong> in this dataset). GSP metrics aggregate here for comparison.</p></article>
          <article><h3>GSA — Groundwater Sustainability Agency</h3><p>Local public agency with authority to regulate pumping. <strong>132 GSA boundaries</strong> overlap the valley.</p></article>
          <article><h3>GSP — Groundwater Sustainability Plan</h3><p>Plan area document: how the subbasin reaches sustainable yield. <strong>45 GSP polygons</strong> in the SJV.</p></article>
        </div>
        <div class="inst-visual">
          <img src="{a}/image6.png" alt="San Joaquin Valley basin and subbasin boundaries"/>
          <p class="fig-cap">SJV basin subdivided into subbasins (DWR / GSA boundaries)</p>
          <div class="chart-sm" id="chartSubbasin"></div>
        </div>
      </div>
    </section>

    <!-- 8 SGMA verdict -->
    <section class="slide slide-content slide-verdict" data-n="8">
      <p class="kicker">08 · Evidence · Is SGMA working?</p>
      <h2>Mixed verdict: governance progress, persistent harm</h2>
      <div class="rule"></div>
      <div class="verdict-cols">
        <div class="verdict-no">
          <h3>Not yet working — residents &amp; physical outcomes</h3>
          <ul>
            <li>Subsidence continues in hotspots (InSAR; Knight &amp; Lee 2024)</li>
            <li>Dry-well reports: <strong id="vWell">—</strong> — harm to residents rising, not falling</li>
            <li><strong id="vGw">—</strong> counties still show groundwater decline</li>
            <li><strong id="vSmall">—</strong> small farms lost; large farms expanding in 5 of 8 counties</li>
            <li>Equity: well failures × CalEnviroScreen burden in disadvantaged communities</li>
          </ul>
        </div>
        <div class="verdict-yes">
          <h3>Partial progress — policy &amp; agricultural adjustment</h3>
          <ul>
            <li><strong id="vGsp">—</strong> GSPs approved — institutional framework in place</li>
            <li>Fallow ↔ Δ groundwater: <strong>R² = 0.95</strong> — basins adjusting acreage to drawdown</li>
            <li>Fallow acreage fell sharply in Kern (−262k ac) and Tulare (−83k ac) 2014–2022</li>
            <li>Some crop shifts (alfalfa ↓, nuts ↑) show economic adaptation</li>
          </ul>
        </div>
      </div>
      <div class="chart-row triple">
        <div class="chart-box"><h4>Well failures pre vs post SGMA</h4><div id="chartVerdictWells"></div></div>
        <div class="chart-box"><h4>Small farm loss by county</h4><div id="chartVerdictSmall"></div></div>
        <div class="chart-box"><h4>Fallow Δ vs groundwater Δ (R²=0.95)</h4><div id="chartScatter"></div></div>
      </div>
      <div class="verdict-bottom">
        <img src="{a}/image10.png" alt="Irrigated crop acreage trends 2013-2020"/>
        <p><strong>Conclusion:</strong> SGMA has built governance and induced fallowing in stressed basins, but has <em>not</em> reduced domestic well failures or universal drawdown — and farm consolidation continues. Production persists via permanent crops, maintaining pumping pressure.</p>
      </div>
    </section>

    <!-- 9 Research question -->
    <section class="slide slide-quote" data-n="9">
      <div class="slide-body center dark">
        <p class="kicker">Research question</p>
        <blockquote>Will SGMA-induced groundwater regulation stop the consequences of overextraction, or shift the costs onto small farmers and disadvantaged communities?</blockquote>
        <p class="lead">County panel (n=8) · DWR wells · CASGEM groundwater · CDL fallow · NASS farm size · CalEnviroScreen</p>
      </div>
    </section>

    <!-- 10 Atlas -->
    <section class="slide slide-title" data-n="10">
      <div class="slide-body center light" style="background:var(--bg)">
        <p class="kicker">Interactive data atlas</p>
        <h1 style="color:var(--navy)">SGMA Equity Pathways — San Joaquin Valley</h1>
        <p class="lead" style="color:var(--muted)">Maps · scatter plots · county/GSP cards · Pre / Post / Δ comparisons</p>
        <a class="btn" href="index.html">Open full scrolly atlas →</a>
        <a class="btn secondary" href="index.html">Open full scrolly atlas →</a>
        <p class="source" style="margin-top:24px">Alexandra Beyret · ECON 30 · Questions welcome</p>
      </div>
    </section>

  </main>
  <script src="thesis_presentation.js"></script>
</body>
</html>
"""


def embed_data_in_html(data: dict) -> None:
    html = OUT_TEMPLATE.read_text(encoding="utf-8")
    payload = f'<script>window.THESIS_DATA = {json.dumps(data)};</script>'
    html = html.replace(DATA_MARKER, payload)
    OUT_HTML.write_text(html, encoding="utf-8")


def main() -> None:
    data = load_data()
    OUT_DATA.write_text(f"window.THESIS_DATA = {json.dumps(data, indent=2)};\n", encoding="utf-8")
    embed_data_in_html(data)
    print(f"Wrote {OUT_DATA}")
    print(f"Updated {OUT_HTML} with inline data")
    print(f"Open {OUT_HTML} directly in a browser (double-click or open_thesis_presentation.bat)")
    print(f"Wells {data['summary']['well_pre']}->{data['summary']['well_post']}")


if __name__ == "__main__":
    main()
