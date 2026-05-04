"""
Build data/clean/sjv_equity_atlas.html

Goal: best single interactive element to answer the SGMA equity question by linking:
- Preexisting community harm (well failures by issue-start date)
- Physical overdraft costs (subsidence via InSAR, optional if available)
- Groundwater level change (CASGEM measurements.csv, chunked)
- SGMA-era farm adjustment (fallowing + farm size composition)
- Contextual vulnerability (CalEnviroScreen tract scores aggregated to counties)

Outputs a self-contained HTML dashboard with embedded county GeoJSON and metrics.

Requires: geopandas, pandas, shapely
"""

from __future__ import annotations

import csv
import html
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from shapely.geometry import Point, mapping

ROOT = Path(__file__).resolve().parent
OUT_HTML = ROOT / "data/clean/sjv_equity_atlas.html"

SJV_FIPS5 = ("06019", "06029", "06031", "06039", "06047", "06077", "06099", "06107")
COUNTY_NAME = {
    "06019": "Fresno",
    "06029": "Kern",
    "06031": "Kings",
    "06039": "Madera",
    "06047": "Merced",
    "06077": "San Joaquin",
    "06099": "Stanislaus",
    "06107": "Tulare",
}
FIPS5_TO_NORM = {f: COUNTY_NAME[f].lower().replace(" ", "_") for f in SJV_FIPS5}
NORM_TO_FIPS5 = {v: k for k, v in FIPS5_TO_NORM.items()}

# --- External datasets (cached) ---
CACHE_DIR = ROOT / "data/clean/_cache_external"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

CES4_GEOJSON_URL = "https://data.ca.gov/dataset/calenviroscreen-4-0-results1/resource/67f35f7f-0ff0-4773-a3dd-6ab6a266a70e/download"
CES4_CACHE = CACHE_DIR / "calenviroscreen4_results.geojson"

INSAR_ZIP_URL = "https://data.cnra.ca.gov/dataset/5e2d49e1-9ed0-425e-9f3e-2cda4a213c26/resource/6e855ae7-f365-4d40-984f-3c016b54f69d/download/verticaldisplacementpointdata.zip"
INSAR_CACHE_ZIP = CACHE_DIR / "verticaldisplacementpointdata.zip"
MNM_SITES_URL = (
    "https://data.cnra.ca.gov/dataset/536dc423-01b3-4094-bdcd-903df84f6768"
    "/resource/38dc5a77-0428-4d8b-970a-51797ed2cd36/download/groundwater_level_sites.csv"
)
MNM_DATA_URL = (
    "https://data.cnra.ca.gov/dataset/536dc423-01b3-4094-bdcd-903df84f6768"
    "/resource/d6317634-7489-4dc9-8d05-cc939e109f4a/download/groundwater_level_data.csv"
)
MNM_SITES_CACHE = CACHE_DIR / "sjv_gsp_groundwater_sites.csv"
MNM_DATA_CACHE = CACHE_DIR / "sjv_gsp_groundwater_data.csv"

# County boundaries (cached) - US Census 2022 generalized counties
COUNTY_ZIP_URL = "https://www2.census.gov/geo/tiger/GENZ2022/shp/cb_2022_us_county_500k.zip"
COUNTY_CACHE_ZIP = ROOT / "data/clean/_cache_cb_2022_us_county_500k.zip"


def urlretrieve_with_ua(url: str, out_path: Path) -> None:
    """Download with a browser-like User-Agent to avoid 403 blocks."""
    headers_list = [
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
        },
        # some endpoints block Python-like UAs; empty UA sometimes passes
        {"User-Agent": "", "Accept": "*/*"},
    ]
    last_err: Exception | None = None
    for hdr in headers_list:
        try:
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=300) as resp:
                out_path.write_bytes(resp.read())
            return
        except Exception as e:
            last_err = e
            continue
    assert last_err is not None
    raise last_err


def download_with_requests(url: str, out_path: Path, timeout_s: int = 180) -> None:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
    }
    with requests.get(url, headers=headers, timeout=timeout_s, allow_redirects=True, stream=True) as r:
        r.raise_for_status()
        with out_path.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)


def load_mnm_gsp_groundwater_means(gsp: gpd.GeoDataFrame) -> dict[str, dict]:
    """
    Use the same MNM URLs as fetch_sjv_gsp_groundwater.py (GSP Monitoring Network Module).
    We download two CSVs (sites + data), spatially join sites to GSP polygons (because data doesn't include GSP_ID),
    then compute GSP mean groundwater elevation by period:
      - pre: 2012-2014
      - post: 2018-2022
    """
    # download caches if missing
    if not MNM_SITES_CACHE.is_file():
        print("Downloading MNM groundwater sites (GSP-level)...")
        download_with_requests(MNM_SITES_URL, MNM_SITES_CACHE)
    if not MNM_DATA_CACHE.is_file():
        print("Downloading MNM groundwater measurements (GSP-level)...")
        download_with_requests(MNM_DATA_URL, MNM_DATA_CACHE, timeout_s=300)

    sites = pd.read_csv(MNM_SITES_CACHE, low_memory=False)
    data = pd.read_csv(MNM_DATA_CACHE, low_memory=False)

    # detect site id and measurement columns
    lat_col = next((c for c in sites.columns if c.lower() in ("latitude", "lat")), None)
    lon_col = next((c for c in sites.columns if c.lower() in ("longitude", "lon", "long")), None)
    site_key = next((c for c in sites.columns if "site" in c.lower() and "code" in c.lower()), None) or next(
        (c for c in sites.columns if "site" in c.lower() and "id" in c.lower()), None
    ) or sites.columns[0]
    data_key = next((c for c in data.columns if "site" in c.lower() and "code" in c.lower()), None) or next(
        (c for c in data.columns if "site" in c.lower() and "id" in c.lower()), None
    ) or data.columns[0]
    date_col = next((c for c in data.columns if "date" in c.lower() or "time" in c.lower()), None)
    # try common groundwater elevation column names
    elev_col = next((c for c in data.columns if c.upper() in ("WSE", "GWE", "GW_ELEVATION", "WATER_SURFACE_ELEVATION")), None)
    if elev_col is None:
        # fallback: any column containing "elev" and not reference point
        elev_col = next((c for c in data.columns if ("elev" in c.lower() and "rp" not in c.lower())), None)

    if not (lat_col and lon_col and date_col and elev_col):
        return {}

    sites = sites[[site_key, lat_col, lon_col]].copy()
    sites[lat_col] = pd.to_numeric(sites[lat_col], errors="coerce")
    sites[lon_col] = pd.to_numeric(sites[lon_col], errors="coerce")
    sites = sites.dropna(subset=[lat_col, lon_col]).copy()
    sites[site_key] = sites[site_key].astype(str)

    # spatial join sites to GSP polygons
    pts = gpd.GeoDataFrame(
        sites[[site_key]].copy(),
        geometry=[Point(xy) for xy in zip(sites[lon_col], sites[lat_col])],
        crs="EPSG:4326",
    )
    gsp_wgs = gsp.copy()
    if gsp_wgs.crs is None:
        gsp_wgs = gsp_wgs.set_crs(4326)
    else:
        gsp_wgs = gsp_wgs.to_crs(4326)
    if "GSP_ID" not in gsp_wgs.columns:
        return {}
    sj_sites = gpd.sjoin(pts, gsp_wgs[["GSP_ID", "geometry"]], predicate="within", how="inner")
    if sj_sites.empty:
        return {}
    site_to_gsp = dict(zip(sj_sites[site_key].astype(str), sj_sites["GSP_ID"].astype(str)))

    # reduce data to needed columns
    d = data[[data_key, date_col, elev_col]].copy()
    d[data_key] = d[data_key].astype(str)
    d["GSP_ID"] = d[data_key].map(site_to_gsp)
    d = d.dropna(subset=["GSP_ID"]).copy()
    d["_dt"] = pd.to_datetime(d[date_col], errors="coerce", utc=True)
    d = d.dropna(subset=["_dt"]).copy()
    d["_yr"] = d["_dt"].dt.year.astype(int)
    d["_elev"] = pd.to_numeric(d[elev_col], errors="coerce")
    d = d.dropna(subset=["_elev"]).copy()

    pre = d.loc[d["_yr"].isin([2012, 2013, 2014])]
    post = d.loc[d["_yr"].isin([2018, 2019, 2020, 2021, 2022])]

    out: dict[str, dict] = {}
    pre_mean = pre.groupby("GSP_ID")["_elev"].mean() if not pre.empty else pd.Series(dtype=float)
    post_mean = post.groupby("GSP_ID")["_elev"].mean() if not post.empty else pd.Series(dtype=float)
    ids = sorted(set(d["GSP_ID"].unique()))
    for gid in ids:
        gp = float(pre_mean.get(gid)) if gid in pre_mean.index else None
        go = float(post_mean.get(gid)) if gid in post_mean.index else None
        out[gid] = {
            "gwe_pre_mnm": gp,
            "gwe_post_mnm": go,
            "gwe_delta_mnm": (go - gp) if (gp is not None and go is not None) else None,
        }
    return out


def load_counties_sjv() -> gpd.GeoDataFrame:
    if not COUNTY_CACHE_ZIP.is_file():
        COUNTY_CACHE_ZIP.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {COUNTY_ZIP_URL} ...")
        urlretrieve_with_ua(COUNTY_ZIP_URL, COUNTY_CACHE_ZIP)

    src = f"zip://{COUNTY_CACHE_ZIP}!cb_2022_us_county_500k.shp"
    gdf = gpd.read_file(src)
    gdf["GEOID"] = gdf["GEOID"].astype(str).str.zfill(5)
    sjv = gdf.loc[gdf["GEOID"].isin(SJV_FIPS5)].copy()
    sjv["geometry"] = sjv.geometry.simplify(0.003, preserve_topology=True)
    # Ensure WGS84 for stable joins
    if sjv.crs is None:
        sjv = sjv.set_crs(4326)
    else:
        sjv = sjv.to_crs(4326)
    return sjv[["GEOID", "geometry"]].copy()


def load_gsa_outline() -> gpd.GeoDataFrame:
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
    slim = gsa[["geometry"]].copy()
    slim["name"] = "GSA"
    return slim


def load_gsp_plan_areas_sjv() -> gpd.GeoDataFrame:
    """
    Load GSP plan areas, filter to San Joaquin Valley basin, keep key fields:
    - GSP_ID / Loc_GSP_ID
    - Basin_Subbasin_Name
    - Status
    - Submitted, Date_Posted (as completion proxy)
    """
    candidates = [
        ROOT / "data/raw/gsa_boundaries/i03_Groundwater_Sustainability_Plan_Areas.geojson",
        ROOT / "i03_Groundwater_Sustainability_Plan_Areas.geojson",
    ]
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        raise FileNotFoundError("GSP Plan Areas GeoJSON not found (i03_Groundwater_Sustainability_Plan_Areas.geojson).")
    gsp = gpd.read_file(path)
    if "Basin_Name" in gsp.columns:
        mask = gsp["Basin_Name"].astype(str).str.contains("SAN JOAQUIN VALLEY", case=False, na=False)
        gsp = gsp.loc[mask].copy()
    gsp["geometry"] = gsp.geometry.simplify(0.004, preserve_topology=True)
    if gsp.crs is None:
        gsp = gsp.set_crs(4326)
    else:
        gsp = gsp.to_crs(4326)

    keep = [c for c in ["GSP_ID", "Loc_GSP_ID", "Basin_Subbasin_Name", "Status", "Submitted", "Date_Posted", "UPDATED_DT"] if c in gsp.columns]
    out = gsp[keep + ["geometry"]].copy()
    # normalize datetimes to ISO strings (safe for JSON)
    for c in ["Submitted", "Date_Posted", "UPDATED_DT"]:
        if c in out.columns:
            out[c] = pd.to_datetime(out[c], errors="coerce", utc=True).dt.strftime("%Y-%m-%d")
    if "Status" in out.columns:
        out["Status"] = out["Status"].astype(str).str.strip()
    return out


def gsp_status_score(status: str) -> float:
    s = (status or "").strip().lower()
    if not s:
        return 0.0
    if "approved" in s:
        return 1.0
    if "adequate" in s:
        return 0.85
    if "conditionally" in s:
        return 0.75
    if "under review" in s or "in review" in s:
        return 0.5
    if "incomplete" in s:
        return 0.1
    if "inadequate" in s:
        return 0.2
    return 0.3


