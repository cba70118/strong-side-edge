"""
drive_sim.py - an INDEPENDENT game simulator built on possessions.

Why this exists: every other model here is market-anchored. The margin and
totals models take the market's mean and shape the distribution around it,
which is right for pricing and useless for forming an opinion. This one starts
from team strength and simulates football, so it can disagree with the market.

It will probably be WORSE than the market at predicting outcomes - we measured
opponent-adjusted ratings at -0.04 partial correlation against the closing
spread. That is not a reason to skip it. Its value is a view we understand, the
ability to say why a line moved, and a score distribution for markets the
market prices lazily.

STRUCTURE
  1. Drive outcome  P(TD | FG | PUNT | TURNOVER | DOWNS | MISSED_FG | ...)
     conditioned on starting field position and on offense-vs-defense strength.
  2. Field position transitions: where the next drive starts given how the last
     one ended, sampled from the empirical distribution.
  3. Drive count per game, sampled empirically.
  4. Monte Carlo over alternating possessions.

Key numbers are NOT pasted on. Scores are built from 3s and 7s, so margins of
3 and 7 emerge from the arithmetic. That is the structural advantage over the
margin model, whose bolted-on multipliers failed conditionally.

THE NULL TEST, run by `validate`: fed league-average strength, the simulator
must reproduce the observed drive-outcome mix, points per game, and the shape
of the real margin and total distributions. If it cannot reproduce reality with
neutral inputs, nothing it says about a specific game is worth reading. This is
the discipline the golf project's sim EV kept failing.

Usage:
    py -3 scripts/drive_sim.py fit
    py -3 scripts/drive_sim.py validate
    py -3 scripts/drive_sim.py game --home KC --away BAL --season 2026 --week 1
"""

from __future__ import annotations

import argparse
import json
import random
from bisect import bisect
from collections import Counter, defaultdict
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"

FP_EDGES = [20, 40, 60, 70, 80, 100]        # yards to opponent goal

# STRENGTH RESOLUTION. The first version used four edges - five buckets - and
# that was too coarse to price a game. Two matchups three points apart in the
# market (ARI at LAC -10.5, NO at DET -7.0) landed in the same bucket pair and
# returned the IDENTICAL simulated margin, because bucketing quantises team
# strength before it ever reaches the drive model. Fitted strength runs from
# about -0.20 to +0.21 (1st to 99th percentile, sd 0.089), so the old top
# bucket of "0.06 and above" swallowed a third of the real range.
#
# Two changes: more edges across the range that actually occurs, and linear
# INTERPOLATION between neighbouring cells at sample time so strength is
# continuous rather than stepped. Thin cells still shrink toward their
# field-position pool, so widening the grid does not buy noise.
STRENGTH_EDGES = [-0.15, -0.10, -0.05, 0.0, 0.05, 0.10, 0.15]
OUTCOMES = ["TD", "FG", "MISSED_FG", "PUNT", "TURNOVER", "DOWNS",
            "SAFETY", "END_HALF"]

# HOME FIELD. The first version applied none at all, which biased every home
# team by the full HFA. Realised home margin is 1.744 points over 2020-2025
# regular season (n=1,615); the market prices 1.512 over the same span. We use
# the realised figure because this model's job is to be independent of the
# market. Applied as a strength delta, half to each side, scaled by the
# points-per-strength slope measured at fit time. Neutral-site games get zero.
HFA_POINTS = 1.744

# GLOBAL TOTALS CALIBRATION. Fed real matchups with point-in-time ratings the
# simulator ran +2.27 points hot on 1,184 games (2021-2025, week 4 on). The
# neutral null test could NOT see this - it reported only +0.89 - because a
# neutral benchmark is ambiguous: league-average games total 45.75 and
# near-pick'em games total 44.15, a 1.60-point spread that is twice the drift
# being chased. The bias is identified only against real matchups, where no
# benchmark has to be chosen.
#
# Mechanism is Jensen: points respond convexly to strength, so spreading real
# strength around zero raises the mean total above the neutral value. Scaling
# possessions is the lever that moves the total without touching the scoring
# lattice. Swept 1.000 / 0.960 / 0.948 / 0.935 - bias +2.48 / +0.60 / +0.06 /
# -0.41, MAE 10.95 / 10.64 / 10.61 / 10.58, correlation flat at 0.18 - so 0.948
# is taken on the bias, not on MAE, which keeps improving for the wrong reason.
# Multiplies any drive_scale passed in, so market tuning still composes.
DRIVE_SCALE_BASE = 0.948

# Fraction of a team's drives that fall in the 4th quarter. Behavior changes
# sharply there and nowhere else - measured, see fit_state().
Q4_SHARE = 0.28


def score_state(diff: int) -> str:
    """Score state from the possessing team's perspective."""
    if diff == 0:
        return "tied"
    if -3 <= diff <= -1:
        return "trail_1_3"
    if -8 <= diff <= -4:
        return "trail_4_8"
    if diff < -8:
        return "trail_9p"
    if 1 <= diff <= 3:
        return "lead_1_3"
    return "lead_4p"
POINTS = {"TD": 7, "FG": 3, "SAFETY": -2, "MISSED_FG": 0, "PUNT": 0,
          "TURNOVER": 0, "DOWNS": 0, "END_HALF": 0}

