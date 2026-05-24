"""Aggregate SJV data for client-grade SGMA equity briefing (sgma_research + atlas)."""
from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
RESEARCH_CSV = ROOT / "data" / "processed" / "csv"
RESEARCH_GEO = ROOT / "data" / "processed" / "geoparquet"
RESEARCH_META = ROOT / "data" / "processed" / "metadata"
ATLAS_JS = ROOT / "vercel_site" / "atlas_data.js"
COUNTIES_GEO = ROOT / "vercel_site" / "thesis_counties.geojson"
REG_CSV = ROOT / "data" / "clean" / "sjv_regressions_results.csv"

COUNTY_NORM = {
    "fresno": "Fresno",
    "kern": "Kern",
    "kings": "Kings",
    "madera": "Madera",
    "merced": "Merced",
    "san_joaquin": "San Joaquin",
    "stanislaus": "Stanislaus",
    "tulare": "Tulare",
}
SJV_COUNTY_NORMS = frozenset(COUNTY_NORM)

STATUS_STD_LABELS = {
    "approved": "Approved",
    "under_review": "Under review",
    "state_intervention": "State intervention",
    "inadequate": "Inadequate (approved)",
    "inadequate_under_review": "Inadequate · under review",
    "incomplete": "Incomplete",
    "other": "Other",
}
STATUS_STD_COLORS = {
    "approved": "#15803d",
    "under_review": "#ca8a04",
    "state_intervention": "#b91c1c",
    "inadequate": "#2563eb",
    "inadequate_under_review": "#7c3aed",
    "incomplete": "#64748b",
    "other": "#94a3b8",
}


def load_atlas() -> dict:
    text = ATLAS_JS.read_text(encoding="utf-8")
    m = re.search(r"const ATLAS = (\{.*\});", text, re.DOTALL)
    if not m:
        raise ValueError("Could not parse ATLAS from atlas_data.js")
    return json.loads(m.group(1))


def _read_csv(name: str) -> pd.DataFrame | None:
    path = RESEARCH_CSV / name
    return pd.read_csv(path) if path.is_file() else None


def load_gsp_research() -> pd.DataFrame:
    df = _read_csv("gsp_determination_status.csv")
    if df is None:
        return pd.DataFrame()
    df["gsp_id"] = df["gsp_id"].astype(str)
    return df


def load_fallowing_research() -> pd.DataFrame:
    df = _read_csv("fallowing_panel.csv")
    if df is None:
        return pd.DataFrame()
    df["entity_id"] = df["entity_id"].astype(str)
    return df


def load_panel_research() -> pd.DataFrame:
    df = _read_csv("sjv_sgma_panel.csv")
    if df is None:
        return pd.DataFrame()
    df["gsp_id"] = df["gsp_id"].astype(str)
    df["gsa_id"] = df["gsa_id"].astype(str)
    return df


def norm_county(name: str) -> str:
    if not isinstance(name, str):
        return ""
    s = name.lower().replace(" county", "").replace(" ", "_").strip()
    return s


def load_dry_wells_research() -> pd.DataFrame:
    """County-year dry well panel from sgma_research CSV or raw source."""
    df = _read_csv("dry_wells_county_year_normalized.csv")
    if df is not None and "_county_norm" in df.columns:
        return df

    # Recompute from raw if export lost county column
    candidates = sorted(ROOT.glob("householdwatersupplyshortagereportingsystemdata*.csv"))
    if not candidates:
        return pd.DataFrame()
    raw = pd.read_csv(candidates[0], low_memory=False)
    raw["_county_norm"] = raw["County"].map(norm_county)
    raw = raw.loc[raw["_county_norm"].isin(SJV_COUNTY_NORMS)].copy()
    raw["_issue_date"] = pd.to_datetime(raw.get("Approximate Issue Start Date"), errors="coerce")
    raw["_year"] = raw["_issue_date"].dt.year
    raw["_is_dry_well"] = raw.get("Shortage Type", pd.Series(dtype=str)).astype(str).str.contains(
        "dry well", case=False, na=False
    )
    dry = raw.loc[raw["_is_dry_well"]].dropna(subset=["_year"])
    cy = dry.groupby(["_county_norm", "_year"], as_index=False).size().rename(columns={"size": "raw_dry_well_count"})
    cy["_year"] = cy["_year"].astype(int)
    cy["post_reporting_system"] = (cy["_year"] >= 2014).astype(int)
    return cy


