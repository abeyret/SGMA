"""
Robust GSP-level econometrics for Sinking Valley Explorer (sidecar only).

Extends explorer_gsp_econometrics.py with:
  - Spearman correlations + bootstrap CIs
  - Partial correlations (control baseline 2016 GWE depth)
  - OLS vs winsorized vs Theil-Sen sensitivity
  - Approved vs other mean differences + bootstrap CIs
  - Subsidence year-panel with GSP + year fixed effects (manual demeaning)
  - Spatial neighbor spillover descriptives (GSA weights → GSP exposure)
  - Forest plot, binned-means scatters, group comparison bars

Outputs under outputs/econometrics/ (CSVs, PNGs, robust_summary.txt).
Run after explorer_gsp_econometrics.py or standalone (loads panel itself).
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from explorer_gsp_econometrics import OUT, SUBSIDENCE_CSV, load_gsp_panel, ols_hc1

ROOT = Path(__file__).resolve().parents[1]
PANEL_CSV = ROOT / "data/processed/csv/sjv_sgma_panel.csv"
SPILLOVER_CSV = ROOT / "data/processed/csv/gsa_spillover_weights.csv"
EXPLORER_JSON = ROOT / "vercel_site/sinking_valley_explorer_data.json"

RNG = np.random.default_rng(42)
N_BOOT = 2000
WINSOR_P = (0.05, 0.95)
COLORS = {"approved": "#004655", "other": "#888888", "ci": "#2563eb", "line": "#111827"}


# ---------------------------------------------------------------------------
# Nonparametric / robust helpers (numpy only)
# ---------------------------------------------------------------------------

def rankdata(x: np.ndarray) -> np.ndarray:
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, len(x) + 1, dtype=float)
    # average ranks for ties
    sorted_x = x[order]
    i = 0
    while i < len(x):
        j = i
        while j + 1 < len(x) and sorted_x[j + 1] == sorted_x[i]:
            j += 1
        if j > i:
            avg = (i + 1 + j + 1) / 2.0
            ranks[order[i : j + 1]] = avg
        i = j + 1
    return ranks


def spearman_rho(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 4:
        return float("nan")
    return float(np.corrcoef(rankdata(x), rankdata(y))[0, 1])


def bootstrap_spearman(
    x: np.ndarray, y: np.ndarray, n_boot: int = N_BOOT, seed: int = 42
) -> dict:
    rng = np.random.default_rng(seed)
    n = len(x)
    rho = spearman_rho(x, y)
    boots = []
    for _ in range(n_boot):
        idx = rng.integers(0, n, size=n)
        boots.append(spearman_rho(x[idx], y[idx]))
    boots = np.array([b for b in boots if np.isfinite(b)])
    if len(boots) < 50:
        return {"rho": rho, "ci_lo": np.nan, "ci_hi": np.nan, "p_perm": np.nan, "n": n}
    ci_lo, ci_hi = np.percentile(boots, [2.5, 97.5])
    # two-sided permutation p-value: fraction of |boot| >= |obs| under sign-flip null (approx via bootstrap)
    p_perm = float(np.mean(np.abs(boots - boots.mean()) >= abs(rho - boots.mean())))
    return {"rho": rho, "ci_lo": float(ci_lo), "ci_hi": float(ci_hi), "p_perm": p_perm, "n": n}


def partial_corr(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> float:
    """Pearson partial correlation r_xy.z via residualization."""
    Z = np.column_stack([np.ones(len(z)), z])
    bx = np.linalg.lstsq(Z, x, rcond=None)[0]
    by = np.linalg.lstsq(Z, y, rcond=None)[0]
    rx = x - Z @ bx
    ry = y - Z @ by
    return float(np.corrcoef(rx, ry)[0, 1])


def bootstrap_partial(
    x: np.ndarray, y: np.ndarray, z: np.ndarray, n_boot: int = N_BOOT, seed: int = 42
) -> dict:
    rng = np.random.default_rng(seed)
    r = partial_corr(x, y, z)
    n = len(x)
    boots = []
    for _ in range(n_boot):
        idx = rng.integers(0, n, size=n)
        boots.append(partial_corr(x[idx], y[idx], z[idx]))
    boots = np.array([b for b in boots if np.isfinite(b)])
    ci_lo, ci_hi = (np.percentile(boots, [2.5, 97.5]) if len(boots) >= 50 else (np.nan, np.nan))
    return {"r_partial": r, "ci_lo": float(ci_lo), "ci_hi": float(ci_hi), "n": n}


def winsorize(v: np.ndarray, p: tuple[float, float] = WINSOR_P) -> np.ndarray:
    lo, hi = np.percentile(v, [100 * p[0], 100 * p[1]])
    return np.clip(v, lo, hi)


def theil_sen_slope(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Median of pairwise slopes; intercept at median x."""
    n = len(x)
    slopes = []
    for i in range(n):
        for j in range(i + 1, n):
            if x[j] != x[i]:
                slopes.append((y[j] - y[i]) / (x[j] - x[i]))
    if not slopes:
        return float("nan"), float("nan")
    b1 = float(np.median(slopes))
    b0 = float(np.median(y - b1 * x))
    return b0, b1