# A TOUCHDOWN IS NOT WORTH 7. It is worth 6, 7 or 8, and which one it is
# decides whether the lattice of reachable scores has holes in it. With a flat
# 7 the simulator can only build totals from 3s and 7s, so totals that need a 6
# or an 8 came out thin - the validate step flagged 46 as the worst at 0.94pp
# and labeled the cause in a NOTE rather than fixing it.
#
# Measured on 8,248 try plays, 2020-2025 REG (post-2015 rules, so the whole
# sample is on the current 15-yard extra point):
#   extra point good   94.73%   of 7,454 attempts
#   two-point good     47.61%   of 794 attempts, a 9.63% share of all tries
TD_POINTS = ((6, 0.0981), (7, 0.8561), (8, 0.0458))   # E = 6.948, not 7.0

# THE TRY IS A DECISION, NOT A COIN FLIP. Drawing 6/7/8 independently per
# touchdown filled the total lattice exactly as intended (46 went from 0.94pp
# error to 0.04pp) and destroyed the margin: key numbers went 2.48pp to 4.29pp.
# The reason is that a real two-point try is taken to convert an awkward margin
# INTO a key one - the standard chart goes for two when trailing by 2, 5, 10,
# 16 or 18 - so it CONCENTRATES margins on key numbers. An independent 4.6%
# flip does the opposite. Model the decision and the lattice fills without the
# margin damage; model the coin flip and you trade one for the other.
XP_GOOD = 0.9473            # 7,061 of 7,454, 2020-2025 REG
TWO_PT_GOOD = 0.4761        # 378 of 794
TWO_PT_CHART = {2, 5, 10, 16, 18}   # deficit AFTER the touchdown, before the try
TWO_PT_BASE = 0.030         # off-chart tries: desperation, weather, analytics
# Share of possessions late enough for the chart to be consulted. Swept
# 0.28 / 0.40 / 0.50 / 0.65 / 1.00: the key-number error is flat across the
# range (1.57 to 1.91pp) so this choice is not load-bearing, and 0.40 is taken
# on football grounds - roughly the back end of the third quarter onward, which
# is when coaches actually start playing the chart. The simulated two-point
# rate lands at 5.90% against a real 9.63%; the chart set {2,5,10,16,18} plus a
# 3% off-chart base cannot reach the real rate, and forcing it there with a
# larger random component made the key numbers worse. Under-firing is the
# conservative direction.
TWO_PT_WINDOW = 0.40


def try_points(deficit_after_td: int, late: bool, rng) -> int:
    """Points from the try. `deficit_after_td` is how far the scoring team
    still trails once the six is on the board; negative means they lead."""
    go = (late and deficit_after_td in TWO_PT_CHART) or rng.random() < TWO_PT_BASE
    if go:
        return 2 if rng.random() < TWO_PT_GOOD else 0
    return 1 if rng.random() < XP_GOOD else 0

# Non-offensive scoring (return and defensive touchdowns) is not attributable
# to a drive. Rate stays at the value calibrated against real points per game -
# the raw 2020-2025 count implies 1.83ppg, but part of that is already inside
# the drive outcomes, and re-deriving it here would double count. Only the
# POINT VALUE becomes stochastic, for the same lattice reason as above.
NON_DRIVE_PPG = 1.36


def td_points(rng) -> int:
    """Draw the value of a touchdown including the try."""
    u = rng.random()
    c = 0.0
    for v, p in TD_POINTS:
        c += p
        if u < c:
            return v
    return 7


def outcome_points(o: str, rng) -> int:
    """Point value of a drive outcome. Only TD is stochastic."""
    return td_points(rng) if o == "TD" else POINTS[o]


def log(m: str = "") -> None:
    print(m, flush=True)


def fp_bucket(yl: float) -> int:
    return bisect(FP_EDGES, yl - 0.0001)


def st_bucket(s: float) -> int:
    return bisect(STRENGTH_EDGES, s)


