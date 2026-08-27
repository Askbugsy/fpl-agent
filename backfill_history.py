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
from db import save_player_gw_history

DELAY_BETWEEN_REQUESTS = 0.3  # seconds - be a reasonable API citizen


def backfill():
    print("Fetching player list...")
    data = get_bootstrap_static()
    players = data["elements"]
    print(f"Backfilling history for {len(players)} players. This will take a few minutes...\n")

    done, failed = 0, 0

    for i, player in enumerate(players, start=1):
        player_id = player["id"]
        try:
            summary = get_element_summary(player_id)
            save_player_gw_history(player_id, summary)
            done += 1

        except Exception as e:
            print(f"  Skipped player {player_id} ({player.get('web_name', '?')}): {e}")
            failed += 1

        if i % 50 == 0:
            print(f"  ...{i}/{len(players)} processed")

        time.sleep(DELAY_BETWEEN_REQUESTS)

    print(f"\nBackfill complete: {done} players saved, {failed} failed/skipped.")


if __name__ == "__main__":
    backfill()
