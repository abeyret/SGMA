"""
Download ACS 5-year county estimates from the Census public API (no key required).

Outputs:
  data/raw/socioeconomic/acs5_2014.csv
  data/raw/socioeconomic/acs5_2021.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "data/raw/socioeconomic"

# California SJV counties: full 5-digit county FIPS (state 06 + county)
TARGET_COUNTY_FIPS = frozenset(
    {
        "06019",
        "06029",
        "06031",
        "06039",
        "06047",
        "06077",
        "06099",
        "06107",
    }
)

# Census API variable codes
VAR_INCOME = "B19013_001E"
VAR_POVERTY_COUNT = "B17001_002E"
VAR_HISPANIC = "B03003_003E"
VAR_TOTAL_POP = "B01003_001E"

API_VARS = [VAR_INCOME, VAR_POVERTY_COUNT, VAR_HISPANIC, VAR_TOTAL_POP]


def census_url(year: int) -> str:
    """ACS 5-year estimates endpoint for a release year."""
    params = "&".join(
        [
            "get=" + ",".join(["NAME"] + API_VARS),
            "for=county:*",
            "in=state:06",
        ]
    )
    return f"https://api.census.gov/data/{year}/acs/acs5?{params}"


def fetch_json(url: str) -> list:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "SGMA-ECON30-download_acs/1.0"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)


def rows_to_csv_rows(data: list, target_fips: frozenset[str]) -> list[dict]:
    """Parse Census API JSON table; keep only requested county FIPS."""
    if not data or len(data) < 2:
        return []

    header = data[0]
    rows_out: list[dict] = []

    try:
        i_state = header.index("state")
        i_county = header.index("county")
    except ValueError as e:
        raise ValueError("Unexpected API response: missing state/county columns") from e

    var_idx = {v: header.index(v) for v in API_VARS if v in header}

    for row in data[1:]:
        state = str(row[i_state]).zfill(2)
        county = str(row[i_county]).zfill(3)
        fips5 = f"{state}{county}"
        if fips5 not in target_fips:
            continue

        name = row[header.index("NAME")] if "NAME" in header else ""

        record = {
            "NAME": name,
            "state_fips": state,
            "county_fips": county,
            "county_fips5": fips5,
            "B19013_001E": row[var_idx[VAR_INCOME]],
            "B17001_002E": row[var_idx[VAR_POVERTY_COUNT]],
            "B03003_003E": row[var_idx[VAR_HISPANIC]],
            "B01003_001E": row[var_idx[VAR_TOTAL_POP]],
        }
        rows_out.append(record)

    rows_out.sort(key=lambda r: r["county_fips5"])
    return rows_out


def write_csv(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not records:
        raise ValueError(f"No matching counties to write for {path.name}")
    fieldnames = list(records[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(records)


def download_year(year: int, out_dir: Path) -> Path:
    url = census_url(year)
    print(f"GET {url}")
    try:
        raw = fetch_json(url)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Census API HTTP error {e.code}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Request failed: {e}") from e

    records = rows_to_csv_rows(raw, TARGET_COUNTY_FIPS)
    out_path = out_dir / f"acs5_{year}.csv"
    write_csv(out_path, records)
    print(f"Wrote {len(records)} counties -> {out_path}")
    return out_path


def main() -> int:
    p = argparse.ArgumentParser(description="Download ACS 5-year county data from Census API.")
    p.add_argument(
        "--out-dir",
        type=Path,
        default=OUT_DIR,
        help=f"Output directory (default: {OUT_DIR})",
    )
    args = p.parse_args()

    years = [2014, 2021]
    try:
        for y in years:
            download_year(y, args.out_dir)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
