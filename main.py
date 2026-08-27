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
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from fpl_client import get_bootstrap_static, get_element_summary, get_entry_picks, get_entry_summary, get_fixtures
from db import save_snapshot, save_squad_picks, save_teams, save_fixtures, save_entry_summary, save_gameweek_summary, save_player_gw_history, get_movers, get_top_value, get_captain_suggestions, get_chip_suggestions, get_transfer_suggestions, get_optimal_formation, get_next_deadline, get_watchlist, get_squad_alltime_player_ids
from config import TEAM_ID, MY_WATCHLIST

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

    # Work out which gameweek to pull your squad for. FPL keeps
    # is_current=True on a gameweek from kickoff all the way through
    # to the next one's kickoff - including the whole gap after it's
    # finished, while you're setting up transfers for next week. So
    # is_current alone would keep showing last week's (locked, stale)
    # squad even after you've made changes for the upcoming gameweek.
    # Prefer a gameweek that's actually live (is_current and not yet
    # finished) - during a live gameweek you want that one, not next
    # week's still-editable squad. Otherwise prefer is_next, since
    # that's the gameweek you can currently edit and whose picks
    # reflect your latest transfers. Fall back to is_current even if
    # finished only for the end-of-season case where there's no next
    # gameweek at all.
    current_gw = next((e["id"] for e in data["events"] if e["is_current"] and not e["finished"]), None)
    if current_gw is None:
        current_gw = next((e["id"] for e in data["events"] if e["is_next"]), None)
    if current_gw is None:
        current_gw = next((e["id"] for e in data["events"] if e["is_current"]), None)

    if current_gw:
        print(f"Fetching your squad (Team ID {TEAM_ID}) for Gameweek {current_gw}...")
        try:
            picks_data = get_entry_picks(TEAM_ID, current_gw)

            player_points = {p["id"]: p["event_points"] for p in players}
            save_squad_picks(TEAM_ID, current_gw, picks_data, player_points)

            live_summary = None
            try:
                live_summary = get_entry_summary(TEAM_ID)
            except Exception as e:
                # Rank still saves from entry_history below, just frozen
                # at whenever that gameweek was scored rather than live.
                print(f"Could not fetch live rank: {e}\n")

            save_entry_summary(TEAM_ID, current_gw, picks_data.get("entry_history", {}), live_summary)
            print("Squad saved.\n")
        except Exception as e:
            # Most commonly a 404: the public picks endpoint doesn't
            # expose a gameweek until its deadline has passed, even for
            # your own team - that's by design, so rivals can't scout
            # your squad early. There's no way around this without your
            # own login, which FPL's identity provider doesn't allow a
            # script to do on your behalf.
            print(f"Could not fetch squad for GW{current_gw}: {e}\n")

    # Keeps the "What Could Have Been" trajectories current: only the
    # players who've ever actually been in your squad (a season's worth
    # of transfers - a few dozen players at most), not a full ~700-player
    # backfill sweep. One element-summary call per player, lightly paced;
    # failures here never break the rest of the run.
    try:
        alltime_ids = get_squad_alltime_player_ids(TEAM_ID)
        if alltime_ids:
            print(f"Refreshing gameweek history for {len(alltime_ids)} squad player(s)...")
            for player_id in alltime_ids:
                try:
                    save_player_gw_history(player_id, get_element_summary(player_id))
                except Exception as e:
                    print(f"  Could not refresh history for player {player_id}: {e}")
                time.sleep(0.2)
            print("Done.\n")
    except Exception as e:
        print(f"Could not refresh squad player history: {e}\n")

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
    captain_picks = get_captain_suggestions(TEAM_ID, limit=5)
    for i, c in enumerate(captain_picks):
        role = " (C)" if i == 0 else " (VC)" if i == 1 else ""
        venue = "H" if c["is_home"] else "A" if c["is_home"] is not None else "?"
        opp = c["opponent"] or "?"
        diff = c["difficulty"] or "?"
        print(f"  {c['name']:<20}{role:<5} vs {opp} ({venue}, FDR {diff})  form={c['form']}  score={c['score']}")
    if len(captain_picks) >= 2:
        print(f"  If {captain_picks[0]['name']} doesn't register a score, the armband passes to {captain_picks[1]['name']}.")

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
        if formation.get("suggested_vice_captain"):
            print(f"  Suggested vice-captain: {formation['suggested_vice_captain']['name']}")
        print("  All formations compared:")
        for c in formation["all_formations"]:
            print(f"    {c['formation']:<8} {c['projected_total']} pts")
    else:
        print(f"  {formation.get('reason', 'Not enough data yet.')}")

    deadline = get_next_deadline()
    if deadline:
        print(f"\n=== Next deadline ===")
        print(f"  Gameweek {deadline['gameweek']}: {deadline['deadline_time']}")

    print("\n=== Watchlist ===")
    watchlist = get_watchlist(MY_WATCHLIST)
    for pos, picks in watchlist.items():
        dd = picks["data_driven"]
        if dd and dd["form_change"] is not None:
            print(f"  {pos} - Data-driven: {dd['name']} (form {dd['form_change']:+.1f}, price {dd['price_change']:+.1f}m)")
        else:
            print(f"  {pos} - Data-driven: {dd['name'] if dd else 'n/a'}")
        m = picks["manual"]
        if m and m.get("found") is False:
            print(f"       Your pick: '{m['name']}' not found - check the name in config.py")
        elif m:
            print(f"       Your pick: {m['name']}")
        else:
            print(f"       Your pick: not set")


if __name__ == "__main__":
    main()
