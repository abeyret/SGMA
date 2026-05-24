"""
GSP-level econometric analysis for Sinking Valley Explorer (sidecar — does not touch the site).

Reads the same JSON the explorer uses, merges subsidence aggregates, runs descriptive
comparisons and OLS with HC1 robust SE (numpy only).

Outputs:
  outputs/econometrics/gsp_panel.csv
  outputs/econometrics/regression_results.csv
  outputs/econometrics/regression_summary.txt
  outputs/econometrics/ANALYSIS.md
  outputs/econometrics/*.png

For robust analysis (Spearman, bootstrap, FE, spillover), also run:
  analysis/explorer_gsp_robust_econometrics.py
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EXPLORER_JSON = ROOT / "vercel_site/sinking_valley_explorer_data.json"
SUBSIDENCE_CSV = ROOT / "data/processed/csv/subsidence_by_gsp_year.csv"
OUT = ROOT / "outputs/econometrics"


@dataclass
class OLSResult:
    n: int
    k: int
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
    r2 = 1.0 - float((resid**2).sum()) / sst if sst > 0 else float("nan")
    meat = X.T @ (X * (resid[:, None] ** 2))
    scale = n / (n - k) if n > k else 1.0
    se = np.sqrt(np.diag(scale * XtX_inv @ meat @ XtX_inv))
    return OLSResult(n=n, k=k, coef=beta, se_hc1=se, t=beta / se, r2=r2)


def _metric(g: dict, key: str, year: str):
    return (g.get("metrics") or {}).get(key, {}).get(year)


def _delta(g: dict, key: str):
    return (g.get("metrics") or {}).get(key, {}).get("delta")


def load_gsp_panel() -> pd.DataFrame:
    data = json.loads(EXPLORER_JSON.read_text(encoding="utf-8"))
    catalog = [g for g in data["gsp_catalog"] if g.get("is_sjv")]

    sub_agg = {}
    if SUBSIDENCE_CSV.is_file():
        sub = pd.read_csv(SUBSIDENCE_CSV)
        for gid, grp in sub.groupby("gsp_id"):
            sub_agg[str(gid)] = {
                "mean_subsidence_ft_yr": float(grp["mean_subsidence_ft"].mean()),
                "subsidence_2024": float(grp.loc[grp["year"] == 2024, "mean_subsidence_ft"].iloc[0])
                if (grp["year"] == 2024).any() else None,
            }

    rows = []
    for g in catalog:
        gid = str(g["gsp_id"])
        row = {
            "gsp_id": gid,
            "label": g.get("label"),
            "status_2024": g.get("status_2024"),
            "approved": int(bool(g.get("compliant"))),
            "sgma_era_gwe_drop_ft": g.get("sgma_era_gwe_drop_ft"),
            "gwe_drop_2016": _metric(g, "gwe_cumulative_drop", "2016"),
            "gwe_drop_2024": _metric(g, "gwe_cumulative_drop", "2024"),
            "gwe_trend_2024": _metric(g, "gwe_trend_4yr_ft_yr", "2024"),
            "fallow_pct_2016": _metric(g, "fallow_pct", "2016"),
            "fallow_pct_2024": _metric(g, "fallow_pct", "2024"),
            "fallow_delta_pp": _delta(g, "fallow_pct"),
            "ag_acres_delta": _delta(g, "total_ag_acres"),
            "well_reports_2016": _metric(g, "well_reports", "2016"),
            "well_reports_2024": _metric(g, "well_reports", "2024"),
            "well_reports_delta": _delta(g, "well_reports"),
            "large_farm_share_delta": _delta(g, "large_farm_share"),
        }
        row.update(sub_agg.get(gid, {}))
        rows.append(row)

    df = pd.DataFrame(rows)
    for c in df.columns:
        if c not in {"gsp_id", "label", "status_2024"}:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def run_model(df: pd.DataFrame, name: str, y: str, xs: list[str]) -> tuple[pd.DataFrame, OLSResult | None]:
    sub = df[[y, *xs]].dropna()
    if len(sub) < len(xs) + 3:
        return pd.DataFrame(), None
    X = np.column_stack([np.ones(len(sub)), sub[xs].to_numpy(float)])
    res = ols_hc1(X, sub[y].to_numpy(float))
    out = pd.DataFrame({
        "model": name,
        "y": y,
        "x": " + ".join(xs),
        "term": ["Intercept", *xs],
        "coef": res.coef,
        "se_hc1": res.se_hc1,
        "t_hc1": res.t,
        "n": res.n,
        "r2": res.r2,
    })
    return out, res


def plot_bivariate(df: pd.DataFrame, x: str, y: str, path: Path, hue: str | None = None) -> None:
    sub = df[[x, y] + ([hue] if hue else [])].dropna()
    if len(sub) < 4:
        return
    fig, ax = plt.subplots(figsize=(7.5, 5.2))
    if hue:
        for val, grp in sub.groupby(hue):
            color = "#004655" if val == 1 else "#888888"
            label = "Approved" if val == 1 else "Other"
            ax.scatter(grp[x], grp[y], s=45, alpha=0.75, color=color, label=label)
        ax.legend(frameon=False, fontsize=9)
    else:
        ax.scatter(sub[x], sub[y], s=45, alpha=0.75, color="#2563eb")
    xv = sub[x].to_numpy(float)
    yv = sub[y].to_numpy(float)
    X = np.column_stack([np.ones(len(sub)), xv])
    b = ols_hc1(X, yv)
    xs = np.linspace(xv.min(), xv.max(), 100)
    ax.plot(xs, b.coef[0] + b.coef[1] * xs, color="#111827", lw=2)
    ax.set_xlabel(x)
    ax.set_ylabel(y)
    ax.set_title(f"{y} vs {x} (n={b.n}, R²={b.r2:.2f})")
    ax.grid(True, color="#e5e7eb")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def approved_comparison(df: pd.DataFrame) -> pd.DataFrame:
    metrics = [
        "sgma_era_gwe_drop_ft", "fallow_delta_pp", "ag_acres_delta",
        "well_reports_delta", "mean_subsidence_ft_yr", "subsidence_2024",
    ]
    rows = []
    for m in metrics:
        if m not in df.columns:
            continue
        for label, mask in [("approved", df["approved"] == 1), ("other", df["approved"] == 0)]:
            s = df.loc[mask, m].dropna()
            if s.empty:
                continue
            rows.append({
                "metric": m,
                "group": label,
                "n": len(s),
                "mean": round(s.mean(), 3),
                "median": round(s.median(), 3),
            })
    return pd.DataFrame(rows)


def write_analysis_md(df: pd.DataFrame, comp: pd.DataFrame, n_models: int) -> str:
    n = len(df)
    n_ap = int((df["approved"] == 1).sum())
    return f"""# Explorer econometrics (sidecar analysis)