def dry_well_summary(cy: pd.DataFrame) -> dict:
    if cy.empty:
        return {}
    pre = cy.loc[(cy["_year"] >= 2012) & (cy["_year"] <= 2014)]
    post = cy.loc[(cy["_year"] >= 2018) & (cy["_year"] <= 2022)]
    pre_sum = int(pre["raw_dry_well_count"].sum())
    post_sum = int(post["raw_dry_well_count"].sum())
    by_county = []
    for cn, label in COUNTY_NORM.items():
        sub = cy.loc[cy["_county_norm"] == cn]
        p = int(sub.loc[(sub["_year"] >= 2012) & (sub["_year"] <= 2014), "raw_dry_well_count"].sum())
        q = int(sub.loc[(sub["_year"] >= 2018) & (sub["_year"] <= 2022), "raw_dry_well_count"].sum())
        rate_col = "dry_wells_per_100k" if "dry_wells_per_100k" in sub.columns else None
        post_rate = None
        if rate_col:
            post_rate = sub.loc[(sub["_year"] >= 2018) & (sub["_year"] <= 2022), rate_col].mean()
        by_county.append(
            {
                "county": label,
                "well_pre": p,
                "well_post": q,
                "well_ratio": round(q / max(1, p), 1),
                "post_rate_per_100k": round(float(post_rate), 2) if post_rate and not np.isnan(post_rate) else None,
            }
        )
    ts = (
        cy.groupby("_year", as_index=False)["raw_dry_well_count"]
        .sum()
        .rename(columns={"raw_dry_well_count": "valley_total"})
        .sort_values("_year")
    )
    return {
        "well_pre": pre_sum,
        "well_post": post_sum,
        "well_ratio": round(post_sum / max(1, pre_sum), 1),
        "by_county": by_county,
        "timeseries": [{"year": int(r["_year"]), "total": int(r["valley_total"])} for _, r in ts.iterrows()],
    }


@dataclass
class OLSResult:
    n: int
    coef: np.ndarray
    se_hc1: np.ndarray
    t: np.ndarray
    r2: float


def ols_hc1(X: np.ndarray, y: np.ndarray) -> OLSResult:
    n, k = X.shape
    XtX_inv = np.linalg.inv(X.T @ X)
    beta = XtX_inv @ (X.T @ y)
    resid = y - X @ beta
    ybar = y.mean()
    sst = float(((y - ybar) ** 2).sum())
    ssr = float((resid**2).sum())
    r2 = 1.0 - (ssr / sst) if sst > 0 else float("nan")
    meat = X.T @ (X * (resid[:, None] ** 2))
    scale = n / (n - k) if n > k else 1.0
    V = scale * (XtX_inv @ meat @ XtX_inv)
    se = np.sqrt(np.diag(V))
    return OLSResult(n=n, coef=beta, se_hc1=se, t=beta / se, r2=r2)


def run_ols(df_rows: list[dict], y_key: str, x_keys: list[str], model_id: str, level: str = "gsp") -> dict | None:
    rows = [r for r in df_rows if all(r.get(k) is not None for k in [y_key, *x_keys])]
    if len(rows) < max(5, len(x_keys) + 3):
        return None
    y = np.array([r[y_key] for r in rows], dtype=float)
    X = np.column_stack([np.ones(len(rows)), *[np.array([r[x] for r in rows], dtype=float) for x in x_keys]])
    res = ols_hc1(X, y)
    terms = ["Intercept", *x_keys]
    coefs = [
        {"term": t, "coef": float(res.coef[i]), "se_hc1": float(res.se_hc1[i]), "t_hc1": float(res.t[i])}
        for i, t in enumerate(terms)
    ]
    return {
        "model": model_id,
        "level": level,
        "y": y_key,
        "x": " + ".join(x_keys),
        "n": res.n,
        "r2": round(float(res.r2), 4),
        "coefs": coefs,
        "coef_x": float(res.coef[1]) if len(x_keys) == 1 else None,
    }


