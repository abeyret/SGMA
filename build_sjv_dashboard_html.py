"""
Build data/clean/sjv_dashboard.html: self-contained 3-panel dashboard.

Left: Leaflet county choropleth map (select county on click)
Center: Chart.js scatter (four correlation views via dropdown)
Right: Stat card for selected county, with period toggle:
  Pre-SGMA, Post-SGMA, Change (delta)

Data are embedded inline so the HTML works without local files.

Requires: geopandas, pandas
"""

from __future__ import annotations

import html
import json
import sys
import urllib.request
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import mapping

ROOT = Path(__file__).resolve().parent
OUT_HTML = ROOT / "data/clean/sjv_dashboard.html"
CACHE_ZIP = ROOT / "data/clean/_cache_cb_2022_us_county_500k.zip"
CENSUS_COUNTY_ZIP = "https://www2.census.gov/geo/tiger/GENZ2022/shp/cb_2022_us_county_500k.zip"

SJV_FIPS5 = ("06019", "06029", "06031", "06039", "06047", "06077", "06099", "06107")
COUNTY_ORDER = {
    "06019": "Fresno",
    "06029": "Kern",
    "06031": "Kings",
    "06039": "Madera",
    "06047": "Merced",
    "06077": "San Joaquin",
    "06099": "Stanislaus",
    "06107": "Tulare",
}
FIPS5_TO_NORM = {f: COUNTY_ORDER[f].lower().replace(" ", "_") for f in SJV_FIPS5}

BUCKET_MAP: dict[str, str] = {
    "AREA OPERATED: (1.0 TO 9.9 ACRES)": "under_50",
    "AREA OPERATED: (10.0 TO 49.9 ACRES)": "under_50",
    "AREA OPERATED: (50.0 TO 69.9 ACRES)": "50_179",
    "AREA OPERATED: (70.0 TO 99.9 ACRES)": "50_179",
    "AREA OPERATED: (100 TO 139 ACRES)": "50_179",
    "AREA OPERATED: (140 TO 179 ACRES)": "50_179",
    "AREA OPERATED: (180 TO 219 ACRES)": "180_499",
    "AREA OPERATED: (220 TO 259 ACRES)": "180_499",
    "AREA OPERATED: (260 TO 499 ACRES)": "180_499",
    "AREA OPERATED: (500 TO 999 ACRES)": "500_999",
    "AREA OPERATED: (1,000 TO 1,999 ACRES)": "1000_plus",
    "AREA OPERATED: (2,000 OR MORE ACRES)": "1000_plus",
}
BUCKET_ORDER = ["under_50", "50_179", "180_499", "500_999", "1000_plus"]


def parse_nass_int(v: object) -> int:
    if v is None:
        return 0
    s = str(v).strip().replace(",", "")
    if not s or s in {"**", "(D)"}:
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def load_farm_year_totals(path: Path) -> dict[tuple[str, int], dict]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    acc: dict[tuple[str, int], dict[str, int]] = {}
    for r in data.get("records", []):
        if r.get("commodity_desc") != "FARM OPERATIONS":
            continue
        if r.get("domain_desc") != "AREA OPERATED":
            continue
        if r.get("statisticcat_desc") != "OPERATIONS":
            continue
        yr = int(r.get("year", 0))
        if yr not in (2012, 2022):
            continue
        cat = str(r.get("domaincat_desc", "")).strip()
        b = BUCKET_MAP.get(cat)
        if not b:
            continue
        f5 = str(r.get("county_fips5", "")).zfill(5)
        if f5 not in SJV_FIPS5:
            continue
        key = (f5, yr)
        if key not in acc:
            acc[key] = {x: 0 for x in BUCKET_ORDER}
        acc[key][b] += parse_nass_int(r.get("Value"))
    out: dict[tuple[str, int], dict] = {}
    for key, buckets in acc.items():
        total = sum(buckets[b] for b in BUCKET_ORDER)
        small = buckets["under_50"] + buckets["50_179"]
        large = buckets["500_999"] + buckets["1000_plus"]
        out[key] = {
            **{b: buckets[b] for b in BUCKET_ORDER},
            "total_farms": int(total),
            "small_farms": int(small),
            "large_farms": int(large),
        }
    return out


def load_fallow_by_county(path: Path, year: int) -> dict[str, float]:
    df = pd.read_csv(path, dtype={"county_fips5": str})
    df["county_fips5"] = df["county_fips5"].astype(str).str.zfill(5)
    sub = df.loc[
        (df["cdl_year"] == year)
        & (df["category"].astype(str).str.strip() == "Fallow/Idle Cropland")
    ]
    g = sub.groupby("county_fips5", as_index=False)["acreage"].sum()
    return {row.county_fips5: float(row.acreage) for _, row in g.iterrows()}


def load_acs_income(path: Path) -> dict[str, float]:
    df = pd.read_csv(path, dtype={"county_fips5": str})
    df["county_fips5"] = df["county_fips5"].astype(str).str.zfill(5)
    out: dict[str, float] = {}
    for _, row in df.iterrows():
        f5 = row["county_fips5"]
        v = row.get("B19013_001E")
        if pd.notna(v) and str(v) not in {"", "-666666666"}:
            try:
                out[f5] = float(v)
            except (TypeError, ValueError):
                pass
    return out


