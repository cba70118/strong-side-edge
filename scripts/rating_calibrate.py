"""rating_calibrate.py - should offense and defense be rated the same way?

NOT A BETTING SCRIPT. It never reads a line, a price or a book. The only thing
it scores against is the actual margin of the game.

THE QUESTION. stability.py found that defense is markedly less stable than
offense - year over year 0.233 against 0.331, offense winning 5 of 6 paired
same-metric comparisons, and def EPA/play with a split-half reliability of only
0.184. mart.team_rating does not know this. It shrinks both sides toward the
league mean with the same SHRINK_PLAYS constant and carries both sides of the
prior season at the same PRIOR_SEASON weight. If the stability finding is real
and it matters, loosening those two knobs per side should predict future games
better. If it is real but does not matter, the sweep will say so and the rating
stays as it is.

TWO KNOBS, PER SIDE.
  shrink   plays of league-average prior mixed in. Higher = trust the sample
           less. This is the WITHIN-season knob, and reliability sets it:
           a metric that disagrees with itself across halves of a season should
           be pulled harder toward the mean at the same sample size.
  prior    weight on last season's games. This is the ACROSS-season knob, and
           year-over-year stability sets it.

The two are separable and are swept separately, because a metric can be
reliable within a season and still not carry across one.

HONEST SCORING. Correlation between the rating differential and the actual
margin, on games from week 4 on, with every rating built strictly before its
own week (build_mart's leakage assertion is reproduced here). Correlation and
not MAE as the primary metric because correlation needs no fitted slope, so no
configuration can win by being better scaled. MAE is reported alongside using a
per-config OLS fit, as a sanity check that the two agree.

TUNE AND HOLDOUT ARE SPLIT. The grid is swept on 2020-2023 only. 2024-2025 is
never looked at until one winner has been chosen. A knob tuned and scored on
the same games will always look like it helped.

Usage:
    py -3 scripts/rating_calibrate.py
    py -3 scripts/rating_calibrate.py --min-season 2016
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"

ITERATIONS = 20        # matches build_mart.py
WEEK_DECAY = 0.97      # matches build_mart.py
MIN_PLAYS = 20         # matches build_mart.py

BASE_SHRINK = 250.0    # the current symmetric setting
BASE_PRIOR = 0.35      # the current symmetric setting

PRIOR_GRID = [0.10, 0.20, 0.35, 0.50, 0.70]
SHRINK_GRID = [100.0, 250.0, 450.0, 700.0, 1000.0]

TUNE_SEASONS = (2020, 2023)
HOLD_SEASONS = (2024, 2025)


def log(m: str = "") -> None:
    print(m, flush=True)


def corr(pairs):
    n = len(pairs)
    if n < 8:
        return None
    mx = sum(a for a, _ in pairs) / n
    my = sum(b for _, b in pairs) / n
    sxy = sum((a - mx) * (b - my) for a, b in pairs)
    sxx = sum((a - mx) ** 2 for a, _ in pairs)
    syy = sum((b - my) ** 2 for _, b in pairs)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def ols_mae(pairs):
    """Fit margin = a + b*x, return (mae, slope). Slope is points per rating."""
    n = len(pairs)
    mx = sum(a for a, _ in pairs) / n
    my = sum(b for _, b in pairs) / n
    sxy = sum((a - mx) * (b - my) for a, b in pairs)
    sxx = sum((a - mx) ** 2 for a, _ in pairs)
    b = sxy / sxx if sxx > 0 else 0.0
    a = my - b * mx
    return sum(abs(y - (a + b * x)) for x, y in pairs) / n, b


def adjust(rows, off_key, def_key):
    """Iterative opponent adjustment - identical to build_mart.adjust."""
    teams = sorted({r["team"] for r in rows})
    off = {t: 0.0 for t in teams}
    dfn = {t: 0.0 for t in teams}
    usable = [r for r in rows
              if r.get(off_key) is not None and r.get(def_key) is not None]
    if not usable:
        return off, dfn
    by_team: dict[str, list] = {}
    for r in usable:
        by_team.setdefault(r["team"], []).append(r)
    for _ in range(ITERATIONS):
        new_off, new_def = {}, {}
        for t in teams:
            mine = by_team.get(t)
            if not mine:
                new_off[t], new_def[t] = off[t], dfn[t]
                continue
            wsum = sum(r["w"] for r in mine)
            if wsum <= 0:
                new_off[t], new_def[t] = off[t], dfn[t]
                continue
            new_off[t] = sum(
                r["w"] * (r[off_key] - dfn.get(r["opponent"], 0.0)) for r in mine
            ) / wsum
            new_def[t] = sum(
                r["w"] * (r[def_key] - off.get(r["opponent"], 0.0)) for r in mine
            ) / wsum
        mo = sum(new_off.values()) / len(new_off)
        md = sum(new_def.values()) / len(new_def)
        off = {t: v - mo for t, v in new_off.items()}
        dfn = {t: v - md for t, v in new_def.items()}
    return off, dfn


def build_unshrunk(games, prior_season):
    """Point-in-time UNSHRUNK ratings for every (season, week, team).

    Shrinkage is deliberately NOT applied here: it is a post-hoc multiply, so
    holding the expensive opponent adjustment fixed lets the whole shrink grid
    be scored for free. Only the prior-season weight changes the adjustment.
    """
    out: dict[tuple, tuple] = {}
    seasons = sorted({g["season"] for g in games})
    for season in seasons:
        weeks = [g["week"] for g in games if g["season"] == season]
        if not weeks:
            continue
        max_week = max(weeks)
        prev_max = max([g["week"] for g in games
                        if g["season"] == season - 1] or [0])
        for week in range(1, max_week + 2):
            train = []
            for g in games:
                if g["season"] == season and g["week"] < week:
                    factor = WEEK_DECAY ** (week - g["week"])
                elif g["season"] == season - 1:
                    factor = prior_season * (
                        WEEK_DECAY ** (week + (prev_max - g["week"])))
                else:
                    continue
                # leakage assertion, same contract as build_mart
                if g["season"] == season and g["week"] >= week:
                    raise AssertionError(f"LEAKAGE {season} wk{week}")
                if g["season"] > season:
                    raise AssertionError("LEAKAGE future season")
                r = dict(g)
                r["w"] = (g["off_plays"] or 0) * factor
                train.append(r)
            if not train:
                continue
            o, d = adjust(train, "off_epa_play", "def_epa_play")
            plays: dict[str, int] = {}
            for r in train:
                plays[r["team"]] = plays.get(r["team"], 0) + (r["off_plays"] or 0)
            for t in set(o) | set(d):
                out[(season, week, t)] = (
                    o.get(t, 0.0), d.get(t, 0.0), plays.get(t, 0))
    return out


def score(off_tab, def_tab, k_off, k_def, gm, lo, hi):
    """gm = list of (season, week, home, away, margin)."""
    pairs = []
    for season, week, home, away, margin in gm:
        if not (lo <= season <= hi):
            continue
        h = off_tab.get((season, week, home))
        a = off_tab.get((season, week, away))
        hd = def_tab.get((season, week, home))
        ad = def_tab.get((season, week, away))
        if h is None or a is None or hd is None or ad is None:
            continue
        # shrink each side by its own constant, then net
        sh_h = h[0] * (h[2] / (h[2] + k_off)) - hd[1] * (hd[2] / (hd[2] + k_def))
        sh_a = a[0] * (a[2] / (a[2] + k_off)) - ad[1] * (ad[2] / (ad[2] + k_def))
        pairs.append((sh_h - sh_a, margin))
    if len(pairs) < 50:
        return None
    r = corr(pairs)
    mae, slope = ols_mae(pairs)
    return r, mae, slope, len(pairs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--min-season", type=int, default=2019)
    a = ap.parse_args()
    con = duckdb.connect(str(a.db), read_only=True)

    cols = ["game_id", "season", "week", "team", "opponent", "off_plays",
            "off_epa_play", "def_epa_play"]
    games = [dict(zip(cols, r)) for r in con.execute(f"""
        SELECT {', '.join(cols)} FROM mart.team_game
        WHERE off_plays >= {MIN_PLAYS} AND season >= {a.min_season - 1}
        ORDER BY season, week
    """).fetchall()]
    gm = con.execute(f"""
        SELECT season, week, team, opponent, margin FROM mart.team_game
        WHERE is_home AND margin IS NOT NULL AND week >= 4
          AND season >= {a.min_season} ORDER BY season, week
    """).fetchall()
    con.close()

    log("=" * 78)
    log("RATING CALIBRATION - is defense worth less than offense in the rating?")
    log("=" * 78)
    log(f"team-games {len(games):,}   scored games {len(gm):,}   "
        f"tune {TUNE_SEASONS[0]}-{TUNE_SEASONS[1]}  "
        f"holdout {HOLD_SEASONS[0]}-{HOLD_SEASONS[1]}")
    log("Scored against actual margin only. No market data is read anywhere.\n")

    log("building point-in-time ratings, one pass per prior-season weight...")
    tabs = {}
    for p in PRIOR_GRID:
        tabs[p] = build_unshrunk(games, p)
        log(f"  prior={p:.2f}  {len(tabs[p]):,} team-weeks")

    base = score(tabs[BASE_PRIOR], tabs[BASE_PRIOR], BASE_SHRINK, BASE_SHRINK,
                 gm, *TUNE_SEASONS)
    log(f"\nBASELINE (symmetric, shipping): prior {BASE_PRIOR} both sides, "
        f"shrink {BASE_SHRINK:.0f} both sides")
    log(f"  tune corr {base[0]:+.4f}   MAE {base[1]:.3f}   n {base[3]}")

    # --- sweep -----------------------------------------------------------
    results = []
    for po in PRIOR_GRID:
        for pd in PRIOR_GRID:
            for ko in SHRINK_GRID:
                for kd in SHRINK_GRID:
                    s = score(tabs[po], tabs[pd], ko, kd, gm, *TUNE_SEASONS)
                    if s:
                        results.append((s[0], s[1], po, pd, ko, kd, s[2]))
    results.sort(key=lambda r: -r[0])

    log(f"\n{len(results)} configurations swept on the tuning seasons.")
    log(f"\n  {'corr':>8}{'MAE':>8}{'pr_off':>8}{'pr_def':>8}"
        f"{'k_off':>8}{'k_def':>8}   note")
    for r in results[:6]:
        note = "def shrunk harder" if r[5] > r[4] else (
            "off shrunk harder" if r[4] > r[5] else "symmetric shrink")
        log(f"  {r[0]:>+8.4f}{r[1]:>8.3f}{r[2]:>8.2f}{r[3]:>8.2f}"
            f"{r[4]:>8.0f}{r[5]:>8.0f}   {note}")

    # Where does the SYMMETRIC family top out? That is the fair comparison -
    # otherwise the asymmetric winner is credited for gains that come from
    # simply retuning the shared knob.
    sym = [r for r in results if r[4] == r[5] and r[2] == r[3]]
    sym.sort(key=lambda r: -r[0])
    log(f"\n  best SYMMETRIC config: corr {sym[0][0]:+.4f}  "
        f"prior {sym[0][2]:.2f}  shrink {sym[0][4]:.0f}")
    best = results[0]
    log(f"  best ANY config:       corr {best[0]:+.4f}  "
        f"prior {best[2]:.2f}/{best[3]:.2f}  shrink {best[4]:.0f}/{best[5]:.0f}")
    log(f"  asymmetry buys {best[0] - sym[0][0]:+.4f} on the tuning seasons")

    # --- holdout ---------------------------------------------------------
    log("\n" + "=" * 78)
    log(f"HOLDOUT {HOLD_SEASONS[0]}-{HOLD_SEASONS[1]} - never used for tuning")
    log("=" * 78)
    hb = score(tabs[BASE_PRIOR], tabs[BASE_PRIOR], BASE_SHRINK, BASE_SHRINK,
               gm, *HOLD_SEASONS)
    hs = score(tabs[sym[0][2]], tabs[sym[0][3]], sym[0][4], sym[0][5],
               gm, *HOLD_SEASONS)
    ha = score(tabs[best[2]], tabs[best[3]], best[4], best[5], gm, *HOLD_SEASONS)
    log(f"  {'config':<34}{'corr':>9}{'MAE':>9}{'n':>6}")
    log(f"  {'shipping symmetric 0.35 / 250':<34}{hb[0]:>+9.4f}{hb[1]:>9.3f}{hb[3]:>6}")
    log(f"  {f'best symmetric {sym[0][2]:.2f} / {sym[0][4]:.0f}':<34}"
        f"{hs[0]:>+9.4f}{hs[1]:>9.3f}{hs[3]:>6}")
    log(f"  {f'best asymmetric {best[2]:.2f},{best[3]:.2f} / {best[4]:.0f},{best[5]:.0f}':<34}"
        f"{ha[0]:>+9.4f}{ha[1]:>9.3f}{ha[3]:>6}")

    gain = ha[0] - hs[0]
    log("")
    log("=" * 78)
    log("VERDICT")
    log("=" * 78)
    harder = best[5] > best[4] or best[3] < best[2]
    log(f"  asymmetric minus best symmetric, on the holdout: {gain:+.4f}")
    log(f"  tuned optimum shrinks defense harder: {'yes' if harder else 'NO'}")
    if gain >= 0.010 and harder:
        log("  CONFIRMED. The stability asymmetry transfers to rating")
        log("  construction. Apply it in build_mart.py.")
    elif not harder:
        log("  REFUTED on direction. The sweep did not want defense shrunk")
        log("  harder, so the stability finding does not transfer this way.")
    else:
        log("  REFUTED on size. Defense IS shrunk harder at the optimum, but")
        log("  the holdout gain is under the 0.010 bar set in advance. The")
        log("  rating is not the place where this asymmetry pays.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