def county_records(atlas: dict, fallow_df: pd.DataFrame, dry_by_county: list[dict]) -> list[dict]:
    dry_map = {d["county"]: d for d in dry_by_county}
    fallow_county = fallow_df.loc[fallow_df["entity_type"] == "county"].copy() if not fallow_df.empty else pd.DataFrame()
    rows = []
    for feat in atlas["counties"]["features"]:
        p = feat["properties"]
        pre, post, d = p.get("pre", {}), p.get("post", {}), p.get("delta", {})
        name = p["name"]
        fips = str(p.get("fips5", ""))[-4:]
        fpre = fpost = ff_delta = None
        if not fallow_county.empty:
            fc = fallow_county.loc[fallow_county["entity_id"] == fips]
            pre_rows = fc.loc[fc["year"].isin([2012, 2014])]
            post_rows = fc.loc[fc["year"].isin([2022])]
            if not pre_rows.empty:
                fpre = float(pre_rows["fallow_acres"].mean())
            if not post_rows.empty:
                fpost = float(post_rows["fallow_acres"].mean())
            if fpre is not None and fpost is not None:
                ff_delta = int(fpost - fpre)
        dry = dry_map.get(name, {})
        rows.append(
            {
                "name": name,
                "fips5": p.get("fips5"),
                "well_pre": dry.get("well_pre", pre.get("well_failures_issue_start")),
                "well_post": dry.get("well_post", post.get("well_failures_issue_start")),
                "well_total": post.get("well_failures_total"),
                "well_delta": (dry.get("well_post") or 0) - (dry.get("well_pre") or 0),
                "well_ratio": dry.get("well_ratio"),
                "post_rate_per_100k": dry.get("post_rate_per_100k"),
                "gwe_pre": pre.get("gwe_ft"),
                "gwe_post": post.get("gwe_ft"),
                "gwe_delta": round(d.get("gwe_ft", (post.get("gwe_ft") or 0) - (pre.get("gwe_ft") or 0)), 1),
                "fallow_pre": fpre if fpre is not None else pre.get("fallow_acres"),
                "fallow_post": fpost if fpost is not None else post.get("fallow_acres"),
                "fallow_delta": ff_delta if ff_delta is not None else int(d.get("fallow_acres") or 0),
                "inc_pre": pre.get("median_income"),
                "inc_post": post.get("median_income"),
                "inc_delta": d.get("median_income"),
                "small_pre": pre.get("small_farms"),
                "small_post": post.get("small_farms"),
                "small_loss": int(d.get("small_farms") or 0),
                "large_pre": pre.get("large_farms"),
                "large_post": post.get("large_farms"),
                "large_gain": int((post.get("large_farms") or 0) - (pre.get("large_farms") or 0)),
                "ces_score_pct": p.get("ces_score_pct"),
                "regulation": p.get("regulation"),
            }
        )
    return rows