def norm_county(name: object) -> str | None:
    if pd.isna(name):
        return None
    return str(name).strip().lower()


def find_date_column(columns: list[str]) -> str | None:
    for c in columns:
        cl = c.lower().replace(" ", "_")
        if "date" in cl:
            return c
    for c in columns:
        if c.lower() in ("year", "yr", "measurement_year"):
            return c
    return None


def year_from_col(df: pd.DataFrame, date_col: str) -> pd.Series:
    if date_col.lower() in ("year", "yr", "measurement_year"):
        return pd.to_numeric(df[date_col], errors="coerce").astype("Int64")
    dt = pd.to_datetime(df[date_col], errors="coerce", utc=True)
    return dt.dt.year.astype("Int64")


def aggregate_gwe_periods(
    ms_path: Path, fips_lookup: dict[str, str], chunksize: int = 200_000
) -> tuple[dict[str, float], dict[str, float], str]:
    header = pd.read_csv(ms_path, nrows=0).columns.tolist()
    date_col = find_date_column(header)
    if date_col is None or "county_name" not in header or "gwe" not in header:
        return {}, {}, "Groundwater: no usable date column; using all-time mean for both periods."

    usecols = ["county_name", "gwe", date_col]
    pre_sums: dict[str, float] = {}
    pre_n: dict[str, int] = {}
    post_sums: dict[str, float] = {}
    post_n: dict[str, int] = {}

    reader = pd.read_csv(
        ms_path,
        chunksize=chunksize,
        usecols=usecols,
        dtype={"county_name": "string"},
        engine="c",
    )
    for chunk in reader:
        chunk["gwe"] = pd.to_numeric(chunk["gwe"], errors="coerce")
        chunk = chunk.dropna(subset=["gwe"])
        cn = chunk["county_name"].map(norm_county)
        chunk = chunk.loc[cn.notna()].copy()
        chunk["_f5"] = cn.map(lambda x: fips_lookup.get(x))
        chunk = chunk.loc[chunk["_f5"].notna()].copy()
        y = year_from_col(chunk, date_col)
        chunk["_yr"] = y
        chunk = chunk.loc[chunk["_yr"].notna()].copy()
        yr = chunk["_yr"].astype(int)

        m_pre = yr.isin([2012, 2013, 2014])
        m_post = yr.isin([2018, 2019, 2020, 2021, 2022])
        if m_pre.any():
            p = chunk.loc[m_pre]
            for f5, grp in p.groupby("_f5")["gwe"]:
                pre_sums[f5] = pre_sums.get(f5, 0.0) + float(grp.sum())
                pre_n[f5] = pre_n.get(f5, 0) + int(grp.count())
        if m_post.any():
            p = chunk.loc[m_post]
            for f5, grp in p.groupby("_f5")["gwe"]:
                post_sums[f5] = post_sums.get(f5, 0.0) + float(grp.sum())
                post_n[f5] = post_n.get(f5, 0) + int(grp.count())

    def finalize(sums: dict[str, float], counts: dict[str, int]) -> dict[str, float]:
        out: dict[str, float] = {}
        for f5 in set(sums.keys()) | set(counts.keys()):
            n = counts.get(f5, 0)
            if n > 0:
                out[f5] = sums[f5] / n
        return out

    return (
        finalize(pre_sums, pre_n),
        finalize(post_sums, post_n),
        "Groundwater means are averaged from CASGEM dates in 2012–2014 (Pre) and 2018–2022 (Post).",
    )


def aggregate_gwe_all_time(ms_path: Path, fips_lookup: dict[str, str]) -> dict[str, float]:
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    reader = pd.read_csv(
        ms_path,
        chunksize=200_000,
        usecols=["county_name", "gwe"],
        dtype={"county_name": "string"},
        engine="c",
    )
    for chunk in reader:
        chunk["gwe"] = pd.to_numeric(chunk["gwe"], errors="coerce")
        chunk = chunk.dropna(subset=["gwe"])
        cn = chunk["county_name"].map(norm_county)
        chunk = chunk.loc[cn.notna()].copy()
        chunk["_f5"] = cn.map(lambda x: fips_lookup.get(x))
        chunk = chunk.loc[chunk["_f5"].notna()]
        for f5, grp in chunk.groupby("_f5")["gwe"]:
            sums[f5] = sums.get(f5, 0.0) + float(grp.sum())
            counts[f5] = counts.get(f5, 0) + int(grp.count())
    out: dict[str, float] = {}
    for f5 in sums:
        n = counts.get(f5, 0)
        if n:
            out[f5] = sums[f5] / n
    return out


