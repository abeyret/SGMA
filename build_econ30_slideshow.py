"""Build ECON 30 HTML slideshow: ECON107 motivation + Section 7 spine + publication figures."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from build_sgma_equity_analysis import build_briefing_data, load_atlas, gsp_records, load_gsp_research, load_fallowing_research, load_dry_wells_research, dry_well_summary, county_records

ROOT = Path(__file__).resolve().parent
DECK = ROOT / "vercel_site" / "assets" / "deck"
TEMPLATE = ROOT / "vercel_site" / "thesis_presentation_template.html"
OUT = ROOT / "vercel_site" / "thesis_presentation.html"
PPT = "./assets/ppt"
MARKER = "<!-- THESIS_DATA_INLINE -->"


def setup_mpl() -> None:
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "figure.facecolor": "white",
        }
    )


def save(fig, name: str) -> str:
    DECK.mkdir(parents=True, exist_ok=True)
    p = DECK / name
    fig.savefig(p, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return f"./assets/deck/{name}"


def chart_wells(counties: list[dict]) -> str:
    rows = sorted(counties, key=lambda c: c["well_post"], reverse=True)
    fig, ax = plt.subplots(figsize=(9, 4))
    x = np.arange(len(rows))
    w = 0.35
    ax.bar(x - w / 2, [r["well_pre"] for r in rows], w, label="Pre-SGMA (2012–14)", color="#1e3a5f")
    ax.bar(x + w / 2, [r["well_post"] for r in rows], w, label="Post-SGMA (2018–22)", color="#8b4513")
    ax.set_xticks(x)
    ax.set_xticklabels([r["name"] for r in rows])
    ax.set_ylabel("Dry-well reports (issue start)")
    ax.set_title("Reported well failures rose sharply after SGMA", loc="left", fontsize=12, pad=10)
    ax.legend(frameon=False, loc="upper right")
    ax.yaxis.grid(True, linestyle="--", alpha=0.35)
    ax.set_axisbelow(True)
    fig.text(0.01, 0.01, "Source: DWR Household Water Supply Shortage Reporting System", fontsize=7, color="#666")
    return save(fig, "wells_by_county.png")


def chart_gwe(counties: list[dict]) -> str:
    rows = sorted(counties, key=lambda c: c["gwe_delta"])
    fig, ax = plt.subplots(figsize=(8, 3.8))
    colors = ["#2980b9" if r["gwe_delta"] < 0 else "#27ae60" for r in rows]
    ax.bar([r["name"] for r in rows], [r["gwe_delta"] for r in rows], color=colors, edgecolor="white")
    ax.axhline(0, color="#333", lw=0.8)
    ax.set_ylabel("Δ groundwater elevation (ft)")
    ax.set_title("County groundwater change · CASGEM 2012–14 vs 2018–22", loc="left", fontsize=12)
    ax.yaxis.grid(True, linestyle="--", alpha=0.3)
    ax.set_axisbelow(True)
    return save(fig, "gwe_delta_county.png")


def chart_farms(counties: list[dict]) -> str:
    rows = sorted(counties, key=lambda c: abs(c.get("small_loss", 0)), reverse=True)
    fig, ax = plt.subplots(figsize=(8, 3.8))
    x = np.arange(len(rows))
    w = 0.35
    ax.bar(x - w / 2, [abs(r.get("small_loss", 0)) for r in rows], w, label="Small farm loss (<180 ac)", color="#b42318")
    ax.bar(x + w / 2, [r.get("large_gain", 0) for r in rows], w, label="Large farm gain (≥500 ac)", color="#64748b")
    ax.set_xticks(x)
    ax.set_xticklabels([r["name"] for r in rows])
    ax.set_ylabel("Farm operations (count)")
    ax.set_title("Farm consolidation under water stress · NASS 2012→2022", loc="left", fontsize=12)
    ax.legend(frameon=False)
    return save(fig, "farm_consolidation.png")


def chart_dry_ts(timeseries: list[dict]) -> str:
    fig, ax = plt.subplots(figsize=(9, 3.6))
    years = [d["year"] for d in timeseries if 2005 <= d["year"] <= 2025]
    vals = [d["total"] for d in timeseries if 2005 <= d["year"] <= 2025]
    ax.plot(years, vals, color="#b42318", lw=2.5, marker="o", ms=4)
    ax.axvline(2014, color="#00778b", ls="--", lw=1.5, alpha=0.8)
    ax.text(2014.2, max(vals) * 0.92, "SGMA 2014", fontsize=9, color="#00778b")
    ax.set_xlabel("Issue-start year")
    ax.set_ylabel("Valley-wide dry-well reports")
    ax.set_title("Dry-well reporting accelerated post-SGMA", loc="left", fontsize=12)
    ax.yaxis.grid(True, linestyle="--", alpha=0.3)
    return save(fig, "dry_well_timeseries.png")


def chart_gsp_status(status: dict, labels: dict, colors: dict) -> str:
    items = sorted(status.items(), key=lambda x: -x[1])
    fig, ax = plt.subplots(figsize=(6, 3.5))
    names = [labels.get(k, k.replace("_", " ")) for k, _ in items]
    vals = [v for _, v in items]
    cols = [colors.get(k, "#888") for k, _ in items]
    ax.barh(names, vals, color=cols)
    ax.set_xlabel("Number of GSP plan areas")
    ax.set_title("GSP determination status · San Joaquin Valley (n=45)", loc="left", fontsize=11)
    for i, v in enumerate(vals):
        ax.text(v + 0.3, i, str(v), va="center", fontsize=9)
    fig.tight_layout()
    return save(fig, "gsp_status_bar.png")


def chart_scatter_reg(counties: list[dict], reg: dict | None) -> str:
    fig, ax = plt.subplots(figsize=(5.5, 4.5))
    xs = [c["gwe_delta"] for c in counties]
    ys = [c["fallow_delta"] for c in counties]
    ax.scatter(xs, ys, s=80, c="#00778b", edgecolors="white", linewidths=1.5, zorder=3)
    for c in counties:
        ax.annotate(c["name"][:4], (c["gwe_delta"], c["fallow_delta"]), fontsize=8, xytext=(4, 4), textcoords="offset points")
    if reg:
        xm, ym = np.mean(xs), np.mean(ys)
        b0 = ym - reg["coef_x"] * xm
        xline = np.linspace(min(xs), max(xs), 50)
        ax.plot(xline, reg["coef_x"] * xline + b0, "k-", lw=2, alpha=0.75)
        ax.set_title(f"Δ fallow vs Δ groundwater · R² = {reg['r2']:.3f} · n=8", loc="left", fontsize=11)
    ax.set_xlabel("Δ groundwater (ft)")
    ax.set_ylabel("Δ fallow acres (CDL)")
    ax.axhline(0, color="#ccc", lw=0.8)
    ax.axvline(0, color="#ccc", lw=0.8)
    return save(fig, "scatter_fallow_gwe.png")


def chart_gsp_map() -> str | None:
    src = ROOT / "outputs" / "maps" / "gsp_governance_quality.png"
    if src.is_file():
        DECK.mkdir(parents=True, exist_ok=True)
        dst = DECK / "gsp_map.png"
        shutil.copy2(src, dst)
        return "./assets/deck/gsp_map.png"
    try:
        import geopandas as gpd
        import pandas as pd

        gsp = gpd.read_parquet(ROOT / "data" / "processed" / "geoparquet" / "sjv_gsps.geoparquet")
        status = pd.read_csv(ROOT / "data" / "processed" / "csv" / "gsp_determination_status.csv")
        gsp["gsp_id"] = gsp["gsp_id"].astype(str)
        status["gsp_id"] = status["gsp_id"].astype(str)
        merged = gsp.merge(status, on="gsp_id", how="left")
        cmap = {
            "approved": "#27ae60",
            "inadequate": "#2980b9",
            "inadequate_under_review": "#8e44ad",
            "under_review": "#f39c12",
            "state_intervention": "#c0392b",
            "incomplete": "#7f8c8d",
        }
        merged["color"] = merged["status_std"].map(cmap).fillna("#bdc3c7")
        fig, ax = plt.subplots(figsize=(10, 8))
        merged.plot(ax=ax, color=merged["color"], edgecolor="white", linewidth=0.4)
        ax.set_axis_off()
        ax.set_title("GSP plan areas by determination status", loc="left", fontsize=13, pad=12)
        return save(fig, "gsp_map.png")
    except Exception:
        return None


def build_figures(data: dict) -> dict[str, str]:
    counties = data["counties"]
    paths = {
        "wells": chart_wells(counties),
        "gwe": chart_gwe(counties),
        "farms": chart_farms(counties),
        "dry_ts": chart_dry_ts(data["dry_wells"]["timeseries"]),
        "gsp_bar": chart_gsp_status(
            data["governance"]["gsp_status_std"],
            data["status_labels"],
            data["status_colors"],
        ),
        "scatter": chart_scatter_reg(
            counties,
            next((r for r in data["regressions"]["county"] if r["model"] == "m5_fallow_change_gw_change"), None),
        ),
    }
    gsp_map = chart_gsp_map()
    if gsp_map:
        paths["gsp_map"] = gsp_map
    return paths


def build_payload() -> dict:
    atlas = load_atlas()
    gsp_r = load_gsp_research()
    fallow = load_fallowing_research()
    dry = dry_well_summary(load_dry_wells_research())
    counties = county_records(atlas, fallow, dry.get("by_county", []))
    gsps = gsp_records(atlas, gsp_r, fallow)
    brief = build_briefing_data()
    brief["counties"] = counties
    brief["gsps"] = gsps
    brief["gsp_status_map"] = {g["gsp_id"]: g.get("status_std", "other") for g in gsps}
    brief["quotes"] = [
        {"type": "pro", "text": "SGMA finally forces us to live within our water means. Better to plan now than face mandatory cutbacks later.", "author": "Westlands grower · public comment, 2019"},
        {"type": "pro", "text": "We need sustainable groundwater for the long term — for communities, not just agriculture.", "author": "Karen Ross, CA Secretary of Food & Agriculture · SGMA outreach"},
        {"type": "con", "text": "Small farmers weren't at the table when these plans were written. We're the ones who'll lose access first.", "author": "Community Water Center · small farmer clinic, 2023"},
        {"type": "con", "text": "It's David and Goliath — a handful of large pumpers can keep going while family farms dry up.", "author": "Brenton Kelly, Cuyama Valley farmer · CalMatters, 2024"},
    ]
    brief["repair_costs"] = {
        "federal_2026_m": 889,
        "housing_2025_b": 1.87,
        "cumulative_b_min": 1,
        "long_term_aqueduct_b": 3,
    }
    brief["sources"] = [
        "TRE Altamira vertical displacement (CNRA) · subsidence map",
        "DWR Bulletin 118 critically overdrafted basins",
        "DWR Household Water Supply Shortage Reporting System",
        "DWR GSP Monitoring Network (CASGEM / MNM)",
        "USDA NASS farm operations by size",
        "CDL fallow acreage · Knight & Lee (2024) · Faunt et al. (2016)",
        "CalEnviroScreen tract aggregation · sgma_research panel",
    ]
    brief["meta"]["n_slides"] = 18
    return brief


def main() -> None:
    setup_mpl()
    data = build_payload()
    figures = build_figures(data)
    data["figures"] = figures
    data["ppt"] = PPT

    html = TEMPLATE.read_text(encoding="utf-8")
    payload = f"<script>window.THESIS_DATA = {json.dumps(data, separators=(',', ':'))};</script>"
    html = html.replace(MARKER, payload)
    OUT.write_text(html, encoding="utf-8")
    (ROOT / "vercel_site" / "thesis_presentation_data.js").write_text(
        f"window.THESIS_DATA = {json.dumps(data, indent=2)};\n", encoding="utf-8"
    )
    print(f"Wrote {OUT}")
    print(f"Figures: {', '.join(figures.keys())}")
    print(f"Slides: 18 · open via open_thesis_presentation.bat")


if __name__ == "__main__":
    main()
