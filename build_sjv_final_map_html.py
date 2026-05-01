"""
Build data/clean/sjv_final_map.html: self-contained Leaflet map with embedded GeoJSON.

Requires: geopandas, pandas (run: pip install geopandas pandas)

First run downloads CA county boundaries from US Census (needs network).
Subsequent runs use data/clean/_cache_cb_2022_us_county_500k.zip if present.

Reads:
  data/clean/sjv_county_summary.csv
  data/raw/farm_size/farm_operations.json
  data/raw/land_use/cdl_acreage_2012.csv, cdl_acreage_2022.csv
  data/raw/socioeconomic/acs5_2014.csv, acs5_2021.csv
  data/raw/groundwater/measurements.csv (optional; chunked)
  i03_Groundwater_Sustainability_Agencies.geojson (GSA boundaries)
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
OUT_HTML = ROOT / "data/clean/sjv_final_map.html"
CACHE_ZIP = ROOT / "data/clean/_cache_cb_2022_us_county_500k.zip"
CENSUS_COUNTY_ZIP = (
    "https://www2.census.gov/geo/tiger/GENZ2022/shp/cb_2022_us_county_500k.zip"
)

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

# NASS disjoint buckets — match farm_consolidation.py
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
    """county_fips5, year -> buckets + derived."""
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
            "total_farms": total,
            "small_farms": small,
            "large_farms": large,
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
    out = {}
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


def year_from_row(series: pd.Series, date_col: str | None) -> pd.Series:
    if date_col is None:
        return pd.Series([pd.NA] * len(series), index=series.index)
    raw = series[date_col]
    if date_col and date_col.lower() in ("year", "yr", "measurement_year"):
        return pd.to_numeric(raw, errors="coerce").astype("Int64")
    dt = pd.to_datetime(raw, errors="coerce", utc=True)
    return dt.dt.year.astype("Int64")


def aggregate_gwe_periods(
    ms_path: Path,
    fips_lookup: dict[str, str],
    chunksize: int = 200_000,
) -> tuple[dict[str, float], dict[str, float], bool]:
    """
    Returns (pre_mean_by_fips5, post_mean_by_fips5, used_date_column).
    Pre: 2012-2014; Post: 2018-2022.
    If no usable date column, returns empty dicts and False.
    """
    header = pd.read_csv(ms_path, nrows=0).columns.tolist()
    date_col = find_date_column(header)
    if date_col is None:
        return {}, {}, False

    usecols = ["county_name", "gwe", date_col]
    for c in usecols:
        if c not in header:
            return {}, {}, False

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
        y = year_from_row(chunk, date_col)
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

    return finalize(pre_sums, pre_n), finalize(post_sums, post_n), True


def aggregate_gwe_fallback_all_time(ms_path: Path, fips_lookup: dict[str, str]) -> dict[str, float]:
    """Mean gwe by county (all years) when date filtering unavailable."""
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
    if len(sjv) != len(SJV_FIPS5):
        raise RuntimeError(f"Expected {len(SJV_FIPS5)} counties, got {len(sjv)}")
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
    if "Basin_Name" not in gsa.columns:
        raise ValueError("GSA layer missing Basin_Name")
    mask = gsa["Basin_Name"].astype(str).str.contains(
        "SAN JOAQUIN VALLEY", case=False, na=False
    )
    gsa = gsa.loc[mask].copy()
    gsa["geometry"] = gsa.geometry.simplify(0.004, preserve_topology=True)
    if gsa.crs is not None and not gsa.crs.is_geographic:
        gsa = gsa.to_crs(4326)
    elif gsa.crs is None:
        gsa = gsa.set_crs(4326)
    return gsa


def build_feature_collection(
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
        row = row.iloc[0]
        wells = int(float(row["well_failure_count"]))
        gmean = float(row["mean_groundwater_elevation_ft"])

        def gwe_for(period: str) -> tuple[float, str]:
            if period == "pre":
                v = gwe_pre.get(f5)
                src = "measurements" if f5 in gwe_pre else "summary"
                if v is None:
                    v = gmean
                    src = "summary"
                return round(v, 2), src
            v = gwe_post.get(f5)
            src = "measurements" if f5 in gwe_post else "summary"
            if v is None:
                v = gmean
                src = "summary"
            return round(v, 2), src

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
            "gwe_ft": gpre,
            "gwe_src": spre,
            "fallow_acres": round(fallow12.get(f5, 0.0), 1),
            "well_failures": wells,
            "total_farms": total12,
            "small_farms": small12,
            "large_farms": large12,
            "pct_small": pct_small(small12, total12),
            "median_income": int(inc14.get(f5, 0)) if f5 in inc14 else None,
        }
        post = {
            "gwe_ft": gpost,
            "gwe_src": spost,
            "fallow_acres": round(fallow22.get(f5, 0.0), 1),
            "well_failures": wells,
            "total_farms": total22,
            "small_farms": small22,
            "large_farms": large22,
            "pct_small": pct_small(small22, total22),
            "median_income": int(inc21.get(f5, 0)) if f5 in inc21 else None,
        }

        geom = counties_gdf.loc[counties_gdf["GEOID"] == f5, "geometry"]
        if geom.empty:
            continue
        geom_json = mapping(geom.iloc[0])

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
                "geometry": geom_json,
            }
        )

    return {"type": "FeatureCollection", "features": features}


def gsa_to_geojson(gsa: gpd.GeoDataFrame) -> dict:
    # Drop huge attribute payloads; keep minimal
    slim = gsa[["geometry"]].copy()
    slim["name"] = "GSA"
    return json.loads(slim.to_json())


def render_html(counties_fc: dict, gsa_fc: dict, meta: dict) -> str:
    payload = {
        "counties": counties_fc,
        "gsa": gsa_fc,
        "meta": meta,
    }
    json_text = json.dumps(payload, separators=(",", ":"))
    # Safe inside <script> without breaking on </script>
    json_text = json_text.replace("</", "<\\/")

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
    html, body {{ margin: 0; height: 100%; font-family: system-ui, Segoe UI, Roboto, sans-serif; }}
    #map {{ height: 100%; width: 100%; }}
    .title-panel {{
      position: absolute; z-index: 1000; top: 12px; left: 12px;
      background: rgba(255,255,255,0.95); padding: 14px 18px; border-radius: 10px;
      box-shadow: 0 2px 10px rgba(0,0,0,0.12); max-width: 420px;
    }}
    .title-panel h1 {{ margin: 0 0 4px 0; font-size: 1.15rem; font-weight: 700; color: #1a2b3c; }}
    .title-panel p {{ margin: 0; font-size: 0.76rem; color: #555; line-height: 1.35; }}
    .controls {{
      margin-top: 10px; display: flex; flex-direction: column; gap: 10px;
    }}
    .period-toggle {{
      display: flex; border-radius: 8px; overflow: hidden; border: 1px solid #ccd;
    }}
    .period-toggle button {{
      flex: 1; padding: 14px 10px; border: none; background: #f4f6f8; cursor: pointer;
      font-size: 0.92rem; font-weight: 600; color: #334; transition: background 0.15s;
    }}
    .period-toggle button.active {{ background: #2563eb; color: #fff; }}
    .period-toggle button:not(.active):hover {{ background: #e8ecf1; }}
    .layers {{
      display: flex; flex-wrap: wrap; gap: 6px 12px;
      font-size: 0.78rem; align-items: center;
    }}
    .layers label {{
      display: inline-flex; align-items: center; gap: 5px; cursor: pointer; user-select: none; color: #333;
    }}
    .layers input {{ width: 15px; height: 15px; accent-color: #2563eb; }}
    .layer-hint {{ font-size: 0.72rem; color: #666; margin-top: 4px; line-height: 1.3; }}
    .legend {{
      position: absolute; z-index: 1000; bottom: 24px; right: 12px;
      background: rgba(255,255,255,0.95); padding: 12px 14px; border-radius: 10px;
      box-shadow: 0 2px 10px rgba(0,0,0,0.12); font-size: 0.78rem; min-width: 200px; max-width: 280px;
    }}
    .legend h3 {{ margin: 0 0 8px 0; font-size: 0.85rem; color: #1a2b3c; }}
    .legend-gradient {{
      height: 14px; border-radius: 4px; margin: 4px 0 6px 0;
      border: 1px solid #ccc;
    }}
    .legend-scale {{ display: flex; justify-content: space-between; font-variant-numeric: tabular-nums; color: #444; }}
    .leaflet-popup-content-wrapper {{ border-radius: 8px; }}
    .popup dl {{ margin: 0; display: grid; grid-template-columns: auto 1fr; gap: 2px 10px; font-size: 0.82rem; }}
    .popup dt {{ color: #666; font-weight: 600; }}
    .popup dd {{ margin: 0; text-align: right; }}
    .popup-note {{ font-size: 0.72rem; color: #777; margin-top: 8px; }}
  </style>
</head>
<body>
  <div id="map"></div>
  <div class="title-panel">
    <h1>SGMA Equity Atlas &mdash; San Joaquin Valley</h1>
    <p>County metrics align with period: Pre-SGMA uses 2012 CDL fallow, 2012 farms, 2014 median income;
       Post-SGMA uses 2022 CDL, 2022 farms, 2021 median income. Groundwater means use CASGEM measurements
       by year when a date column is available; otherwise county means from the merged summary.</p>
    <div class="controls">
      <div class="period-toggle">
        <button type="button" id="btn-pre" class="active">Pre-SGMA (2012&ndash;2014)</button>
        <button type="button" id="btn-post">Post-SGMA (2018&ndash;2022)</button>
      </div>
      <div class="layers">
        <span style="font-weight:600;color:#444;">Layers:</span>
        <label><input type="checkbox" id="ly-gw" checked /> Groundwater</label>
        <label><input type="checkbox" id="ly-fallow" checked /> Fallow</label>
        <label><input type="checkbox" id="ly-wells" checked /> Well failures</label>
        <label><input type="checkbox" id="ly-farm" checked /> Farm size</label>
        <label><input type="checkbox" id="ly-demo" checked /> Median income</label>
      </div>
      <p class="layer-hint">County colors use the <strong>first checked</strong> layer in this order:
        Groundwater &rarr; Fallow &rarr; Well failures &rarr; Farm size &rarr; Median income.
        Uncheck all to hide the choropleth.</p>
    </div>
  </div>
  <div class="legend" id="legend">
    <h3 id="legend-title">Groundwater (ft)</h3>
    <div class="legend-gradient" id="legend-bar"></div>
    <div class="legend-scale"><span id="legend-low"></span><span id="legend-high"></span></div>
  </div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
          integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
  <script>
  const ATLAS = {json_text};
  const LAYER_ORDER = [
    {{ id: 'gw', key: 'gwe', title: 'Mean groundwater elevation (ft)', format: (v) => v.toFixed(1) }},
    {{ id: 'fallow', key: 'fallow', title: 'Fallow cropland (acres)', format: (v) => v.toLocaleString() }},
    {{ id: 'wells', key: 'wells', title: 'Well failure reports (count)', format: (v) => String(v) }},
    {{ id: 'farm', key: 'farm', title: 'Small farm share (% under 180 ac)', format: (v) => v.toFixed(1) + '%' }},
    {{ id: 'demo', key: 'income', title: 'Median household income ($)', format: (v) => '$' + Math.round(v).toLocaleString() }},
  ];

  let period = 'pre';

  function layerEnabled(id) {{
    return document.getElementById('ly-' + id).checked;
  }}

  function activeLayerKey() {{
    for (const L of LAYER_ORDER) {{
      if (layerEnabled(L.id)) return L.key;
    }}
    return null;
  }}

  function metricValue(feat, key) {{
    const d = period === 'pre' ? feat.properties.pre : feat.properties.post;
    switch (key) {{
      case 'gwe': return d.gwe_ft;
      case 'fallow': return d.fallow_acres;
      case 'wells': return d.well_failures;
      case 'farm': return d.pct_small;
      case 'income': return d.median_income;
      default: return null;
    }}
  }}

  function extentForActive(feats, key) {{
    let lo = Infinity, hi = -Infinity;
    for (const f of feats) {{
      const v = metricValue(f, key);
      if (v === null || v === undefined || Number.isNaN(v)) continue;
      lo = Math.min(lo, v);
      hi = Math.max(hi, v);
    }}
    if (!isFinite(lo) || !isFinite(hi)) return [0, 1];
    if (lo === hi) {{ lo -= 1; hi += 1; }}
    return [lo, hi];
  }}

  function lerp(a, b, t) {{ return a + (b - a) * t; }}

  /** Sequential blues for groundwater; oranges fallow/wells; purple farm; greens income */
  function colorFor(key, t) {{
    if (key === 'gwe') {{
      return 'rgb(' +
        Math.round(lerp(247, 8, t)) + ',' +
        Math.round(lerp(252, 70, t)) + ',' +
        Math.round(lerp(255, 150, t)) + ')';
    }}
    if (key === 'fallow' || key === 'wells') {{
      return 'rgb(' +
        Math.round(lerp(255, 128, t)) + ',' +
        Math.round(lerp(247, 35, t)) + ',' +
        Math.round(lerp(230, 35, t)) + ')';
    }}
    if (key === 'farm') {{
      return 'rgb(' +
        Math.round(lerp(254, 106, t)) + ',' +
        Math.round(lerp(235, 27, t)) + ',' +
        Math.round(lerp(255, 188, t)) + ')';
    }}
    if (key === 'income') {{
      return 'rgb(' +
        Math.round(lerp(215, 35, t)) + ',' +
        Math.round(lerp(48, 160, t)) + ',' +
        Math.round(lerp(39, 96, t)) + ')';
    }}
    return '#ddd';
  }}

  function styleFeature(feat) {{
    const ak = activeLayerKey();
    if (!ak) {{
      return {{ fillColor: '#ececec', color: '#888', weight: 0.6, fillOpacity: 0.85 }};
    }}
    const feats = ATLAS.counties.features;
    const [lo, hi] = extentForActive(feats, ak);
    const v = metricValue(feat, ak);
    let t = 0.5;
    if (v !== null && v !== undefined && !Number.isNaN(v)) {{
      t = (v - lo) / (hi - lo);
      t = Math.max(0, Math.min(1, t));
    }}
    const tt = t;
    return {{
      fillColor: colorFor(ak, tt),
      color: '#555',
      weight: 0.7,
      fillOpacity: 0.88
    }};
  }}

  function updateLegend() {{
    const ak = activeLayerKey();
    const titleEl = document.getElementById('legend-title');
    const lowEl = document.getElementById('legend-low');
    const highEl = document.getElementById('legend-high');
    const bar = document.getElementById('legend-bar');
    if (!ak) {{
      titleEl.textContent = 'No layer selected';
      lowEl.textContent = '';
      highEl.textContent = '';
      bar.style.background = '#e5e5e5';
      return;
    }}
    const spec = LAYER_ORDER.find((x) => x.key === ak);
    titleEl.textContent = spec.title + (period === 'pre' ? ' (Pre-SGMA)' : ' (Post-SGMA)');
    const feats = ATLAS.counties.features;
    const [lo, hi] = extentForActive(feats, ak);
    lowEl.textContent = spec.format(lo);
    highEl.textContent = spec.format(hi);
    const c0 = colorFor(ak, 0);
    const c1 = colorFor(ak, 1);
    bar.style.background = 'linear-gradient(90deg,' + c0 + ',' + c1 + ')';
  }}

  function periodLabel() {{
    return period === 'pre'
      ? 'Pre-SGMA (2012 to 2014)'
      : 'Post-SGMA (2018 to 2022)';
  }}

  function popupHtml(props) {{
    const d = period === 'pre' ? props.pre : props.post;
    const inc = d.median_income != null ? '$' + Math.round(d.median_income).toLocaleString() : 'n/a';
    const gnote = props.gwe_embed_note ? '<div class="popup-note">' + props.gwe_embed_note + '</div>' : '';
    return (
      '<div class="popup"><h3 style="margin:0 0 8px 0">' + props.name + ' County</h3>' +
      '<p style="margin:0 0 8px;font-size:0.8rem;color:#555">' + periodLabel() + '</p>' +
      '<dl>' +
      '<dt>Mean groundwater (ft)</dt><dd>' + (d.gwe_ft != null ? d.gwe_ft.toFixed(1) : 'n/a') +
        ' <span style="color:#888">(' + d.gwe_src + ')</span></dd>' +
      '<dt>Fallow cropland (ac)</dt><dd>' + (d.fallow_acres != null ? d.fallow_acres.toLocaleString() : 'n/a') + '</dd>' +
      '<dt>Well failure reports</dt><dd>' + d.well_failures + '</dd>' +
      '<dt>Total farms</dt><dd>' + d.total_farms + '</dd>' +
      '<dt>Small farms (&lt;180 ac)</dt><dd>' + d.small_farms + '</dd>' +
      '<dt>Large farms (500+ ac)</dt><dd>' + d.large_farms + '</dd>' +
      '<dt>Median household income</dt><dd>' + inc + '</dd>' +
      '</dl>' + gnote + '</div>'
    );
  }}

  const map = L.map('map', {{ attributionControl: true }}).setView([36.6, -119.8], 8);
  L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap &copy; CARTO'
  }}).addTo(map);

  const countyLayer = L.geoJSON(ATLAS.counties, {{
    style: styleFeature,
    onEachFeature: function(feature, layer) {{
      layer.bindPopup(function () {{ return popupHtml(feature.properties); }});
    }}
  }}).addTo(map);

  const gsaLayer = L.geoJSON(ATLAS.gsa, {{
    style: {{
      color: '#1d4ed8',
      weight: 1.2,
      opacity: 0.95,
      fillOpacity: 0,
      fill: false
    }},
    interactive: false
  }}).addTo(map);

  gsaLayer.bringToFront();

  map.fitBounds(countyLayer.getBounds(), {{ padding: [24, 24] }});

  function refresh() {{
    countyLayer.eachLayer(function (layer) {{
      layer.setStyle(styleFeature(layer.feature));
      try {{
        if (typeof layer.isPopupOpen === 'function' && layer.isPopupOpen()) {{
          layer.setPopupContent(popupHtml(layer.feature.properties));
        }}
      }} catch (e) {{}}
    }});
    updateLegend();
  }}

  document.getElementById('btn-pre').addEventListener('click', function () {{
    period = 'pre';
    document.getElementById('btn-pre').classList.add('active');
    document.getElementById('btn-post').classList.remove('active');
    refresh();
  }});
  document.getElementById('btn-post').addEventListener('click', function () {{
    period = 'post';
    document.getElementById('btn-post').classList.add('active');
    document.getElementById('btn-pre').classList.remove('active');
    refresh();
  }});
  ['gw','fallow','wells','farm','demo'].forEach(function (id) {{
    document.getElementById('ly-' + id).addEventListener('change', refresh);
  }});

  updateLegend();
  </script>
</body>
</html>
"""