def load_county_boundaries() -> gpd.GeoDataFrame:
    zip_path = CACHE_ZIP if CACHE_ZIP.is_file() else None
    if zip_path is None:
        CACHE_ZIP.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {CENSUS_COUNTY_ZIP} ...")
        urllib.request.urlretrieve(CENSUS_COUNTY_ZIP, CACHE_ZIP)
        zip_path = CACHE_ZIP

    src = f"zip://{zip_path}!cb_2022_us_county_500k.shp"
    counties = gpd.read_file(src)
    counties["GEOID"] = counties["GEOID"].astype(str).str.zfill(5)
    sjv = counties.loc[counties["GEOID"].isin(SJV_FIPS5)].copy()
    sjv["geometry"] = sjv.geometry.simplify(0.003, preserve_topology=True)
    if sjv.crs is not None and not sjv.crs.is_geographic:
        sjv = sjv.to_crs(4326)
    elif sjv.crs is None:
        sjv = sjv.set_crs(4326)
    return sjv


def load_gsa_sjv_outline() -> gpd.GeoDataFrame:
    candidates = [
        ROOT / "data/raw/gsa_boundaries/i03_Groundwater_Sustainability_Agencies.geojson",
        ROOT / "i03_Groundwater_Sustainability_Agencies.geojson",
    ]
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        raise FileNotFoundError("GSA GeoJSON not found.")
    gsa = gpd.read_file(path)
    mask = gsa["Basin_Name"].astype(str).str.contains("SAN JOAQUIN VALLEY", case=False, na=False)
    gsa = gsa.loc[mask].copy()
    gsa["geometry"] = gsa.geometry.simplify(0.004, preserve_topology=True)
    if gsa.crs is not None and not gsa.crs.is_geographic:
        gsa = gsa.to_crs(4326)
    elif gsa.crs is None:
        gsa = gsa.set_crs(4326)
    return gsa[[ "geometry" ]].copy()


def gsa_to_geojson(gsa: gpd.GeoDataFrame) -> dict:
    slim = gsa.copy()
    slim["name"] = "GSA"
    return json.loads(slim.to_json())


def build_county_fc(
    counties_gdf: gpd.GeoDataFrame,
    summary: pd.DataFrame,
    farm: dict[tuple[str, int], dict],
    fallow12: dict[str, float],
    fallow22: dict[str, float],
    inc14: dict[str, float],
    inc21: dict[str, float],
    gwe_pre: dict[str, float],
    gwe_post: dict[str, float],
    gwe_note: str,
) -> dict:
    features = []
    for f5 in sorted(SJV_FIPS5, key=lambda x: COUNTY_ORDER[x]):
        row = summary.loc[summary["county_fips5"].astype(str).str.zfill(5) == f5]
        if row.empty:
            continue
        row0 = row.iloc[0]
        wells = int(float(row0["well_failure_count"]))
        gmean = float(row0["mean_groundwater_elevation_ft"])

        def gwe_for(period: str) -> tuple[float, str]:
            if period == "pre":
                v = gwe_pre.get(f5)
                src = "measurements" if v is not None else "summary"
                return float(v if v is not None else gmean), src
            v = gwe_post.get(f5)
            src = "measurements" if v is not None else "summary"
            return float(v if v is not None else gmean), src

        gpre, spre = gwe_for("pre")
        gpost, spost = gwe_for("post")

        f12 = farm.get((f5, 2012), {})
        f22 = farm.get((f5, 2022), {})
        total12 = int(f12.get("total_farms", 0))
        total22 = int(f22.get("total_farms", 0))
        small12 = int(f12.get("small_farms", 0))
        small22 = int(f22.get("small_farms", 0))
        large12 = int(f12.get("large_farms", 0))
        large22 = int(f22.get("large_farms", 0))

        def pct_small(s: int, t: int) -> float:
            return round(100.0 * s / t, 2) if t > 0 else 0.0

        pre = {
            "gwe_ft": round(gpre, 2),
            "gwe_src": spre,
            "fallow_acres": round(fallow12.get(f5, 0.0), 1),
            "well_failures": wells,
            "total_farms": total12,
            "small_farms": small12,
            "large_farms": large12,
            "pct_small": pct_small(small12, total12),
            "median_income": int(inc14[f5]) if f5 in inc14 else None,
        }
        post = {
            "gwe_ft": round(gpost, 2),
            "gwe_src": spost,
            "fallow_acres": round(fallow22.get(f5, 0.0), 1),
            "well_failures": wells,
            "total_farms": total22,
            "small_farms": small22,
            "large_farms": large22,
            "pct_small": pct_small(small22, total22),
            "median_income": int(inc21[f5]) if f5 in inc21 else None,
        }

        geom = counties_gdf.loc[counties_gdf["GEOID"] == f5, "geometry"]
        if geom.empty:
            continue

        features.append(
            {
                "type": "Feature",
                "id": f5,
                "properties": {
                    "fips5": f5,
                    "name": COUNTY_ORDER[f5],
                    "gwe_embed_note": gwe_note,
                    "pre": pre,
                    "post": post,
                },
                "geometry": mapping(geom.iloc[0]),
            }
        )
    return {"type": "FeatureCollection", "features": features}


