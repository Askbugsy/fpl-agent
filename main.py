"""
main.py
-------
The entry point. Run this weekly (e.g. every Friday before deadline)
and it will:
  1. Fetch the current player data from the FPL API
  2. Save a snapshot for today
  3. Compare it against last week's snapshot to show trends
  4. Print a top-10 value picks table

Usage:
    python3 main.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from fpl_client import get_bootstrap_static
from storage import save_snapshot, load_all_snapshots
from analyze import compute_trends, top_value_players

POSITION_NAMES = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


def main():
    print("Fetching latest FPL data...")
    data = get_bootstrap_static()
    players = data["elements"]

    snapshot_path = save_snapshot(players)
    print(f"Saved snapshot -> {snapshot_path}\n")

    snapshots = load_all_snapshots()

    trends = compute_trends(snapshots)
    if trends:
        print("=== Biggest form risers since last snapshot ===")
        risers = sorted(trends, key=lambda t: t["form_change"], reverse=True)[:5]
        for t in risers:
            print(f"  {t['name']:<20} form {t['current_form']:>4} ({t['form_change']:+.1f})  £{t['price']}m")
        print()
    else:
        print("Only one snapshot saved so far - trends will appear from next run.\n")

    latest_date = sorted(snapshots.keys())[-1]
    latest = snapshots[latest_date]

    print("=== Top 10 value picks (points per game, per £1m) ===")
    for p in top_value_players(latest)[:10]:
        pos = POSITION_NAMES.get(p["position"], "?")
        print(f"  {p['name']:<20} {pos:<4} £{p['price']:<5} value={p['value_score']}")


if __name__ == "__main__":
    main()
