"""qb_td_proj.py - backtest QB passing and touchdowns before projecting either.

NOT A BETTING SCRIPT. It reads no line, no price, no book.

WHY THESE THREE. The season projection covers receiving and rushing yards.
Quarterbacks were absent because nothing in the warehouse projects passing, and
touchdowns were absent because mart.player_game_usage has no TD column at all.
Both are buildable; whether they are worth building is a measurement, and the
stability work says they will not behave alike:

    receiving yards    +0.606      receiving TDs      +0.396
    yards per target   +0.473      TD per target      +0.221
    passing yards      +0.378      passing TDs        +0.426
    yards per attempt  +0.354      TD per attempt     +0.411

TOUCHDOWN REGRESSION IS A RECEIVER PHENOMENON, NOT A LAW. A quarterback throws
20-40 touchdowns, so the rate is a real signal about scheme and red-zone
quality. A receiver catches 2-10, and the rate is mostly Poisson noise. Any
model that applies one TD prior to both positions is wrong about one of them.

SAME CONSTRUCTION AS THE VALIDATED ONE, deliberately: volume shrunk lightly,
efficiency shrunk hard toward the league rate, times a constant games count.
Nothing new is invented so nothing new has to be justified.

Usage:
    py -3 scripts/qb_td_proj.py
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
SHRINK_VOL = 2.0     # games of league prior on volume
SHRINK_EFF = 40.0    # attempts/targets of league prior on efficiency


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


def load(con, kind, floor):
    """Player-seasons with the PRIOR season attached. Nothing from season S is
    read except the actual being scored."""
    if kind == "pass":
        vol, yds, tds = "attempts", "passing_yards", "passing_tds"
    else:
        vol, yds, tds = "targets", "receiving_yards", "receiving_tds"
    return con.execute(f"""
        WITH ps AS (
            SELECT season, player_id,
                   sum({vol})  AS vol,
                   sum({yds})  AS yds,
                   sum({tds})  AS tds,
                   count(*)    AS games
            FROM raw.stats_player_week
            WHERE season_type = 'REG'
            GROUP BY 1, 2
            HAVING sum({vol}) >= {floor}
        ),
        lg AS (
            SELECT season, sum(yds) / nullif(sum(vol), 0) AS lg_eff,
                   sum(tds) / nullif(sum(vol), 0)         AS lg_tdr
            FROM ps GROUP BY 1
        )
        SELECT c.season, c.games, c.yds, c.tds,
               p.games, p.vol, p.yds, p.tds, l.lg_eff, l.lg_tdr
        FROM ps c
        JOIN ps p ON p.player_id = c.player_id AND p.season = c.season - 1
        JOIN lg l ON l.season = p.season
        WHERE c.season BETWEEN ? AND ?
    """, [TEST[0] - 2, TEST[1]]).fetchall()


def run(con, kind, floor, label):
    rows = load(con, kind, floor)
    fit = [r for r in rows if r[0] <= FIT[1]]
    sc = [r for r in rows if r[0] > FIT[1]]
    if len(fit) < 20 or len(sc) < 40:
        log(f"  {label}: not enough rows ({len(fit)} fit, {len(sc)} score)")
        return None
    g_const = sum(r[1] for r in fit) / len(fit)
    vol_pg = sum(r[5] / r[4] for r in fit) / len(fit)   # league volume per game

    out = {"yards": {"naive": [], "proj": []},
           "tds": {"naive": [], "proj": []}}
    ratios = {"yards": [], "tds": []}
    for (_s, _g, act_y, act_td, pr_g, pr_vol, pr_y, pr_td, lg_eff, lg_tdr) in sc:
        # volume: light shrinkage, it is the stable part
        vpg = ((pr_vol / pr_g) * pr_g + vol_pg * SHRINK_VOL) / (pr_g + SHRINK_VOL)
        vol = vpg * g_const
        # efficiency and TD rate: heavy shrinkage toward the league rate
        eff = (pr_y + lg_eff * SHRINK_EFF) / (pr_vol + SHRINK_EFF)
        tdr = (pr_td + lg_tdr * SHRINK_EFF) / (pr_vol + SHRINK_EFF)
        py, ptd = vol * eff, vol * tdr
        out["yards"]["naive"].append((pr_y, act_y))
        out["yards"]["proj"].append((py, act_y))
        out["tds"]["naive"].append((pr_td, act_td))
        out["tds"]["proj"].append((ptd, act_td))
        if py > 0:
            ratios["yards"].append(act_y / py)
        if ptd > 0.5:
            ratios["tds"].append(act_td / ptd)

    log(f"  {label}  (fit {len(fit)}, scored {len(sc)}, games const "
        f"{g_const:.2f})")
    res = {}
    for stat in ("yards", "tds"):
        b, p = mae(out[stat]["naive"]), mae(out[stat]["proj"])
        gain = 1 - p / b
        bias = sum(x - y for x, y in out[stat]["proj"]) / len(out[stat]["proj"])
        rs = sorted(ratios[stat])
        q = (lambda f: rs[int(f * (len(rs) - 1))]) if rs else (lambda f: 0)
        verdict = "SHIP" if gain >= 0.05 else "does not clear 5%"
        log(f"    {stat:<7} naive MAE {b:>8.2f}   proj MAE {p:>8.2f}   "
            f"gain {gain * 100:>+5.1f}%   bias {bias:>+7.2f}   {verdict}")
        if rs:
            log(f"            band p10 {q(.10):.2f}  p50 {q(.50):.2f}  "
                f"p90 {q(.90):.2f}   corr {corr(out[stat]['proj']):+.3f}")
        res[stat] = {"gain": gain, "p10": q(.10), "p50": q(.50), "p90": q(.90),
                     "games": g_const}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    a = ap.parse_args()
    con = duckdb.connect(str(a.db), read_only=True)
    log("=" * 78)
    log("QB PASSING AND TOUCHDOWNS - does the same construction hold?")
    log("=" * 78)
    log("Scored on actual season totals. Fit 2021-22, scored 2023-25.")
    log("")
    qb = run(con, "pass", 200, "QB passing, 200+ prior attempts")
    log("")
    rec = run(con, "rec", 50, "Receiving, 50+ prior targets")
    con.close()

    log("")
    log("=" * 78)
    log("VERDICT")
    log("=" * 78)
    for name, r in (("QB passing yards", qb and qb["yards"]),
                    ("QB passing TDs", qb and qb["tds"]),
                    ("Receiving TDs", rec and rec["tds"])):
        if not r:
            continue
        log(f"  {name:<20}{r['gain'] * 100:>+6.1f}%   "
            f"{'ships' if r['gain'] >= 0.05 else 'DOES NOT SHIP'}")
    log("")
    log("  Each stat that ships carries its OWN band. Sharing receiving")
    log("  yards' band would misstate every one of them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
