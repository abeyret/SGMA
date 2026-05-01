"""
Download Census of Agriculture farm operation counts by "area operated" size class
for San Joaquin Valley California counties (NASS QuickStats API).

Expects NASS_API_KEY in a .env file in the project root (or current working directory).

Output: data/raw/farm_size/farm_operations.json

Install: pip install python-dotenv
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
OUT_PATH = ROOT / "data/raw/farm_size/farm_operations.json"
NASS_GET = "https://quickstats.nass.usda.gov/api/api_GET/"

# SJV county 3-digit FIPS (state 06) — same as 5-digit 06xxx last three digits
SJV_COUNTY_CODES = ("019", "029", "031", "039", "047", "077", "099", "107")
CENSUS_YEARS = (2012, 2017, 2022)


def load_key() -> str:
    load_dotenv(ROOT / ".env")
    key = os.environ.get("NASS_API_KEY", "").strip()
    if not key:
        print("Set NASS_API_KEY in .env in the project root.", file=sys.stderr)
        raise SystemExit(1)
    return key


def fetch_census_farm_ops_by_size(
    key: str,
    year: int,
) -> list[dict]:
    """
    All California county records: FARM OPERATIONS, number of operations,
    domain = AREA OPERATED (size category in domaincat_desc).
    """
    params = {
        "key": key,
        "format": "JSON",
        "source_desc": "CENSUS",
        "year": str(year),
        "state_alpha": "CA",
        "agg_level_desc": "COUNTY",
        "commodity_desc": "FARM OPERATIONS",
        "statisticcat_desc": "OPERATIONS",
        "unit_desc": "OPERATIONS",
        "domain_desc": "AREA OPERATED",
    }
    url = NASS_GET + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "SGMA-ECON30-download_farm_size/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"NASS API HTTP {e.code}: {err[:500]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"NASS request failed: {e}") from e

    payload = json.loads(body)
    if "data" not in payload:
        if "error" in payload:
            raise RuntimeError(f"NASS API error: {payload.get('error')}")
        raise RuntimeError("Unexpected NASS response: no 'data' key")

    return list(payload["data"])


def filter_sjv_rows(rows: list[dict]) -> list[dict]:
    want = set(SJV_COUNTY_CODES)
    out: list[dict] = []
    for row in rows:
        code = str(row.get("county_code", "")).zfill(3)
        if code in want:
            st = str(row.get("state_fips_code", row.get("state_ansi", ""))).zfill(2)
            row = dict(row)
            row["county_fips5"] = f"{st}{code}"
            out.append(row)
    out.sort(
        key=lambda r: (
            r.get("year", ""),
            r.get("county_fips5", ""),
            r.get("domaincat_desc", ""),
        )
    )
    return out


def main() -> int:
    key = load_key()
    all_rows: list[dict] = []

    for year in CENSUS_YEARS:
        print(f"Downloading Census {year} FARM OPERATIONS by AREA OPERATED (CA counties)...")
        rows = fetch_census_farm_ops_by_size(key, year)
        sjv = filter_sjv_rows(rows)
        print(f"  Total CA county rows: {len(rows)}; SJV counties kept: {len(sjv)}")
        all_rows.extend(sjv)

    meta = {
        "dataset": "USDA NASS QuickStats — Census of Agriculture",
        "description": (
            "Farm operation counts by area-operated size class "
            "(commodity=FARM OPERATIONS, statistic=OPERATIONS, domain=AREA OPERATED)"
        ),
        "state": "CA",
        "county_codes_3digit": list(SJV_COUNTY_CODES),
        "census_years": list(CENSUS_YEARS),
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "record_count": len(all_rows),
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    output = {"meta": meta, "records": all_rows}

    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
        f.write("\n")

    print(f"Wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        raise SystemExit(1)