Generated from `sinking_valley_explorer_data.json` — **not wired into the website**.

## Sample

- **{n}** SJV GSPs in catalog
- **{n_ap}** approved (2024 status)
- Years: 2016 → 2024 (same metrics as Close view / Takeaways)

## What this can answer (descriptive / associational)

1. **Producer adjustment vs recovery:** Do GSPs that raised fallow share also see water-table recovery?
2. **Residents:** Where the table rose, did dry-well reports fall (joint patterns — reporting bias caveat)?
3. **Environment:** Is subsidence (InSAR per GSP) associated with water-table depth below baseline?
4. **Equity / governance:** Do approved vs other GSPs differ in mean outcomes? (Not causal — worst basins get scrutiny.)

## What this cannot claim

- GSP approval **causes** better outcomes (selection / endogeneity)
- OLS at GSP level with ~45 units → **cluster-robust inference still fragile**
- Dry-well counts = true failure rate (reporting expanded post-2020)
- Subsidence GSP aggregates are sparse in some plan areas

## Approved vs other (means)

```
{comp.to_string(index=False) if not comp.empty else '(no comparison)'}
```

## Models estimated

See `regression_results.csv` and `regression_summary.txt` ({n_models} specifications).

## Next steps (before adding to site)

1. **Panel FE:** GSP + year fixed effects (`linearmodels`) when installed
2. **Event study:** determination date × year relative to approval
3. **Spatial spillover:** neighbor GWE / subsidence (`gsa_spillover_weights.csv`)
4. **Drought controls:** PDSI by year × subbasin