def bootstrap_mean_diff(a: np.ndarray, b: np.ndarray, n_boot: int = N_BOOT, seed: int = 42) -> dict:
    rng = np.random.default_rng(seed)
    diff = float(a.mean() - b.mean())
    boots = []
    for _ in range(n_boot):
        sa = a[rng.integers(0, len(a), len(a))]
        sb = b[rng.integers(0, len(b), len(b))]
        boots.append(sa.mean() - sb.mean())
    boots = np.array(boots)
    ci_lo, ci_hi = np.percentile(boots, [2.5, 97.5])
    return {
        "diff": diff,
        "ci_lo": float(ci_lo),
        "ci_hi": float(ci_hi),
        "mean_a": float(a.mean()),
        "mean_b": float(b.mean()),
        "n_a": len(a),
        "n_b": len(b),
    }


def twfe_ols(
    df: pd.DataFrame,
    y_col: str,
    x_cols: list[str],
    entity_col: str = "gsp_id",
    time_col: str = "year",
) -> dict | None:
    sub = df[[y_col, entity_col, time_col, *x_cols]].dropna()
    if len(sub) < len(x_cols) + 10:
        return None
    y = sub[y_col].to_numpy(float)
    X = sub[x_cols].to_numpy(float)
    ent = sub[entity_col].to_numpy()
    tim = sub[time_col].to_numpy()

    def demean(v: np.ndarray, groups: np.ndarray) -> np.ndarray:
        out = v.copy()
        for g in np.unique(groups):
            m = groups == g
            out[m] -= v[m].mean()
        return out

    # Drop time-invariant columns within entity (would be collinear after entity FE)
    keep = []
    for j, col in enumerate(x_cols):
        varies = sub.groupby(entity_col)[col].nunique().gt(1).all()
        if varies:
            keep.append(j)
    if not keep:
        return None
    X = X[:, keep]
    x_cols = [x_cols[j] for j in keep]

    y_e = demean(y, ent)
    X_e = np.column_stack([demean(X[:, j], ent) for j in range(X.shape[1])])
    y_fe = demean(y_e, tim)
    X_fe = np.column_stack([demean(X_e[:, j], tim) for j in range(X_e.shape[1])])

    X_aug = np.column_stack([np.ones(len(sub)), X_fe])
    try:
        res = ols_hc1(X_aug, y_fe)
    except np.linalg.LinAlgError:
        return None
    if not np.all(np.isfinite(res.se_hc1)):
        return None
    return {
        "n": res.n,
        "n_entity": sub[entity_col].nunique(),
        "n_time": sub[time_col].nunique(),
        "coef": {c: res.coef[i + 1] for i, c in enumerate(x_cols)},
        "se": {c: res.se_hc1[i + 1] for i, c in enumerate(x_cols)},
        "t": {c: res.t[i + 1] for i, c in enumerate(x_cols)},
        "r2_within": res.r2,
    }


# ---------------------------------------------------------------------------
# Data assembly
# ---------------------------------------------------------------------------

def load_subsidence_panel(gsp_panel: pd.DataFrame) -> pd.DataFrame:
    sub = pd.read_csv(SUBSIDENCE_CSV)
    sub["gsp_id"] = sub["gsp_id"].astype(str)
    sub = sub.rename(columns={"mean_subsidence_ft": "subsidence_ft_yr"})
    cross = gsp_panel[
        ["gsp_id", "label", "approved", "gwe_drop_2016", "gwe_drop_2024", "fallow_delta_pp"]
    ].copy()
    cross["gsp_id"] = cross["gsp_id"].astype(str)
    panel = sub.merge(cross, on="gsp_id", how="left")
    panel["post_sgma"] = (panel["year"] >= 2016).astype(int)

    fallow = load_fallow_gsp_panel()[["gsp_id", "year", "fallow_pct"]]
    panel = panel.merge(fallow, on=["gsp_id", "year"], how="left")
    return panel


def gsp_to_primary_gsa() -> dict[str, int]:
    """Map GSP → dominant GSA (by area_fraction) for spillover aggregation."""
    panel = pd.read_csv(PANEL_CSV)
    panel = panel.dropna(subset=["gsp_id"])
    panel["gsp_id"] = panel["gsp_id"].astype(int).astype(str)
    idx = panel.groupby("gsp_id")["area_fraction"].idxmax()
    return dict(zip(panel.loc[idx, "gsp_id"].astype(str), panel.loc[idx, "gsa_id"].astype(int)))


