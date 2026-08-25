"""
fpl_client.py
-------------
Thin wrapper around the official Fantasy Premier League API.
No API key needed - it's public. Two endpoints matter for us:

1. bootstrap-static  -> every player, team, and "current gameweek" info
2. fixtures           -> the full season's fixture list (past + future)

Docs aren't official, but the community has reverse-engineered this
well: https://github.com/vaastav/Fantasy-Premier-League/wiki
"""

import requests

BASE_URL = "https://fantasy.premierleague.com/api"


def get_bootstrap_static() -> dict:
    """
    Returns the big 'everything' payload: all players (elements),
    all teams, all gameweeks (events), and scoring rules.
    This is the endpoint you'll call most often.
    """
    resp = requests.get(f"{BASE_URL}/bootstrap-static/", timeout=10)
    resp.raise_for_status()  # throws an error if the request failed
    return resp.json()


def get_fixtures() -> list[dict]:
    """
    Returns every fixture for the season, with a 'difficulty' rating
    (1-5) for both the home and away team, provided by FPL itself.
    """
    resp = requests.get(f"{BASE_URL}/fixtures/", timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_player_history(player_id: int) -> dict:
    """
    Returns one player's full gameweek-by-gameweek history for this
    season (points, minutes, goals, etc. per match) plus their
    history from past seasons. Same as get_element_summary below -
    kept for backwards compatibility with earlier code.
    """
    return get_element_summary(player_id)


def get_element_summary(player_id: int) -> dict:
    """
    Returns one player's full gameweek-by-gameweek history for this
    season ('history'), plus a summary of their previous seasons
    ('history_past') - this is what powers the one-time historical
    backfill.
    """
    resp = requests.get(f"{BASE_URL}/element-summary/{player_id}/", timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_entry_picks(entry_id: int, event_id: int) -> dict:
    """
    Returns a manager's squad (15 players, formation, captain/vice)
    for one specific gameweek. This is what 'my squad' pulls from.
    """
    resp = requests.get(
        f"{BASE_URL}/entry/{entry_id}/event/{event_id}/picks/", timeout=10
    )
    resp.raise_for_status()
    return resp.json()
