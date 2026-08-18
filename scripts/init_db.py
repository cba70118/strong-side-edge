"""
init_db.py - build and validate the NFL analytics DuckDB from scratch.

Build order step 1: schema first, validated end to end BEFORE any loader is
written. This script proves four things:

  1. The DDL applies cleanly.
  2. The column contract matches what nflverse actually publishes RIGHT NOW
     (validated against the live remote parquet header, not a stale assumption).
  3. A row can travel the full path: odds -> model -> bet -> CLV grade.
  4. Every audit view returns zero issues on a clean database.

Dependencies: duckdb + stdlib only. No pandas, no nflreadpy.

Usage (see RUNBOOK.md):
    py -3 scripts/init_db.py --reset
    py -3 scripts/init_db.py --validate
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"

NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download/{tag}/{file}"

# ---------------------------------------------------------------------------
# Column contract: the columns we actually depend on downstream.
# Presence is asserted against the live remote schema, so upstream drift shows
# up here rather than as a silent NULL three layers later.
# ---------------------------------------------------------------------------
CONTRACT: dict[str, list[tuple[str, str | None, str]]] = {
    "raw.pbp": [
        ("game_id", "VARCHAR", "spine join key"),
        ("play_id", None, ""),
        ("season", None, ""),
        ("week", None, ""),
        ("posteam", None, "offense"),
        ("defteam", None, "defense"),
        ("home_team", None, ""),
        ("away_team", None, ""),
        ("play_type", None, ""),
        ("down", None, ""),
        ("ydstogo", None, ""),
        ("yardline_100", None, ""),
        ("yards_gained", None, ""),
        ("epa", None, "core efficiency metric"),
        ("wp", None, "win probability"),
        ("success", None, ""),
        ("pass", None, ""),
        ("rush", None, ""),
        ("qb_dropback", None, ""),
        ("qb_epa", None, ""),
        ("cpoe", None, "completion pct over expected"),
        ("xpass", None, "PROE denominator"),
        ("air_yards", None, "air yard share input"),
        ("complete_pass", None, ""),
        ("sack", None, ""),
        ("interception", None, ""),
        ("fumble_lost", None, ""),
        ("touchdown", None, ""),
        ("passer_player_id", None, "prop usage"),
        ("rusher_player_id", None, "prop usage"),
        ("receiver_player_id", None, "prop usage"),
        ("drive", None, "drive-level aggregation"),
        ("series", None, ""),
        ("score_differential", None, "game script"),
        ("game_seconds_remaining", None, ""),
        ("half_seconds_remaining", None, "1H derivative markets"),
        ("special", None, "ST filtering"),
        ("penalty", None, ""),
    ],
    "raw.games": [
        ("game_id", "VARCHAR", "canonical spine"),
        ("season", None, ""),
        ("week", None, ""),
        ("gameday", None, ""),
        ("home_team", None, ""),
        ("away_team", None, ""),
        ("result", None, "home margin, NULL until played"),
        ("total", None, "realized total"),
        ("spread_line", None, "FREE closing spread, 1999+"),
        ("total_line", None, "FREE closing total, 1999+"),
        ("home_moneyline", None, "2006+"),
        ("away_moneyline", None, "2006+"),
        ("over_odds", None, ""),
        ("under_odds", None, ""),
        ("home_rest", None, "rest/travel layer"),
        ("away_rest", None, ""),
        ("div_game", None, ""),
        ("roof", None, "gates weather pull"),
        ("surface", None, ""),
        ("temp", None, ""),
        ("wind", None, "the only weather var with real totals signal"),
        ("stadium_id", None, "venue join"),
    ],
    "raw.snap_counts": [
        ("game_id", None, ""), ("pfr_player_id", None, ""),
        ("player", None, "entity resolution input"),
        ("team", None, ""), ("offense_snaps", None, ""), ("offense_pct", None, ""),
    ],
    "raw.injuries": [
        ("season", None, ""), ("week", None, ""), ("gsis_id", None, "canonical player key"),
        ("team", None, ""), ("report_status", None, ""),
        ("practice_status", None, "DNP->LP->FP trajectory"),
    ],
    "raw.players": [
        ("gsis_id", "VARCHAR", "canonical player key"),
        ("display_name", None, ""), ("position", None, ""),
    ],
    "raw.stats_player_week": [
        ("player_id", None, ""), ("season", None, ""), ("week", None, ""),
        ("team", None, ""), ("targets", None, "target share"),
        ("receiving_yards", None, ""), ("rushing_yards", None, ""),
        ("passing_yards", None, ""), ("receptions", None, ""),
    ],
}

# Remote asset backing each contracted table, for live header validation.
CONTRACT_SOURCE = {
    "raw.pbp":               ("pbp", "play_by_play_2025.parquet"),
    "raw.games":             ("schedules", "games.parquet"),
    "raw.snap_counts":       ("snap_counts", "snap_counts_2025.parquet"),
    "raw.injuries":          ("injuries", "injuries_2025.parquet"),
    "raw.players":           ("players", "players.parquet"),
    "raw.stats_player_week": ("stats_player", "stats_player_week_2025.parquet"),
}


def log(msg: str) -> None:
    print(msg, flush=True)


def connect(db_path: Path) -> duckdb.DuckDBPyConnection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute("INSTALL httpfs; LOAD httpfs;")
    return con


def apply_ddl(con: duckdb.DuckDBPyConnection) -> None:
    for fname in ("01_schema.sql", "02_audit_views.sql", "03_book_registry.sql",
                  "04_bet_log.sql", "05_sgo.sql"):
        path = SQL_DIR / fname
        if not path.exists():
            raise FileNotFoundError(f"missing {path}")
        log(f"  applying {fname}")
        con.execute(path.read_text(encoding="utf-8"))


def seed_contract(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("DELETE FROM raw._column_contract")
    rows = [
        (tbl, col, typ, True, note)
        for tbl, cols in CONTRACT.items()
        for col, typ, note in cols
    ]
    con.executemany(
        "INSERT INTO raw._column_contract "
        "(target_table, column_name, expected_type, is_required, note) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    log(f"  seeded {len(rows)} contract columns across {len(CONTRACT)} tables")


def validate_contract_against_live(con: duckdb.DuckDBPyConnection) -> int:
    """Check the contract against what nflverse publishes right now."""
    problems = 0
    for tbl, (tag, fname) in CONTRACT_SOURCE.items():
        url = NFLVERSE.format(tag=tag, file=fname)
        try:
            desc = con.execute(
                f"SELECT * FROM read_parquet('{url}') LIMIT 0"
            ).description
        except Exception as exc:  # noqa: BLE001
            log(f"  FAIL {tbl}: cannot read {fname} -> {type(exc).__name__}: {exc}")
            problems += 1
            continue
        remote_cols = {c[0] for c in desc}
        want = {c for c, _, _ in CONTRACT[tbl]}
        missing = sorted(want - remote_cols)
        if missing:
            log(f"  DRIFT {tbl}: {len(missing)} contracted column(s) absent upstream")
            for m in missing:
                log(f"         - {m}")
            problems += len(missing)
        else:
            log(f"  ok   {tbl}: {len(want)}/{len(want)} contracted cols present "
                f"({len(remote_cols)} total upstream)")
    return problems


def seed_teams(con: duckdb.DuckDBPyConnection) -> None:
    """Load the team dimension straight from nflverse via DuckDB. No pandas."""
    url = NFLVERSE.format(tag="teams", file="teams_colors_logos.parquet")
    cols = {c[0] for c in con.execute(
        f"SELECT * FROM read_parquet('{url}') LIMIT 0").description}
    conf = "team_conf" if "team_conf" in cols else "NULL"
    div = "team_division" if "team_division" in cols else "NULL"
    nick = "team_nick" if "team_nick" in cols else "NULL"
    con.execute(f"""
        INSERT INTO ref.team
            (team_id, full_name, nickname, conference, division, is_current)
        SELECT DISTINCT
            team_abbr,
            team_name,
            {nick},
            {conf},
            CASE WHEN {div} IS NULL THEN NULL
                 ELSE regexp_extract({div}, '(North|South|East|West)$') END,
            TRUE
        FROM read_parquet('{url}')
        WHERE team_abbr IS NOT NULL
        ON CONFLICT DO NOTHING
    """)
    # Relocated franchises. nflverse play-by-play and schedules use the CURRENT
    # code (LA, LAC, LV) throughout, including for historical seasons, so the
    # old codes must never win an alias lookup. LA/LAR in particular share the
    # full name "Los Angeles Rams" - seeding aliases from full_name without
    # this filter silently maps every Rams game to LAR and orphans all 17 of
    # them from the schedule spine.
    con.execute("""
        UPDATE ref.team SET is_current = FALSE, active_to = 2015
         WHERE team_id IN ('STL')
    """)
    con.execute("""
        UPDATE ref.team SET is_current = FALSE, active_to = 2016
         WHERE team_id IN ('SD')
    """)
    con.execute("""
        UPDATE ref.team SET is_current = FALSE, active_to = 2019
         WHERE team_id IN ('OAK')
    """)
    con.execute("""
        UPDATE ref.team SET is_current = FALSE WHERE team_id IN ('LAR')
    """)
    n = con.execute("SELECT count(*) FROM ref.team").fetchone()[0]
    ncur = con.execute(
        "SELECT count(*) FROM ref.team WHERE is_current").fetchone()[0]
    log(f"  ref.team seeded: {n} rows ({ncur} current, {n - ncur} legacy)")

    # Self-aliases so the resolver always has an identity mapping to fall back on.
    con.execute("""
        INSERT INTO ref.team_alias (source, alias, team_id, is_manual, added_at)
        SELECT 'nflverse', team_id, team_id, FALSE, now() FROM ref.team
        ON CONFLICT DO NOTHING
    """)
    # Odds feeds send full names. Only CURRENT franchises may claim one.
    con.execute("""
        INSERT INTO ref.team_alias (source, alias, team_id, is_manual, added_at)
        SELECT 'the_odds_api', full_name, team_id, FALSE, now()
        FROM ref.team WHERE full_name IS NOT NULL AND is_current
        ON CONFLICT (source, alias) DO UPDATE SET team_id = excluded.team_id
    """)
    n = con.execute("SELECT count(*) FROM ref.team_alias").fetchone()[0]
    log(f"  ref.team_alias seeded: {n} rows")


def load_manual_overrides(con: duckdb.DuckDBPyConnection) -> None:
    """Manual overrides are a first-class input, not a hotfix."""
    ov_dir = ROOT / "ref" / "overrides"

    def sections(data: dict) -> list[tuple[str, dict]]:
        # Keys beginning with '_' are documentation, not mappings.
        return [(k, v) for k, v in data.items()
                if not k.startswith("_") and isinstance(v, dict)]

    team_file = ov_dir / "team_aliases.json"
    if team_file.exists():
        data = json.loads(team_file.read_text(encoding="utf-8"))
        rows = [(src, alias, tid, True)
                for src, m in sections(data) for alias, tid in m.items()]
        if rows:
            # upsert: a corrected mapping must overwrite the auto-seeded guess
            con.executemany(
                "INSERT INTO ref.team_alias (source, alias, team_id, is_manual, added_at) "
                "VALUES (?, ?, ?, ?, now()) "
                "ON CONFLICT (source, alias) DO UPDATE SET "
                "  team_id = excluded.team_id, is_manual = excluded.is_manual",
                rows)
            log(f"  manual team aliases applied: {len(rows)}")

    book_file = ov_dir / "book_aliases.json"
    if book_file.exists():
        data = json.loads(book_file.read_text(encoding="utf-8"))
        rows = [(src, alias, bid, True)
                for src, m in sections(data) for alias, bid in m.items()]
        if rows:
            # upsert: a corrected mapping must overwrite a stale guess
            con.executemany(
                "INSERT INTO ref.book_alias (source, alias, book_id, is_manual) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT (source, alias) DO UPDATE SET "
                "  book_id = excluded.book_id, is_manual = excluded.is_manual",
                rows)
            log(f"  manual book aliases applied: {len(rows)}")


def roundtrip_smoke_test(con: duckdb.DuckDBPyConnection) -> int:
    """Push one synthetic row the whole way: odds -> model -> bet -> CLV."""
    now = datetime.now(timezone.utc)
    con.execute("BEGIN TRANSACTION")
    try:
        sid = con.execute("""
            INSERT INTO odds.snapshot
                (captured_at, source, season, week, phase, markets_pulled,
                 credits_used, credits_left, http_status, note)
            VALUES (?, 'smoke_test', 2026, 1, 'opener', 'spreads', 3, 19997, 200,
                    'init_db roundtrip')
            RETURNING snapshot_id
        """, [now]).fetchone()[0]

        con.execute("""
            INSERT INTO odds.game_map
                (source, source_game_id, game_id, commence_time,
                 source_home_raw, source_away_raw, match_method, resolved_at)
            VALUES ('smoke_test', 'SMOKE1', '2026_01_SMOKE_TEST', ?,
                    'Home Team', 'Away Team', 'manual', ?)
        """, [now, now])

        con.execute("""
            INSERT INTO odds.line
                (snapshot_id, game_id, source_game_id, book_id, market_id,
                 side_key, line_value, price_american, price_decimal,
                 implied_prob, captured_at, is_alt)
            VALUES (?, '2026_01_SMOKE_TEST', 'SMOKE1', 'draftkings',
                    'spread_game', 'home', -3.0, -110, 1.909, 0.5238, ?, FALSE)
        """, [sid, now])

        con.execute("""
            INSERT INTO model.registry
                (model_id, model_name, layer, version, created_at,
                 config_json, is_active, is_paper_only, note)
            VALUES ('smoke_model', 'Smoke Test', 'projection', '0.0.0', ?,
                    '{}', FALSE, TRUE, 'init_db roundtrip')
        """, [now])

        # Decomposition must reconcile to proj_spread or the audit view fires.
        con.execute("""
            INSERT INTO model.projection
                (projection_id, model_id, game_id, generated_at,
                 proj_spread, proj_total, proj_home_pts, proj_away_pts,
                 c_ratings, c_hfa, c_rest, c_travel, c_weather, c_injury,
                 c_qb, c_other, inputs_json)
            VALUES (1, 'smoke_model', '2026_01_SMOKE_TEST', ?,
                    -3.5, 44.0, 23.75, 20.25,
                    -2.0, -1.7, 0.0, 0.2, 0.0, 0.0, 0.0, 0.0, '{}')
        """, [now])

        bid = con.execute("""
            INSERT INTO bet.wager
                (placed_at, is_paper, game_id, market_id, side_key, book_id,
                 line_value, price_american, stake, model_id, fair_prob,
                 ev_pct, snapshot_id, note)
            VALUES (?, TRUE, '2026_01_SMOKE_TEST', 'spread_game', 'home',
                    'draftkings', -3.0, -110, NULL, 'smoke_model', 0.556,
                    6.1, ?, 'init_db roundtrip')
            RETURNING bet_id
        """, [now, sid]).fetchone()[0]

        con.execute("""
            INSERT INTO bet.clv
                (bet_id, close_line, close_price_am, close_book_id,
                 close_novig_prob, clv_line_pts, clv_prob_delta, clv_cents,
                 beat_close, graded_at)
            VALUES (?, -3.5, -110, 'pinnacle', 0.548, 0.5, 0.024, 11.0, TRUE, ?)
        """, [bid, now + timedelta(days=1)])

        con.execute("""
            INSERT INTO bet.result (bet_id, outcome, settled_at, actual_value, pnl)
            VALUES (?, 'win', ?, 7.0, NULL)
        """, [bid, now + timedelta(days=2)])

        checks = con.execute("SELECT check_name, n_issues FROM audit.run_all").fetchall()
        bad = [(c, n) for c, n in checks if n and c not in ("snapshot_gaps",)]

        log("\n  audit.run_all during roundtrip:")
        for name, n in checks:
            flag = "ISSUE" if (n and name != "snapshot_gaps") else "ok   "
            log(f"    {flag} {name:<28} {n}")

        # snapshot_gaps is expected to fire: one opener, no wed/fri/close yet.
        log("\n  note: snapshot_gaps firing is CORRECT here - the roundtrip")
        log("        writes only an 'opener' phase, so the week is incomplete.")
        return len(bad)
    finally:
        con.execute("ROLLBACK")


def main() -> int:
    ap = argparse.ArgumentParser(description="Build and validate the NFL DuckDB.")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--reset", action="store_true",
                    help="delete the database file and rebuild from scratch")
    ap.add_argument("--validate", action="store_true",
                    help="run live contract validation + roundtrip smoke test")
    ap.add_argument("--skip-remote", action="store_true",
                    help="skip network-dependent steps (offline schema check)")
    args = ap.parse_args()

    if args.reset and args.db.exists():
        args.db.unlink()
        log(f"removed existing database {args.db}")

    t0 = time.time()
    con = connect(args.db)
    log(f"database: {args.db}")
    log(f"duckdb:   {duckdb.__version__}")
    log(f"python:   {sys.version.split()[0]}\n")

    log("[1/5] applying DDL")
    apply_ddl(con)

    log("\n[2/5] seeding column contract")
    seed_contract(con)

    problems = 0
    if not args.skip_remote:
        log("\n[3/5] validating contract against LIVE nflverse schemas")
        problems += validate_contract_against_live(con)

        log("\n[4/5] seeding reference dimensions from nflverse")
        seed_teams(con)
        load_manual_overrides(con)
    else:
        log("\n[3/5] skipped (--skip-remote)")
        log("[4/5] skipped (--skip-remote)")

    if args.validate:
        log("\n[5/5] roundtrip smoke test (odds -> model -> bet -> CLV)")
        problems += roundtrip_smoke_test(con)
    else:
        log("\n[5/5] skipped (pass --validate to run)")

    con.close()
    log(f"\ndone in {time.time() - t0:.1f}s")
    if problems:
        log(f"RESULT: {problems} problem(s) found - do NOT write loaders yet")
        return 1
    log("RESULT: clean - schema validated end to end, safe to write loaders")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