def gsp_records(atlas: dict, gsp_research: pd.DataFrame, fallow_df: pd.DataFrame) -> list[dict]:
    research = {str(r["gsp_id"]): r for _, r in gsp_research.iterrows()} if not gsp_research.empty else {}
    fallow_gsp = fallow_df.loc[fallow_df["entity_type"] == "gsp"].copy() if not fallow_df.empty else pd.DataFrame()

    rows = []
    for feat in atlas.get("gsp", atlas.get("gsps", {})).get("features", []):
        p = feat["properties"]
        gid = str(p.get("GSP_ID") or p.get("Loc_GSP_ID") or "")
        rs = research.get(gid, {})
        status_std = rs.get("status_std") or "other"
        status_label = STATUS_STD_LABELS.get(status_std, status_std)
        approved = 1 if status_std == "approved" else 0

        fpre = fpost = ff_delta = fpct_pre = fpct_post = None
        if not fallow_gsp.empty:
            fg = fallow_gsp.loc[fallow_gsp["entity_id"] == gid]
            pre_rows = fg.loc[fg["year"].isin([2014, 2016])]
            post_rows = fg.loc[fg["year"].isin([2021, 2022])]
            if not pre_rows.empty:
                fpre = float(pre_rows["fallow_acres"].mean())
                fpct_pre = float(pre_rows["fallow_pct"].mean()) if pre_rows["fallow_pct"].notna().any() else None
            if not post_rows.empty:
                fpost = float(post_rows["fallow_acres"].mean())
                fpct_post = float(post_rows["fallow_pct"].mean()) if post_rows["fallow_pct"].notna().any() else None
            if fpre is not None and fpost is not None:
                ff_delta = fpost - fpre

        rows.append(
            {
                "gsp_id": gid,
                "name": p.get("Basin_Subbasin_Name") or p.get("GSP_Name") or f"GSP {gid}",
                "subbasin": p.get("Basin_Subbasin_Name") or "",
                "status": status_label,
                "status_std": status_std,
                "status_raw": rs.get("status_raw") or p.get("Status") or "",
                "approved": approved,
                "governance_quality_index": float(rs["governance_quality_index"]) if pd.notna(rs.get("governance_quality_index")) else None,
                "well_pre": p.get("well_pre_raw"),
                "well_post": p.get("well_post_raw"),
                "well_delta": p.get("well_delta_raw"),
                "well_pre_adj": p.get("well_pre_adj"),
                "well_post_adj": p.get("well_post_adj"),
                "well_delta_adj": p.get("well_delta_adj"),
                "gwe_pre": p.get("gwe_pre_mnm"),
                "gwe_post": p.get("gwe_post_mnm"),
                "gwe_delta": p.get("gwe_delta_mnm"),
                "fallow_pre": fpre if fpre is not None else p.get("fallow_pre_acres"),
                "fallow_post": fpost if fpost is not None else p.get("fallow_post_acres"),
                "fallow_delta": ff_delta if ff_delta is not None else p.get("fallow_delta_acres"),
                "fallow_pre_pct": fpct_pre if fpct_pre is not None else p.get("fallow_pre_pct"),
                "fallow_post_pct": fpct_post if fpct_post is not None else p.get("fallow_post_pct"),
            }
        )
    return rows


def panel_summary(panel: pd.DataFrame) -> dict:
    if panel.empty:
        return {}
    gsp_gov = panel.groupby("gsp_id", as_index=False).agg(
        governance_quality_index=("governance_quality_index", "first"),
        status_std=("status_std", "first"),
        subbasin_id=("subbasin_id", "first"),
    )
    approved = gsp_gov.loc[gsp_gov["status_std"] == "approved", "governance_quality_index"].mean()
    other = gsp_gov.loc[gsp_gov["status_std"] != "approved", "governance_quality_index"].mean()
    fallow_trend = (
        panel.loc[panel["fallow_pct"].notna()]
        .groupby("gsp_id")
        .apply(lambda g: g.loc[g["year"] == 2022, "fallow_pct"].mean() - g.loc[g["year"] == 2014, "fallow_pct"].mean())
        .dropna()
    )
    return {
        "n_rows": len(panel),
        "n_gsa": panel["gsa_id"].nunique(),
        "year_min": int(panel["year"].min()),
        "year_max": int(panel["year"].max()),
        "avg_gov_approved": round(float(approved), 3) if not np.isnan(approved) else None,
        "avg_gov_other": round(float(other), 3) if not np.isnan(other) else None,
        "median_fallow_pct_change": round(float(fallow_trend.median()), 2) if len(fallow_trend) else None,
    }


