"""
SGMA / SJV exploratory analysis: merge well-failure points (with CASGEM) with ACS,
CDL fallow acreage, and farm-operations metadata for county-level equity views.

Reads:
  data/clean/sjv_merged.geojson
  data/raw/socioeconomic/acs5_2014.csv, acs5_2021.csv
  data/raw/land_use/cdl_acreage_*.csv (all years present)
  data/raw/farm_size/farm_operations.json

Writes:
  data/clean/sjv_county_summary.csv
  data/clean/fallowing_chart.png (grouped bars: county × CDL year fallow)
  data/clean/farmsize_vs_fallow.png (Census mean farm size vs CDL fallow)
  data/clean/income_vs_wellfailures.png
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent

MERGED = ROOT / "data/clean/sjv_merged.geojson"
ACS_2014 = ROOT / "data/raw/socioeconomic/acs5_2014.csv"
ACS_2021 = ROOT / "data/raw/socioeconomic/acs5_2021.csv"
LAND_USE_DIR = ROOT / "data/raw/land_use"
FARM_JSON = ROOT / "data/raw/farm_size/farm_operations.json"

OUT_SUMMARY = ROOT / "data/clean/sjv_county_summary.csv"
OUT_FALLOW = ROOT / "data/clean/fallowing_chart.png"
OUT_FARM_FALLOW = ROOT / "data/clean/farmsize_vs_fallow.png"
OUT_SCATTER = ROOT / "data/clean/income_vs_wellfailures.png"

FALLOW_CATEGORY = "Fallow/Idle Cropland"

# Mutually exclusive AREA OPERATED bins in NASS Census (exclude roll-ups that double-count).
# Midpoints (acres) approximate within-bin averages for weighted mean farm size.
BIN_MIDPOINTS: dict[str, float] = {
    "AREA OPERATED: (1.0 TO 9.9 ACRES)": 5.0,
    "AREA OPERATED: (10.0 TO 49.9 ACRES)": 30.0,
    "AREA OPERATED: (50.0 TO 69.9 ACRES)": 60.0,
    "AREA OPERATED: (70.0 TO 99.9 ACRES)": 85.0,
    "AREA OPERATED: (100 TO 139 ACRES)": 119.5,
    "AREA OPERATED: (140 TO 179 ACRES)": 159.5,
    "AREA OPERATED: (180 TO 219 ACRES)": 199.5,
    "AREA OPERATED: (220 TO 259 ACRES)": 239.5,
    "AREA OPERATED: (260 TO 499 ACRES)": 379.5,
    "AREA OPERATED: (500 TO 999 ACRES)": 749.5,
    "AREA OPERATED: (1,000 TO 1,999 ACRES)": 1499.5,
    "AREA OPERATED: (2,000 OR MORE ACRES)": 3000.0,
}

# Map Census farm year -> nearest CDL year available for fallow comparison.
FARM_YEAR_TO_CDL_YEAR = {2012: 2012, 2017: 2018, 2022: 2022}


def safe_float(x: object) -> float | None:
    if pd.isna(x):
        return None
    s = str(x).strip().replace(",", "")
    if s in {"", ".", "-", "**", "(X)"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def load_acs(path: Path, income_col: str = "B19013_001E") -> pd.DataFrame:
    df = pd.read_csv(path)
    df["county_fips5"] = df["county_fips5"].astype(str).str.zfill(5)
    df["median_hh_income"] = df[income_col].apply(safe_float)
    return df[["county_fips5", "NAME", "median_hh_income"]]


def load_fallow_year(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df.loc[df["category"].astype(str).str.strip() == FALLOW_CATEGORY].copy()
    df["county_fips5"] = df["county_fips5"].astype(str).str.zfill(5)
    df["fallow_acres"] = df["acreage"].apply(safe_float)
    return df[["county_fips5", "fallow_acres"]]


def discover_cdl_acreage_files() -> list[tuple[int, Path]]:
    """Return sorted (cdl_year, path) for cdl_acreage_YYYY.csv under data/raw/land_use/."""
    if not LAND_USE_DIR.is_dir():
        raise FileNotFoundError(f"Missing land use directory: {LAND_USE_DIR}")
    out: list[tuple[int, Path]] = []
    for p in sorted(LAND_USE_DIR.glob("cdl_acreage_*.csv")):
        m = re.match(r"cdl_acreage_(\d{4})\.csv$", p.name, re.I)
        if m:
            out.append((int(m.group(1)), p))
    if not out:
        raise FileNotFoundError(f"No cdl_acreage_*.csv files in {LAND_USE_DIR}")
    return sorted(out, key=lambda x: x[0])


def load_fallow_long() -> pd.DataFrame:
    """Stack CDL fallow rows for all available years."""
    parts: list[pd.DataFrame] = []
    for year, path in discover_cdl_acreage_files():
        sub = load_fallow_year(path)
        sub["cdl_year"] = year
        parts.append(sub)
    return pd.concat(parts, ignore_index=True)


def weighted_mean_farm_acres_from_json(path: Path) -> pd.DataFrame:
    """
    Census farm operation counts by disjoint area-operated bins -> weighted mean acres
    per county and census year.
    """
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    # (county_fips5, farm_year) -> list of (midpoint_acres, operations)
    buckets: dict[tuple[str, int], list[tuple[float, float]]] = defaultdict(list)
    for r in data.get("records", []):
        if r.get("domain_desc") != "AREA OPERATED":
            continue
        cat = str(r.get("domaincat_desc", "")).strip()
        if cat not in BIN_MIDPOINTS:
            continue
        v = safe_float(r.get("Value"))
        if v is None or v <= 0:
            continue
        f5 = str(r.get("county_fips5", "")).zfill(5)
        yr = int(r["year"])
        buckets[(f5, yr)].append((BIN_MIDPOINTS[cat], v))

    rows: list[dict[str, float | int | str]] = []
    for (f5, yr), pairs in sorted(buckets.items()):
        ops = sum(p[1] for p in pairs)
        if ops <= 0:
            continue
        wmean = sum(p[0] * p[1] for p in pairs) / ops
        rows.append(
            {
                "county_fips5": f5,
                "farm_census_year": yr,
                "weighted_mean_acres": float(wmean),
                "total_operations": float(ops),
            }
        )
    return pd.DataFrame(rows)


def build_farm_fallow_scatter_df(fallow_long: pd.DataFrame) -> pd.DataFrame:
    """One row per county × farm census year, with matched CDL fallow year."""
    farm = weighted_mean_farm_acres_from_json(FARM_JSON)
    if farm.empty:
        return pd.DataFrame()

    fl = fallow_long.rename(columns={"fallow_acres": "fallow_acres_cdl"})
    rows: list[dict[str, object]] = []
    for _, fr in farm.iterrows():
        fy = int(fr["farm_census_year"])
        cdl_y = FARM_YEAR_TO_CDL_YEAR.get(fy)
        if cdl_y is None:
            continue
        sub = fl.loc[fl["cdl_year"] == cdl_y, ["county_fips5", "fallow_acres_cdl"]]
        m = sub.loc[sub["county_fips5"] == fr["county_fips5"]]
        if m.empty:
            continue
        fall = m["fallow_acres_cdl"].iloc[0]
        rows.append(
            {
                "county_fips5": fr["county_fips5"],
                "farm_census_year": fy,
                "cdl_year": cdl_y,
                "weighted_mean_farm_acres": fr["weighted_mean_acres"],
                "fallow_acres": fall,
                "total_farm_operations": fr["total_operations"],
            }
        )
    return pd.DataFrame(rows)


def county_short_name(acs_name: str) -> str:
    m = re.match(r"^([^,]+)", acs_name or "")
    return (m.group(1).replace(" County", "").strip()) if m else acs_name


def load_farm_operations(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    meta = data.get("meta", {})
    records = data.get("records", [])
    return {"meta": meta, "record_count": len(records)}


def fix_county_fips_mapping() -> dict[str, str]:
    """Build mapping from well CSV County field to FIPS."""
    acs = pd.read_csv(ACS_2021)
    m: dict[str, str] = {}
    for _, row in acs.iterrows():
        f5 = str(row["county_fips5"]).zfill(5)
        name = str(row["NAME"])
        base = county_short_name(name)
        m[base] = f5
        m[base.upper()] = f5
        m[base.lower()] = f5
    return m


def map_county_to_fips(raw: object, m: dict[str, str]) -> str | None:
    if pd.isna(raw):
        return None
    s = str(raw).strip()
    return m.get(s) or m.get(s.title()) or m.get(s.upper()) or m.get(s.lower())


def build_summary() -> pd.DataFrame:
    wells = gpd.read_file(MERGED)
    if wells.empty:
        raise ValueError(f"No features in {MERGED}")

    map_co = fix_county_fips_mapping()
    wells["county_fips5"] = wells["County"].apply(lambda x: map_county_to_fips(x, map_co))
    missing = wells["county_fips5"].isna()
    if missing.any():
        print(
            f"Warning: {int(missing.sum())} rows could not map County -> FIPS.",
            file=sys.stderr,
        )
        wells = wells.loc[~missing].copy()

    gwe_col = "casgem_mean_gwe_ft"
    if gwe_col not in wells.columns:
        raise ValueError(f"Expected column {gwe_col!r} in merged GeoJSON.")

    agg = (
        wells.groupby("county_fips5", as_index=False)
        .agg(
            well_failure_count=("geometry", "count"),
            mean_groundwater_elevation_ft=(
                gwe_col,
                lambda s: pd.to_numeric(s, errors="coerce").mean(),
            ),
        )
    )

    acs14 = load_acs(ACS_2014)
    acs21 = load_acs(ACS_2021)
    inc = acs14.merge(acs21, on="county_fips5", suffixes=("_2014", "_2021"))
    inc["median_income_change_2014_2021"] = (
        inc["median_hh_income_2021"] - inc["median_hh_income_2014"]
    )

    fall14 = load_fallow_year(LAND_USE_DIR / "cdl_acreage_2014.csv").rename(
        columns={"fallow_acres": "fallow_acres_2014"}
    )
    fall22 = load_fallow_year(LAND_USE_DIR / "cdl_acreage_2022.csv").rename(
        columns={"fallow_acres": "fallow_acres_2022"}
    )

    d = agg.merge(
        inc[
            [
                "county_fips5",
                "NAME_2021",
                "median_hh_income_2014",
                "median_hh_income_2021",
                "median_income_change_2014_2021",
            ]
        ],
        on="county_fips5",
        how="left",
    )
    d = d.rename(columns={"NAME_2021": "county_acs_name"})
    d = d.merge(fall14, on="county_fips5", how="left")
    d = d.merge(fall22, on="county_fips5", how="left")
    d["fallow_acreage_change_2014_2022"] = d["fallow_acres_2022"] - d["fallow_acres_2014"]
    d["county_short"] = d["county_acs_name"].map(county_short_name)
    d = d.sort_values("county_fips5").reset_index(drop=True)

    cols = [
        "county_fips5",
        "county_short",
        "well_failure_count",
        "mean_groundwater_elevation_ft",
        "median_hh_income_2014",
        "median_hh_income_2021",
        "median_income_change_2014_2021",
        "fallow_acres_2014",
        "fallow_acres_2022",
        "fallow_acreage_change_2014_2022",
    ]
    return d[cols]


def county_lookup_table() -> pd.DataFrame:
    acs = pd.read_csv(ACS_2021)
    acs["county_fips5"] = acs["county_fips5"].astype(str).str.zfill(5)
    acs["county_short"] = acs["NAME"].map(county_short_name)
    return acs[["county_fips5", "county_short"]]


def chart_fallow_all_years(fallow_long: pd.DataFrame, lookup: pd.DataFrame) -> None:
    """Grouped bar chart: counties on x-axis, one bar per CDL year (color = year)."""
    d = fallow_long.merge(lookup, on="county_fips5", how="left")
    d = d.dropna(subset=["county_short", "fallow_acres"])

    counties = sorted(d["county_short"].unique())
    years = sorted(d["cdl_year"].unique().astype(int))
    n_c, n_y = len(counties), len(years)
    if n_c == 0 or n_y == 0:
        print("Skipping fallow chart (no county or year data).", file=sys.stderr)
        return

    pivot = d.pivot_table(
        index="county_short",
        columns="cdl_year",
        values="fallow_acres",
        aggfunc="first",
    )
    pivot = pivot.reindex(counties)
    pivot.columns = [int(c) for c in pivot.columns]

    fig, ax = plt.subplots(figsize=(12, 6.5))
    x = np.arange(n_c)
    group_w = 0.82
    bar_w = group_w / n_y
    offsets = (np.arange(n_y) - (n_y - 1) / 2.0) * bar_w
    year_colors = plt.cm.viridis(np.linspace(0.15, 0.92, n_y))

    for j, year in enumerate(years):
        heights = []
        for county in counties:
            try:
                v = pivot.loc[county, year]
            except (KeyError, TypeError):
                v = np.nan
            heights.append(float(v) if pd.notna(v) else 0.0)
        ax.bar(
            x + offsets[j],
            heights,
            width=bar_w * 0.92,
            label=str(year),
            color=year_colors[j],
            edgecolor="0.25",
            linewidth=0.4,
        )

    ax.set_xticks(x)
    ax.set_xticklabels(counties, rotation=35, ha="right")
    ax.set_xlabel("County")
    ax.set_ylabel("Fallow / idle cropland (acres, CDL)")
    ax.set_title(
        "San Joaquin Valley — CDL fallow / idle cropland by county\n"
        "(one bar per CDL year)"
    )
    ax.legend(title="CDL year", loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    OUT_FALLOW.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_FALLOW, dpi=150, bbox_inches="tight")
    plt.close(fig)


def chart_farm_vs_fallow(panel: pd.DataFrame, lookup: pd.DataFrame) -> None:
    """
    Scatter: weighted mean Census farm size (acres) vs CDL fallow acres.
    Color = county; marker shape = farm census year.
    """
    from matplotlib.lines import Line2D

    if panel.empty:
        print("Skipping farm-size vs fallow chart (no merged panel data).", file=sys.stderr)
        return

    d = panel.merge(lookup, on="county_fips5", how="left")
    d = d.dropna(subset=["county_short", "weighted_mean_farm_acres", "fallow_acres"])
    if d.empty:
        print("Skipping farm-size vs fallow chart (empty after join).", file=sys.stderr)
        return

    county_order = sorted(d["county_short"].unique())
    cmap = {c: plt.cm.tab10(i % 10) for i, c in enumerate(county_order)}
    markers: dict[int, str] = {2012: "o", 2017: "s", 2022: "^"}

    fig, ax = plt.subplots(figsize=(9, 6))
    for _, row in d.iterrows():
        cy = int(row["farm_census_year"])
        mk = markers.get(cy, "o")
        ax.scatter(
            row["weighted_mean_farm_acres"],
            row["fallow_acres"],
            color=cmap[row["county_short"]],
            marker=mk,
            s=110,
            edgecolors="black",
            linewidths=0.55,
            zorder=3,
        )

    county_handles = [
        Line2D(
            [0],
            [0],
            marker="o",
            color=cmap[c],
            linestyle="",
            markersize=9,
            label=c,
            markeredgecolor="black",
        )
        for c in county_order
    ]
    year_handles = [
        Line2D(
            [0],
            [0],
            marker=markers[y],
            color="0.35",
            linestyle="",
            markersize=9,
            label=f"Census {y} (CDL {FARM_YEAR_TO_CDL_YEAR[y]})",
            markeredgecolor="black",
        )
        for y in sorted(markers)
        if y in set(d["farm_census_year"].astype(int))
    ]
    ax.legend(handles=county_handles + year_handles, loc="best", fontsize=8)

    ax.set_xlabel("Weighted mean farm size (acres operated, Census of Ag)")
    ax.set_ylabel("Fallow / idle cropland (acres, CDL)")
    ax.set_title(
        "Farm size vs. fallowing — SJV counties\n"
        "(Census 2012/2017/2022 matched to CDL 2012/2018/2022 fallow)"
    )
    ax.grid(alpha=0.25)
    fig.tight_layout()
    OUT_FARM_FALLOW.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_FARM_FALLOW, dpi=150, bbox_inches="tight")
    plt.close(fig)


def chart_income_vs_wells(df: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(8, 6))
    plot_df = df.dropna(subset=["median_hh_income_2021", "well_failure_count"])
    ax.scatter(
        plot_df["well_failure_count"],
        plot_df["median_hh_income_2021"],
        s=80,
        alpha=0.85,
        color="#55A868",
        edgecolors="black",
        linewidths=0.6,
    )
    for _, row in plot_df.iterrows():
        ax.annotate(
            row["county_short"],
            (row["well_failure_count"], row["median_hh_income_2021"]),
            textcoords="offset points",
            xytext=(6, 4),
            fontsize=9,
        )
    ax.set_xlabel("Household water shortage reports (count, SJV)")
    ax.set_ylabel("Median household income (ACS 2021 $)")
    ax.set_title("SJV counties — income vs. reported supply stress")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(OUT_SCATTER, dpi=150)
    plt.close(fig)


def main() -> int:
    farm = load_farm_operations(FARM_JSON)
    print(
        f"Loaded farm_operations.json: {farm['record_count']} records "
        f"(census years in meta: {farm['meta'].get('census_years', '?')})."
    )

    cdl_files = discover_cdl_acreage_files()
    print(f"CDL acreage files: {[f'{y} -> {p.name}' for y, p in cdl_files]}")

    lookup = county_lookup_table()
    fallow_long = load_fallow_long()

    df = build_summary()
    OUT_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_SUMMARY, index=False)

    display_cols = [
        "county_short",
        "well_failure_count",
        "mean_groundwater_elevation_ft",
        "median_income_change_2014_2021",
        "fallow_acreage_change_2014_2022",
    ]
    print("\n=== County summary (selected columns) ===\n")
    fmt = {
        "well_failure_count": lambda x: f"{int(x)}" if pd.notna(x) else "",
        "mean_groundwater_elevation_ft": lambda x: f"{x:,.2f}" if pd.notna(x) else "",
        "median_income_change_2014_2021": lambda x: f"{x:,.0f}" if pd.notna(x) else "",
        "fallow_acreage_change_2014_2022": lambda x: f"{x:,.1f}" if pd.notna(x) else "",
    }
    print(df[display_cols].to_string(index=False, formatters=fmt))

    chart_fallow_all_years(fallow_long, lookup)

    farm_fallow = build_farm_fallow_scatter_df(fallow_long)
    chart_farm_vs_fallow(farm_fallow, lookup)

    chart_income_vs_wells(df)

    print(f"\nWrote {OUT_SUMMARY}")
    print(f"Wrote {OUT_FALLOW}")
    print(f"Wrote {OUT_FARM_FALLOW}")
    print(f"Wrote {OUT_SCATTER}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FileNotFoundError as e:
        print(f"Missing file: {e}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        raise SystemExit(1)
