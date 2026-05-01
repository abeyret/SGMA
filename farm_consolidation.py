"""
Analyze Census of Agriculture farm operation counts by area-operated size class
for San Joaquin Valley counties (2012, 2017, 2022).

Uses disjoint NASS size bins only (excludes overlapping roll-up categories).

Outputs (data/clean/):
  farm_consolidation_stacked.png   — stacked bars by county × year
  farm_consolidation_totals.png    — total farm count over time (lines)
  farm_consolidation_summary.csv   — small/large farm changes (optional table)

Requires: pandas, matplotlib
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
FARM_JSON = ROOT / "data/raw/farm_size/farm_operations.json"
OUT_DIR = ROOT / "data/clean"

YEARS = (2012, 2017, 2022)

# Disjoint size buckets — keys = normalized domaincat tail (after "AREA OPERATED: ")
# Roll-ups "(50 TO 179)", "(180 TO 499)", "(1,000 OR MORE ACRES)" are excluded (double-count).
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

BUCKET_LABELS = {
    "under_50": "Under 50 ac",
    "50_179": "50–179 ac",
    "180_499": "180–499 ac",
    "500_999": "500–999 ac",
    "1000_plus": "1,000+ ac",
}

BUCKET_ORDER = ["under_50", "50_179", "180_499", "500_999", "1000_plus"]

COLORS = ["#7fc97f", "#beaed4", "#fdc086", "#ffff99", "#386cb0"]


def parse_value(v: object) -> int:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return 0
    s = str(v).strip().replace(",", "")
    if not s or s in {"**", "(D)"}:
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def load_long_records(path: Path) -> pd.DataFrame:
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
        if yr not in YEARS:
            continue
        cat = str(r.get("domaincat_desc", "")).strip()
        bucket = BUCKET_MAP.get(cat)
        if bucket is None:
            continue
        f5 = str(r.get("county_fips5", "")).zfill(5)
        rows.append(
            {
                "county_fips5": f5,
                "year": yr,
                "bucket": bucket,
                "operations": parse_value(r.get("Value")),
            }
        )
    if not rows:
        raise ValueError("No AREA OPERATED farm-operation rows matched.")
    return pd.DataFrame(rows)


def aggregate_counts(df: pd.DataFrame) -> pd.DataFrame:
    """Sum operations by county, year, bucket."""
    g = (
        df.groupby(["county_fips5", "year", "bucket"], as_index=False)["operations"]
        .sum()
    )
    return g


def pivot_wide(agg: pd.DataFrame) -> pd.DataFrame:
    wide = agg.pivot_table(
        index=["county_fips5", "year"],
        columns="bucket",
        values="operations",
        aggfunc="sum",
        fill_value=0,
    ).reset_index()
    for b in BUCKET_ORDER:
        if b not in wide.columns:
            wide[b] = 0
    return wide


def county_name_lookup() -> dict[str, str]:
    """Short names for FIPS from farm JSON county_name."""
    path = FARM_JSON
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    m: dict[str, str] = {}
    for r in data.get("records", []):
        f5 = str(r.get("county_fips5", "")).zfill(5)
        nm = str(r.get("county_name", "")).strip()
        if f5 and nm:
            m[f5] = nm.title()
    return m


def add_derived(wide: pd.DataFrame) -> pd.DataFrame:
    out = wide.copy()
    out["small_under_180"] = out["under_50"] + out["50_179"]
    out["large_500_plus"] = out["500_999"] + out["1000_plus"]
    out["total_farms"] = out[BUCKET_ORDER].sum(axis=1)
    return out


def plot_stacked_bars(wide: pd.DataFrame, names: dict[str, str], out_path: Path) -> None:
    """One panel: counties on x, grouped stacks for 2012 / 2017 / 2022."""
    counties = sorted(
        wide["county_fips5"].unique(),
        key=lambda c: names.get(c, c),
    )
    n_c, n_y = len(counties), len(YEARS)
    fig_w = max(12, n_c * 1.1)
    fig, ax = plt.subplots(figsize=(fig_w, 7))

    group_w = 0.75
    bar_w = group_w / n_y
    x = np.arange(n_c)
    offsets = (np.arange(n_y) - (n_y - 1) / 2.0) * bar_w

    for yi, year in enumerate(YEARS):
        bot = np.zeros(n_c)
        sub = wide.loc[wide["year"] == year].set_index("county_fips5")
        for bi, bucket in enumerate(BUCKET_ORDER):
            heights = []
            for c in counties:
                if c in sub.index:
                    row = sub.loc[c]
                    h = float(row.get(bucket, 0) or 0)
                else:
                    h = 0.0
                heights.append(h)
            heights = np.array(heights)
            ax.bar(
                x + offsets[yi],
                heights,
                width=bar_w * 0.92,
                bottom=bot,
                label=BUCKET_LABELS[bucket] if yi == 0 else None,
                color=COLORS[bi],
                edgecolor="0.25",
                linewidth=0.35,
            )
            bot += heights

    ax.set_xticks(x)
    ax.set_xticklabels([names.get(c, c) for c in counties], rotation=35, ha="right")
    ax.set_ylabel("Number of farm operations")
    ax.set_title(
        "Farm operations by area-operated size — SJV counties (Census of Ag)\n"
        "Within each county: bars left to right = 2012, 2017, 2022",
        fontsize=11,
    )
    ax.legend(title="Size class", bbox_to_anchor=(1.02, 1), loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_totals_lines(wide: pd.DataFrame, names: dict[str, str], out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 6))
    years = list(YEARS)
    for f5 in sorted(wide["county_fips5"].unique(), key=lambda c: names.get(c, c)):
        sub = wide.loc[wide["county_fips5"] == f5].sort_values("year")
        ax.plot(
            sub["year"],
            sub["total_farms"],
            marker="o",
            linewidth=2,
            label=names.get(f5, f5),
        )
    ax.set_xticks(years)
    ax.set_xlabel("Census year")
    ax.set_ylabel("Total farm operations (sum of size classes)")
    ax.set_title("Total farm operations over time — SJV counties")
    ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), fontsize=9)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    if not FARM_JSON.is_file():
        print(f"Missing {FARM_JSON}", file=sys.stderr)
        return 1

    long_df = load_long_records(FARM_JSON)
    agg = aggregate_counts(long_df)
    wide = pivot_wide(agg)
    wide = add_derived(wide)
    names = county_name_lookup()

    # 2012 vs 2022 comparison table
    w12 = wide.loc[wide["year"] == 2012].set_index("county_fips5")
    w22 = wide.loc[wide["year"] == 2022].set_index("county_fips5")
    summary_rows = []
    for f5 in sorted(wide["county_fips5"].unique()):
        s12 = w12.loc[f5] if f5 in w12.index else None
        s22 = w22.loc[f5] if f5 in w22.index else None
        if s12 is None or s22 is None:
            continue
        d_small = float(s22["small_under_180"]) - float(s12["small_under_180"])
        d_large = float(s22["large_500_plus"]) - float(s12["large_500_plus"])
        summary_rows.append(
            {
                "county": names.get(f5, f5),
                "county_fips5": f5,
                "small_farms_2012": int(s12["small_under_180"]),
                "small_farms_2022": int(s22["small_under_180"]),
                "change_small_under_180": int(d_small),
                "large_farms_2012": int(s12["large_500_plus"]),
                "large_farms_2022": int(s22["large_500_plus"]),
                "change_large_500_plus": int(d_large),
            }
        )
    summary = pd.DataFrame(summary_rows)
    summary["lost_small_rank"] = summary["change_small_under_180"].rank(ascending=True, method="min")
    summary_sorted = summary.sort_values("change_small_under_180", ascending=True)

    print("\n=== Counties by change in small farms (<180 ac), 2012 to 2022 ===\n")
    print("(Most loss = largest decline first)\n")
    disp = summary_sorted[
        [
            "county",
            "small_farms_2012",
            "small_farms_2022",
            "change_small_under_180",
            "change_large_500_plus",
        ]
    ].copy()
    disp.columns = [
        "County",
        "Small farms 2012",
        "Small farms 2022",
        "chg small (<180 ac)",
        "chg large (500+ ac)",
    ]
    print(disp.to_string(index=False))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUT_DIR / "farm_consolidation_summary.csv"
    summary_sorted.to_csv(csv_path, index=False)
    print(f"\nWrote table: {csv_path}")

    plot_stacked_bars(wide, names, OUT_DIR / "farm_consolidation_stacked.png")
    plot_totals_lines(wide, names, OUT_DIR / "farm_consolidation_totals.png")
    print(f"Wrote {OUT_DIR / 'farm_consolidation_stacked.png'}")
    print(f"Wrote {OUT_DIR / 'farm_consolidation_totals.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