def compute_regulation_by_county(counties: gpd.GeoDataFrame, gsp: gpd.GeoDataFrame) -> dict[str, dict]:
    """
    Area-weighted county regulation metrics from overlapping GSP plan areas.
    Returns dict keyed by county fips5.
    """
    if gsp.empty:
        return {}

    # compute date normalization among non-null Date_Posted
    date_vals = []
    if "Date_Posted" in gsp.columns:
        for v in gsp["Date_Posted"].dropna().astype(str).tolist():
            try:
                date_vals.append(pd.to_datetime(v))
            except Exception:
                pass
    dmin = min(date_vals) if date_vals else None
    dmax = max(date_vals) if date_vals else None

    def date_norm(iso: str | None) -> float | None:
        if not iso or dmin is None or dmax is None or dmin == dmax:
            return None
        try:
            d = pd.to_datetime(iso)
        except Exception:
            return None
        return float((d - dmin).days) / float((dmax - dmin).days)

    # project for area
    counties_a = counties.rename(columns={"GEOID": "_fips5"})[["_fips5", "geometry"]].copy().to_crs(3310)
    gsp_a = gsp.copy()
    gsp_a = gsp_a.to_crs(3310)

    inter = gpd.overlay(gsp_a, counties_a, how="intersection")
    if inter.empty:
        return {}
    inter["_area"] = inter.geometry.area.astype(float)
    inter = inter.loc[inter["_area"] > 0].copy()

    # score each intersect piece
    inter["_status_score"] = inter.get("Status", "").map(gsp_status_score)
    if "Date_Posted" in inter.columns:
        inter["_date_norm"] = inter["Date_Posted"].map(date_norm)
    else:
        inter["_date_norm"] = None
    # completion component: treat missing date_norm as 0 for incomplete-ish statuses
    inter["_date_component"] = inter["_date_norm"].fillna(0.0)
    inter["_reg_score_piece"] = inter["_status_score"] * (0.5 + 0.5 * inter["_date_component"])

    out: dict[str, dict] = {}
    for f5, grp in inter.groupby("_fips5", sort=False):
        total_area = float(grp["_area"].sum())
        if total_area <= 0:
            continue
        score = float((grp["_reg_score_piece"] * grp["_area"]).sum() / total_area)
        approved_share = float((grp["_area"] * (grp["_status_score"] >= 0.85)).sum() / total_area)
        out[f5] = {
            "regulation_score": round(score, 4),
            "approved_area_share": round(approved_share, 4),
            "gsp_count": int(grp.shape[0]),
            "date_min": str(dmin.date()) if dmin is not None else None,
            "date_max": str(dmax.date()) if dmax is not None else None,
        }
    return out


def parse_year_mdy(s: object) -> int | None:
    if s is None or pd.isna(s):
        return None
    txt = str(s).strip()
    if not txt:
        return None
    # common: MM/DD/YYYY
    try:
        dt = pd.to_datetime(txt, errors="coerce")
        if pd.isna(dt):
            return None
        return int(dt.year)
    except Exception:
        return None


def load_well_failures_pre_post() -> dict[str, dict[str, int]]:
    """
    Uses household shortage CSV (project root naming), groups by County.
    Pre: issue start 2012-2014; Post: issue start 2018-2022.
    """
    # find the CSV
    candidates = [
        ROOT / "data/raw/well_failures/householdwatersupplyshortagereportingsystemdata.csv",
        ROOT / "householdwatersupplyshortagereportingsystemdata.csv",
    ]
    if not any(p.is_file() for p in candidates):
        # fallback: any householdwatersupplyshortagereportingsystemdata*.csv
        matches = sorted(ROOT.glob("householdwatersupplyshortagereportingsystemdata*.csv"))
        path = matches[0] if matches else None
    else:
        path = next(p for p in candidates if p.is_file())
    if path is None or not path.is_file():
        print("Well failures CSV not found; skipping time-sliced well failures.", file=sys.stderr)
        return {f: {"pre": 0, "post": 0} for f in SJV_FIPS5}

    df = pd.read_csv(path, dtype=str)
    if "County" not in df.columns or "Approximate Issue Start Date" not in df.columns:
        print("Well failures CSV missing expected columns; skipping time-sliced well failures.", file=sys.stderr)
        return {f: {"pre": 0, "post": 0} for f in SJV_FIPS5}

    df["_county_norm"] = df["County"].map(lambda x: str(x).strip().lower() if pd.notna(x) else None)
    df["_fips5"] = df["_county_norm"].map(lambda c: NORM_TO_FIPS5.get(c.replace(" ", "_")) if c else None)
    df["_year"] = df["Approximate Issue Start Date"].map(parse_year_mdy)
    df = df.loc[df["_fips5"].isin(SJV_FIPS5) & df["_year"].notna()].copy()
    y = df["_year"].astype(int)
    df["_pre"] = y.isin([2012, 2013, 2014])
    df["_post"] = y.isin([2018, 2019, 2020, 2021, 2022])

    out = {f: {"pre": 0, "post": 0} for f in SJV_FIPS5}
    grp = df.groupby("_fips5", sort=False)
    for f5, g in grp:
        out[f5] = {"pre": int(g["_pre"].sum()), "post": int(g["_post"].sum())}
    return out


def load_well_failures_by_gsp(gsp: gpd.GeoDataFrame) -> dict[str, dict]:
    """
    Spatially join well-failure points to GSP plan areas.

    Produces, per GSP_ID (string):
      - well_pre_raw: count of issue-start in 2012-2014
      - well_post_raw: count of issue-start in 2018-2022
      - well_pre_adj: sum over years of (gsp_count_year / total_sjv_count_year) for 2012-2014
      - well_post_adj: same for 2018-2022

    The adjusted metrics attempt to reduce bias from overall reporting volume increasing over time
    (e.g., improved awareness/accessibility).
    """
    # find the CSV
    candidates = [
        ROOT / "data/raw/well_failures/householdwatersupplyshortagereportingsystemdata.csv",
        ROOT / "householdwatersupplyshortagereportingsystemdata.csv",
    ]
    if not any(p.is_file() for p in candidates):
        matches = sorted(ROOT.glob("householdwatersupplyshortagereportingsystemdata*.csv"))
        path = matches[0] if matches else None
    else:
        path = next(p for p in candidates if p.is_file())
    if path is None or not path.is_file():
        return {}

    df = pd.read_csv(path, dtype=str)
    need = {"LATITUDE", "LONGITUDE", "Approximate Issue Start Date"}
    if not need.issubset(set(df.columns)):
        return {}

    df["LATITUDE"] = pd.to_numeric(df["LATITUDE"], errors="coerce")
    df["LONGITUDE"] = pd.to_numeric(df["LONGITUDE"], errors="coerce")
    df = df.dropna(subset=["LATITUDE", "LONGITUDE"]).copy()
    df["_year"] = df["Approximate Issue Start Date"].map(parse_year_mdy)
    df = df.loc[df["_year"].notna()].copy()
    df["_year"] = df["_year"].astype(int)

    pts = gpd.GeoDataFrame(
        df[["_year"]].copy(),
        geometry=[Point(xy) for xy in zip(df["LONGITUDE"], df["LATITUDE"])],
        crs="EPSG:4326",
    )

    gsp_wgs = gsp.copy()
    if gsp_wgs.crs is None:
        gsp_wgs = gsp_wgs.set_crs(4326)
    else:
        gsp_wgs = gsp_wgs.to_crs(4326)

    if "GSP_ID" not in gsp_wgs.columns:
        return {}

    sj = gpd.sjoin(
        pts,
        gsp_wgs[["GSP_ID", "Status", "Date_Posted", "geometry"]],
        predicate="within",
        how="inner",
    )
    if sj.empty:
        return {}
    sj["GSP_ID"] = sj["GSP_ID"].astype(str)

    # raw counts by gsp/year
    g_year = sj.groupby(["GSP_ID", "_year"], as_index=False).size().rename(columns={"size": "n"})
    totals = g_year.groupby("_year", as_index=False)["n"].sum().rename(columns={"n": "n_total"})
    merged = g_year.merge(totals, on="_year", how="left")
    merged["share"] = merged["n"] / merged["n_total"].replace({0: np.nan})
    merged["share"] = merged["share"].fillna(0.0)

    def sum_period(col: str, years: list[int]) -> pd.Series:
        m = merged["_year"].isin(years)
        return merged.loc[m].groupby("GSP_ID")[col].sum()

    pre_years = [2012, 2013, 2014]
    post_years = [2018, 2019, 2020, 2021, 2022]
    pre_raw = sum_period("n", pre_years)
    post_raw = sum_period("n", post_years)
    pre_adj = sum_period("share", pre_years)
    post_adj = sum_period("share", post_years)

    out: dict[str, dict] = {}
    all_ids = sorted(set(merged["GSP_ID"].unique()))
    for gid in all_ids:
        out[gid] = {
            "well_pre_raw": int(pre_raw.get(gid, 0)),
            "well_post_raw": int(post_raw.get(gid, 0)),
            "well_pre_adj": float(pre_adj.get(gid, 0.0)),
            "well_post_adj": float(post_adj.get(gid, 0.0)),
        }
    return out


def load_ces4_county_scores(counties: gpd.GeoDataFrame) -> dict[str, float]:
    """
    Download/Read CalEnviroScreen 4.0 GeoJSON (tracts).
    Aggregate to county by spatial join: mean of CES score percentile (best-effort).
    """
    if not CES4_CACHE.is_file():
        print("Downloading CalEnviroScreen 4.0 GeoJSON ...")
        urlretrieve_with_ua(CES4_GEOJSON_URL, CES4_CACHE)

    tracts = gpd.read_file(CES4_CACHE)
    if tracts.crs is not None and not tracts.crs.is_geographic:
        tracts = tracts.to_crs(4326)
    elif tracts.crs is None:
        tracts = tracts.set_crs(4326)

    # Find a reasonable score field
    # CES4 GeoJSON often uses CIscoreP (cumulative impact score percentile)
    if "CIscoreP" in tracts.columns:
        score_col = "CIscoreP"
    else:
        score_col = None
    cols = {c.lower(): c for c in tracts.columns}
    candidates = [
        "ces 4.0 score percentile",
        "ces_score_pctl",
        "ces_score_percentile",
        "ces_score",
        "score_pctl",
        "pctile",
        "percentile",
    ]
    if score_col is None:
        for want in candidates:
            for k, c in cols.items():
                if want.replace(" ", "") in k.replace(" ", ""):
                    score_col = c
                    break
            if score_col:
                break

    if score_col is None:
        # last resort: look for something with "PCTL" and "CES" substrings
        for c in tracts.columns:
            cl = c.upper()
            if "CES" in cl and "PCTL" in cl:
                score_col = c
                break

    if score_col is None:
        print("Could not find CES score percentile column; skipping CES layer.", file=sys.stderr)
        return {}

    tracts["_score"] = pd.to_numeric(tracts[score_col], errors="coerce")
    tracts = tracts.dropna(subset=["_score"]).copy()
    tracts = tracts[["geometry", "_score"]].copy()

    sj = gpd.sjoin(
        tracts,
        counties.rename(columns={"GEOID": "_fips5"})[["_fips5", "geometry"]],
        predicate="within",
        how="inner",
    )
    g = sj.groupby("_fips5", as_index=False)["_score"].mean()
    return {row._fips5: float(row._score) for _, row in g.iterrows()}


