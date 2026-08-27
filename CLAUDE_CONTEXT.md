# FPL Dashboard — System Context

> Digest file for an AI assistant. Paste or upload this once at the start of a conversation to load full context on what this system is, how it's built, what data it holds, and why certain things work the way they do. This covers the *system* — for this week's actual numbers, use the "Discuss with Claude" button's live briefing instead (it's generated fresh from the database on every dashboard build and pasted as the opening message).

## Identity

- Project: `fpl-agent` — a personal Fantasy Premier League (FPL) dashboard.
- Team: "Real Bugsy United", FPL entry/team ID `6738704`.
- Repo: `Askbugsy/fpl-agent` on GitHub.
- Live URL: `askbugsy.github.io/fpl-agent/`.
- Cost: **$0**. Static GitHub Pages hosting + GitHub Actions automation + FPL's public API. No server, no database service, no paid API calls anywhere in the core pipeline.

## Architecture

Pipeline (`.github/workflows/fpl-update.yml`, scheduled Tuesdays + manual trigger):

1. `main.py` — fetches FPL's public API (bootstrap-static, fixtures, this manager's squad picks for the current gameweek), writes everything into `data/fpl.db` (SQLite), refreshes per-gameweek history for every player who's ever been in the squad, prints a console summary.
2. `generate_dashboard.py` — reads `data/fpl.db`, builds one self-contained `docs/index.html` (inline CSS/JS, Chart.js from CDN, no build step, no framework).
3. `update_status.py` — stamps `README.md` with the last-pull timestamp.
4. Commits `data/fpl.db` + `docs/` + `README.md` back to `main`. GitHub Pages auto-deploys from there (~under a minute).