def extract_map_layers(atlas: dict, gsps: list[dict]) -> dict:
    gsp_lookup = {g["gsp_id"]: g for g in gsps}
    counties_geo = json.loads(COUNTIES_GEO.read_text(encoding="utf-8"))

    def feat_from_geom(geom, props: dict, crs) -> dict | None:
        if geom is None or getattr(geom, "is_empty", True):
            return None
        import geopandas as gpd

        geo = gpd.GeoSeries([geom], crs=crs).__geo_interface__
        return {"type": "Feature", "properties": props, "geometry": geo["features"][0]["geometry"]}

    try:
        import geopandas as gpd

        gsp_gdf = gpd.read_parquet(RESEARCH_GEO / "sjv_gsps.geoparquet")
        gsa_gdf = gpd.read_parquet(RESEARCH_GEO / "sjv_gsas.geoparquet")
        if gsp_gdf.crs and str(gsp_gdf.crs) != "EPSG:4326":
            gsp_gdf = gsp_gdf.to_crs(4326)
            gsa_gdf = gsa_gdf.to_crs(4326)

        id_col = "gsp_id" if "gsp_id" in gsp_gdf.columns else "GSP_ID"
        gsp_feats = []
        for _, row in gsp_gdf.iterrows():
            gid = str(row[id_col])
            g = gsp_lookup.get(gid, {})
            feat = feat_from_geom(
                row.geometry,
                {
                    "gsp_id": gid,
                    "name": (g.get("name") or gid)[:48],
                    "status_std": g.get("status_std") or "other",
                    "status": g.get("status") or "",
                    "governance_quality_index": g.get("governance_quality_index"),
                    "well_delta": g.get("well_delta"),
                    "gwe_delta": g.get("gwe_delta"),
                    "fallow_delta": g.get("fallow_delta"),
                },
                gsp_gdf.crs,
            )
            if feat:
                gsp_feats.append(feat)

        gsa_id_col = "gsa_id" if "gsa_id" in gsa_gdf.columns else "GSA_ID"
        gsa_feats = []
        for _, row in gsa_gdf.iterrows():
            feat = feat_from_geom(
                row.geometry,
                {"gsa_id": str(row[gsa_id_col]), "subbasin_id": str(row.get("subbasin_id", ""))},
                gsa_gdf.crs,
            )
            if feat:
                gsa_feats.append(feat)
        return {
            "counties": counties_geo,
            "gsps": {"type": "FeatureCollection", "features": gsp_feats},
            "gsas": {"type": "FeatureCollection", "features": gsa_feats},
            "source": "sgma_research_geoparquet",
        }
    except Exception:
        pass

    # Fallback: atlas embedded geometry
    gsp_feats = []
    for feat in atlas.get("gsp", atlas.get("gsps", {})).get("features", []):
        p = feat["properties"]
        gid = str(p.get("GSP_ID") or p.get("Loc_GSP_ID") or "")
        g = gsp_lookup.get(gid, {})
        gsp_feats.append(
            {
                "type": "Feature",
                "properties": {
                    "gsp_id": gid,
                    "name": g.get("name") or gid,
                    "status_std": g.get("status_std") or "other",
                    "status": g.get("status") or "",
                    "governance_quality_index": g.get("governance_quality_index"),
                    "well_delta": g.get("well_delta"),
                    "gwe_delta": g.get("gwe_delta"),
                    "fallow_delta": g.get("fallow_delta"),
                },
                "geometry": feat.get("geometry"),
            }
        )
    gsa_feats = []
    for feat in atlas.get("gsa", atlas.get("gsas", {})).get("features", []):
        p = feat["properties"]
        gsa_feats.append(
            {
                "type": "Feature",
                "properties": {"gsa_id": p.get("GSA_ID"), "gsp_id": str(p.get("GSP_ID") or "")},
                "geometry": feat.get("geometry"),
            }
        )
    return {
        "counties": counties_geo,
        "gsps": {"type": "FeatureCollection", "features": gsp_feats},
        "gsas": {"type": "FeatureCollection", "features": gsa_feats},
        "source": "atlas_fallback",
    }


def load_county_regressions() -> list[dict]:
    if not REG_CSV.is_file():
        return []
    rows = list(csv.DictReader(REG_CSV.open(encoding="utf-8")))
    seen = set()
    out = []
    for r in rows:
        if r["model"] in seen:
            continue
        seen.add(r["model"])
        model_rows = [x for x in rows if x["model"] == r["model"]]
        xrow = next(x for x in model_rows if x["term"] != "Intercept")
        out.append(
            {
                "model": r["model"],
                "level": "county",
                "y": r["y"],
                "x": r["x"],
                "n": int(r["n"]),
                "r2": round(float(r["r2"]), 4),
                "coef_x": float(xrow["coef"]),
                "se_x": float(xrow["se_hc1"]),
                "t_x": float(xrow["t_hc1"]),
            }
        )
    return out


