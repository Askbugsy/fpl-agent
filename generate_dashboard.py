"""
generate_dashboard.py
----------------------
Builds a single, self-contained dashboard.html - no server, no
build step, just a file you open (or that GitHub Pages hosts for
you). Charts are drawn with Chart.js, loaded from a CDN.

Layout: a persistent deadline countdown sits above a tabbed
interface (Today / Squad / Moves / Explore) - only one section
visible at a time, since a single long scroll of every section
stacked full-width gets unwieldy fast on a phone.

Run this after main.py in the workflow; it reads straight from
data/fpl.db.
"""

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from db import get_connection, get_movers, get_top_value, get_latest_squad, get_captain_suggestions, get_chip_suggestions, get_transfer_suggestions, get_all_player_profiles, get_optimal_formation, get_manager_stats, get_next_deadline, get_watchlist, has_form_trend_baseline, get_squad_history, get_what_if_scenarios, get_prior_season_summary, STATUS_LABELS
from config import TEAM_ID, MY_WATCHLIST, CLAUDE_CHAT_URL

OUTPUT_PATH = Path(__file__).parent / "docs" / "index.html"
POSITION_NAMES = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}

# The "premierleague25" segment is a Premier League-side asset bucket, not
# something exposed by any bootstrap-static field - confirmed against the
# real FPL site's own image URL. It's bumped by Premier League itself when
# they refresh player photos for kit changes/transfers/a new season, so if
# photos start showing stale kits again, check the real site's image URL
# again and update this constant to match. 250x250 (and larger) return
# HTTP 403 on the real CDN - 110x140 is the size actually served.
PLAYER_PHOTO_BASE = "https://resources.premierleague.com/premierleague25/photos/players"

# Fallback bucket for players who don't have a photo in the current-season
# bucket above. Can serve a stale, wrong-kit photo for players who've since
# had their current-season photo published, so it's only used as a second
# choice, never first.
PLAYER_PHOTO_LEGACY_BASE = "https://resources.premierleague.com/premierleague/photos/players"

# Shown in place of a player photo that fails to load on both buckets above
# - some players simply don't have a headshot on Premier League's CDN yet
# at all. A plain silhouette matching the light theme, inlined as base64 so
# it never needs its own network request and can't itself fail to load.
PLAYER_PHOTO_FALLBACK = (
    "data:image/svg+xml;base64,"
    "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxMDAgMTAwIj48cmVjdCB3aWR0aD0iMTAwIiBoZWlnaHQ9IjEwMCIgZmlsbD0iI0YzRTNENiIvPjxjaXJjbGUgY3g9IjUwIiBjeT0iMzgiIHI9IjE4IiBmaWxsPSIjOEE4MDc0Ii8+PHBhdGggZD0iTTIwIDkwYzAtMjIgMTMtMzYgMzAtMzZzMzAgMTQgMzAgMzYiIGZpbGw9IiM4QTgwNzQiLz48L3N2Zz4="
)