def load_insar_subsidence_by_county(counties: gpd.GeoDataFrame) -> dict[str, dict[str, float]]:
    """
    Best-effort: download TRE Altamira vertical displacement point data (zip),
    read point time series columns, and compute:
      - early_rate_mm_yr: mean annual rate over 2015-2017 (if present)
      - late_rate_mm_yr: mean annual rate over 2018-2022 (if present)
      - rate_change_mm_yr: late - early

    If schema is unknown or too large, returns {}.
    """
    try:
        if not INSAR_CACHE_ZIP.is_file():
            print("Downloading InSAR subsidence point ZIP ...")
            urlretrieve_with_ua(INSAR_ZIP_URL, INSAR_CACHE_ZIP)

        with zipfile.ZipFile(INSAR_CACHE_ZIP) as z:
            names = z.namelist()
            # find first CSV
            csv_name = next((n for n in names if n.lower().endswith(".csv")), None)
            if csv_name is None:
                print("InSAR ZIP had no CSV; skipping subsidence layer.", file=sys.stderr)
                return {}
            # extract to cache
            extracted = CACHE_DIR / Path(csv_name).name
            if not extracted.is_file():
                z.extract(csv_name, CACHE_DIR)
                # move if nested
                nested = CACHE_DIR / csv_name
                if nested.is_file() and nested != extracted:
                    nested.replace(extracted)

        # inspect header to identify lon/lat and rate columns
        with extracted.open("r", encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.reader(f)
            header = next(reader)
        hl = [h.strip() for h in header]
        lower = {h.lower(): h for h in hl}

        lon_col = None
        lat_col = None
        for key in ("lon", "longitude", "x", "long"):
            if key in lower:
                lon_col = lower[key]
                break
        for key in ("lat", "latitude", "y"):
            if key in lower:
                lat_col = lower[key]
                break
        if lon_col is None or lat_col is None:
            # sometimes easting/northing only; bail
            print("InSAR CSV missing lon/lat columns; skipping subsidence layer.", file=sys.stderr)
            return {}

        # Rate columns: try to find annual rate fields (mm/yr) with year in name
        rate_cols = [c for c in hl if ("rate" in c.lower() and ("yr" in c.lower() or "year" in c.lower()))]
        # Another possibility: displacement per date columns; too heavy to infer. If no rate columns, skip.
        if not rate_cols:
            print("InSAR CSV schema not recognized (no rate columns); skipping subsidence layer.", file=sys.stderr)
            return {}

        # Identify early/late rate columns by year in name
        def year_in_col(c: str) -> int | None:
            for y in range(2015, 2023):
                if str(y) in c:
                    return y
            return None

        early = [c for c in rate_cols if (year_in_col(c) in (2015, 2016, 2017))]
        late = [c for c in rate_cols if (year_in_col(c) in (2018, 2019, 2020, 2021, 2022))]
        if not early or not late:
            print("InSAR rate columns do not cover both early and late periods; skipping subsidence layer.", file=sys.stderr)
            return {}

        usecols = [lon_col, lat_col] + sorted(set(early + late))
        pts = []
        for chunk in pd.read_csv(extracted, usecols=usecols, chunksize=250_000):
            chunk[lon_col] = pd.to_numeric(chunk[lon_col], errors="coerce")
            chunk[lat_col] = pd.to_numeric(chunk[lat_col], errors="coerce")
            chunk = chunk.dropna(subset=[lon_col, lat_col])
            # average across columns inside each period
            chunk["_early"] = pd.to_numeric(chunk[early], errors="coerce").mean(axis=1)
            chunk["_late"] = pd.to_numeric(chunk[late], errors="coerce").mean(axis=1)
            chunk = chunk.dropna(subset=["_early", "_late"])
            pts.append(chunk[[lon_col, lat_col, "_early", "_late"]])

        if not pts:
            return {}
        df = pd.concat(pts, ignore_index=True)
        gpts = gpd.GeoDataFrame(
            df,
            geometry=[Point(xy) for xy in zip(df[lon_col], df[lat_col])],
            crs="EPSG:4326",
        )
        sj = gpd.sjoin(
            gpts,
            counties.rename(columns={"GEOID": "_fips5"})[["_fips5", "geometry"]],
            predicate="within",
            how="inner",
        )
        agg = sj.groupby("_fips5", as_index=False)[["_early", "_late"]].mean()
        out = {}
        for _, r in agg.iterrows():
            f5 = r["_fips5"]
            early_v = float(r["_early"])
            late_v = float(r["_late"])
            out[f5] = {
                "early_rate_mm_yr": early_v,
                "late_rate_mm_yr": late_v,
                "rate_change_mm_yr": late_v - early_v,
            }
        return out
    except Exception as e:
        print(f"InSAR subsidence aggregation failed: {e}", file=sys.stderr)
        return {}


def load_groundwater_period_means() -> tuple[dict[str, float], dict[str, float], str]:
    """
    Uses measurements.csv if present. If missing, falls back to sjv_county_summary.
    Pre: 2012-2014; Post: 2018-2022.
    """
    ms_candidates = [ROOT / "data/raw/groundwater/measurements.csv", ROOT / "measurements.csv"]
    ms_path = next((p for p in ms_candidates if p.is_file()), None)
    if ms_path is None:
        return {}, {}, "Groundwater: measurements.csv not found; using summary means."

    # detect date column
    header = pd.read_csv(ms_path, nrows=0).columns.tolist()
    date_col = None
    for c in header:
        cl = c.lower().replace(" ", "_")
        if "date" in cl:
            date_col = c
            break
    if date_col is None:
        for c in header:
            if c.lower() in ("year", "yr", "measurement_year"):
                date_col = c
                break
    if date_col is None:
        return {}, {}, "Groundwater: no date column in measurements.csv; using summary means."

    usecols = ["county_name", "gwe", date_col]
    for c in usecols:
        if c not in header:
            return {}, {}, "Groundwater: missing expected columns in measurements.csv; using summary means."

    pre_sums: dict[str, float] = {}
    pre_n: dict[str, int] = {}
    post_sums: dict[str, float] = {}
    post_n: dict[str, int] = {}

    reader = pd.read_csv(
        ms_path,
        chunksize=200_000,
        usecols=usecols,
        dtype={"county_name": "string"},
        engine="c",
    )
    for chunk in reader:
        chunk["gwe"] = pd.to_numeric(chunk["gwe"], errors="coerce")
        chunk = chunk.dropna(subset=["gwe"])
        cn = chunk["county_name"].astype(str).str.strip().str.lower()
        chunk["_f5"] = cn.map(lambda x: NORM_TO_FIPS5.get(x.replace(" ", "_")))
        chunk = chunk.loc[chunk["_f5"].isin(SJV_FIPS5)].copy()

        if date_col.lower() in ("year", "yr", "measurement_year"):
            yr = pd.to_numeric(chunk[date_col], errors="coerce").astype("Int64")
        else:
            dt = pd.to_datetime(chunk[date_col], errors="coerce", utc=True)
            yr = dt.dt.year.astype("Int64")
        chunk["_yr"] = yr
        chunk = chunk.loc[chunk["_yr"].notna()].copy()
        y = chunk["_yr"].astype(int)
        m_pre = y.isin([2012, 2013, 2014])
        m_post = y.isin([2018, 2019, 2020, 2021, 2022])
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
        "Groundwater means from CASGEM measurement dates (2012–2014 vs 2018–2022).",
    )


def build_county_feature_collection(
    counties: gpd.GeoDataFrame,
    base_summary: pd.DataFrame,
    farm_summary: pd.DataFrame,
    fallow12: dict[str, float],
    fallow22: dict[str, float],
    inc14: dict[str, float],
    inc21: dict[str, float],
    gwe_pre: dict[str, float],
    gwe_post: dict[str, float],
    gwe_note: str,
    well_pre_post: dict[str, dict[str, int]],
    ces_scores: dict[str, float],
    subsidence: dict[str, dict[str, float]],
    regulation_by_county: dict[str, dict],
) -> dict:
    feats = []
    for f5 in sorted(SJV_FIPS5, key=lambda x: COUNTY_NAME[x]):
        geom = counties.loc[counties["GEOID"] == f5, "geometry"]
        if geom.empty:
            continue

        # base summary (for fallback wells + county mean gwe)
        row = base_summary.loc[base_summary["county_fips5"] == f5]
        row0 = row.iloc[0] if not row.empty else None
        wells_total = int(float(row0["well_failure_count"])) if row0 is not None else 0
        gwe_fallback = float(row0["mean_groundwater_elevation_ft"]) if row0 is not None else None

        # farm counts (2012/2022)
        f12 = farm_summary.loc[(farm_summary["county_fips5"] == f5) & (farm_summary["year"] == 2012)]
        f22 = farm_summary.loc[(farm_summary["county_fips5"] == f5) & (farm_summary["year"] == 2022)]
        f12 = f12.iloc[0] if not f12.empty else None
        f22 = f22.iloc[0] if not f22.empty else None

        def pick_gwe(period: str) -> tuple[float | None, str]:
            if period == "pre":
                if f5 in gwe_pre:
                    return float(gwe_pre[f5]), "measurements"
                return gwe_fallback, "summary"
            if f5 in gwe_post:
                return float(gwe_post[f5]), "measurements"
            return gwe_fallback, "summary"

        gpre, gpre_src = pick_gwe("pre")
        gpost, gpost_src = pick_gwe("post")

        pre = {
            "gwe_ft": round(gpre, 2) if gpre is not None else None,
            "gwe_src": gpre_src,
            "fallow_acres": round(float(fallow12.get(f5, 0.0)), 1),
            "median_income": int(inc14[f5]) if f5 in inc14 else None,
            "well_failures_issue_start": int(well_pre_post.get(f5, {}).get("pre", 0)),
            "well_failures_total": wells_total,
            "total_farms": int(f12["total_farms"]) if f12 is not None else None,
            "small_farms": int(f12["small_farms"]) if f12 is not None else None,
            "large_farms": int(f12["large_farms"]) if f12 is not None else None,
        }
        post = {
            "gwe_ft": round(gpost, 2) if gpost is not None else None,
            "gwe_src": gpost_src,
            "fallow_acres": round(float(fallow22.get(f5, 0.0)), 1),
            "median_income": int(inc21[f5]) if f5 in inc21 else None,
            "well_failures_issue_start": int(well_pre_post.get(f5, {}).get("post", 0)),
            "well_failures_total": wells_total,
            "total_farms": int(f22["total_farms"]) if f22 is not None else None,
            "small_farms": int(f22["small_farms"]) if f22 is not None else None,
            "large_farms": int(f22["large_farms"]) if f22 is not None else None,
        }
        delta = {
            "gwe_ft": (post["gwe_ft"] - pre["gwe_ft"]) if (post["gwe_ft"] is not None and pre["gwe_ft"] is not None) else None,
            "fallow_acres": (post["fallow_acres"] - pre["fallow_acres"]) if (post["fallow_acres"] is not None and pre["fallow_acres"] is not None) else None,
            "median_income": (post["median_income"] - pre["median_income"]) if (post["median_income"] is not None and pre["median_income"] is not None) else None,
            "well_failures_issue_start": post["well_failures_issue_start"] - pre["well_failures_issue_start"],
            "total_farms": (post["total_farms"] - pre["total_farms"]) if (post["total_farms"] is not None and pre["total_farms"] is not None) else None,
            "small_farms": (post["small_farms"] - pre["small_farms"]) if (post["small_farms"] is not None and pre["small_farms"] is not None) else None,
            "large_farms": (post["large_farms"] - pre["large_farms"]) if (post["large_farms"] is not None and pre["large_farms"] is not None) else None,
            "small_farm_loss": (pre["small_farms"] - post["small_farms"]) if (post["small_farms"] is not None and pre["small_farms"] is not None) else None,
        }

        ces = ces_scores.get(f5)
        sub = subsidence.get(f5)
        reg = regulation_by_county.get(f5)

        feats.append(
            {
                "type": "Feature",
                "id": f5,
                "properties": {
                    "fips5": f5,
                    "name": COUNTY_NAME[f5],
                    "notes": {
                        "gwe": gwe_note,
                        "wells": "Well failures use Approximate Issue Start Date to bin pre/post. Total well failures are cumulative reports.",
                        "subsidence": (
                            "Subsidence rates are from DWR TRE Altamira InSAR point data (early 2015–2017 vs late 2018–2022) when available."
                            if sub is not None
                            else "Subsidence layer unavailable (download/schema)."
                        ),
                        "ces": "CalEnviroScreen is aggregated to county as mean tract score percentile (best-effort field match)."
                        if ces is not None
                        else "CalEnviroScreen layer unavailable (download/schema).",
                    },
                    "pre": pre,
                    "post": post,
                    "delta": delta,
                    "ces_score_pct": round(float(ces), 2) if ces is not None else None,
                    "subsidence": sub,
                    "regulation": reg,
                },
                "geometry": mapping(geom.iloc[0]),
            }
        )
    return {"type": "FeatureCollection", "features": feats}


def attach_gsp_metrics(gsp: gpd.GeoDataFrame, wells_by_gsp: dict[str, dict]) -> gpd.GeoDataFrame:
    out = gsp.copy()
    if "GSP_ID" in out.columns:
        out["GSP_ID"] = out["GSP_ID"].astype(str)
        out["well_pre_raw"] = out["GSP_ID"].map(lambda x: (wells_by_gsp.get(x) or {}).get("well_pre_raw"))
        out["well_post_raw"] = out["GSP_ID"].map(lambda x: (wells_by_gsp.get(x) or {}).get("well_post_raw"))
        out["well_pre_adj"] = out["GSP_ID"].map(lambda x: (wells_by_gsp.get(x) or {}).get("well_pre_adj"))
        out["well_post_adj"] = out["GSP_ID"].map(lambda x: (wells_by_gsp.get(x) or {}).get("well_post_adj"))
        # Make zeros explicit for display (many GSPs have no joined points)
        out["well_pre_raw"] = out["well_pre_raw"].fillna(0).astype(float)
        out["well_post_raw"] = out["well_post_raw"].fillna(0).astype(float)
        out["well_pre_adj"] = out["well_pre_adj"].fillna(0).astype(float)
        out["well_post_adj"] = out["well_post_adj"].fillna(0).astype(float)
        out["well_delta_raw"] = out["well_post_raw"] - out["well_pre_raw"]
        out["well_delta_adj"] = out["well_post_adj"] - out["well_pre_adj"]
    return out


