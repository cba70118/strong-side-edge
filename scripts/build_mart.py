"""
build_mart.py - build the modeling layer.

Applies sql/10_mart.sql (deterministic tables), then computes point-in-time
opponent-adjusted team ratings.

POINT-IN-TIME IS THE WHOLE POINT. A rating row for (season, week W) is built
from games played strictly BEFORE week W. Backtests that use end-of-season
ratings to "predict" mid-season games look brilliant and are worthless. The
builder asserts no training row violates the cutoff before it writes.

Opponent adjustment is iterative rather than a ridge solve, to keep the
dependency surface at duckdb + stdlib:

    adj_off[t] = weighted_mean_g( off_epa[t,g] - adj_def[opponent(g)] )
    adj_def[t] = weighted_mean_g( def_epa[t,g] - adj_off[opponent(g)] )

repeated to convergence, re-centered to league mean 0 each pass, then shrunk
toward 0 by plays observed. Prior-season games carry in at a decayed weight so
that week 1 is not an empty prior.

Usage:
    py -3 scripts/build_mart.py
    py -3 scripts/build_mart.py --skip-tables      # ratings only
    py -3 scripts/build_mart.py --evaluate         # + out-of-sample signal test
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"
MART_SQL = ROOT / "sql" / "10_mart.sql"

# --- tuning ---------------------------------------------------------------
ITERATIONS   = 20      # iterative adjustment passes
SHRINK_PLAYS = 250.0   # plays of "league average" prior mixed into each rating
PRIOR_SEASON = 0.35    # weight on prior-season games vs current-season
WEEK_DECAY   = 0.97    # per-week exponential recency decay
MIN_PLAYS    = 20      # ignore team-games with fewer plays than this

METRICS = [
    ("off_epa_play", "def_epa_play", "off_rating",      "def_rating"),
    ("off_pass_epa", "def_pass_epa", "off_pass_rating", "def_pass_rating"),
    ("off_rush_epa", "def_rush_epa", "off_rush_rating", "def_rush_rating"),
]


def log(m: str = "") -> None:
    print(m, flush=True)


def adjust(rows, off_key, def_key):
    """Iterative opponent adjustment. rows = list of dicts. Returns (off, def)."""
    teams = sorted({r["team"] for r in rows})
    off = {t: 0.0 for t in teams}
    dfn = {t: 0.0 for t in teams}

    usable = [r for r in rows
              if r.get(off_key) is not None and r.get(def_key) is not None]
    if not usable:
        return off, dfn

    for _ in range(ITERATIONS):
        new_off, new_def = {}, {}
        for t in teams:
            mine = [r for r in usable if r["team"] == t]
            if not mine:
                new_off[t], new_def[t] = off[t], dfn[t]
                continue
            wsum = sum(r["w"] for r in mine)
            if wsum <= 0:
                new_off[t], new_def[t] = off[t], dfn[t]
                continue
            # my offense, credited against the defense I faced
            new_off[t] = sum(
                r["w"] * (r[off_key] - dfn.get(r["opponent"], 0.0)) for r in mine
            ) / wsum
            # my defense, credited against the offense I faced
            new_def[t] = sum(
                r["w"] * (r[def_key] - off.get(r["opponent"], 0.0)) for r in mine
            ) / wsum
        # re-center so the league mean is 0 and ratings stay interpretable
        mo = sum(new_off.values()) / len(new_off)
        md = sum(new_def.values()) / len(new_def)
        off = {t: v - mo for t, v in new_off.items()}
        dfn = {t: v - md for t, v in new_def.items()}
    return off, dfn


def build_ratings(con: duckdb.DuckDBPyConnection) -> int:
    cols = ["game_id", "season", "week", "team", "opponent",
            "off_plays", "def_plays",
            "off_epa_play", "def_epa_play",
            "off_pass_epa", "def_pass_epa",
            "off_rush_epa", "def_rush_epa"]
    raw = con.execute(f"""
        SELECT {', '.join(cols)} FROM mart.team_game
        WHERE off_plays >= {MIN_PLAYS} ORDER BY season, week
    """).fetchall()
    games = [dict(zip(cols, r)) for r in raw]
    log(f"  team-game rows available: {len(games):,}")

    seasons = sorted({g["season"] for g in games})
    now = datetime.now(timezone.utc)
    out: list[tuple] = []
    leak_checks = 0

    for season in seasons:
        weeks = sorted({g["week"] for g in games if g["season"] == season})
        max_week = max(weeks) if weeks else 0
        for week in range(1, max_week + 2):   # +1 so we get an end-of-season row
            train = []
            for g in games:
                if g["season"] == season and g["week"] < week:
                    weeks_ago = week - g["week"]
                    factor = WEEK_DECAY ** weeks_ago
                elif g["season"] == season - 1:
                    weeks_ago = week + (max_week - g["week"])
                    factor = PRIOR_SEASON * (WEEK_DECAY ** weeks_ago)
                else:
                    continue
                r = dict(g)
                r["w"] = (g["off_plays"] or 0) * factor
                train.append(r)

            if not train:
                continue

            # --- leakage assertion -------------------------------------
            for r in train:
                if r["season"] == season and r["week"] >= week:
                    raise AssertionError(
                        f"LEAKAGE: {season} wk{week} trained on wk{r['week']}")
                if r["season"] > season:
                    raise AssertionError("LEAKAGE: future season in training set")
            leak_checks += len(train)

            ratings: dict[str, dict[str, float]] = {}
            for off_key, def_key, off_name, def_name in METRICS:
                o, d = adjust(train, off_key, def_key)
                for t in set(o) | set(d):
                    ratings.setdefault(t, {})[off_name] = o.get(t, 0.0)
                    ratings[t][def_name] = d.get(t, 0.0)

            for t, vals in ratings.items():
                mine = [r for r in train if r["team"] == t]
                plays = int(sum(r["off_plays"] or 0 for r in mine))
                n_games = len(mine)
                if n_games == 0:
                    continue
                # shrink toward league average by sample size
                k = plays / (plays + SHRINK_PLAYS)
                prior_w = 1.0 - k
                sh = {name: vals.get(name, 0.0) * k for _, _, a, b in METRICS
                      for name in (a, b)}
                out.append((
                    int(season), int(week), t, n_games, plays,
                    sh["off_rating"], sh["def_rating"],
                    sh["off_pass_rating"], sh["def_pass_rating"],
                    sh["off_rush_rating"], sh["def_rush_rating"],
                    sh["off_rating"] - sh["def_rating"],
                    prior_w, now,
                ))

    con.execute("DELETE FROM mart.team_rating")
    con.executemany("""
        INSERT INTO mart.team_rating
            (season, week, team, games_used, plays_used,
             off_rating, def_rating, off_pass_rating, def_pass_rating,
             off_rush_rating, def_rush_rating, net_rating, prior_weight, built_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, out)
    log(f"  leakage assertions passed on {leak_checks:,} training rows")
    log(f"  mart.team_rating rows written: {len(out):,}")
    return len(out)


