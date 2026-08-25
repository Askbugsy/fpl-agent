"""
main.py
-------
The entry point. Run this weekly (e.g. every Friday before deadline)
and it will:
  1. Fetch the current player data from the FPL API
  2. Save it into data/fpl.db (SQLite)
  3. Print the biggest form movers and top value picks to the console

Usage:
    python3 main.py

To also (re)build the visual dashboard from this data, run:
    python3 generate_dashboard.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from fpl_client import get_bootstrap_static
from db import save_snapshot, get_movers, get_top_value

POSITION_NAMES = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


def main():
    print("Fetching latest FPL data...")
    data = get_bootstrap_static()
    players = data["elements"]

    snapshot_date = save_snapshot(players)
    print(f"Saved snapshot for {snapshot_date}\n")

    movers = get_movers(limit=5)
    if movers:
        print("=== Biggest form risers since last snapshot ===")
        for m in movers:
            print(f"  {m['name']:<20} form {m['current_form']:>4} ({m['form_change']:+.1f})  £{m['price']}m")
        print()
    else:
        print("Only one snapshot saved so far - trends will appear from next run.\n")

    print("=== Top 10 value picks (points per game, per £1m) ===")
    for p in get_top_value(limit=10):
        pos = POSITION_NAMES.get(p["position"], "?")
        print(f"  {p['name']:<20} {pos:<4} £{p['price']:<5} value={p['value_score']}")


if __name__ == "__main__":
    main()
