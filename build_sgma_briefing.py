"""Build lightweight SGMA equity briefing (Leaflet + Chart.js)."""
from __future__ import annotations

import json
from pathlib import Path

from build_sgma_equity_analysis import build_briefing_data

ROOT = Path(__file__).resolve().parent
OUT_DATA = ROOT / "vercel_site" / "sgma_briefing_data.js"
TEMPLATE = ROOT / "vercel_site" / "sgma_briefing.html"


def main() -> None:
    data = build_briefing_data()
    OUT_DATA.write_text(f"window.BRIEF = {json.dumps(data, separators=(',', ':'))};\n", encoding="utf-8")
    print(f"Wrote {OUT_DATA} ({OUT_DATA.stat().st_size // 1024} KB)")
    print(f"Open {TEMPLATE}")


if __name__ == "__main__":
    main()
