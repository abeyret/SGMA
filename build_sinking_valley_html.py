"""

Build vercel_site/sinking_valley.html — interactive "Sinking Valley" experience.



Uses DWR SAR cumulative subsidence PNGs + existing GSA/GSP/dry-well data.

Clipped to 8 SJV counties via thesis_counties.geojson.

"""



from __future__ import annotations



import json

import math

import sys

from pathlib import Path



import geopandas as gpd

import pandas as pd



ROOT = Path(__file__).resolve().parent

MANIFEST = ROOT / "outputs/subsidence/manifest.json"

GSA_PATH = ROOT / "data/raw/boundaries/gsa_boundaries.geojson"

GSP_PATH = ROOT / "data/raw/boundaries/gsp_plan_areas.geojson"

GSP_STATUS = ROOT / "data/processed/csv/gsp_determination_status.csv"

COUNTIES = ROOT / "vercel_site/thesis_counties.geojson"

DRY_WELLS = ROOT / "data/interim/dry_wells/dry_well_points.geoparquet"

OUT_HTML = ROOT / "vercel_site/sinking_valley.html"

OUT_DATA = ROOT / "vercel_site/sinking_valley_data.json"



SJV_COUNTIES = {

    "Fresno", "Kern", "Kings", "Madera", "Merced", "San Joaquin", "Stanislaus", "Tulare",

}





def load_counties() -> gpd.GeoDataFrame:

    gdf = gpd.read_file(COUNTIES).to_crs(4326)

    if "name" in gdf.columns:

        gdf = gdf[gdf["name"].isin(SJV_COUNTIES)]

    gdf["geometry"] = gdf.geometry.simplify(0.003, preserve_topology=True)

    return gdf





def clip_to_counties(gdf: gpd.GeoDataFrame, counties: gpd.GeoDataFrame) -> gpd.GeoDataFrame:

    counties_union = counties.union_all()

    gdf = gdf.to_crs(4326)

    clipped = gdf[gdf.intersects(counties_union)].copy()

    clipped["geometry"] = clipped.geometry.intersection(counties_union)

    clipped = clipped[~clipped.geometry.is_empty]

    return clipped





def load_dry_wells_sjv(counties: gpd.GeoDataFrame) -> list[dict]:

    if not DRY_WELLS.is_file():

        return []

    gdf = gpd.read_parquet(DRY_WELLS).to_crs(4326)

    union = counties.union_all()

    gdf = gdf[gdf.intersects(union)]

    pts = []

    for _, row in gdf.iterrows():

        if row.geometry is None:

            continue

        yr = row.get("_year")

        pts.append({

            "lon": float(row.geometry.x),

            "lat": float(row.geometry.y),

            "year": int(yr) if pd.notna(yr) else None,

            "county": str(row.get("_county_norm", "")),

        })

    return pts[:8000]





def simplify_geojson_gdf(gdf: gpd.GeoDataFrame, filter_basin: bool = True) -> dict:

    if filter_basin and "Basin_Name" in gdf.columns:

        gdf = gdf[gdf["Basin_Name"].astype(str).str.contains("SAN JOAQUIN VALLEY", na=False)]

    elif filter_basin and "Basin_Subbasin_Name" in gdf.columns:

        gdf = gdf[gdf["Basin_Subbasin_Name"].astype(str).str.contains("SAN JOAQUIN VALLEY", na=False)]

    gdf = gdf.to_crs(4326)

    gdf["geometry"] = gdf.geometry.simplify(0.005, preserve_topology=True)

    if "GSP_ID" in gdf.columns:

        gdf["gsp_id"] = gdf["GSP_ID"].astype(str)

    if "GSA_ID" in gdf.columns:

        gdf["gsa_id"] = gdf["GSA_ID"].astype(str)

    return json.loads(gdf.to_json())





def enrich_gsps_with_status(gsp_geo: dict, status_df: pd.DataFrame) -> dict:

    if status_df.empty:

        return gsp_geo

    smap = dict(zip(status_df["gsp_id"].astype(str), status_df["status_std"].astype(str)))

    for feat in gsp_geo.get("features", []):

        props = feat.setdefault("properties", {})

        gid = str(props.get("gsp_id") or props.get("GSP_ID") or "")

        props["status_std"] = smap.get(gid, "unknown")

    return gsp_geo





def clean_for_json(obj):

    if isinstance(obj, dict):

        return {k: clean_for_json(v) for k, v in obj.items()}

    if isinstance(obj, list):

        return [clean_for_json(v) for v in obj]

    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):

        return None

    if isinstance(obj, (pd.Timestamp,)):

        return obj.isoformat()

    return obj





