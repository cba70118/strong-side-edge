"""
props.py — usage-tree projection and distributional pricing for NFL player props.

The whole point: never price a prop against a MEAN. Yardage markets are strongly
right-skewed, so a projection whose mean clears the line can still be an under.

    python3 props.py     # runs the worked example + self-test

Requires: scipy, numpy.
"""

from __future__ import annotations
from dataclasses import dataclass
import math

import numpy as np
from scipy import stats

# --------------------------------------------------------- 1. game environment


def implied_team_totals(total: float, home_spread: float) -> dict:
    """home_spread negative = home favored."""
    return {"home": total / 2 - home_spread / 2, "away": total / 2 + home_spread / 2}


def script_adjusted_pass_rate(base_pass_rate: float, spread: float,
                              sensitivity: float = 0.009) -> float:
    """Teams projected to trail pass more; teams projected to lead run more.

    `spread` from the team's perspective (negative = favorite). The default
    sensitivity is a starting prior — refit it on nflverse play-by-play by
    regressing actual pass rate on pregame spread, then replace it.
    """
    return float(np.clip(base_pass_rate + sensitivity * spread, 0.25, 0.80))


@dataclass
class GameEnvironment:
    total: float
    spread: float                  # team's perspective, negative = favorite
    seconds_per_play: float = 27.5
    base_pass_rate: float = 0.58   # PROE-informed, not league average

    @property
    def team_total(self) -> float:
        return self.total / 2 - self.spread / 2

    @property
    def plays(self) -> float:
        # ~60 min of game clock, roughly half the possessions each
        return (60 * 60 / 2) / self.seconds_per_play

    @property
    def pass_rate(self) -> float:
        return script_adjusted_pass_rate(self.base_pass_rate, self.spread)

    @property
    def dropbacks(self) -> float:
        return self.plays * self.pass_rate

    @property
    def rush_attempts(self) -> float:
        return self.plays * (1 - self.pass_rate)


# ------------------------------------------------------------ 2. distributions


def gamma_from_mean_cv(mean: float, cv: float):
    """Right-skewed yardage. cv = sd/mean. Typical: WR yards 0.55-0.70, RB 0.45-0.60."""
    shape = 1.0 / (cv ** 2)
    scale = mean / shape
    return stats.gamma(a=shape, scale=scale)


def negbin_from_mean_var(mean: float, var: float):
    """Receptions. Overdispersed vs Poisson (var > mean), so Poisson understates tails."""
    if var <= mean:
        var = mean * 1.25          # enforce overdispersion
    p = mean / var
    n = mean * p / (1 - p)
    return stats.nbinom(n=n, p=p)


def prob_over(dist, line: float) -> float:
    """P(result > line). Half-point lines have no push; integer lines do."""
    if float(line).is_integer():
        # continuous dists: P(>line) directly; discrete: exclude the push
        return float(dist.sf(line))
    return float(dist.sf(math.floor(line)) if hasattr(dist, "pmf") else dist.sf(line))


def anytime_td_prob(team_total: float, player_td_share: float,
                    fg_share: float = 0.22) -> float:
    """Poisson. team TDs approximated from implied total net of field-goal scoring."""
    team_tds = (team_total * (1 - fg_share)) / 7.0
    lam = team_tds * player_td_share
    return 1.0 - math.exp(-lam)


# ----------------------------------------------------------- 3. the usage tree


@dataclass
class ReceiverProjection:
    env: GameEnvironment
    snap_share: float
    target_share: float
    catch_rate: float
    yards_per_reception: float
    yards_cv: float = 0.60

    @property
    def targets(self) -> float:
        return self.env.dropbacks * self.target_share * (self.snap_share / 0.90)

    @property
    def receptions(self) -> float:
        return self.targets * self.catch_rate

    @property
    def mean_yards(self) -> float:
        return self.receptions * self.yards_per_reception

    def yards_dist(self):
        return gamma_from_mean_cv(self.mean_yards, self.yards_cv)

    def receptions_dist(self):
        # target variance ~ binomial on targets, plus target-count variance
        var = self.receptions * 1.45
        return negbin_from_mean_var(self.receptions, var)

    def report(self, yards_line: float, receptions_line: float | None = None) -> dict:
        yd = self.yards_dist()
        out = {
            "targets": round(self.targets, 2),
            "receptions": round(self.receptions, 2),
            "mean_yards": round(self.mean_yards, 1),
            "median_yards": round(float(yd.median()), 1),
            "yards_line": yards_line,
            "p_over_yards": round(prob_over(yd, yards_line), 4),
            "fair_odds_over": None,
        }
        p = out["p_over_yards"]
        out["fair_odds_over"] = round(-100 * p / (1 - p) if p >= 0.5
                                      else 100 * (1 - p) / p, 0)
        out["side"] = "OVER" if p > 0.5 else "UNDER"
        out["mean_beats_line"] = self.mean_yards > yards_line
        if receptions_line is not None:
            rd = self.receptions_dist()
            out["p_over_receptions"] = round(prob_over(rd, receptions_line), 4)
            out["receptions_line"] = receptions_line
        return out


