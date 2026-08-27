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

    Its 'entry_history' block also has 'rank'/'overall_rank' fields,
    but those are a snapshot from when that gameweek was scored - they
    don't keep moving afterward as the wider standings shift. Use
    get_entry_summary() below for rank figures that match what the
    FPL app shows right now.
    """
    resp = requests.get(
        f"{BASE_URL}/entry/{entry_id}/event/{event_id}/picks/", timeout=10
    )
    resp.raise_for_status()
    return resp.json()


def get_entry_summary(entry_id: int) -> dict:
    """
    Returns the manager's current, live-updating standing - the same
    numbers the FPL app's "Team Overview" screen shows. In particular
    'summary_overall_rank' and 'summary_event_rank' keep tracking the
    live standings, unlike the picks endpoint's frozen-at-scoring-time
    entry_history.overall_rank/rank.
    """
    resp = requests.get(f"{BASE_URL}/entry/{entry_id}/", timeout=10)
    resp.raise_for_status()
    return resp.json()


# --- Authenticated access (optional) -----------------------------------
#
# Everything above is public - no login needed. But get_entry_picks()
# for a gameweek whose deadline hasn't passed yet returns a 404: FPL
# doesn't expose a manager's pending picks publicly (so rivals can't
# scout your team before deadline). The only way to see YOUR OWN
# pending squad - the one you can still edit - is the authenticated
# "my-team" endpoint below, which needs a real login.
#
# main.py only uses this as a fallback, and only if FPL_EMAIL/
# FPL_PASSWORD are set (meant to come from GitHub Actions secrets,
# never committed to the repo). Credentials are held in memory only
# for the duration of one login call - never logged, never written
# to disk or the database.

FPL_LOGIN_URL = "https://users.premierleague.com/accounts/login/"


def get_authenticated_session(email: str, password: str) -> requests.Session:
    """
    Logs into the real FPL site - the same login your browser uses -
    and returns a requests.Session carrying the resulting cookies.
    Needed only for get_my_team() below.

    FPL's login form returns HTTP 200 even on a wrong password (it's
    a normal form post, not an API), so success isn't judged by
    status code - it's judged by whether a session cookie actually
    came back.
    """
    session = requests.Session()
    session.post(
        FPL_LOGIN_URL,
        data={
            "login": email,
            "password": password,
            "app": "plfpl-web",
            "redirect_uri": "https://fantasy.premierleague.com/",
        },
        timeout=10,
    )
    if "sessionid" not in session.cookies.get_dict():
        raise RuntimeError(
            "FPL login did not return a session cookie - check that FPL_EMAIL/FPL_PASSWORD are correct."
        )
    return session


def get_my_team(session: requests.Session, entry_id: int) -> dict:
    """
    Returns your own current saved picks, chips, and transfer/bank
    state - including for a gameweek whose deadline hasn't passed
    yet, unlike the public picks endpoint. Requires an authenticated
    session from get_authenticated_session().

    Shape matches get_entry_picks()'s 'picks' list closely enough to
    be saved the same way, but has no 'entry_history' block - points
    and rank for a gameweek that hasn't been scored yet don't exist.
    """
    resp = session.get(f"{BASE_URL}/my-team/{entry_id}/", timeout=10)
    resp.raise_for_status()
    return resp.json()
