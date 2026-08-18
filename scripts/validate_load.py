"""
validate_load.py - substrate trust checks after a nflverse load.

Row counts prove a load ran. These queries prove the data is USABLE: season
coverage, join integrity to the canonical player key, null rates on the columns
the models actually depend on, and the empirical margin distribution that the
simulation layer has to reproduce.

Read-only. Safe to run any time.

Usage:
    py -3 scripts/validate_load.py
    py -3 scripts/validate_load.py --keynumbers 1999
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"


def hdr(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}", flush=True)


def table_exists(con: duckdb.DuckDBPyConnection, t: str) -> bool:
    return con.execute(
        "SELECT count(*) FROM duckdb_tables() "
        "WHERE schema_name || '.' || table_name = ?", [t]).fetchone()[0] > 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--keynumbers", type=int, default=2006,
                    help="earliest season for the margin distribution")
    args = ap.parse_args()

    con = duckdb.connect(str(args.db), read_only=True)

    hdr("SEASON COVERAGE")
    targets = [
        ("raw.pbp", "season"), ("raw.stats_player_week", "season"),
        ("raw.snap_counts", "season"), ("raw.injuries", "season"),
        ("raw.participation" if table_exists(con, "raw.participation")
         else "raw.pbp_participation", "season"),
        ("raw.ftn_charting", "season"), ("raw.roster_weekly", "season"),
    ]
    print(f"{'table':<26}{'seasons':<14}{'rows':>12}")
    for t, col in targets:
        if not table_exists(con, t):
            continue
        cols = {c[0] for c in con.execute(f"SELECT * FROM {t} LIMIT 0").description}
        if col not in cols:
            # participation keys on nflverse_game_id and carries no season col
            if "nflverse_game_id" in cols:
                expr = "cast(split_part(nflverse_game_id, '_', 1) AS INTEGER)"
            elif "game_id" in cols:
                expr = "cast(split_part(game_id, '_', 1) AS INTEGER)"
            else:
                print(f"{t:<26}{'(no season col)':<14}")
                continue
            lo, hi, n = con.execute(
                f"SELECT min({expr}), max({expr}), count(*) FROM {t}").fetchone()
            print(f"{t:<26}{f'{lo}-{hi}':<14}{n:>12,}  (derived)")
            continue
        lo, hi, n = con.execute(
            f"SELECT min(cast({col} AS INTEGER)), max(cast({col} AS INTEGER)), "
            f"count(*) FROM {t}").fetchone()
        print(f"{t:<26}{f'{lo}-{hi}':<14}{n:>12,}")

    hdr("GAMES SPINE + FREE CLOSING LINES")
    rows = con.execute("""
        SELECT season, count(*) games,
               count(result) played,
               count(spread_line) has_spread,
               count(total_line) has_total,
               count(home_moneyline) has_ml
        FROM raw.games WHERE season >= 2020 GROUP BY season ORDER BY season
    """).fetchall()
    print(f"{'season':<9}{'games':>7}{'played':>8}{'spread':>8}{'total':>8}{'ml':>8}")
    for r in rows:
        print(f"{r[0]:<9}{r[1]:>7}{r[2]:>8}{r[3]:>8}{r[4]:>8}{r[5]:>8}")
    lo = con.execute(
        "SELECT min(season) FROM raw.games WHERE spread_line IS NOT NULL").fetchone()[0]
    tot = con.execute(
        "SELECT count(*) FROM raw.games WHERE spread_line IS NOT NULL").fetchone()[0]
    print(f"\nclosing spreads available from {lo}: {tot:,} games")

    hdr("PBP NULL RATES ON MODEL-CRITICAL COLUMNS")
    crit = ["epa", "wp", "success", "cpoe", "xpass", "air_yards",
            "qb_epa", "score_differential", "down", "yardline_100"]
    print(f"{'column':<24}{'non-null':>12}{'null %':>10}")
    total = con.execute("SELECT count(*) FROM raw.pbp").fetchone()[0]
    for c in crit:
        nn = con.execute(f"SELECT count({c}) FROM raw.pbp").fetchone()[0]
        print(f"{c:<24}{nn:>12,}{100 * (1 - nn / total):>9.1f}%")
    print(f"\n(high null % on epa/down is EXPECTED - includes timeouts, "
          f"kneels,\n end-of-quarter and other non-plays. Filter play_type "
          f"before modeling.)")

    hdr("ENTITY RESOLUTION READINESS")
    for col, label in [("passer_player_id", "passers"),
                       ("rusher_player_id", "rushers"),
                       ("receiver_player_id", "receivers")]:
        r = con.execute(f"""
            SELECT count(DISTINCT p.{col}) AS ids,
                   count(DISTINCT pl.gsis_id) AS matched
            FROM raw.pbp p
            LEFT JOIN raw.players pl ON pl.gsis_id = p.{col}
            WHERE p.{col} IS NOT NULL
        """).fetchone()
        pct = 100 * r[1] / r[0] if r[0] else 0
        print(f"  {label:<12} {r[1]:>6,} / {r[0]:>6,} distinct ids "
              f"resolve to raw.players  ({pct:.1f}%)")

    orphan = con.execute("""
        SELECT count(*) FROM raw.pbp p
        LEFT JOIN raw.games g ON g.game_id = p.game_id
        WHERE g.game_id IS NULL
    """).fetchone()[0]
    print(f"  pbp rows whose game_id is absent from raw.games: {orphan:,}")

    hdr(f"EMPIRICAL MARGIN DISTRIBUTION (>= {args.keynumbers})")
    print("The simulation layer must reproduce this shape. A normal "
          "distribution\naround the spread will misprice every one of these.\n")
    rows = con.execute(f"""
        SELECT abs(result) AS margin, count(*) AS n,
               round(100.0 * count(*) / sum(count(*)) OVER (), 2) AS pct
        FROM raw.games
        WHERE result IS NOT NULL AND season >= {args.keynumbers}
        GROUP BY margin ORDER BY n DESC LIMIT 12
    """).fetchall()
    print(f"{'margin':>8}{'games':>9}{'pct':>8}  {'':<3}")
    for m, n, pct in rows:
        bar = "#" * int(pct * 2.2)
        star = " *" if int(m) in (3, 7, 10, 6, 14, 4) else "  "
        print(f"{int(m):>8}{n:>9,}{pct:>7.2f}%{star} {bar}")
    print("\n  * = classic key number")

    hdr("MARKET CALIBRATION BASELINE")
    # SIGN CONVENTION, verified empirically against raw.games:
    #   result      = home_score - away_score  (home margin)
    #   spread_line = home perspective, POSITIVE means HOME IS FAVORED
    #   home covers when  result > spread_line
    # Getting this backwards inverts every edge in the system, so it is
    # asserted here rather than assumed anywhere downstream.
    r = con.execute(f"""
        SELECT round(avg(result - spread_line), 3),
               round(corr(result, spread_line), 3),
               count(*)
        FROM raw.games
        WHERE result IS NOT NULL AND spread_line IS NOT NULL
          AND season >= {args.keynumbers}
    """).fetchone()
    print(f"  avg(result - spread_line) : {r[0]:+.3f}   "
          f"(~0 = market unbiased; this is the number to beat)")
    print(f"  corr(result, spread_line) : {r[1]:+.3f}   "
          f"(must be POSITIVE - confirms home-perspective sign)")
    print(f"  games                     : {r[2]:,}")
    if r[1] is not None and r[1] < 0:
        print("  ** SIGN CONVENTION VIOLATED - investigate before modeling **")

    rows = con.execute(f"""
        WITH b AS (
            SELECT CASE
                     WHEN abs(spread_line) <= 3  THEN '0-3'
                     WHEN abs(spread_line) <= 7  THEN '3.5-7'
                     WHEN abs(spread_line) <= 10 THEN '7.5-10'
                     WHEN abs(spread_line) <= 14 THEN '10.5-14'
                     ELSE '14.5+' END AS bucket,
                   abs(spread_line) AS a,
                   CASE WHEN result > spread_line THEN 1
                        WHEN result < spread_line THEN 0 END AS home_cover
            FROM raw.games
            WHERE result IS NOT NULL AND spread_line IS NOT NULL
              AND season >= {args.keynumbers}
        )
        SELECT bucket, count(*) n,
               round(100.0 * avg(home_cover), 1) home_cover_pct
        FROM b WHERE home_cover IS NOT NULL
        GROUP BY bucket ORDER BY min(a)
    """).fetchall()
    print(f"\n{'spread':<12}{'games':>8}{'home cover %':>14}   "
          f"(all buckets should sit near 50)")
    for row in rows:
        flag = "  <-- OFF" if abs(row[2] - 50) > 6 else ""
        print(f"{row[0]:<12}{row[1]:>8,}{row[2]:>13.1f}%{flag}")

    push = con.execute(f"""
        SELECT count(*) FILTER (WHERE result = spread_line), count(*)
        FROM raw.games WHERE result IS NOT NULL AND spread_line IS NOT NULL
          AND season >= {args.keynumbers}
    """).fetchone()
    print(f"\n  pushes: {push[0]:,} / {push[1]:,} = {100 * push[0] / push[1]:.2f}%")

    hdr("KNOWN SUBSTRATE QUIRKS")
    print("  raw.injuries.season is DOUBLE while raw.pbp.season is BIGINT.")
    print("    -> cast on join, or normalize in the mart layer.")
    print("  raw.pbp_participation keys on 'nflverse_game_id', NOT 'game_id',")
    print("    and has no season column (derive: split_part(id,'_',1)).")
    print("    Carries offense_formation, offense_personnel, defenders_in_box,")
    print("    number_of_pass_rushers, was_pressure, route, defense_coverage_type")
    print("    -> 'route' is the route-participation input for prop projections.")

    con.close()
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