def build_subbasin_points_from_gsp(gsp: gpd.GeoDataFrame) -> list[dict]:
    """
    Build subbasin-level aggregates for the *scatter plot*.

    The map stays GSP-wise (plan areas). In GSP mode, the scatter switches to
    subbasin-wise points by aggregating existing per-GSP metrics.
    """
    if gsp is None or gsp.empty:
        return []

    if "Basin_Subbasin_Name" not in gsp.columns:
        return []

    df = gsp.copy()
    df["Basin_Subbasin_Name"] = df["Basin_Subbasin_Name"].fillna("").astype(str).str.strip()
    df = df[df["Basin_Subbasin_Name"] != ""].copy()
    if df.empty:
        return []

    # area weights for intensive metrics
    try:
        df["_area_w"] = df.to_crs(3310).geometry.area.fillna(0).astype(float)
    except Exception:
        df["_area_w"] = 1.0

    def wmean(series: pd.Series, weights: pd.Series) -> float | None:
        s = pd.to_numeric(series, errors="coerce")
        w = pd.to_numeric(weights, errors="coerce").fillna(0)
        m = s.notna() & (w > 0)
        if not m.any():
            return None
        return float((s[m] * w[m]).sum() / w[m].sum())

    def ssum(series: pd.Series) -> float:
        return float(pd.to_numeric(series, errors="coerce").fillna(0).sum())

    def pick_date_min(series: pd.Series) -> str | None:
        s = pd.to_datetime(series, errors="coerce")
        if s.dropna().empty:
            return None
        return str(s.dropna().min().date())

    out: list[dict] = []
    for sub_name, g in df.groupby("Basin_Subbasin_Name", dropna=False):
        posted_min = pick_date_min(g["Date_Posted"]) if "Date_Posted" in g.columns else None
        status_counts = (
            g["Status"].fillna("Unknown").astype(str).value_counts().to_dict()
            if "Status" in g.columns
            else {}
        )

        # additive across GSPs
        well_pre_raw = ssum(g["well_pre_raw"]) if "well_pre_raw" in g.columns else 0.0
        well_post_raw = ssum(g["well_post_raw"]) if "well_post_raw" in g.columns else 0.0
        well_pre_adj = ssum(g["well_pre_adj"]) if "well_pre_adj" in g.columns else 0.0
        well_post_adj = ssum(g["well_post_adj"]) if "well_post_adj" in g.columns else 0.0
        fallow_pre = ssum(g["fallow_pre_acres"]) if "fallow_pre_acres" in g.columns else 0.0
        fallow_post = ssum(g["fallow_post_acres"]) if "fallow_post_acres" in g.columns else 0.0

        # intensive (area-weighted)
        gwe_pre = wmean(g["gwe_pre_mnm"], g["_area_w"]) if "gwe_pre_mnm" in g.columns else None
        gwe_post = wmean(g["gwe_post_mnm"], g["_area_w"]) if "gwe_post_mnm" in g.columns else None
        med_field_pre = (
            wmean(g["median_field_pre_acres"], g["_area_w"]) if "median_field_pre_acres" in g.columns else None
        )
        med_field_post = (
            wmean(g["median_field_post_acres"], g["_area_w"]) if "median_field_post_acres" in g.columns else None
        )

        out.append(
            {
                "subbasin_name": sub_name,
                "n_gsp": int(len(g)),
                "date_posted_min": posted_min,
                "status_counts": status_counts,
                "well_pre_raw": well_pre_raw,
                "well_post_raw": well_post_raw,
                "well_delta_raw": well_post_raw - well_pre_raw,
                "well_pre_adj": well_pre_adj,
                "well_post_adj": well_post_adj,
                "well_delta_adj": well_post_adj - well_pre_adj,
                "fallow_pre_acres": fallow_pre,
                "fallow_post_acres": fallow_post,
                "fallow_delta_acres": fallow_post - fallow_pre,
                "gwe_pre_mnm": gwe_pre,
                "gwe_post_mnm": gwe_post,
                "gwe_delta_mnm": (None if (gwe_pre is None or gwe_post is None) else float(gwe_post - gwe_pre)),
                "median_field_pre_acres": med_field_pre,
                "median_field_post_acres": med_field_post,
                "median_field_delta_acres": (
                    None if (med_field_pre is None or med_field_post is None) else float(med_field_post - med_field_pre)
                ),
            }
        )

    out.sort(key=lambda r: (r.get("subbasin_name") or "").lower())
    return out


def attach_gsp_groundwater_mnm(gsp: gpd.GeoDataFrame, gwe_by_gsp: dict[str, dict]) -> gpd.GeoDataFrame:
    out = gsp.copy()
    if "GSP_ID" in out.columns:
        out["GSP_ID"] = out["GSP_ID"].astype(str)
        out["gwe_pre_mnm"] = out["GSP_ID"].map(lambda x: (gwe_by_gsp.get(x) or {}).get("gwe_pre_mnm"))
        out["gwe_post_mnm"] = out["GSP_ID"].map(lambda x: (gwe_by_gsp.get(x) or {}).get("gwe_post_mnm"))
        out["gwe_delta_mnm"] = out["GSP_ID"].map(lambda x: (gwe_by_gsp.get(x) or {}).get("gwe_delta_mnm"))
    return out


def load_gsp_landuse_outputs() -> tuple[dict[str, dict], dict[str, dict], str]:
    """
    Read outputs from fetch_sjv_gsp_landuse_status.py if present in project root:
      - sjv_gsp_fallow_by_year.csv
      - sjv_gsp_fieldsize_by_year.csv

    Periods:
      - pre: WY2014 + WY2016 (mean)
      - post: WY2021 + WY2022 (mean)
    """
    fallow_path = ROOT / "sjv_gsp_fallow_by_year.csv"
    field_path = ROOT / "sjv_gsp_fieldsize_by_year.csv"
    if (not fallow_path.is_file()) or (not field_path.is_file()):
        return {}, {}, "GSP land use outputs not found; run fetch_sjv_gsp_landuse_status.py to generate CSVs."

    fallow = pd.read_csv(fallow_path, dtype={"GSP_ID": str})
    field = pd.read_csv(field_path, dtype={"GSP_ID": str})
    for df in (fallow, field):
        if "GSP_ID" not in df.columns or "Water_Year" not in df.columns:
            return {}, {}, "GSP land use CSVs missing expected columns (GSP_ID, Water_Year)."
        df["GSP_ID"] = df["GSP_ID"].astype(str)
        df["Water_Year"] = pd.to_numeric(df["Water_Year"], errors="coerce").astype("Int64")

    pre_years = [2014, 2016]
    post_years = [2021, 2022]

    # fallow metrics
    for c in ("Fallow_Acres", "Fallow_Pct"):
        if c in fallow.columns:
            fallow[c] = pd.to_numeric(fallow[c], errors="coerce")
    f_pre = fallow.loc[fallow["Water_Year"].isin(pre_years)].copy()
    f_post = fallow.loc[fallow["Water_Year"].isin(post_years)].copy()
    pre_agg = f_pre.groupby("GSP_ID").agg(fallow_pre_acres=("Fallow_Acres", "mean"), fallow_pre_pct=("Fallow_Pct", "mean"))
    post_agg = f_post.groupby("GSP_ID").agg(fallow_post_acres=("Fallow_Acres", "mean"), fallow_post_pct=("Fallow_Pct", "mean"))
    fallow_metrics = pre_agg.join(post_agg, how="outer").reset_index()
    fallow_metrics["fallow_delta_acres"] = fallow_metrics["fallow_post_acres"] - fallow_metrics["fallow_pre_acres"]
    fallow_out = {
        r["GSP_ID"]: {
            "fallow_pre_acres": (float(r["fallow_pre_acres"]) if pd.notna(r["fallow_pre_acres"]) else None),
            "fallow_post_acres": (float(r["fallow_post_acres"]) if pd.notna(r["fallow_post_acres"]) else None),
            "fallow_delta_acres": (float(r["fallow_delta_acres"]) if pd.notna(r["fallow_delta_acres"]) else None),
            "fallow_pre_pct": (float(r["fallow_pre_pct"]) if pd.notna(r["fallow_pre_pct"]) else None),
            "fallow_post_pct": (float(r["fallow_post_pct"]) if pd.notna(r["fallow_post_pct"]) else None),
        }
        for _, r in fallow_metrics.iterrows()
    }

    # field-size metrics
    for c in ("Median_Field_Acres", "Mean_Field_Acres"):
        if c in field.columns:
            field[c] = pd.to_numeric(field[c], errors="coerce")
    s_pre = field.loc[field["Water_Year"].isin(pre_years)].copy()
    s_post = field.loc[field["Water_Year"].isin(post_years)].copy()
    s_pre_agg = s_pre.groupby("GSP_ID").agg(median_field_pre_acres=("Median_Field_Acres", "mean"), mean_field_pre_acres=("Mean_Field_Acres", "mean"))
    s_post_agg = s_post.groupby("GSP_ID").agg(median_field_post_acres=("Median_Field_Acres", "mean"), mean_field_post_acres=("Mean_Field_Acres", "mean"))
    field_metrics = s_pre_agg.join(s_post_agg, how="outer").reset_index()
    field_metrics["median_field_delta_acres"] = field_metrics["median_field_post_acres"] - field_metrics["median_field_pre_acres"]
    field_out = {
        r["GSP_ID"]: {
            "median_field_pre_acres": (float(r["median_field_pre_acres"]) if pd.notna(r["median_field_pre_acres"]) else None),
            "median_field_post_acres": (float(r["median_field_post_acres"]) if pd.notna(r["median_field_post_acres"]) else None),
            "median_field_delta_acres": (float(r["median_field_delta_acres"]) if pd.notna(r["median_field_delta_acres"]) else None),
            "mean_field_pre_acres": (float(r["mean_field_pre_acres"]) if pd.notna(r["mean_field_pre_acres"]) else None),
            "mean_field_post_acres": (float(r["mean_field_post_acres"]) if pd.notna(r["mean_field_post_acres"]) else None),
        }
        for _, r in field_metrics.iterrows()
    }

    return fallow_out, field_out, "GSP land use metrics computed from DWR crop mapping (2014/2016 vs 2021/2022)."


def attach_gsp_landuse(gsp: gpd.GeoDataFrame, fallow_by_gsp: dict[str, dict], field_by_gsp: dict[str, dict]) -> gpd.GeoDataFrame:
    out = gsp.copy()
    if "GSP_ID" not in out.columns:
        return out
    out["GSP_ID"] = out["GSP_ID"].astype(str)

    def pick(src: dict[str, dict], gid: str, key: str):
        return (src.get(gid) or {}).get(key)

    out["fallow_pre_acres"] = out["GSP_ID"].map(lambda gid: pick(fallow_by_gsp, gid, "fallow_pre_acres"))
    out["fallow_post_acres"] = out["GSP_ID"].map(lambda gid: pick(fallow_by_gsp, gid, "fallow_post_acres"))
    out["fallow_delta_acres"] = out["GSP_ID"].map(lambda gid: pick(fallow_by_gsp, gid, "fallow_delta_acres"))
    out["fallow_pre_pct"] = out["GSP_ID"].map(lambda gid: pick(fallow_by_gsp, gid, "fallow_pre_pct"))
    out["fallow_post_pct"] = out["GSP_ID"].map(lambda gid: pick(fallow_by_gsp, gid, "fallow_post_pct"))

    out["median_field_pre_acres"] = out["GSP_ID"].map(lambda gid: pick(field_by_gsp, gid, "median_field_pre_acres"))
    out["median_field_post_acres"] = out["GSP_ID"].map(lambda gid: pick(field_by_gsp, gid, "median_field_post_acres"))
    out["median_field_delta_acres"] = out["GSP_ID"].map(lambda gid: pick(field_by_gsp, gid, "median_field_delta_acres"))
    out["mean_field_pre_acres"] = out["GSP_ID"].map(lambda gid: pick(field_by_gsp, gid, "mean_field_pre_acres"))
    out["mean_field_post_acres"] = out["GSP_ID"].map(lambda gid: pick(field_by_gsp, gid, "mean_field_post_acres"))
    return out

def load_fallow_and_income() -> tuple[dict[str, float], dict[str, float], dict[str, float], dict[str, float]]:
    cdl12 = ROOT / "data/raw/land_use/cdl_acreage_2012.csv"
    cdl22 = ROOT / "data/raw/land_use/cdl_acreage_2022.csv"
    acs14 = ROOT / "data/raw/socioeconomic/acs5_2014.csv"
    acs21 = ROOT / "data/raw/socioeconomic/acs5_2021.csv"
    for p in (cdl12, cdl22, acs14, acs21):
        if not p.is_file():
            raise FileNotFoundError(f"Missing {p}")

    def fallow(path: Path, year: int) -> dict[str, float]:
        df = pd.read_csv(path, dtype={"county_fips5": str})
        df["county_fips5"] = df["county_fips5"].astype(str).str.zfill(5)
        sub = df.loc[(df["cdl_year"] == year) & (df["category"].astype(str).str.strip() == "Fallow/Idle Cropland")]
        g = sub.groupby("county_fips5", as_index=False)["acreage"].sum()
        return {r.county_fips5: float(r.acreage) for _, r in g.iterrows()}

    def income(path: Path) -> dict[str, float]:
        df = pd.read_csv(path, dtype={"county_fips5": str})
        df["county_fips5"] = df["county_fips5"].astype(str).str.zfill(5)
        out = {}
        for _, r in df.iterrows():
            f5 = r["county_fips5"]
            v = r.get("B19013_001E")
            if pd.notna(v) and str(v) not in {"", "-666666666"}:
                try:
                    out[f5] = float(v)
                except Exception:
                    pass
        return out

    return fallow(cdl12, 2012), fallow(cdl22, 2022), income(acs14), income(acs21)