def render_dashboard_html(counties_fc: dict, gsa_fc: dict, meta: dict) -> str:
    payload = {"counties": counties_fc, "gsa": gsa_fc, "meta": meta}
    json_text = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    title_esc = html.escape(meta.get("title", "SGMA Equity Atlas"))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title_esc}</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
        integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="" />
  <style>
    * {{ box-sizing: border-box; }}
    html, body {{ margin: 0; height: 100%; background: #ffffff; font-family: system-ui, Segoe UI, Roboto, sans-serif; color: #111827; }}
    .wrap {{ height: 100%; display: flex; flex-direction: column; }}
    .topbar {{
      display: flex; align-items: center; justify-content: space-between;
      padding: 14px 16px; border-bottom: 1px solid #e5e7eb;
      position: sticky; top: 0; background: #fff; z-index: 10;
    }}
    .title {{
      display: flex; flex-direction: column; gap: 2px;
    }}
    .title h1 {{ margin: 0; font-size: 1.05rem; font-weight: 750; letter-spacing: 0.2px; }}
    .title p {{ margin: 0; font-size: 0.78rem; color: #6b7280; }}
    .toggles {{
      display: inline-flex; border: 1px solid #d1d5db; border-radius: 10px; overflow: hidden;
    }}
    .toggles button {{
      border: none; background: #f9fafb; color: #374151; padding: 10px 12px; cursor: pointer;
      font-size: 0.82rem; font-weight: 650; min-width: 132px;
    }}
    .toggles button.active {{ background: #2563eb; color: #fff; }}
    .content {{
      flex: 1; display: grid; grid-template-columns: 1.05fr 1.4fr 1.05fr; gap: 12px;
      padding: 12px; min-height: 0;
    }}
    .panel {{
      border: 1px solid #e5e7eb; border-radius: 12px; background: #fff;
      box-shadow: 0 1px 6px rgba(0,0,0,0.04);
      display: flex; flex-direction: column; min-height: 0;
    }}
    .panel .hdr {{
      padding: 12px 12px 10px 12px; border-bottom: 1px solid #eef2f7;
      display: flex; align-items: center; justify-content: space-between; gap: 10px;
    }}
    .panel .hdr h2 {{ margin: 0; font-size: 0.92rem; font-weight: 750; color: #111827; }}
    .panel .body {{ padding: 12px; flex: 1; min-height: 0; }}
    #map {{ width: 100%; height: 340px; border-radius: 10px; }}
    .hint {{ font-size: 0.75rem; color: #6b7280; line-height: 1.25; margin-top: 10px; }}
    select {{
      font-size: 0.82rem; padding: 8px 10px; border-radius: 10px; border: 1px solid #d1d5db;
      background: #fff; color: #111827; min-width: 320px;
    }}
    #scatterWrap {{ height: 100%; min-height: 420px; }}
    #scatter {{ width: 100% !important; height: 100% !important; }}
    .card {{
      display: flex; flex-direction: column; gap: 10px;
    }}
    .county-name {{ font-size: 1.0rem; font-weight: 800; }}
    .pill {{
      display: inline-flex; align-items: center; gap: 6px;
      font-size: 0.74rem; color: #374151; background: #f3f4f6; border: 1px solid #e5e7eb;
      padding: 6px 8px; border-radius: 999px;
    }}
    .grid {{
      display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
    }}
    .stat {{
      border: 1px solid #eef2f7; border-radius: 12px; padding: 10px 10px;
      background: #ffffff;
    }}
    .stat .k {{ font-size: 0.74rem; color: #6b7280; margin-bottom: 6px; }}
    .stat .v {{ font-size: 0.98rem; font-weight: 800; font-variant-numeric: tabular-nums; }}
    .stat .sub {{ margin-top: 4px; font-size: 0.72rem; color: #6b7280; }}
    .footer-note {{ margin-top: 10px; font-size: 0.72rem; color: #6b7280; line-height: 1.25; }}
    .leaflet-container {{ background: #f8fafc; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <div class="title">
        <h1>SGMA Equity Atlas — San Joaquin Valley</h1>
        <p>Click a county on the map or a dot on the scatter plot to inspect before/after metrics.</p>
      </div>
      <div class="toggles" role="group" aria-label="Period toggle">
        <button type="button" id="btn-pre" class="active">Pre-SGMA</button>
        <button type="button" id="btn-post">Post-SGMA</button>
        <button type="button" id="btn-delta">Change (Δ)</button>
      </div>
    </div>

    <div class="content">
      <div class="panel">
        <div class="hdr">
          <h2>Map (county selection)</h2>
          <span class="pill" id="mapMetricPill">Choropleth: Groundwater (ft)</span>
        </div>
        <div class="body">
          <div id="map"></div>
          <div class="hint">
            Choropleth follows the current scatter X metric. Selection is shared with the scatter and the stat card.
          </div>
        </div>
      </div>

      <div class="panel">
        <div class="hdr">
          <h2>Correlation explorer</h2>
          <select id="viewSelect" aria-label="Choose scatter view">
            <option value="v1">Pre-SGMA groundwater elevation vs fallow acreage change</option>
            <option value="v2">Well failure count vs median income 2021</option>
            <option value="v3">Small farm loss (2012–2022) vs groundwater change</option>
            <option value="v4">Pre-SGMA income vs well failures</option>
          </select>
        </div>
        <div class="body" style="padding: 10px 12px;">
          <div id="scatterWrap">
            <canvas id="scatter"></canvas>
          </div>
          <div class="footer-note" id="scatterNote"></div>
        </div>
      </div>

      <div class="panel">
        <div class="hdr">
          <h2>County snapshot</h2>
          <span class="pill" id="periodPill">Period: Pre-SGMA</span>
        </div>
        <div class="body">
          <div class="card">
            <div class="county-name" id="countyTitle">Select a county</div>
            <div class="grid">
              <div class="stat"><div class="k">Well failures</div><div class="v" id="s-wells">—</div><div class="sub">Reports (all years)</div></div>
              <div class="stat"><div class="k">Groundwater elevation</div><div class="v" id="s-gwe">—</div><div class="sub" id="s-gwe-sub">ft (source: —)</div></div>
              <div class="stat"><div class="k">Fallow cropland</div><div class="v" id="s-fallow">—</div><div class="sub">acres</div></div>
              <div class="stat"><div class="k">Median income</div><div class="v" id="s-inc">—</div><div class="sub">$</div></div>
              <div class="stat"><div class="k">Total farms</div><div class="v" id="s-farms">—</div><div class="sub">operations</div></div>
              <div class="stat"><div class="k">Small / Large farms</div><div class="v" id="s-farm-split">—</div><div class="sub">small &lt;180 ac; large 500+ ac</div></div>
            </div>
            <div class="footer-note" id="countyNote"></div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
          integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
  <script>
  const ATLAS = {json_text};

  // --- selection + period state ---
  let period = 'pre'; // pre | post | delta
  let selectedFips = null;

  function countyByFips(fips) {{
    return ATLAS.counties.features.find(f => f.properties.fips5 === fips) || null;
  }}

  function fmtInt(v) {{
    if (v === null || v === undefined || Number.isNaN(v)) return 'n/a';
    return Math.round(v).toLocaleString();
  }}
  function fmtFloat(v, d=1) {{
    if (v === null || v === undefined || Number.isNaN(v)) return 'n/a';
    return Number(v).toFixed(d);
  }}
  function fmtMoney(v) {{
    if (v === null || v === undefined || Number.isNaN(v)) return 'n/a';
    return '$' + Math.round(v).toLocaleString();
  }}
  function delta(a, b) {{
    if (a === null || a === undefined || b === null || b === undefined) return null;
    if (Number.isNaN(a) || Number.isNaN(b)) return null;
    return b - a;
  }}

  function metricsFor(feat) {{
    const pre = feat.properties.pre;
    const post = feat.properties.post;
    const d = {{
      wells: pre.well_failures,
      gwe_pre: pre.gwe_ft, gwe_post: post.gwe_ft,
      fallow_pre: pre.fallow_acres, fallow_post: post.fallow_acres,
      inc_pre: pre.median_income, inc_post: post.median_income,
      farms_pre: pre.total_farms, farms_post: post.total_farms,
      small_pre: pre.small_farms, small_post: post.small_farms,
      large_pre: pre.large_farms, large_post: post.large_farms,
      gwe_change: delta(pre.gwe_ft, post.gwe_ft),
      fallow_change: delta(pre.fallow_acres, post.fallow_acres),
      income_change: delta(pre.median_income, post.median_income),
      total_farms_change: delta(pre.total_farms, post.total_farms),
      small_farm_loss: delta(post.small_farms, pre.small_farms) ? (pre.small_farms - post.small_farms) : null,
      small_change: delta(pre.small_farms, post.small_farms),
      large_change: delta(pre.large_farms, post.large_farms),
    }};
    return d;
  }}

  // --- scatter views ---
  const VIEWS = {{
    v1: {{
      title: 'Pre-SGMA groundwater elevation vs fallow acreage change',
      xLabel: 'Pre-SGMA mean groundwater elevation (ft)',
      yLabel: 'Fallow acreage change (2022 - 2012, acres)',
      note: 'Groundwater uses CASGEM period means when dates are available; fallow uses CDL fallow/idle cropland.',
      x: (feat) => feat.properties.pre.gwe_ft,
      y: (feat) => metricsFor(feat).fallow_change,
      mapMetric: (feat) => feat.properties.pre.gwe_ft,
      mapMetricLabel: 'Groundwater (ft)',
    }},
    v2: {{
      title: 'Well failure count vs median income 2021',
      xLabel: 'Median income 2021 ($)',
      yLabel: 'Well failure reports (count)',
      note: 'Well failures are taken from the county summary (cumulative reports).',
      x: (feat) => feat.properties.post.median_income,
      y: (feat) => feat.properties.pre.well_failures,
      mapMetric: (feat) => feat.properties.post.median_income,
      mapMetricLabel: 'Median income 2021 ($)',
    }},
    v3: {{
      title: 'Small farm loss (2012–2022) vs groundwater change',
      xLabel: 'Groundwater change (Post - Pre, ft)',
      yLabel: 'Small farm loss (2012 - 2022, count)',
      note: 'Small farms are <180 acres (NASS disjoint size bins). Positive Y means fewer small farms in 2022.',
      x: (feat) => metricsFor(feat).gwe_change,
      y: (feat) => metricsFor(feat).small_farm_loss,
      mapMetric: (feat) => metricsFor(feat).gwe_change,
      mapMetricLabel: 'Groundwater change (ft)',
    }},
    v4: {{
      title: 'Pre-SGMA income vs well failures',
      xLabel: 'Median income 2014 ($)',
      yLabel: 'Well failure reports (count)',
      note: 'Income uses ACS 2014 estimates; well failures are from the county summary.',
      x: (feat) => feat.properties.pre.median_income,
      y: (feat) => feat.properties.pre.well_failures,
      mapMetric: (feat) => feat.properties.pre.median_income,
      mapMetricLabel: 'Median income 2014 ($)',
    }},
  }};

  let currentViewId = 'v1';

  function extent(values) {{
    let lo = Infinity, hi = -Infinity;
    for (const v of values) {{
      if (v === null || v === undefined || Number.isNaN(v)) continue;
      lo = Math.min(lo, v);
      hi = Math.max(hi, v);
    }}
    if (!isFinite(lo) || !isFinite(hi)) return [0, 1];
    if (lo === hi) {{ lo -= 1; hi += 1; }}
    return [lo, hi];
  }}

  function lerp(a,b,t) {{ return a + (b-a)*t; }}
  function colorRamp(t) {{
    // subtle blue ramp
    const r = Math.round(lerp(239, 37, t));
    const g = Math.round(lerp(246, 99, t));
    const b = Math.round(lerp(255, 235, t));
    return `rgb(${{r}},${{g}},${{b}})`;
  }}

  function styleForMapFeature(feat) {{
    const view = VIEWS[currentViewId];
    const vals = ATLAS.counties.features.map(f => view.mapMetric(f));
    const [lo, hi] = extent(vals);
    const v = view.mapMetric(feat);
    let t = 0.5;
    if (v !== null && v !== undefined && !Number.isNaN(v)) {{
      t = (v - lo) / (hi - lo);
      t = Math.max(0, Math.min(1, t));
    }}
    const isSel = selectedFips && feat.properties.fips5 === selectedFips;
    return {{
      fillColor: colorRamp(t),
      fillOpacity: 0.9,
      color: isSel ? '#111827' : '#6b7280',
      weight: isSel ? 2.2 : 0.9,
      opacity: 1.0
    }};
  }}

  // --- Leaflet map ---
  const map = L.map('map', {{ zoomControl: true, attributionControl: false }}).setView([36.6, -119.8], 7.7);
  L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
    maxZoom: 19,
  }}).addTo(map);

  const gsaLayer = L.geoJSON(ATLAS.gsa, {{
    style: {{ color: '#1d4ed8', weight: 1.0, opacity: 0.9, fillOpacity: 0, fill: false }},
    interactive: false
  }}).addTo(map);

  const countyLayer = L.geoJSON(ATLAS.counties, {{
    style: styleForMapFeature,
    onEachFeature: function (feature, layer) {{
      layer.on('click', function () {{
        setSelected(feature.properties.fips5);
      }});
      layer.on('mouseover', function () {{
        if (!selectedFips || selectedFips !== feature.properties.fips5) {{
          layer.setStyle({{ weight: 1.6 }});
        }}
      }});
      layer.on('mouseout', function () {{
        refreshMapStyles();
      }});
    }}
  }}).addTo(map);

  map.fitBounds(countyLayer.getBounds(), {{ padding: [8, 8] }});
  gsaLayer.bringToFront();

  function refreshMapStyles() {{
    countyLayer.eachLayer(function (layer) {{
      layer.setStyle(styleForMapFeature(layer.feature));
    }});
  }}

  // --- Chart.js scatter ---
  const labelPlugin = {{
    id: 'countyLabels',
    afterDatasetsDraw(chart, args, opts) {{
      const {{ ctx }} = chart;
      const meta = chart.getDatasetMeta(0);
      ctx.save();
      ctx.font = '11px system-ui, Segoe UI, Roboto, sans-serif';
      ctx.fillStyle = '#374151';
      ctx.textBaseline = 'middle';
      for (let i = 0; i < meta.data.length; i++) {{
        const pt = meta.data[i];
        const d = chart.data.datasets[0].data[i];
        if (!pt || !d || !d._label) continue;
        const x = pt.x + 8;
        const y = pt.y;
        ctx.fillText(d._label, x, y);
      }}
      ctx.restore();
    }}
  }};

  const ctx = document.getElementById('scatter').getContext('2d');
  const scatterChart = new Chart(ctx, {{
    type: 'scatter',
    data: {{
      datasets: [{{
        label: 'Counties',
        data: [],
        pointRadius: 5,
        pointHoverRadius: 7,
        pointBackgroundColor: '#2563eb',
        pointBorderColor: '#ffffff',
        pointBorderWidth: 1.5,
      }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          callbacks: {{
            label: (ctx) => {{
              const p = ctx.raw;
              return `${{p._label}}: (${{p.x}}, ${{p.y}})`;
            }}
          }}
        }}
      }},
      scales: {{
        x: {{ title: {{ display: true, text: '' }}, grid: {{ color: '#eef2f7' }} }},
        y: {{ title: {{ display: true, text: '' }}, grid: {{ color: '#eef2f7' }} }},
      }},
      onClick: (evt) => {{
        const points = scatterChart.getElementsAtEventForMode(evt, 'nearest', {{ intersect: true }}, true);
        if (!points.length) return;
        const idx = points[0].index;
        const d = scatterChart.data.datasets[0].data[idx];
        if (d && d._fips) setSelected(d._fips);
      }}
    }},
    plugins: [labelPlugin],
  }});

  function updateScatter() {{
    const view = VIEWS[currentViewId];
    const pts = [];
    for (const f of ATLAS.counties.features) {{
      const x = view.x(f);
      const y = view.y(f);
      if (x === null || x === undefined || y === null || y === undefined) continue;
      if (Number.isNaN(x) || Number.isNaN(y)) continue;
      pts.push({{ x, y, _label: f.properties.name, _fips: f.properties.fips5 }});
    }}
    scatterChart.data.datasets[0].data = pts;
    scatterChart.options.scales.x.title.text = view.xLabel;
    scatterChart.options.scales.y.title.text = view.yLabel;
    scatterChart.update();

    document.getElementById('scatterNote').textContent = view.note;
    document.getElementById('mapMetricPill').textContent = 'Choropleth: ' + view.mapMetricLabel;
    refreshMapStyles();
  }}

  function highlightScatterSelection() {{
    const ds = scatterChart.data.datasets[0];
    ds.pointBackgroundColor = ds.data.map(p => (selectedFips && p._fips === selectedFips) ? '#111827' : '#2563eb');
    ds.pointRadius = ds.data.map(p => (selectedFips && p._fips === selectedFips) ? 7 : 5);
    scatterChart.update();
  }}

  // --- stat card ---
  function updateCard() {{
    const pill = document.getElementById('periodPill');
    pill.textContent = period === 'pre' ? 'Period: Pre-SGMA'
      : (period === 'post' ? 'Period: Post-SGMA' : 'Period: Change (delta)');

    if (!selectedFips) {{
      document.getElementById('countyTitle').textContent = 'Select a county';
      ['s-wells','s-gwe','s-fallow','s-inc','s-farms','s-farm-split'].forEach(id => document.getElementById(id).textContent = '—');
      document.getElementById('s-gwe-sub').textContent = 'ft (source: —)';
      document.getElementById('countyNote').textContent = '';
      return;
    }}

    const feat = countyByFips(selectedFips);
    if (!feat) return;

    document.getElementById('countyTitle').textContent = feat.properties.name + ' County';
    const pre = feat.properties.pre;
    const post = feat.properties.post;
    const d = metricsFor(feat);

    // wells (always same)
    document.getElementById('s-wells').textContent = fmtInt(pre.well_failures);

    if (period === 'pre') {{
      document.getElementById('s-gwe').textContent = fmtFloat(pre.gwe_ft, 1);
      document.getElementById('s-gwe-sub').textContent = 'ft (source: ' + pre.gwe_src + ')';
      document.getElementById('s-fallow').textContent = fmtInt(pre.fallow_acres);
      document.getElementById('s-inc').textContent = fmtMoney(pre.median_income);
      document.getElementById('s-farms').textContent = fmtInt(pre.total_farms);
      document.getElementById('s-farm-split').textContent = fmtInt(pre.small_farms) + ' / ' + fmtInt(pre.large_farms) + ' (small %: ' + fmtFloat(pre.pct_small, 1) + '%)';
    }} else if (period === 'post') {{
      document.getElementById('s-gwe').textContent = fmtFloat(post.gwe_ft, 1);
      document.getElementById('s-gwe-sub').textContent = 'ft (source: ' + post.gwe_src + ')';
      document.getElementById('s-fallow').textContent = fmtInt(post.fallow_acres);
      document.getElementById('s-inc').textContent = fmtMoney(post.median_income);
      document.getElementById('s-farms').textContent = fmtInt(post.total_farms);
      document.getElementById('s-farm-split').textContent = fmtInt(post.small_farms) + ' / ' + fmtInt(post.large_farms) + ' (small %: ' + fmtFloat(post.pct_small, 1) + '%)';
    }} else {{
      const dg = d.gwe_change;
      const df = d.fallow_change;
      const di = d.income_change;
      const dt = d.total_farms_change;
      const dsmall = d.small_change;
      const dlarge = d.large_change;
      document.getElementById('s-gwe').textContent = (dg === null ? 'n/a' : (dg >= 0 ? '+' : '') + fmtFloat(dg, 1));
      document.getElementById('s-gwe-sub').textContent = 'ft (Post - Pre)';
      document.getElementById('s-fallow').textContent = (df === null ? 'n/a' : (df >= 0 ? '+' : '') + fmtInt(df));
      document.getElementById('s-inc').textContent = (di === null ? 'n/a' : (di >= 0 ? '+' : '') + fmtMoney(di).replace('$',''));
      document.getElementById('s-farms').textContent = (dt === null ? 'n/a' : (dt >= 0 ? '+' : '') + fmtInt(dt));
      const smallLoss = d.small_farm_loss;
      const lossTxt = smallLoss === null ? 'n/a' : (smallLoss >= 0 ? '+' : '') + fmtInt(smallLoss);
      document.getElementById('s-farm-split').textContent =
        (dsmall === null ? 'n/a' : (dsmall >= 0 ? '+' : '') + fmtInt(dsmall)) +
        ' / ' +
        (dlarge === null ? 'n/a' : (dlarge >= 0 ? '+' : '') + fmtInt(dlarge)) +
        ' (small loss: ' + lossTxt + ')';
    }}

    document.getElementById('countyNote').textContent = feat.properties.gwe_embed_note || '';
  }}

  // --- selection wiring ---
  function setSelected(fips) {{
    selectedFips = fips;
    refreshMapStyles();
    highlightScatterSelection();
    updateCard();
  }}

  // --- period toggle wiring ---
  function setPeriod(p) {{
    period = p;
    document.getElementById('btn-pre').classList.toggle('active', p === 'pre');
    document.getElementById('btn-post').classList.toggle('active', p === 'post');
    document.getElementById('btn-delta').classList.toggle('active', p === 'delta');
    updateCard();
  }}
  document.getElementById('btn-pre').addEventListener('click', () => setPeriod('pre'));
  document.getElementById('btn-post').addEventListener('click', () => setPeriod('post'));
  document.getElementById('btn-delta').addEventListener('click', () => setPeriod('delta'));

  // --- dropdown wiring ---
  document.getElementById('viewSelect').addEventListener('change', (e) => {{
    currentViewId = e.target.value;
    updateScatter();
  }});

  // initialize
  updateScatter();
  setPeriod('pre');
  </script>
</body>
</html>
"""


def main() -> int:
    summary_path = ROOT / "data/clean/sjv_county_summary.csv"
    farm_path = ROOT / "data/raw/farm_size/farm_operations.json"
    cdl12 = ROOT / "data/raw/land_use/cdl_acreage_2012.csv"
    cdl22 = ROOT / "data/raw/land_use/cdl_acreage_2022.csv"
    acs14 = ROOT / "data/raw/socioeconomic/acs5_2014.csv"
    acs21 = ROOT / "data/raw/socioeconomic/acs5_2021.csv"

    for p in (summary_path, farm_path, cdl12, cdl22, acs14, acs21):
        if not p.is_file():
            print(f"Missing {p}", file=sys.stderr)
            return 1

    summary = pd.read_csv(summary_path, dtype={"county_fips5": str})
    summary["county_fips5"] = summary["county_fips5"].astype(str).str.zfill(5)

    farm = load_farm_year_totals(farm_path)
    fallow12 = load_fallow_by_county(cdl12, 2012)
    fallow22 = load_fallow_by_county(cdl22, 2022)
    inc14 = load_acs_income(acs14)
    inc21 = load_acs_income(acs21)

    fips_lookup = {v: k for k, v in FIPS5_TO_NORM.items()}
    gwe_pre: dict[str, float] = {}
    gwe_post: dict[str, float] = {}
    gwe_note = "Groundwater: using county means from the summary (no measurements.csv found)."

    ms_candidates = [ROOT / "data/raw/groundwater/measurements.csv", ROOT / "measurements.csv"]
    ms_path = next((p for p in ms_candidates if p.is_file()), None)
    if ms_path is not None:
        print(f"Aggregating groundwater from {ms_path} ...")
        pre, post, note = aggregate_gwe_periods(ms_path, fips_lookup)
        if pre or post:
            gwe_pre, gwe_post, gwe_note = pre, post, note
        else:
            overall = aggregate_gwe_all_time(ms_path, fips_lookup)
            gwe_pre = overall.copy()
            gwe_post = overall.copy()
            gwe_note = "Groundwater: using all-time mean gwe per county for both periods."

    counties_gdf = load_county_boundaries()
    gsa = load_gsa_sjv_outline()
    counties_fc = build_county_fc(
        counties_gdf, summary, farm, fallow12, fallow22, inc14, inc21, gwe_pre, gwe_post, gwe_note
    )
    gsa_fc = gsa_to_geojson(gsa)

    meta = {"title": "SGMA Equity Atlas — San Joaquin Valley (Dashboard)"}
    html_out = render_dashboard_html(counties_fc, gsa_fc, meta)
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(html_out, encoding="utf-8")
    print(f"Wrote {OUT_HTML} ({OUT_HTML.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