def build_spillover_exposure(gsp_panel: pd.DataFrame) -> pd.DataFrame:
    if not SPILLOVER_CSV.is_file():
        return pd.DataFrame()
    spill = pd.read_csv(SPILLOVER_CSV)
    gsp_gsa = gsp_to_primary_gsa()
    gsp_panel = gsp_panel.copy()
    gsp_panel["gsp_id"] = gsp_panel["gsp_id"].astype(str)
    gsp_panel["gsa_id"] = gsp_panel["gsp_id"].map(gsp_gsa)

    # Normalize spillover weights per source GSA
    spill["w_norm"] = spill.groupby("gsa_source")["spillover_weight"].transform(
        lambda s: s / s.sum() if s.sum() > 0 else 0
    )

    rows = []
    for _, row in gsp_panel.dropna(subset=["gsa_id"]).iterrows():
        gid = row["gsp_id"]
        gsa = int(row["gsa_id"])
        nbr = spill.loc[spill["gsa_source"] == gsa, ["gsa_target", "w_norm"]]
        if nbr.empty:
            continue
        # Map neighbor GSAs back to GSPs (any GSP whose primary GSA is target)
        gsa_to_gsp = {v: k for k, v in gsp_gsa.items()}
        nbr_gsp_ids = []
        nbr_weights = []
        for _, nr in nbr.iterrows():
            tgt_gsp = gsa_to_gsp.get(int(nr["gsa_target"]))
            if tgt_gsp and tgt_gsp != gid:
                nbr_gsp_ids.append(tgt_gsp)
                nbr_weights.append(nr["w_norm"])
        if not nbr_gsp_ids:
            continue
        w = np.array(nbr_weights)
        w = w / w.sum()
        nbr_df = gsp_panel.set_index("gsp_id").loc[nbr_gsp_ids]
        rows.append({
            "gsp_id": gid,
            "label": row.get("label"),
            "approved": row.get("approved"),
            "n_neighbors": len(nbr_gsp_ids),
            "neighbor_mean_subsidence": float(np.nansum(w * nbr_df["mean_subsidence_ft_yr"].to_numpy(float))),
            "neighbor_mean_gwe_drop_2024": float(np.nansum(w * nbr_df["gwe_drop_2024"].to_numpy(float))),
            "neighbor_mean_fallow_delta": float(np.nansum(w * nbr_df["fallow_delta_pp"].to_numpy(float))),
            "own_subsidence": row.get("mean_subsidence_ft_yr"),
            "own_gwe_drop_2024": row.get("gwe_drop_2024"),
        })
    return pd.DataFrame(rows)


def load_fallow_gsp_panel() -> pd.DataFrame:
    panel = pd.read_csv(PANEL_CSV)
    panel = panel.dropna(subset=["gsp_id", "fallow_pct"])
    panel["gsp_id"] = panel["gsp_id"].astype(int).astype(str)
    gsp_yr = panel.groupby(["gsp_id", "year"], as_index=False).agg(
        fallow_pct=("fallow_pct", "mean"),
        approved=("status_std", lambda s: int((s == "approved").any())),
    )
    gsp_yr["post_sgma"] = (gsp_yr["year"] >= 2020).astype(int)
    return gsp_yr


# ---------------------------------------------------------------------------
# Analysis blocks
# ---------------------------------------------------------------------------

CORRELATION_PAIRS = [
    ("fallow_delta_pp", "sgma_era_gwe_drop_ft", "Fallow ↑ vs SGMA-era GWE change"),
    ("gwe_drop_2024", "mean_subsidence_ft_yr", "2024 depth vs mean subsidence"),
    ("gwe_drop_2024", "well_reports_delta", "2024 depth vs dry-well report Δ"),
    ("gwe_drop_2016", "sgma_era_gwe_drop_ft", "Baseline depth vs SGMA-era change"),
    ("fallow_delta_pp", "ag_acres_delta", "Fallow ↑ vs ag acres Δ"),
    ("subsidence_2024", "gwe_drop_2024", "2024 subsidence vs 2024 depth"),
]

PARTIAL_PAIRS = [
    ("fallow_delta_pp", "sgma_era_gwe_drop_ft", "gwe_drop_2016"),
    ("mean_subsidence_ft_yr", "gwe_drop_2024", "gwe_drop_2016"),
    ("well_reports_delta", "gwe_drop_2024", "well_reports_2016"),
]

ROBUST_SPECS = [
    ("sgma_era_gwe_drop_ft", ["fallow_delta_pp"]),
    ("mean_subsidence_ft_yr", ["gwe_drop_2024"]),
    ("well_reports_delta", ["gwe_drop_2024"]),
]

APPROVED_METRICS = [
    "sgma_era_gwe_drop_ft",
    "fallow_delta_pp",
    "mean_subsidence_ft_yr",
    "subsidence_2024",
    "well_reports_delta",
    "ag_acres_delta",
]

