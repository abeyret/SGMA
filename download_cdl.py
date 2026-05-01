"""
Download Cropland Data Layer county-level acreage summaries from the USDA CropScape
webservice (nassgeodata.gmu.edu). No API key required.

Uses GetCDLStat (format=csv): the service returns XML with a <returnURL> to the CSV.

Output (one CSV per CDL year, all SJV counties combined):
  data/raw/land_use/cdl_acreage_2012.csv
  data/raw/land_use/cdl_acreage_2014.csv
  data/raw/land_use/cdl_acreage_2018.csv
  data/raw/land_use/cdl_acreage_2022.csv
"""

from __future__ import annotations

import csv
import io
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "data/raw/land_use"

STAT_SERVICE = "https://nassgeodata.gmu.edu/axis2/services/CDLService/GetCDLStat"

# San Joaquin Valley counties — full 5-digit county FIPS (state 06 + county)
COUNTY_FIPS5 = (
    "06019",
    "06029",
    "06031",
    "06039",
    "06047",
    "06077",
    "06099",
    "06107",
)

CDL_YEARS = (2012, 2014, 2018, 2022)

RETURN_URL_RE = re.compile(r"<returnURL>\s*([^<\s]+)\s*</returnURL>", re.I)

USER_AGENT = "SGMA-ECON30-download_cdl/1.0"


def request_text(url: str, timeout: int = 180) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_return_url(xml_body: str) -> str:
    m = RETURN_URL_RE.search(xml_body)
    if not m:
        raise ValueError(
            "CropScape response did not contain <returnURL>. "
            f"First 400 chars: {xml_body[:400]!r}"
        )
    return m.group(1).strip()


def fetch_county_stat_csv(year: int, county_fips5: str) -> str:
    """GET county CDL statistics table as CSV text."""
    qs = urllib.parse.urlencode(
        {"year": str(year), "fips": county_fips5, "format": "csv"}
    )
    meta_url = f"{STAT_SERVICE}?{qs}"
    xml_body = request_text(meta_url)
    csv_url = parse_return_url(xml_body)
    return request_text(csv_url)


def parse_stat_csv(csv_text: str) -> list[dict[str, str]]:
    """Parse CDL stat CSV; normalize header names."""
    reader = csv.reader(io.StringIO(csv_text.strip()))
    rows = list(reader)
    if not rows:
        return []
    header = [h.strip() for h in rows[0]]
    # Typical: Value, Category, Count, Acreage (spacing varies)
    key_map = {}
    for i, h in enumerate(header):
        hl = h.lower()
        if hl == "value":
            key_map[i] = "cdl_value"
        elif hl == "category":
            key_map[i] = "category"
        elif hl == "count":
            key_map[i] = "pixel_count"
        elif "acreage" in hl:
            key_map[i] = "acreage"
        else:
            key_map[i] = h.replace(" ", "_").lower()

    out: list[dict[str, str]] = []
    for parts in rows[1:]:
        if not parts or all(not p.strip() for p in parts):
            continue
        row = {}
        for i, val in enumerate(parts):
            k = key_map.get(i, f"col_{i}")
            row[k] = val.strip()
        out.append(row)
    return out


def download_year(year: int, pause_s: float) -> list[dict[str, str]]:
    combined: list[dict[str, str]] = []
    for i, fips in enumerate(COUNTY_FIPS5):
        if i and pause_s > 0:
            time.sleep(pause_s)
        print(f"  {year} / county {fips} ...")
        try:
            raw = fetch_county_stat_csv(year, fips)
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"HTTP {e.code} for year={year} fips={fips}: {e.reason}"
            ) from e
        for rec in parse_stat_csv(raw):
            rec["cdl_year"] = str(year)
            rec["county_fips5"] = fips
            combined.append(rec)
    return combined


def write_csv(path: Path, records: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not records:
        raise ValueError(f"No records to write for {path}")
    all_keys: set[str] = set()
    for r in records:
        all_keys.update(r.keys())
    preferred = [
        "cdl_year",
        "county_fips5",
        "cdl_value",
        "category",
        "pixel_count",
        "acreage",
    ]
    fieldnames = [k for k in preferred if k in all_keys]
    fieldnames.extend(sorted(all_keys - set(fieldnames)))

    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in records:
            row = {k: r.get(k, "") for k in fieldnames}
            w.writerow(row)


def main() -> int:
    import argparse

    p = argparse.ArgumentParser(description="Download CDL county acreage from CropScape.")
    p.add_argument(
        "--pause",
        type=float,
        default=0.4,
        help="Seconds to sleep between county requests (default: 0.4). Use 0 to disable.",
    )
    args = p.parse_args()

    try:
        for year in CDL_YEARS:
            print(f"Downloading CDL stats for {year} ...")
            records = download_year(year, pause_s=args.pause)
            out_path = OUT_DIR / f"cdl_acreage_{year}.csv"
            write_csv(out_path, records)
            print(f"  Wrote {len(records)} rows -> {out_path}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