def main() -> int:
    summary_path = ROOT / "data/clean/sjv_county_summary.csv"
    if not summary_path.is_file():
        print(f"Missing {summary_path}", file=sys.stderr)
        return 1
    farm_path = ROOT / "data/raw/farm_size/farm_operations.json"
    cdl12 = ROOT / "data/raw/land_use/cdl_acreage_2012.csv"
    cdl22 = ROOT / "data/raw/land_use/cdl_acreage_2022.csv"
    acs14 = ROOT / "data/raw/socioeconomic/acs5_2014.csv"
    acs21 = ROOT / "data/raw/socioeconomic/acs5_2021.csv"
    for p, label in [
        (farm_path, "farm_operations.json"),
        (cdl12, "cdl 2012"),
        (cdl22, "cdl 2022"),
        (acs14, "acs 2014"),
        (acs21, "acs 2021"),
    ]:
        if not p.is_file():
            print(f"Missing {p} ({label})", file=sys.stderr)
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
    gwe_note = (
        "Groundwater: period-specific means use CASGEM rows dated "
        "2012–2014 (Pre) or 2018–2022 (Post) when a date column is present."
    )

    ms_candidates = [
        ROOT / "data/raw/groundwater/measurements.csv",
        ROOT / "measurements.csv",
    ]
    ms_path = next((p for p in ms_candidates if p.is_file()), None)

    if ms_path is not None:
        print(f"Aggregating groundwater from {ms_path} ...")
        a, b, ok = aggregate_gwe_periods(ms_path, fips_lookup)
        if ok and (a or b):
            gwe_pre, gwe_post = a, b
            gwe_note = (
                "Groundwater means are averaged from CASGEM measurement dates "
                "in 2012–2014 (Pre) and 2018–2022 (Post)."
            )
        else:
            overall = aggregate_gwe_fallback_all_time(ms_path, fips_lookup)
            gwe_pre = overall.copy()
            gwe_post = overall.copy()
            gwe_note = (
                "Groundwater: no usable date column in measurements.csv; "
                "using the same all-time mean gwe per county for Pre and Post."
            )
    else:
        gwe_note = (
            "Groundwater: measurements.csv not found; "
            "using county means from sjv_county_summary.csv for both periods."
        )

    counties_gdf = load_county_boundaries()
    gsa = load_gsa_sjv_outline()

    fc = build_feature_collection(
        counties_gdf,
        summary,
        farm,
        fallow12,
        fallow22,
        inc14,
        inc21,
        gwe_pre,
        gwe_post,
        gwe_note,
    )
    gsa_j = gsa_to_geojson(gsa)
    meta = {"title": "SGMA Equity Atlas — San Joaquin Valley"}

    html_out = render_html(fc, gsa_j, meta)
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(html_out, encoding="utf-8")
    print(f"Wrote {OUT_HTML} ({OUT_HTML.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