def evaluate(con: duckdb.DuckDBPyConnection) -> None:
    """Out-of-sample: does the rating differential predict margin, and how does
    it compare against the closing spread on the same games?"""
    log("\n" + "=" * 74)
    log("OUT-OF-SAMPLE SIGNAL TEST")
    log("=" * 74)
    log("Each game is scored using ratings built BEFORE its week.\n")

    rows = con.execute("""
        WITH g AS (
            SELECT tg.game_id, tg.season, tg.week, tg.margin,
                   tg.spread_line_team AS spread, tg.is_home,
                   rh.net_rating AS my_net, ra.net_rating AS opp_net,
                   rh.games_used
            FROM mart.team_game tg
            JOIN mart.team_rating rh
              ON rh.season = tg.season AND rh.week = tg.week AND rh.team = tg.team
            JOIN mart.team_rating ra
              ON ra.season = tg.season AND ra.week = tg.week AND ra.team = tg.opponent
            WHERE tg.is_home AND tg.margin IS NOT NULL AND tg.spread_line_team IS NOT NULL
        )
        SELECT season, count(*) n,
               round(corr(my_net - opp_net, margin), 3)  AS rating_corr,
               round(corr(spread, margin), 3)            AS spread_corr,
               round(stddev_samp(margin), 2)             AS margin_sd
        FROM g WHERE week >= 4 GROUP BY season ORDER BY season
    """).fetchall()
    log(f"{'season':<9}{'games':>7}{'rating corr':>14}{'spread corr':>14}{'margin sd':>12}")
    for r in rows:
        log(f"{r[0]:<9}{r[1]:>7}{r[2]:>14}{r[3]:>14}{r[4]:>12}")

    agg = con.execute("""
        WITH g AS (
            SELECT tg.margin, tg.spread_line_team AS spread,
                   rh.net_rating - ra.net_rating AS rd, tg.week
            FROM mart.team_game tg
            JOIN mart.team_rating rh
              ON rh.season=tg.season AND rh.week=tg.week AND rh.team=tg.team
            JOIN mart.team_rating ra
              ON ra.season=tg.season AND ra.week=tg.week AND ra.team=tg.opponent
            WHERE tg.is_home AND tg.margin IS NOT NULL
              AND tg.spread_line_team IS NOT NULL AND tg.week >= 4
        )
        SELECT count(*), round(corr(rd, margin),3), round(corr(spread, margin),3),
               round(corr(rd, spread),3)
        FROM g
    """).fetchone()
    n, r_y2, r_y1, r_12 = agg
    log(f"\n  pooled (week >= 4), n = {n:,}")
    log(f"    corr(rating_diff, margin) : {r_y2}")
    log(f"    corr(spread,      margin) : {r_y1}   <- the benchmark")
    log(f"    corr(rating_diff, spread) : {r_12}   <- overlap with the market")

    # ---------------------------------------------------------------
    # THE DECISION-RELEVANT TEST.
    # Raw correlation is not the question - the ratings and the spread
    # overlap heavily, so of course both correlate with margin. The
    # question is whether the ratings carry information the spread does
    # NOT already contain. That is the partial correlation of margin with
    # rating_diff, CONTROLLING for the spread:
    #
    #   r(y,x2 | x1) = (r_y2 - r_y1*r_12) / sqrt((1-r_y1^2)(1-r_12^2))
    #
    # Near zero => the model is a worse-measured copy of the market and
    # has no standalone edge on sides, no matter how good it looks alone.
    # ---------------------------------------------------------------
    denom = ((1 - r_y1 ** 2) * (1 - r_12 ** 2)) ** 0.5
    partial = (r_y2 - r_y1 * r_12) / denom if denom else float("nan")

    log("\n" + "-" * 74)
    log("  INCREMENTAL SIGNAL vs THE CLOSING SPREAD")
    log("-" * 74)
    log(f"    partial corr(rating_diff, margin | spread) : {partial:+.4f}")

    # Does the market residual move with the rating disagreement at all?
    resid = con.execute("""
        WITH g AS (
            SELECT tg.margin - tg.spread_line_team AS mkt_resid,
                   rh.net_rating - ra.net_rating   AS rd,
                   tg.spread_line_team             AS spread
            FROM mart.team_game tg
            JOIN mart.team_rating rh
              ON rh.season=tg.season AND rh.week=tg.week AND rh.team=tg.team
            JOIN mart.team_rating ra
              ON ra.season=tg.season AND ra.week=tg.week AND ra.team=tg.opponent
            WHERE tg.is_home AND tg.margin IS NOT NULL
              AND tg.spread_line_team IS NOT NULL AND tg.week >= 4
        )
        SELECT round(corr(rd, mkt_resid), 4),
               round(regr_slope(margin_proxy, rd), 2)
        FROM (SELECT *, mkt_resid + spread AS margin_proxy FROM g)
    """).fetchone()
    log(f"    corr(rating_diff, market residual)        : {resid[0]:+.4f}")
    log(f"    points of margin per 1.0 rating unit      : {resid[1]}")

    log("")
    if abs(partial) < 0.06:
        log("    VERDICT: no incremental signal. The ratings are a noisier")
        log("    restatement of the closing spread - they overlap it at")
        log(f"    r={r_12} and add nothing once it is controlled for.")
        log("    Betting sides off this model would be betting the market")
        log("    against itself with extra variance.")
    elif partial > 0:
        log("    VERDICT: positive incremental signal detected. Worth")
        log("    pursuing, but confirm on held-out seasons before trusting.")
    else:
        log("    VERDICT: NEGATIVE incremental signal - where the model")
        log("    disagrees with the market, the market has been right.")
        log("    Fading our own disagreement would have been profitable.")

    log("")
    log("    This is the finding that justifies the architecture: the")
    log("    betting engine targets props and derivatives, while the")
    log("    projection lens stays a CLV-graded challenger until it earns")
    log("    its way in. See RUNBOOK Step 4.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--skip-tables", action="store_true")
    ap.add_argument("--evaluate", action="store_true")
    args = ap.parse_args()

    if not args.db.exists():
        log(f"database not found: {args.db}")
        return 1

    con = duckdb.connect(str(args.db))
    t0 = time.time()

    if not args.skip_tables:
        log("[1/3] applying sql/10_mart.sql")
        con.execute(MART_SQL.read_text(encoding="utf-8"))
        for t in ("mart.pbp_clean", "mart.team_game", "mart.player_game_usage"):
            n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            c = len(con.execute(f"SELECT * FROM {t} LIMIT 0").description)
            log(f"  {t:<28}{n:>10,} rows  {c:>3} cols")
    else:
        log("[1/3] skipped")

    log("\n[2/3] building point-in-time team ratings")
    build_ratings(con)

    log("\n[3/3] rating sanity")
    r = con.execute("""
        SELECT round(avg(off_rating),4), round(avg(def_rating),4),
               round(stddev_samp(net_rating),4), count(DISTINCT team)
        FROM mart.team_rating
    """).fetchone()
    log(f"  mean off_rating {r[0]} | mean def_rating {r[1]} "
        f"(both should be ~0 after centring)")
    log(f"  net_rating sd {r[2]} across {r[3]} teams")

    log("\n  top 5 net rating, end of 2025:")
    for row in con.execute("""
        SELECT team, round(off_rating,4), round(def_rating,4), round(net_rating,4), games_used
        FROM mart.team_rating
        WHERE season = 2025 AND week = (SELECT max(week) FROM mart.team_rating WHERE season=2025)
        ORDER BY net_rating DESC LIMIT 5
    """).fetchall():
        log(f"    {row[0]:<5} off {row[1]:>8}  def {row[2]:>8}  net {row[3]:>8}  ({row[4]} gm)")

    if args.evaluate:
        evaluate(con)

    con.close()
    log(f"\ndone in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
