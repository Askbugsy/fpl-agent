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

import os
import subprocess

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
# database.
#
# Confirmed live: PingOne rotates the refresh token on every exchange
# and invalidates the old one - a token that worked fine on one run
# got a 400 Bad Request on the very next. refresh_access_token() below
# returns any newly-issued refresh token alongside the access token,
# and rotate_refresh_token_secret() persists it back to the GitHub
# secret via the gh CLI, so a stored token keeps working indefinitely
# instead of needing manual re-extraction after every single use.

FPL_OIDC_AUTHORITY = "https://account.premierleague.com/as"
FPL_OIDC_CLIENT_ID = "bfcbaf69-aade-4c1b-8f00-c1cb8a193030"


def refresh_access_token(refresh_token: str) -> tuple[str, str | None]:
    """
    Exchanges a refresh token for a fresh access token via FPL's OIDC
    provider, using standard OAuth2 discovery (fetching the provider's
    own .well-known/openid-configuration for its token endpoint,
    rather than a hardcoded URL that could go stale) plus the standard
    refresh_token grant.

    Returns (access_token, rotated_refresh_token) - the second element
    is None unless the provider issued a genuinely new refresh token in
    this same response (some do this on every exchange, invalidating
    the one that was just used). Callers should persist a non-None
    rotated_refresh_token, or the next run will fail with the same
    now-dead token.
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
    data = resp.json()

    new_refresh_token = data.get("refresh_token")
    rotated = new_refresh_token if new_refresh_token and new_refresh_token != refresh_token else None
    return data["access_token"], rotated


def rotate_refresh_token_secret(new_refresh_token: str) -> None:
    """
    Persists a rotated refresh token back to this repo's
    FPL_REFRESH_TOKEN GitHub Actions secret, via the gh CLI (already
    installed on GitHub-hosted runners) - so the next run doesn't fail
    with the old, now-invalidated token.

    Needs GH_SECRETS_PAT: a fine-grained personal access token scoped
    to just this one repo, with only its "Secrets" permission set to
    read/write - stored as its own GitHub secret, separate from
    FPL_REFRESH_TOKEN. Silently does nothing outside of GitHub Actions
    or without that token set - this is a nice-to-have that should
    never break the actual data pull if it fails.

    The new token is piped in via stdin, never passed as a CLI argument
    or interpolated into workflow YAML, so it's never written to the
    process list or a step's logged command line. Failures are caught
    and reported, never raised - the run this token was fetched for has
    already succeeded regardless of whether saving it for next time
    works.
    """
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    pat = os.environ.get("GH_SECRETS_PAT")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not pat or not repo:
        print("GH_SECRETS_PAT not set - can't persist the rotated refresh token; "
              "the next run will need a fresh one extracted manually.")
        return

    try:
        subprocess.run(
            ["gh", "secret", "set", "FPL_REFRESH_TOKEN", "--repo", repo],
            input=new_refresh_token,
            text=True,
            check=True,
            env={**os.environ, "GH_TOKEN": pat},
            capture_output=True,
        )
        print("Rotated FPL_REFRESH_TOKEN saved for next time.")
    except Exception as e:
        print(f"Could not save the rotated refresh token: {e}")


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
