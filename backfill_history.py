"""
backfill_history.py
--------------------
A ONE-TIME job (or re-run occasionally, e.g. once a season) - not
part of the weekly pipeline. Pulls every player's full history:
  - This season, gameweek by gameweek
  - Past seasons, as end-of-season summaries

This is one API call PER PLAYER (~700 of them), so it's slow and
deliberately paced to avoid hammering the FPL API. Expect this to
take several minutes.

Usage:
    python3 backfill_history.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from fpl_client import get_bootstrap_static, get_element_summary
from db import get_connection

CURRENT_SEASON = "2026/27"
DELAY_BETWEEN_REQUESTS = 0.3  # seconds - be a reasonable API citizen


def backfill():
    print("Fetching player list...")
    data = get_bootstrap_static()
    players = data["elements"]
    print(f"Backfilling history for {len(players)} players. This will take a few minutes...\n")

    conn = get_connection()
    done, failed = 0, 0

    for i, player in enumerate(players, start=1):
        player_id = player["id"]
        try:
            summary = get_element_summary(player_id)

            # This season, gameweek by gameweek
            gw_rows = [
                (
                    player_id, CURRENT_SEASON, gw["round"], gw["total_points"],
                    gw["minutes"], gw["goals_scored"], gw["assists"],
                    gw["value"] / 10,
                )
                for gw in summary.get("history", [])
            ]

            # Past seasons, one summary row each (gameweek = NULL)
            past_rows = [
                (
                    player_id, season["season_name"], None, season["total_points"],
                    season["minutes"], season["goals_scored"], season["assists"],
                    None,  # no single price for a whole past season
                )
                for season in summary.get("history_past", [])
            ]

            conn.executemany(
                """INSERT OR REPLACE INTO player_gw_history
                   (player_id, season, gameweek, total_points, minutes,
                    goals_scored, assists, price)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                gw_rows + past_rows,
            )
            conn.commit()
            done += 1

        except Exception as e:
            print(f"  Skipped player {player_id} ({player.get('web_name', '?')}): {e}")
            failed += 1

        if i % 50 == 0:
            print(f"  ...{i}/{len(players)} processed")

        time.sleep(DELAY_BETWEEN_REQUESTS)

    conn.close()
    print(f"\nBackfill complete: {done} players saved, {failed} failed/skipped.")


if __name__ == "__main__":
    backfill()
