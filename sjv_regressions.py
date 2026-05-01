"""
Run simple county-level regressions for the SGMA Equity project.

Reads embedded county metrics from:
  data/clean/sjv_equity_atlas.html

Outputs:
  data/clean/sjv_regressions_results.csv
  data/clean/sjv_regressions_results.txt
  data/clean/sjv_regressions_<model>.png   (scatter + fitted line)

Notes:
- Only 8 SJV counties, so treat inference cautiously.
- Uses plain OLS with (small-sample) HC1 robust SE implemented in numpy.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
ATLAS_HTML = ROOT / "data/clean/sjv_equity_atlas.html"
OUT_DIR = ROOT / "data/clean"


def extract_atlas_json(html_text: str) -> dict:
    # Parse "const ATLAS = " then read a balanced JSON object until matching "}".
    marker = "const ATLAS = "
    start = html_text.find(marker)
    if start < 0:
        raise ValueError("Could not find 'const ATLAS =' in HTML.")
    i = start + len(marker)
    # skip whitespace
    while i < len(html_text) and html_text[i].isspace():
        i += 1
    if i >= len(html_text) or html_text[i] != "{":
        raise ValueError("ATLAS JSON does not start with '{'.")

    depth = 0
    in_str = False
    esc = False
    j = i
    while j < len(html_text):
        ch = html_text[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == "\"":
                in_str = False
        else:
            if ch == "\"":
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    blob = html_text[i : j + 1]
                    return json.loads(blob)
        j += 1
    raise ValueError("Could not find end of ATLAS JSON object.")


def load_county_df() -> pd.DataFrame:
    txt = ATLAS_HTML.read_text(encoding="utf-8")
    atlas = extract_atlas_json(txt)
    feats = atlas["counties"]["features"]
    rows = []
    for f in feats:
        p = f["properties"]
        pre = p.get("pre", {})
        post = p.get("post", {})
        d = p.get("delta", {})
        rows.append(
            {
                "county": p.get("name"),
                "fips5": p.get("fips5"),
                # wells
                "well_pre": pre.get("well_failures_issue_start"),
                "well_post": post.get("well_failures_issue_start"),
                "well_delta": d.get("well_failures_issue_start"),
                "well_total": pre.get("well_failures_total"),
                # groundwater
                "gwe_pre": pre.get("gwe_ft"),
                "gwe_post": post.get("gwe_ft"),
                "gwe_delta": d.get("gwe_ft"),
                # fallow
                "fallow_pre": pre.get("fallow_acres"),
                "fallow_post": post.get("fallow_acres"),
                "fallow_delta": d.get("fallow_acres"),
                # income
                "inc_pre": pre.get("median_income"),
                "inc_post": post.get("median_income"),
                "inc_delta": d.get("median_income"),
                # farms
                "farms_pre": pre.get("total_farms"),
                "farms_post": post.get("total_farms"),
                "farms_delta": d.get("total_farms"),
                "small_pre": pre.get("small_farms"),
                "small_post": post.get("small_farms"),
                "small_delta": d.get("small_farms"),
                "small_loss": d.get("small_farm_loss"),
                "large_pre": pre.get("large_farms"),
                "large_post": post.get("large_farms"),
                "large_delta": d.get("large_farms"),
                # CES
                "ces_score_pct": p.get("ces_score_pct"),
                # subsidence (optional)
                "subs_rate_change_mm_yr": (p.get("subsidence") or {}).get("rate_change_mm_yr"),
                "subs_early_rate_mm_yr": (p.get("subsidence") or {}).get("early_rate_mm_yr"),
                "subs_late_rate_mm_yr": (p.get("subsidence") or {}).get("late_rate_mm_yr"),
            }
        )
    df = pd.DataFrame(rows)
    # numeric coercion
    for c in df.columns:
        if c in {"county", "fips5"}:
            continue
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


@dataclass
class OLSResult:
    n: int
    k: int
    coef: np.ndarray
    se_hc1: np.ndarray
    t: np.ndarray
    r2: float


def ols_hc1(X: np.ndarray, y: np.ndarray) -> OLSResult:
    """
    OLS with HC1 robust SE.
    X includes intercept already.
    """
    n, k = X.shape
    XtX = X.T @ X
    XtX_inv = np.linalg.inv(XtX)
    beta = XtX_inv @ (X.T @ y)
    yhat = X @ beta
    resid = y - yhat

    # R^2 (centered)
    ybar = y.mean()
    sst = float(((y - ybar) ** 2).sum())
    ssr = float((resid**2).sum())
    r2 = 1.0 - (ssr / sst) if sst > 0 else float("nan")

    # HC1: (n/(n-k)) * (X' diag(e^2) X)
    meat = X.T @ (X * (resid[:, None] ** 2))
    scale = n / (n - k) if n > k else 1.0
    V = scale * (XtX_inv @ meat @ XtX_inv)
    se = np.sqrt(np.diag(V))
    t = beta / se
    return OLSResult(n=n, k=k, coef=beta, se_hc1=se, t=t, r2=r2)


def run_model(df: pd.DataFrame, y_col: str, x_cols: list[str]) -> tuple[pd.DataFrame, OLSResult]:
    sub = df[["county", y_col, *x_cols]].dropna().copy()
    y = sub[y_col].to_numpy(dtype=float)
    X = sub[x_cols].to_numpy(dtype=float)
    X = np.column_stack([np.ones(len(sub)), X])
    res = ols_hc1(X, y)

    names = ["Intercept", *x_cols]
    out = pd.DataFrame(
        {
            "term": names,
            "coef": res.coef,
            "se_hc1": res.se_hc1,
            "t_hc1": res.t,
        }
    )
    out.insert(0, "y", y_col)
    out.insert(1, "x", " + ".join(x_cols))
    out["n"] = res.n
    out["r2"] = res.r2
    return out, res


def plot_scatter_fit(df: pd.DataFrame, y_col: str, x_col: str, out_path: Path) -> None:
    sub = df[["county", x_col, y_col]].dropna().copy()
    if sub.empty:
        return
    x = sub[x_col].to_numpy(float)
    y = sub[y_col].to_numpy(float)
    X = np.column_stack([np.ones(len(sub)), x])
    res = ols_hc1(X, y)

    fig, ax = plt.subplots(figsize=(7.6, 5.2))
    ax.scatter(x, y, s=55, color="#2563eb", edgecolor="white", linewidth=1.0, zorder=3)
    for _, r in sub.iterrows():
        ax.text(r[x_col], r[y_col], str(r["county"]), fontsize=9, alpha=0.9, ha="left", va="center")

    xs = np.linspace(x.min(), x.max(), 100)
    ys = res.coef[0] + res.coef[1] * xs
    ax.plot(xs, ys, color="#111827", linewidth=2.0, zorder=2)

    ax.grid(True, color="#e5e7eb", linewidth=1)
    ax.set_xlabel(x_col)
    ax.set_ylabel(y_col)
    ax.set_title(f"{y_col} vs {x_col} (n={res.n}, R2={res.r2:.2f})", fontsize=11)
    fig.tight_layout()
    fig.savefig(out_path, dpi=180)
    plt.close(fig)


def main() -> int:
    if not ATLAS_HTML.is_file():
        raise FileNotFoundError(f"Missing {ATLAS_HTML}. Build it first.")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    df = load_county_df()

    models: list[tuple[str, str, list[str], str | None]] = [
        # (name, y, x list, plot_x for 1D plot)
        ("m1_pre_harm", "well_pre", ["gwe_pre"], "gwe_pre"),
        ("m2_income_wells_2021", "well_total", ["inc_post"], "inc_post"),
        ("m3_small_loss_gw_change", "small_loss", ["gwe_delta"], "gwe_delta"),
        ("m4_pre_income_pre_wells", "well_pre", ["inc_pre"], "inc_pre"),
        ("m5_fallow_change_gw_change", "fallow_delta", ["gwe_delta"], "gwe_delta"),
        ("m6_small_loss_fallow_change", "small_loss", ["fallow_delta"], "fallow_delta"),
    ]

    results = []
    summary_lines = []
    summary_lines.append("SJV county regressions (OLS with HC1 robust SE)\n")
    summary_lines.append("Note: only 8 counties; treat inference cautiously.\n")

    for name, y, xs, plot_x in models:
        out, res = run_model(df, y, xs)
        out.insert(0, "model", name)
        results.append(out)
        summary_lines.append(f"Model {name}: {y} ~ {', '.join(xs)} (n={res.n}, R2={res.r2:.3f})")
        for i, term in enumerate(["Intercept", *xs]):
            summary_lines.append(
                f"  {term}: coef={res.coef[i]:.4g}, se(HC1)={res.se_hc1[i]:.4g}, t={res.t[i]:.3g}"
            )
        summary_lines.append("")

        if plot_x and len(xs) == 1:
            plot_scatter_fit(df, y, plot_x, OUT_DIR / f"sjv_regressions_{name}.png")

    all_res = pd.concat(results, ignore_index=True)
    all_res.to_csv(OUT_DIR / "sjv_regressions_results.csv", index=False)
    (OUT_DIR / "sjv_regressions_results.txt").write_text("\n".join(summary_lines), encoding="utf-8")

    print(f"Wrote {OUT_DIR / 'sjv_regressions_results.csv'}")
    print(f"Wrote {OUT_DIR / 'sjv_regressions_results.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