def build_data() -> dict:

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.is_file() else {}

    cumulative = manifest.get("cumulative_layers", [])

    for layer in cumulative:

        layer["web_path"] = layer["file"].replace("outputs/subsidence/", "subsidence/")



    counties_gdf = load_counties()

    counties_geo = json.loads(counties_gdf.to_json())



    gsa_gdf = gpd.read_file(GSA_PATH)

    gsp_gdf = gpd.read_file(GSP_PATH)

    gsa_gdf = clip_to_counties(gsa_gdf, counties_gdf)

    gsp_gdf = clip_to_counties(gsp_gdf, counties_gdf)



    gsp_status_df = pd.DataFrame()

    gsp_status = []

    if GSP_STATUS.is_file():

        gsp_status_df = pd.read_csv(GSP_STATUS)

        gsp_status = gsp_status_df.where(pd.notna(gsp_status_df), None).to_dict(orient="records")



    gsps = simplify_geojson_gdf(gsp_gdf, filter_basin=False)

    gsps = enrich_gsps_with_status(gsps, gsp_status_df)



    xmin, ymin, xmax, ymax = counties_gdf.total_bounds

    pad = 0.012

    county_bbox = {"xmin": xmin - pad, "ymin": ymin - pad, "xmax": xmax + pad, "ymax": ymax + pad}

    bbox = manifest.get("bbox_wgs84") or county_bbox



    return clean_for_json({

        "manifest": manifest,

        "cumulative_layers": cumulative,

        "annual_layers": manifest.get("annual_rate_layers", []),

        "bbox": bbox,

        "counties": counties_geo,

        "gsas": simplify_geojson_gdf(gsa_gdf, filter_basin=False),

        "gsps": gsps,

        "gsp_status": gsp_status,

        "dry_wells": load_dry_wells_sjv(counties_gdf),

        "sgma_signed": "2014-09-16",

        "subsidence_legend": {

            "min_ft": 0,

            "max_ft": 4,

            "label": "Cumulative subsidence (ft since Jun 2015)",

            "colormap": "DWR SAR warm (green → yellow → orange → red)",

        },

        "gsp_legend": [

            {"status_std": "approved", "label": "Approved", "color": "#004655"},

            {"status_std": "under_review", "label": "Under review", "color": "#c8922a"},

            {"status_std": "inadequate", "label": "Inadequate", "color": "#c0392b"},

            {"status_std": "inadequate_under_review", "label": "Inadequate (under review)", "color": "#c0392b"},

            {"status_std": "state_intervention", "label": "State intervention", "color": "#6b1d1d"},

            {"status_std": "incomplete", "label": "Incomplete", "color": "#888888"},

        ],

    })





def render_html() -> str:

    return '''<!doctype html>

<html lang="en">

<head>

  <meta charset="utf-8"/>

  <meta name="viewport" content="width=device-width,initial-scale=1"/>

  <title>Sinking Valley — SGMA & Subsidence</title>

  <link rel="preconnect" href="https://fonts.googleapis.com"/>

  <link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=IBM+Plex+Sans:wght@300;400;600&display=swap" rel="stylesheet"/>

  <link href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css" rel="stylesheet"/>

  <link rel="stylesheet" href="sinking_valley.css"/>

</head>

<body>

  <div id="map"></div>

  <div id="split-container" aria-hidden="true">

    <div class="split-pane"><div id="map-pre"></div><span class="split-label">Pre-SGMA · 2016</span></div>

    <div class="split-divider" aria-hidden="true"></div>

    <div class="split-pane"><div id="map-post"></div><span class="split-label">Latest · 2024</span></div>

  </div>



  <div class="hud">

    <header>

      <p class="eyebrow">8 SJV Counties · DWR InSAR</p>

      <h1>Sinking Valley</h1>

      <p class="lede">Drag through years of cumulative land subsidence across groundwater governance boundaries.</p>

    </header>

    <div class="hud-body">

      <div class="controls">

        <label for="year-slider">Cumulative subsidence since Jun 2015</label>

        <input type="range" id="year-slider" min="0" max="0" value="0" step="1"/>

        <div class="year-row"><span id="year-label">—</span><button id="play-btn" type="button">▶ Play</button></div>

      </div>

      <hr class="rule"/>

      <div class="toggles">

        <label><input type="checkbox" id="toggle-gsa" checked/> GSA boundaries</label>

        <label><input type="checkbox" id="toggle-gsp" checked/> GSP status</label>

        <label><input type="checkbox" id="toggle-wells"/> Dry wells</label>

        <label><input type="checkbox" id="toggle-pulse"/> Hotspot pulse</label>

        <label><input type="checkbox" id="toggle-split"/> Before/after SGMA</label>

      </div>

      <hr class="rule"/>

      <p class="note" id="baseline-note"></p>

      <p class="credit">Subsidence: DWR TRE Altamira SAR ImageServer. Shared baseline 2015-06-13. Not causal proof of governance failure.</p>

    </div>

  </div>



  <aside class="legend-panel" id="legend-panel" aria-label="Map legend">

    <h3>Subsidence</h3>

    <div class="subsidence-bar" id="subsidence-bar"></div>

    <div class="bar-labels"><span id="legend-min">0 ft</span><span id="legend-max">4 ft</span></div>

    <div class="gsp-key">

      <h3 style="margin-top:0.75rem">GSP status</h3>

      <div id="gsp-key-items"></div>

    </div>

  </aside>



  <script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>

  <script src="sinking_valley_data.json" type="application/json" id="embedded-data"></script>

  <script src="sinking_valley.js"></script>

</body>

</html>'''





def main() -> int:

    if not MANIFEST.is_file():

        print("Run build_sjv_subsidence.py first.", file=sys.stderr)

        return 1

    data = build_data()

    OUT_DATA.write_text(json.dumps(data, allow_nan=False), encoding="utf-8")

    OUT_HTML.write_text(render_html(), encoding="utf-8")

    print(f"Wrote {OUT_HTML} and {OUT_DATA}")

    return 0





if __name__ == "__main__":

    raise SystemExit(main())


