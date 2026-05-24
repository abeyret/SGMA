"""Build client-grade SGMA equity briefing slideshow."""
from __future__ import annotations

import json
from pathlib import Path

from build_sgma_equity_analysis import build_deck_data

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "vercel_site" / "sgma_equity_slideshow_template.html"
OUT = ROOT / "vercel_site" / "sgma_equity_slideshow.html"
MARKER = "<!-- DECK_DATA -->"


def main() -> None:
    deck = build_deck_data()
    html = TEMPLATE.read_text(encoding="utf-8")
    payload = f"<script>window.DECK_DATA = {json.dumps(deck, separators=(',', ':'))};</script>"
    html = html.replace(MARKER, payload)
    OUT.write_text(html, encoding="utf-8")
    json_path = ROOT / "vercel_site" / "sgma_equity_deck_data.json"
    json_path.write_text(json.dumps(deck, indent=2), encoding="utf-8")
    n = deck["meta"]["n_slides"]
    print(f"Wrote {OUT}")
    print(f"Wrote {json_path}")
    print(f"{n} slides · ~{deck['meta']['duration_min']} min · {len(deck['gsps'])} GSPs · {deck['governance']['n_gsa']} GSAs")


if __name__ == "__main__":
    main()