def governance_stats(gsp_research: pd.DataFrame, panel: pd.DataFrame, atlas: dict) -> dict:
    if not gsp_research.empty:
        status_counts = Counter(gsp_research["status_std"].fillna("other"))
        complete = gsp_research.loc[gsp_research["is_complete"].astype(bool)] if "is_complete" in gsp_research.columns else pd.DataFrame()
        n_complete = len(complete)
    else:
        status_counts = Counter()
        n_complete = 0
    n_gsp = len(gsp_research) if not gsp_research.empty else 45
    n_total = max(1, sum(status_counts.values()) or n_gsp)
    cross = _read_csv("gsa_subbasin_crosswalk.csv")
    n_sub = cross["subbasin_id"].nunique() if cross is not None else 18
    return {
        "n_counties": 8,
        "n_subbasins": int(n_sub),
        "n_gsp": n_gsp,
        "n_gsa": int(panel["gsa_id"].nunique()) if not panel.empty else 132,
        "gsp_status_std": dict(status_counts),
        "gsp_status": {STATUS_STD_LABELS.get(k, k): v for k, v in status_counts.items()},
        "gsp_complete": n_complete,
        "gsp_complete_pct": round(100 * n_complete / n_total, 1),
        "gsp_approved_pct": round(100 * status_counts.get("approved", 0) / n_total, 1),
        "intervention": status_counts.get("state_intervention", 0),
        "panel_rows": len(panel) if not panel.empty else 0,
    }


def subbasin_records(atlas: dict) -> list[dict]:
    out = []
    for sb in atlas.get("subbasins", []):
        out.append(
            {
                "name": sb.get("subbasin_name", "").replace("SAN JOAQUIN VALLEY - ", ""),
                "n_gsp": sb.get("n_gsp"),
                "gwe_delta": round(sb.get("gwe_delta_mnm") or 0, 1),
                "well_delta_adj": round(sb.get("well_delta_adj") or 0, 3),
                "fallow_delta": int(sb.get("fallow_delta_acres") or 0),
            }
        )
    return sorted(out, key=lambda x: -(x.get("well_delta_adj") or 0))


def valley_summary(counties: list[dict], gsps: list[dict], dry: dict, panel_sum: dict) -> dict:
    pre_w = dry.get("well_pre") or sum(c["well_pre"] or 0 for c in counties)
    post_w = dry.get("well_post") or sum(c["well_post"] or 0 for c in counties)
    small_loss = sum(abs(c["small_loss"] or 0) for c in counties)
    approved = [g for g in gsps if g.get("status_std") in ("approved", "inadequate", "inadequate_under_review")]
    not_approved = [g for g in gsps if g.get("status_std") not in ("approved", "inadequate", "inadequate_under_review")]
    return {
        "well_pre": pre_w,
        "well_post": post_w,
        "well_ratio": dry.get("well_ratio") or round(post_w / max(1, pre_w), 1),
        "well_total_reports": sum(c["well_total"] or 0 for c in counties),
        "small_loss": small_loss,
        "gw_decline_counties": sum(1 for c in counties if (c["gwe_delta"] or 0) < 0),
        "large_farm_gain": sum(c["large_gain"] or 0 for c in counties),
        "gsp_well_increase": sum(1 for g in gsps if (g.get("well_delta") or 0) > 0),
        "gsp_gwe_decline": sum(1 for g in gsps if g.get("gwe_delta") is not None and g["gwe_delta"] < 0),
        "gsp_total": len(gsps),
        "gsp_approved": len(approved),
        "gsp_complete_pct": None,  # filled below
        "avg_gov_approved": panel_sum.get("avg_gov_approved"),
        "avg_gov_other": panel_sum.get("avg_gov_other"),
        "median_fallow_pct_change": panel_sum.get("median_fallow_pct_change"),
    }


