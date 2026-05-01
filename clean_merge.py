"""
Merge San Joaquin Valley GSA boundaries with household water shortage / well failure
reports, enriched with county-level CASGEM groundwater statistics (chunked).

Place inputs under data/raw/ (recommended):
  data/raw/gsa_boundaries/i03_Groundwater_Sustainability_Agencies.geojson
  data/raw/well_failures/*.csv  (household shortage reporting export)
  data/raw/groundwater/measurements.csv

The script falls back to the project root for the same filenames when raw paths
are missing, so existing layouts keep working.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parent

# Core San Joaquin Valley counties (California Water Board / SGMA basin footprint).
SJV_COUNTIES = frozenset(
    {
        "fresno",
        "kern",
        "kings",
        "madera",
        "merced",
        "san joaquin",
        "stanislaus",
        "tulare",
    }
)


def norm_county(name: object) -> str | None:
    if pd.isna(name):
        return None
    return str(name).strip().lower()


def ensure_dirs() -> None:
    for rel in (
        "data/raw/gsa_boundaries",
        "data/raw/well_failures",
        "data/raw/groundwater",
        "data/clean",
    ):
        (ROOT / rel).mkdir(parents=True, exist_ok=True)


def find_gsa_geojson() -> Path:
    candidates = [
        ROOT / "data/raw/gsa_boundaries/i03_Groundwater_Sustainability_Agencies.geojson",
        ROOT / "i03_Groundwater_Sustainability_Agencies.geojson",
    ]
    for p in candidates:
        if p.is_file():
            return p
    raise FileNotFoundError(
        "Could not find i03_Groundwater_Sustainability_Agencies.geojson "
        "in data/raw/gsa_boundaries/ or project root."
    )


def find_well_failure_csv() -> Path:
    raw_dir = ROOT / "data/raw/well_failures"
    if raw_dir.is_dir():
        csvs = sorted(raw_dir.glob("*.csv"))
        if csvs:
            return csvs[0]
    root_csvs = sorted(ROOT.glob("householdwatersupplyshortagereportingsystemdata*.csv"))
    if root_csvs:
        return root_csvs[0]
    raise FileNotFoundError(
        "Could not find household water shortage CSV. "
        "Place it in data/raw/well_failures/ or name it "
        "householdwatersupplyshortagereportingsystemdata*.csv in the project root."
    )


def find_measurements_csv() -> Path:
    candidates = [
        ROOT / "data/raw/groundwater/measurements.csv",
        ROOT / "measurements.csv",
    ]
    for p in candidates:
        if p.is_file():
            return p
    raise FileNotFoundError(
        "Could not find measurements.csv in data/raw/groundwater/ or project root."
    )


def load_gsa_sjv(gsa_path: Path) -> gpd.GeoDataFrame:
    gsa = gpd.read_file(gsa_path)
    if "Basin_Name" not in gsa.columns:
        raise ValueError("GSA GeoJSON missing expected column 'Basin_Name'.")
    basin = gsa["Basin_Name"].fillna("").astype(str).str.upper()
    mask = basin.str.contains("SAN JOAQUIN VALLEY", regex=False)
    out = gsa.loc[mask].copy()
    if out.empty:
        raise ValueError(
            "No GSAs left after filtering to Basin_Name containing 'SAN JOAQUIN VALLEY'."
        )
    if out.crs is None:
        out.set_crs(epsg=4326, inplace=True)
    else:
        out = out.to_crs(epsg=4326)
    return out


def load_well_failures_sjv(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, low_memory=False)
    required = {"LATITUDE", "LONGITUDE", "County"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Well failure CSV missing columns: {sorted(missing)}")
    df["_county_norm"] = df["County"].map(norm_county)
    df = df.loc[df["_county_norm"].isin(SJV_COUNTIES)].copy()

    lat = pd.to_numeric(df["LATITUDE"], errors="coerce")
    lon = pd.to_numeric(df["LONGITUDE"], errors="coerce")
    ok = lat.notna() & lon.notna()
    df = df.loc[ok].copy()
    df["_lat"] = lat.loc[df.index]
    df["_lon"] = lon.loc[df.index]
    return df


def wells_to_gdf(df: pd.DataFrame) -> gpd.GeoDataFrame:
    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["_lon"], df["_lat"]),
        crs="EPSG:4326",
    )
    return gdf


def prefix_gsa_columns(gsa: gpd.GeoDataFrame, prefix: str = "gsa_") -> gpd.GeoDataFrame:
    rename = {}
    for c in gsa.columns:
        if c == "geometry":
            continue
        rename[c] = f"{prefix}{c}"
    return gsa.rename(columns=rename)


def aggregate_gwe_by_county_chunked(
    measurements_path: Path,
    chunksize: int,
) -> pd.DataFrame:
    """
    Mean groundwater elevation (gwe, feet) and observation count per county,
    restricted to San Joaquin Valley counties. Single pass with chunked IO.
    """
    usecols = ["county_name", "gwe"]
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}

    reader = pd.read_csv(
        measurements_path,
        chunksize=chunksize,
        usecols=usecols,
        dtype={"county_name": "string"},
        engine="c",
    )
    for chunk in reader:
        chunk["gwe"] = pd.to_numeric(chunk["gwe"], errors="coerce")
        chunk = chunk.dropna(subset=["gwe"])
        cn = chunk["county_name"].map(norm_county)
        chunk = chunk.loc[cn.isin(SJV_COUNTIES)].copy()
        chunk["_cn"] = cn.loc[chunk.index]

        grp = chunk.groupby("_cn", sort=False)["gwe"]
        part_sums = grp.sum()
        part_counts = grp.count()
        for county, s in part_sums.items():
            sums[county] = sums.get(county, 0.0) + float(s)
        for county, n in part_counts.items():
            counts[county] = counts.get(county, 0) + int(n)

    rows = []
    for county in sorted(set(sums.keys()) | set(counts.keys())):
        n = counts.get(county, 0)
        if n == 0:
            continue
        rows.append(
            {
                "_county_norm": county,
                "casgem_mean_gwe_ft": sums[county] / n,
                "casgem_obs_count": n,
            }
        )
    return pd.DataFrame(rows)


def run(
    chunksize: int,
    output_path: Path | None = None,
) -> Path:
    ensure_dirs()

    out = output_path or (ROOT / "data/clean/sjv_merged.geojson")
    out.parent.mkdir(parents=True, exist_ok=True)

    gsa_path = find_gsa_geojson()
    well_csv = find_well_failure_csv()
    ms_path = find_measurements_csv()

    print(f"Loading GSAs: {gsa_path}")
    gsa = load_gsa_sjv(gsa_path)
    gsa = prefix_gsa_columns(gsa)

    print(f"Loading well failures: {well_csv}")
    wells_df = load_well_failures_sjv(well_csv)
    wells_gdf = wells_to_gdf(wells_df)

    print("Spatial join (well points within GSA polygons)...")
    joined = gpd.sjoin(
        wells_gdf,
        gsa,
        how="left",
        predicate="within",
    )
    if joined.index.duplicated().any():
        joined = joined[~joined.index.duplicated(keep="first")]

    print(f"Aggregating CASGEM measurements (chunksize={chunksize})...")
    casgem_stats = aggregate_gwe_by_county_chunked(ms_path, chunksize=chunksize)
    if not casgem_stats.empty:
        joined = joined.merge(
            casgem_stats,
            on="_county_norm",
            how="left",
        )
    else:
        joined["casgem_mean_gwe_ft"] = pd.NA
        joined["casgem_obs_count"] = pd.NA

    drop_internal = [c for c in ("_lat", "_lon", "index_right") if c in joined.columns]
    joined = joined.drop(columns=drop_internal, errors="ignore")

    print(f"Writing {out} ({len(joined)} features)...")
    joined.to_file(out, driver="GeoJSON")
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Build sjv_merged.geojson from GSAs, wells, CASGEM.")
    p.add_argument(
        "--chunksize",
        type=int,
        default=100_000,
        help="Rows per chunk when reading measurements.csv (default: 100000).",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output GeoJSON path (default: data/clean/sjv_merged.geojson).",
    )
    args = p.parse_args()
    try:
        path = run(chunksize=args.chunksize, output_path=args.output)
        print(f"Done: {path}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
