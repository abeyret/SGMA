"""
Export DWR TRE Altamira SAR subsidence rasters for San Joaquin Valley ONLY.

Source: https://gis.water.ca.gov/arcgisimg/rest/services/SAR

Products:
  - Annual rate (Dec–Dec epochs): displacement during each 12-month period (ft)
  - Cumulative (Total_Since_20150613): shared baseline since 2015-06-13 — slider-safe

Outputs:
  outputs/subsidence/yearly/       PNG + metadata per annual epoch
  outputs/subsidence/cumulative/   PNG + metadata per cumulative snapshot
  outputs/subsidence/previews/     Latest preview
  outputs/subsidence/manifest.json Website + thesis manifest
  vercel_site/subsidence/          Copied web assets
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import requests

ROOT = Path(__file__).resolve().parent
SAR_BASE = "https://gis.water.ca.gov/arcgisimg/rest/services"
SUBBASINS = ROOT / "data/raw/boundaries/bulletin118_subbasins.geojson"
COUNTIES = ROOT / "vercel_site/thesis_counties.geojson"
OUT = ROOT / "outputs/subsidence"
VERCEL_OUT = ROOT / "vercel_site/subsidence"

# Dec-anchored layers only (avoid monthly duplicates)
ANNUAL_RE = re.compile(
    r"Vertical_Displacement_TRE_ALTAMIRA_Annual_Rate_(\d{4})1201_(\d{4})1201"
)
CUMULATIVE_RE = re.compile(
    r"Vertical_Displacement_TRE_ALTAMIRA_Total_Since_20150613_(\d{4})1201"
)

EXPORT_SIZE = "1000,750"  # px — balance quality vs file size
PNG_KW = dict(bboxSR=4326, imageSR=3857, format="png", f="image", interpolation="RSP_BilinearSampling")


def sjv_bbox() -> tuple[float, float, float, float]:
    """Clip to 8 SJV counties (not full Bulletin 118 basin extent)."""
    path = COUNTIES if COUNTIES.is_file() else SUBBASINS
    g = gpd.read_file(path)
    if path == SUBBASINS:
        g = g[g["Basin_Name"].astype(str).str.contains("SAN JOAQUIN VALLEY", na=False)]
    xmin, ymin, xmax, ymax = g.total_bounds
    pad = 0.012
    return (xmin - pad, ymin - pad, xmax + pad, ymax + pad)


def list_sar_services() -> list[str]:
    cache = OUT / "_sar_services.json"
    if cache.is_file():
        return json.loads(cache.read_text(encoding="utf-8"))
    for attempt in range(3):
        try:
            r = requests.get(f"{SAR_BASE}/SAR?f=pjson", timeout=90)
            r.raise_for_status()
            names = [s["name"] for s in r.json().get("services", []) if s.get("type") == "ImageServer"]
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(names), encoding="utf-8")
            return names
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    return []


def export_png(service_name: str, bbox: str, dest: Path) -> dict:
    url = f"{SAR_BASE}/{service_name}/ImageServer/exportImage"
    params = {"bbox": bbox, "size": EXPORT_SIZE, **PNG_KW}
    dest.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, timeout=180)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    time.sleep(0.4)  # gentle on DWR server
    return {
        "service": service_name,
        "url": url,
        "file": str(dest.relative_to(ROOT)).replace("\\", "/"),
        "bytes": len(resp.content),
        "export_sec": round(time.time() - t0, 2),
    }


def pick_layers(services: list[str], year_min: int, year_max: int) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    annual, cumulative = [], []
    for name in services:
        m = ANNUAL_RE.search(name)
        if m:
            y0, y1 = int(m.group(1)), int(m.group(2))
            if y1 == y0 + 1 and year_min <= y0 <= year_max:
                annual.append((y0, name))
            continue
        m = CUMULATIVE_RE.search(name)
        if m:
            y = int(m.group(1))
            if year_min <= y <= year_max:
                cumulative.append((y, name))
    return sorted(set(annual)), sorted(set(cumulative))


def build_manifest(
    bbox: tuple[float, float, float, float],
    annual_exports: list[dict],
    cumulative_exports: list[dict],
) -> dict:
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": f"{SAR_BASE}/SAR",
        "provider": "DWR TRE Altamira InSAR via ArcGIS ImageServer",
        "study_area": "8 SJV counties (Fresno, Kern, Kings, Madera, Merced, San Joaquin, Stanislaus, Tulare)",
        "bbox_wgs84": {"xmin": bbox[0], "ymin": bbox[1], "xmax": bbox[2], "ymax": bbox[3]},
        "units": "feet (vertical displacement; annual layers = displacement during epoch)",
        "baseline_note": (
            "Cumulative layers share reference date 2015-06-13 (Total_Since_20150613). "
            "Safe for direct time-slider comparison. Annual rate layers measure displacement "
            "during each Dec–Dec period only — not additive across different reference frames."
        ),
        "annual_rate_layers": annual_exports,
        "cumulative_layers": cumulative_exports,
        "slider_recommendation": "Use cumulative_layers for sinking time-lapse; annual_rate_layers for year-specific velocity.",
        "arcgis_export_template": (
            f"{SAR_BASE}/{{service}}/ImageServer/exportImage"
            "?bbox={xmin},{ymin},{xmax},{ymax}&bboxSR=4326&size={width},{height}&imageSR=3857&format=png&f=image"
        ),
    }


def run(year_min: int = 2016, year_max: int = 2024, force: bool = False) -> Path:
    if not SUBBASINS.is_file():
        raise FileNotFoundError(f"Missing {SUBBASINS}. Run sgma_research boundary acquisition first.")

    bbox = sjv_bbox()
    bbox_str = ",".join(str(x) for x in bbox)
    print(f"SJV bbox: {bbox_str}")

    services = list_sar_services()
    annual, cumulative = pick_layers(services, year_min, year_max)
    print(f"Annual rate epochs: {[y for y, _ in annual]}")
    print(f"Cumulative snapshots: {[y for y, _ in cumulative]}")

    annual_exports, cumulative_exports = [], []

    for year, svc in annual:
        dest = OUT / "yearly" / f"annual_rate_{year}_{year+1}.png"
        if dest.is_file() and not force:
            print(f"  skip annual {year} (cached)")
            annual_exports.append({"year": year, "epoch": f"{year}-12-01 to {year+1}-12-01", "file": str(dest.relative_to(ROOT)).replace("\\", "/"), "service": svc})
            continue
        print(f"  export annual {year}…")
        meta = export_png(svc, bbox_str, dest)
        annual_exports.append({"year": year, "epoch": f"{year}-12-01 to {year+1}-12-01", **meta})

    for year, svc in cumulative:
        dest = OUT / "cumulative" / f"cumulative_since2015_{year}.png"
        if dest.is_file() and not force:
            print(f"  skip cumulative {year} (cached)")
            cumulative_exports.append({"year": year, "baseline": "2015-06-13", "file": str(dest.relative_to(ROOT)).replace("\\", "/"), "service": svc})
            continue
        print(f"  export cumulative {year}…")
        meta = export_png(svc, bbox_str, dest)
        cumulative_exports.append({"year": year, "baseline": "2015-06-13", **meta})

    manifest = build_manifest(bbox, annual_exports, cumulative_exports)
    manifest_path = OUT / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Copy to vercel_site for deploy
    import shutil
    VERCEL_OUT.mkdir(parents=True, exist_ok=True)
    for sub in ("yearly", "cumulative", "previews"):
        src_dir = OUT / sub
        src_dir.mkdir(parents=True, exist_ok=True)
        dst_dir = VERCEL_OUT / sub
        dst_dir.mkdir(parents=True, exist_ok=True)
        for f in src_dir.glob("*.png"):
            shutil.copy2(f, dst_dir / f.name)
    shutil.copy2(manifest_path, VERCEL_OUT / "manifest.json")

    if cumulative_exports:
        latest = sorted(cumulative_exports, key=lambda x: x["year"])[-1]
        preview_src = ROOT / latest["file"].replace("/", "\\") if "/" in latest["file"] else ROOT / latest["file"]
        if not preview_src.is_file():
            preview_src = OUT / "cumulative" / f"cumulative_since2015_{latest['year']}.png"
        preview_dst = OUT / "previews" / "latest_cumulative.png"
        vercel_preview = VERCEL_OUT / "previews" / "latest_cumulative.png"
        preview_dst.parent.mkdir(parents=True, exist_ok=True)
        vercel_preview.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(preview_src, preview_dst)
        shutil.copy2(preview_src, vercel_preview)

    print(f"Done: {manifest_path} ({len(annual_exports)} annual, {len(cumulative_exports)} cumulative)")
    return manifest_path


def main() -> int:
    p = argparse.ArgumentParser(description="Export DWR SAR subsidence rasters for SJV.")
    p.add_argument("--year-min", type=int, default=2016)
    p.add_argument("--year-max", type=int, default=2024)
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    try:
        run(year_min=args.year_min, year_max=args.year_max, force=args.force)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