def top_gsp_lists(gsps: list[dict]) -> dict:
    by_well = sorted([g for g in gsps if g.get("well_delta") is not None], key=lambda x: -(x["well_delta"] or 0))[:5]
    by_fallow = sorted([g for g in gsps if g.get("fallow_delta") is not None], key=lambda x: abs(x["fallow_delta"] or 0), reverse=True)[:5]
    by_gov = sorted([g for g in gsps if g.get("governance_quality_index") is not None], key=lambda x: x["governance_quality_index"])[:5]
    return {
        "well_harm": [{"name": g["name"][:42], "status": g["status"], "well_delta": g["well_delta"], "gsp_id": g["gsp_id"]} for g in by_well],
        "fallow_shift": [{"name": g["name"][:42], "fallow_delta": int(g["fallow_delta"] or 0), "gsp_id": g["gsp_id"]} for g in by_fallow],
        "low_governance": [{"name": g["name"][:42], "governance_quality_index": g["governance_quality_index"], "status": g["status"]} for g in by_gov],
    }


def load_qa_summary() -> dict:
    path = RESEARCH_META / "qa_report.json"
    if not path.is_file():
        return {}
    qa = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for block in qa:
        if block.get("type") == "panel":
            out["panel_rows"] = block.get("n_rows")
            out["panel_gsa"] = block.get("n_gsa")
        if block.get("type") == "topology" and block.get("layer") == "gsas":
            out["gsa_count"] = block.get("n_features")
    return out


def build_briefing_data() -> dict:
    """Lightweight chart payload — no embedded geometry (maps use atlas_data.js)."""
    atlas = load_atlas()
    gsp_research = load_gsp_research()
    fallow_df = load_fallowing_research()
    panel = load_panel_research()
    dry_cy = load_dry_wells_research()
    dry = dry_well_summary(dry_cy)
    panel_sum = panel_summary(panel)

    counties = county_records(atlas, fallow_df, dry.get("by_county", []))
    gsps = gsp_records(atlas, gsp_research, fallow_df)
    gov = governance_stats(gsp_research, panel, atlas)
    summary = valley_summary(counties, gsps, dry, panel_sum)
    summary["gsp_complete_pct"] = gov.get("gsp_complete_pct")

    gsp_status_map = {g["gsp_id"]: g.get("status_std", "other") for g in gsps}

    county_regs = load_county_regressions()
    gsp_regs = [
        run_ols(gsps, "fallow_delta", ["governance_quality_index"], "r1_gsp_fallow_governance", "gsp"),
        run_ols([c for c in counties if c.get("gwe_delta") is not None], "fallow_delta", ["gwe_delta"], "r2_county_fallow_gwe", "county"),
    ]
    gsp_regs = [r for r in gsp_regs if r]

    return {
        "meta": {
            "title": "Is SGMA reducing overdraft consequences equitably?",
            "author": "Alexandra Beyret",
            "course": "ECON 30 · May 2026",
            "duration_min": 8,
            "n_slides": 7,
        },
        "summary": summary,
        "governance": {
            "n_gsa": gov["n_gsa"],
            "n_gsp": gov["n_gsp"],
            "n_subbasins": gov["n_subbasins"],
            "gsp_complete_pct": gov["gsp_complete_pct"],
            "intervention": gov["intervention"],
            "gsp_status_std": gov["gsp_status_std"],
        },
        "counties": [
            {
                "name": c["name"],
                "well_pre": c["well_pre"],
                "well_post": c["well_post"],
                "gwe_delta": c["gwe_delta"],
                "fallow_delta": c["fallow_delta"],
                "small_loss": abs(c["small_loss"] or 0),
                "large_gain": c["large_gain"],
            }
            for c in counties
        ],
        "dry_wells": {
            "timeseries": dry.get("timeseries", []),
            "well_pre": dry.get("well_pre"),
            "well_post": dry.get("well_post"),
            "well_ratio": dry.get("well_ratio"),
        },
        "gsp_status_map": gsp_status_map,
        "status_colors": STATUS_STD_COLORS,
        "status_labels": STATUS_STD_LABELS,
        "regressions": {
            "county": [r for r in county_regs if r["model"] in ("m5_fallow_change_gw_change", "m3_small_loss_gw_change")],
            "gsp": gsp_regs,
        },
    }