class DriveModel:
    def __init__(self):
        self.outcome = {}      # (fp,st) -> (outcomes, cumulative probs)
        self.trans = {}        # prev outcome -> (starts, cumulative probs)
        self.drives = []       # empirical drives per team per game
        self.q4 = {}           # score_state -> {outcome: multiplier}
        self.centers = []      # mean fitted strength within each strength cell
        self.meta = {}

    # -------------------------------------------------------- interpolation
    def _pmf(self, fp: int, sb: int) -> list[float]:
        """Outcome probabilities for one fitted cell, undoing the cumsum."""
        cum = self.outcome.get((fp, sb))
        if cum is None:
            cum = self.outcome[(fp, len(STRENGTH_EDGES) // 2)]
        out, prev = [], 0.0
        for c in cum:
            out.append(c - prev)
            prev = c
        return out

    def strength_pmf(self, fp: int, s: float) -> list[float]:
        """Outcome probabilities at a CONTINUOUS strength.

        Linear blend of the two fitted cells whose centers bracket s. Beyond
        the end centers we clamp rather than extrapolate - the fit has no
        evidence out there and extrapolating a probability vector is how you
        manufacture a 40-point favorite out of nothing.
        """
        cs = self.centers
        if not cs:
            return self._pmf(fp, st_bucket(s))
        if s <= cs[0]:
            return self._pmf(fp, 0)
        if s >= cs[-1]:
            return self._pmf(fp, len(cs) - 1)
        hi = bisect(cs, s)
        lo = hi - 1
        span = cs[hi] - cs[lo]
        w = 0.0 if span <= 0 else (s - cs[lo]) / span
        a, b = self._pmf(fp, lo), self._pmf(fp, hi)
        return [(1.0 - w) * x + w * y for x, y in zip(a, b)]

    # -------------------------------------------------------------- fitting
    def fit(self, con) -> None:
        rows = con.execute("""
            SELECT d.start_yl, d.outcome,
                   coalesce(ro.off_rating, 0) + coalesce(rd.def_rating, 0) AS strength
            FROM mart.drive d
            LEFT JOIN mart.team_rating ro
                   ON ro.season = d.season AND ro.week = d.week
                  AND ro.team = d.posteam
            LEFT JOIN mart.team_rating rd
                   ON rd.season = d.season AND rd.week = d.week
                  AND rd.team = d.defteam
            -- BASE RATES ARE EARLY-GAME ONLY. The Q4 multiplier below is the
            -- ratio of Q4 behavior to early behavior, so if the base already
            -- contained Q4 drives the adjustment would be applied twice. That
            -- mistake cost 2 points of total and compressed margin SD from
            -- 14.2 to 11.9 by suppressing scoring in leading states.
            WHERE d.outcome <> 'OTHER' AND d.start_qtr <= 3
        """).fetchall()

        cells = defaultdict(Counter)
        for yl, out, st in rows:
            cells[(fp_bucket(yl), st_bucket(st))][out] += 1

        # The center of each strength cell is its MEAN fitted strength, not the
        # midpoint of its edges. The end cells are open-ended and the strength
        # distribution is not uniform inside any of them, so midpoints would
        # put the interpolation knots in the wrong place.
        st_sum = defaultdict(float)
        st_n = Counter()
        for _, _, st in rows:
            b = st_bucket(st)
            st_sum[b] += st
            st_n[b] += 1
        self.centers = [
            (st_sum[b] / st_n[b]) if st_n[b] else
            (STRENGTH_EDGES[min(b, len(STRENGTH_EDGES) - 1)])
            for b in range(len(STRENGTH_EDGES) + 1)
        ]

        # pooled fallback per field-position bucket, for thin strength cells
        pooled = defaultdict(Counter)
        for (fp, _), c in cells.items():
            pooled[fp].update(c)

        for key, c in cells.items():
            fp = key[0]
            base = pooled[fp]
            n = sum(c.values())
            # shrink a thin cell toward its field-position pool
            k = n / (n + 150.0)
            probs = []
            for o in OUTCOMES:
                p_cell = c.get(o, 0) / n if n else 0.0
                p_pool = base.get(o, 0) / sum(base.values())
                probs.append(k * p_cell + (1 - k) * p_pool)
            tot = sum(probs)
            cum, run = [], 0.0
            for p in probs:
                run += p / tot
                cum.append(run)
            self.outcome[key] = cum

        # transitions: where does the opponent start after each outcome
        tr = con.execute("""
            WITH seq AS (
                SELECT game_id, fixed_drive, posteam, outcome, start_yl,
                       lead(start_yl) OVER (PARTITION BY game_id
                                            ORDER BY fixed_drive) AS nxt,
                       lead(posteam)  OVER (PARTITION BY game_id
                                            ORDER BY fixed_drive) AS nxt_team
                FROM mart.drive)
            SELECT outcome, nxt FROM seq
            WHERE nxt IS NOT NULL AND nxt_team <> posteam
        """).fetchall()
        buckets = defaultdict(list)
        for out, nxt in tr:
            buckets[out].append(float(nxt))
        for out, vals in buckets.items():
            vals.sort()
            self.trans[out] = vals

        self.drives = [r[0] for r in con.execute("""
            SELECT count(*) FROM mart.drive GROUP BY game_id, posteam
        """).fetchall()]

        # ---- game state -------------------------------------------------
        # A 3-point game is overwhelmingly a late field goal. Teams trailing by
        # 1-3 in Q4 kick at 24.9% against 17.2% earlier; teams trailing 4-8 go
        # for it on downs at 18.7% against 4.4%. Independent drives cannot
        # produce that, which is why the first fit reproduced margin-of-7 to
        # within 0.3pp and missed margin-of-3 by 7.4pp. The multiplier below is
        # the Q4 behavior divided by the Q1-3 behavior in the same score
        # state, pooled over field position because the effect is strategic
        # rather than positional.
        st_rows = con.execute("""
            SELECT CASE WHEN start_qtr <= 3 THEN 'early' ELSE 'q4' END AS phase,
                   CASE WHEN start_score_diff = 0 THEN 'tied'
                        WHEN start_score_diff BETWEEN -3 AND -1 THEN 'trail_1_3'
                        WHEN start_score_diff BETWEEN -8 AND -4 THEN 'trail_4_8'
                        WHEN start_score_diff < -8 THEN 'trail_9p'
                        WHEN start_score_diff BETWEEN 1 AND 3 THEN 'lead_1_3'
                        ELSE 'lead_4p' END AS st,
                   outcome, count(*)
            FROM mart.drive WHERE outcome <> 'OTHER' AND start_score_diff IS NOT NULL
            GROUP BY 1,2,3
        """).fetchall()
        tab = defaultdict(Counter)
        for phase, st, out, n in st_rows:
            tab[(phase, st)][out] += n
        for st in ("tied","trail_1_3","trail_4_8","trail_9p","lead_1_3","lead_4p"):
            e, q = tab.get(("early", st)), tab.get(("q4", st))
            if not e or not q:
                continue
            te, tq = sum(e.values()), sum(q.values())
            mult = {}
            for o in OUTCOMES:
                pe = e.get(o, 0) / te
                pq = q.get(o, 0) / tq
                # shrink toward 1 when the q4 cell is thin
                k = tq / (tq + 200.0)
                raw = (pq / pe) if pe > 1e-6 else 1.0
                mult[o] = 1.0 + k * (raw - 1.0)
            self.q4[st] = mult

        self.meta = {
            "drives_fitted": len(rows),
            "cells": len(self.outcome),
            "mean_drives_per_team": sum(self.drives) / len(self.drives),
        }
        self.meta["points_per_strength"] = self.calibrate_slope()
        self.meta["total_per_strength"] = self.calibrate_total_slope()
        self.meta["total_per_drive_scale"] = self.calibrate_drive_slope()

    def calibrate_slope(self, n_sims: int = 6000) -> float:
        """Points of margin per unit of strength differential.

        Needed to express home-field advantage - a quantity the world states in
        POINTS - inside a model whose only lever is strength. Measured by
        simulating a symmetric strength split at several magnitudes and taking
        the least-squares slope through the origin. Only meaningful once
        strength is continuous; under the old bucketing this was a step
        function and the regression was meaningless.
        """
        xs, ys = [], []
        for d in (-0.08, -0.04, 0.04, 0.08):
            sims = self.simulate(d / 2.0, -d / 2.0, n_sims=n_sims, hfa=0.0)
            xs.append(d)
            ys.append(sum(h - a for h, a in sims) / len(sims))
        base = sum(ys) / len(ys) * 0.0     # slope through origin, no intercept
        num = sum(x * (y - base) for x, y in zip(xs, ys))
        den = sum(x * x for x in xs)
        return (num / den) if den else 0.0

    def calibrate_drive_slope(self, n_sims: int = 6000) -> float:
        """Points of game total per unit of drive-count scaling."""
        xs, ys = [], []
        for ds in (0.85, 0.95, 1.05, 1.15):
            sims = self.simulate(0.0, 0.0, n_sims=n_sims, hfa=0.0,
                                 drive_scale=ds)
            xs.append(ds)
            ys.append(sum(h + a for h, a in sims) / len(sims))
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        den = sum((x - mx) ** 2 for x in xs)
        return (num / den) if den else 0.0

    def calibrate_total_slope(self, n_sims: int = 6000) -> float:
        """Points of GAME TOTAL per unit of common strength offset.

        The companion to points_per_strength, and usefully orthogonal to it:
        raising both sides together moves the total and leaves the margin
        alone, while splitting them moves the margin and leaves the total
        alone. Two independent controls are what let a caller tune the
        simulator onto the market's spread AND total at the same time, instead
        of shifting its output afterwards and smearing the key numbers.
        """
        xs, ys = [], []
        for c in (-0.06, -0.03, 0.03, 0.06):
            sims = self.simulate(c, c, n_sims=n_sims, hfa=0.0)
            xs.append(c)
            ys.append(sum(h + a for h, a in sims) / len(sims))
        mx = sum(xs) / len(xs)
        my = sum(ys) / len(ys)
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        den = sum((x - mx) ** 2 for x in xs)
        return (num / den) if den else 0.0

    # ------------------------------------------------------------ sampling
    def sample_outcome(self, yl: float, strength: float, rng,
                       state: str | None = None) -> str:
        probs = self.strength_pmf(fp_bucket(yl), strength)
        if state is None or state not in self.q4:
            r, run = rng.random(), 0.0
            for o, p in zip(OUTCOMES, probs):
                run += p
                if r <= run:
                    return o
            return OUTCOMES[-1]
        # re-weight the base distribution by the Q4 behavioral multiplier
        m = self.q4[state]
        w = [p * m.get(o, 1.0) for p, o in zip(probs, OUTCOMES)]
        tot = sum(w)
        r, run = rng.random() * tot, 0.0
        for o, x in zip(OUTCOMES, w):
            run += x
            if r <= run:
                return o
        return OUTCOMES[-1]

    def sample_start(self, prev_outcome: str, rng) -> float:
        vals = self.trans.get(prev_outcome) or self.trans["PUNT"]
        return vals[rng.randrange(len(vals))]

    def simulate(self, home_strength: float, away_strength: float,
                 n_sims: int = 10000, seed: int = 7, hfa: float | None = None,
                 drive_scale: float = 1.0):
        """(home_strength, away_strength) are off_rating + opposing def_rating.

        `hfa` is home-field advantage IN POINTS. None uses the fitted default;
        pass 0.0 for a neutral-site game. It is converted to a strength delta
        with the slope measured at fit time and split evenly, so the home side
        gains half and the away side loses half - a symmetric margin shift that
        leaves the total unchanged.
        """
        rng = random.Random(seed)
        pts_per_strength = self.meta.get("points_per_strength") or 0.0
        hfa_pts = HFA_POINTS if hfa is None else hfa
        d = (hfa_pts / pts_per_strength / 2.0) if pts_per_strength else 0.0
        home_strength += d
        away_strength -= d
        out = []
        nd = self.drives
        self._n_try = 0        # tries taken, for the 2-point rate check
        self._n_two = 0
        drive_scale *= DRIVE_SCALE_BASE
        for _ in range(n_sims):
            n_drives = nd[rng.randrange(len(nd))]
            if drive_scale != 1.0:
                # POSSESSIONS ARE THE OTHER WAY A GAME GETS TO A LOW TOTAL.
                # Strength clamps at the edge of the fitted range, so a 39-point
                # market total is unreachable by making both offenses worse -
                # the simulator floors around 42. Real 39-point games are also
                # SHORTER: run-heavy, clock-draining, fewer possessions each.
                # Scaling the drive count reaches those totals through the
                # mechanism that actually produces them, and correctly drops
                # margin variance at the same time.
                # STOCHASTIC ROUNDING, not int(round(...)). Rounding the
                # scaled drive count to an integer makes the game total a STEP
                # function of drive_scale: with a fixed seed, a change of 0.013
                # flipped enough games between 10 and 11 drives to swing the
                # simulated total by 3.2 points, and any solver tuning onto a
                # market number oscillated instead of converging. Carrying the
                # fractional part as a probability makes the expected drive
                # count exactly proportional to the scale, and the response
                # smooth.
                scaled = n_drives * drive_scale
                base = int(scaled)
                n_drives = max(6, base + (1 if rng.random() < scaled - base
                                          else 0))
            pts = {"home": 0, "away": 0}
            prev = "PUNT"
            yl = 75.0
            pos = "away"           # away receives the opening kickoff
            total_poss = n_drives * 2
            for i in range(total_poss):
                s = home_strength if pos == "home" else away_strength
                # Q4 is the closing share of possessions; behavior there is
                # conditioned on the live score from the offense's viewpoint.
                state = None
                if i >= total_poss * (1.0 - Q4_SHARE):
                    diff = (pts[pos]
                            - pts["home" if pos == "away" else "away"])
                    state = score_state(diff)
                o = self.sample_outcome(yl, s, rng, state)
                foe = "home" if pos == "away" else "away"
                if o == "TD":
                    pts[pos] += 6
                    self._n_try += 1
                    late = i >= total_poss * (1.0 - TWO_PT_WINDOW)
                    d = pts[foe] - pts[pos]
                    got = try_points(d, late, rng)
                    if got == 2 or (got == 0 and late and d in TWO_PT_CHART):
                        self._n_two += 1
                    pts[pos] += got
                else:
                    p = POINTS[o]
                    if p >= 0:
                        pts[pos] += p
                    else:
                        pts[foe] += -p
                yl = self.sample_start(o, rng)
                prev = o
                pos = "home" if pos == "away" else "away"
            # non-offensive scoring, split evenly, Poisson-ish in 7s
            for side in ("home", "away"):
                if rng.random() < NON_DRIVE_PPG / 2 / 7:
                    pts[side] += td_points(rng)

            # OVERTIME. Without it the simulator returned 7.1% ties against a
            # real 0.3%, and every one of those unresolved games is a margin
            # that never got created. Since OT is usually decided by a field
            # goal, this is also a direct source of the missing 3-point games.
            if pts["home"] == pts["away"]:
                first = "away" if rng.random() < 0.5 else "home"
                other = "home" if first == "away" else "away"
                for poss in range(6):
                    side = first if poss % 2 == 0 else other
                    st = score_state(pts[side] - pts[other if side == first
                                                     else first])
                    o = self.sample_outcome(75.0, home_strength if side == "home"
                                            else away_strength, rng, st)
                    p_ = (6 + try_points(pts[other if side == first else first]
                                         - pts[side] - 6, True, rng)
                          ) if o == "TD" else POINTS[o]
                    if p_ >= 0:
                        pts[side] += p_
                    else:
                        pts["home" if side == "away" else "away"] += -p_
                    # after both have had one possession, first lead ends it
                    if poss >= 1 and pts["home"] != pts["away"]:
                        break
            out.append((pts["home"], pts["away"]))
        return out

    # ------------------------------------------------------------ persist
    def save(self, path: Path):
        path.write_text(json.dumps({
            "outcome": {f"{k[0]}|{k[1]}": v for k, v in self.outcome.items()},
            "trans": self.trans, "drives": self.drives, "q4": self.q4,
            "centers": self.centers, "meta": self.meta,
        }), encoding="utf-8")

    @classmethod
    def load(cls, path: Path):
        d = json.loads(path.read_text(encoding="utf-8"))
        m = cls()
        m.outcome = {tuple(int(x) for x in k.split("|")): v
                     for k, v in d["outcome"].items()}
        m.trans = d["trans"]
        m.drives = d["drives"]
        m.q4 = d.get("q4", {})     # persisted separately; absent = no state model
        m.centers = d.get("centers", [])
        m.meta = d["meta"]
        return m


MODEL_PATH = ROOT / "data" / "drive_model.json"


def cmd_fit(con, a) -> int:
    m = DriveModel()
    m.fit(con)
    m.save(MODEL_PATH)
    log(f"fitted on {m.meta['drives_fitted']:,} drives")
    log(f"  cells (field position x strength): {m.meta['cells']}")
    log(f"  mean drives per team per game    : {m.meta['mean_drives_per_team']:.2f}")
    log(f"  points of margin per unit strength: {m.meta['points_per_strength']:.1f}")
    log(f"  points of total  per unit strength: {m.meta['total_per_strength']:.1f}")
    log(f"  home field advantage applied      : {HFA_POINTS:.2f} pts")
    log(f"  saved -> {MODEL_PATH}")

    log("\n  scoring rate by field position, weakest vs strongest offense:")
    log(f"    {'field pos':<14}{'weak TD%':>10}{'strong TD%':>12}")
    labels = ["1-20", "21-40", "41-60", "61-70", "71-80", "81-99"]
    for fp in range(len(FP_EDGES)):
        lo = m.outcome.get((fp, 0)); hi = m.outcome.get((fp, len(STRENGTH_EDGES)))
        if lo and hi:
            log(f"    {labels[fp]:<14}{lo[0]*100:>9.1f}%{hi[0]*100:>11.1f}%")
    return 0


def cmd_validate(con, a) -> int:
    m = DriveModel.load(MODEL_PATH)
    log("=" * 74)
    log("NULL TEST - neutral inputs must reproduce reality")
    log("=" * 74)
    log("Fed league-average strength on both sides, the simulator has to")
    log("recover the real game. If it cannot, nothing it says about a")
    log("specific matchup is worth reading.\n")

    sims = m.simulate(0.0, 0.0, n_sims=a.sims)
    hs = [h for h, _ in sims]; as_ = [a2 for _, a2 in sims]
    allpts = hs + as_
    margins = [h - a2 for h, a2 in sims]
    totals = [h + a2 for h, a2 in sims]

    # LIKE FOR LIKE. The simulator is fed two EQUAL teams, so its margin
    # spread contains only within-game randomness. League-wide margin SD
    # (14.18) also contains between-team mismatch, so comparing to it would
    # condemn a correct model. The right target is the residual SD around the
    # spread - what is left once team strength is accounted for - and the right
    # key-number target is games the market made a coin flip.
    act = con.execute("""
        SELECT avg(total), stddev_samp(total), avg(abs(result)),
               stddev_samp(result - spread_line), count(*)
        FROM raw.games
        WHERE season BETWEEN 2018 AND 2025 AND result IS NOT NULL
          AND spread_line IS NOT NULL AND abs(spread_line) <= 2.5
    """).fetchone()
    act_pts = con.execute("""
        SELECT avg(pts), stddev_samp(pts) FROM (
          SELECT home_score AS pts FROM raw.games
           WHERE season BETWEEN 2020 AND 2025 AND result IS NOT NULL
             AND spread_line IS NOT NULL AND abs(spread_line) <= 2.5
          UNION ALL SELECT away_score FROM raw.games
           WHERE season BETWEEN 2020 AND 2025 AND result IS NOT NULL
             AND spread_line IS NOT NULL AND abs(spread_line) <= 2.5)
    """).fetchone()

    def stat(v):
        n = len(v); mu = sum(v) / n
        sd = (sum((x - mu) ** 2 for x in v) / (n - 1)) ** 0.5
        return mu, sd

    mu_p, sd_p = stat(allpts); mu_t, sd_t = stat(totals); mu_m, sd_m = stat(margins)
    log(f"  {'metric':<26}{'simulated':>12}{'actual':>10}{'diff':>9}")
    log(f"  {'points per team':<26}{mu_p:>12.2f}{act_pts[0]:>10.2f}"
        f"{mu_p-act_pts[0]:>+9.2f}")
    log(f"  {'points sd':<26}{sd_p:>12.2f}{act_pts[1]:>10.2f}"
        f"{sd_p-act_pts[1]:>+9.2f}")
    log(f"  {'game total':<26}{mu_t:>12.2f}{act[0]:>10.2f}{mu_t-act[0]:>+9.2f}")
    log(f"  {'total sd':<26}{sd_t:>12.2f}{act[1]:>10.2f}{sd_t-act[1]:>+9.2f}")
    log(f"  {'margin sd (resid vs line)':<26}{sd_m:>12.2f}{act[3]:>10.2f}"
        f"{sd_m-act[3]:>+9.2f}")

    log("\n  KEY NUMBERS - do 3 and 7 emerge without being pasted on?")
    sim_c = Counter(abs(x) for x in margins)
    n = len(margins)
    # near-pick'em games only: the market's own judgement that the teams are
    # equal, which is the condition the simulator was run under
    real = dict(con.execute("""
        SELECT abs(result), count(*)*1.0/sum(count(*)) OVER ()
        FROM raw.games
        WHERE season BETWEEN 2018 AND 2025 AND result IS NOT NULL
          AND spread_line IS NOT NULL AND abs(spread_line) <= 2.5
        GROUP BY 1
    """).fetchall())
    n_pk = con.execute("""
        SELECT count(*) FROM raw.games
        WHERE season BETWEEN 2018 AND 2025 AND result IS NOT NULL
          AND spread_line IS NOT NULL AND abs(spread_line) <= 2.5
    """).fetchone()[0]
    log(f"  (compared against {n_pk:,} near-pick'em games, |spread| <= 2.5)")
    log(f"    {'margin':>7}{'simulated':>12}{'actual':>10}{'diff':>9}")
    worst = 0.0
    for k in (3, 7, 6, 10, 4, 14, 1, 17):
        s = sim_c.get(k, 0) / n; r = real.get(k, 0)
        worst = max(worst, abs(s - r))
        log(f"    {k:>7}{s*100:>11.2f}%{r*100:>9.2f}%{(s-r)*100:>+8.2f}")
    log(f"\n    largest key-number error: {worst*100:.2f}pp")

    levels_ok = abs(mu_t - act[0]) < 2.0 and abs(sd_m - act[3]) < 1.0
    shape_ok = worst < 0.03
    # ---- TOTAL SHAPE ---------------------------------------------------
    # The margin key-number test above says nothing about totals. It should:
    # scores are built only from 3s and 7s here, with no missed extra point,
    # no two-point try and no 6-point touchdown, so the simulated total is
    # more lattice-bound than reality. Totals that are awkward to assemble
    # from 3s and 7s come out too thin, and that directly under-prices any
    # middle whose window is one of them.
    act = con.execute("""
        SELECT home_score + away_score AS t, count(*) FROM raw.games
        WHERE season >= 2006 AND game_type='REG' AND home_score IS NOT NULL
        GROUP BY 1
    """).fetchall()
    na = sum(c for _, c in act)
    emp = {t: c / na for t, c in act}
    sims = m.simulate(0.0, 0.0, n_sims=a.sims, hfa=0.0, seed=11)
    tt = [h + x for h, x in sims]
    cs = Counter(tt)
    ns = len(tt)
    log("")
    log("  TOTAL SHAPE - does the simulated total land where real ones do?")
    log(f"     {'total':>7}{'simulated':>12}{'actual':>10}{'diff':>9}")
    # Separate accumulator: `worst` above is a FRACTION (key-number error) and
    # this one is already in percentage points. Sharing the name overwrote the
    # margin result and printed it back multiplied by 100 - the verdict line
    # reported "102.35pp" for a 2.82pp error.
    worst_total, worst_t = 0.0, None
    for x in (37, 38, 39, 41, 42, 43, 44, 46, 47, 49, 51):
        s_ = cs.get(x, 0) / ns * 100
        a_ = emp.get(x, 0) * 100
        if abs(s_ - a_) > worst_total:
            worst_total, worst_t = abs(s_ - a_), x
        log(f"     {x:>7}{s_:>11.2f}%{a_:>9.2f}%{s_ - a_:>+9.2f}")
    log(f"    largest exact-total error: {worst_total:.2f}pp at {worst_t}")
    log("    NOTE totals that are hard to build from 3s and 7s run thin here.")

    log("")
    log("=" * 74)
    log("VERDICT")
    log("=" * 74)
    log(f"  SCORE LEVELS  {'PASS' if levels_ok else 'FAIL'}  "
        f"points, totals and residual margin spread")
    log(f"  MARGIN SHAPE  {'PASS' if shape_ok else 'FAIL'}  "
        f"key numbers, worst error {worst*100:.2f}pp")
    log("")
    if levels_ok and not shape_ok:
        log("  USE FOR: totals, team totals, projected score, win probability.")
        log("  DO NOT USE FOR: spread or alternate-spread pricing, until the")
        log("  key numbers come in. Same split the margin model landed on,")
        log("  reached independently.")
    elif levels_ok and shape_ok:
        log("  Recovers reality on neutral inputs. Cleared for use, and it is")
        log("  the only model here that is not anchored to the market.")
    return 0


def cmd_game(con, a) -> int:
    m = DriveModel.load(MODEL_PATH)
    # Prefer the preseason prior: last season's rating cannot see ten coaching
    # changes and nine new starting quarterbacks. Falls back to the raw rating
    # when no prior has been built.
    src = "2025 rating"
    try:
        rt = dict(con.execute(
            "SELECT team, blended_off FROM mart.preseason_prior").fetchall())
        rd = dict(con.execute(
            "SELECT team, blended_def FROM mart.preseason_prior").fetchall())
        if rt:
            src = "preseason prior (market + 2025)"
    except Exception:
        rt, rd = {}, {}
    if not rt:
        mx = con.execute("SELECT max(week) FROM mart.team_rating WHERE season=?",
                         [a.season - 1]).fetchone()[0]
        rt = dict(con.execute("""
            SELECT team, off_rating FROM mart.team_rating WHERE season=? AND week=?
        """, [a.season - 1, mx]).fetchall())
        rd = dict(con.execute("""
            SELECT team, def_rating FROM mart.team_rating WHERE season=? AND week=?
        """, [a.season - 1, mx]).fetchall())
    hs = rt.get(a.home, 0) + rd.get(a.away, 0)
    as_ = rt.get(a.away, 0) + rd.get(a.home, 0)

    # Neutral site is not cosmetic - it is the whole home-field term. Week 1
    # 2026 has SF vs LA at the Melbourne Cricket Ground.
    loc = con.execute("""
        SELECT location FROM raw.games
        WHERE season=? AND week=? AND home_team=? AND away_team=?
    """, [a.season, a.week, a.home, a.away]).fetchone()
    neutral = bool(loc) and str(loc[0]).lower().startswith("neutral")
    hfa = 0.0 if neutral else HFA_POINTS

    sims = m.simulate(hs, as_, n_sims=a.sims, hfa=hfa)
    margins = sorted(h - x for h, x in sims)
    totals = sorted(h + x for h, x in sims)
    n = len(sims)
    hw = sum(1 for h, x in sims if h > x) / n
    push = sum(1 for h, x in sims if h == x) / n
    log(f"{a.away} at {a.home}  ({a.sims:,} sims)  strength source: {src}")
    log(f"  home field      {hfa:.2f} pts" + ("  [NEUTRAL SITE]" if neutral else ""))
    log(f"  mean score      {sum(h for h,_ in sims)/n:.1f} - "
        f"{sum(x for _,x in sims)/n:.1f}")
    log(f"  home win        {hw*100:.1f}%   tie {push*100:.1f}%")
    log(f"  median margin   {margins[n//2]:+.0f}   median total {totals[n//2]:.0f}")
    log(f"  our spread      home {-(sum(margins)/n):+.1f}")
    log(f"  our total       {sum(totals)/n:.1f}")
    mk = Counter(margins)
    log("  key numbers     " + "  ".join(
        f"P({k})={mk.get(k,0)/n*100:.1f}%" for k in (3, 7, 6, 10)))
    return 0


def _ols(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sxy / sxx if sxx else 0.0
    return my - b * mx, b


def _corr(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return sxy / ((sxx * syy) ** 0.5) if sxx and syy else 0.0


def cmd_backtest(con, a) -> int:
    """Does the simulated spread beat the closing line? Almost certainly not.

    This is the test the simulator had never faced. `validate` proves it
    reproduces the AGGREGATE shape of football; that is a different and much
    weaker claim than getting a specific game right. Team strength is quantized
    and shrunk, the market sees injuries and news we do not, and we have
    already measured opponent-adjusted ratings at -0.04 partial correlation
    against the close.

    The number that decides it is the PARTIAL correlation of the simulated
    spread with the realised margin, holding the closing line fixed. Raw
    correlation will look impressive - about 0.9 - purely because both track
    the same underlying football. Only the partial tells you whether the sim
    knows anything the market does not.
    """
    m = DriveModel.load(MODEL_PATH)
    seasons = [int(s) for s in a.seasons.split(",")]
    rows = con.execute(f"""
        SELECT g.season, g.week, g.home_team, g.away_team, g.location,
               g.spread_line, g.home_score - g.away_score AS margin,
               ro.off_rating AS h_off, rdh.def_rating AS h_def,
               ao.off_rating AS a_off, rda.def_rating AS a_def
        FROM raw.games g
        JOIN mart.team_rating ro  ON ro.season=g.season AND ro.week=g.week
                                 AND ro.team=g.home_team
        JOIN mart.team_rating rdh ON rdh.season=g.season AND rdh.week=g.week
                                 AND rdh.team=g.home_team
        JOIN mart.team_rating ao  ON ao.season=g.season AND ao.week=g.week
                                 AND ao.team=g.away_team
        JOIN mart.team_rating rda ON rda.season=g.season AND rda.week=g.week
                                 AND rda.team=g.away_team
        WHERE g.season IN ({",".join(str(s) for s in seasons)})
          AND g.game_type='REG' AND g.home_score IS NOT NULL
          AND g.spread_line IS NOT NULL
        ORDER BY g.season, g.week
    """).fetchall()

    sim, mkt, act = [], [], []
    for (_, _, _, _, loc, sl, mg, ho, hd, ao_, ad) in rows:
        neutral = bool(loc) and str(loc).lower().startswith("neutral")
        hs = (ho or 0) + (ad or 0)
        as_ = (ao_ or 0) + (hd or 0)
        s = m.simulate(hs, as_, n_sims=a.sims,
                       hfa=0.0 if neutral else HFA_POINTS)
        sim.append(sum(h - x for h, x in s) / len(s))
        mkt.append(float(sl))
        act.append(float(mg))

    n = len(sim)
    log("=" * 74)
    log("BACKTEST - simulated spread vs the closing line")
    log("=" * 74)
    log(f"  {n} regular season games, seasons {a.seasons}, {a.sims:,} sims each")
    log(f"  point-in-time ratings; neutral sites get zero home field")
    log("")
    mae_s = sum(abs(s - x) for s, x in zip(sim, act)) / n
    mae_m = sum(abs(k - x) for k, x in zip(mkt, act)) / n
    log(f"  {'mean abs error vs realised margin':<42}{'sim':>8}{'market':>9}")
    log(f"  {'':<42}{mae_s:>8.2f}{mae_m:>9.2f}")
    log(f"  {'bias (mean signed error)':<42}"
        f"{sum(s-x for s,x in zip(sim,act))/n:>8.2f}"
        f"{sum(k-x for k,x in zip(mkt,act))/n:>9.2f}")
    log("")
    log(f"  corr(sim, market)                  {_corr(sim, mkt):>7.3f}")
    log(f"  corr(sim, realised margin)         {_corr(sim, act):>7.3f}   <- flattering")
    log(f"  corr(market, realised margin)      {_corr(mkt, act):>7.3f}")

    ia, ba = _ols(mkt, act)
    is_, bs = _ols(mkt, sim)
    ra = [x - (ia + ba * k) for x, k in zip(act, mkt)]
    rs = [s - (is_ + bs * k) for s, k in zip(sim, mkt)]
    pc = _corr(rs, ra)
    log(f"  PARTIAL corr(sim, margin | market) {pc:>7.3f}   <- the only one that counts")
    log("")

    # If you actually bet the disagreement, what happens?
    log(f"  {'disagreement':<16}{'n':>6}{'ATS W-L-P':>14}{'win%':>8}")
    for thr in (1.0, 2.0, 3.0):
        w = l = p = 0
        for s, k, x in zip(sim, mkt, act):
            if abs(s - k) < thr:
                continue
            # sim likes home when it projects a bigger home margin than the line
            cover = x - k
            if abs(cover) < 1e-9:
                p += 1
            elif (cover > 0) == (s > k):
                w += 1
            else:
                l += 1
        tot = w + l
        log(f"  {'>= ' + str(thr) + ' pts':<16}{w+l+p:>6}{f'{w}-{l}-{p}':>14}"
            f"{(w/tot*100 if tot else 0):>7.1f}%")
    log(f"  {'breakeven at -110 is 52.4%':<16}")
    log("")
    log("=" * 74)
    log("VERDICT")
    log("=" * 74)
    if pc > 0.08:
        log(f"  Partial correlation {pc:+.3f}. The simulator carries incremental")
        log("  signal over the closing line. Worth pricing sides from.")
    elif pc > 0.03:
        log(f"  Partial correlation {pc:+.3f}. Marginal - too thin to bet sides")
        log("  on, but not zero. Re-test with more seasons before trusting it.")
    else:
        log(f"  Partial correlation {pc:+.3f}. NO incremental signal over the")
        log("  closing line, exactly as the -0.04 rating result predicted.")
        log("  USE THE SIM FOR SHAPE, NOT FOR THE CENTER: take the mean from")
        log("  the market and the distribution from here. Pricing a side off")
        log("  the sim's disagreement would be manufacturing an edge.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fit", "validate", "game", "backtest"])
    ap.add_argument("--seasons", default="2023,2024,2025")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--sims", type=int, default=20000)
    ap.add_argument("--home", default="KC")
    ap.add_argument("--away", default="BAL")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, default=1)
    a = ap.parse_args()
    con = duckdb.connect(str(a.db), read_only=(a.cmd != "fit"))
    try:
        return {"fit": cmd_fit, "validate": cmd_validate,
                "game": cmd_game, "backtest": cmd_backtest}[a.cmd](con, a)
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