## Files

| File | Purpose |
|------|---------|
| `gsp_panel.csv` | One row per GSP, analysis-ready |
| `regression_results.csv` | OLS coefficients (HC1 SE) |
| `regression_summary.txt` | Human-readable output |
| `*.png` | Bivariate scatter + fit lines |
"""


def main() -> int:
    if not EXPLORER_JSON.is_file():
        raise FileNotFoundError(f"Run build_sinking_valley_explorer.py first — missing {EXPLORER_JSON}")

    OUT.mkdir(parents=True, exist_ok=True)
    df = load_gsp_panel()
    df.to_csv(OUT / "gsp_panel.csv", index=False)

    models = [
        ("m1_fallow_vs_gwe_recovery", "sgma_era_gwe_drop_ft", ["fallow_delta_pp"]),
        ("m2_wells_vs_gwe_stress", "well_reports_delta", ["gwe_drop_2024"]),
        ("m3_subsidence_vs_baseline_depth", "mean_subsidence_ft_yr", ["gwe_drop_2024"]),
        ("m4_ag_loss_vs_fallow", "ag_acres_delta", ["fallow_delta_pp"]),
        ("m5_gwe_recovery_vs_baseline_2016", "sgma_era_gwe_drop_ft", ["gwe_drop_2016"]),
        ("m6_approved_outcome", "fallow_delta_pp", ["approved"]),
    ]

    results = []
    summary = ["GSP-level OLS (HC1 robust SE) — explorer metrics\n", f"N GSPs in panel: {len(df)}\n"]
    for name, y, xs in models:
        out, res = run_model(df, name, y, xs)
        if res is None:
            summary.append(f"{name}: insufficient data\n")
            continue
        results.append(out)
        summary.append(f"{name}: {y} ~ {' + '.join(xs)}  (n={res.n}, R²={res.r2:.3f})")
        for i, term in enumerate(["Intercept", *xs]):
            summary.append(f"  {term}: {res.coef[i]:.4g} (se={res.se_hc1[i]:.4g}, t={res.t[i]:.2f})")
        summary.append("")

    if results:
        pd.concat(results, ignore_index=True).to_csv(OUT / "regression_results.csv", index=False)
    (OUT / "regression_summary.txt").write_text("\n".join(summary), encoding="utf-8")

    comp = approved_comparison(df)
    comp.to_csv(OUT / "approved_vs_other_means.csv", index=False)

    plot_bivariate(df, "fallow_delta_pp", "sgma_era_gwe_drop_ft", OUT / "scatter_fallow_vs_gwe_recovery.png", hue="approved")
    plot_bivariate(df, "gwe_drop_2024", "well_reports_delta", OUT / "scatter_wells_vs_gwe_stress.png")
    plot_bivariate(df, "gwe_drop_2024", "mean_subsidence_ft_yr", OUT / "scatter_subsidence_vs_gwe_depth.png")

    (OUT / "ANALYSIS.md").write_text(write_analysis_md(df, comp, len(results)), encoding="utf-8")

    print(f"Wrote {OUT}/")
    print(f"  gsp_panel.csv ({len(df)} GSPs)")
    print(f"  {len(results)} regression models")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