def build_deck_data() -> dict:
    atlas = load_atlas()
    gsp_research = load_gsp_research()
    fallow_df = load_fallowing_research()
    panel = load_panel_research()
    dry_cy = load_dry_wells_research()
    dry = dry_well_summary(dry_cy)
    panel_sum = panel_summary(panel)

    counties = county_records(atlas, fallow_df, dry.get("by_county", []))
    gsps = gsp_records(atlas, gsp_research, fallow_df)
    maps = extract_map_layers(atlas, gsps)

    gsp_regs = [
        run_ols(gsps, "fallow_delta", ["governance_quality_index"], "r1_gsp_fallow_governance"),
        run_ols(gsps, "well_delta_adj", ["gwe_delta"], "r2_gsp_wells_gwe"),
        run_ols(gsps, "fallow_delta", ["gwe_delta"], "r3_gsp_fallow_gwe"),
        run_ols([g for g in gsps if g.get("fallow_post_pct") is not None], "fallow_post_pct", ["governance_quality_index"], "r4_gsp_fallowpct_gov"),
    ]
    gsp_regs = [r for r in gsp_regs if r]

    county_regs = load_county_regressions()
    summary = valley_summary(counties, gsps, dry, panel_sum)
    gov = governance_stats(gsp_research, panel, atlas)
    summary["gsp_complete_pct"] = gov.get("gsp_complete_pct")

    return {
        "meta": {
            "title": "Is SGMA reducing overdraft consequences equitably?",
            "subtitle": "San Joaquin Valley · sgma_research panel v1.0 · May 2026",
            "duration_min": 8,
            "n_slides": 14,
            "data_pipeline": "sgma_research",
        },
        "governance": gov,
        "summary": summary,
        "panel": panel_sum,
        "dry_wells": dry,
        "counties": counties,
        "gsps": gsps,
        "subbasins": subbasin_records(atlas),
        "top_gsp": top_gsp_lists(gsps),
        "regressions": {"county": county_regs, "gsp": gsp_regs},
        "maps": maps,
        "status_colors": STATUS_STD_COLORS,
        "status_labels": STATUS_STD_LABELS,
        "methods": {
            "spatial_units": "Bulletin 118 subbasins · 132 GSAs · 45 GSP plan areas (primary); counties for demographic context",
            "panel": "sgma_research GSA×year panel (2012–2024) · sjv_sgma_panel.csv",
            "wells": "DWR dry-well reports · county-year normalization · detrended index · post-2014 reporting bias flagged",
            "governance": "DWR GSP determination status → standardized status_std + governance_quality_index heuristic",
            "fallow": "USDA CDL (county) + DWR GSP land-use survey (GSP) · fallowing_panel.csv",
            "gwe": "CASGEM county means + DWR GSP Monitoring Network (MNM) at plan-area scale",
            "farms": "USDA NASS Census · small (&lt;180 ac) vs large (≥500 ac) operations",
            "qa": "Topology validation · overlap reporting · qa_report.json",
        },
        "sources": [
            "sgma_research pipeline · data/processed/",
            "CA DWR SGMA Portal · GSP status & plan areas",
            "CA DWR Household Water Supply Shortage Reporting System",
            "CA DWR GSP Monitoring Network (CASGEM / MNM)",
            "USDA CropScape CDL · USDA NASS QuickStats",
            "US Census ACS · CalEnviroScreen 4.0 (CA OEHHA)",
        ],
        "qa": load_qa_summary(),
        "atlas_page": "index.html",
    }


if __name__ == "__main__":
    data = build_deck_data()
    out = ROOT / "vercel_site" / "sgma_equity_deck_data.json"
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Wrote {out}")
    print(f"  pipeline: {data['meta']['data_pipeline']}")
    print(f"  {len(data['counties'])} counties · {len(data['gsps'])} GSPs · {data['governance']['n_gsa']} GSAs")
    print(f"  panel: {data['panel'].get('n_rows', 0)} rows · dry wells {data['summary']['well_pre']}→{data['summary']['well_post']}")
