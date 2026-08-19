"""qb_games_test.py - is the flat games constant the best available?

THE COMPLAINT THAT STARTED THIS. Every 17-game 2025 quarterback projects about
18% below his own actual, while a 14-game quarterback projects flat. That
pattern is not a passing model, it is a games constant: the projection is built
on QB_GAMES_CONST games regardless of who the player is.

The constant is not arbitrary. Quarterbacks with 200+ attempts play a mean of
12.83 games the following season and only 25.4% play all 17. So a projection
that assumed a full season would be biased high on the population.

The open question is whether the CONSTANT should be a per-player number. That
requires games played to carry year over year, which is measured here, not
assumed. Two comparisons on the same holdout:

    flat      vol_per_game * mean_games
    player    vol_per_game * shrunk(player games)

Falsifier registered as h-0015: carry below +0.15, or holdout gain under 2%,
and the flat constant stays.

Usage:
    py -3 scripts/qb_games_test.py
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"

FIT = (2021, 2022)
TEST = (2023, 2025)
SHRINK_VOL = 2.0
SHRINK_EFF = 40.0


def log(m=""):
    print(m, flush=True)


def mae(p):
    return sum(abs(a - b) for a, b in p) / len(p)


def corr(p):
    n = len(p)
    mx = sum(a for a, _ in p) / n
    my = sum(b for _, b in p) / n
    sxy = sum((a - mx) * (b - my) for a, b in p)
    sxx = sum((a - mx) ** 2 for a, _ in p)
    syy = sum((b - my) ** 2 for _, b in p)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else 0.0


def load(con):
    return con.execute("""
        WITH ps AS (
            SELECT season, player_id,
                   sum(attempts)      AS vol,
                   sum(passing_yards) AS yds,
                   sum(passing_tds)   AS tds,
                   count(*)           AS games
            FROM raw.stats_player_week
            WHERE season_type = 'REG'
            GROUP BY 1, 2
            HAVING sum(attempts) >= 200
        ),
        lg AS (
            SELECT season, sum(yds) / nullif(sum(vol), 0) AS lg_eff
            FROM ps GROUP BY 1
        )
        SELECT c.season, c.games, c.yds, p.games, p.vol, p.yds, l.lg_eff
        FROM ps c
        JOIN ps p ON p.player_id = c.player_id AND p.season = c.season - 1
        JOIN lg l ON l.season = p.season
        WHERE c.season BETWEEN ? AND ?
    """, [TEST[0] - 2, TEST[1]]).fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    a = ap.parse_args()
    con = duckdb.connect(str(a.db), read_only=True)
    rows = load(con)
    con.close()

    fit = [r for r in rows if r[0] <= FIT[1]]
    sc = [r for r in rows if r[0] > FIT[1]]
    g_const = sum(r[1] for r in fit) / len(fit)
    vol_pg = sum(r[4] / r[3] for r in fit) / len(fit)

    log("=" * 78)
    log("QB GAMES: is the flat constant beatable? (h-0015)")
    log("=" * 78)
    log(f"fit {len(fit)} rows, scored {len(sc)} rows")
    log(f"flat constant {g_const:.2f} games   league volume {vol_pg:.2f} att/gm")
    log("")

    # --- the carry, on the fit set only ------------------------------------
    car = corr([(r[3], r[1]) for r in fit])
    log(f"  games played, year over year (fit set):  {car:+.3f}")
    log(f"  {'REFUTED at the first gate' if car < 0.15 else 'clears the +0.15 gate; testing the projection'}")
    log("")

    # --- both projections on the same holdout ------------------------------
    best = None
    for k in (0, 2, 4, 6, 8, 12, 20, 40):
        flat, per = [], []
        for (_s, act_g, act_y, pr_g, pr_vol, pr_y, lg_eff) in sc:
            vpg = ((pr_vol / pr_g) * pr_g + vol_pg * SHRINK_VOL) / (pr_g + SHRINK_VOL)
            eff = (pr_y + lg_eff * SHRINK_EFF) / (pr_vol + SHRINK_EFF)
            g_p = (pr_g + g_const * k) / (1 + k) if k else pr_g
            flat.append((vpg * g_const * eff, act_y))
            per.append((vpg * g_p * eff, act_y))
        b, p = mae(flat), mae(per)
        gain = 1 - p / b
        if best is None or gain > best[1]:
            best = (k, gain, p, b)
        log(f"  shrink k={k:>3}   flat MAE {b:>8.1f}   per-player MAE {p:>8.1f}"
            f"   gain {gain * 100:>+5.1f}%")

    k, gain, p, b = best
    log("")
    log("=" * 78)
    log("VERDICT")
    log("=" * 78)
    log(f"  best per-player variant: k={k}, gain {gain * 100:+.1f}%")
    if car < 0.15 or gain < 0.02:
        log("  REFUTED. The flat constant stays. The projection is not wrong;")
        log("  it is an availability-discounted expectation that the page never")
        log("  declared. The fix is labeling and a stated games basis.")
    else:
        log("  CONFIRMED. Ship the per-player games expectation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
