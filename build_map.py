"""
Interactive Folium map for the San Joaquin Valley with a time slider choropleth.

Animates CDL fallow / idle cropland (acres) by county for 2012, 2014, 2018, and 2022
using folium.plugins.TimeSliderChoropleth. Popups (via centroid markers) show
year-by-year fallow, annual well-failure report counts, and available ACS incomes.

GSA boundaries are a static layer underneath. Output: data/clean/sjv_map_animated.html

Requires: folium, geopandas, pandas, branca.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

import folium
import geopandas as gpd
import pandas as pd
from branca.colormap import LinearColormap
from folium.plugins import TimeSliderChoropleth

ROOT = Path(__file__).resolve().parent

MERGED = ROOT / "data/clean/sjv_merged.geojson"
SUMMARY_CSV = ROOT / "data/clean/sjv_county_summary.csv"
LAND_USE = ROOT / "data/raw/land_use"
ACS_2014 = ROOT / "data/raw/socioeconomic/acs5_2014.csv"
ACS_2021 = ROOT / "data/raw/socioeconomic/acs5_2021.csv"
OUT_HTML = ROOT / "data/clean/sjv_map_animated.html"

CENSUS_COUNTIES_URL = (
    "https://www2.census.gov/geo/tiger/GENZ2022/shp/cb_2022_us_county_500k.zip"
)

# CDL years requested; require matching cdl_acreage_{year}.csv
CDL_YEARS = (2012, 2014, 2018, 2022)
FALLOW_CAT = "Fallow/Idle Cropland"


def find_gsa_geojson() -> Path:
    for p in (
        ROOT / "data/raw/gsa_boundaries/i03_Groundwater_Sustainability_Agencies.geojson",
        ROOT / "i03_Groundwater_Sustainability_Agencies.geojson",
    ):
        if p.is_file():
            return p
    raise FileNotFoundError(
        "Could not find i03_Groundwater_Sustainability_Agencies.geojson "
        "in data/raw/gsa_boundaries/ or project root."
    )


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


def sanitize_properties_for_json(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    out = gdf.copy()
    for col in out.columns:
        if col == "geometry":
            continue
        s = out[col]
        if pd.api.types.is_datetime64_any_dtype(s):
            out[col] = s.dt.strftime("%Y-%m-%d")
        elif str(s.dtype) == "object":
            out[col] = s.map(
                lambda x: x.isoformat()
                if isinstance(x, (pd.Timestamp, dt.datetime, dt.date))
                else x
            )
    return out


def load_gsa_sjv(path: Path) -> gpd.GeoDataFrame:
    gsa = gpd.read_file(path)
    if "Basin_Name" not in gsa.columns:
        raise ValueError("GSA GeoJSON missing Basin_Name.")
    basin = gsa["Basin_Name"].fillna("").astype(str).str.upper()
    mask = basin.str.contains("SAN JOAQUIN VALLEY", regex=False)
    out = gsa.loc[mask].copy()
    if out.empty:
        raise ValueError("No GSAs after filtering to San Joaquin Valley basin.")
    if out.crs is None:
        out.set_crs(epsg=4326, inplace=True)
    else:
        out = out.to_crs(epsg=4326)
    return sanitize_properties_for_json(out)


def county_name_to_fips_lookup() -> dict[str, str]:
    """Map County field variants -> 5-digit FIPS from ACS 2021."""
    acs = pd.read_csv(ACS_2021)
    m: dict[str, str] = {}
    for _, row in acs.iterrows():
        f5 = str(row["county_fips5"]).zfill(5)
        name = str(row["NAME"])
        base = re.match(r"^([^,]+)", name)
        short = base.group(1).replace(" County", "").strip() if base else name
        m[short] = f5
        m[short.upper()] = f5
        m[short.lower()] = f5
    return m


def map_county_to_fips(raw: object, mapping: dict[str, str]) -> str | None:
    if pd.isna(raw):
        return None
    s = str(raw).strip()
    return mapping.get(s) or mapping.get(s.title()) or mapping.get(s.upper()) or mapping.get(s.lower())


def load_fallow_for_year(year: int) -> pd.DataFrame:
    path = LAND_USE / f"cdl_acreage_{year}.csv"
    if not path.is_file():
        raise FileNotFoundError(f"Missing CDL file: {path}")
    df = pd.read_csv(path)
    df = df.loc[df["category"].astype(str).str.strip() == FALLOW_CAT].copy()
    df["county_fips5"] = df["county_fips5"].astype(str).str.zfill(5)
    df["fallow_acres"] = df["acreage"].apply(safe_float)
    return df[["county_fips5", "fallow_acres"]]


def annual_well_counts_by_county(wells: gpd.GeoDataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    """Count shortage reports per calendar year (Report Date) and county."""
    w = wells.copy()
    w["county_fips5"] = w["County"].apply(lambda x: map_county_to_fips(x, mapping))
    w = w.loc[w["county_fips5"].notna()].copy()
    w["report_year"] = pd.to_datetime(w.get("Report Date"), errors="coerce").dt.year
    w = w.loc[w["report_year"].notna()].copy()
    grp = (
        w.groupby(["county_fips5", "report_year"], as_index=False)
        .size()
        .rename(columns={"size": "well_reports"})
    )
    return grp


def load_acs_income() -> pd.DataFrame:
    out = []
    if ACS_2014.is_file():
        a = pd.read_csv(ACS_2014)
        a["county_fips5"] = a["county_fips5"].astype(str).str.zfill(5)
        a["median_income"] = a["B19013_001E"].apply(safe_float)
        a["acs_year"] = 2014
        out.append(a[["county_fips5", "acs_year", "median_income", "NAME"]])
    if ACS_2021.is_file():
        a = pd.read_csv(ACS_2021)
        a["county_fips5"] = a["county_fips5"].astype(str).str.zfill(5)
        a["median_income"] = a["B19013_001E"].apply(safe_float)
        a["acs_year"] = 2021
        out.append(a[["county_fips5", "acs_year", "median_income", "NAME"]])
    if not out:
        return pd.DataFrame(columns=["county_fips5", "acs_year", "median_income", "NAME"])
    return pd.concat(out, ignore_index=True)


def load_county_geometries(geoids: list[str]) -> gpd.GeoDataFrame:
    counties = gpd.read_file(CENSUS_COUNTIES_URL).to_crs(epsg=4326)
    counties["GEOID"] = (counties["STATEFP"] + counties["COUNTYFP"]).astype(str)
    return counties.loc[counties["GEOID"].isin(geoids)].copy()


def year_to_unix_key(y: int) -> str:
    """TimeSliderChoropleth sorts timestamp keys with int() — use UTC epoch seconds as str."""
    ts = pd.Timestamp(f"{y}-01-01", tz="UTC")
    return str(int(ts.timestamp()))


def build_panel(
    geoids: list[str],
    mapping: dict[str, str],
) -> tuple[pd.DataFrame, gpd.GeoDataFrame]:
    """Wide panel: one row per county with fallow_y, wells_y columns."""
    wells_gdf = gpd.read_file(MERGED)
    well_long = annual_well_counts_by_county(wells_gdf, mapping)

    fall_cols: dict[str, pd.Series] = {}
    well_cols: dict[str, pd.Series] = {}
    for y in CDL_YEARS:
        f = load_fallow_for_year(y).set_index("county_fips5")["fallow_acres"]
        fall_cols[f"fallow_{y}"] = f
        wl = well_long.loc[well_long["report_year"] == y].set_index("county_fips5")["well_reports"]
        well_cols[f"wells_{y}"] = wl

    rows = []
    for g in sorted(geoids):
        row: dict[str, object] = {"county_fips5": g}
        for y in CDL_YEARS:
            row[f"fallow_{y}"] = fall_cols.get(f"fallow_{y}", pd.Series(dtype=float)).get(g)
            row[f"wells_{y}"] = well_cols.get(f"wells_{y}", pd.Series(dtype=float)).get(g)
        rows.append(row)
    panel = pd.DataFrame(rows)
    for c in panel.columns:
        if c != "county_fips5":
            panel[c] = pd.to_numeric(panel[c], errors="coerce").fillna(0)

    gdf = load_county_geometries(geoids)
    acs = load_acs_income()
    name_map = (
        acs.loc[acs["acs_year"] == 2021, ["county_fips5", "NAME"]]
        .drop_duplicates("county_fips5")
        .set_index("county_fips5")["NAME"]
        .to_dict()
    )
    gdf = gdf.merge(panel, left_on="GEOID", right_on="county_fips5", how="left")
    gdf["county_label"] = gdf["GEOID"].map(
        lambda x: re.sub(r", California$", "", name_map.get(x, x), flags=re.I)
    )
    return panel, sanitize_properties_for_json(gdf)


def popup_html(geoid: str, row: pd.Series, acs_df: pd.DataFrame) -> str:
    """HTML table: CDL years × fallow & annual wells; ACS incomes in footer."""
    lines = [
        f"<b>{row.get('county_label', geoid)}</b>",
        "<table style='font-size:12px;border-collapse:collapse;min-width:260px;'>",
        "<tr><th style='text-align:left;padding:2px 6px;'>Year</th>"
        "<th style='text-align:right;'>Fallow (ac)</th>"
        "<th style='text-align:right;'>Well reports</th></tr>",
    ]
    for y in CDL_YEARS:
        fa = row.get(f"fallow_{y}", float("nan"))
        wr = row.get(f"wells_{y}", float("nan"))
        lines.append(
            f"<tr><td>{y}</td><td style='text-align:right;'>{fa:,.1f}</td>"
            f"<td style='text-align:right;'>{int(wr):,}</td></tr>"
        )
    lines.append("</table>")

    inc_bits = []
    sub = acs_df.loc[acs_df["county_fips5"] == geoid]
    for _, r in sub.iterrows():
        mi = r["median_income"]
        if pd.notna(mi):
            inc_bits.append(f"ACS {int(r['acs_year'])} median HH income: ${mi:,.0f}")
    if inc_bits:
        lines.append("<hr style='margin:6px 0;'/>" + "<br/>".join(inc_bits))
    if SUMMARY_CSV.is_file():
        try:
            s = pd.read_csv(SUMMARY_CSV)
            s["county_fips5"] = s["county_fips5"].astype(str).str.zfill(5)
            one = s.loc[s["county_fips5"] == geoid]
            if not one.empty:
                o = one.iloc[0]
                gwe = o.get("mean_groundwater_elevation_ft")
                if pd.notna(gwe):
                    lines.append(
                        f"<hr style='margin:6px 0;'/><span style='font-size:11px;'>"
                        f"CASGEM mean GWE (county avg): {float(gwe):,.1f} ft</span>"
                    )
        except Exception:
            pass

    return "<div style='font-family:sans-serif;'>" + "\n".join(lines) + "</div>"


def legend_html(fmin: float, fmax: float) -> str:
    return f"""
    <div style="position: fixed; bottom: 88px; right: 14px; width: 240px; z-index: 9998;
         font-family: sans-serif; font-size: 12px; background: rgba(255,255,255,0.93);
         border: 1px solid #333; border-radius: 4px; padding: 8px;">
      <div style="font-weight:bold;margin-bottom:4px;">Fallow / idle cropland (acres)</div>
      <div style="font-size:11px;color:#444;">Choropleth colors use the slider year.</div>
      <div style="font-size:11px;margin:4px 0;">Scale: {fmin:,.0f} (light) → {fmax:,.0f} (dark)</div>
      <div style="height:12px;width:100%;border:1px solid #888;border-radius:2px;
           background: linear-gradient(90deg, #ffffcc, #fd8d3c, #800026);"></div>
    </div>
    """


def main() -> int:
    if not MERGED.is_file():
        raise FileNotFoundError(f"Missing {MERGED}")
    if not LAND_USE.is_dir():
        raise FileNotFoundError(f"Missing {LAND_USE}")

    for y in CDL_YEARS:
        load_fallow_for_year(y)  # validate files early

    mapping = county_name_to_fips_lookup()
    geoids = sorted(set(mapping.values()))
    panel, gdf = build_panel(geoids, mapping)
    acs_df = load_acs_income()

    # Choropleth scale from all county-year fallow values
    fall_vals = []
    for y in CDL_YEARS:
        fall_vals.extend(panel[f"fallow_{y}"].dropna().tolist())
    fmin, fmax = min(fall_vals), max(fall_vals)
    if fmax <= fmin:
        fmax = fmin + 1.0
    cmap = LinearColormap(
        colors=["#ffffcc", "#fd8d3c", "#800026"],
        vmin=fmin,
        vmax=fmax,
    )

    styledict: dict[str, dict[str, dict[str, float | str]]] = {}
    for _, row in panel.iterrows():
        geoid = str(row["county_fips5"])
        styledict[geoid] = {}
        for y in CDL_YEARS:
            ts = year_to_unix_key(y)
            v = float(row[f"fallow_{y}"])
            styledict[geoid][ts] = {
                "color": cmap(v),
                "opacity": 0.78,
            }

    # GeoJSON: Feature id must match styledict keys (see folium TimeSliderChoropleth)
    geo = json.loads(gdf.to_json())
    for feat in geo.get("features", []):
        geoid = feat.get("properties", {}).get("GEOID") or feat.get("properties", {}).get(
            "county_fips5"
        )
        if geoid is not None:
            feat["id"] = str(geoid)

    data_str = json.dumps(geo)

    b = gdf.total_bounds
    m = folium.Map(
        location=[(b[1] + b[3]) / 2, (b[0] + b[2]) / 2],
        zoom_start=8,
        tiles="CartoDB positron",
        control_scale=True,
    )

    m.get_root().html.add_child(
        folium.Element(
            """
    <div style="position: fixed; top: 10px; left: 50%; transform: translateX(-50%); z-index: 9999;
         background: rgba(255,255,255,0.95); border: 1px solid #333; border-radius: 4px;
         padding: 8px 18px; font-family: sans-serif; font-size: 16px; font-weight: 600;
         box-shadow: 0 1px 4px rgba(0,0,0,0.25); max-width: 92vw; text-align: center;">
      San Joaquin Valley — CDL fallow over time (slider) &amp; county context
    </div>
            """
        )
    )
    m.get_root().html.add_child(folium.Element(legend_html(fmin, fmax)))

    # Static GSA layer first (under counties)
    gsa = load_gsa_sjv(find_gsa_geojson())
    folium.GeoJson(
        data=json.loads(gsa.to_json()),
        name="GSA boundaries (SJV)",
        style_function=lambda _f: {
            "fillColor": "#6baed6",
            "color": "#08519c",
            "weight": 1.1,
            "fillOpacity": 0.07,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=["GSA_Name", "Basin_Subbasin_Name"],
            aliases=["GSA", "Subbasin"],
        ),
    ).add_to(m)

    ts_layer = TimeSliderChoropleth(
        data=data_str,
        styledict=styledict,
        date_options="YYYY",
        name="Counties — CDL fallow (time slider)",
        highlight=True,
        init_timestamp=-1,
        stroke_color="#333333",
        stroke_width=1.0,
        stroke_opacity=0.9,
    )
    ts_layer.add_to(m)

    # Clickable centroid markers with full stats (TimeSlider layer has no popups)
    labels = gdf.set_index("GEOID")["county_label"]
    fg_pop = folium.FeatureGroup(name="County stats (click marker)", show=True)
    for _, row in panel.iterrows():
        geoid = str(row["county_fips5"])
        sub = gdf.loc[gdf["GEOID"] == geoid]
        if sub.empty:
            continue
        geom = sub.geometry.iloc[0]
        c = geom.centroid
        rs = row.copy()
        rs["county_label"] = labels.get(geoid, geoid)
        label = str(rs["county_label"])
        folium.CircleMarker(
            location=[c.y, c.x],
            radius=8,
            color="#222",
            weight=1,
            fill=True,
            fillColor="#ffffff",
            fillOpacity=0.85,
            popup=folium.Popup(popup_html(geoid, rs, acs_df), max_width=340),
            tooltip=f"{label} — click for table",
        ).add_to(fg_pop)
    fg_pop.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    m.fit_bounds([[b[1], b[0]], [b[3], b[2]]])

    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(OUT_HTML))
    print(f"Wrote {OUT_HTML}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        raise SystemExit(1)
