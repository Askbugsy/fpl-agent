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
# "my-team" endpoint below.
#
# FPL retired plain email/password login at some point (the old
# users.premierleague.com login form no longer resolves at all) in
# favour of a PingOne-based OIDC flow - the same modernization that's
# why "Sign in with Google" works on the FPL site now. There's no
# login call this code can make on your behalf any more: a refresh
# token has to come from an already-logged-in browser session
# (extracted from localStorage - see the app's OIDC client below),
# which this code then exchanges for short-lived access tokens.
#
# main.py only uses this as a fallback, and only if FPL_REFRESH_TOKEN
# is set (meant to come from a GitHub Actions secret, never committed
# to the repo). The token is held in memory only for the duration of
# the exchange call - never logged, never written to disk or the
# database. Note some OIDC providers rotate the refresh token on every
# use, in which case a stored token only keeps working for one run and
# has to be re-extracted periodically - main.py doesn't currently have
# a way to write a rotated token back to the GitHub secret.

FPL_OIDC_AUTHORITY = "https://account.premierleague.com/as"
FPL_OIDC_CLIENT_ID = "bfcbaf69-aade-4c1b-8f00-c1cb8a193030"


def refresh_access_token(refresh_token: str) -> str:
    """
    Exchanges a refresh token for a fresh access token via FPL's OIDC
    provider, using standard OAuth2 discovery (fetching the provider's
    own .well-known/openid-configuration for its token endpoint,
    rather than a hardcoded URL that could go stale) plus the standard
    refresh_token grant. Returns just the access token string.
    """
    discovery = requests.get(f"{FPL_OIDC_AUTHORITY}/.well-known/openid-configuration", timeout=10)
    discovery.raise_for_status()
    token_endpoint = discovery.json()["token_endpoint"]

    resp = requests.post(
        token_endpoint,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": FPL_OIDC_CLIENT_ID,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def get_my_team(access_token: str, entry_id: int) -> dict:
    """
    Returns your own current saved picks, chips, and transfer/bank
    state - including for a gameweek whose deadline hasn't passed
    yet, unlike the public picks endpoint. Requires an access token
    from refresh_access_token(). FPL's authenticated endpoints use a
    custom X-API-Authorization header rather than the standard
    Authorization header.

    Shape matches get_entry_picks()'s 'picks' list closely enough to
    be saved the same way, but has no 'entry_history' block - points
    and rank for a gameweek that hasn't been scored yet don't exist.
    """
    resp = requests.get(
        f"{BASE_URL}/my-team/{entry_id}/",
        headers={"X-API-Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()