@dataclass
class RusherProjection:
    env: GameEnvironment
    carry_share: float
    yards_per_carry: float
    yards_cv: float = 0.52

    @property
    def carries(self) -> float:
        return self.env.rush_attempts * self.carry_share

    @property
    def mean_yards(self) -> float:
        return self.carries * self.yards_per_carry

    def report(self, yards_line: float) -> dict:
        d = gamma_from_mean_cv(self.mean_yards, self.yards_cv)
        p = prob_over(d, yards_line)
        return {
            "carries": round(self.carries, 2),
            "mean_yards": round(self.mean_yards, 1),
            "median_yards": round(float(d.median()), 1),
            "yards_line": yards_line,
            "p_over": round(p, 4),
            "side": "OVER" if p > 0.5 else "UNDER",
            "mean_beats_line": self.mean_yards > yards_line,
        }


def props_vs_team_total(projected_player_yards: dict, team_total: float,
                        yards_per_point: float = 14.5) -> dict:
    """Consistency check: do the props collectively imply more offense than the game line?

    If they do, the props are too high in aggregate and the game line is the anchor.
    `yards_per_point` is a rough prior — recompute it from nflverse for your era.
    """
    total_yards = sum(projected_player_yards.values())
    implied_points = total_yards / yards_per_point
    return {
        "summed_player_yards": round(total_yards, 1),
        "implied_points_from_props": round(implied_points, 2),
        "market_team_total": team_total,
        "gap": round(implied_points - team_total, 2),
        "read": ("props collectively too HIGH vs the game line"
                 if implied_points - team_total > 1.5 else
                 "props collectively too LOW vs the game line"
                 if implied_points - team_total < -1.5 else "broadly consistent"),
    }


# --------------------------------------------------------------------- demo

if __name__ == "__main__":
    env = GameEnvironment(total=46.0, spread=-3.0, seconds_per_play=27.0,
                          base_pass_rate=0.60)
    print("=== game environment ===")
    print(f"team total     {env.team_total:.1f}")
    print(f"plays          {env.plays:.1f}")
    print(f"pass rate      {env.pass_rate:.3f}  (script-adjusted from 0.600)")
    print(f"dropbacks      {env.dropbacks:.1f}")
    print(f"rush attempts  {env.rush_attempts:.1f}")

    print("\n=== WR1: the skew trap ===")
    wr1 = ReceiverProjection(env, snap_share=0.88, target_share=0.24,
                             catch_rate=0.66, yards_per_reception=11.5, yards_cv=0.58)
    r = wr1.report(yards_line=64.5, receptions_line=4.5)
    for k, v in r.items():
        print(f"  {k:20s} {v}")
    if r["mean_beats_line"] and r["side"] == "UNDER":
        print("  -> MEAN clears the line by "
              f"{r['mean_yards'] - r['yards_line']:.1f} yards, but the DISTRIBUTION "
              "says under. This is the trap.")

    print("\n=== RB1 ===")
    rb = RusherProjection(env, carry_share=0.62, yards_per_carry=4.3)
    for k, v in rb.report(yards_line=68.5).items():
        print(f"  {k:20s} {v}")

    print("\n=== anytime TD ===")
    for share in (0.28, 0.12, 0.05):
        p = anytime_td_prob(env.team_total, share)
        fair = -100 * p / (1 - p) if p >= 0.5 else 100 * (1 - p) / p
        print(f"  td_share {share:.2f} -> P={p:.3f}  fair {fair:+.0f}")

    print("\n=== props vs team total consistency ===")
    print(props_vs_team_total(
        {"WR1": wr1.mean_yards, "WR2": 48, "TE": 36, "RB_rush": rb.mean_yards,
         "RB_rec": 22, "WR3": 24}, env.team_total))

    print("\n=== self-test ===")
    g = gamma_from_mean_cv(100, 0.6)
    assert abs(g.mean() - 100) < 1e-6
    assert abs(g.std() / g.mean() - 0.6) < 1e-6
    assert g.median() < g.mean(), "gamma must be right-skewed"
    nb = negbin_from_mean_var(5.0, 7.25)
    assert abs(nb.mean() - 5.0) < 1e-6
    assert nb.var() > nb.mean()
    assert 0 < anytime_td_prob(24.5, 0.25) < 1
    assert implied_team_totals(46.5, -3.5)["home"] == 25.0
    assert script_adjusted_pass_rate(0.58, -7) < 0.58 < script_adjusted_pass_rate(0.58, 7)
    print("all passed")