# Faint white pitch markings (touchlines, halfway line + center circle, both
# penalty boxes) drawn as an inline SVG and layered as a second background
# image behind the player cards, so every pitch view reads as an actual
# football pitch rather than a plain green rectangle. Kept low-opacity since
# the player cards themselves are near-opaque and sit on top.
_PITCH_MARKINGS_SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 150">
<g fill="none" stroke="#ffffff" stroke-opacity="0.35" stroke-width="1">
<rect x="3" y="3" width="94" height="144"/>
<line x1="3" y1="75" x2="97" y2="75"/>
<circle cx="50" cy="75" r="13"/>
<rect x="24" y="3" width="52" height="20"/>
<rect x="38" y="3" width="24" height="8"/>
<path d="M 38 23 A 13 13 0 0 1 62 23"/>
<rect x="24" y="127" width="52" height="20"/>
<rect x="38" y="139" width="24" height="8"/>
<path d="M 38 127 A 13 13 0 0 0 62 127"/>
</g>
<g fill="#ffffff" fill-opacity="0.35">
<circle cx="50" cy="75" r="0.8"/>
<circle cx="50" cy="16" r="0.8"/>
<circle cx="50" cy="134" r="0.8"/>
</g>
</svg>'''
PITCH_MARKINGS_DATA_URI = "data:image/svg+xml;base64," + base64.b64encode(_PITCH_MARKINGS_SVG.encode()).decode()


def get_latest_full_table() -> list[dict]:
    conn = get_connection()
    conn.row_factory = None
    latest_date = conn.execute("SELECT MAX(snapshot_date) FROM player_snapshots").fetchone()[0]
    rows = conn.execute(
        """SELECT player_id, name, team, position, price, total_points, form,
                  points_per_game, selected_by_percent
           FROM player_snapshots WHERE snapshot_date = ?
           ORDER BY total_points DESC""",
        (latest_date,),
    ).fetchall()
    conn.close()
    cols = ["player_id", "name", "team", "position", "price", "total_points", "form", "points_per_game", "selected_by_percent"]
    return [dict(zip(cols, r)) for r in rows], latest_date


def player_link(player_id: int, name: str) -> str:
    """Wraps a player's name in a clickable span that opens the shared profile popup."""
    return f'<span class="player-link" onclick="openProfile({player_id})">{name}</span>'


def captain_role_tag(i: int) -> str:
    if i == 0:
        return ' <span class="role-tag role-captain">Captain</span>'
    if i == 1:
        return ' <span class="role-tag role-vice">Vice-Captain</span>'
    return ""


def render_watchlist_pick(pick: dict) -> str:
    if not pick:
        return '<span class="muted">not set</span>'
    if pick.get("found") is False:
        return f'<span class="text-bad">"{pick["name"]}" not found &mdash; check config.py</span>'

    # Form and price change are independent signals (form only moves once a new
    # gameweek is scored; price moves day to day with transfer activity), so
    # each renders only when its own baseline actually exists - not gated on
    # the other one being available.
    trend_parts = []
    if pick.get("form_change") is not None:
        fc = pick["form_change"]
        fc_cls = "text-good" if fc > 0 else "text-bad" if fc < 0 else "muted"
        trend_parts.append(f'<span class="{fc_cls}">form {fc:+.1f}</span>')
    if pick.get("price_change") is not None:
        pc = pick["price_change"]
        pc_cls = "text-good" if pc > 0 else "text-bad" if pc < 0 else "muted"
        trend_parts.append(f'<span class="{pc_cls}">price {pc:+.1f}m</span>')
    trend = f' {" ".join(trend_parts)}' if trend_parts else ""

    # A watchlist exists to decide whether to actually buy someone, not
    # just to watch their price - so availability and fixture come
    # through loudly (same status-dot convention as the squad pitch),
    # alongside the season-long numbers a single week's form can't show.
    status_dot = ""
    if pick.get("status") and pick["status"] != "a":
        dot_cls = "status-amber" if pick["status"] == "d" else "status-red"
        chance = pick.get("chance_of_playing")
        title = pick["status_label"] + (f", {chance}% chance" if chance is not None else "")
        if pick.get("news"):
            title += f" &mdash; {pick['news']}"
        status_dot = f' <span class="status-badge {dot_cls}" title="{title}">&#9679;</span>'

    fixture = f"vs {pick['opponent']} (FDR {pick['difficulty']})" if pick.get("opponent") else "no upcoming fixture"

    return (
        f'{player_link(pick["player_id"], pick["name"])}{status_dot} &mdash; £{pick["price"]}m{trend}'
        f'<br><span class="subtitle">{pick["points_per_game"]} PPG &middot; {pick["total_points"]} pts &middot; '
        f'{pick["selected_by_percent"]}% owned</span>'
        f'<br><span class="subtitle">Next: {fixture}</span>'
        f'{_render_swap_line(pick)}'
    )


def _render_swap_line(pick: dict) -> str:
    """
    The other half of "should I buy this": who in your actual squad
    you'd realistically drop for them, budget-checked against their
    real selling price (post sell-on tax) plus your bank - see
    get_watchlist's swap_candidate() for the underlying logic.
    """
    swap = pick.get("swap_suggestion")
    if not swap:
        return ""
    if swap.get("already_owned"):
        return '<br><span class="subtitle">Already in your squad</span>'
    if swap.get("affordable"):
        return (
            f'<br><span class="subtitle text-good">Swap idea: OUT {swap["out_name"]} '
            f'(form {swap["out_form"]}) &rarr; IN {pick["name"]} &mdash; '
            f'£{swap["budget_after"]}m left in the bank</span>'
        )
    return (
        f'<br><span class="subtitle">Need &pound;{swap["shortfall"]}m more to afford '
        f'(vs selling {swap["out_name"]})</span>'
    )


def render_watchlist_card(watchlist: dict) -> str:
    rows = ""
    for pos, picks in watchlist.items():
        rows += f'''
        <div class="watchlist-row">
          <div class="watchlist-pos">{pos}</div>
          <div class="watchlist-col"><span class="subtitle">Data-driven</span><br>{render_watchlist_pick(picks["data_driven"])}</div>
          <div class="watchlist-col"><span class="subtitle">Your pick</span><br>{render_watchlist_pick(picks["manual"])}</div>
        </div>'''
    return rows


def render_formation_card(formation: dict) -> str:
    """
    Renders the comparison table server-side (so it's visible even if
    JS fails to load) but leaves the pitch/bench/summary as empty
    placeholders - selectFormation() in the <script> block below fills
    those in from the embedded per-formation JSON, and swaps them
    again whenever a comparison row is clicked. Keeps the actual
    pitch-building logic (photos, captain/vice tags, status dots) in
    exactly one place (JS) instead of duplicating it between Python
    and JS.
    """
    if not formation.get("formation"):
        return f'<p>{formation.get("reason", "Not enough data yet.")}</p>'

    def comparison_row(c: dict) -> str:
        is_best = c["formation"] == formation["formation"]
        best_tag = ' <span class="best-tag">Recommended</span>' if is_best else ""
        return (
            f'<div class="formation-compare-row{" best" if is_best else ""}" '
            f'data-formation="{c["formation"]}" onclick="selectFormation(\'{c["formation"]}\')">'
            f'{c["formation"]} &mdash; {c["projected_total"]} pts{best_tag}</div>'
        )

    comparison_rows = "".join(comparison_row(c) for c in formation["all_formations"])

    return f'''
    <p id="formationSummary"></p>
    <div class="pitch" id="formationPitch"></div>
    <div class="formation-compare">{comparison_rows}</div>
    <strong class="block-label">Bench</strong>
    <div id="formationBench"></div>
    '''


def render_transfer_suggestion(t: dict) -> str:
    urgency_class = {"High": "badge-bad", "Medium": "badge-warn", "Low": "badge-muted"}.get(t["urgency"], "badge-muted")
    reasons = []
    if t.get("injury_reason"):
        reasons.append(t["injury_reason"])
    if t["form_drop"]:
        reasons.append(f"form -{t['form_drop']}")
    if t["price_drop"]:
        reasons.append(f"price -£{t['price_drop']}m")
    reason_text = ", ".join(reasons)

    candidates_html = "".join(
        f'<div class="candidate-row">&rarr; {player_link(c["player_id"], c["name"])} £{c["price"]}m '
        f'(form {c["form"]}, value {c["value_score"]})</div>'
        for c in t["candidates"]
    ) or '<div class="candidate-row">No affordable replacement found in this position.</div>'

    return f'''
    <div class="transfer-card">
      <span class="urgency-badge {urgency_class}">{t["urgency"]}</span>
      <strong>{player_link(t["player_id"], t["name"])}</strong> ({POSITION_NAMES.get(t["position"], "?")}) &mdash; {reason_text}
      <div class="budget-line">Budget if sold: £{t["budget_available"]}m</div>
      {candidates_html}
    </div>'''


def _squad_line(p: dict) -> str:
    tag = " (C)" if p.get("is_captain") else " (V)" if p.get("is_vice_captain") else ""
    fixture = f"vs {p['opponent']} (FDR {p['difficulty']})" if p.get("opponent") else "no upcoming fixture found"
    status_note = ""
    if p.get("status") and p["status"] != "a":
        label = STATUS_LABELS.get(p["status"], "Unavailable")
        chance = p.get("chance_of_playing")
        status_note = f" [{label}{f', {chance}% chance' if chance is not None else ''}]"
    return f"  - {p['name']}{tag} ({p['position']}) - {fixture}, form {p['form']}, £{p['price']}m{status_note}"


def _watchlist_pick_text(pick: dict) -> str:
    """Same fields as render_watchlist_pick, as plain text for the briefing."""
    if not pick:
        return "n/a"
    if pick.get("found") is False:
        return f"'{pick['name']}' not found"
    fixture = f"vs {pick['opponent']} (FDR {pick['difficulty']})" if pick.get("opponent") else "no upcoming fixture"
    status_note = ""
    if pick.get("status") and pick["status"] != "a":
        chance = pick.get("chance_of_playing")
        status_note = f" [{pick['status_label']}{f', {chance}% chance' if chance is not None else ''}]"
    swap = pick.get("swap_suggestion")
    swap_note = ""
    if swap and swap.get("already_owned"):
        swap_note = " (already in your squad)"
    elif swap and swap.get("affordable"):
        swap_note = f" (swap idea: OUT {swap['out_name']} form {swap['out_form']}, £{swap['budget_after']}m left after)"
    elif swap:
        swap_note = f" (need £{swap['shortfall']}m more, vs selling {swap['out_name']})"
    return (
        f"{pick['name']} £{pick['price']}m, {pick['points_per_game']} PPG, {pick['total_points']} pts, "
        f"{pick['selected_by_percent']}% owned, {fixture}{status_note}{swap_note}"
    )


def _departed_squad_contributions(squad_history: dict[int, list[dict]]) -> list[dict]:
    """
    Python port of the dashboard's own previousSquadHTML() JS logic:
    for every player who's ever been in a recorded squad but isn't in
    the LATEST one, sums their points across only the gameweeks they
    were actually owned. Kept in lockstep with that function so the
    briefing text can never tell a different "who contributed what"
    story than the Historical Contributors card does.
    """
    if not squad_history:
        return []
    gws = sorted(squad_history.keys())
    latest_gw = gws[-1]
    current_ids = {r["player_id"] for r in squad_history[latest_gw]}

    departed: dict[int, dict] = {}
    for gw in gws:
        for r in squad_history[gw]:
            if r["player_id"] in current_ids:
                continue
            pts = (r["gw_points"] or 0) * r["multiplier"] if r["multiplier"] > 0 else (r["gw_points"] or 0)
            entry = departed.setdefault(
                r["player_id"], {"name": r["name"], "position": r["position"], "total": 0, "last_gw": gw}
            )
            entry["total"] += pts
            entry["last_gw"] = gw

    return sorted(departed.values(), key=lambda e: (-e["last_gw"], -e["total"]))


def build_weekly_briefing(
    manager_stats: dict, starters: list[dict], bench: list[dict],
    captain_picks: list[dict], chip_advice: dict, transfer_suggestions: list[dict],
    watchlist: dict, formation: dict, what_if: dict, departed_contributions: list[dict],
    next_deadline: dict,
) -> str:
    """
    Plain-text summary of the week, meant to be pasted straight into a
    Claude conversation as an opening message - the "Discuss with
    Claude" button copies this to the clipboard. Built entirely from
    data the dashboard has already computed (no extra API calls, no
    cost), so it can never say something different from what the page
    itself shows.

    Every section that could legitimately be empty says so explicitly
    (e.g. "no transfer suggestions flagged") rather than silently
    vanishing, so the briefing never reads as if something's missing
    by accident.
    """
    lines = [f"Here's my FPL dashboard for Gameweek {manager_stats.get('gameweek', '?')} - talk me through it?", ""]

    if manager_stats:
        gw_points = manager_stats.get("gw_points")
        if gw_points is not None:
            gw_line = f"Last gameweek: {gw_points} pts"
            if manager_stats.get("average_score") is not None:
                gw_line += f" (league average {manager_stats['average_score']}, highest {manager_stats.get('highest_score', '?')})"
            lines.append(gw_line + ".")
        else:
            lines.append(f"Gameweek {manager_stats.get('gameweek', '?')} hasn't been played yet.")
        if manager_stats.get("total_points") is not None:
            lines.append(f"Total points this season: {manager_stats['total_points']}.")
        if manager_stats.get("overall_rank"):
            lines.append(f"Overall rank: {manager_stats['overall_rank']:,}.")
        if manager_stats.get("bank") is not None and manager_stats.get("team_value") is not None:
            lines.append(f"Squad value £{manager_stats['team_value']}m, £{manager_stats['bank']}m in the bank.")
        lines.append("")

    if starters or bench:
        lines.append("Current squad - starting XI:")
        for p in starters:
            lines.append(_squad_line(p))
        if bench:
            lines.append("Bench:")
            for p in bench:
                lines.append(_squad_line(p))
        lines.append("")

    if captain_picks:
        top = captain_picks[0]
        lines.append(f"Suggested captain: {top['name']} (form {top['form']}, projected score {top['score']}).")
        lines.append("")

    if chip_advice:
        lines.append(f"Chip timing - Triple Captain/Bench Boost: {chip_advice['triple_captain_bench_boost']}")
        lines.append(f"Chip timing - Free Hit: {chip_advice['free_hit']}")
        lines.append("")

    if transfer_suggestions:
        lines.append("Players worth considering transferring out:")
        for t in transfer_suggestions[:3]:
            reasons = []
            if t.get("injury_reason"):
                reasons.append(t["injury_reason"])
            if t["form_drop"]:
                reasons.append(f"form -{t['form_drop']}")
            if t["price_drop"]:
                reasons.append(f"price -£{t['price_drop']}m")
            reason_text = ", ".join(reasons)
            candidate = t["candidates"][0]["name"] if t["candidates"] else "no clear replacement found"
            lines.append(f"  - {t['name']} ({t['urgency']}): {reason_text}. Top replacement idea: {candidate}.")
    else:
        lines.append("No transfer suggestions flagged right now - squad looks stable on form/price/injury signals.")
    lines.append("")

    if watchlist:
        lines.append("Watchlist (data-driven pick + your manual pick, per position):")
        for pos, picks in watchlist.items():
            dd_text = _watchlist_pick_text(picks.get("data_driven"))
            manual = picks.get("manual")
            manual_text = _watchlist_pick_text(manual) if manual else "not set"
            lines.append(f"  - {pos}:")
            lines.append(f"      data-driven: {dd_text}")
            lines.append(f"      yours: {manual_text}")
        lines.append("")

    if formation.get("formation"):
        lines.append(f"Recommended formation for upcoming fixtures: {formation['formation']} (projected {formation['projected_total']} pts).")
        lines.append("")

    if departed_contributions:
        lines.append("Players transferred out this season (points scored while you owned them):")
        for d in departed_contributions[:5]:
            lines.append(f"  - {d['name']} ({d['position']}): {d['total']} pts (through GW{d['last_gw']})")
        lines.append("")

    scenarios = what_if.get("scenarios", [])
    reality = what_if.get("reality", [])
    if scenarios and reality:
        reality_total = reality[-1]["cumulative"]
        if len(scenarios) == 1:
            lines.append(
                f"What Could Have Been: only one squad recorded so far this season (no transfers made yet) - "
                f"GW{scenarios[0]['start_gw']} squad tracks exactly with Actual ({reality_total} pts) by definition."
            )
        else:
            lines.append("What Could Have Been so far this season:")
            lines.append(f"  - Actual: {reality_total} pts")
            for s in scenarios:
                diff = s["total_to_date"] - reality_total
                sign = "+" if diff > 0 else ""
                lines.append(f"  - GW{s['start_gw']} squad: {s['total_to_date']} pts ({sign}{diff} vs actual)")
        lines.append("")
    else:
        lines.append("What Could Have Been: not enough gameweeks played yet to compare.")
        lines.append("")

    if next_deadline:
        lines.append(f"Next deadline: Gameweek {next_deadline['gameweek']} - {next_deadline['deadline_time']}")
        lines.append("")

    lines.append("Given all this, what would you do before the deadline?")
    return "\n".join(lines)


def build_html() -> str:
    movers = get_movers(limit=10)
    movers_subtitle = (
        "form change since last week" if has_form_trend_baseline()
        else "no week-over-week trend yet &mdash; showing current form"
    )
    value_picks = get_top_value(limit=10)
    full_table, latest_date = get_latest_full_table()
    squad = get_latest_squad(TEAM_ID)
    captain_picks = get_captain_suggestions(TEAM_ID, limit=5)
    vice_backup_note = ""
    if len(captain_picks) >= 2:
        vice_backup_note = (
            '<div class="squad-row bench">If ' + captain_picks[0]["name"] +
            " doesn't register a score (didn't play, injured, red-carded before kickoff), "
            "the armband automatically passes to " + captain_picks[1]["name"] + ".</div>"
        )
    chip_advice = get_chip_suggestions(TEAM_ID)
    transfer_suggestions = get_transfer_suggestions(TEAM_ID)

    prior_season_summary = get_prior_season_summary()
    for row in full_table:
        row["position"] = POSITION_NAMES.get(row["position"], "?")
        prior = prior_season_summary.get(row["player_id"])
        row["prior_season"] = prior["season"] if prior else None
        row["prior_season_points"] = prior["total_points"] if prior else None
        row["prior_season_ppg"] = prior["points_per_game_est"] if prior else None
    for row in squad:
        row["position"] = POSITION_NAMES.get(row["position"], "?")

    # "Up to £X.Xm" dropdown options, same idea as FPL's own price filter -
    # built from the actual price range in this snapshot (in tenths of a
    # million to avoid float rounding) rather than a hardcoded range, so it
    # stays correct as prices drift over the season.
    prices_tenths = [round(row["price"] * 10) for row in full_table]
    step_tenths = 5  # £0.5m steps
    start_tenths = (min(prices_tenths) // step_tenths) * step_tenths if prices_tenths else 40
    end_tenths = -(-max(prices_tenths) // step_tenths) * step_tenths if prices_tenths else 150
    price_filter_options = "".join(
        f'<option value="{t / 10}">up to &pound;{t / 10:.1f}m</option>'
        for t in range(start_tenths, end_tenths + step_tenths, step_tenths)
    )

    starters = [r for r in squad if r["multiplier"] > 0]
    bench = [r for r in squad if r["multiplier"] == 0]
    squad_total = sum((r["gw_points"] or 0) * r["multiplier"] for r in starters)

    profiles = get_all_player_profiles()
    formation = get_optimal_formation(TEAM_ID)
    manager_stats = get_manager_stats(TEAM_ID)

    # manager_stats' numeric fields are None (not missing) for a
    # gameweek still pending - plain dict.get(key, '-') only falls
    # back to '-' when a key is absent, so it would otherwise print
    # the literal text "None" once a gameweek's points/value legitimately
    # have no value yet. gw_points can genuinely be 0, so this checks
    # "is not None" rather than truthiness.
    gw_points_display = manager_stats.get("gw_points") if manager_stats.get("gw_points") is not None else "-"
    average_score_display = manager_stats.get("average_score") if manager_stats.get("average_score") is not None else "-"
    highest_score_display = manager_stats.get("highest_score") if manager_stats.get("highest_score") is not None else "-"
    team_value_display = f"£{manager_stats['team_value']}m" if manager_stats.get("team_value") is not None else "-"
    bank_display = f"£{manager_stats['bank']}m" if manager_stats.get("bank") is not None else "-"
    total_points_display = manager_stats.get("total_points") if manager_stats.get("total_points") is not None else "-"
    overall_rank_display = f"{manager_stats['overall_rank']:,}" if manager_stats.get("overall_rank") else "-"
    gw_rank_display = f"{manager_stats['gw_rank']:,}" if manager_stats.get("gw_rank") else "-"

    next_deadline = get_next_deadline()
    watchlist = get_watchlist(TEAM_ID, MY_WATCHLIST)
    squad_history = get_squad_history(TEAM_ID)
    for rows in squad_history.values():
        for row in rows:
            row["position"] = POSITION_NAMES.get(row["position"], "?")
    squad_history_gws = sorted(squad_history.keys())

    what_if = get_what_if_scenarios(TEAM_ID)
    what_if_ready = len(what_if.get("scenarios", [])) >= 1 and len(what_if.get("reality", [])) >= 1
    for scenario in what_if.get("scenarios", []):
        for p in scenario["squad"]:
            p["position"] = POSITION_NAMES.get(p["position"], "?")

    formations_by_label = {c["formation"]: c for c in formation.get("all_formations", [])}

    departed_contributions = _departed_squad_contributions(squad_history)
    weekly_briefing = build_weekly_briefing(
        manager_stats, starters, bench, captain_picks, chip_advice,
        transfer_suggestions, watchlist, formation, what_if,
        departed_contributions, next_deadline,
    )

    movers_json = json.dumps(movers)
    value_json = json.dumps(value_picks)
    table_json = json.dumps(full_table)
    profiles_json = json.dumps(profiles)
    squad_json = json.dumps(squad)
    squad_history_json = json.dumps(squad_history)
    what_if_json = json.dumps(what_if)
    weekly_briefing_json = json.dumps(weekly_briefing)
    claude_chat_url_json = json.dumps(CLAUDE_CHAT_URL)
    formations_json = json.dumps(formations_by_label)
    best_formation_json = json.dumps(formation.get("formation"))
    photo_base_json = json.dumps(PLAYER_PHOTO_BASE)
    photo_legacy_base_json = json.dumps(PLAYER_PHOTO_LEGACY_BASE)
    photo_fallback_json = json.dumps(PLAYER_PHOTO_FALLBACK)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Real Bugsy United &mdash; FPL Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600;9..144,700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
  :root {{
    --bg: #FAF7F1; --surface: #FFFFFF; --border: #E8E1D3;
    --ink: #2B2620; --ink-soft: #8A8074;
    --clay: #C1613C; --clay-tint: #F3E3D6;
    --good: #5B7B4F; --warn: #BF8B32; --bad: #B0402A;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    margin: 0; padding: 0 0 40px; background: var(--bg); color: var(--ink);
  }}
  h1, h2 {{ font-family: "Fraunces", Georgia, serif; font-weight: 600; margin: 0; }}
  header {{ padding: 20px 16px 12px; }}
  header h1 {{ font-size: 1.3rem; }}
  .updated {{ color: var(--ink-soft); font-size: 0.8rem; margin-top: 2px; }}

  .hero {{
    margin: 0 16px 16px; background: linear-gradient(135deg, var(--clay-tint), var(--surface));
    border: 1px solid var(--border); border-radius: 14px; padding: 18px; text-align: center;
  }}
  .hero h2 {{ font-size: 0.95rem; color: var(--ink-soft); font-weight: 500; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 6px; }}
  .countdown {{ font-family: "Fraunces", Georgia, serif; font-size: 2.4rem; font-weight: 700; color: var(--clay); letter-spacing: 0.01em; }}
  .countdown.urgent {{ color: var(--bad); }}
  .hero-cta.with-divider {{ margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--border); }}
  .hero-cta .toggle-btn {{ padding: 8px 16px; }}
  .hero-cta .subtitle {{ margin-bottom: 8px; }}

  .tabs {{ position: sticky; top: 0; z-index: 10; background: var(--bg); display: flex; gap: 6px;
           padding: 8px 16px; overflow-x: auto; border-bottom: 1px solid var(--border); }}
  .tab-btn {{ background: var(--surface); border: 1px solid var(--border); color: var(--ink-soft);
              border-radius: 20px; padding: 7px 16px; font-size: 0.85rem; font-weight: 600;
              cursor: pointer; white-space: nowrap; }}
  .tab-btn.active {{ background: var(--clay); color: #fff; border-color: var(--clay); }}

  .tab-panel {{ display: none; padding: 16px; }}
  .tab-panel.active {{ display: block; }}

  .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
           padding: 16px; margin-bottom: 16px; }}
  .card h2 {{ font-size: 1.05rem; margin-bottom: 10px; }}
  .subtitle {{ color: var(--ink-soft); font-size: 0.75rem; font-weight: normal; font-family: inherit; }}
  .muted {{ color: var(--ink-soft); }}
  .text-good {{ color: var(--good); }} .text-bad {{ color: var(--bad); }} .text-warn {{ color: var(--warn); }}
  .block-label {{ display: block; margin: 12px 0 4px; }}

  canvas {{ max-height: 300px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  th, td {{ text-align: left; padding: 7px 8px; border-bottom: 1px solid var(--border); }}
  th {{ cursor: pointer; color: var(--clay); position: sticky; top: 0; background: var(--surface); font-weight: 600; }}
  .scroll {{ max-height: 420px; overflow-y: auto; }}

  .squad-row {{ padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 0.9rem;
                display: flex; align-items: center; gap: 8px; }}
  .player-pic {{ width: 32px; height: 40px; object-fit: cover; border-radius: 6px; background: transparent; flex-shrink: 0; }}
  .squad-row.bench {{ color: var(--ink-soft); }}
  .pos-tag {{ color: var(--clay); font-size: 0.72rem; font-weight: 600; }}

  .transfer-card {{ border: 1px solid var(--border); border-radius: 10px; padding: 12px; margin-bottom: 10px; font-size: 0.9rem; }}
  .urgency-badge {{ display: inline-block; padding: 2px 10px; border-radius: 10px; font-size: 0.7rem; font-weight: 700; color: #fff; margin-right: 6px; }}
  .badge-bad {{ background: var(--bad); }} .badge-warn {{ background: var(--warn); }} .badge-muted {{ background: var(--ink-soft); }}
  .budget-line {{ color: var(--ink-soft); font-size: 0.8rem; margin: 5px 0; }}
  .candidate-row {{ font-size: 0.85rem; color: var(--clay); padding-left: 6px; }}

  .player-link {{ color: var(--ink); text-decoration: underline dotted var(--clay); cursor: pointer; font-weight: 500; }}
  .player-link:hover {{ color: var(--clay); }}

  #profileModal {{ display: none; position: fixed; inset: 0; background: rgba(43,38,32,0.55); z-index: 100;
                    align-items: center; justify-content: center; padding: 16px; }}
  #profileModal.open {{ display: flex; }}
  .modal-content {{ background: var(--surface); border-radius: 16px; padding: 20px; max-width: 400px;
                     width: 100%; max-height: 80vh; overflow-y: auto; }}
  .modal-header {{ display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }}
  .modal-header img {{ width: 56px; height: 70px; object-fit: cover; border-radius: 8px; background: transparent; }}
  .modal-header strong {{ font-family: "Fraunces", serif; font-size: 1.1rem; }}
  .modal-close {{ float: right; cursor: pointer; color: var(--ink-soft); font-size: 1.3rem; }}
  .modal-stats {{ display: grid; grid-template-columns: 1fr 1fr; gap: 6px 12px; font-size: 0.85rem; margin: 12px 0; }}
  .modal-stats div {{ display: flex; justify-content: space-between; border-bottom: 1px solid var(--border); padding: 4px 0; }}
  .modal-gw-table {{ width: 100%; font-size: 0.8rem; margin-top: 10px; }}
  .modal-gw-table th, .modal-gw-table td {{ padding: 4px 6px; text-align: center; border-bottom: 1px solid var(--border); }}

  .pitch {{
    background-image: url("{PITCH_MARKINGS_DATA_URI}"), linear-gradient(180deg, #6B8F5C, #5B7B4F);
    background-size: 100% 100%, 100% 100%;
    background-repeat: no-repeat, no-repeat;
    border-radius: 12px; padding: 14px 6px; margin: 12px 0;
  }}
  .pitch-row {{ display: flex; justify-content: space-around; flex-wrap: wrap; margin: 8px 0; }}
  .pitch-player {{ background: rgba(255,255,255,0.92); border-radius: 8px; padding: 5px 9px; text-align: center; font-size: 0.75rem; min-width: 62px; }}
  .pitch-player.is-captain {{ border: 2px solid var(--clay); }}
  .pitch-player.is-vice-captain {{ border: 2px dashed var(--ink-soft); }}
  .pitch-pic {{ width: 40px; height: 50px; object-fit: cover; border-radius: 6px; background: transparent; display: block; margin: 0 auto 4px; }}
  .pitch-score {{ color: var(--ink-soft); font-size: 0.7rem; }}
  .formation-compare {{ margin-top: 12px; font-size: 0.8rem; }}
  .formation-compare-row {{ padding: 5px 8px; color: var(--ink-soft); cursor: pointer; border-radius: 6px; border: 1px solid transparent; }}
  .formation-compare-row:hover {{ background: var(--clay-tint); }}
  .formation-compare-row.best {{ color: var(--good); font-weight: 700; }}
  .formation-compare-row.selected {{ border-color: var(--clay); background: var(--clay-tint); }}
  .best-tag {{ font-size: 0.65rem; font-weight: 700; color: #fff; background: var(--good); border-radius: 8px; padding: 1px 8px; margin-left: 4px; }}

  .toggle-row {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }}
  .toggle-btn {{ background: var(--bg); border: 1px solid var(--border); color: var(--ink-soft); border-radius: 8px;
                  padding: 5px 10px; font-size: 0.75rem; cursor: pointer; }}
  .toggle-btn.active {{ background: var(--clay); color: #fff; border-color: var(--clay); font-weight: 700; }}

  .price-select {{ background: var(--bg); border: 1px solid var(--border); color: var(--ink-soft); border-radius: 8px;
                    padding: 5px 10px; font-size: 0.75rem; cursor: pointer; }}

  .prior-col {{ display: none; }}
  #fullTable.show-prior .prior-col {{ display: table-cell; }}

  .history-slider-row {{ display: flex; align-items: center; gap: 14px; margin-bottom: 12px; flex-wrap: wrap; }}
  .history-slider-row input[type="range"] {{ flex: 1; min-width: 160px; accent-color: var(--clay); }}
  .history-label {{ white-space: nowrap; }}

  .status-badge {{ font-size: 0.7rem; }}
  .status-red {{ color: var(--bad); }} .status-amber {{ color: var(--warn); }}

  .role-tag {{ font-size: 0.65rem; font-weight: 700; color: #fff; border-radius: 8px; padding: 1px 8px; margin-left: 4px; }}
  .role-captain {{ background: var(--clay); }}
  .role-vice {{ background: var(--ink-soft); }}

  .watchlist-row {{ display: grid; grid-template-columns: 46px 1fr 1fr; gap: 8px; padding: 9px 0;
                     border-bottom: 1px solid var(--border); font-size: 0.85rem; align-items: start; }}
  .watchlist-pos {{ font-weight: 700; color: var(--clay); }}
  .watchlist-col .subtitle {{ display: block; margin-bottom: 2px; }}
</style>
</head>
<body>
  <header>
    <h1>Real Bugsy United</h1>
    <div class="updated">Latest data: {latest_date}</div>
  </header>

  <div class="hero">
    {"" if not next_deadline else f'''
    <h2>Gameweek {next_deadline["gameweek"]} deadline</h2>
    <div id="countdown" class="countdown" data-deadline="{next_deadline["deadline_time"]}">calculating&hellip;</div>
    '''}
    <div class="hero-cta{' with-divider' if next_deadline else ''}">
      <p class="subtitle">{"Not sure what to do before it? " if next_deadline else ""}Talk through your squad, captain pick, and transfer options.</p>
      <button class="toggle-btn active" id="discussClaudeBtn">Discuss with Claude</button>
      <div id="discussStatus" class="subtitle"></div>
    </div>
  </div>

  <div class="tabs" id="tabBar">
    <button class="tab-btn active" data-tab="squad">Squad</button>
    <button class="tab-btn" data-tab="today">Tips</button>
    <button class="tab-btn" data-tab="moves">Moves</button>
    <button class="tab-btn" data-tab="explore">Explore</button>
    <button class="tab-btn" data-tab="history">History</button>
  </div>

  <div class="tab-panel active" id="tab-squad">
    <div class="card">
      <h2>Manager Stats &mdash; Gameweek {manager_stats.get('gameweek', '?')}</h2>
      {"<p>No manager data yet.</p>" if not manager_stats else f'''
      <div class="toggle-row" id="managerStatsToggle">
        <button class="toggle-btn active" data-view="season">Season</button>
        <button class="toggle-btn" data-view="week">This Week</button>
      </div>
      <div class="modal-stats stats-view" data-view="season">
        <div><span>Total points</span><span><strong>{total_points_display}</strong></span></div>
        <div><span>Overall rank</span><span>{overall_rank_display}</span></div>
        <div><span>Squad value</span><span>{team_value_display}</span></div>
        <div><span>Bank</span><span>{bank_display}</span></div>
      </div>
      <div class="modal-stats stats-view" data-view="week" style="display:none;">
        <div><span>Your points</span><span><strong>{gw_points_display}</strong></span></div>
        <div><span>League average</span><span>{average_score_display}</span></div>
        <div><span>League highest</span><span>{highest_score_display}</span></div>
        <div><span>GW rank</span><span>{gw_rank_display}</span></div>
        <div><span>Overall rank</span><span>{overall_rank_display}</span></div>
        <div><span>Squad value</span><span>{team_value_display}</span></div>
        <div><span>Bank</span><span>{bank_display}</span></div>
      </div>
      '''}
    </div>

    <div class="card">
      <h2>Current Squad {"" if not starters else f"&mdash; {squad_total} pts this gameweek"}</h2>
      {"<p>No squad data yet - runs after the current gameweek's picks are published.</p>" if not squad else '''
      <div class="toggle-row" id="squadToggle">
        <button class="toggle-btn active" data-metric="points">Points</button>
        <button class="toggle-btn" data-metric="opponent">Opponent</button>
        <button class="toggle-btn" data-metric="price">Price</button>
        <button class="toggle-btn" data-metric="selling_price">Selling</button>
        <button class="toggle-btn" data-metric="difficulty">FDR</button>
        <button class="toggle-btn" data-metric="form">Form</button>
        <button class="toggle-btn" data-metric="selected_by_percent">Own%</button>
        <button class="toggle-btn" data-metric="price_change">Price &Delta;</button>
      </div>
      <div class="pitch" id="squadPitch"></div>
      <strong class="block-label">Historical Contributors <span class="subtitle">(players transferred out, and what they contributed while owned)</span></strong>
      <div id="previousSquad"></div>
      '''}
    </div>
  </div>

  <div class="tab-panel" id="tab-today">
    <div class="card">
      <h2>Best Formation for Upcoming Fixtures</h2>
      {render_formation_card(formation)}
    </div>

    <div class="card">
      <h2>Captain Suggestions <span class="subtitle">(form &times; fixture favourability)</span></h2>
      {"<p>Not enough fixture data yet.</p>" if not captain_picks else "".join(
          f'<div class="squad-row">{i+1}. {player_link(c["player_id"], c["name"])}'
          f'{captain_role_tag(i)} vs {c["opponent"] or "?"} '
          f'({"H" if c["is_home"] else "A" if c["is_home"] is not None else "?"}, FDR {c["difficulty"] or "?"}) '
          f'&mdash; form {c["form"]}, score <strong>{c["score"]}</strong></div>'
          for i, c in enumerate(captain_picks)
      )}
      {vice_backup_note}
    </div>

    <div class="card">
      <h2>Chip Timing <span class="subtitle">(double/blank gameweek detection)</span></h2>
      <div class="squad-row">🎯 <strong>Triple Captain / Bench Boost:</strong> {chip_advice['triple_captain_bench_boost']}</div>
      <div class="squad-row">🃏 <strong>Free Hit:</strong> {chip_advice['free_hit']}</div>
      <div class="squad-row bench">Wildcard timing isn't scored here &mdash; that call depends on broader squad health and fixture swings, better discussed directly.</div>
    </div>
  </div>

  <div class="tab-panel" id="tab-moves">
    <div class="card">
      <h2>Transfer Suggestions <span class="subtitle">(form + price trend, budget-aware)</span></h2>
      {"<p>No players flagged - either everything's stable, or only one snapshot exists so far (trends need two).</p>" if not transfer_suggestions else "".join(render_transfer_suggestion(t) for t in transfer_suggestions)}

      <strong class="block-label" style="font-family:'Fraunces',serif;font-size:1rem;">Watchlist <span class="subtitle">(1 data-driven + 1 yours, per position)</span></strong>
      <p class="subtitle">Incoming transfer candidates &mdash; who the data likes, and who you're personally tracking.</p>
      {render_watchlist_card(watchlist)}
    </div>

    <div class="card">
      <h2>Movers &amp; Shakers <span class="subtitle">({movers_subtitle})</span></h2>
      <canvas id="moversChart"></canvas>
    </div>

    <div class="card">
      <h2>Top Value Picks <span class="subtitle">(points/game per £1m)</span></h2>
      <canvas id="valueChart"></canvas>
    </div>
  </div>

  <div class="tab-panel" id="tab-explore">
    <div class="card">
      <h2>Full Table</h2>
      <div class="toggle-row" id="positionFilter">
        <button class="toggle-btn active" data-position="All">All</button>
        <button class="toggle-btn" data-position="GK">GK</button>
        <button class="toggle-btn" data-position="DEF">DEF</button>
        <button class="toggle-btn" data-position="MID">MID</button>
        <button class="toggle-btn" data-position="FWD">FWD</button>
      </div>
      <div class="toggle-row">
        <button class="toggle-btn" id="priorSeasonToggle">Show Last Season</button>
        <select id="maxPriceFilter" class="price-select">
          <option value="">Any price</option>
          {price_filter_options}
        </select>
      </div>
      <div class="scroll">
        <table id="fullTable">
          <thead>
            <tr>
              <th onclick="sortTable(0)">Name</th>
              <th onclick="sortTable(1)">Pos</th>
              <th onclick="sortTable(2)">Price</th>
              <th onclick="sortTable(3)">Pts</th>
              <th onclick="sortTable(4)">Form</th>
              <th onclick="sortTable(5)">PPG</th>
              <th onclick="sortTable(6)">Own%</th>
              <th class="prior-col" onclick="sortTable(7)">Last Pts</th>
              <th class="prior-col" onclick="sortTable(8)">Last PPG</th>
            </tr>
          </thead>
          <tbody id="tableBody"></tbody>
        </table>
      </div>
      <p class="muted" style="font-size:0.75rem;margin-top:8px;">Last season figures are shown where available - a player's FPL debut season won't have one. PPG is estimated from minutes &divide; 90, same as the profile popup's past-season history.</p>
    </div>
  </div>

  <div class="tab-panel" id="tab-history">
    <div class="card">
      <h2>Squad History <span class="subtitle">(recorded automatically each gameweek)</span></h2>
      {"<p>No squad history recorded yet &mdash; this fills in automatically each week as the pipeline runs, no action needed.</p>" if not squad_history_gws else f'''
      <div class="history-slider-row">
        <input type="range" id="historySlider" min="{squad_history_gws[0]}" max="{squad_history_gws[-1]}" value="{squad_history_gws[-1]}" step="1" {"disabled" if len(squad_history_gws) < 2 else ""}>
        <div class="history-label"><strong id="historyGwLabel">Gameweek {squad_history_gws[-1]}</strong> <span id="historyPtsLabel" class="subtitle"></span></div>
      </div>
      {'<p class="subtitle">Only one gameweek recorded so far &mdash; the slider will start moving once GW2 is in.</p>' if len(squad_history_gws) < 2 else ''}
      <div class="pitch" id="historyPitch"></div>
      <strong class="block-label">Bench</strong>
      <div id="historyBench"></div>
      <strong class="block-label">Transfers <span class="subtitle">(vs the previous recorded gameweek)</span></strong>
      <div id="historyTransfers"></div>
      '''}
    </div>

    <div class="card">
      <h2>What Could Have Been <span class="subtitle">(each squad you've held, tracked forward as if never touched again)</span></h2>
      {"<p>Not enough gameweeks played yet to compare &mdash; this fills in once a couple of gameweeks are on the books.</p>" if not what_if_ready else '''
      <canvas id="whatIfChart"></canvas>
      <div id="whatIfSummary" style="margin-top:14px;"></div>
      <p class="muted" style="font-size:0.75rem;margin-top:10px;">Each line freezes a squad's starting XI, bench, and captain exactly as picked that week, then carries it forward using every player's real points each gameweek since &mdash; captain changes, formation tweaks, and the Bench Boost chip aren't modelled.</p>
      '''}
    </div>
  </div>

  <div id="profileModal" onclick="if(event.target===this) closeProfile()">
    <div class="modal-content">
      <span class="modal-close" onclick="closeProfile()">&times;</span>
      <div id="modalBody"></div>
    </div>
  </div>

<script>
const movers = {movers_json};
const valuePicks = {value_json};
const fullTable = {table_json};
const playerProfiles = {profiles_json};
const squadData = {squad_json};
const squadHistory = {squad_history_json};
const whatIf = {what_if_json};
const weeklyBriefing = {weekly_briefing_json};
const claudeChatUrl = {claude_chat_url_json};
const allFormations = {formations_json};
const bestFormationLabel = {best_formation_json};
const PLAYER_PHOTO_BASE = {photo_base_json};
const PLAYER_PHOTO_LEGACY_BASE = {photo_legacy_base_json};
const PLAYER_PHOTO_FALLBACK = {photo_fallback_json};
const POS_NAMES = {{1: 'GK', 2: 'DEF', 3: 'MID', 4: 'FWD'}};

// Current-season photo first (correct kit when it exists); if that 403s,
// fall back to the legacy bucket (a real photo, but can be a stale/wrong
// kit for anyone transferred or re-photographed this season); only show
// the silhouette if neither bucket has anything at all.
function photoOnErrorAttr(photoCode) {{
  const legacyUrl = `${{PLAYER_PHOTO_LEGACY_BASE}}/110x140/p${{photoCode}}.png`;
  return `this.onerror=function(){{this.onerror=null;this.src='${{PLAYER_PHOTO_FALLBACK}}';}};this.src='${{legacyUrl}}'`;
}}

document.getElementById('tabBar').addEventListener('click', (e) => {{
  if (!e.target.classList.contains('tab-btn')) return;
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  e.target.classList.add('active');
  document.getElementById('tab-' + e.target.dataset.tab).classList.add('active');
}});

const discussClaudeBtn = document.getElementById('discussClaudeBtn');
if (discussClaudeBtn) {{
  discussClaudeBtn.addEventListener('click', async () => {{
    const statusEl = document.getElementById('discussStatus');
    try {{
      await navigator.clipboard.writeText(weeklyBriefing);
      if (statusEl) statusEl.textContent = 'Copied! Paste it into the chat that just opened.';
    }} catch (e) {{
      // Clipboard API can fail (no HTTPS, permissions denied, etc.) -
      // fall back to a manual copy so the button still does something.
      prompt('Copy this, then paste it into the chat that just opened:', weeklyBriefing);
      if (statusEl) statusEl.textContent = '';
    }}
    window.open(claudeChatUrl || 'https://claude.ai/new', '_blank', 'noopener');
  }});
}}

function statusDotHTML(status, news) {{
  if (status === 'i' || status === 's' || status === 'u' || status === 'n') {{
    return `<span class="status-badge status-red" title="${{news || ''}}">&#9679;</span>`;
  }}
  if (status === 'd') {{
    return `<span class="status-badge status-amber" title="${{news || ''}}">&#9679;</span>`;
  }}
  return '';
}}

function pitchPhotoUrl(photoCode) {{
  return `${{PLAYER_PHOTO_BASE}}/110x140/${{photoCode}}.png`;
}}

function buildPitchHTML(xi, captainId, viceCaptainId) {{
  const byPos = {{1: [], 2: [], 3: [], 4: []}};
  xi.forEach(p => byPos[p.position].push(p));
  return [1, 2, 3, 4].map(pos => {{
    const cells = byPos[pos].map(p => {{
      const isCaptain = p.player_id === captainId;
      const isVice = p.player_id === viceCaptainId;
      return `
      <div class="pitch-player${{isCaptain ? ' is-captain' : isVice ? ' is-vice-captain' : ''}}">
        <img class="pitch-pic" src="${{pitchPhotoUrl(p.photo_code)}}" onerror="${{photoOnErrorAttr(p.photo_code)}}" alt="">
        ${{statusDotHTML(p.status)}}
        <span class="player-link" onclick="openProfile(${{p.player_id}})">${{p.name}}</span><br>
        <span class="pitch-score">${{p.score}}${{isCaptain ? ' (C)' : isVice ? ' (VC)' : ''}}</span>
      </div>`;
    }}).join('');
    return `<div class="pitch-row">${{cells}}</div>`;
  }}).join('');
}}

function buildBenchHTML(bench) {{
  return bench.map(p => `
    <div class="squad-row bench">
      <img class="player-pic" src="${{pitchPhotoUrl(p.photo_code)}}" onerror="${{photoOnErrorAttr(p.photo_code)}}" alt="">
      ${{statusDotHTML(p.status)}}
      <span class="player-link" onclick="openProfile(${{p.player_id}})">${{p.name}}</span>
      <span class="pos-tag">${{POS_NAMES[p.position] || '?'}}</span> &mdash; score ${{p.score}}
    </div>`).join('');
}}

function selectFormation(label) {{
  const f = allFormations[label];
  if (!f) return;

  const isBest = label === bestFormationLabel;
  const viceHTML = f.suggested_vice_captain
    ? ` &mdash; vice-captain <strong><span class="player-link" onclick="openProfile(${{f.suggested_vice_captain.player_id}})">${{f.suggested_vice_captain.name}}</span></strong> (steps up if the captain doesn't register a score)`
    : '';
  document.getElementById('formationSummary').innerHTML =
    `<strong>${{isBest ? 'Recommended' : 'Selected'}}: ${{f.formation}}</strong> &mdash; projected ${{f.projected_total}} pts, ` +
    `captain <strong><span class="player-link" onclick="openProfile(${{f.suggested_captain.player_id}})">${{f.suggested_captain.name}}</span></strong>${{viceHTML}}`;
  document.getElementById('formationPitch').innerHTML = buildPitchHTML(f.starting_xi, f.suggested_captain.player_id, f.suggested_vice_captain ? f.suggested_vice_captain.player_id : null);
  document.getElementById('formationBench').innerHTML = buildBenchHTML(f.bench);

  document.querySelectorAll('.formation-compare-row').forEach(el => {{
    el.classList.toggle('selected', el.dataset.formation === label);
  }});
}}

if (bestFormationLabel) selectFormation(bestFormationLabel);

const SQUAD_POS_ORDER = ['GK', 'DEF', 'MID', 'FWD'];

function pitchPoints(r) {{
  return r.multiplier > 0 ? (r.gw_points || 0) * r.multiplier : (r.gw_points || 0);
}}

// Current Squad has a toggle to view different metrics per player; History
// only ever shows points (it's a look back at what happened, not a
// what-if-I-sell-this-player view), so squadPitchPlayerHTML defaults to
// 'points' and History's calls just don't pass a metric.
const SQUAD_METRICS = {{
  points: {{ label: 'Pts', value: pitchPoints }},
  opponent: {{ label: 'Opp', value: r => r.opponent || '-' }},
  price: {{ label: 'Price', value: r => `£${{r.price}}m` }},
  selling_price: {{ label: 'Selling', value: r => r.selling_price != null ? `£${{r.selling_price}}m` : '-' }},
  difficulty: {{ label: 'FDR', value: r => r.difficulty != null ? r.difficulty : '-' }},
  form: {{ label: 'Form', value: r => r.form }},
  selected_by_percent: {{ label: 'Own%', value: r => `${{r.selected_by_percent}}%` }},
  price_change: {{ label: 'Price Δ', value: r => {{
    if (r.price_change == null) return '-';
    if (r.price_change > 0) return `+£${{r.price_change}}m`;
    if (r.price_change < 0) return `-£${{Math.abs(r.price_change)}}m`;
    return '£0.0m';
  }} }},
}};

function squadPitchPlayerHTML(r, metric = 'points') {{
  const m = SQUAD_METRICS[metric];
  const tag = r.is_captain ? ' (C)' : r.is_vice_captain ? ' (V)' : '';
  const photoUrl = `${{PLAYER_PHOTO_BASE}}/110x140/${{r.photo_code}}.png`;
  return `
    <div class="pitch-player${{r.is_captain ? ' is-captain' : ''}}">
      <img class="pitch-pic" src="${{photoUrl}}" onerror="${{photoOnErrorAttr(r.photo_code)}}" alt="">
      ${{statusDotHTML(r.status, r.news)}}
      <span class="player-link" onclick="openProfile(${{r.player_id}})">${{r.name}}</span>${{tag}}<br>
      <span class="pitch-score">${{m.label}} ${{m.value(r)}}</span>
    </div>`;
}}

function renderSquadPitch(metric) {{
  const byPos = {{GK: [], DEF: [], MID: [], FWD: []}};
  squadData.forEach(r => {{ if (byPos[r.position]) byPos[r.position].push(r); }});
  document.getElementById('squadPitch').innerHTML = SQUAD_POS_ORDER.map(pos =>
    `<div class="pitch-row">${{byPos[pos].map(r => squadPitchPlayerHTML(r, metric)).join('')}}</div>`
  ).join('');
}}

const managerStatsToggle = document.getElementById('managerStatsToggle');
if (managerStatsToggle) {{
  managerStatsToggle.querySelectorAll('.toggle-btn').forEach(btn => {{
    btn.addEventListener('click', () => {{
      managerStatsToggle.querySelectorAll('.toggle-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      document.querySelectorAll('.stats-view').forEach(el => {{
        el.style.display = el.dataset.view === btn.dataset.view ? '' : 'none';
      }});
    }});
  }});
}}

const squadToggle = document.getElementById('squadToggle');
if (squadToggle) {{
  squadToggle.querySelectorAll('.toggle-btn').forEach(btn => {{
    btn.addEventListener('click', () => {{
      squadToggle.querySelectorAll('.toggle-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      renderSquadPitch(btn.dataset.metric);
    }});
  }});
}}

function historyBenchRowHTML(r) {{
  const pts = r.gw_points != null ? r.gw_points : 0;
  return `
    <div class="squad-row bench">
      <img class="player-pic" src="${{pitchPhotoUrl(r.photo_code)}}" onerror="${{photoOnErrorAttr(r.photo_code)}}" alt="">
      ${{statusDotHTML(r.status, r.news)}}
      <span class="player-link" onclick="openProfile(${{r.player_id}})">${{r.name}}</span>
      <span class="pos-tag">${{r.position}}</span> &mdash; ${{pts}} pts
    </div>`;
}}

const historyGws = Object.keys(squadHistory).map(Number).sort((a, b) => a - b);

// How many points a player has actually contributed to YOUR squad -
// only counting gameweeks they were genuinely part of squadHistory, so
// a player bought partway through the season doesn't get credited for
// points scored (for someone else's team) before you owned them. Their
// real season total (playerProfiles[id].total_points, shown separately
// in the profile modal) is untouched by this - that's the universal
// FPL figure, this is specifically "what have they done for me."
function squadContributionTotal(playerId) {{
  let total = 0, weeks = 0;
  historyGws.forEach(gw => {{
    const row = squadHistory[gw].find(r => r.player_id === playerId);
    if (row) {{ total += pitchPoints(row); weeks++; }}
  }});
  return weeks > 0 ? {{ total, weeks }} : null;
}}

function transferListHTML(players) {{
  if (!players.length) return '&mdash;';
  return players.map(r => `<span class="player-link" onclick="openProfile(${{r.player_id}})">${{r.name}}</span>`).join(', ');
}}

function historyTransfersHTML(gw) {{
  const idx = historyGws.indexOf(gw);
  if (idx <= 0) {{
    return '<p class="muted" style="font-size:0.85rem;">Starting squad &mdash; no earlier recorded gameweek to compare against.</p>';
  }}
  const prevGw = historyGws[idx - 1];
  const curRows = squadHistory[gw], prevRows = squadHistory[prevGw];
  const curIds = new Set(curRows.map(r => r.player_id));
  const prevIds = new Set(prevRows.map(r => r.player_id));
  const inPlayers = curRows.filter(r => !prevIds.has(r.player_id));
  const outPlayers = prevRows.filter(r => !curIds.has(r.player_id));

  if (!inPlayers.length && !outPlayers.length) {{
    return `<p class="muted" style="font-size:0.85rem;">No transfers made since Gameweek ${{prevGw}}.</p>`;
  }}
  return `
    <div class="squad-row"><span class="text-good">IN:</span> ${{transferListHTML(inPlayers)}}</div>
    <div class="squad-row"><span class="text-bad">OUT:</span> ${{transferListHTML(outPlayers)}}</div>`;
}}

function renderHistoryGW(gw) {{
  gw = Number(gw);
  const rows = squadHistory[gw];
  if (!rows) return;

  const starters = rows.filter(r => r.squad_position <= 11);
  const bench = rows.filter(r => r.squad_position > 11);

  const byPos = {{GK: [], DEF: [], MID: [], FWD: []}};
  starters.forEach(r => {{ if (byPos[r.position]) byPos[r.position].push(r); }});
  document.getElementById('historyPitch').innerHTML = SQUAD_POS_ORDER.map(pos =>
    `<div class="pitch-row">${{byPos[pos].map(r => squadPitchPlayerHTML(r)).join('')}}</div>`
  ).join('');
  document.getElementById('historyBench').innerHTML = bench.map(historyBenchRowHTML).join('');
  document.getElementById('historyTransfers').innerHTML = historyTransfersHTML(gw);

  const total = starters.reduce((sum, r) => sum + pitchPoints(r), 0);
  document.getElementById('historyGwLabel').textContent = `Gameweek ${{gw}}`;
  document.getElementById('historyPtsLabel').textContent = `${{total}} pts`;
}}

const historySlider = document.getElementById('historySlider');
if (historySlider) {{
  renderHistoryGW(historySlider.value);
  historySlider.addEventListener('input', () => renderHistoryGW(historySlider.value));
}}

function previousSquadHTML() {{
  if (!historyGws.length) return '';
  const latestGw = historyGws[historyGws.length - 1];
  const currentIds = new Set(squadHistory[latestGw].map(r => r.player_id));

  // Walk every recorded gameweek and tally contributions for anyone who
  // isn't in the current squad - if they were transferred out and later
  // bought back, they're in currentIds again and correctly excluded here.
  const departed = new Map();
  historyGws.forEach(gw => {{
    squadHistory[gw].forEach(r => {{
      if (currentIds.has(r.player_id)) return;
      const pts = pitchPoints(r);
      if (!departed.has(r.player_id)) {{
        departed.set(r.player_id, {{ ...r, totalPoints: 0, lastGw: gw }});
      }}
      const entry = departed.get(r.player_id);
      entry.totalPoints += pts;
      entry.lastGw = gw;
    }});
  }});

  const rows = Array.from(departed.values()).sort((a, b) => b.lastGw - a.lastGw || b.totalPoints - a.totalPoints);
  if (!rows.length) return '<p class="muted" style="font-size:0.85rem;">No transfers made yet this season.</p>';

  return rows.map(r => `
    <div class="squad-row bench">
      <img class="player-pic" src="${{pitchPhotoUrl(r.photo_code)}}" onerror="${{photoOnErrorAttr(r.photo_code)}}" alt="">
      <span class="player-link" onclick="openProfile(${{r.player_id}})">${{r.name}}</span>
      <span class="pos-tag">${{r.position}}</span> &mdash; ${{r.totalPoints}} pts contributed (through GW${{r.lastGw}})
    </div>`).join('');
}}

const previousSquadEl = document.getElementById('previousSquad');
if (previousSquadEl) previousSquadEl.innerHTML = previousSquadHTML();

if (squadData.length) renderSquadPitch('points');

const CLAY = '#C1613C', GOOD = '#5B7B4F', BAD = '#B0402A';

function renderWhatIf() {{
  const canvas = document.getElementById('whatIfChart');
  if (!canvas || !whatIf.scenarios || !whatIf.scenarios.length || !whatIf.reality || !whatIf.reality.length) return;

  const gws = whatIf.reality.map(r => r.gameweek);
  const palette = ['#C1613C', '#5B7B4F', '#BF8B32', '#8A8074', '#7A5C99', '#3D7A8A'];

  const datasets = whatIf.scenarios.map((s, i) => {{
    const byGw = {{}};
    s.trajectory.forEach(t => {{ byGw[t.gameweek] = t.cumulative; }});
    return {{
      label: `GW${{s.start_gw}} squad`,
      data: gws.map(gw => gw in byGw ? byGw[gw] : null),
      borderColor: palette[i % palette.length],
      backgroundColor: palette[i % palette.length],
      spanGaps: false,
      tension: 0.15,
    }};
  }});

  datasets.push({{
    label: 'Actual',
    data: whatIf.reality.map(r => r.cumulative),
    borderColor: '#2B2620',
    backgroundColor: '#2B2620',
    borderWidth: 3,
    borderDash: [5, 3],
    tension: 0.15,
  }});

  new Chart(canvas, {{
    type: 'line',
    data: {{ labels: gws.map(gw => `GW${{gw}}`), datasets }},
    options: {{ plugins: {{ legend: {{ position: 'bottom' }} }} }}
  }});

  const realityTotal = whatIf.reality[whatIf.reality.length - 1].cumulative;
  const rows = whatIf.scenarios.map(s => {{
    const diff = s.total_to_date - realityTotal;
    const diffCls = diff > 0 ? 'text-good' : diff < 0 ? 'text-bad' : 'muted';
    const diffText = diff > 0 ? `+${{diff}}` : `${{diff}}`;
    return `<div class="squad-row"><strong>GW${{s.start_gw}} squad</strong> &mdash; ${{s.total_to_date}} pts <span class="${{diffCls}}">(${{diffText}} vs actual)</span></div>`;
  }}).join('');
  document.getElementById('whatIfSummary').innerHTML =
    `<div class="squad-row"><strong>Actual</strong> &mdash; ${{realityTotal}} pts</div>` + rows;
}}

new Chart(document.getElementById('moversChart'), {{
  type: 'bar',
  data: {{
    labels: movers.map(m => m.name),
    datasets: [{{
      label: 'Form change',
      data: movers.map(m => m.form_change),
      backgroundColor: movers.map(m => m.form_change >= 0 ? GOOD : BAD),
      borderRadius: 4,
    }}]
  }},
  options: {{
    indexAxis: 'y', plugins: {{ legend: {{ display: false }} }},
    onClick: (evt, elements) => {{ if (elements.length) openProfile(movers[elements[0].index].player_id); }}
  }}
}});

new Chart(document.getElementById('valueChart'), {{
  type: 'bar',
  data: {{
    labels: valuePicks.map(v => v.name),
    datasets: [{{
      label: 'Value score',
      data: valuePicks.map(v => v.value_score),
      backgroundColor: CLAY,
      borderRadius: 4,
    }}]
  }},
  options: {{
    indexAxis: 'y', plugins: {{ legend: {{ display: false }} }},
    onClick: (evt, elements) => {{ if (elements.length) openProfile(valuePicks[elements[0].index].player_id); }}
  }}
}});

renderWhatIf();

const tbody = document.getElementById('tableBody');
function renderTable(rows) {{
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td><span class="player-link" onclick="openProfile(${{r.player_id}})">${{r.name}}</span></td>
      <td>${{r.position}}</td><td>£${{r.price}}m</td>
      <td>${{r.total_points}}</td><td>${{r.form}}</td>
      <td>${{r.points_per_game}}</td><td>${{r.selected_by_percent}}%</td>
      <td class="prior-col">${{r.prior_season_points !== null ? r.prior_season_points : '&mdash;'}}</td>
      <td class="prior-col">${{r.prior_season_ppg !== null ? r.prior_season_ppg : '&mdash;'}}</td>
    </tr>`).join('');
}}

const keys = ['name','position','price','total_points','form','points_per_game','selected_by_percent','prior_season_points','prior_season_ppg'];
let sortDir = {{}};
let sortKey = null;
let positionFilter = 'All';
let maxPriceFilter = null;

function applyTableFilterAndSort() {{
  let rows = positionFilter === 'All' ? fullTable : fullTable.filter(r => r.position === positionFilter);
  if (maxPriceFilter !== null) {{
    rows = rows.filter(r => r.price <= maxPriceFilter);
  }}
  if (sortKey) {{
    rows = [...rows].sort((a, b) =>
      sortDir[sortKey] ? (a[sortKey] > b[sortKey] ? 1 : -1) : (a[sortKey] < b[sortKey] ? 1 : -1)
    );
  }}
  renderTable(rows);
}}

function sortTable(colIndex) {{
  sortKey = keys[colIndex];
  sortDir[sortKey] = !sortDir[sortKey];
  applyTableFilterAndSort();
}}

document.getElementById('positionFilter').querySelectorAll('.toggle-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    positionFilter = btn.dataset.position;
    document.getElementById('positionFilter').querySelectorAll('.toggle-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    applyTableFilterAndSort();
  }});
}});

const maxPriceSelect = document.getElementById('maxPriceFilter');
if (maxPriceSelect) {{
  maxPriceSelect.addEventListener('change', () => {{
    maxPriceFilter = maxPriceSelect.value === '' ? null : parseFloat(maxPriceSelect.value);
    applyTableFilterAndSort();
  }});
}}

applyTableFilterAndSort();

const priorSeasonToggle = document.getElementById('priorSeasonToggle');
if (priorSeasonToggle) {{
  priorSeasonToggle.addEventListener('click', () => {{
    priorSeasonToggle.classList.toggle('active');
    document.getElementById('fullTable').classList.toggle('show-prior');
  }});
}}

function openProfile(playerId) {{
  const p = playerProfiles[playerId];
  if (!p) return;

  const photoUrl = `${{PLAYER_PHOTO_BASE}}/110x140/${{p.photo_code}}.png`;
  let gwRows = '';
  if (p.gw_history && p.gw_history.length) {{
    gwRows = `<strong class="block-label" style="font-size:0.85rem;">This Season</strong>
      <table class="modal-gw-table"><thead><tr><th>GW</th><th>Pts</th><th>Min</th><th>G</th><th>A</th></tr></thead><tbody>` +
      p.gw_history.map(h => `<tr><td>${{h.gameweek}}</td><td>${{h.total_points}}</td><td>${{h.minutes}}</td><td>${{h.goals_scored}}</td><td>${{h.assists}}</td></tr>`).join('') +
      `</tbody></table>`;
  }} else {{
    gwRows = `<p class="muted" style="font-size:0.85rem;">Gameweek-by-gameweek history not available yet &mdash; run the historical backfill to populate this.</p>`;
  }}

  let seasonRows = '';
  if (p.season_history && p.season_history.length) {{
    seasonRows = `<strong class="block-label" style="font-size:0.85rem;">Past Seasons</strong>
      <table class="modal-gw-table"><thead><tr><th>Season</th><th>Pts</th><th>Min</th><th>G</th><th>A</th><th>PPG*</th></tr></thead><tbody>` +
      p.season_history.map(h => `<tr><td>${{h.season}}</td><td>${{h.total_points}}</td><td>${{h.minutes}}</td><td>${{h.goals_scored}}</td><td>${{h.assists}}</td><td>${{h.points_per_game_est !== null ? h.points_per_game_est : '&mdash;'}}</td></tr>`).join('') +
      `</tbody></table>
      <p class="muted" style="font-size:0.7rem;margin-top:4px;">*Estimated from minutes &divide; 90 &mdash; past seasons only record total minutes, not actual appearances, so treat this as a rough guide rather than an exact figure.</p>`;
  }}

  let statusHtml = '';
  if (p.status && p.status !== 'a') {{
    const badgeColor = (p.status === 'd') ? '#BF8B32' : '#B0402A';
    statusHtml = `<div style="background:${{badgeColor}}1A;border:1px solid ${{badgeColor}};border-radius:8px;padding:8px;margin:10px 0;font-size:0.85rem;">
      <strong style="color:${{badgeColor}}">${{p.status_label}}</strong>${{p.chance_of_playing !== null && p.chance_of_playing !== undefined ? ` &mdash; ${{p.chance_of_playing}}% chance of playing` : ''}}
      ${{p.news ? `<br>${{p.news}}` : ''}}
    </div>`;
  }}

  const contrib = squadContributionTotal(playerId);
  const contribRow = contrib
    ? `<div><span>Contributed to your squad</span><span>${{contrib.total}} pts (${{contrib.weeks}} GW${{contrib.weeks > 1 ? 's' : ''}})</span></div>`
    : '';

  document.getElementById('modalBody').innerHTML = `
    <div class="modal-header">
      <img src="${{photoUrl}}" onerror="${{photoOnErrorAttr(p.photo_code)}}" alt="">
      <div><strong>${{p.full_name || p.name}}</strong><br><span class="muted">${{p.team || ''}} &middot; ${{POS_NAMES[p.position] || '?'}}</span></div>
    </div>
    ${{statusHtml}}
    <div class="modal-stats">
      <div><span>Price</span><span>£${{p.price}}m</span></div>
      <div><span>Total points</span><span>${{p.total_points}}</span></div>
      <div><span>Form</span><span>${{p.form}}</span></div>
      <div><span>Points/game</span><span>${{p.points_per_game}}</span></div>
      <div><span>Minutes</span><span>${{p.minutes}}</span></div>
      <div><span>Selected by</span><span>${{p.selected_by_percent}}%</span></div>
      ${{contribRow}}
    </div>
    ${{gwRows}}
    ${{seasonRows}}
  `;
  document.getElementById('profileModal').classList.add('open');
}}

function closeProfile() {{
  document.getElementById('profileModal').classList.remove('open');
}}

const countdownEl = document.getElementById('countdown');
if (countdownEl) {{
  const deadline = new Date(countdownEl.dataset.deadline).getTime();
  function updateCountdown() {{
    const diff = deadline - Date.now();
    if (diff <= 0) {{
      countdownEl.textContent = 'Deadline has passed';
      countdownEl.classList.add('urgent');
      return;
    }}
    const days = Math.floor(diff / 86400000);
    const hours = Math.floor((diff % 86400000) / 3600000);
    const mins = Math.floor((diff % 3600000) / 60000);
    const secs = Math.floor((diff % 60000) / 1000);
    countdownEl.textContent = days > 0
      ? `${{days}}d ${{hours}}h ${{mins}}m ${{secs}}s`
      : `${{hours}}h ${{mins}}m ${{secs}}s`;
    countdownEl.classList.toggle('urgent', diff < 3 * 3600000);
  }}
  updateCountdown();
  setInterval(updateCountdown, 1000);
}}
</script>
</body>
</html>"""


def main():
    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(build_html())
    print(f"Dashboard written -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