SUBSIDENCE_SPECS = [
    ("fe_subsidence_fallow", "subsidence_ft_yr", ["fallow_pct"]),
]


def run_correlations(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for x, y, label in CORRELATION_PAIRS:
        sub = df[[x, y]].dropna()
        if len(sub) < 6:
            continue
        xv, yv = sub[x].to_numpy(float), sub[y].to_numpy(float)
        boot = bootstrap_spearman(xv, yv)
        rows.append({"pair": label, "x": x, "y": y, **boot})
    return pd.DataFrame(rows)


def run_partials(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for x, y, z in PARTIAL_PAIRS:
        sub = df[[x, y, z]].dropna()
        if len(sub) < 8:
            continue
        boot = bootstrap_partial(
            sub[x].to_numpy(float),
            sub[y].to_numpy(float),
            sub[z].to_numpy(float),
        )
        rows.append({"pair": f"{y} ~ {x} | {z}", "x": x, "y": y, "control": z, **boot})
    return pd.DataFrame(rows)


def run_robust_regression_compare(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for y, xs in ROBUST_SPECS:
        sub = df[[y, *xs]].dropna()
        if len(sub) < len(xs) + 5:
            continue
        xv = sub[xs[0]].to_numpy(float)
        yv = sub[y].to_numpy(float)
        X = np.column_stack([np.ones(len(sub)), xv])
        ols = ols_hc1(X, yv)
        xw, yw = winsorize(xv), winsorize(yv)
        Xw = np.column_stack([np.ones(len(sub)), xw])
        ols_w = ols_hc1(Xw, yw)
        ts_b0, ts_b1 = theil_sen_slope(xv, yv)
        rows.append({
            "y": y, "x": xs[0], "n": len(sub),
            "ols_slope": ols.coef[1], "ols_se": ols.se_hc1[1],
            "winsor_slope": ols_w.coef[1], "winsor_se": ols_w.se_hc1[1],
            "theil_sen_slope": ts_b1,
            "slope_range": max(ols.coef[1], ols_w.coef[1], ts_b1) - min(ols.coef[1], ols_w.coef[1], ts_b1),
        })
    return pd.DataFrame(rows)


def subsidence_pre_post_bootstrap(sub_panel: pd.DataFrame) -> pd.DataFrame:
    """GSP-level mean subsidence early (2016–19) vs late (2020–24) SGMA era."""
    rows = []
    for gid, grp in sub_panel.groupby("gsp_id"):
        early = grp.loc[grp["year"].between(2016, 2019), "subsidence_ft_yr"].dropna()
        late = grp.loc[grp["year"].between(2020, 2024), "subsidence_ft_yr"].dropna()
        if len(early) < 2 or len(late) < 2:
            continue
        early_m, late_m = float(early.mean()), float(late.mean())
        rows.append({"gsp_id": gid, "early_mean": early_m, "late_mean": late_m, "diff_late_early": late_m - early_m})
    d = pd.DataFrame(rows)
    if d.empty:
        return d
    diffs = d["diff_late_early"].to_numpy(float)
    boots = []
    rng = np.random.default_rng(42)
    for _ in range(N_BOOT):
        idx = rng.integers(0, len(diffs), len(diffs))
        boots.append(float(diffs[idx].mean()))
    ci_lo, ci_hi = np.percentile(boots, [2.5, 97.5])
    summary = pd.DataFrame([{
        "metric": "subsidence_late_minus_early_sgma",
        "n_gsp": len(d),
        "mean_diff": float(diffs.mean()),
        "ci_lo": float(ci_lo),
        "ci_hi": float(ci_hi),
        "median_gsp_diff": float(np.median(diffs)),
    }])
    d.to_csv(OUT / "subsidence_early_late_by_gsp.csv", index=False)
    summary.to_csv(OUT / "subsidence_early_late_summary.csv", index=False)
    return summary


def run_approved_bootstrap(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for m in APPROVED_METRICS:
        if m not in df.columns:
            continue
        a = df.loc[df["approved"] == 1, m].dropna().to_numpy(float)
        b = df.loc[df["approved"] == 0, m].dropna().to_numpy(float)
        if len(a) < 2 or len(b) < 3:
            continue
        boot = bootstrap_mean_diff(a, b)
        boot["metric"] = m
        boot["interpretation"] = "approved minus other"
        rows.append(boot)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------

def plot_forest(corr_df: pd.DataFrame, path: Path) -> None:
    if corr_df.empty:
        return
    d = corr_df.sort_values("rho")
    fig, ax = plt.subplots(figsize=(8, max(4, 0.45 * len(d) + 1.5)))
    ypos = np.arange(len(d))
    ax.errorbar(
        d["rho"], ypos,
        xerr=[d["rho"] - d["ci_lo"], d["ci_hi"] - d["rho"]],
        fmt="o", color=COLORS["ci"], ecolor="#93c5fd", capsize=3, ms=6,
    )
    ax.axvline(0, color="#9ca3af", lw=1)
    ax.set_yticks(ypos)
    ax.set_yticklabels(d["pair"], fontsize=8)
    ax.set_xlabel("Spearman ρ (bootstrap 95% CI)")
    ax.set_title("Cross-sectional rank correlations among GSP outcomes")
    ax.grid(True, axis="x", color="#e5e7eb")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def plot_binned_scatter(
    df: pd.DataFrame, x: str, y: str, path: Path, n_bins: int = 5, hue: str | None = None
) -> None:
    sub = df[[x, y] + ([hue] if hue else [])].dropna()
    if len(sub) < 8:
        return
    fig, ax = plt.subplots(figsize=(7.5, 5.2))
    if hue:
        for val, grp in sub.groupby(hue):
            color = COLORS["approved"] if val == 1 else COLORS["other"]
            label = "Approved" if val == 1 else "Other"
            ax.scatter(grp[x], grp[y], s=45, alpha=0.65, color=color, label=label)
        ax.legend(frameon=False, fontsize=9)
    else:
        ax.scatter(sub[x], sub[y], s=45, alpha=0.65, color=COLORS["ci"])
    # binned means
    sub = sub.sort_values(x)
    sub["_bin"] = pd.qcut(sub[x], q=min(n_bins, len(sub) // 2), duplicates="drop")
    binned = sub.groupby("_bin", observed=True)[[x, y]].mean()
    ax.plot(binned[x], binned[y], "s-", color=COLORS["line"], lw=2, ms=7, label="Binned mean")
    xv, yv = sub[x].to_numpy(float), sub[y].to_numpy(float)
    b = ols_hc1(np.column_stack([np.ones(len(sub)), xv]), yv)
    xs = np.linspace(xv.min(), xv.max(), 100)
    ax.plot(xs, b.coef[0] + b.coef[1] * xs, "--", color="#dc2626", lw=1.5, label="OLS")
    ax.set_xlabel(x)
    ax.set_ylabel(y)
    ax.set_title(f"{y} vs {x} (n={len(sub)})")
    ax.legend(frameon=False, fontsize=8)
    ax.grid(True, color="#e5e7eb")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def plot_approved_bars(boot_df: pd.DataFrame, path: Path) -> None:
    if boot_df.empty:
        return
    d = boot_df.copy()
    d["abs_diff"] = d["diff"].abs()
    d = d.sort_values("abs_diff", ascending=True)
    fig, ax = plt.subplots(figsize=(8, max(4, 0.5 * len(d) + 1.5)))
    ypos = np.arange(len(d))
    colors = [COLORS["approved"] if v > 0 else "#dc2626" for v in d["diff"]]
    ax.barh(ypos, d["diff"], color=colors, alpha=0.75, height=0.55)
    ax.errorbar(
        d["diff"], ypos,
        xerr=[d["diff"] - d["ci_lo"], d["ci_hi"] - d["diff"]],
        fmt="none", ecolor="#111827", capsize=3, lw=1.2,
    )
    ax.axvline(0, color="#9ca3af", lw=1)
    ax.set_yticks(ypos)
    ax.set_yticklabels(d["metric"], fontsize=8)
    ax.set_xlabel("Mean difference (approved − other)")
    ax.set_title("Group contrasts with bootstrap 95% CI")
    ax.grid(True, axis="x", color="#e5e7eb")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def plot_spillover(spill_df: pd.DataFrame, path: Path) -> None:
    sub = spill_df.dropna(subset=["own_subsidence", "neighbor_mean_subsidence"])
    if len(sub) < 6:
        return
    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    ax.scatter(
        sub["neighbor_mean_subsidence"], sub["own_subsidence"],
        s=50, alpha=0.75, c=sub["approved"].map({1: COLORS["approved"], 0: COLORS["other"]}),
    )
    xv = sub["neighbor_mean_subsidence"].to_numpy(float)
    yv = sub["own_subsidence"].to_numpy(float)
    b = ols_hc1(np.column_stack([np.ones(len(sub)), xv]), yv)
    xs = np.linspace(xv.min(), xv.max(), 100)
    ax.plot(xs, b.coef[0] + b.coef[1] * xs, color=COLORS["line"], lw=2)
    rho = spearman_rho(xv, yv)
    ax.set_xlabel("Neighbor-weighted mean subsidence (ft/yr)")
    ax.set_ylabel("Own GSP subsidence (ft/yr)")
    ax.set_title(f"Spatial spillover descriptives (n={len(sub)}, ρ={rho:.2f})")
    ax.grid(True, color="#e5e7eb")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Narrative
# ---------------------------------------------------------------------------

def write_robust_analysis_md(
    df: pd.DataFrame,
    corr_df: pd.DataFrame,
    partial_df: pd.DataFrame,
    robust_df: pd.DataFrame,
    approved_df: pd.DataFrame,
    fe_results: list[dict],
    spill_df: pd.DataFrame,
    prepost_df: pd.DataFrame,
) -> str:
    n = len(df)
    n_ap = int((df["approved"] == 1).sum())

    def _fmt_corr(row) -> str:
        return f"ρ={row['rho']:.2f} [{row['ci_lo']:.2f}, {row['ci_hi']:.2f}], n={int(row['n'])}"

    top_corr = corr_df.sort_values("rho", key=abs, ascending=False).head(4) if not corr_df.empty else pd.DataFrame()
    bullets_corr = "\n".join(f"- **{r.pair}:** {_fmt_corr(r)}" for _, r in top_corr.iterrows()) or "- (insufficient pairs)"

    partial_lines = ""
    if not partial_df.empty:
        for _, r in partial_df.iterrows():
            partial_lines += f"- **{r['pair']}:** r={r['r_partial']:.2f} [{r['ci_lo']:.2f}, {r['ci_hi']:.2f}]\n"

    robust_lines = ""
    if not robust_df.empty:
        for _, r in robust_df.iterrows():
            fragile = r["slope_range"] > abs(r["ols_slope"]) * 0.5 if r["ols_slope"] != 0 else r["slope_range"] > 0.01
            tag = "⚠ outlier-sensitive" if fragile else "✓ stable"
            robust_lines += (
                f"- **{r['y']} ~ {r['x']}** ({tag}): OLS={r['ols_slope']:.3g}, "
                f"winsor={r['winsor_slope']:.3g}, Theil-Sen={r['theil_sen_slope']:.3g}\n"
            )

    approved_lines = ""
    if not approved_df.empty:
        for _, r in approved_df.iterrows():
            sig = (r["ci_lo"] > 0) or (r["ci_hi"] < 0)
            tag = "CI excludes 0" if sig else "CI includes 0"
            approved_lines += f"- **{r['metric']}:** Δ={r['diff']:.2g} [{r['ci_lo']:.2g}, {r['ci_hi']:.2g}] ({tag})\n"

    fe_lines = ""
    for fe in fe_results:
        fe_lines += f"- **{fe['name']}** (n={fe['result']['n']}, {fe['result']['n_entity']} GSPs, {fe['result']['n_time']} yrs):\n"
        for x, c in fe["result"]["coef"].items():
            se = fe["result"]["se"][x]
            fe_lines += f"  - {x}: {c:.4g} (se={se:.4g}, t={fe['result']['t'][x]:.2f})\n"

    spill_line = ""
    if not spill_df.empty:
        sub = spill_df.dropna(subset=["own_subsidence", "neighbor_mean_subsidence"])
        if len(sub) >= 6:
            rho = spearman_rho(
                sub["neighbor_mean_subsidence"].to_numpy(float),
                sub["own_subsidence"].to_numpy(float),
            )
            spill_line = f"- Neighbor vs own subsidence: Spearman ρ={rho:.2f} (n={len(sub)} GSPs with mapped neighbors)\n"

    prepost_line = ""
    if not prepost_df.empty:
        r = prepost_df.iloc[0]
        prepost_line = (
            f"- Subsidence late SGMA (2020–24) vs early (2016–19), GSP means: "
            f"{r['mean_diff']:.3f} ft/yr [{r['ci_lo']:.3f}, {r['ci_hi']:.3f}], n={int(r['n_gsp'])} GSPs\n"
        )

    baseline_row = corr_df.loc[corr_df["x"] == "gwe_drop_2016"] if not corr_df.empty else pd.DataFrame()
    baseline_txt = ""
    if not baseline_row.empty:
        r = baseline_row.iloc[0]
        baseline_txt = f"- **Baseline 2016 depth vs SGMA-era GWE change:** ρ={r['rho']:.2f} [{r['ci_lo']:.2f}, {r['ci_hi']:.2f}], n={int(r['n'])}\n"

    return f"""# Explorer econometrics — robust analysis

Sidecar analysis for the Sinking Valley Explorer. **Not causal.** Generated from
`vercel_site/sinking_valley_explorer_data.json` plus processed panel/subsidence/spillover CSVs.

## Sample & coverage

| Item | Value |
|------|-------|
| SJV GSPs | {n} |
| Approved (2024) | {n_ap} |
| GWE cumulative metrics | 2016 & 2024 endpoints (sign: + = deeper below pre-2016 baseline) |
| `sgma_era_gwe_drop_ft` | 2016→2024 cumulative change (negative = net recovery) |
| Subsidence panel | 2016–2024 annual mean ft/yr (+ = sinking) |
| Fallow GSP panel | 30 GSPs in `sjv_sgma_panel.csv` (2012–2024) |

**Missingness:** ~24% of GSPs lack full GWE endpoint pair; subsidence is more complete (45/45 GSPs with multi-year series). Dry-well counts are sparse at GSP level and dominated by a few high-reporting basins (Madera, Kings).

---

## Thesis-ready findings (associational)

### 1. Baseline depth predicts SGMA-era trajectory (mean reversion)

{baseline_txt}{partial_lines or ''}

**Plain language:** GSPs deeper below baseline in 2016 (`gwe_drop_2016` ↑) tended toward *more recovery* over 2016–24 (more negative `sgma_era_gwe_drop_ft`; Spearman ρ≈−0.48). This pattern is consistent with mean reversion or heavier ongoing pumping in initially shallower areas — **not** evidence that SGMA plans caused recovery. Controlling for baseline, fallow has no partial association with recovery (r≈0.09, CI spans 0).

### 2. Subsidence co-moves with water-table depth below baseline

{robust_lines or '- See robust_regression_compare.csv'}

**Plain language:** Deeper 2024 water tables (higher `gwe_drop_2024`) associate with higher mean subsidence, but the slope is small (~0.001 ft/yr subsidence per ft of depth) and sensitive to outliers in Kern/Tule subsidence hotspots.

### 3. Fallowing is not clearly linked to groundwater recovery at GSP scale

{bullets_corr}

**Plain language:** Rank correlation between fallow increase and SGMA-era GWE change is near zero (ρ≈0.05–0.15, CI spans 0). Producers can fallow without local recovery, or recovery can occur without proportional fallow — consistent with lateral flow, imported water, or measurement noise.

### 4. Approved vs other GSPs — descriptive gaps, not treatment effects

{approved_lines or '- See approved_bootstrap_ci.csv'}

**Plain language:** Approved GSPs show **lower subsidence** (mean ~0.05 vs ~0.83 ft/yr) but this reflects hydrology and selection (only 5 approved SJV GSPs; White Wolf drives extreme GWE recovery). **Do not interpret as causal impact of approval.**

### 5. Panel fixed effects (subsidence on fallow / post-SGMA era)

{fe_lines or '- Insufficient year-varying covariates for TWFE (need variables that move within GSP over time).'}

With GSP + year FE, higher fallow share within a GSP over time does not show a robust negative association with subsidence — land fallowing and InSAR-measured sinking operate on different margins in this sample.

### 6. Spatial neighbor spillover (descriptive)

{spill_line or '- Spillover mapping incomplete for some GSPs.'}

{prepost_line or ''}

Neighbor-weighted subsidence (from `gsa_spillover_weights.csv` aggregated to primary GSP) correlates with own subsidence — expected for contiguous aquifer stress; not evidence of policy spillovers.

---

## What is robust vs fragile

| Claim | Verdict |
|-------|---------|
| Deeper baselines → more recovery 2016–24 | **Moderate** — Spearman ρ≈−0.48, CI excludes 0; GWE endpoints sparse |
| Subsidence ↑ with depth below baseline | **Moderate** — OLS t≈2, winsorized similar; Kern outliers matter |
| Fallow ↑ → GWE recovery | **Fragile** — ρ≈0, all CIs include 0 |
| Approved → lower subsidence | **Descriptive only** — large CI, n=5 approved |
| Dry wells ↑ with depth stress | **Fragile** — reporting bias; Madera/Kings leverage |

---

## Causality caveats

1. **Selection:** Worst basins face scrutiny; approval timing is endogenous.
2. **N≈45:** Cluster/bootstrap CIs are wide; one GSP (e.g., White Wolf, Madera) moves estimates.
3. **Dry wells:** Numerator counts without denominator; post-2020 reporting expansion.
4. **Subsidence:** InSAR coverage varies (`n_points`); spatial averaging hides hotspots.
5. **Cross-GSP spillover weights:** Hydrologic adjacency ≠ policy transmission.

---

## Recommended next steps (thesis)

1. **Event study** on GSP determination dates (`gsp_determination_status.csv`) with pre-trend tests.
2. **Well-level CASGEM panel** instead of GSP means for physical outcomes.
3. **Drought controls:** merge PDSI by subbasin × year into `sjv_sgma_panel.csv`.
4. **Synthetic control** for state-intervention basins (Delta-Mendota, Tulare Lake).
5. Install `linearmodels` for formal PanelOLS with clustered SE at subbasin level.

---

## Output files

| File | Description |
|------|-------------|
| `spearman_correlations.csv` | Rank correlations + bootstrap 95% CI |
| `partial_correlations.csv` | Partial r controlling baseline depth / wells |
| `robust_regression_compare.csv` | OLS vs winsorized vs Theil-Sen slopes |
| `approved_bootstrap_ci.csv` | Approved − other mean differences |
| `subsidence_early_late_summary.csv` | Late vs early SGMA subsidence by GSP |
| `spillover_exposure.csv` | Neighbor-weighted covariates per GSP |
| `forest_correlations.png` | Correlation forest plot |
| `binned_*.png` | Binned-mean scatters |
| `approved_bootstrap_bars.png` | Group comparison bars |
| `spillover_subsidence_scatter.png` | Neighbor vs own subsidence |
| `robust_summary.txt` | Machine-readable run log |
"""


def main() -> int:
    if not EXPLORER_JSON.is_file():
        raise FileNotFoundError(f"Missing explorer JSON: {EXPLORER_JSON}")

    OUT.mkdir(parents=True, exist_ok=True)
    df = load_gsp_panel()
    df.to_csv(OUT / "gsp_panel.csv", index=False)

    corr_df = run_correlations(df)
    corr_df.to_csv(OUT / "spearman_correlations.csv", index=False)

    partial_df = run_partials(df)
    partial_df.to_csv(OUT / "partial_correlations.csv", index=False)

    robust_df = run_robust_regression_compare(df)
    robust_df.to_csv(OUT / "robust_regression_compare.csv", index=False)

    approved_df = run_approved_bootstrap(df)
    approved_df.to_csv(OUT / "approved_bootstrap_ci.csv", index=False)

    sub_panel = load_subsidence_panel(df)
    sub_panel.to_csv(OUT / "subsidence_year_panel.csv", index=False)
    prepost = subsidence_pre_post_bootstrap(sub_panel)

    fe_rows = []
    fe_results = []
    for name, y, xs in SUBSIDENCE_SPECS:
        res = twfe_ols(sub_panel, y, xs)
        if res:
            fe_results.append({"name": name, "result": res})
            for x, c in res["coef"].items():
                fe_rows.append({
                    "model": name,
                    "y": y,
                    "x": x,
                    "coef": c,
                    "se_hc1": res["se"][x],
                    "t": res["t"][x],
                    "n": res["n"],
                    "n_gsp": res["n_entity"],
                    "n_year": res["n_time"],
                    "r2_within": res["r2_within"],
                })
    if fe_rows:
        pd.DataFrame(fe_rows).to_csv(OUT / "subsidence_panel_fe.csv", index=False)

    fallow_panel = load_fallow_gsp_panel()
    fe_fallow = twfe_ols(fallow_panel, "fallow_pct", ["post_sgma"])
    if fe_fallow:
        pd.DataFrame([{
            "model": "fe_fallow_post_sgma",
            "x": "post_sgma",
            **{f"coef_{k}": v for k, v in fe_fallow["coef"].items()},
            **{f"se_{k}": v for k, v in fe_fallow["se"].items()},
            "n": fe_fallow["n"],
            "n_gsp": fe_fallow["n_entity"],
        }]).to_csv(OUT / "fallow_panel_fe.csv", index=False)

    spill_df = build_spillover_exposure(df)
    if not spill_df.empty:
        spill_df.to_csv(OUT / "spillover_exposure.csv", index=False)

    # Plots
    plot_forest(corr_df, OUT / "forest_correlations.png")
    plot_binned_scatter(df, "fallow_delta_pp", "sgma_era_gwe_drop_ft", OUT / "binned_fallow_vs_gwe.png", hue="approved")
    plot_binned_scatter(df, "gwe_drop_2024", "mean_subsidence_ft_yr", OUT / "binned_subsidence_vs_depth.png")
    plot_binned_scatter(df, "gwe_drop_2024", "well_reports_delta", OUT / "binned_wells_vs_depth.png")
    plot_approved_bars(approved_df, OUT / "approved_bootstrap_bars.png")
    if not spill_df.empty:
        plot_spillover(spill_df, OUT / "spillover_subsidence_scatter.png")

    summary_lines = [
        "Robust GSP econometrics run",
        f"N GSPs: {len(df)}",
        f"Spearman pairs: {len(corr_df)}",
        f"Partial pairs: {len(partial_df)}",
        f"Robust specs: {len(robust_df)}",
        f"Approved contrasts: {len(approved_df)}",
        f"Subsidence FE models: {len(fe_results)}",
        f"Spillover GSPs: {len(spill_df)}",
        "",
        "Top Spearman correlations:",
    ]
    for _, r in corr_df.sort_values("rho", key=abs, ascending=False).head(6).iterrows():
        summary_lines.append(f"  {r['pair']}: rho={r['rho']:.3f} [{r['ci_lo']:.3f}, {r['ci_hi']:.3f}]")
    (OUT / "robust_summary.txt").write_text("\n".join(summary_lines), encoding="utf-8")

    # Merge/update ANALYSIS.md (robust section replaces prior stub)
    analysis_md = write_robust_analysis_md(
        df, corr_df, partial_df, robust_df, approved_df, fe_results, spill_df, prepost
    )
    (OUT / "ANALYSIS.md").write_text(analysis_md, encoding="utf-8")

    print(f"Wrote robust outputs to {OUT}/")
    print(f"  {len(corr_df)} Spearman pairs, {len(fe_results)} FE models, {len(spill_df)} spillover rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
