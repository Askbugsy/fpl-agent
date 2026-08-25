"""
main.py
-------
The entry point. Run this weekly (e.g. every Tuesday) and it will:
  1. Fetch the current player data from the FPL API
  2. Save it into data/fpl.db (SQLite)
  3. Fetch your squad (config.TEAM_ID) for the current gameweek
  4. Print the biggest form movers and top value picks to the console

Usage:
    python3 main.py

To also (re)build the visual dashboard from this data, run:
    python3 generate_dashboard.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from fpl_client import get_bootstrap_static, get_entry_picks, get_fixtures
from db import save_snapshot, save_squad_picks, save_teams, save_fixtures, save_entry_summary, save_gameweek_summary, get_movers, get_top_value, get_captain_suggestions, get_chip_suggestions, get_transfer_suggestions, get_optimal_formation
from config import TEAM_ID

POSITION_NAMES = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


def main():
    print("Fetching latest FPL data...")
    data = get_bootstrap_static()
    players = data["elements"]

    snapshot_date = save_snapshot(players)
    print(f"Saved snapshot for {snapshot_date}\n")

    save_teams(data["teams"])
    fixtures = get_fixtures()
    save_fixtures(fixtures)
    save_gameweek_summary(data["events"])
    print(f"Saved {len(fixtures)} fixtures with difficulty ratings.\n")

    # Work out the current gameweek from the bootstrap data
    current_gw = next((e["id"] for e in data["events"] if e["is_current"]), None)
    if current_gw is None:
        current_gw = next((e["id"] for e in data["events"] if e["is_next"]), None)

    if current_gw:
        print(f"Fetching your squad (Team ID {TEAM_ID}) for Gameweek {current_gw}...")
        try:
            picks_data = get_entry_picks(TEAM_ID, current_gw)
            player_points = {p["id"]: p["event_points"] for p in players}
            save_squad_picks(TEAM_ID, current_gw, picks_data, player_points)
            save_entry_summary(TEAM_ID, current_gw, picks_data.get("entry_history", {}))
            print("Squad saved.\n")
        except Exception as e:
            # Picks for the current gameweek aren't published until it's
            # actually started - don't let this crash the whole run.
            print(f"Could not fetch squad picks yet: {e}\n")

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

    print("\n=== Captain suggestions (form x fixture favourability) ===")
    for c in get_captain_suggestions(TEAM_ID, limit=5):
        venue = "H" if c["is_home"] else "A" if c["is_home"] is not None else "?"
        opp = c["opponent"] or "?"
        diff = c["difficulty"] or "?"
        print(f"  {c['name']:<20} vs {opp} ({venue}, FDR {diff})  form={c['form']}  score={c['score']}")

    print("\n=== Chip timing ===")
    chips = get_chip_suggestions(TEAM_ID)
    print(f"  Triple Captain / Bench Boost: {chips['triple_captain_bench_boost']}")
    print(f"  Free Hit: {chips['free_hit']}")

    print("\n=== Transfer suggestions ===")
    transfers = get_transfer_suggestions(TEAM_ID)
    if not transfers:
        print("  No players flagged - either everything's stable, or only one snapshot exists so far.")
    for t in transfers:
        print(f"  [{t['urgency']}] {t['name']} ({POSITION_NAMES.get(t['position'], '?')}) "
              f"- form {'-' + str(t['form_drop']) if t['form_drop'] else 'stable'}, "
              f"price {'-£' + str(t['price_drop']) + 'm' if t['price_drop'] else 'stable'}. "
              f"Budget if sold: £{t['budget_available']}m")
        for c in t["candidates"]:
            print(f"      -> {c['name']:<18} £{c['price']}m  form={c['form']}  value={c['value_score']}")

    print("\n=== Recommended formation for upcoming fixtures ===")
    formation = get_optimal_formation(TEAM_ID)
    if formation.get("formation"):
        print(f"  Best shape: {formation['formation']}  (projected {formation['projected_total']} pts)")
        print(f"  Suggested captain: {formation['suggested_captain']['name']}")
        print("  All formations compared:")
        for c in formation["all_formations"]:
            print(f"    {c['formation']:<8} {c['projected_total']} pts")
    else:
        print(f"  {formation.get('reason', 'Not enough data yet.')}")


if __name__ == "__main__":
    main()