Supporting files:
- `src/fpl_client.py` — thin wrapper around FPL's public REST API. No authentication anywhere (see "Known quirks" below for why).
- `src/db.py` — SQLite schema plus every query/save function; this is where almost all the actual logic (suggestion engines, projections, the What Could Have Been simulator) lives.
- `config.py` — `TEAM_ID`, `MY_WATCHLIST` (manual per-position picks), `CLAUDE_CHAT_URL` (optional, see below).
- `backfill_history.py` + `.github/workflows/backfill-history.yml` — separate, occasional job that pulls full gameweek-by-gameweek history for all ~700 FPL players. Run once at season start, re-run rarely. Not part of the weekly pipeline (too many API calls to run every week for players you don't own).

## Data model (`data/fpl.db`, SQLite)

- `player_snapshots(player_id, snapshot_date, ...)` — one row per player per day the pipeline runs. Name, team, position, price, total_points (real season-wide total, universal across all managers), form, points-per-game, minutes, ownership %, photo code, injury status, chance-of-playing %, news text.
- `squad_picks(entry_id, gameweek, player_id, ...)` — one row per player per gameweek *for this specific manager's squad*. squad_position (1-11 starters, 12-15 bench), multiplier (0=benched, 1=starter, 2=captain, 3=triple captain), captain/vice flags, gw_points (points that player scored *that gameweek, while in this squad*), selling_price, purchase_price. This table is the full season's transfer history by construction — diffing consecutive gameweeks' player sets reveals every transfer made.
- `entry_summary(entry_id, gameweek, ...)` — bank, team_value, gw_points, gw_rank, overall_rank, one row per gameweek.
- `gameweek_summary(gameweek, ...)` — league-wide average/highest score and deadline_time per gameweek.
- `player_gw_history(player_id, season, gameweek, ...)` — real per-player, per-gameweek total_points/minutes/goals/assists, independent of squad ownership. Current season populated incrementally (only for ever-owned players, every weekly run); past seasons populated once via the full backfill.
- `teams`, `fixtures` — reference data, including FPL's own 1–5 fixture difficulty rating (FDR) per side.

## Dashboard structure (5 tabs)

1. **Squad** — "Discuss with Claude" button (top); Manager Stats card (points, rank, squad value, bank vs. league average/highest); Current Squad pitch view with a metric toggle (Points / Opponent / Price / Selling Price / FDR / Form / Ownership % / Price Δ); Historical Contributors (players since transferred out, with what they scored *while owned*).
2. **Tips** — Best Formation for upcoming fixtures (compares every valid FPL formation); Captain Suggestions (form × fixture favourability, restricted to your actual starting XI, with an automatic vice-captain fallback note); Chip Timing (double/blank gameweek detection).
3. **Moves** — Transfer Suggestions (urgency-scored, budget-aware replacement candidates); Watchlist (one data-driven pick + one manual pick per position); Movers & Shakers (form trend chart); Top Value Picks (points-per-game per £1m chart).
4. **Explore** — Full sortable, position-filterable player table, all ~700 players.
5. **History** — Squad History (a gameweek slider walking every recorded squad snapshot, with pitch/bench/transfers-in-out); **What Could Have Been** (see below), nested as a second card in this same tab.

Every player name across the whole dashboard is clickable and opens a shared profile modal (photo, price, season stats, gameweek-by-gameweek history, past-season history, and — if the player has ever been in this squad — a "Contributed to your squad" line).

## Computed features (all deterministic/rule-based — no ML, no LLM in the core pipeline)

- **Captain suggestion**: `score = form × (6 - fixture_difficulty) / 5`, scaled to 0 for unavailable players and by FPL's own chance-of-playing % for doubtful ones. Only considers your actual starting XI.
- **Formation optimizer**: tries every valid FPL formation (3-4-3 through 5-2-3); for a fixed formation the best XI is just the top-N scorers per position group (mathematically optimal since scoring is additive across groups with no cross-position interaction). Compares all formations, recommends the highest-projected one.
- **Transfer suggestions**: `urgency = form_drop×2 + price_drop×10 + injury_urgency` (injury_urgency itself scaled by 100-minus-chance-of-playing% for a doubtful player). Tiers: High ≥5, Medium ≥2, Low >0. Suggests up to 3 same-position replacements you can actually afford (selling price + bank).
- **Chip timing**: scans upcoming fixtures for your squad's clubs; flags a double gameweek (Triple Captain/Bench Boost signal) or a blank gameweek (Free Hit signal).
- **Watchlist**: per position, the single best value-score player (points-per-game ÷ price) plus your manually configured pick, each with form/price trend vs. the previous data pull.
- **"Contributed to your squad"**: per-player total, summed *only* across the gameweeks `squad_picks` actually has them in — not their universal season total. A player bought partway through the season isn't credited for points scored (for someone else's team) before you owned them.
- **"What Could Have Been"**: for every distinct 15-man squad held this season (a new "epoch" starts each time the 15-man player set genuinely changes via a transfer — captain/formation tweaks alone don't count), freezes that gameweek's starting XI/bench/captain exactly as picked, then projects it forward using every player's REAL points each subsequent gameweek (from `player_gw_history`, independent of who actually owned them). Plotted as cumulative-points lines against "Actual" (what really happened). Deliberately doesn't model hypothetical re-captaining, formation changes, or the Bench Boost chip within an epoch — it's "what if you'd never touched this exact squad again," not a fully optimal replay.
- **"Discuss with Claude"**: a button that assembles a plain-text weekly briefing (manager stats, captain suggestion, chip timing, top transfer suggestions, recommended formation, What Could Have Been vs. Actual, next deadline) from data already computed on that page load — zero extra API calls, zero cost — copies it to the clipboard, and opens a chat. Defaults to a brand-new `claude.ai` chat each time; set `CLAUDE_CHAT_URL` in `config.py` to one persistent conversation URL instead, so every week's briefing lands in the same ongoing thread for longitudinal discussion.

## Known quirks and design decisions worth knowing

- **FPL's public API can never show a pending (pre-deadline) gameweek's squad picks — not even to the account owner.** This is intentional on FPL's side, so managers can't scout rivals' teams before the deadline locks. A real authenticated workaround (FPL's PingOne-based OIDC identity provider, extracting a refresh token from browser localStorage) was built, tested live, and ultimately **abandoned**: the provider only accepts confidential-client authentication methods (a client secret), which a public script fundamentally can't hold — confirmed via the provider's own discovery document. The dashboard now simply shows "not available yet" for a still-pending gameweek and picks it up automatically once the deadline passes, same limitation every manager has for a rival's squad.
- **Stale pending-gameweek data is actively guarded against.** Two safety-net functions (`clear_pending_gw_points`, `clear_pending_manager_stats`) null out any leftover points/value/bank figures whenever a gameweek is confirmed still-pending (its public endpoint 404s) — this fixed a real incident where a mid-development bug had briefly saved a pending gameweek's squad with mislabeled points from the previous week. Self-heals automatically once the real gameweek is actually scored.
- **`current_gw` resolution deliberately prefers**: a genuinely live gameweek → the next editable one → the last known one (in that order) — not simply "whichever FPL marks `is_current`", since FPL keeps that flag true through the entire gap between gameweeks, which would otherwise show a stale, already-decided squad while you're mid-transfer for the next one.
- **Player photos** fall back through three tiers: current-season CDN bucket → legacy CDN bucket → an inline SVG silhouette — because some players don't have a current-season photo yet, and the CDN 403s on certain image sizes.
- **A real embedded live chat (calling the Claude API directly from the page) was scoped but not built.** It would require a paid Anthropic API key, a backend to hold that key securely (can't live in public client-side HTML), and hosting for that backend. The free link-out "Discuss with Claude" button was built instead as the practical first step; the proactive weekly-briefing text it generates could later feed a paid, tool-using embedded chat if that's ever built, without needing to change the underlying data model.
- **There is no iframe-based embed of Claude anywhere.** `claude.ai` blocks being iframed by other sites, and the Claude API itself is a server-to-server REST endpoint with no browsable chat UI — there's nothing to put in an iframe `src`.

## Evolution (condensed timeline)

Redesign → full warm cream/terracotta visual identity, Fraunces serif, sticky tab bar → iterative tab reorganization (renamed/merged several times: Today→Recommendations→Tips; Squad moved first; Manager Stats moved onto Squad; Watchlist folded into Moves; a History tab added with a gameweek slider; What Could Have Been added as its own tab, then folded into History as a subsection) → injury/availability status wired into every suggestion engine → squad-transfer tracking and a "previous squad" / Historical Contributors view added → the authenticated pending-squad experiment (built, debugged extensively, ultimately reverted as structurally infeasible) → stale pending-gameweek data bugs found and fixed with permanent safety nets → "Contributed to your squad" and "What Could Have Been" added (season-long squad-decision tracking) → "Discuss with Claude" free link-out chat integration added.