def load_farm_summary() -> pd.DataFrame:
    path = ROOT / "data/raw/farm_size/farm_operations.json"
    if not path.is_file():
        raise FileNotFoundError(f"Missing {path}")
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    rows = []
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
        rows.append(
            {
                "county_fips5": f5,
                "year": yr,
                "bucket": b,
                "ops": int(parse_nass_int(r.get("Value"))),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError("No farm rows matched expected disjoint bins.")
    wide = (
        df.pivot_table(index=["county_fips5", "year"], columns="bucket", values="ops", aggfunc="sum", fill_value=0)
        .reset_index()
    )
    for b in BUCKET_ORDER:
        if b not in wide.columns:
            wide[b] = 0
    wide["small_farms"] = wide["under_50"] + wide["50_179"]
    wide["large_farms"] = wide["500_999"] + wide["1000_plus"]
    wide["total_farms"] = wide[BUCKET_ORDER].sum(axis=1)
    return wide[["county_fips5", "year", "total_farms", "small_farms", "large_farms"]].copy()


# NASS bins mapping reused (disjoint bins only)
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


def render_html(payload: dict) -> str:
    json_text = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    title = html.escape(payload.get("meta", {}).get("title", "SGMA Equity Atlas"))

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>{title}</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
        integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin=""/>
  <style>
    * {{ box-sizing:border-box; }}
    /* Warm theme: light beige + dark brown */
    html,body {{ margin:0; height:100%; background:#f6f0e6; color:#2a1a12; font-family: system-ui, Segoe UI, Roboto, sans-serif; }}
    a {{ color: inherit; }}
    .btn {{
      display:inline-flex; align-items:center; justify-content:center; gap:10px;
      border:1px solid rgba(42,26,18,0.22);
      background:#6b3f2a; color:#fff; padding:12px 14px; border-radius:14px;
      font-weight:800; font-size:0.92rem; cursor:pointer; text-decoration:none;
      box-shadow: 0 10px 24px rgba(42,26,18,0.14);
    }}
    .btn.secondary {{
      background:rgba(255,255,255,0.65); color:#2a1a12;
    }}

    /* Intro story */
    .intro {{
      min-height: 100vh;
      display:flex; flex-direction:column;
      padding: 24px 16px;
      background:
        radial-gradient(1200px 600px at 20% 10%, rgba(107,63,42,0.14), transparent 55%),
        radial-gradient(900px 500px at 85% 20%, rgba(42,26,18,0.10), transparent 60%),
        linear-gradient(180deg, rgba(255,255,255,0.10), rgba(255,255,255,0.00));
    }}
    .intro .wrap {{ max-width: 980px; width:100%; margin: 0 auto; }}
    .snap {{
      height: calc(100vh - 48px);
      display:flex; align-items:center;
    }}
    .snap .card {{
      width:100%;
      border:1px solid rgba(42,26,18,0.14);
      border-radius: 22px;
      background: rgba(255,255,255,0.74);
      box-shadow: 0 16px 40px rgba(42,26,18,0.12);
      padding: 22px 20px;
    }}
    .intro h1 {{ margin:0; font-size:2.0rem; letter-spacing:-0.02em; line-height:1.05; }}
    .intro p {{ margin:10px 0 0 0; font-size:1.06rem; color:rgba(42,26,18,0.82); line-height:1.35; }}
    .kicker {{
      display:inline-flex; align-items:center; gap:10px;
      font-weight:900; font-size:0.82rem; letter-spacing:0.08em; text-transform:uppercase;
      color: rgba(42,26,18,0.75);
      margin-bottom: 12px;
    }}
    .scrollHint {{ margin-top: 14px; font-size:0.9rem; color:rgba(42,26,18,0.70); }}
    .introGrid {{
      display:grid; grid-template-columns: 1fr; gap: 12px; margin-top: 14px;
    }}
    .mini {{
      border:1px solid rgba(42,26,18,0.10);
      border-radius: 16px;
      background: rgba(255,255,255,0.62);
      padding: 12px 12px;
      color: rgba(42,26,18,0.80);
      font-size: 0.98rem;
      line-height: 1.25;
    }}
    .muted {{ color: rgba(42,26,18,0.70); }}
    .introActions {{ display:flex; flex-wrap:wrap; gap:10px; margin-top: 14px; }}
    .divider {{
      height: 1px; background: rgba(42,26,18,0.10);
      margin: 14px 0;
    }}

    .app {{ height:100%; display:flex; flex-direction:column; }}
    .top {{
      padding:14px 16px; border-bottom:1px solid rgba(42,26,18,0.12);
      display:flex; align-items:flex-end; justify-content:space-between; gap:12px;
    }}
    .top h1 {{ margin:0; font-size:1.08rem; font-weight:800; }}
    .top p {{ margin:4px 0 0 0; font-size:0.78rem; color:rgba(42,26,18,0.72); max-width: 760px; }}
    .toggles {{ display:inline-flex; border:1px solid rgba(42,26,18,0.22); border-radius:10px; overflow:hidden; background:rgba(255,255,255,0.5); }}
    .toggles button {{
      border:none; background:rgba(255,255,255,0.55); color:#3a241a; padding:10px 12px; cursor:pointer;
      font-size:0.82rem; font-weight:700; min-width: 150px;
    }}
    .toggles button.active {{ background:#6b3f2a; color:#fff; }}
    .main {{ flex:1; min-height:0; padding:12px; display:grid; grid-template-columns: 1.1fr 1.35fr 1.05fr; gap:12px; }}
    .panel {{ border:1px solid rgba(42,26,18,0.14); border-radius:14px; box-shadow: 0 6px 18px rgba(42,26,18,0.08); background:rgba(255,255,255,0.78); backdrop-filter: blur(2px); display:flex; flex-direction:column; min-height:0; }}
    .hdr {{ padding:12px; border-bottom:1px solid rgba(42,26,18,0.08); display:flex; align-items:center; justify-content:space-between; gap:10px; }}
    .hdr h2 {{ margin:0; font-size:0.92rem; font-weight:800; }}
    .body {{ padding:12px; min-height:0; flex:1; }}
    #map {{ height: 360px; width:100%; border-radius:10px; }}
    .legend {{
      position: absolute; right: 18px; bottom: 18px; z-index: 800;
      background: rgba(255,255,255,0.90); border: 1px solid rgba(42,26,18,0.18);
      border-radius: 10px; padding: 10px 12px; box-shadow: 0 1px 8px rgba(0,0,0,0.08);
      min-width: 220px; max-width: 260px;
      font-size: 0.76rem; color: #3a241a;
    }}
    .legend.small {{ min-width: 190px; max-width: 220px; padding: 8px 10px; }}
    .legend .drag {{ cursor: move; user-select: none; }}
    .legend h3 {{ margin: 0 0 8px 0; font-size: 0.82rem; font-weight: 800; color:#2a1a12; }}
    .legend .bar {{ height: 8px; border-radius: 5px; border: 1px solid rgba(42,26,18,0.22); }}
    .legend .scale {{ display:flex; justify-content:space-between; margin-top:6px; font-variant-numeric: tabular-nums; color:rgba(42,26,18,0.72); }}
    .legend .cats {{ margin-top: 8px; display:flex; flex-direction:column; gap:4px; }}
    .legend .cat {{ display:flex; align-items:center; gap:8px; }}
    .legend .sw {{ width: 14px; height: 10px; border-radius: 3px; border: 1px solid rgba(0,0,0,0.25); }}
    .pill {{ font-size:0.74rem; color:#3a241a; background:rgba(255,255,255,0.55); border:1px solid rgba(42,26,18,0.18); padding:6px 8px; border-radius:999px; }}
    .mini-toggle {{ display:inline-flex; align-items:center; gap:8px; font-size:0.78rem; color:#3a241a; user-select:none; }}
    .mini-toggle input {{ width:16px; height:16px; accent-color:#6b3f2a; }}
    select {{
      font-size:0.82rem; padding:8px 10px; border-radius:12px; border:1px solid rgba(42,26,18,0.22); background:rgba(255,255,255,0.75); min-width: 320px;
    }}
    #chartWrap {{ height: 440px; }}
    #chart {{ width:100% !important; height:100% !important; }}
    .grid {{ display:grid; grid-template-columns: 1fr 1fr; gap:10px; }}
    .stat {{ border:1px solid rgba(42,26,18,0.10); border-radius:12px; padding:10px; background:rgba(255,255,255,0.55); }}
    .k {{ font-size:0.74rem; color:rgba(42,26,18,0.72); margin-bottom:6px; }}
    .v {{ font-size:0.98rem; font-weight:900; font-variant-numeric: tabular-nums; }}
    .sub {{ margin-top:4px; font-size:0.72rem; color:rgba(42,26,18,0.66); line-height:1.2; }}
    .note {{ margin-top:10px; font-size:0.72rem; color:rgba(42,26,18,0.66); line-height:1.25; }}
  </style>
</head>
<body>

<div class="intro" id="top">
  <div class="wrap">
    <section class="snap">
      <div class="card">
        <div class="kicker">San Joaquin Valley • Subsidence</div>
        <h1>The San Joaquin Central Valley is sinking at record-breaking rates.</h1>
        <p class="muted">Scroll to see the stakes, then explore what changed after SGMA.</p>
        <div class="scrollHint">Scroll ↓</div>
      </div>
    </section>

    <section class="snap">
      <div class="card">
        <div class="kicker">Overextraction</div>
        <h1>Some regions have seen subsidence rates above 30 cm/yr.</h1>
        <p>In many places, groundwater pumping has outpaced recharge—leading to land sinking, infrastructure damage, and higher costs for communities.</p>
        <div class="scrollHint">Scroll ↓</div>
      </div>
    </section>

    <section class="snap">
      <div class="card">
        <div class="kicker">Policy response</div>
        <h1>Since 2014, SGMA has been trying to bring groundwater use into balance.</h1>
        <p class="muted">Implementation is uneven: some plan areas are further along (approved/adequate) while others remain incomplete or under review.</p>
        <div class="scrollHint">Scroll ↓</div>
      </div>
    </section>

    <section class="snap">
      <div class="card">
        <div class="kicker">Equity question</div>
        <h1>Will SGMA stop record-breaking subsidence—or shift costs onto small farmers?</h1>
        <p>Press the button below to explore how regions in the SJV changed since SGMA implementation.</p>
        <div class="introActions">
          <a class="btn" href="#atlas" id="goAtlas">Press here to explore the atlas</a>
          <a class="btn secondary" href="#atlas" onclick="document.getElementById('atlas').scrollIntoView({{behavior:'smooth'}}); return false;">Jump to map</a>
        </div>
        <div class="divider"></div>
        <div class="introGrid">
          <div class="mini"><b>Left:</b> Map of counties + GSA outline. Toggle <b>GSP</b> to view plan areas and regulation progress.</div>
          <div class="mini"><b>Middle:</b> Comparison scatter (county mode) or <b>subbasin scatter</b> (GSP mode) to test “mechanism” stories.</div>
          <div class="mini"><b>Right:</b> Click a county (or a GSP) to see <b>Pre / Post / Δ</b> (or <b>Pre approval / Post approval / Δ</b>).</div>
        </div>
      </div>
    </section>
  </div>
</div>

<div class="app" id="atlas">
  <div class="top">
    <div>
      <h1>SGMA Equity Pathways — San Joaquin Valley</h1>
      <p>Orientation: left map (counties + GSA outline; toggle GSP for plan areas). Middle compares regions. Right shows selected county/GSP metrics for Pre, Post, and Change (Δ).</p>
    </div>
    <div class="toggles" role="group" aria-label="Period toggle">
      <button id="btn-pre" class="active" type="button">Pre-SGMA</button>
      <button id="btn-post" type="button">Post-SGMA</button>
      <button id="btn-delta" type="button">Change (Δ)</button>
    </div>
  </div>

  <div class="main">
    <div class="panel">
      <div class="hdr">
        <h2>Map</h2>
        <div style="display:flex; align-items:center; gap:10px;">
          <label class="mini-toggle" title="Show GSP plan status polygons">
            <input type="checkbox" id="toggleGsp" />
            Show GSP status
          </label>
          <span class="pill" id="mapPill">Choropleth: —</span>
        </div>
      </div>
      <div class="body">
        <div id="map" style="position:relative;">
          <div class="legend" id="mapLegend" style="display:none;">
            <h3 id="legTitle" class="drag">Legend</h3>
            <div class="bar" id="legBar"></div>
            <div class="scale"><span id="legLo"></span><span id="legHi"></span></div>
            <div class="cats" id="legCats" style="display:none;"></div>
          </div>
        </div>
        <div class="note">
          Click a county to select. Choropleth follows the scatter X metric.
          Turn on <b>Show GSP status</b> to reveal plan-area status + completion date shading.
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="hdr">
        <h2>Mechanism checks (scatter)</h2>
        <select id="viewSelect">
          <option value="v1">Overdraft → harm: groundwater (Pre) vs well failures (Pre)</option>
          <option value="v2">Overdraft → costs: groundwater change vs subsidence rate change</option>
          <option value="v3">Policy adjustment: groundwater change vs fallow change</option>
          <option value="v4">Distribution: small farm loss vs groundwater change</option>
          <option value="v5">Equity context: CalEnviroScreen vs well failures (Pre)</option>
        </select>
      </div>
      <div class="body" style="padding:10px 12px;">
        <div id="chartWrap"><canvas id="chart"></canvas></div>
        <div class="note" id="chartNote"></div>
      </div>
    </div>

    <div class="panel">
      <div class="hdr">
        <h2 id="rhsTitle">County card</h2>
        <span class="pill" id="periodPill">Period: Pre-SGMA</span>
      </div>
      <div class="body">
        <div id="countyCard">
          <div style="font-size:1rem; font-weight:900;" id="countyTitle">Select a county</div>
          <div class="grid" style="margin-top:10px;">
            <div class="stat"><div class="k">Well failures (issue start)</div><div class="v" id="c-well">—</div><div class="sub">Binned by Approx. Issue Start Date</div></div>
            <div class="stat"><div class="k">Groundwater elevation</div><div class="v" id="c-gwe">—</div><div class="sub" id="c-gwe-sub">ft (source: —)</div></div>
            <div class="stat"><div class="k">Fallow cropland</div><div class="v" id="c-fallow">—</div><div class="sub">Fallow/Idle Cropland (CDL)</div></div>
            <div class="stat"><div class="k">Farm consolidation</div><div class="v" id="c-farm">—</div><div class="sub">Small (&lt;180 ac) and Large (500+ ac)</div></div>
            <div class="stat"><div class="k">Median income</div><div class="v" id="c-inc">—</div><div class="sub">ACS (2014 / 2021)</div></div>
            <div class="stat"><div class="k">Vulnerability (CES)</div><div class="v" id="c-ces">—</div><div class="sub">Mean tract score percentile (county)</div></div>
          </div>
          <div class="note" id="countyNote"></div>
        </div>

        <div id="gspCard" style="display:none;">
          <div style="font-size:1rem; font-weight:900;" id="gspTitle">Select a GSP</div>
          <div class="grid" style="margin-top:10px;">
            <div class="stat"><div class="k">Plan status</div><div class="v" id="g-status">—</div><div class="sub" id="g-posted">Date posted: —</div></div>
            <div class="stat"><div class="k">Well failures (adj share)</div><div class="v" id="g-well">—</div><div class="sub">Pre 2012–14 vs Post 2018–22</div></div>
            <div class="stat"><div class="k">Groundwater elevation (MNM)</div><div class="v" id="g-gwe">—</div><div class="sub">Pre 2012–14 vs Post 2018–22 (when available)</div></div>
            <div class="stat"><div class="k">Fallow acres (DWR crop mapping)</div><div class="v" id="g-fallow">—</div><div class="sub">Pre WY2014/2016 vs Post WY2021/2022</div></div>
            <div class="stat"><div class="k">Median field size (acres)</div><div class="v" id="g-field">—</div><div class="sub">Pre WY2014/2016 vs Post WY2021/2022</div></div>
            <div class="stat"><div class="k">Note</div><div class="v" id="g-note">—</div><div class="sub">If not approved/adequate, Post=Pre and Δ=0</div></div>
          </div>
          <div class="note" id="gspNote"></div>
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
let period = 'pre'; // pre | post | delta
let selected = null;
let viewId = 'v1';

function featByFips(fips) {{
  return ATLAS.counties.features.find(f => f.properties.fips5 === fips) || null;
}}
function num(v) {{ return (v===null || v===undefined || Number.isNaN(v)) ? null : Number(v); }}
function fmtInt(v) {{ if (v===null||v===undefined||Number.isNaN(v)) return 'n/a'; return Math.round(v).toLocaleString(); }}
function fmtFloat(v,d=1) {{ if (v===null||v===undefined||Number.isNaN(v)) return 'n/a'; return Number(v).toFixed(d); }}
function fmtMoney(v) {{ if (v===null||v===undefined||Number.isNaN(v)) return 'n/a'; return '$' + Math.round(v).toLocaleString(); }}
function signFmt(v, fmt) {{
  if (v===null||v===undefined||Number.isNaN(v)) return 'n/a';
  const s = v>=0 ? '+' : '';
  return s + fmt(v);
}}

function metric(feat, key) {{
  const p = feat.properties;
  const pre = p.pre, post = p.post, d = p.delta;
  if (period === 'pre') return pre[key];
  if (period === 'post') return post[key];
  return d[key];
}}

const VIEWS = {{
  v1: {{
    title: 'Overdraft → harm: groundwater (Pre) vs well failures (Pre)',
    note: 'Uses well failures binned by Approximate Issue Start Date (2012–2014).',
    xLabel: 'Pre groundwater elevation (ft)',
    yLabel: 'Well failures (issue start, 2012–2014)',
    x: (f) => f.properties.pre.gwe_ft,
    y: (f) => f.properties.pre.well_failures_issue_start,
    mapLabel: 'Pre groundwater (ft)',
    map: (f) => f.properties.pre.gwe_ft,
  }},
  v2: {{
    title: 'Overdraft → costs: groundwater change vs subsidence rate change',
    note: 'Subsidence rate change is late(2018–2022) - early(2015–2017) when available.',
    xLabel: 'Groundwater change (Post - Pre, ft)',
    yLabel: 'Subsidence rate change (mm/yr)',
    x: (f) => f.properties.delta.gwe_ft,
    y: (f) => (f.properties.subsidence ? f.properties.subsidence.rate_change_mm_yr : null),
    mapLabel: 'GW change (ft)',
    map: (f) => f.properties.delta.gwe_ft,
  }},
  v3: {{
    title: 'Policy adjustment: groundwater change vs fallow change',
    note: 'Fallow is CDL Fallow/Idle Cropland (2022 - 2012).',
    xLabel: 'Groundwater change (Post - Pre, ft)',
    yLabel: 'Fallow change (acres)',
    x: (f) => f.properties.delta.gwe_ft,
    y: (f) => f.properties.delta.fallow_acres,
    mapLabel: 'Fallow change (ac)',
    map: (f) => f.properties.delta.fallow_acres,
  }},
  v4: {{
    title: 'Distribution: small farm loss vs groundwater change',
    note: 'Small farm loss = small farms 2012 - small farms 2022 (positive = loss).',
    xLabel: 'Groundwater change (ft)',
    yLabel: 'Small farm loss (count)',
    x: (f) => f.properties.delta.gwe_ft,
    y: (f) => f.properties.delta.small_farm_loss,
    mapLabel: 'Small farm loss',
    map: (f) => f.properties.delta.small_farm_loss,
  }},
  v5: {{
    title: 'Equity context: CalEnviroScreen vs well failures (Pre)',
    note: 'CES is county mean of tract score percentile (higher = more burden).',
    xLabel: 'CalEnviroScreen score percentile (county mean)',
    yLabel: 'Well failures (issue start, 2012–2014)',
    x: (f) => f.properties.ces_score_pct,
    y: (f) => f.properties.pre.well_failures_issue_start,
    mapLabel: 'CES score pct',
    map: (f) => f.properties.ces_score_pct,
  }},
}};

function extent(vals) {{
  let lo = Infinity, hi = -Infinity;
  for (const v of vals) {{
    if (v===null || v===undefined || Number.isNaN(v)) continue;
    lo = Math.min(lo, v); hi = Math.max(hi, v);
  }}
  if (!isFinite(lo) || !isFinite(hi)) return [0,1];
  if (lo===hi) {{ lo-=1; hi+=1; }}
  return [lo,hi];
}}
function lerp(a,b,t) {{ return a+(b-a)*t; }}
function ramp(t) {{
  const r = Math.round(lerp(239, 37, t));
  const g = Math.round(lerp(246, 99, t));
  const b = Math.round(lerp(255, 235, t));
  return 'rgb(' + r + ',' + g + ',' + b + ')';
}}

// Map
const map = L.map('map', {{ zoomControl:true, attributionControl:false }}).setView([36.6,-119.8], 7.7);
L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{ maxZoom:19 }}).addTo(map);
const gsaLayer = L.geoJSON(ATLAS.gsa, {{ style: {{ color:'#1d4ed8', weight:1.0, opacity:0.9, fillOpacity:0 }}, interactive:false }}).addTo(map);

// --- GSP plan status layer (always-on background when present) ---
function parseISO(s) {{
  if (!s) return null;
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
}}
function statusHue(status) {{
  const s = (status || '').toLowerCase();
  // requested: red if inadequate/incomplete; green if approved/adequate
  if (s.includes('inadequate') || s.includes('incomplete')) return 0;   // red
  if (s.includes('approved') || s.includes('adequate')) return 120;     // green
  if (s.includes('review')) return 45;                                  // amber
  return 210;                                                           // blue/other
}}

function statusFill(status) {{
  const hue = statusHue(status);
  return 'hsl(' + hue + ',85%,58%)';
}}
function gspStyle(feat) {{
  const p = feat.properties || {{}};
  const status = p.Status || p.status || '';
  const hue = statusHue(status);
  const posted = parseISO(p.Date_Posted || p.date_posted || null);
  const range = (ATLAS._gsp_date_range || null);
  let t = null;
  if (posted && range && range.min && range.max) {{
    const d0 = parseISO(range.min), d1 = parseISO(range.max);
    if (d0 && d1 && d1.getTime() > d0.getTime()) {{
      t = (posted.getTime() - d0.getTime()) / (d1.getTime() - d0.getTime());
      t = Math.max(0, Math.min(1, t));
    }}
  }}
  // Earlier completion -> lighter; later completion -> darker. Missing date -> very light.
  const light = (t === null ? 86 : (78 - 38 * t));
  const fill = 'hsl(' + hue + ',85%,' + light + '%)';
  return {{
    color: '#111827',
    weight: 1.0,
    opacity: 0.65,
    fillColor: fill,
    fillOpacity: 0.35,
  }};
}}

function isGspMode() {{
  const el = document.getElementById('toggleGsp');
  return !!(el && el.checked);
}}

let selectedGspId = null;
function gspFeatureId(p) {{
  if (!p) return null;
  return (p.GSP_ID || p.Loc_GSP_ID || null);
}}

let selectedSubbasin = null;
function setSelectedSubbasin(subName) {{
  selectedSubbasin = subName || null;
  if (!gspLayer) return;
  gspLayer.eachLayer(l => {{
    const lp = l.feature && l.feature.properties ? l.feature.properties : {{}};
    const sb = lp.Basin_Subbasin_Name || null;
    const sel = (selectedSubbasin && sb && sb === selectedSubbasin);
    l.setStyle({{
      weight: sel ? 2.6 : 1.0,
      opacity: sel ? 0.98 : 0.65,
      fillOpacity: sel ? 0.55 : 0.35
    }});
  }});
}}

function isApprovedOrAdequate(status) {{
  const s = (status || '').toLowerCase();
  return (s.includes('approved') || s.includes('adequate'));
}}

function updatePanels() {{
  const countyCard = document.getElementById('countyCard');
  const gspCard = document.getElementById('gspCard');
  const rhsTitle = document.getElementById('rhsTitle');
  if (!countyCard || !gspCard || !rhsTitle) return;
  if (isGspMode()) {{
    countyCard.style.display = 'none';
    gspCard.style.display = 'block';
    rhsTitle.textContent = 'GSP card';
  }} else {{
    gspCard.style.display = 'none';
    countyCard.style.display = 'block';
    rhsTitle.textContent = 'County card';
  }}
}}

function updatePeriodLabelsForCounty() {{
  document.getElementById('btn-pre').textContent = 'Pre-SGMA';
  document.getElementById('btn-post').textContent = 'Post-SGMA';
  document.getElementById('btn-delta').textContent = 'Change (Δ)';
}}

function updatePeriodLabelsForGsp(feature) {{
  document.getElementById('btn-pre').textContent = 'Pre approval';
  document.getElementById('btn-post').textContent = 'Post approval';
  document.getElementById('btn-delta').textContent = 'Change (Δ)';
}}

function updateGspCard(feature) {{
  if (!feature) {{
    document.getElementById('gspTitle').textContent = 'Select a GSP';
    ['g-status','g-posted','g-well','g-gwe','g-fallow','g-field','g-note','gspNote'].forEach(id => {{
      const el = document.getElementById(id);
      if (el) el.textContent = '—';
    }});
    return;
  }}
  const p = feature.properties || {{}};
  const nm = p.Basin_Subbasin_Name || p.Loc_GSP_ID || p.GSP_ID || 'GSP';
  const status = p.Status || 'Unknown';
  const posted = p.Date_Posted || 'n/a';
  const ok = isApprovedOrAdequate(status);
  updatePeriodLabelsForGsp(feature);

  function pick(preV, postV) {{
    if (!ok) return [preV, preV, 0];
    if (preV==null || postV==null || Number.isNaN(preV) || Number.isNaN(postV)) return [preV, postV, null];
    return [preV, postV, Number(postV) - Number(preV)];
  }}
  const [wpre, wpost, wdel] = pick(p.well_pre_adj, p.well_post_adj);
  const [gpre, gpost, gdel] = pick(p.gwe_pre_mnm, p.gwe_post_mnm);
  const [fpre, fpost, fdel] = pick(p.fallow_pre_acres, p.fallow_post_acres);
  const [mspre, mspost, msdel] = pick(p.median_field_pre_acres, p.median_field_post_acres);

  function fmtNum(v, d=2) {{ return (v==null || v===undefined || Number.isNaN(v)) ? 'n/a' : Number(v).toFixed(d); }}
  function fmtAc(v) {{ return (v==null || v===undefined || Number.isNaN(v)) ? 'n/a' : Math.round(Number(v)).toLocaleString(); }}
  function showTriplet(preV, postV, delV, fmt) {{
    if (period === 'pre') return fmt(preV);
    if (period === 'post') return fmt(postV);
    if (delV==null || delV===undefined || Number.isNaN(delV)) return 'n/a';
    const s = delV>=0 ? '+' : '';
    return s + fmt(delV);
  }}

  document.getElementById('gspTitle').textContent = nm;
  document.getElementById('g-status').textContent = status;
  document.getElementById('g-posted').textContent = 'Date posted: ' + posted;
  document.getElementById('g-well').textContent = showTriplet(wpre, wpost, wdel, (v)=>fmtNum(v,2));
  document.getElementById('g-gwe').textContent = showTriplet(gpre, gpost, gdel, (v)=>fmtNum(v,1));
  document.getElementById('g-fallow').textContent = showTriplet(fpre, fpost, fdel, (v)=>fmtAc(v));
  document.getElementById('g-field').textContent = showTriplet(mspre, mspost, msdel, (v)=>fmtNum(v,1));
  document.getElementById('g-note').textContent = ok ? 'Approved/Adequate: showing pre vs post.' : 'Not approved/adequate: forcing Post=Pre (Δ=0).';
  document.getElementById('gspNote').textContent = (ATLAS.gsp_notes && ATLAS.gsp_notes.landuse) ? ATLAS.gsp_notes.landuse : '';
}}
function setSelectedGspFeature(feature) {{
  const p = feature.properties || {{}};
  selectedGspId = gspFeatureId(p);
  if (gspLayer) {{
    gspLayer.eachLayer(l => {{
      const lp = l.feature && l.feature.properties ? l.feature.properties : {{}};
      const id = gspFeatureId(lp);
      const sel = (id && selectedGspId && id === selectedGspId);
      l.setStyle({{ weight: sel ? 2.6 : 1.0, opacity: sel ? 0.95 : 0.65, fillOpacity: sel ? 0.5 : 0.35 }});
    }});
  }}
  updatePanels();
  updateGspCard(feature);
}}

let gspLayer = null;
if (ATLAS.gsp && ATLAS.gsp.type === 'FeatureCollection') {{
  gspLayer = L.geoJSON(ATLAS.gsp, {{
    style: gspStyle,
    onEachFeature: (feature, layer) => {{
      const p = feature.properties || {{}};
      const nm = p.Basin_Subbasin_Name || p.Loc_GSP_ID || p.GSP_ID || 'GSP';
      const st = p.Status || 'Unknown';
      const dp = p.Date_Posted || 'n/a';
      const wpre = (p.well_pre_raw != null ? p.well_pre_raw : 'n/a');
      const wpost = (p.well_post_raw != null ? p.well_post_raw : 'n/a');
      const wapre = (p.well_pre_adj != null ? Number(p.well_pre_adj).toFixed(2) : 'n/a');
      const wapost = (p.well_post_adj != null ? Number(p.well_post_adj).toFixed(2) : 'n/a');
      const fpre = (p.fallow_pre_acres != null ? Math.round(p.fallow_pre_acres).toLocaleString() : 'n/a');
      const fpost = (p.fallow_post_acres != null ? Math.round(p.fallow_post_acres).toLocaleString() : 'n/a');
      const medPre = (p.median_field_pre_acres != null ? Number(p.median_field_pre_acres).toFixed(1) : 'n/a');
      const medPost = (p.median_field_post_acres != null ? Number(p.median_field_post_acres).toFixed(1) : 'n/a');
      layer.bindPopup(
        '<b>' + nm + '</b>' +
        '<br/>Status: ' + st +
        '<br/>Posted: ' + dp +
        '<br/>Well failures (raw): pre ' + wpre + ', post ' + wpost +
        '<br/>Well failures (adj share): pre ' + wapre + ', post ' + wapost +
        '<br/>Fallow acres (DWR crop mapping): pre ' + fpre + ', post ' + fpost +
        '<br/>Median field size (acres): pre ' + medPre + ', post ' + medPost
      );
      layer.on('click', () => {{
        if (isGspMode()) {{
          setSelectedGspFeature(feature);
          // also highlight the whole subbasin for context
          const sb = (feature.properties || {{}}).Basin_Subbasin_Name || null;
          if (sb) setSelectedSubbasin(sb);
        }}
      }});
    }}
  }});
}}

function styleCounty(feat) {{
  const view = VIEWS[viewId];
  const vals = ATLAS.counties.features.map(f => num(view.map(f)));
  const [lo, hi] = extent(vals);
  const v = num(view.map(feat));
  let t = 0.5;
  if (v!==null) {{ t = (v-lo)/(hi-lo); t = Math.max(0, Math.min(1, t)); }}
  const sel = selected && feat.properties.fips5===selected;
  const showGsp = (document.getElementById('toggleGsp') && document.getElementById('toggleGsp').checked);
  return {{
    fillColor: ramp(t),
    fillOpacity: showGsp ? 0.12 : 0.9,
    color: sel ? '#111827' : '#6b7280',
    weight: sel ? 2.2 : 1.0,
    opacity: 1.0
  }};
}}
const countiesLayer = L.geoJSON(ATLAS.counties, {{
  style: styleCounty,
  onEachFeature: (feature, layer) => {{
    layer.on('click', () => {{
      if (isGspMode()) return;
      setSelected(feature.properties.fips5);
    }});
  }}
}}).addTo(map);
map.fitBounds(countiesLayer.getBounds(), {{ padding:[8,8] }});
if (gspLayer) gspLayer.bringToFront();
countiesLayer.bringToFront();
gsaLayer.bringToFront();

function refreshMap() {{
  const on = isGspMode();
  // mutually exclusive boundaries
  if (on) {{
    if (map.hasLayer(countiesLayer)) map.removeLayer(countiesLayer);
    if (gspLayer && !map.hasLayer(gspLayer)) gspLayer.addTo(map);
    if (gspLayer) gspLayer.bringToFront();
    gsaLayer.bringToFront();
  }} else {{
    if (gspLayer && map.hasLayer(gspLayer)) map.removeLayer(gspLayer);
    if (!map.hasLayer(countiesLayer)) countiesLayer.addTo(map);
    countiesLayer.eachLayer(l => l.setStyle(styleCounty(l.feature)));
    countiesLayer.bringToFront();
    gsaLayer.bringToFront();
  }}
  updateLegend();
}}

function updateLegend() {{
  const leg = document.getElementById('mapLegend');
  const title = document.getElementById('legTitle');
  const bar = document.getElementById('legBar');
  const lo = document.getElementById('legLo');
  const hi = document.getElementById('legHi');
  const cats = document.getElementById('legCats');
  if (!leg) return;
  leg.style.display = 'block';

  if (isGspMode()) {{
    leg.classList.add('small');
    title.textContent = 'GSP status + completion date';
    // categorical status legend + date shading note
    cats.style.display = 'flex';
    const items = [
      ['Approved', statusFill('Approved')],
      ['Adequate', statusFill('Adequate')],
      ['Inadequate', statusFill('Inadequate')],
      ['Incomplete', statusFill('Incomplete')],
      ['Review/Other', statusFill('Review')],
    ];
    cats.innerHTML = items.map(([lab, col]) => '<div class=\"cat\"><span class=\"sw\" style=\"background:' + col + '\"></span><span>' + lab + '</span></div>').join('');
    bar.style.background = 'linear-gradient(90deg, rgba(0,0,0,0.08), rgba(0,0,0,0.35))';
    lo.textContent = (ATLAS._gsp_date_range && ATLAS._gsp_date_range.min) ? ('earlier (' + ATLAS._gsp_date_range.min + ')') : 'earlier';
    hi.textContent = (ATLAS._gsp_date_range && ATLAS._gsp_date_range.max) ? ('later (' + ATLAS._gsp_date_range.max + ')') : 'later';
    return;
  }}
  leg.classList.remove('small');

  // county mode: continuous ramp for current map metric (scatter X)
  cats.style.display = 'none';
  const view = VIEWS[viewId];
  title.textContent = view.mapLabel + ' (county)';
  const vals = ATLAS.counties.features.map(f => num(view.map(f))).filter(v => v!==null);
  const ex = extent(vals);
  lo.textContent = (ex[0] != null ? String(Math.round(ex[0] * 10) / 10) : 'n/a');
  hi.textContent = (ex[1] != null ? String(Math.round(ex[1] * 10) / 10) : 'n/a');
  bar.style.background = 'linear-gradient(90deg,' + ramp(0) + ',' + ramp(1) + ')';
}}

// Make legend draggable (title is the handle)
function makeLegendDraggable() {{
  const leg = document.getElementById('mapLegend');
  const handle = document.getElementById('legTitle');
  if (!leg || !handle) return;
  let dragging = false, startX = 0, startY = 0, startLeft = 0, startTop = 0;
  handle.addEventListener('mousedown', (e) => {{
    dragging = true;
    const r = leg.getBoundingClientRect();
    startX = e.clientX; startY = e.clientY;
    startLeft = r.left; startTop = r.top;
    // switch to top/left anchored so it can move
    leg.style.right = 'auto';
    leg.style.bottom = 'auto';
    leg.style.left = startLeft + 'px';
    leg.style.top = startTop + 'px';
    e.preventDefault();
  }});
  window.addEventListener('mousemove', (e) => {{
    if (!dragging) return;
    const dx = e.clientX - startX, dy = e.clientY - startY;
    leg.style.left = (startLeft + dx) + 'px';
    leg.style.top = (startTop + dy) + 'px';
  }});
  window.addEventListener('mouseup', () => {{ dragging = false; }});
}}

// Scatter
const labelPlugin = {{
  id: 'labels',
  afterDatasetsDraw(chart) {{
    const {{ ctx }} = chart;
    const meta = chart.getDatasetMeta(0);
    ctx.save();
    ctx.font = '11px system-ui, Segoe UI, Roboto, sans-serif';
    ctx.fillStyle = '#374151';
    ctx.textBaseline = 'middle';
    for (let i=0;i<meta.data.length;i++) {{
      const pt = meta.data[i];
      const d = chart.data.datasets[0].data[i];
      if (!pt || !d || !d._label) continue;
      ctx.fillText(d._label, pt.x+8, pt.y);
    }}
    ctx.restore();
  }}
}};

const chart = new Chart(document.getElementById('chart').getContext('2d'), {{
  type:'scatter',
  data: {{ datasets:[{{ data:[], pointRadius:5, pointHoverRadius:7, pointBackgroundColor:'#6b3f2a', pointBorderColor:'rgba(255,255,255,0.9)', pointBorderWidth:1.5 }}] }},
  options: {{
    responsive:true, maintainAspectRatio:false,
    plugins: {{ legend:{{display:false}} }},
    scales: {{
      x: {{ title: {{ display:true, text:'' }}, grid: {{ color:'rgba(42,26,18,0.10)' }} }},
      y: {{ title: {{ display:true, text:'' }}, grid: {{ color:'rgba(42,26,18,0.10)' }} }},
    }},
    onClick: (evt) => {{
      const pts = chart.getElementsAtEventForMode(evt, 'nearest', {{ intersect:true }}, true);
      if (!pts.length) return;
      const idx = pts[0].index;
      const d = chart.data.datasets[0].data[idx];
      if (isGspMode() && d && d._subbasin) setSelectedSubbasin(d._subbasin);
      if (d && d._fips) setSelected(d._fips);
    }}
  }},
  plugins: [labelPlugin]
}});

function refreshScatter() {{
  // In GSP mode, map stays GSP-wise, scatter switches to subbasin-wise.
  if (isGspMode() && ATLAS.subbasins && Array.isArray(ATLAS.subbasins) && ATLAS.subbasins.length) {{
    const feats = ATLAS.subbasins || [];
    // x = years since Date_Posted (incomplete/missing = 0)
    const maxYear = new Date().getFullYear();
    const pts = [];
    // In GSP mode, the dropdown becomes "metric selector".
    // v1: MNM groundwater, v2: well failures (adjusted share), v3: fallow acres, v4: median field size
    function yForGsp(p) {{
      if (viewId === 'v1') return (period === 'pre') ? p.gwe_pre_mnm : (period === 'post') ? p.gwe_post_mnm : p.gwe_delta_mnm;
      if (viewId === 'v2') return (period === 'pre') ? p.well_pre_adj : (period === 'post') ? p.well_post_adj : p.well_delta_adj;
      if (viewId === 'v3') return (period === 'pre') ? p.fallow_pre_acres : (period === 'post') ? p.fallow_post_acres : p.fallow_delta_acres;
      if (viewId === 'v4') return (period === 'pre') ? p.median_field_pre_acres : (period === 'post') ? p.median_field_post_acres : p.median_field_delta_acres;
      return (period === 'pre') ? p.well_pre_adj : (period === 'post') ? p.well_post_adj : p.well_delta_adj;
    }}
    function yLabelForView() {{
      if (viewId === 'v1') return (period==='delta') ? 'Δ groundwater elevation (MNM)' : 'Groundwater elevation (MNM)';
      if (viewId === 'v2') return (period==='delta') ? 'Δ well failures (adj share)' : 'Well failures (adj share)';
      if (viewId === 'v3') return (period==='delta') ? 'Δ fallow acres (DWR crop mapping)' : 'Fallow acres (DWR crop mapping)';
      if (viewId === 'v4') return (period==='delta') ? 'Δ median field size (acres)' : 'Median field size (acres)';
      return 'GSP metric';
    }}
    for (const p of feats) {{
      const nm = p.subbasin_name || 'Subbasin';
      const dp = parseISO(p.date_posted_min || null);
      const yearsSince = dp ? Math.max(0, (maxYear - dp.getFullYear())) : 0;
      const y = yForGsp(p);
      if (y === null || y === undefined || Number.isNaN(y)) continue;
      pts.push({{ x: yearsSince, y: Number(y), _label: nm, _subbasin: nm, _fips: null }});
    }}
    chart.data.datasets[0].data = pts;
    chart.options.scales.x.title.text = 'Years since earliest GSP posted in subbasin (missing/incomplete = 0)';
    chart.options.scales.y.title.text = yLabelForView();
    chart.update();
    document.getElementById('chartNote').textContent =
      'GSP map + subbasin scatter: x = years since earliest GSP Date_Posted in subbasin (missing/incomplete = 0). ' +
      'y = ' + yLabelForView() + ' aggregated to subbasin. ' +
      ((ATLAS.gsp_notes && ATLAS.gsp_notes.landuse) ? ATLAS.gsp_notes.landuse : '');
    document.getElementById('mapPill').textContent = 'GSP mode';
    updateLegend();
    return;
  }}

  const view = VIEWS[viewId];
  const pts = [];
  for (const f of ATLAS.counties.features) {{
    const x = num(view.x(f));
    const y = num(view.y(f));
    if (x===null || y===null) continue;
    pts.push({{ x, y, _label: f.properties.name, _fips: f.properties.fips5 }});
  }}
  chart.data.datasets[0].data = pts;
  chart.options.scales.x.title.text = view.xLabel;
  chart.options.scales.y.title.text = view.yLabel;
  chart.update();
  document.getElementById('chartNote').textContent = view.note;
  document.getElementById('mapPill').textContent = 'Choropleth: ' + view.mapLabel;
  refreshMap();
  refreshSelectionStyling();
  updateLegend();
}}

function refreshSelectionStyling() {{
  const ds = chart.data.datasets[0];
  ds.pointBackgroundColor = ds.data.map(p => (selected && p._fips===selected) ? '#2a1a12' : '#6b3f2a');
  ds.pointRadius = ds.data.map(p => (selected && p._fips===selected) ? 7 : 5);
  chart.update();
}}

// Card
function updateCard() {{
  document.getElementById('periodPill').textContent =
    (period==='pre' ? 'Period: Pre-SGMA' : period==='post' ? 'Period: Post-SGMA' : 'Period: Change (Δ)');
  if (isGspMode()) {{
    document.getElementById('periodPill').textContent =
      (period==='pre' ? 'Period: Pre approval' : period==='post' ? 'Period: Post approval' : 'Period: Change (Δ)');
  }}
  if (!selected) {{
    document.getElementById('countyTitle').textContent = 'Select a county';
    ['c-well','c-gwe','c-fallow','c-farm','c-inc','c-ces'].forEach(id => document.getElementById(id).textContent = '—');
    document.getElementById('c-gwe-sub').textContent = 'ft (source: —)';
    document.getElementById('countyNote').textContent = '';
    return;
  }}
  const f = featByFips(selected);
  if (!f) return;
  const pre = f.properties.pre, post = f.properties.post, d = f.properties.delta;
  document.getElementById('countyTitle').textContent = f.properties.name + ' County';

  function wellsVal() {{
    if (period==='pre') return fmtInt(pre.well_failures_issue_start);
    if (period==='post') return fmtInt(post.well_failures_issue_start);
    return signFmt(d.well_failures_issue_start, (v)=>fmtInt(v));
  }}
  function gweVal() {{
    if (period==='pre') {{ document.getElementById('c-gwe-sub').textContent = 'ft (source: ' + pre.gwe_src + ')'; return fmtFloat(pre.gwe_ft,1); }}
    if (period==='post') {{ document.getElementById('c-gwe-sub').textContent = 'ft (source: ' + post.gwe_src + ')'; return fmtFloat(post.gwe_ft,1); }}
    document.getElementById('c-gwe-sub').textContent = 'ft (Post - Pre)';
    return signFmt(d.gwe_ft, (v)=>fmtFloat(v,1));
  }}
  function fallowVal() {{
    if (period==='pre') return fmtInt(pre.fallow_acres);
    if (period==='post') return fmtInt(post.fallow_acres);
    return signFmt(d.fallow_acres, (v)=>fmtInt(v));
  }}
  function incomeVal() {{
    if (period==='pre') return fmtMoney(pre.median_income);
    if (period==='post') return fmtMoney(post.median_income);
    const v = d.median_income;
    if (v===null || v===undefined || Number.isNaN(v)) return 'n/a';
    const s = v>=0?'+':'';
    return s + Math.round(v).toLocaleString();
  }}
  function farmVal() {{
    const show = (obj) => {{
      const t = obj.total_farms, s = obj.small_farms, l = obj.large_farms;
      return (t==null?'n/a':fmtInt(t)) + ' total; ' + (s==null?'n/a':fmtInt(s)) + ' small; ' + (l==null?'n/a':fmtInt(l)) + ' large';
    }};
    if (period==='pre') return show(pre);
    if (period==='post') return show(post);
    const loss = d.small_farm_loss;
    return 'Δ small: ' + signFmt(d.small_farms, (v)=>fmtInt(v)) + ' (loss: ' + (loss==null?'n/a':fmtInt(loss)) + '), Δ large: ' + signFmt(d.large_farms, (v)=>fmtInt(v));
  }}

  document.getElementById('c-well').textContent = wellsVal();
  document.getElementById('c-gwe').textContent = gweVal();
  document.getElementById('c-fallow').textContent = fallowVal();
  document.getElementById('c-inc').textContent = incomeVal();
  document.getElementById('c-farm').textContent = farmVal();
  document.getElementById('c-ces').textContent = (f.properties.ces_score_pct==null ? 'n/a' : fmtFloat(f.properties.ces_score_pct, 1));
  document.getElementById('countyNote').textContent =
    [f.properties.notes.gwe, f.properties.notes.wells, f.properties.notes.subsidence, f.properties.notes.ces].filter(Boolean).join(' ');
  const reg = (f.properties.regulation || null);
  if (reg) {{
    document.getElementById('countyNote').textContent +=
      ' Regulation progress score: ' + reg.regulation_score +
      ' (approved area share: ' + reg.approved_area_share + ').';
  }}
}}

function setSelected(fips) {{
  selected = fips;
  refreshMap();
  refreshSelectionStyling();
  updateCard();
}}

function setPeriod(p) {{
  period = p;
  document.getElementById('btn-pre').classList.toggle('active', p==='pre');
  document.getElementById('btn-post').classList.toggle('active', p==='post');
  document.getElementById('btn-delta').classList.toggle('active', p==='delta');
  updateCard();
}}

document.getElementById('btn-pre').addEventListener('click', ()=>setPeriod('pre'));
document.getElementById('btn-post').addEventListener('click', ()=>setPeriod('post'));
document.getElementById('btn-delta').addEventListener('click', ()=>setPeriod('delta'));
document.getElementById('viewSelect').addEventListener('change', (e)=>{{ viewId = e.target.value; refreshScatter(); }});
if (document.getElementById('toggleGsp')) {{
  function setViewOptionsForMode() {{
    const sel = document.getElementById('viewSelect');
    if (!sel) return;
    if (isGspMode()) {{
      sel.innerHTML = ''
        + '<option value="v1">GSP metric: MNM groundwater elevation</option>'
        + '<option value="v2">GSP metric: well failures (adjusted share)</option>'
        + '<option value="v3">GSP metric: fallow acres (DWR crop mapping)</option>'
        + '<option value="v4">GSP metric: median field size (acres)</option>';
      // default to groundwater in GSP mode
      viewId = (viewId === 'v1' || viewId === 'v2' || viewId === 'v3' || viewId === 'v4') ? viewId : 'v1';
      sel.value = viewId;
    }} else {{
      sel.innerHTML = ''
        + '<option value="v1">Overdraft → harm: groundwater (Pre) vs well failures (Pre)</option>'
        + '<option value="v2">Overdraft → costs: groundwater change vs subsidence rate change</option>'
        + '<option value="v3">Policy adjustment: groundwater change vs fallow change</option>'
        + '<option value="v4">Distribution: small farm loss vs groundwater change</option>'
        + '<option value="v5">Equity context: CalEnviroScreen vs well failures (Pre)</option>';
      // return to v1 if current viewId isn't a county view
      viewId = (viewId === 'v1' || viewId === 'v2' || viewId === 'v3' || viewId === 'v4' || viewId === 'v5') ? viewId : 'v1';
      sel.value = viewId;
    }}
  }}

  document.getElementById('toggleGsp').addEventListener('change', ()=>{{
    setViewOptionsForMode();
    refreshMap();
    refreshScatter();
    updatePanels();
    if (isGspMode()) {{
      // keep current button labels as-is until a GSP is clicked
    }} else {{
      updatePeriodLabelsForCounty();
    }}
  }});
  // ensure correct options at load
  setViewOptionsForMode();
}}

// init
document.getElementById('mapPill').textContent = 'Choropleth: ' + VIEWS[viewId].mapLabel;
refreshScatter();
setPeriod('pre');
refreshMap();
updatePanels();
makeLegendDraggable();
</script>
</body>
</html>
"""


def main() -> int:
    # required local inputs
    summary_path = ROOT / "data/clean/sjv_county_summary.csv"
    if not summary_path.is_file():
        print(f"Missing {summary_path}", file=sys.stderr)
        return 1

    counties = load_counties_sjv()
    gsa = load_gsa_outline()
    gsp = load_gsp_plan_areas_sjv()

    base_summary = pd.read_csv(summary_path, dtype={"county_fips5": str})
    base_summary["county_fips5"] = base_summary["county_fips5"].astype(str).str.zfill(5)
    base_summary = base_summary.loc[base_summary["county_fips5"].isin(SJV_FIPS5)].copy()

    farm = load_farm_summary()
    fallow12, fallow22, inc14, inc21 = load_fallow_and_income()
    well_pre_post = load_well_failures_pre_post()

    gwe_pre, gwe_post, gwe_note = load_groundwater_period_means()

    print("Aggregating CalEnviroScreen to counties (cached)...")
    ces = load_ces4_county_scores(counties)

    print("Aggregating InSAR subsidence to counties (cached, optional)...")
    subs = load_insar_subsidence_by_county(counties)

    print("Computing regulation progress from GSP statuses...")
    reg_by_county = compute_regulation_by_county(counties, gsp)

    print("Computing well failures at GSP level (spatial join)...")
    wells_by_gsp = load_well_failures_by_gsp(gsp)
    gsp = attach_gsp_metrics(gsp, wells_by_gsp)

    print("Computing GSP groundwater (MNM) pre/post means...")
    gwe_by_gsp = load_mnm_gsp_groundwater_means(gsp)
    gsp = attach_gsp_groundwater_mnm(gsp, gwe_by_gsp)

    print("Attaching GSP fallow + field-size metrics (if CSVs exist)...")
    fallow_by_gsp, field_by_gsp, gsp_landuse_note = load_gsp_landuse_outputs()
    gsp = attach_gsp_landuse(gsp, fallow_by_gsp, field_by_gsp)

    # Build subbasin-level aggregates for scatter in GSP mode
    subbasin_points = build_subbasin_points_from_gsp(gsp)

    county_fc = build_county_feature_collection(
        counties=counties,
        base_summary=base_summary,
        farm_summary=farm,
        fallow12=fallow12,
        fallow22=fallow22,
        inc14=inc14,
        inc21=inc21,
        gwe_pre=gwe_pre,
        gwe_post=gwe_post,
        gwe_note=gwe_note,
        well_pre_post=well_pre_post,
        ces_scores=ces,
        subsidence=subs,
        regulation_by_county=reg_by_county,
    )

    payload = {
        "meta": {"title": "SGMA Equity Pathways — SJV"},
        "counties": county_fc,
        "gsa": json.loads(gsa.to_json()),
        "gsp": json.loads(gsp.to_json()),
        "subbasins": subbasin_points,
        "gsp_notes": {"landuse": gsp_landuse_note},
        "_gsp_date_range": {
            "min": (gsp["Date_Posted"].dropna().min() if ("Date_Posted" in gsp.columns and not gsp["Date_Posted"].dropna().empty) else None),
            "max": (gsp["Date_Posted"].dropna().max() if ("Date_Posted" in gsp.columns and not gsp["Date_Posted"].dropna().empty) else None),
        },
    }
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(render_html(payload), encoding="utf-8")
    print(f"Wrote {OUT_HTML} ({OUT_HTML.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

