"""
Build vercel_site/sinking_valley_explorer.* — SGMA effectiveness explorer with
time-varying GSP panels (status, fallow, water access, farm size, overdraft).
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "outputs/subsidence/manifest.json"
GSP_PATH = ROOT / "data/raw/boundaries/gsp_plan_areas.geojson"
GSP_STATUS = ROOT / "data/processed/csv/gsp_determination_status.csv"
COUNTIES = ROOT / "vercel_site/thesis_counties.geojson"
DRY_WELLS = ROOT / "data/interim/dry_wells/dry_well_points.geoparquet"
FALLOW_GSP = ROOT / "sjv_gsp_fallow_by_year.csv"
FARM_JSON = ROOT / "data/raw/farm_size/farm_operations.json"
DECK_DATA = ROOT / "vercel_site/sgma_equity_deck_data.json"
SUBSIDENCE_ZIP = ROOT / "data/raw/subsidence/verticaldisplacementpointdata.zip"
SUBSIDENCE_CACHE = ROOT / "data/processed/csv/subsidence_by_gsp_year.csv"
SUBSIDENCE_YEARS = list(range(2016, 2025))
CM_TO_FT = 1.0 / 30.48

SJV_FIPS5 = {
    "06019", "06029", "06031", "06039", "06047", "06077", "06099", "06107",
}
FARM_BUCKET_MAP: dict[str, str] = {
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
FARM_BUCKET_ORDER = ["under_50", "50_179", "180_499", "500_999", "1000_plus"]
FARM_CENSUS_YEARS = [2012, 2017, 2022]
GWE_BASELINE_YEAR = 2014  # SGMA enacted; pre-SGMA average = all MNM years before this
SGMA_ERA_START = 2016  # start of site's main before/after metric window (fallow, wells, etc.)

MNM_SITES_URL = (
    "https://data.cnra.ca.gov/dataset/536dc423-01b3-4094-bdcd-903df84f6768/"
    "resource/38dc5a77-0428-4d8b-970a-51797ed2cd36/download/groundwater_level_sites.csv"
)
MNM_DATA_URL = (
    "https://data.cnra.ca.gov/dataset/536dc423-01b3-4094-bdcd-903df84f6768/"
    "resource/d6317634-7489-4dc9-8d05-cc939e109f4a/download/groundwater_level_data.csv"
)
MNM_SITES_CACHE = ROOT / "data/clean/_cache_external/mnm_sites.csv"
MNM_DATA_CACHE = ROOT / "data/clean/_cache_external/mnm_data.csv"

OUT_HTML = ROOT / "vercel_site/sinking_valley_explorer.html"
OUT_INDEX = ROOT / "vercel_site/index.html"
OUT_DATA = ROOT / "vercel_site/sinking_valley_explorer_data.json"
ECON_OUT = ROOT / "outputs/econometrics"
ECON_ASSETS = ROOT / "vercel_site/assets/econometrics"

SJV_COUNTIES = {
    "Fresno", "Kern", "Kings", "Madera", "Merced", "San Joaquin", "Stanislaus", "Tulare",
}
# Indian Wells Valley (east Kern / Mojave) — not part of the San Joaquin Valley study area
EXCLUDED_GSP_IDS = {"59"}
SLIDER_YEARS = list(range(2012, 2025))
DROUGHT_YEARS = {2013, 2014, 2015, 2016, 2020, 2021, 2022}
# San Joaquin Valley GSPs only (Close view / catalog filter)
SJV_GSP_PREFIX = "SAN JOAQUIN VALLEY"
WET_YEARS_RECENT = {2017, 2019, 2023, 2024}


def load_counties() -> gpd.GeoDataFrame:
    gdf = gpd.read_file(COUNTIES).to_crs(4326)
    if "name" in gdf.columns:
        gdf = gdf[gdf["name"].isin(SJV_COUNTIES)]
    gdf["geometry"] = gdf.geometry.simplify(0.003, preserve_topology=True)
    return gdf


def exclude_non_sjv_gsps(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    gid_col = "gsp_id" if "gsp_id" in gdf.columns else "GSP_ID"
    return gdf[~gdf[gid_col].astype(str).isin(EXCLUDED_GSP_IDS)].copy()


def clip_to_counties(gdf: gpd.GeoDataFrame, counties: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    union = counties.union_all()
    gdf = gdf.to_crs(4326)
    clipped = gdf[gdf.intersects(union)].copy()
    clipped["geometry"] = clipped.geometry.intersection(union)
    return clipped[~clipped.geometry.is_empty]


def simplify_geojson_gdf(gdf: gpd.GeoDataFrame) -> dict:
    gdf = gdf.to_crs(4326)
    gdf["geometry"] = gdf.geometry.simplify(0.005, preserve_topology=True)
    if "GSP_ID" in gdf.columns:
        gdf["gsp_id"] = gdf["GSP_ID"].astype(str)
    if "Basin_Subbasin_Name" in gdf.columns:
        gdf["subbasin_name"] = gdf["Basin_Subbasin_Name"].astype(str)
    return json.loads(gdf.to_json())


def parse_year(val) -> int | None:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    try:
        return int(pd.Timestamp(val).year)
    except Exception:
        return None


def format_status_date(val) -> str | None:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    try:
        return pd.Timestamp(val).strftime("%b %Y")
    except Exception:
        return None


def format_status_note(status: str, date_posted) -> str | None:
    """Short Close-view note — uses DWR date_posted as plan determination proxy."""
    when = format_status_date(date_posted)
    if not when:
        return None
    st = (status or "unknown").strip()
    if st == "approved":
        return f"Approved {when}"
    if st == "inadequate":
        return f"Inadequate · {when}"
    if st == "inadequate_under_review":
        return f"Inadequate (under review) · {when}"
    if st == "state_intervention":
        return f"State intervention · {when}"
    if st == "under_review":
        return f"Under review · posted {when}"
    if st == "incomplete":
        return f"Incomplete · posted {when}"
    return f"Status updated · {when}"


def status_at_year(final_status: str, year: int, posted_year: int | None) -> str:
    """SGMA timeline: incomplete (grey) pre-2020, then under review until DWR determination."""
    fs = (final_status or "unknown").strip()
    intervention_year = posted_year or 2020

    if year < GWE_BASELINE_YEAR:
        return "pre_sgma"
    if fs == "state_intervention":
        if year < intervention_year:
            return "incomplete" if year <= 2019 else "under_review"
        return "state_intervention"

    if year <= 2019:
        return "incomplete"
    if year == 2020:
        return "under_review"
    if posted_year and year >= posted_year:
        return fs
    return "under_review"


def build_status_timeline(status_df: pd.DataFrame) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for _, row in status_df.iterrows():
        gid = str(row["gsp_id"])
        final = str(row.get("status_std", "unknown"))
        post_y = parse_year(row.get("date_posted"))
        out[gid] = [
            {"year": y, "status_std": status_at_year(final, y, post_y)}
            for y in SLIDER_YEARS
        ]
    return out


def load_fallow_ag_series() -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    if not FALLOW_GSP.is_file():
        return {}, {}
    df = pd.read_csv(FALLOW_GSP)
    df["gsp_id"] = df["GSP_ID"].astype(str)
    df["year"] = df["Water_Year"].astype(int)
    df["fallow_pct"] = pd.to_numeric(df["Fallow_Pct"], errors="coerce")
    df["total_ag_acres"] = pd.to_numeric(df["Total_Ag_Acres"], errors="coerce")
    df["fallow_acres"] = pd.to_numeric(df["Fallow_Acres"], errors="coerce")
    df["active_ag_acres"] = df["total_ag_acres"] - df["fallow_acres"]
    fallow: dict[str, list[dict]] = {}
    ag: dict[str, list[dict]] = {}
    for gid, grp in df.groupby("gsp_id"):
        frows, arows = [], []
        for _, r in grp.sort_values("year").iterrows():
            yr = int(r["year"])
            if pd.notna(r["fallow_pct"]):
                frows.append({"year": yr, "fallow_pct": round(float(r["fallow_pct"]), 2)})
            if pd.notna(r["total_ag_acres"]):
                arows.append({
                    "year": yr,
                    "total_ag_acres": round(float(r["total_ag_acres"]), 1),
                    "active_ag_acres": round(float(r["active_ag_acres"]), 1)
                    if pd.notna(r["active_ag_acres"]) else round(float(r["total_ag_acres"]), 1),
                })
        if frows:
            fallow[str(gid)] = frows
        if arows:
            ag[str(gid)] = arows
    return fallow, ag


def _download(url: str, path: Path) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > 1000:
            return True
        r = requests.get(url, timeout=300, headers={"User-Agent": "SGMA-ECON30/1.0"})
        r.raise_for_status()
        path.write_bytes(r.content)
        return True
    except Exception:
        return False


def load_gsp_gwe_yearly(gsp_gdf: gpd.GeoDataFrame) -> dict[str, list[dict]]:
    from shapely.geometry import Point

    if _download(MNM_SITES_URL, MNM_SITES_CACHE) and _download(MNM_DATA_URL, MNM_DATA_CACHE):
        try:
            sites = pd.read_csv(MNM_SITES_CACHE, low_memory=False)
            data = pd.read_csv(MNM_DATA_CACHE, low_memory=False)
            lat_col = next((c for c in sites.columns if c.lower() in ("latitude", "lat")), None)
            lon_col = next((c for c in sites.columns if c.lower() in ("longitude", "lon", "long")), None)
            site_key = next((c for c in sites.columns if "site" in c.lower()), sites.columns[0])
            data_key = next((c for c in data.columns if "site" in c.lower()), data.columns[0])
            date_col = next((c for c in data.columns if "date" in c.lower()), None)
            elev_col = next(
                (c for c in data.columns if c.upper() in ("WSE", "GWE", "GW_ELEVATION")),
                next((c for c in data.columns if "elev" in c.lower()), None),
            )
            if all([lat_col, lon_col, date_col, elev_col]):
                sites = sites.dropna(subset=[lat_col, lon_col]).copy()
                sites[site_key] = sites[site_key].astype(str)
                pts = gpd.GeoDataFrame(
                    sites[[site_key]],
                    geometry=[Point(x, y) for x, y in zip(sites[lon_col], sites[lat_col])],
                    crs="EPSG:4326",
                )
                gsp_w = gsp_gdf.to_crs(4326)
                gid_col = "gsp_id" if "gsp_id" in gsp_w.columns else "GSP_ID"
                joined = gpd.sjoin(pts, gsp_w[[gid_col, "geometry"]], predicate="within", how="inner")
                site_to_gsp = dict(zip(joined[site_key].astype(str), joined[gid_col].astype(str)))
                d = data[[data_key, date_col, elev_col]].copy()
                d[data_key] = d[data_key].astype(str)
                d["gsp_id"] = d[data_key].map(site_to_gsp)
                d = d.dropna(subset=["gsp_id"])
                d["_dt"] = pd.to_datetime(d[date_col], errors="coerce")
                d = d.dropna(subset=["_dt"])
                d["year"] = d["_dt"].dt.year.astype(int)
                d["gwe"] = pd.to_numeric(d[elev_col], errors="coerce")
                d = d.dropna(subset=["gwe"]).loc[d["year"].between(2010, 2024)]
                yearly = d.groupby(["gsp_id", "year"], as_index=False)["gwe"].mean()
                out: dict[str, list[dict]] = {}
                for gid, grp in yearly.groupby("gsp_id"):
                    out[str(gid)] = [
                        {"year": int(r.year), "gwe_ft": round(float(r.gwe), 1)} for _, r in grp.iterrows()
                    ]
                if out:
                    return out
        except Exception:
            pass

    return _gwe_from_deck()


def _gwe_pre2014_from_deck() -> dict[str, float]:
    if not DECK_DATA.is_file():
        return {}
    deck = json.loads(DECK_DATA.read_text(encoding="utf-8"))
    return {
        str(g["gsp_id"]): float(g["gwe_pre"])
        for g in deck.get("gsps", [])
        if g.get("gwe_pre") is not None
    }


def _gwe_from_deck() -> dict[str, list[dict]]:
    if not DECK_DATA.is_file():
        return {}
    deck = json.loads(DECK_DATA.read_text(encoding="utf-8"))
    out: dict[str, list[dict]] = {}
    for gsp in deck.get("gsps", []):
        gid = str(gsp.get("gsp_id", ""))
        pre = gsp.get("gwe_pre")
        post = gsp.get("gwe_post")
        if pre is None or post is None:
            continue
        series = []
        for year in range(2014, 2025):
            t = (year - 2014) / max(2024 - 2014, 1)
            gwe = float(pre) + (float(post) - float(pre)) * t
            series.append({"year": year, "gwe_ft": round(gwe, 1)})
        out[gid] = series
    return out


def is_sjv_gsp_label(label: str) -> bool:
    return str(label or "").strip().upper().startswith(SJV_GSP_PREFIX)


def build_gwe_trend_series(
    gwe_series: dict[str, list[dict]], window: int = 3, value_key: str = "gwe_trend_ft_yr",
) -> dict[str, list[dict]]:
    """Linear GWE trend (ft/yr). Positive = water table falling; smooths noisy well coverage."""
    out: dict[str, list[dict]] = {}
    for gid, rows in gwe_series.items():
        rows = sorted(rows, key=lambda x: x["year"])
        by_year = {r["year"]: r["gwe_ft"] for r in rows}
        years_available = sorted(by_year.keys())
        series = []
        for year in years_available:
            window_years = [y for y in years_available if year - window + 1 <= y <= year]
            if len(window_years) < 2:
                continue
            xs = np.array(window_years, dtype=float)
            ys = np.array([by_year[y] for y in window_years], dtype=float)
            slope = float(np.polyfit(xs, ys, 1)[0])
            series.append({"year": year, value_key: round(-slope, 2)})
        if series:
            out[gid] = series
    return out


def build_gwe_pre_sgma_baseline(gwe_series: dict[str, list[dict]]) -> dict[str, float]:
    deck_pre = _gwe_pre2014_from_deck()
    out: dict[str, float] = {}
    for gid, rows in gwe_series.items():
        early = [r["gwe_ft"] for r in rows if r["year"] < GWE_BASELINE_YEAR]
        if early:
            out[gid] = round(sum(early) / len(early), 1)
        elif gid in deck_pre:
            out[gid] = round(deck_pre[gid], 1)
    for gid, val in deck_pre.items():
        out.setdefault(gid, round(val, 1))
    return out


def _parse_nass_int(val) -> int:
    if val is None:
        return 0
    s = str(val).strip().replace(",", "")
    if not s or s in {"**", "(D)"}:
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def load_farm_consolidation_by_county() -> pd.DataFrame:
    if not FARM_JSON.is_file():
        return pd.DataFrame()
    data = json.loads(FARM_JSON.read_text(encoding="utf-8"))
    rows = []
    for r in data.get("records", []):
        if r.get("commodity_desc") != "FARM OPERATIONS":
            continue
        if r.get("domain_desc") != "AREA OPERATED":
            continue
        yr = int(r.get("year", 0))
        if yr not in FARM_CENSUS_YEARS:
            continue
        bucket = FARM_BUCKET_MAP.get(str(r.get("domaincat_desc", "")).strip())
        if not bucket:
            continue
        fips = str(r.get("county_fips5", "")).zfill(5)
        if fips not in SJV_FIPS5:
            continue
        rows.append({"county_fips": fips, "year": yr, "bucket": bucket, "ops": _parse_nass_int(r.get("Value"))})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    wide = (
        df.pivot_table(index=["county_fips", "year"], columns="bucket", values="ops", aggfunc="sum", fill_value=0)
        .reset_index()
    )
    for b in FARM_BUCKET_ORDER:
        if b not in wide.columns:
            wide[b] = 0
    wide["small_farms"] = wide["under_50"] + wide["50_179"]
    wide["large_farms"] = wide["500_999"] + wide["1000_plus"]
    wide["total_farms"] = wide[FARM_BUCKET_ORDER].sum(axis=1)
    wide["large_farm_share"] = np.where(
        wide["total_farms"] > 0,
        100.0 * wide["large_farms"] / wide["total_farms"],
        np.nan,
    )
    return wide


def assign_gsp_county_fips(gsp_gdf: gpd.GeoDataFrame, counties_gdf: gpd.GeoDataFrame) -> dict[str, str]:
    gsp = gsp_gdf.to_crs(3857).copy()
    cnt = counties_gdf.to_crs(3857).copy()
    gid_col = "gsp_id" if "gsp_id" in gsp.columns else "GSP_ID"
    gsp["gsp_id"] = gsp[gid_col].astype(str)
    cent = gsp.copy()
    cent["geometry"] = gsp.geometry.centroid
    joined = gpd.sjoin(cent[["gsp_id", "geometry"]], cnt[["fips", "geometry"]], predicate="within", how="left")
    out = {}
    for gid, grp in joined.groupby("gsp_id"):
        fips = grp["fips"].dropna()
        out[str(gid)] = str(fips.iloc[0]) if len(fips) else ""
    return out


def build_farm_consolidation_timeline(farm_df: pd.DataFrame) -> dict[str, dict[int, dict]]:
    if farm_df.empty:
        return {}
    out: dict[str, dict[int, dict]] = {}
    for fips, grp in farm_df.groupby("county_fips"):
        census = {int(r.year): r for _, r in grp.iterrows()}
        base_small = float(census[2012].small_farms) if 2012 in census else None
        year_vals: dict[int, dict] = {}
        for year in SLIDER_YEARS:
            share, small = _interp_farm_census(census, year)
            if share is None:
                continue
            year_vals[year] = {
                "large_farm_share": round(float(share), 1),
                "small_farm_loss": round(base_small - float(small), 0) if base_small is not None and small is not None else None,
            }
        out[str(fips)] = year_vals
    return out


def _interp_farm_census(census: dict[int, object], year: int) -> tuple[float | None, float | None]:
    years = sorted(census.keys())
    if not years:
        return None, None
    if year <= years[0]:
        row = census[years[0]]
        return float(row.large_farm_share), float(row.small_farms)
    if year >= years[-1]:
        row = census[years[-1]]
        return float(row.large_farm_share), float(row.small_farms)
    lower = max(y for y in years if y <= year)
    upper = min(y for y in years if y >= year)
    if lower == upper:
        row = census[lower]
        return float(row.large_farm_share), float(row.small_farms)
    t = (year - lower) / (upper - lower)
    a, b = census[lower], census[upper]
    share = float(a.large_farm_share) + t * (float(b.large_farm_share) - float(a.large_farm_share))
    small = float(a.small_farms) + t * (float(b.small_farms) - float(a.small_farms))
    return share, small


def cumulative_well_reports(well_rows: list[dict], year: int) -> int:
    return sum(r["well_reports"] for r in well_rows if 2016 <= r["year"] <= year)


def lookup_gwe_at_year(series: list[dict], year: int):
    """Exact year only — avoids forward-fill artifacts in GWE metrics."""
    if not series:
        return None
    hit = next((s for s in series if s["year"] == year), None)
    return hit.get("gwe_ft") if hit else None


def lookup_series(series: list[dict], value_key: str, year: int):
    if not series:
        return None
    exact = [s for s in series if s["year"] == year]
    if exact:
        return exact[0].get(value_key)
    prior = [s for s in series if s["year"] <= year]
    if prior:
        return prior[-1].get(value_key)
    return series[0].get(value_key)


def load_dry_wells_gsp(gsp_gdf: gpd.GeoDataFrame, counties: gpd.GeoDataFrame) -> tuple[list[dict], dict[str, list[dict]]]:
    if not DRY_WELLS.is_file():
        return [], {}

    gdf = gpd.read_parquet(DRY_WELLS).to_crs(4326)
    gdf = gdf.loc[gdf.get("_is_dry_well", True)].copy()
    gdf = gdf[gdf.intersects(counties.union_all())]

    gsp_clip = gsp_gdf.to_crs(4326).copy()
    if "gsp_id" not in gsp_clip.columns:
        gsp_clip["gsp_id"] = gsp_clip["GSP_ID"].astype(str)

    joined = gpd.sjoin(gdf, gsp_clip[["gsp_id", "geometry"]], how="inner", predicate="within")
    joined = joined.loc[~joined.index.duplicated(keep="first")]

    county_year = (
        joined.dropna(subset=["_year"])
        .groupby(["_county_norm", "_year"], as_index=False)
        .size()
        .rename(columns={"size": "raw"})
    )
    spike_years: set[tuple[str, int]] = set()
    for county, grp in county_year.groupby("_county_norm"):
        g = grp.sort_values("_year")
        if len(g) < 3:
            continue
        x = g["_year"].values.astype(float)
        y = g["raw"].values.astype(float)
        resid = y - np.polyval(np.polyfit(x, y, 1), x)
        std = resid.std(ddof=1) or 1.0
        for i, (_, row) in enumerate(g.iterrows()):
            yr = int(row["_year"])
            if resid[i] / std > 1.5 and yr not in DROUGHT_YEARS:
                spike_years.add((str(county), yr))

    pts: list[dict] = []
    gsp_year_counts: dict[str, dict[int, int]] = {}
    for _, row in joined.iterrows():
        if pd.isna(row.get("_year")):
            continue
        year = int(row["_year"])
        county = str(row.get("_county_norm", ""))
        gid = str(row.get("gsp_id", ""))
        if (county, year) in spike_years:
            continue
        pts.append({
            "lon": float(row.geometry.x),
            "lat": float(row.geometry.y),
            "year": year,
            "gsp_id": gid,
            "drought_year": year in DROUGHT_YEARS,
        })
        gsp_year_counts.setdefault(gid, {})
        gsp_year_counts[gid][year] = gsp_year_counts[gid].get(year, 0) + 1

    well_series = {
        gid: [{"year": y, "well_reports": c} for y, c in sorted(yc.items())]
        for gid, yc in gsp_year_counts.items()
    }
    return pts[:12000], well_series


def enrich_gsps(
    gsp_geo: dict,
    status_timeline: dict[str, list[dict]],
    fallow_series: dict[str, list[dict]],
    ag_series: dict[str, list[dict]],
    gwe_series: dict[str, list[dict]],
    gwe_trend_series: dict[str, list[dict]],
    gwe_trend_4yr_series: dict[str, list[dict]],
    well_series: dict[str, list[dict]],
    gwe_pre_sgma: dict[str, float],
    gsp_county_fips: dict[str, str],
    farm_by_county: dict[str, dict[int, dict]],
) -> dict:
    max_wells = 1
    max_fallow = 1.0
    max_gwe_drop = 1.0
    max_gwe_trend = 0.1
    max_large_share = 1.0
    max_small_loss = 1.0
    max_ag_acres = 1.0

    for feat in gsp_geo.get("features", []):
        props = feat.setdefault("properties", {})
        gid = str(props.get("gsp_id") or props.get("GSP_ID") or "")
        pre_gwe = gwe_pre_sgma.get(gid)
        county_fips = gsp_county_fips.get(gid, "")
        county_farm = farm_by_county.get(county_fips, {})
        year_vals = {}
        for year in SLIDER_YEARS:
            wr_cum = cumulative_well_reports(well_series.get(gid, []), year)
            fp = lookup_series(fallow_series.get(gid, []), "fallow_pct", year)
            ag_ac = lookup_series(ag_series.get(gid, []), "total_ag_acres", year)
            active_ac = lookup_series(ag_series.get(gid, []), "active_ag_acres", year)
            trend = lookup_series(gwe_trend_series.get(gid, []), "gwe_trend_ft_yr", year)
            trend4 = lookup_series(gwe_trend_4yr_series.get(gid, []), "gwe_trend_4yr_ft_yr", year)
            gwe_now = lookup_gwe_at_year(gwe_series.get(gid, []), year)
            st_list = status_timeline.get(gid, [])
            st = lookup_series(
                [{"year": s["year"], "status_std": s["status_std"]} for s in st_list],
                "status_std",
                year,
            )
            if st is None:
                st = "pre_sgma" if year < GWE_BASELINE_YEAR else ("incomplete" if year <= 2019 else "under_review")

            gwe_drop = None
            if pre_gwe is not None and gwe_now is not None:
                gwe_drop = round(float(pre_gwe) - float(gwe_now), 1)

            farm = county_farm.get(year, {})
            large_share = farm.get("large_farm_share")
            small_loss = farm.get("small_farm_loss")

            year_vals[str(year)] = {
                "status_std": st,
                "fallow_pct": fp,
                "total_ag_acres": ag_ac,
                "active_ag_acres": active_ac,
                "large_farm_share": large_share,
                "small_farm_loss": small_loss,
                "well_reports": wr_cum,
                "gwe_trend_ft_yr": trend,
                "gwe_trend_4yr_ft_yr": trend4,
                "gwe_cumulative_drop": gwe_drop,
                "gwe_ft": round(float(gwe_now), 1) if gwe_now is not None else None,
            }
            max_wells = max(max_wells, int(wr_cum))
            if fp:
                max_fallow = max(max_fallow, float(fp))
            if gwe_drop is not None:
                max_gwe_drop = max(max_gwe_drop, abs(float(gwe_drop)))
            if trend is not None:
                max_gwe_trend = max(max_gwe_trend, abs(float(trend)))
            if trend4 is not None:
                max_gwe_trend = max(max_gwe_trend, abs(float(trend4)))
            if large_share is not None:
                max_large_share = max(max_large_share, float(large_share))
            if small_loss is not None:
                max_small_loss = max(max_small_loss, float(small_loss))
            if ag_ac:
                max_ag_acres = max(max_ag_acres, float(ag_ac))

        props["year_values"] = year_vals
        if pre_gwe is not None:
            props["gwe_pre_sgma"] = pre_gwe
        if county_fips:
            props["county_fips"] = county_fips

    gsp_geo["_scale_max"] = {
        "well_reports": max_wells,
        "fallow_pct": min(max(max_fallow, 10), 50),
        "gwe_cumulative_drop": max(max_gwe_drop, 5.0),
        "gwe_trend_ft_yr": max(max_gwe_trend, 1.0),
        "gwe_trend_4yr_ft_yr": max(max_gwe_trend, 1.0),
        "large_farm_share": max(max_large_share, 18.0),
        "small_farm_loss": max(max_small_loss, 500.0),
        "total_ag_acres": max(max_ag_acres, 100000.0),
    }
    return gsp_geo


def compute_split_gwe_scale(gsp_geo: dict, pre_year: int = 2014, post_year: int = 2024) -> dict[str, float]:
    """Shared min/max GWE elevation (ft) for before/after split maps — SJV GSPs, clipped to p5–p95."""
    vals: list[float] = []
    for feat in gsp_geo.get("features", []):
        props = feat.get("properties", {})
        label = props.get("subbasin_name") or ""
        if not is_sjv_gsp_label(label):
            continue
        yv = props.get("year_values", {})
        for yr in (pre_year, post_year):
            gwe = yv.get(str(yr), {}).get("gwe_ft")
            if gwe is not None:
                vals.append(float(gwe))
    if not vals:
        return {"min": 50.0, "max": 250.0}
    if len(vals) >= 10:
        sv = sorted(vals)
        n = len(sv)
        lo = sv[max(0, int(n * 0.05) - 1)]
        hi = sv[min(n - 1, int(n * 0.95))]
        return {"min": round(lo, 1), "max": round(hi, 1)}
    return {"min": round(min(vals), 1), "max": round(max(vals), 1)}


def compute_split_drop_scale(gsp_geo: dict, pre_year: int = 2016, post_year: int = 2024) -> dict[str, float]:
    vals: list[float] = []
    for feat in gsp_geo.get("features", []):
        yv = feat.get("properties", {}).get("year_values", {})
        for yr in (pre_year, post_year):
            drop = yv.get(str(yr), {}).get("gwe_cumulative_drop")
            if drop is not None:
                vals.append(max(0.0, float(drop)))
    if not vals:
        return {"min": 0.0, "max": 50.0}
    return {"min": 0.0, "max": round(max(vals), 1)}


def load_subsidence_gsp_yearly(
    gsp_gdf: gpd.GeoDataFrame,
    counties_gdf: gpd.GeoDataFrame,
) -> dict[str, dict]:
    """
    Mean annual subsidence (ft) per GSP per Dec–Dec epoch from DWR InSAR point data.
    Positive = sinking. Ignores missing point-year pairs within each GSP average.
    """
    if SUBSIDENCE_CACHE.is_file() and SUBSIDENCE_ZIP.is_file():
        if SUBSIDENCE_CACHE.stat().st_mtime >= SUBSIDENCE_ZIP.stat().st_mtime:
            df = pd.read_csv(SUBSIDENCE_CACHE)
            return _subsidence_dict_from_df(df)

    if not SUBSIDENCE_ZIP.is_file():
        print("InSAR point ZIP missing; skipping per-GSP subsidence.", file=sys.stderr)
        return {}

    gid_col = "gsp_id" if "gsp_id" in gsp_gdf.columns else "GSP_ID"
    zones = gsp_gdf[[gid_col, "geometry"]].copy()
    zones[gid_col] = zones[gid_col].astype(str)
    zones = zones.to_crs(4326)
    xmin, ymin, xmax, ymax = counties_gdf.total_bounds

    year_pairs = [(y, f"D{y}1201", f"D{y + 1}1201") for y in SUBSIDENCE_YEARS]
    usecols = ["LAT", "LON"] + sorted({c for _, a, b in year_pairs for c in (a, b)})

    acc_sum: dict[tuple[str, int], float] = defaultdict(float)
    acc_n: dict[tuple[str, int], int] = defaultdict(int)

    with zipfile.ZipFile(SUBSIDENCE_ZIP) as zf:
        csv_names = sorted(n for n in zf.namelist() if n.lower().endswith(".csv"))
        for csv_name in csv_names:
            with zf.open(csv_name) as raw:
                for chunk in pd.read_csv(raw, usecols=lambda c: c in usecols, chunksize=150_000):
                    chunk["LAT"] = pd.to_numeric(chunk["LAT"], errors="coerce")
                    chunk["LON"] = pd.to_numeric(chunk["LON"], errors="coerce")
                    chunk = chunk.dropna(subset=["LAT", "LON"])
                    chunk = chunk[
                        (chunk["LON"] >= xmin) & (chunk["LON"] <= xmax)
                        & (chunk["LAT"] >= ymin) & (chunk["LAT"] <= ymax)
                    ]
                    if chunk.empty:
                        continue

                    long_rows = []
                    for year, col_a, col_b in year_pairs:
                        if col_a not in chunk.columns or col_b not in chunk.columns:
                            continue
                        a = pd.to_numeric(chunk[col_a], errors="coerce")
                        b = pd.to_numeric(chunk[col_b], errors="coerce")
                        subs_ft = (a - b) * CM_TO_FT
                        valid = subs_ft.notna()
                        if not valid.any():
                            continue
                        idx = chunk.index[valid]
                        long_rows.append(pd.DataFrame({
                            "LAT": chunk.loc[idx, "LAT"].values,
                            "LON": chunk.loc[idx, "LON"].values,
                            "year": year,
                            "subs_ft": subs_ft.loc[idx].values,
                        }))
                    if not long_rows:
                        continue
                    pts_df = pd.concat(long_rows, ignore_index=True)
                    gpts = gpd.GeoDataFrame(
                        pts_df,
                        geometry=gpd.points_from_xy(pts_df["LON"], pts_df["LAT"]),
                        crs="EPSG:4326",
                    )
                    joined = gpd.sjoin(
                        gpts[["year", "subs_ft", "geometry"]],
                        zones,
                        predicate="within",
                        how="inner",
                    )
                    for (gid, year), grp in joined.groupby([gid_col, "year"]):
                        key = (str(gid), int(year))
                        acc_sum[key] += float(grp["subs_ft"].sum())
                        acc_n[key] += len(grp)

    rows = []
    out: dict[str, dict] = {}
    for (gid, year), total in acc_sum.items():
        n = acc_n[(gid, year)]
        if n <= 0:
            continue
        mean_ft = round(total / n, 4)
        rows.append({"gsp_id": gid, "year": year, "mean_subsidence_ft": mean_ft, "n_points": n})
        entry = out.setdefault(gid, {"by_year": {}, "n_points": {}, "mean_ft_yr": None})
        entry["by_year"][str(year)] = mean_ft
        entry["n_points"][str(year)] = n

    for gid, entry in out.items():
        yrs = [float(v) for v in entry["by_year"].values()]
        entry["mean_ft_yr"] = round(float(np.mean(yrs)), 4) if yrs else None

    if rows:
        SUBSIDENCE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).sort_values(["gsp_id", "year"]).to_csv(SUBSIDENCE_CACHE, index=False)
        print(f"  subsidence: {len(out)} GSPs with InSAR point aggregates")
    return out


def _subsidence_dict_from_df(df: pd.DataFrame) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for gid, grp in df.groupby("gsp_id"):
        gid = str(gid)
        by_year = {
            str(int(r["year"])): round(float(r["mean_subsidence_ft"]), 4)
            for _, r in grp.iterrows()
        }
        n_points = {str(int(r["year"])): int(r["n_points"]) for _, r in grp.iterrows()}
        yrs = list(by_year.values())
        out[gid] = {
            "by_year": by_year,
            "n_points": n_points,
            "mean_ft_yr": round(float(np.mean(yrs)), 4) if yrs else None,
        }
    return out


def build_gsp_catalog(
    gsp_geo: dict,
    gwe_series: dict[str, list[dict]],
    status_df: pd.DataFrame | None = None,
    subsidence_by_gsp: dict[str, dict] | None = None,
) -> list[dict]:
    status_by_gid: dict[str, pd.Series] = {}
    if status_df is not None and not status_df.empty:
        for _, row in status_df.iterrows():
            status_by_gid[str(row["gsp_id"])] = row
    metric_keys = [
        "gwe_cumulative_drop", "gwe_trend_4yr_ft_yr", "fallow_pct", "well_reports",
        "total_ag_acres", "large_farm_share",
    ]
    pre_y, post_y = 2016, 2024
    catalog = []
    for feat in gsp_geo.get("features", []):
        props = feat.get("properties", {})
        gid = str(props.get("gsp_id") or props.get("GSP_ID") or "")
        yv = props.get("year_values", {})
        pre = yv.get(str(pre_y), {})
        post = yv.get(str(post_y), {})
        status = post.get("status_std") or "unknown"
        metrics = {}
        for k in metric_keys:
            a, b = pre.get(k), post.get(k)
            delta = None
            if a is not None and b is not None:
                delta = round(float(b) - float(a), 2)
            metrics[k] = {"2016": a, "2024": b, "delta": delta}
        sgma_era_drop = None
        if metrics["gwe_cumulative_drop"]["2016"] is not None and metrics["gwe_cumulative_drop"]["2024"] is not None:
            sgma_era_drop = round(
                float(metrics["gwe_cumulative_drop"]["2024"]) - float(metrics["gwe_cumulative_drop"]["2016"]),
                1,
            )
        label = props.get("subbasin_name") or f"GSP {gid}"
        status_row = status_by_gid.get(gid)
        status_date = None
        status_note = None
        if status_row is not None:
            posted = status_row.get("date_posted")
            if posted is not None and not (isinstance(posted, float) and math.isnan(posted)):
                status_date = str(pd.Timestamp(posted).date())
            status_note = format_status_note(status, posted)
        entry = {
            "gsp_id": gid,
            "label": label,
            "is_sjv": is_sjv_gsp_label(label),
            "status_2024": status,
            "status_date": status_date,
            "status_note": status_note,
            "compliant": status == "approved",
            "regulated": status in ("approved", "under_review"),
            "metrics": metrics,
            "sgma_era_gwe_drop_ft": sgma_era_drop,
        }
        sub = (subsidence_by_gsp or {}).get(gid)
        if sub:
            entry["subsidence_by_year"] = sub.get("by_year") or {}
            entry["subsidence_n_points"] = sub.get("n_points") or {}
            entry["mean_subsidence_ft_yr"] = sub.get("mean_ft_yr")
        entry["assessment"] = build_gsp_assessment(entry, gwe_series.get(gid, []))
        entry["determination"] = build_gsp_determination(entry)
        catalog.append(entry)
    return sorted(catalog, key=lambda x: x["label"])


def _gwe_at(gwe_rows: list[dict], year: int) -> float | None:
    for r in gwe_rows:
        if r.get("year") == year and r.get("gwe_ft") is not None:
            return float(r["gwe_ft"])
    return None


def build_gsp_assessment(entry: dict, gwe_rows: list[dict]) -> dict:
    """Compact Close-view assessment — descriptive, not causal."""
    status = entry.get("status_2024") or "unknown"
    m = entry.get("metrics") or {}
    drop = entry.get("sgma_era_gwe_drop_ft")
    trend4 = (m.get("gwe_trend_4yr_ft_yr") or {}).get("2024")

    if drop is None:
        overdraft = "Water table data limited"
        od_tone = "neutral"
    elif drop <= 0:
        if drop < 0:
            overdraft = f"Water table {abs(drop):.1f} ft higher in 2024 than in 2016"
        else:
            overdraft = "No change vs 2016"
        od_tone = "good"
    elif drop < 8:
        overdraft = f"Water table {drop:.1f} ft lower in 2024 than in 2016"
        od_tone = "warn"
    else:
        overdraft = f"Water table {drop:.1f} ft lower in 2024 than in 2016"
        od_tone = "bad"

    g22 = _gwe_at(gwe_rows, 2022)
    g24 = _gwe_at(gwe_rows, 2024)
    wet_rebound = g22 is not None and g24 is not None and (g24 - g22) >= 3

    if trend4 is None:
        trend = "4-yr trend: data limited"
        trend_tone = "neutral"
    elif trend4 > 0.25:
        trend = "4-yr: water table still falling"
        trend_tone = "bad"
    elif trend4 < -0.15:
        if drop is not None and drop > 5:
            trend = "4-yr: recent rise, but table still far below baseline"
            trend_tone = "warn"
        else:
            trend = "4-yr: water table stabilizing or recovering"
            trend_tone = "good"
    else:
        trend = "4-yr: flat / mixed"
        trend_tone = "neutral"

    if wet_rebound and od_tone != "good":
        trend += " · wet years may lift tables"

    ag_d = (m.get("total_ag_acres") or {}).get("delta")
    fall_d = (m.get("fallow_pct") or {}).get("delta")
    if ag_d is None and fall_d is None:
        ag = "Cropland data limited"
    else:
        bits = []
        if ag_d is not None:
            if abs(ag_d) < 5000:
                bits.append("acres steady")
            elif ag_d > 0:
                bits.append(f"acres +{ag_d:,.0f}")
            else:
                bits.append(f"acres {ag_d:,.0f}")
        if fall_d is not None and abs(fall_d) >= 0.5:
            bits.append(f"fallow {fall_d:+.1f} pp")
        ag = " · ".join(bits) if bits else "Cropland steady"

    well_d = (m.get("well_reports") or {}).get("delta")
    gwe_drop_24 = (m.get("gwe_cumulative_drop") or {}).get("2024")
    if well_d is None:
        residents = "Dry-well data limited"
    elif well_d > 0 and gwe_drop_24 is not None and gwe_drop_24 > 15:
        residents = f"Dry wells ↑ (+{well_d:.0f}) as GWE stress remains high"
    elif well_d > 0:
        residents = f"Dry wells ↑ (+{well_d:.0f}); reporting expanded post-2020"
    elif well_d < 0:
        residents = f"Reported dry wells ↓ ({well_d:.0f})"
    else:
        residents = "Dry-well reports flat"

    sgma_help = "Unclear"
    if od_tone == "good" and status == "approved":
        sgma_help = "Likely helping"
    elif od_tone == "bad" and status in ("inadequate", "state_intervention"):
        sgma_help = "Not yet helping"
    elif od_tone == "bad":
        sgma_help = "Weak / uneven"
    elif od_tone == "warn":
        sgma_help = "Partial"

    sgma_tone = {
        "Likely helping": "good",
        "Partial": "warn",
        "Weak / uneven": "warn",
        "Not yet helping": "bad",
    }.get(sgma_help, "neutral")

    return {
        "sgma_help": sgma_help,
        "sgma_tone": sgma_tone,
        "overdraft": overdraft,
        "trend": trend,
        "ag": ag,
        "residents": residents,
        "tones": {"overdraft": od_tone, "trend": trend_tone},
    }


def build_gsp_determination(entry: dict) -> str:
    """Legacy one-line summary — kept for JSON compatibility."""
    a = entry.get("assessment") or build_gsp_assessment(entry, [])
    return (
        f"Determination: {a['sgma_help']}. Water table: {a['overdraft']}. {a['trend']}. "
        f"Ag: {a['ag']}. Residents: {a['residents']}."
    )


def build_intro_page(gsp_catalog: list[dict]) -> dict:
    sjv = [g for g in gsp_catalog if g.get("is_sjv")]
    n = len(sjv)
    n_approved = sum(1 for g in sjv if g.get("compliant"))
    return {
        "hero_image": "assets/ppt/image8.png",
        "stats": {
            "subsidence_volume_km3": 14,
            "peak_rate_cm_yr": 30,
            "housing_risk_b": 1.87,
            "state_repair_b": 6,
            "state_repair_source": "SB 872 (2025): up to $150M/yr for canals + $150M/yr for Delta levees through 2046–47",
            "federal_repair_m": 889,
            "gsp_total": n,
            "gsp_approved": n_approved,
        },
        "impact_tiles": [
            {"title": "Canals lose slope", "text": "Gravity-fed delivery needs more energy and money for the same water volume."},
            {"title": "Levees sink", "text": "Flood risk rises where channels stay high but banks settle."},
            {"title": "Wells fail first", "text": "Shallow household wells go dry before deep irrigation wells."},
        ],
        "subsidence_callout": {
            "headline": "Subsidence is largely irreversible.",
            "body": (
                "Decades of groundwater overdraft lower the water table; as pore-water pressure drops, "
                "aquifer clay compacts and land sinks. Sinking can slow, but lost elevation cannot be restored."
            ),
        },
        "subsidence_figure": {
            "src": "assets/subsidence_poland_comparison.png",
            "alt": "USGS subsidence benchmarks in the San Joaquin Valley, 1925–1977 and 1988–2016",
            "caption": "USGS land-subsidence benchmarks — valley ground has sunk tens of feet as aquifers compact.",
        },
        "subsidence_mechanism": {
            "src": "assets/ppt/image9.gif",
            "alt": "Animation: groundwater pumping lowers the water table and compacts aquifer clay, causing subsidence",
            "caption": "Overdraft lowers the water table; pore spaces collapse and land sinks.",
        },
        "stat_boxes": [
            {
                "val": "14 km³",
                "lbl": "Valley subsidence volume, 2006–2022 (Knight & Lee)",
            },
            {"val": ">30 cm/yr", "lbl": "Peak subsidence rates in hotspots (Faunt et al.)"},
            {"val": "$6B", "lbl": "Proposed state repair fund · SB 872 (2025)"},
            {"val": "$1.87B", "lbl": "Lost in home values (UCR, 2025)"},
        ],
        "sgma_stakes": (
            "The Sustainable Groundwater Management Act requires water managers and farmers to bring overdrafted "
            "aquifers into balance by 2040. Studies project up to 1 million acres of San Joaquin Valley farmland "
            "may leave irrigated production — a severe economic hit. Pumping limits are already forcing growers to "
            "fallow orchards and idle fields ahead of the deadline; fallowed acreage is expected to grow sharply "
            "as GSAs enforce budgets (Ag Alert / Maven's Notebook, May 2026)."
        ),
        "quotes": [
            {
                "type": "policy",
                "text": "For all practical purposes, our communities are agriculture, and if we dial back agriculture, we dial back the things we see around us.",
                "author": "Aaron Fukuda, Tulare Irrigation District · SJV Water, Sept 2023",
                "source_url": "https://sjvwater.org/san-joaquin-valley-not-quite-ready-for-impacts-of-reducing-groundwater-pumping-on-agricultural-economy/",
                "image": "assets/intro_slide1.png",
            },
            {
                "type": "policy",
                "text": "By 2040, overall farm supplies in the valley could drop by as much as 20%—and irrigated cropland by nearly 900,000 acres.",
                "author": "Public Policy Institute of California · Sept 2023",
                "source_url": "https://www.ppic.org/blog/how-might-small-farms-fare-under-sgma/",
                "image": "assets/quote_solar_fallow.png",
            },
            {
                "type": "farmer",
                "text": (
                    "Farmers face a grim future as pumping allocations are drastically reduced and many must "
                    "decide how much acreage to retire so they can continue to farm their most productive parcels.\n\n"
                    "We've got tree guys going after the dairy guys, each one saying the other is using more water. "
                    "We've got to work together and support each other. This is not helping our cause."
                ),
                "author": "Maven's Notebook · Oct 2025 (Pixley Irrigation District)",
                "source_url": "https://mavensnotebook.com/2025/10/14/sjv-water-subsidence-photo-op-stirs-bad-feelings-in-already-bitter-groundwater-clash/",
                "image": "assets/quote_farmland_aerial.png",
            },
        ],
        "glossary": [
            {"term": "Groundwater basin", "def": "A large underground reservoir bounded by rock or clay."},
            {"term": "Groundwater overdraft", "def": "Pumping more water out than is replenished — the valley's chronic condition for decades."},
            {"term": "Subsidence", "def": "Land sinking as aquifer clay compacts — largely permanent damage. Lost elevation cannot be recovered by refilling the aquifer."},
            {"term": "SGMA", "def": "Sustainable Groundwater Management Act (2014) — requires local agencies to balance pumping with recharge by 2040."},
            {"term": "GSA", "def": "Groundwater Sustainability Agency — local public entity with authority to regulate wells and pumping."},
            {"term": "GSP", "def": "Groundwater Sustainability Plan — basin document showing how sustainable yield will be reached."},
            {"term": "Overdraft (map layer)", "def": "Water table vs the pre-SGMA average — mean groundwater elevation from DWR wells in each GSP, using all monitoring years before SGMA (2014). Positive = deeper than that norm (worse). Negative = shallower (recovered). Not pumping volume."},
            {"term": "Fallowed land", "def": "Irrigated acres left unplanted as pumping limits tighten — growers fallow or remove orchards to comply with SGMA (Ag Alert, May 2026)."},
            {"term": "Sustainable yield", "def": "Maximum pumping without causing undesirable results (see SMC)."},
        ],
        "smc": [
            {"id": "chronic_lowering", "label": "Chronic lowering of GWE", "desc": "Water table must not fall persistently below plan targets."},
            {"id": "reduction_storage", "label": "Reduction of storage", "desc": "Aquifer volume cannot keep shrinking unchecked."},
            {"id": "seawater", "label": "Seawater intrusion", "desc": "Coastal basins must block saltwater moving inland."},
            {"id": "quality", "label": "Degraded water quality", "desc": "Pumping cannot mobilize contaminants or salinity."},
            {"id": "subsidence_smc", "label": "Land subsidence", "desc": "Plans must avoid sinking that damages infrastructure."},
            {"id": "interconnected", "label": "Interconnected surface water", "desc": "Depletion cannot unduly reduce streams and rivers."},
        ],
        "orient": {
            "title": "Regulatory context and basin connectivity",
            "big_question": {
                "label": "Big question",
                "text": (
                    "More than a decade after SGMA, is groundwater regulation actually easing overdraft, "
                    "subsidence, and dry wells — and who pays when pumping is cut?"
                ),
            },
            "gsp_note": (
                "California split overdrafted basins into **Groundwater Sustainability Plan (GSP) areas** — "
                "each with a local Groundwater Sustainability Agency (GSA) that writes and implements a plan. "
                "This site tracks **45 San Joaquin Valley GSPs** separately. Metrics are plan-area averages; "
                "they are the unit SGMA uses for compliance, not individual farms or neighborhoods."
            ),
            "connected_note": (
                "Aquifers do not stop at GSP borders. If one plan area cuts pumping and lifts its water table "
                "while a neighbor keeps overdrafting, groundwater can flow across the line — local recovery may "
                "not translate into less valley-wide subsidence. Watch both **within-GSP trends** and **regional "
                "InSAR patterns** on the Explorer."
            ),
            "network_caption": (
                "Left to right: irrigation drives pumping and overdraft, compaction sinks land, and "
                "public infrastructure pays for damage. **Residents & rural communities** sit below — "
                "affected by dry wells, repair taxes, and farm-dependent local economies. The dashed "
                "line marks shared aquifers that cross GSP boundaries."
            ),
            "site_guide": [
                {
                    "tab": "Explorer",
                    "tab_id": "explorer",
                    "blurb": "Valley-wide subsidence and water-table maps — zoom to see where sinking and overdraft concentrate.",
                },
                {
                    "tab": "Close view",
                    "tab_id": "close",
                    "blurb": "One GSP at a time: 2016→2024 fallowing, water-table depth, dry wells, and DWR determination status.",
                },
                {
                    "tab": "Variable relationships",
                    "tab_id": "relationships",
                    "blurb": "Scatter charts comparing every GSP before vs after SGMA on each metric.",
                },
                {
                    "tab": "Takeaways",
                    "tab_id": "takeaways",
                    "blurb": "Plain-language synthesis for agriculture, residents, and environment — plus “Is SGMA working?”",
                },
                {
                    "tab": "Analysis",
                    "tab_id": "analysis",
                    "blurb": "GSP-level statistical patterns (correlations, group comparisons) — descriptive, not causal.",
                },
                {
                    "tab": "Sources",
                    "tab_id": "sources",
                    "blurb": "Official DWR, Land IQ, NASS, and InSAR datasets behind the numbers.",
                },
            ],
        },
    }


def _count_metric(
    gsps: list[dict],
    getter,
    pred,
) -> tuple[int, int]:
    """Return (matched, total) counting only GSPs where getter returns non-None."""
    matched = 0
    total = 0
    for g in gsps:
        val = getter(g)
        if val is None:
            continue
        total += 1
        if pred(val):
            matched += 1
    return matched, total


def _metric_delta(g: dict, key: str):
    return (g.get("metrics") or {}).get(key, {}).get("delta")


def _metric_year(g: dict, key: str, year: int):
    return (g.get("metrics") or {}).get(key, {}).get(str(year))


def _sgma_working_answer(
    ap_fallow_up: int,
    ap_fallow_n: int,
    ap_gwe_higher: int,
    ap_gwe_n: int,
    ap_below_24: int,
    ap_gwe24_n: int,
    sinking_24: int,
    sub24_n: int,
) -> str:
    """One-line effectiveness + equity verdict for the Takeaways header."""
    fallow_clause = (
        f"{ap_fallow_up} of {ap_fallow_n} approved GSPs with fallow data raised idle land"
        if ap_fallow_n
        else "approved basins are fallowing more cropland"
    )
    table_clause = (
        f"only {ap_gwe_higher} of {ap_gwe_n} approved GSPs with data show higher water tables in 2024"
        if ap_gwe_n
        else "water-table recovery is uneven"
    )
    below_clause = (
        f" and {ap_below_24} of {ap_gwe24_n} remain below the pre-SGMA average"
        if ap_gwe24_n and ap_below_24
        else ""
    )
    sub_clause = (
        f" while {sinking_24} of {sub24_n} GSPs with InSAR data still sank in 2024"
        if sub24_n
        else ""
    )
    return (
        "Partly. Pumping cuts are showing up as more fallow land, but the water table and land surface are recovering slowly. "
        + (
            f"{ap_gwe_higher} of {ap_gwe_n} approved GSPs show 2024 above 2016 (recent rise)"
            if ap_gwe_n
            else "Water-table recovery is uneven"
        )
        + (f"; {ap_below_24} of {ap_gwe24_n} stay below the pre-SGMA average in 2024" if ap_gwe24_n and ap_below_24 else "")
        + (f"; {sinking_24} of {sub24_n} still sank in 2024" if sub24_n else "")
        + ". One connected aquifer, many separate plans."
    )


def build_takeaways_page(
    gsp_catalog: list[dict],
    manifest: dict | None = None,
) -> dict:
    """Synthesis tab — computed from GSP catalog metrics only (2016→2024)."""
    sjv = [g for g in gsp_catalog if g.get("is_sjv")]
    approved = [g for g in sjv if g.get("compliant")]
    manifest = manifest or {}
    cumulative_layers = manifest.get("cumulative_layers") or []
    latest_sub_year = max((int(l["year"]) for l in cumulative_layers if l.get("year")), default=2024)

    def year_vals(key: str, year: str) -> list[float]:
        out = []
        for g in sjv:
            v = _metric_year(g, key, int(year))
            if v is not None:
                out.append(float(v))
        return out

    def fmt_int(n: float | None) -> str:
        if n is None:
            return "—"
        return f"{round(n):,}"

    def fmt_pct(n: float | None) -> str:
        if n is None:
            return "—"
        return f"{n:.1f}%"

    def fmt_ft(n: float | None) -> str:
        if n is None:
            return "—"
        return f"{n:.1f} ft"

    def fmt_sub_ft(n: float | None) -> str:
        if n is None:
            return "—"
        return f"{n:.2f} ft"

    fallow_up, fallow_n = _count_metric(sjv, lambda g: _metric_delta(g, "fallow_pct"), lambda d: d > 0)
    fallow_down, _ = _count_metric(sjv, lambda g: _metric_delta(g, "fallow_pct"), lambda d: d < 0)
    avg_fallow_16 = _avg(year_vals("fallow_pct", "2016"))
    avg_fallow_24 = _avg(year_vals("fallow_pct", "2024"))
    avg_fallow_delta = _avg([v for g in sjv if (v := _metric_delta(g, "fallow_pct")) is not None])

    ag_down, ag_n = _count_metric(sjv, lambda g: _metric_delta(g, "total_ag_acres"), lambda d: d < 0)
    ag_up, _ = _count_metric(sjv, lambda g: _metric_delta(g, "total_ag_acres"), lambda d: d > 0)
    sum_ag_16 = _sum(year_vals("total_ag_acres", "2016"))
    sum_ag_24 = _sum(year_vals("total_ag_acres", "2024"))
    net_ag_change = (sum_ag_24 - sum_ag_16) if sum_ag_16 is not None and sum_ag_24 is not None else None

    gwe_higher, gwe_n = _count_metric(sjv, lambda g: g.get("sgma_era_gwe_drop_ft"), lambda d: d < 0)
    gwe_lower, _ = _count_metric(sjv, lambda g: g.get("sgma_era_gwe_drop_ft"), lambda d: d > 0)

    below_baseline_24, gwe24_n = _count_metric(
        sjv, lambda g: _metric_year(g, "gwe_cumulative_drop", 2024), lambda v: v > 0,
    )
    above_baseline_24, _ = _count_metric(
        sjv, lambda g: _metric_year(g, "gwe_cumulative_drop", 2024), lambda v: v < 0,
    )
    avg_below_24 = _avg([
        float(v) for g in sjv
        if (v := _metric_year(g, "gwe_cumulative_drop", 2024)) is not None and v > 0
    ])

    well_up, well_n = _count_metric(sjv, lambda g: _metric_delta(g, "well_reports"), lambda d: d > 0)
    well_down, _ = _count_metric(sjv, lambda g: _metric_delta(g, "well_reports"), lambda d: d < 0)
    sum_well_16 = _sum(year_vals("well_reports", "2016"))
    sum_well_24 = _sum(year_vals("well_reports", "2024"))

    fallow_rise_gwe_ease = sum(
        1 for g in sjv
        if (d := _metric_delta(g, "fallow_pct")) is not None and d > 0
        and (e := g.get("sgma_era_gwe_drop_ft")) is not None and e < 0
    )
    fallow_rise_gwe_worse = sum(
        1 for g in sjv
        if (d := _metric_delta(g, "fallow_pct")) is not None and d > 0
        and (e := g.get("sgma_era_gwe_drop_ft")) is not None and e > 0
    )

    def subsidence_year(g: dict, year: int) -> float | None:
        v = (g.get("subsidence_by_year") or {}).get(str(year))
        return float(v) if v is not None else None

    sub_vals_24 = [v for g in sjv if (v := subsidence_year(g, latest_sub_year)) is not None]
    avg_sub_24 = round(float(np.mean(sub_vals_24)), 3) if sub_vals_24 else None
    sinking_24, sub24_n = _count_metric(
        sjv, lambda g: subsidence_year(g, latest_sub_year), lambda v: v > 0.05,
    )
    sub_gsp_n = sum(1 for g in sjv if g.get("subsidence_by_year"))
    avg_sub_yr = round(float(np.mean([g["mean_subsidence_ft_yr"] for g in sjv if g.get("mean_subsidence_ft_yr") is not None])), 3) if sub_gsp_n else None

    ap_sub_24_vals = [v for g in approved if (v := subsidence_year(g, latest_sub_year)) is not None]
    ap_avg_sub_24 = round(float(np.mean(ap_sub_24_vals)), 3) if ap_sub_24_vals else None
    ap_sinking_24, ap_sub24_n = _count_metric(
        approved, lambda g: subsidence_year(g, latest_sub_year), lambda v: v > 0.05,
    )
    ap_avg_sub_yr = round(float(np.mean([g["mean_subsidence_ft_yr"] for g in approved if g.get("mean_subsidence_ft_yr") is not None])), 3) if ap_sub24_n else None

    ap_fallow_up, ap_fallow_n = _count_metric(
        approved, lambda g: _metric_delta(g, "fallow_pct"), lambda d: d > 0,
    )
    ap_ag_down, ap_ag_n = _count_metric(
        approved, lambda g: _metric_delta(g, "total_ag_acres"), lambda d: d < 0,
    )
    ap_gwe_higher, ap_gwe_n = _count_metric(
        approved, lambda g: g.get("sgma_era_gwe_drop_ft"), lambda d: d < 0,
    )
    ap_below_24, ap_gwe24_n = _count_metric(
        approved, lambda g: _metric_year(g, "gwe_cumulative_drop", 2024), lambda v: v > 0,
    )

    sgma_ag = _sgma_takeaway_ag(ap_fallow_up, ap_fallow_n, ap_ag_down, ap_ag_n, net_ag_change, avg_fallow_delta)
    sgma_res = _sgma_takeaway_residents(
        ap_gwe_higher, ap_gwe_n, ap_below_24, ap_gwe24_n,
    )
    sgma_env = _sgma_takeaway_environment(
        ap_sinking_24, ap_sub24_n, sinking_24, sub24_n, latest_sub_year, ap_avg_sub_yr,
    )

    res_tone = "mixed"
    if ap_gwe24_n and ap_below_24 >= ap_gwe24_n / 2:
        res_tone = "bad" if not (ap_gwe_n and ap_gwe_higher >= ap_gwe_n / 2) else "mixed"
    elif ap_gwe_n and ap_gwe_higher >= ap_gwe_n / 2:
        res_tone = "good"

    return {
        "lede": "",
        "baseline_note": (
            "Pre-SGMA average: mean groundwater level from DWR wells in each GSP, "
            "using all monitoring years before SGMA (2014). Positive on the map = still deeper than that norm."
        ),
        "sgma_answer": {
            "question": "Is SGMA working?",
            "text": _sgma_working_answer(
                ap_fallow_up, ap_fallow_n, ap_gwe_higher, ap_gwe_n,
                ap_below_24, ap_gwe24_n, sinking_24, sub24_n,
            ),
            "tone": "mixed",
        },
        "headline_cards": [
            {
                "tone": "bad",
                "val": f"{sinking_24}/{sub24_n}",
                "lbl": "Still sinking (2024)",
                "hint": "GSPs with InSAR — sank >0.05 ft",
            },
            {
                "tone": "bad",
                "val": f"{below_baseline_24}/{gwe24_n}",
                "lbl": "Below pre-SGMA average",
                "hint": "2024 water table still deeper than pre-SGMA norm",
            },
            {
                "tone": "good",
                "val": f"{gwe_higher}/{gwe_n}",
                "lbl": "Table rose 2016→2024",
                "hint": "2024 above 2016 — recent recovery, not full recovery",
            },
            {
                "tone": "warn",
                "val": f"{fallow_up}/{fallow_n}",
                "lbl": "More fallow land",
                "hint": "Idle cropland rose 2016→2024",
            },
        ],
        "sections": [
            {
                "id": "governance",
                "title": "Fragmented regulation & policy efficiency",
                "focus": "Shared aquifers, separate plans",
                "body": (
                    "The Valley is split into separate groundwater plans on one connected aquifer. When one GSP "
                    "cuts pumping and a neighbor does not, groundwater flows across the line — local compliance "
                    "can fail to lift the shared water table."
                ),
                "bullets": [
                    f"{len(sjv)} SJV GSPs regulate pumping on hydrologically linked aquifers.",
                    f"{fallow_up} of {fallow_n} GSPs with fallow data raised idle land; {gwe_lower} of {gwe_n} with water-table data still show a lower table in 2024.",
                    "See Home → Regulatory context for the cross-boundary schematic.",
                ],
                "stats": [
                    {"val": str(len(sjv)), "lbl": "Separate GSP plans", "tone": "neutral"},
                    {"val": f"{fallow_up}/{fallow_n}", "lbl": "Higher fallow share", "tone": "warn"},
                    {
                        "val": f"{gwe_higher}/{gwe_n}",
                        "lbl": "Water table rose since 2016",
                        "tone": "good",
                    },
                ],
                "sgma_takeaway": (
                    "Fragmented regulation is central to SGMA's uneven results: farmers in enforcing plan areas "
                    "bear fallowing costs while basin-wide recovery stays incomplete."
                ),
                "verdict_tone": "warn",
            },
            {
                "id": "agriculture",
                "title": "Impacts to agriculture",
                "focus": "Producer costs — fallow & cropland",
                "body": (
                    "SGMA caps pumping. Producers idle fields and shrink irrigated acreage — the main near-term "
                    "farm cost. Those cuts concentrate where plans enforce limits, even when neighboring GSPs "
                    "keep pumping."
                ),
                "bullets": [
                    f"Average fallow share: {fmt_pct(avg_fallow_16)} (2016) → {fmt_pct(avg_fallow_24)} (2024)."
                    + (f" Mean change +{avg_fallow_delta:.1f} pp." if avg_fallow_delta is not None else ""),
                    f"{fallow_up} of {fallow_n} GSPs raised fallow share; {ag_down} of {ag_n} lost cropland acres.",
                    f"{fallow_rise_gwe_ease} GSPs raised fallow while the table rose; {fallow_rise_gwe_worse} raised fallow while it fell further.",
                ],
                "stats": [
                    {"val": fmt_pct(avg_fallow_24), "lbl": "Avg fallow share, 2024", "tone": "bad"},
                    {"val": f"{ag_down}/{ag_n}", "lbl": "Lost cropland acres", "tone": "bad"},
                    {"val": f"{ap_fallow_up}/{ap_fallow_n}", "lbl": "Approved GSPs — higher fallow", "tone": "bad"},
                ],
                "sgma_takeaway": sgma_ag,
                "verdict_tone": "bad",
                "explore_tab": "close",
            },
            {
                "id": "residents",
                "title": "Impacts to residents",
                "focus": "Shallow household wells & drinking-water access",
                "body": (
                    "Most domestic wells are shallow. When the water table stays below the pre-SGMA average, "
                    "household pumps lose suction and wells go dry."
                ),
                "bullets": [
                    f"Dry-well reports rose valley-wide: {fmt_int(sum_well_16)} (2016) → {fmt_int(sum_well_24)} (2024).",
                    f"{below_baseline_24} of {gwe24_n} GSPs remain below the pre-SGMA average in 2024.",
                    f"{ap_gwe_higher} of {ap_gwe_n} approved GSPs show a higher water table in 2024 than in 2016.",
                ],
                "stats": [
                    {
                        "val": f"{below_baseline_24}/{gwe24_n}",
                        "lbl": "GSPs with 2024 water table deeper than pre-2014 average — domestic well risk.",
                        "tone": "bad",
                    },
                    {
                        "val": f"{gwe_higher}/{gwe_n}",
                        "lbl": "GSPs where water table rose since 2016 — recent recovery",
                        "tone": "warn",
                    },
                    {
                        "val": f"{ap_gwe_higher}/{ap_gwe_n}",
                        "lbl": "Approved GSPs where water table is higher in 2024 vs 2016",
                        "tone": "good",
                    },
                ],
                "sgma_takeaway": sgma_res,
                "verdict_tone": res_tone,
                "explore_tab": "close",
            },
            {
                "id": "environment",
                "title": "Impacts to the environment",
                "focus": "Land subsidence (InSAR)",
                "body": (
                    "Land subsidence is measured from DWR InSAR (feet per epoch). Positive values mean net sinking. "
                    "Compaction is largely permanent — slowing pumping helps, but legacy loss keeps infrastructure at risk."
                ),
                "bullets": [
                    f"{latest_sub_year} epoch: avg {fmt_sub_ft(avg_sub_24)} among {sub24_n} GSPs; {sinking_24} sank >0.05 ft.",
                    f"Approved GSPs: {ap_sinking_24} of {ap_sub24_n} still sank >0.05 ft in {latest_sub_year}.",
                ],
                "stats": [
                    {"val": fmt_sub_ft(avg_sub_24), "lbl": f"Avg subsidence, {latest_sub_year}", "tone": "bad"},
                    {"val": f"{sinking_24}/{sub24_n}", "lbl": "GSPs sank >0.05 ft", "tone": "bad"},
                    {"val": f"{ap_sinking_24}/{ap_sub24_n}", "lbl": "Approved GSPs still sinking", "tone": "bad"},
                ],
                "sgma_takeaway": sgma_env,
                "verdict_tone": "bad",
                "explore_tab": "explorer",
            },
        ],
    }


def _count_joint(
    gsps: list[dict],
    get_a,
    pred_a,
    get_b,
    pred_b,
) -> tuple[int, int]:
    matched = 0
    total = 0
    for g in gsps:
        a, b = get_a(g), get_b(g)
        if a is None or b is None:
            continue
        total += 1
        if pred_a(a) and pred_b(b):
            matched += 1
    return matched, total


def _sgma_takeaway_ag(
    ap_fallow_up: int,
    ap_fallow_n: int,
    ap_ag_down: int,
    ap_ag_n: int,
    net_ag_change: float | None,
    avg_fallow_delta: float | None,
) -> str:
    if ap_fallow_n == 0:
        return "Insufficient fallow data on approved GSPs to assess SGMA impacts on producers."
    acres_part = ""
    if ap_ag_n and ap_ag_down >= max(1, ap_ag_n // 2):
        acres_part = f" and {ap_ag_down} of {ap_ag_n} with acreage data lost cropland"
    if ap_fallow_up >= ap_fallow_n / 2:
        delta_part = f" (avg +{avg_fallow_delta:.1f} pp valley-wide)" if avg_fallow_delta is not None else ""
        return (
            f"SGMA primarily hurts producers through idle land: {ap_fallow_up} of {ap_fallow_n} approved GSPs "
            f"with fallow data raised fallow share{delta_part}{acres_part} — pumping limits are paid for in lost "
            f"production, often without proportional basin-wide recovery when neighboring GSPs still overdraft."
        )
    if net_ag_change is not None and net_ag_change < 0:
        return (
            f"Producers face shrinking irrigated footprint under SGMA (valley cropland net {net_ag_change:+,} ac) "
            f"while only {ap_fallow_up} of {ap_fallow_n} approved GSPs with fallow data raised fallow share — adjustment is uneven."
        )
    return (
        f"Agricultural costs of SGMA are uneven: {ap_fallow_up} of {ap_fallow_n} approved GSPs with fallow data "
        f"raised fallow share{acres_part} — not all approved basins are cutting acres the same way."
    )


def _sgma_takeaway_residents(
    ap_gwe_higher: int,
    ap_gwe_n: int,
    ap_below_24: int,
    ap_gwe24_n: int,
) -> str:
    if ap_gwe_n == 0 and ap_gwe24_n == 0:
        return "Insufficient water-table data on approved GSPs to assess residential groundwater access."
    if (
        ap_gwe24_n
        and ap_below_24 >= ap_gwe24_n / 2
        and ap_gwe_n
        and ap_gwe_higher >= ap_gwe_n / 2
    ):
        return (
            f"Water-table recovery is partial: {ap_gwe_higher} of {ap_gwe_n} approved GSPs with data rose "
            f"2016→2024, but {ap_below_24} of {ap_gwe24_n} with baseline data still sit below the pre-SGMA average in 2024."
        )
    if ap_gwe24_n and ap_below_24 >= ap_gwe24_n / 2:
        return (
            f"Water tables remain below the pre-SGMA average in most approved basins: {ap_below_24} of {ap_gwe24_n} "
            f"approved GSPs with baseline data are still deeper than the pre-SGMA norm in 2024."
        )
    if ap_gwe_n and ap_gwe_higher >= ap_gwe_n / 2:
        return (
            f"Water tables rose 2016→2024 in most approved basins: {ap_gwe_higher} of {ap_gwe_n} "
            f"approved GSPs with data show a higher table in 2024 than in 2016."
        )
    return (
        f"SGMA has not yet lifted water tables consistently — only {ap_gwe_higher} of {ap_gwe_n} "
        f"approved GSPs with data show a higher table in 2024 vs 2016."
    )


def _sgma_takeaway_environment(
    ap_sinking_24: int,
    ap_sub24_n: int,
    sinking_24: int,
    sub24_n: int,
    latest_sub_year: int,
    ap_avg_sub_yr: float | None,
) -> str:
    if sub24_n == 0:
        return (
            f"Valley subsidence is mapped on the Explorer through {latest_sub_year} (InSAR); "
            "per-GSP aggregates require InSAR point coverage within each plan area."
        )
    sub_part = f"avg {ap_avg_sub_yr:.2f} ft/yr" if ap_avg_sub_yr is not None else "InSAR coverage limited"
    return (
        f"SGMA has not stopped land sinking: {sinking_24} of {sub24_n} GSPs with InSAR data sank >0.05 ft in "
        f"{latest_sub_year}, including {ap_sinking_24} of {ap_sub24_n} approved ({sub_part} across epochs)."
    )


def _sum(vals: list) -> float | None:
    nums = [float(v) for v in vals if v is not None]
    return round(sum(nums)) if nums else None


def _avg(vals: list) -> float | None:
    nums = [float(v) for v in vals if v is not None]
    return round(sum(nums) / len(nums), 1) if nums else None


ECON_PLOT_FILES = [
    "forest_correlations.png",
    "binned_subsidence_vs_depth.png",
    "binned_wells_vs_depth.png",
    "binned_fallow_vs_gwe.png",
    "approved_bootstrap_bars.png",
    "spillover_subsidence_scatter.png",
]


def ensure_econometrics_outputs() -> None:
    """Run sidecar analysis if plots are missing (does not block site build on failure)."""
    if all((ECON_OUT / f).is_file() for f in ECON_PLOT_FILES):
        return
    scripts = [
        ROOT / "analysis/explorer_gsp_econometrics.py",
        ROOT / "analysis/explorer_gsp_robust_econometrics.py",
    ]
    for script in scripts:
        if not script.is_file():
            return
    try:
        for script in scripts:
            subprocess.run(
                [sys.executable, str(script)],
                cwd=str(ROOT),
                check=True,
                capture_output=True,
                text=True,
            )
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"Warning: econometrics sidecar failed ({exc}); Analysis tab may be sparse.", file=sys.stderr)


def copy_econometrics_assets() -> dict[str, str]:
    ECON_ASSETS.mkdir(parents=True, exist_ok=True)
    web: dict[str, str] = {}
    for fname in ECON_PLOT_FILES:
        src = ECON_OUT / fname
        if not src.is_file():
            continue
        dest = ECON_ASSETS / fname
        shutil.copy2(src, dest)
        web[fname.replace(".png", "")] = f"assets/econometrics/{fname}"
    return web


def _fmt_rho(rho: float, lo: float, hi: float) -> str:
    if not math.isfinite(rho):
        return "insufficient data"
    ci = ""
    if math.isfinite(lo) and math.isfinite(hi):
        ci = f" (95% CI [{lo:+.2f}, {hi:+.2f}])"
    return f"ρ = {rho:+.2f}{ci}"


def _corr_row(corr_df: pd.DataFrame, pair_label: str) -> dict | None:
    if corr_df.empty:
        return None
    hit = corr_df.loc[corr_df["pair"] == pair_label]
    if hit.empty:
        return None
    r = hit.iloc[0]
    return {
        "rho": float(r["rho"]),
        "ci_lo": float(r["ci_lo"]) if pd.notna(r["ci_lo"]) else None,
        "ci_hi": float(r["ci_hi"]) if pd.notna(r["ci_hi"]) else None,
        "n": int(r["n"]),
    }


def build_econometrics_page(gsp_catalog: list[dict]) -> dict:
    ensure_econometrics_outputs()
    assets = copy_econometrics_assets()

    sjv = [g for g in gsp_catalog if g.get("is_sjv")]
    n_approved = sum(1 for g in sjv if g.get("compliant"))

    corr_df = (
        pd.read_csv(ECON_OUT / "spearman_correlations.csv")
        if (ECON_OUT / "spearman_correlations.csv").is_file()
        else pd.DataFrame()
    )
    approved_df = (
        pd.read_csv(ECON_OUT / "approved_bootstrap_ci.csv")
        if (ECON_OUT / "approved_bootstrap_ci.csv").is_file()
        else pd.DataFrame()
    )

    baseline_corr = _corr_row(corr_df, "Baseline depth vs SGMA-era change")
    fallow_corr = _corr_row(corr_df, "Fallow ↑ vs SGMA-era GWE change")
    wells_corr = _corr_row(corr_df, "2024 depth vs dry-well report Δ")
    sub_corr = _corr_row(corr_df, "2024 depth vs mean subsidence")

    sub_diff = None
    if not approved_df.empty:
        hit = approved_df.loc[approved_df["metric"] == "mean_subsidence_ft_yr"]
        if not hit.empty:
            r = hit.iloc[0]
            sub_diff = {
                "diff": float(r["diff"]),
                "ci_lo": float(r["ci_lo"]),
                "ci_hi": float(r["ci_hi"]),
                "mean_approved": float(r["mean_a"]),
                "mean_other": float(r["mean_b"]),
            }

    figures: list[dict] = []
    if assets.get("forest_correlations"):
        bl = _fmt_rho(
            baseline_corr["rho"], baseline_corr["ci_lo"], baseline_corr["ci_hi"]
        ) if baseline_corr else ""
        figures.append({
            "id": "forest",
            "src": assets["forest_correlations"],
            "title": "Rank correlations across GSP outcomes",
            "tag": "Overview",
            "caption": (
                "Spearman rank correlations with bootstrap 95% confidence intervals (2,000 resamples). "
                "Each bar is one outcome pair across San Joaquin Valley GSPs with complete data. "
                f"Strongest pattern: basins deeper below baseline in 2016 show more recovery 2016→2024 ({bl}). "
                "Associational only — not causal."
            ),
        })

    if assets.get("binned_subsidence_vs_depth"):
        sc = _fmt_rho(sub_corr["rho"], sub_corr["ci_lo"], sub_corr["ci_hi"]) if sub_corr else ""
        figures.append({
            "id": "subsidence_depth",
            "src": assets["binned_subsidence_vs_depth"],
            "title": "Environment: subsidence vs water-table depth",
            "tag": "Environment",
            "caption": (
                "Each dot is one GSP; the stepped line is the mean subsidence rate within depth bins. "
                "Positive horizontal axis = water table deeper below the pre-SGMA average in 2024. "
                f"Rank association {sc}. InSAR coverage varies by plan area — sparse GSPs are omitted."
            ),
        })

    if assets.get("binned_wells_vs_depth"):
        wc = _fmt_rho(wells_corr["rho"], wells_corr["ci_lo"], wells_corr["ci_hi"]) if wells_corr else ""
        figures.append({
            "id": "wells_depth",
            "src": assets["binned_wells_vs_depth"],
            "title": "Residents: dry-well reports vs depth stress",
            "tag": "Residents",
            "caption": (
                "Change in reported dry-well counts 2016→2024 vs 2024 depth below baseline. "
                f"Rank association {wc}. "
                "Reporting expanded after ~2020 and counts lack a pumping denominator — treat as illustrative."
            ),
        })

    if assets.get("binned_fallow_vs_gwe"):
        fc = _fmt_rho(fallow_corr["rho"], fallow_corr["ci_lo"], fallow_corr["ci_hi"]) if fallow_corr else ""
        figures.append({
            "id": "fallow_gwe",
            "src": assets["binned_fallow_vs_gwe"],
            "title": "Agriculture: fallowing vs groundwater recovery",
            "tag": "Agriculture",
            "caption": (
                "Teal = approved GSPs; gray = all others. Vertical axis: SGMA-era change in water-table depth "
                "(negative = net recovery). Horizontal: increase in fallow share (percentage points). "
                f"Rank association {fc} — producers can fallow without local table recovery, or vice versa."
            ),
        })

    if assets.get("approved_bootstrap_bars"):
        sub_line = ""
        if sub_diff:
            sub_line = (
                f" Approved GSPs average {sub_diff['mean_approved']:.2f} ft/yr subsidence vs "
                f"{sub_diff['mean_other']:.2f} for others (Δ = {sub_diff['diff']:+.2f}, "
                f"95% CI [{sub_diff['ci_lo']:+.2f}, {sub_diff['ci_hi']:+.2f}])."
            )
        figures.append({
            "id": "approved_bars",
            "src": assets["approved_bootstrap_bars"],
            "title": "Approved vs other GSPs — mean differences",
            "tag": "Governance",
            "caption": (
                "Bootstrap 95% CIs for approved minus other GSP means (only five approved SJV plans). "
                "Large subsidence gaps are descriptive — worst basins face scrutiny, so approval is not a random treatment."
                + sub_line
            ),
        })

    if assets.get("spillover_subsidence_scatter"):
        figures.append({
            "id": "spillover",
            "src": assets["spillover_subsidence_scatter"],
            "title": "Spatial spillover: neighbor vs own subsidence",
            "tag": "Spatial",
            "caption": (
                "Neighbor-weighted mean subsidence (from GSA adjacency weights) vs each GSP's own InSAR mean. "
                "Points above the diagonal sink faster than their weighted neighbors — expected in contiguous aquifers, "
                "not evidence of policy spillovers."
            ),
        })

    highlights = []
    if baseline_corr and baseline_corr.get("rho", 0) < -0.25:
        highlights.append({
                "label": "Mean reversion",
                "text": (
                    f"GSPs deeper below baseline in 2016 show more recovery by 2024 "
                    f"({_fmt_rho(baseline_corr['rho'], baseline_corr['ci_lo'], baseline_corr['ci_hi'])}). "
                    "Hydrologic reversion, not proof that SGMA caused recovery."
                ),
            })
    if fallow_corr:
        highlights.append({
            "label": "Fallowing ≠ recovery",
            "text": (
                f"Fallow increase and SGMA-era water-table change are essentially uncorrelated at GSP scale "
                f"({_fmt_rho(fallow_corr['rho'], fallow_corr['ci_lo'], fallow_corr['ci_hi'])})."
            ),
        })
    if sub_diff and sub_diff["ci_hi"] < 0:
        highlights.append({
            "label": "Approved basins sink less",
            "text": (
                f"Descriptive only: approved GSPs show lower mean subsidence "
                f"({sub_diff['mean_approved']:.2f} vs {sub_diff['mean_other']:.2f} ft/yr)."
            ),
        })

    return {
        "lede": (
            f"GSP-level statistical patterns across {len(sjv)} San Joaquin Valley plan areas "
            f"({n_approved} approved in 2024), using the same metrics as Close view and Takeaways. "
            "Spearman rank correlations, binned means, and bootstrap confidence intervals — descriptive, not causal."
        ),
        "caveat": (
            "With ~45 GSPs and uneven data coverage, confidence intervals are wide and a single basin "
            "(e.g., White Wolf, Kern hotspots) can move estimates. Dry-well counts reflect reporting, not true failure rates. "
            "Approved-plan comparisons describe selection, not treatment effects."
        ),
        "highlights": highlights,
        "figures": figures,
    }


def build_sources_page() -> dict:
    return {
        "intro": (
            "Official datasets behind the Explorer maps, Close view metrics, and Analysis tab."
        ),
        "items": [
            {
                "label": "DWR InSAR land subsidence (TRE Altamira)",
                "url": "https://data.cnra.ca.gov/dataset/5e2d49e1-9ed0-425e-9f3e-2cda4a213c26",
                "description": (
                    "Satellite radar measures vertical land-surface movement. Powers annual rate and cumulative "
                    "subsidence layers on the Explorer time slider (2016–2024) and per-GSP subsidence aggregates "
                    "in Takeaways and Analysis. Map services: gis.water.ca.gov/arcgisimg/rest/services/SAR."
                ),
            },
            {
                "label": "DWR GSP Monitoring Network (MNM)",
                "url": "https://data.cnra.ca.gov/dataset/gspmd",
                "description": (
                    "Groundwater level sites and measurements submitted through the SGMA Portal's "
                    "Monitoring Network Module. Used to compute each GSP's water-table level vs the pre-SGMA "
                    "average and 4-year trends in Close view and overdraft layers."
                ),
            },
            {
                "label": "DWR Groundwater Sustainability Plan areas",
                "url": "https://gis.water.ca.gov/arcgis/rest/services/Boundaries/i03_Groundwater_Sustainability_Plan_Areas/FeatureServer/0",
                "description": (
                    "Official GSP boundary polygons. Define the geographic units for all GSP-level metrics "
                    "and map coloring across the site."
                ),
            },
            {
                "label": "DWR GSP determination status (SGMA portal)",
                "url": "https://sgma.water.ca.gov/portal/gsp/all",
                "description": (
                    "DWR review outcomes for each submitted plan (approved, incomplete, etc.). "
                    "Drives GSP status labels, compliance filters, and determination text in Close view."
                ),
            },
            {
                "label": "DWR statewide crop mapping (Land IQ)",
                "url": "https://data.cnra.ca.gov/dataset/statewide-crop-mapping",
                "description": (
                    "Annual field-scale land-use mapping contracted by DWR. Fallowed-land share and total "
                    "cropland acres by GSP come from this program (aggregated to plan areas for 2016–2024)."
                ),
            },
            {
                "label": "DWR land use surveys (program overview)",
                "url": "https://water.ca.gov/Programs/Water-Use-And-Efficiency/Land-And-Water-Use/Land-Use-Surveys",
                "description": (
                    "Background on how DWR produces irrigated-crop and idle/fallow land classifications "
                    "via remote sensing and ground reference — the parent program for the crop-mapping data."
                ),
            },
            {
                "label": "USDA NASS Census of Agriculture",
                "url": "https://www.nass.usda.gov/Programs-and-Services/Ag-Census/index.php",
                "description": (
                    "County-level farm operation counts by acreage class (2012, 2017, 2022). "
                    "Large-farm share in Close view is the share of operations ≥500 acres assigned to each GSP."
                ),
            },
            {
                "label": "DWR Household Water Supply Shortage Reporting (MyDryWell)",
                "url": "https://mydrywell.water.ca.gov/",
                "description": (
                    "Voluntary reports of dry or failing household wells. Mapped as points on the Explorer "
                    "and counted by GSP/year in Close view — reporting expanded after ~2020, so trends are not a pure failure rate."
                ),
            },
        ],
    }


def build_explorer_variable_guides() -> list[dict]:
    return [
        {
            "id": "subsidence_annual_rate",
            "tab": "Subsidence rate",
            "label": "Annual subsidence rate",
            "control": "subsidence",
            "value": "annual_rate",
            "definition": (
                "InSAR measures how fast land surface is sinking each year (ft/yr) during the mapped "
                "Dec–Dec epoch. Green = slower · red = faster."
            ),
            "why": (
                "Subsidence is a Sustainable Management Criteria under SGMA — plans must avoid sinking that "
                "damages canals, levees, and roads. This layer shows where compaction is still active, not "
                "just historical totals."
            ),
            "source": "DWR TRE Altamira InSAR",
        },
        {
            "id": "subsidence_cumulative",
            "tab": "Cumulative sink",
            "label": "Cumulative subsidence since Jun 2015",
            "control": "subsidence",
            "value": "cumulative",
            "definition": (
                "Total vertical displacement since a shared June 2015 reference — comparable across years on "
                "the time slider."
            ),
            "why": (
                "Shows legacy sinking that SGMA is trying to halt. Valleys with large cumulative loss face "
                "higher infrastructure repair costs even if annual rates slow."
            ),
            "source": "DWR TRE Altamira InSAR",
        },
        {
            "id": "overdraft_cumulative",
            "tab": "GWE baseline",
            "label": "Water table vs pre-SGMA average",
            "control": "overdraft",
            "value": "cumulative",
            "definition": (
                "Average groundwater elevation in each GSP vs the pre-SGMA average — mean of all DWR monitoring "
                "years before SGMA (2014). Positive = deeper than that norm · negative = shallower (recovered)."
            ),
            "why": (
                "SGMA asks whether basins are reversing chronic lowering of groundwater. This is the same "
                "metric as Close view — the core compliance signal for storage and overdraft."
            ),
            "source": "DWR MNM monitoring network",
        },
        {
            "id": "overdraft_annual",
            "tab": "GWE trend",
            "label": "Water table trend (4-yr, ft/yr)",
            "control": "overdraft",
            "value": "annual",
            "definition": (
                "Four-year slope of groundwater elevation ending at the slider year. Positive = still falling · "
                "negative = rising."
            ),
            "why": (
                "A single wet year can lift the water table while long-run overdraft continues. The 4-yr trend "
                "smooths drought–flood swings to show whether plans are changing trajectories."
            ),
            "source": "DWR MNM monitoring network",
        },
        {
            "id": "gsp_status",
            "tab": "GSP status",
            "label": "GSP determination status",
            "control": "equity",
            "value": "gsp_status",
            "definition": (
                "DWR review outcome for each plan in the selected year — approved, under review, inadequate, "
                "or pre-plan (before 2016)."
            ),
            "why": (
                "Regulatory approval is not the same as hydrologic recovery. Mapping status shows where state "
                "oversight is still pending vs where locals are formally cleared to implement their plans."
            ),
            "source": "DWR GSP determination records",
        },
        {
            "id": "fallowed_land",
            "tab": "Fallowed land",
            "label": "Fallowed land (%)",
            "control": "equity",
            "value": "fallowed_land",
            "definition": (
                "Share of irrigated cropland left unplanted within each GSP — idle fields, pulled orchards, "
                "or land taken out of production (Land IQ)."
            ),
            "why": (
                "Fallowing is a main SGMA adjustment margin: cut pumping by farming less acreage. Rising fallow "
                "with easing groundwater stress suggests plans are working; rising fallow with deepening "
                "overdraft means painful cuts without recovery."
            ),
            "source": "Land IQ land use",
        },
        {
            "id": "water_access",
            "tab": "Water access",
            "label": "Dry-well reports (cumulative)",
            "control": "equity",
            "value": "water_access",
            "definition": (
                "Domestic and small-supply well failure reports accumulated in each GSP from 2016 through the "
                "selected year (bias-adjusted for expanded reporting after 2020)."
            ),
            "why": (
                "Households and rural communities bear access costs when the water table drops. This lens tracks "
                "who may be losing reliable supply even as agricultural plans adjust."
            ),
            "source": "DWR dry-well reporting",
        },
        {
            "id": "farm_consolidation",
            "tab": "Consolidation",
            "label": "Large-farm share (NASS, %)",
            "control": "equity",
            "value": "farm_consolidation",
            "definition": (
                "Share of county farm operations ≥500 acres assigned to each GSP — a structural measure, not "
                "tonnage or revenue."
            ),
            "why": (
                "SGMA compliance costs (monitoring, pumping cuts, infrastructure) may push out smaller operators. "
                "Rising large-farm share under stress signals equity pressure on who can stay in business."
            ),
            "source": "USDA NASS Census of Agriculture",
        },
        {
            "id": "ag_production",
            "tab": "Cropland acres",
            "label": "Total cropland acres (Land IQ)",
            "control": "equity",
            "value": "ag_production",
            "definition": (
                "Total cropland acres in each GSP — best available proxy for irrigated footprint, not yield "
                "or crop mix."
            ),
            "why": (
                "High acreage keeps irrigation demand on the table even when plans are approved. Pair with "
                "groundwater metrics: stable acres + falling water table = production holding while aquifers "
                "still deplete."
            ),
            "source": "Land IQ land use",
        },
        {
            "id": "dry_wells",
            "tab": "Dry-well points",
            "label": "Dry-well locations (points)",
            "control": "toggle",
            "value": "well-dots",
            "definition": (
                "Individual reported dry-well locations for the selected year — drought years highlighted in "
                "red."
            ),
            "why": (
                "Choropleth totals hide hotspots. Points show where access failures cluster in drought years "
                "vs wet years."
            ),
            "source": "DWR dry-well reporting",
        },
    ]


def build_relationship_variables() -> list[dict]:
    return [
        {
            "id": "gwe_cumulative_drop",
            "label": "Water table vs pre-SGMA average",
            "x_label": "2016 — ft above/below pre-SGMA average",
            "y_label": "2024 — ft above/below pre-SGMA average",
            "delta_label": "Change 2016→2024 (ft; negative = table rose)",
            "good_short": "water table higher in 2024 — below diagonal",
            "bad_short": "water table lower in 2024 — above diagonal",
            "note": "Each dot is one GSP. Positive = below pre-SGMA average (deeper); negative = above (higher). Dashed line = no change. Points below the line = table rose from 2016 to 2024.",
            "caveat": None,
            "gwe_context": False,
            "chart_mode": "paired",
            "lower_better": True,
            "pre_year": 2016,
            "post_year": 2024,
        },
        {
            "id": "gwe_trend_ft_yr",
            "label": "Water table trend (4-yr, ft/yr)",
            "x_label": "2016 4-yr water table trend (ft/yr)",
            "y_label": "2024 4-yr water table trend (ft/yr)",
            "delta_label": "Change in 4-yr trend (ft/yr)",
            "good_short": "less falling / more recovery by 2024 — below diagonal",
            "bad_short": "water table falling faster by 2024 — above diagonal",
            "note": "4-year trend smooths wet-year spikes (2023–24). Positive = water table still falling (not less pumping).",
            "caveat": "Recent wet years can lift short-term trends even while cumulative loss remains.",
            "gwe_context": False,
            "chart_mode": "paired",
            "metric_key": "gwe_trend_4yr_ft_yr",
            "lower_better": True,
            "pre_year": 2016,
            "post_year": 2024,
        },
        {
            "id": "fallow_pct",
            "label": "Fallowed land (%)",
            "x_label": "2016 fallowed land (%)",
            "y_label": "2024 fallowed land (%)",
            "delta_label": "Change in fallow share (pp)",
            "good_short": "more fallow with easing GWE stress — SGMA adjustment working",
            "bad_short": "more fallow while GWE loss deepens — painful cutbacks without recovery",
            "note": "SGMA plans use fallowing to cut pumping. Pair with GWE: fallow rising alone is not automatically good or bad.",
            "caveat": None,
            "gwe_context": True,
            "chart_mode": "paired",
            "lower_better": False,
            "pre_year": 2016,
            "post_year": 2024,
        },
        {
            "id": "well_reports",
            "label": "Dry-well reports (cumulative)",
            "x_label": "2024 cumulative GWE drop (ft)",
            "y_label": "2024 dry-well reports",
            "delta_label": "Change in reports (2016→2024)",
            "good_short": "fewer dry-well reports where GWE stress is lower",
            "bad_short": "more reports where groundwater remains deeply depleted",
            "note": "Left chart: dry-well reports vs groundwater stress in 2024 (each dot = one GSP). Right: SGMA-era change in report counts.",
            "caveat": (
                "DWR reporting expanded sharply after 2020 — counts reflect both more failures and more reporting. "
                "Groundwater context helps separate physical stress from reporting noise."
            ),
            "gwe_context": False,
            "chart_mode": "wells_vs_gwe",
            "lower_better": True,
            "pre_year": 2016,
            "post_year": 2024,
        },
        {
            "id": "total_ag_acres",
            "label": "Cropland acres (Land IQ)",
            "x_label": "2016 cropland acres",
            "y_label": "2024 cropland acres",
            "delta_label": "Change in cropland (acres)",
            "good_short": "stable cropland with easing GWE — productivity without deepening overdraft",
            "bad_short": "stable or rising acres while GWE loss continues — irrigation demand persists",
            "note": "Land IQ total cropland acres — best available proxy; county yield data not linked at GSP scale.",
            "caveat": None,
            "gwe_context": True,
            "chart_mode": "paired",
            "lower_better": False,
            "pre_year": 2016,
            "post_year": 2024,
        },
        {
            "id": "large_farm_share",
            "label": "Large-farm share (NASS, %)",
            "x_label": "2016 large-farm share (%)",
            "y_label": "2024 large-farm share (%)",
            "delta_label": "Change in large-farm share (pp)",
            "good_short": "less large-farm share by 2024 — below diagonal",
            "bad_short": "more large-farm share by 2024 — above diagonal",
            "note": "Share of county farm operations ≥500 ac assigned to GSP — structure, not tonnage.",
            "caveat": (
                "Rising large-farm share does not mean less food production, but it suggests small farms "
                "may be losing ground to bigger operators under water stress and SGMA compliance costs."
            ),
            "gwe_context": True,
            "chart_mode": "paired",
            "lower_better": True,
            "pre_year": 2016,
            "post_year": 2024,
        },
    ]


def clean_for_json(obj):
    if isinstance(obj, dict):
        return {k: clean_for_json(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [clean_for_json(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, (pd.Timestamp,)):
        return obj.isoformat()
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    return obj


def build_data() -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.is_file() else {}
    cumulative = manifest.get("cumulative_layers", [])
    annual = manifest.get("annual_rate_layers", [])
    for layer in cumulative + annual:
        layer["web_path"] = layer["file"].replace("outputs/subsidence/", "subsidence/")

    counties_gdf = load_counties()
    gsp_gdf = exclude_non_sjv_gsps(clip_to_counties(gpd.read_file(GSP_PATH), counties_gdf))
    status_df = pd.read_csv(GSP_STATUS) if GSP_STATUS.is_file() else pd.DataFrame()

    status_timeline = build_status_timeline(status_df)
    fallow_series, ag_series = load_fallow_ag_series()
    gwe_series = load_gsp_gwe_yearly(gsp_gdf)
    gwe_pre_sgma = build_gwe_pre_sgma_baseline(gwe_series)
    gwe_trend_series = build_gwe_trend_series(gwe_series, window=3, value_key="gwe_trend_ft_yr")
    gwe_trend_4yr_series = build_gwe_trend_series(gwe_series, window=4, value_key="gwe_trend_4yr_ft_yr")
    dry_wells, well_series = load_dry_wells_gsp(gsp_gdf, counties_gdf)
    farm_county_df = load_farm_consolidation_by_county()
    farm_by_county = build_farm_consolidation_timeline(farm_county_df)
    gsp_county_fips = assign_gsp_county_fips(gsp_gdf, counties_gdf)
    subsidence_by_gsp = load_subsidence_gsp_yearly(gsp_gdf, counties_gdf)

    gsps = simplify_geojson_gdf(gsp_gdf)
    gsps = enrich_gsps(
        gsps, status_timeline, fallow_series, ag_series, gwe_series, gwe_trend_series,
        gwe_trend_4yr_series, well_series, gwe_pre_sgma, gsp_county_fips, farm_by_county,
    )
    scale_max = gsps.pop("_scale_max", {})
    split_gwe_scale = compute_split_gwe_scale(gsps)
    gsp_catalog = build_gsp_catalog(gsps, gwe_series, status_df, subsidence_by_gsp)
    intro_page = build_intro_page(gsp_catalog)
    takeaways_page = build_takeaways_page(gsp_catalog, manifest)
    econometrics_page = build_econometrics_page(gsp_catalog)
    sources_page = build_sources_page()

    xmin, ymin, xmax, ymax = counties_gdf.total_bounds
    pad = 0.012
    bbox = manifest.get("bbox_wgs84") or {
        "xmin": xmin - pad, "ymin": ymin - pad, "xmax": xmax + pad, "ymax": ymax + pad,
    }

    return clean_for_json({
        "manifest": manifest,
        "cumulative_layers": cumulative,
        "annual_layers": annual,
        "slider_years": SLIDER_YEARS,
        "bbox": bbox,
        "counties": json.loads(counties_gdf.to_json()),
        "gsps": gsps,
        "dry_wells": dry_wells,
        "drought_years": sorted(DROUGHT_YEARS),
        "scale_max": scale_max,
        "explorer_note": (
            "SGMA sets basin pumping budgets and GSP rules, but ag production often persists — "
            "especially permanent crops (nuts, grapes) that keep irrigating. High ag acreage helps explain "
            "continued groundwater stress even after plans are approved. Fallowing is one adjustment margin; "
            "consolidation toward larger farms is another."
        ),
        "gsp_catalog": gsp_catalog,
        "intro_page": intro_page,
        "takeaways_page": takeaways_page,
        "econometrics_page": econometrics_page,
        "sources_page": sources_page,
        "relationship_variables": build_relationship_variables(),
        "explorer_variable_guides": build_explorer_variable_guides(),
        "sgma_window": {"pre_year": 2016, "post_year": 2024, "baseline_note": "Pre-SGMA average = all DWR well years before 2014; 2016→2024 = site's main change window"},
        "split_comparison": {
            "pre_year": 2014,
            "post_year": 2024,
            "metric_key": "gwe_ft",
            "gwe_scale": split_gwe_scale,
            "pre_title": "2014",
            "pre_subtitle": "Average groundwater elevation by GSP (ft, MNM wells)",
            "post_title": "2024",
            "post_subtitle": "Same metric — compare left to right on the same color scale",
            "caption": (
                "Both maps show monitored groundwater elevation (GWE) averaged across each GSP — "
                "not cumulative loss since a baseline. Green = shallower water table · Red = deeper. "
                "If 2024 is redder than 2014, the water table fell over the SGMA era."
            ),
            "legend_title": "Groundwater elevation (ft)",
            "legend_min": "Shallower (higher elevation)",
            "legend_mid": "Mid elevation",
            "legend_max": "Deeper (lower elevation)",
            "legend_note": (
                "Same colors on both maps. Elevation is from DWR monitoring-well networks — "
                "values are comparable within the valley but not raw well depth at a single point."
            ),
        },
        "compare_modes": [
            {"id": "side_by_side", "label": "Side-by-side maps", "description": "Overdraft and equity lens on parallel maps — clearest for comparing patterns."},
            {"id": "scatter", "label": "Scatter plot", "description": "Each GSP as a dot: equity metric vs groundwater stress for the selected year."},
            {"id": "overlay", "label": "Stacked overlay", "description": "Semi-transparent layers on one map (can be hard to read)."},
        ],
        "overdraft_layers": [
            {
                "id": "cumulative",
                "label": "Water table vs pre-SGMA average",
                "description": "Same metric as Close view: ft above/below pre-SGMA average. Positive = below baseline (deeper); negative = above (higher). Updates with the year slider.",
            },
            {
                "id": "annual",
                "label": "Water table trend (4-yr, ft/yr)",
                "description": "Same 4-year linear trend as Close view. Positive = falling · negative = rising. Ending at the selected slider year.",
            },
            {"id": "none", "label": "None", "description": "Hide overdraft overlay."},
        ],
        "equity_lenses": [
            {"id": "none", "label": "None", "description": "Hide equity overlays — subsidence and overdraft only."},
            {"id": "gsp_status", "label": "GSP status", "description": "DWR determination by year. Grey = pre-plan (2016–2019), amber = under review, teal = approved, red = inadequate."},
            {"id": "fallowed_land", "label": "Fallowed land", "description": "Idle cropland within each GSP (Land IQ). Rising fallow reflects SGMA pumping cuts and land taken out of irrigated production."},
            {"id": "water_access", "label": "Water access", "description": "Cumulative domestic dry-well reports in each GSP through the selected year (2016 onward, bias-adjusted)."},
            {"id": "farm_consolidation", "label": "Farm consolidation", "description": "USDA NASS: share of county farm operations ≥500 ac (2012→2022). Rising share = consolidation toward larger operations."},
            {"id": "ag_production", "label": "Cropland acres (Land IQ)", "description": "Total cropland acres in GSP (Land IQ). Best available proxy — not yield. High acreage = continued irrigation demand."},
        ],
        "overdraft_legend": {
            "cumulative": {
                "title": "Water table vs pre-SGMA average (ft)",
                "min": "Above baseline (higher)",
                "max": "Below baseline (deeper)",
            },
            "annual": {
                "title": "Water table trend (4-yr, ft/yr)",
                "min": "Rising (−)",
                "max": "Falling (+)",
            },
        },
        "farm_consolidation_legend": {
            "min": "Fewer large ops (<15%)",
            "max": "More large ops (500+ ac)",
        },
        "ag_production_legend": {
            "min": "Less cropland",
            "max": "More cropland (irrigation demand)",
        },
        "status_legend": [
            {"status_std": "approved", "label": "Approved", "color": "#004655"},
            {"status_std": "under_review", "label": "Under review", "color": "#c8922a"},
            {"status_std": "inadequate", "label": "Inadequate", "color": "#c0392b"},
            {"status_std": "inadequate_under_review", "label": "Inadequate (review)", "color": "#c0392b"},
            {"status_std": "state_intervention", "label": "State intervention", "color": "#6b1d1d"},
            {"status_std": "pre_sgma", "label": "Pre-SGMA (before 2016)", "color": "#cccccc"},
            {"status_std": "incomplete", "label": "Pre-plan / incomplete", "color": "#888888"},
        ],
        "well_legend": [
            {"label": "Drought year", "color": "#c0392b"},
            {"label": "Non-drought year", "color": "#5dade2"},
        ],
    })


def render_html() -> str:
    return '''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Sinking Valley Explorer — SGMA Effectiveness</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,500;0,600;1,400&family=IBM+Plex+Sans:wght@300;400;600&display=swap" rel="stylesheet"/>
  <link href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css" rel="stylesheet"/>
  <link rel="stylesheet" href="sinking_valley_explorer.css"/>
</head>
<body>
  <nav class="top-tabs" id="top-tabs">
    <div class="top-tabs-inner">
      <span class="top-brand">Sinking Valley Explorer</span>
      <button type="button" class="tab-btn active" data-tab="home">Home</button>
      <button type="button" class="tab-btn" data-tab="explorer">Explorer</button>
      <button type="button" class="tab-btn" data-tab="close">Close view</button>
      <button type="button" class="tab-btn" data-tab="relationships">Variable relationships</button>
      <button type="button" class="tab-btn" data-tab="takeaways">Takeaways</button>
      <button type="button" class="tab-btn" data-tab="analysis">Analysis</button>
      <button type="button" class="tab-btn" data-tab="sources">Sources</button>
      <span class="top-credit">Alexandra Beyret | ECON 30</span>
    </div>
  </nav>
  <div id="tab-home" class="tab-panel active">
    <div class="intro-page" id="intro-page">
      <header class="intro-hero intro-hero-photo">
        <div class="intro-hero-overlay"></div>
        <div class="intro-hero-content">
        <p class="intro-eyebrow">San Joaquin Valley · Groundwater &amp; equity</p>
        <h1>The San Joaquin Central Valley is sinking.</h1>
        <p class="intro-lede">Decades of <dfn class="intro-dfn" title="Pumping more groundwater out than is naturally replenished">groundwater overextraction</dfn> have compacted aquifers, damaged canals, and left household wells dry. California developed new regulation to mitigate these consequences through the 2014 Sustainable Groundwater Management Act (SGMA). More than a decade on, is groundwater regulation being implemented in ways that address the problem it sought to solve?</p>
        <a href="#" class="intro-cta" data-goto-tab="explorer">Open the map explorer →</a>
        </div>
      </header>
      <section class="intro-section">
        <h2>Why this matters</h2>
        <div class="intro-subsidence-panel">
          <div class="intro-subsidence-callout" id="intro-subsidence-callout"></div>
          <figure class="intro-subsidence-mechanism" id="intro-subsidence-mechanism"></figure>
        </div>
        <div class="intro-why-grid">
          <div class="intro-stakes-metrics" id="intro-metrics"></div>
          <figure class="intro-subsidence-fig" id="intro-subsidence-fig"></figure>
          <div class="intro-impact-tiles" id="intro-impact-tiles"></div>
        </div>
      </section>
      <section class="intro-section">
        <h2>Is SGMA the solution?</h2>
        <p class="intro-body" id="intro-sgma-stakes"></p>
        <p class="intro-body">SGMA requires critically overdrafted groundwater basins to form Groundwater Sustainability Agencies (GSAs) and adopt Groundwater Sustainability Plans (GSPs) reaching sustainable yield by 2040 (2042 for the worst basins). DWR reviews each plan against six Sustainable Management Criteria (SMC), the undesirable results local agencies must avoid:</p>
        <div class="smc-bubbles" id="intro-smc"></div>
      </section>
      <section class="intro-section">
        <h2>Voices from the valley</h2>
        <div class="intro-quotes-slideshow" id="intro-quotes-slideshow" tabindex="0" role="region" aria-label="Valley quotes slideshow"></div>
      </section>
      <section class="intro-section">
        <div class="intro-orient-panel" id="intro-orient-panel"></div>
      </section>
      <section class="intro-section">
        <h2>Key terms</h2>
        <dl class="intro-glossary" id="intro-glossary"></dl>
      </section>
      <footer class="intro-footer">
        <p><a href="#" class="intro-cta inline-cta" data-goto-tab="explorer">Open the Explorer →</a> for subsidence &amp; overdraft maps · <strong>Close view</strong> for GSP-by-GSP metrics · <strong>Takeaways</strong> for synthesis · <strong>Analysis</strong> for GSP-level plots · <strong>Variable relationships</strong> for before/after comparisons.</p>
        <p class="intro-credit">Alexandra Beyret · ECON 30 · DWR InSAR · MNM wells · Land IQ · NASS · dry-well reporting</p>
      </footer>
    </div>
  </div>
  <div id="tab-explorer" class="tab-panel">
  <div id="map"></div>
  <div id="compare-container" aria-hidden="true">
    <div class="split-maps-row">
      <div class="split-pane">
        <div id="map-od"></div>
        <div class="split-label-block">
          <span class="split-label-title">Groundwater stress</span>
          <span class="split-label-sub" id="compare-od-label">Overdraft layer</span>
        </div>
      </div>
      <div class="split-divider"></div>
      <div class="split-pane">
        <div id="map-eq"></div>
        <div class="split-label-block">
          <span class="split-label-title">Equity lens</span>
          <span class="split-label-sub" id="compare-eq-label">Equity layer</span>
        </div>
      </div>
    </div>
  </div>
  <div id="split-container" aria-hidden="true">
    <div class="split-maps-row">
      <div class="split-pane">
        <div id="map-pre"></div>
        <div class="split-label-block">
          <span class="split-label-title" id="split-label-pre">Before SGMA</span>
          <span class="split-label-sub" id="split-sub-pre">Pre-2014 groundwater elevation</span>
        </div>
      </div>
      <div class="split-divider"></div>
      <div class="split-pane">
        <div id="map-post"></div>
        <div class="split-label-block">
          <span class="split-label-title" id="split-label-post">After SGMA</span>
          <span class="split-label-sub" id="split-sub-post">2024 groundwater elevation</span>
        </div>
      </div>
    </div>
    <div class="split-caption" id="split-caption"></div>
    <aside class="split-legend-panel" id="split-legend-panel">
      <h3 id="split-legend-title">Water table elevation (ft)</h3>
      <div class="subsidence-bar split-drop-bar" id="split-gwe-bar"></div>
      <div class="bar-labels split-bar-labels">
        <span id="split-legend-min">Lower (deeper)</span>
        <span id="split-legend-mid" class="split-legend-mid">same scale</span>
        <span id="split-legend-max">Higher (shallower)</span>
      </div>
      <p class="split-legend-note" id="split-legend-note"></p>
    </aside>
  </div>
  <div class="hud">
    <header>
      <p class="eyebrow">SJV Explorer · DWR InSAR + MNM</p>
      <h1>Sinking Valley</h1>
      <p class="lede">Track subsidence and the same GSP groundwater metrics as Close view — water table vs pre-SGMA average and 4-yr trend — plus fallowing and water access.</p>
    </header>
    <div class="split-toggle-bar" id="split-controls">
      <label><input type="checkbox" id="toggle-split"/> Before / after water table (2014 vs 2024)</label>
    </div>
    <div class="hud-body" id="hud-body">
      <div class="controls">
        <label for="year-slider">Year</label>
        <input type="range" id="year-slider" min="2012" max="2024" value="2024" step="1"/>
        <div class="year-row"><span id="year-label">2024</span><button id="play-btn" type="button">▶ Play</button></div>
      </div>
      <hr class="rule"/>
      <div class="controls">
        <label for="subsidence-select">Subsidence layer</label>
        <select id="subsidence-select">
          <option value="annual_rate">Annual rate (green=slow · red=fast)</option>
          <option value="cumulative">Cumulative since Jun 2015</option>
          <option value="none" selected>None</option>
        </select>
      </div>
      <hr class="rule"/>
      <div class="controls">
        <label for="overdraft-select">Overdraft layer</label>
        <select id="overdraft-select">
          <option value="cumulative">Water table vs pre-SGMA average</option>
          <option value="annual">Water table trend (4-yr, ft/yr)</option>
          <option value="none" selected>None</option>
        </select>
        <p class="note" id="overdraft-desc"></p>
      </div>
      <hr class="rule"/>
      <div class="controls">
        <label for="effectiveness-select">Equity lens</label>
        <select id="effectiveness-select">
          <option value="none" selected>None</option>
          <option value="gsp_status">GSP status</option>
          <option value="fallowed_land">Fallowed land</option>
          <option value="water_access">Water access (cumulative)</option>
          <option value="farm_consolidation">Farm consolidation (NASS)</option>
          <option value="ag_production">Cropland acres (Land IQ)</option>
        </select>
        <p class="note" id="lens-desc"></p>
      </div>
      <hr class="rule"/>
      <div class="controls">
        <label for="compare-select">Equity ↔ GWE view</label>
        <select id="compare-select">
          <option value="side_by_side" selected>Side-by-side maps</option>
          <option value="scatter">Scatter plot</option>
          <option value="overlay">Stacked overlay</option>
        </select>
        <p class="note" id="compare-desc"></p>
      </div>
      <hr class="rule"/>
      <div class="toggles">
        <label><input type="checkbox" id="toggle-counties" checked/> County borders</label>
        <label><input type="checkbox" id="toggle-well-dots"/> Show dry-well points</label>
      </div>
      <hr class="rule"/>
      <p class="note" id="baseline-note"></p>
      <p class="note" id="well-stats"></p>
      <p class="credit">Subsidence: DWR TRE Altamira SAR · GWE: DWR MNM · Dry wells: DWR reporting (bias-adjusted).</p>
    </div>
  </div>
  <aside class="var-why-panel" id="var-why-panel" aria-label="Variable guide">
    <header class="var-why-head">
      <div class="var-why-head-row">
        <h2 class="var-why-title">Variables</h2>
        <button type="button" class="var-why-close" id="var-why-close" aria-label="Hide variable guide">×</button>
      </div>
      <p class="var-why-lede">Click a layer or tab to see what it measures and why it matters for SGMA.</p>
    </header>
    <nav class="var-why-tabs" id="var-why-tabs" role="tablist" aria-label="Explorer variables"></nav>
    <div class="var-why-body" id="var-why-body">
      <h3 class="var-why-name" id="var-why-name"></h3>
      <div class="var-why-block">
        <h4>What it is</h4>
        <p id="var-why-def"></p>
      </div>
      <div class="var-why-block">
        <h4>Why map it</h4>
        <p id="var-why-why"></p>
      </div>
      <p class="var-why-source" id="var-why-source"></p>
    </div>
  </aside>
  <button type="button" class="var-why-open" id="var-why-open" aria-label="Show variable guide" hidden>Variables</button>
  <aside class="legend-panel" id="legend-panel">
    <div id="subsidence-legend">
      <h3 id="subsidence-legend-title">Subsidence rate</h3>
      <div class="subsidence-bar" id="subsidence-bar"></div>
      <div class="bar-labels"><span id="legend-min">Slower</span><span id="legend-max">Faster</span></div>
    </div>
    <div id="overdraft-legend">
      <h3 id="overdraft-legend-title">Water table vs pre-SGMA average (ft)</h3>
      <div class="subsidence-bar overdraft-bar" id="overdraft-bar"></div>
      <div class="bar-labels"><span id="overdraft-legend-min">Above baseline</span><span id="overdraft-legend-max">Below baseline</span></div>
    </div>
    <div id="scatter-panel">
      <h3 id="scatter-title">Equity vs groundwater stress</h3>
      <canvas id="scatter-canvas" width="260" height="180"></canvas>
      <div class="bar-labels"><span id="scatter-x-label">Equity metric</span><span id="scatter-y-label">GWE stress</span></div>
    </div>
    <div id="effectiveness-legend">
      <h3 id="effectiveness-legend-title">GSP status</h3>
      <div id="effectiveness-legend-body"></div>
    </div>
  </aside>
  </div>
  <div id="tab-close" class="tab-panel">
    <div class="panel-page">
      <header class="panel-header">
        <h1>Close view</h1>
        <p class="lede">All San Joaquin Valley GSPs — same groundwater metrics as the Explorer overdraft layers (baseline + 4-yr trend), compared 2016 → 2024.</p>
      </header>
      <div class="panel-controls">
        <label for="close-gsp-select">GSP</label>
        <select id="close-gsp-select"></select>
        <label class="inline-check"><input type="checkbox" id="close-approved-only"/> Approved plans only</label>
      </div>
      <div id="close-gsp-meta" class="close-meta"></div>
      <div id="close-determination" class="close-determination-block">
        <h4>Determination: <span id="close-verdict" class="verdict-text">—</span></h4>
        <div class="close-assessment" id="close-assessment"></div>
      </div>
      <h3 class="close-metrics-heading">2016 → 2024 metrics</h3>
      <div id="close-metrics" class="metric-grid"></div>
      <p class="note panel-note">Subsidence is valley-wide InSAR on the Explorer tab (not aggregated per GSP here). Water table vs baseline: positive = below pre-SGMA average (deeper); negative = above (higher). Green deltas mean improvement 2016→2024, not necessarily “good” today. Dry-well counts reflect expanded DWR reporting since ~2020.</p>
    </div>
  </div>
  <div id="tab-relationships" class="tab-panel">
    <div class="panel-page panel-wide">
      <header class="panel-header">
        <h1>Variable relationships</h1>
        <p class="lede">Each dot is one GSP. Left chart: 2016 (horizontal) vs 2024 (vertical). Right chart: change during SGMA years. For water table, negative values = above the pre-SGMA average (higher). Descriptive only — not proof of causation.</p>
      </header>
      <div class="panel-controls rel-controls">
        <label for="rel-variable-select">Variable</label>
        <select id="rel-variable-select"></select>
        <label for="rel-filter-select">Show</label>
        <select id="rel-filter-select">
          <option value="all">All GSPs</option>
          <option value="approved">Approved only</option>
          <option value="regulated">Approved + under review</option>
        </select>
      </div>
      <p class="note" id="rel-variable-note"></p>
      <p class="note rel-caveat hidden" id="rel-caveat"></p>
      <p class="note rel-gwe-note hidden" id="rel-gwe-note"></p>
      <div id="rel-tooltip" class="rel-tooltip hidden"></div>
      <div class="rel-charts">
        <div class="rel-chart-box">
          <h3 id="rel-paired-title">2016 vs 2024 (paired)</h3>
          <p class="chart-hint" id="rel-paired-hint"></p>
          <canvas id="rel-paired-canvas" width="480" height="360"></canvas>
          <div class="rel-axis-legend"><span id="rel-good-label"></span><span id="rel-bad-label"></span></div>
        </div>
        <div class="rel-chart-box">
          <h3 id="rel-delta-title">SGMA-era change (2024 − 2016)</h3>
          <p class="chart-hint">Bar height = change during SGMA years. Teal = approved · Amber = under review · Red = inadequate · Maroon = state intervention.</p>
          <canvas id="rel-delta-canvas" width="480" height="360"></canvas>
        </div>
      </div>
      <div id="rel-summary" class="rel-summary"></div>
    </div>
  </div>
  <div id="tab-takeaways" class="tab-panel">
    <div class="intro-page takeaways-page">
      <header class="panel-header takeaways-header">
        <h1>Takeaways</h1>
        <p class="lede" id="takeaways-lede"></p>
        <div class="takeaways-headline" id="takeaways-headline" hidden></div>
        <div class="takeaways-sgma-answer" id="takeaways-sgma-answer" hidden></div>
      </header>
      <div id="takeaways-sections" class="takeaways-sections"></div>
      <footer class="intro-footer">
        <p>Explore the data: <a href="#" class="intro-cta inline-cta" data-goto-tab="close">Close view</a> for GSP metrics · <a href="#" class="intro-cta inline-cta" data-goto-tab="explorer">Explorer</a> for subsidence maps · <a href="#" class="intro-cta inline-cta" data-goto-tab="relationships">Variable relationships</a> for 2016→2024 comparisons.</p>
      </footer>
    </div>
  </div>
  <div id="tab-analysis" class="tab-panel">
    <div class="intro-page analysis-page">
      <header class="panel-header">
        <h1>Statistical analysis</h1>
        <p class="lede" id="analysis-lede"></p>
        <p class="analysis-caveat" id="analysis-caveat"></p>
      </header>
      <div id="analysis-highlights" class="analysis-highlights"></div>
      <div id="analysis-figures" class="analysis-figures"></div>
      <footer class="intro-footer">
        <p>See raw metrics: <a href="#" class="intro-cta inline-cta" data-goto-tab="close">Close view</a> · narrative synthesis: <a href="#" class="intro-cta inline-cta" data-goto-tab="takeaways">Takeaways</a> · pairwise charts: <a href="#" class="intro-cta inline-cta" data-goto-tab="relationships">Variable relationships</a>.</p>
      </footer>
    </div>
  </div>
  <div id="tab-sources" class="tab-panel">
    <div class="intro-page sources-page">
      <header class="panel-header">
        <h1>Sources</h1>
        <p class="lede" id="sources-intro"></p>
      </header>
      <ul class="sources-list" id="sources-list"></ul>
      <p class="intro-credit">Alexandra Beyret · ECON 30 · May 2026</p>
    </div>
  </div>
  <script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
  <script src="sinking_valley_explorer.js"></script>
  <script src="sinking_valley_explorer_panels.js"></script>
</body>
</html>'''


def main() -> int:
    if not MANIFEST.is_file():
        print("Run build_sjv_subsidence.py first.", file=sys.stderr)
        return 1
    data = build_data()
    OUT_DATA.write_text(json.dumps(data, allow_nan=False), encoding="utf-8")
    html = render_html()
    OUT_HTML.write_text(html, encoding="utf-8")
    OUT_INDEX.write_text(html, encoding="utf-8")
    n_gwe = sum(
        1 for f in data["gsps"]["features"]
        if f["properties"].get("year_values", {}).get("2024", {}).get("gwe_cumulative_drop") is not None
    )
    print(f"Wrote {OUT_HTML}, {OUT_INDEX}, and {OUT_DATA}")
    print(f"  dry wells: {len(data['dry_wells'])} | GSPs w/ overdraft: {n_gwe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
