"""
key_numbers.py — compute NFL key numbers, home field advantage, spread->win-probability,
and market calibration from ACTUAL results, not from memorized constants.

Data: nflverse `games.csv` (free, no key) which carries every game's spread, total,
and result back to 1999.

    python3 key_numbers.py                 # last 8 seasons
    python3 key_numbers.py --since 2015
    python3 key_numbers.py --since 2020 --json

Why this exists: key-number frequencies, home field advantage, and margin standard
deviation all drift with rule changes and scoring environment. Quoting a number from an
old article biases every game on your board in the same direction. Recompute, then cite
what you computed and the seasons it covers.
"""

from __future__ import annotations
import argparse
import json
import sys

import numpy as np
import pandas as pd

GAMES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"


def load_games(since: int) -> pd.DataFrame:
    df = pd.read_csv(GAMES_URL, low_memory=False)
    df = df[(df["season"] >= since) & df["result"].notna()].copy()
    # nflverse convention: result = home_score - away_score
    #                      spread_line = points the home team is favored by
    df["margin"] = df["result"].astype(float)
    df["abs_margin"] = df["margin"].abs()
    return df


def key_numbers(df: pd.DataFrame, top: int = 12) -> pd.DataFrame:
    counts = df["abs_margin"].value_counts(normalize=True).sort_values(ascending=False)
    out = counts.head(top).rename("frequency").reset_index()
    out.columns = ["margin", "frequency"]
    out["pct"] = (out["frequency"] * 100).round(2)
    out["games"] = (out["frequency"] * len(df)).round(0).astype(int)
    return out[["margin", "games", "pct"]]


def home_field_advantage(df: pd.DataFrame) -> dict:
    by_season = df.groupby("season")["margin"].mean().round(2)
    return {
        "overall_mean_home_margin": round(float(df["margin"].mean()), 3),
        "by_season": {int(k): float(v) for k, v in by_season.items()},
        "market_mean_home_spread": (round(float(df["spread_line"].mean()), 3)
                                    if "spread_line" in df else None),
    }


def margin_sd(df: pd.DataFrame) -> dict:
    """SD of margin around the market's expectation — the number that belongs in a
    spread->win-probability conversion. NOT the raw SD of margin."""
    out = {"raw_margin_sd": round(float(df["margin"].std()), 3)}
    if "spread_line" in df:
        resid = df["margin"] - df["spread_line"]
        out["residual_sd_vs_spread"] = round(float(resid.std()), 3)
        out["mean_residual"] = round(float(resid.mean()), 3)
    return out


def spread_to_winprob_table(df: pd.DataFrame, min_n: int = 25) -> pd.DataFrame:
    """Empirical win rate by spread. Beats any closed-form approximation."""
    if "spread_line" not in df:
        return pd.DataFrame()
    d = df.dropna(subset=["spread_line"]).copy()
    d["home_win"] = (d["margin"] > 0).astype(float)
    d["push"] = (d["margin"] == 0).astype(float)
    g = d.groupby("spread_line").agg(
        n=("home_win", "size"), win_pct=("home_win", "mean"), tie_pct=("push", "mean")
    )
    g = g[g["n"] >= min_n]
    g["win_pct"] = (g["win_pct"] * 100).round(1)
    g["tie_pct"] = (g["tie_pct"] * 100).round(1)
    return g.reset_index()


def market_calibration(df: pd.DataFrame) -> dict:
    """Did favorites/overs actually cover? This is your baseline for 'is the market
    beatable at all in this segment' — and a check that your data is oriented right."""
    out = {}
    if "spread_line" in df:
        d = df.dropna(subset=["spread_line"])
        cover = (d["margin"] > d["spread_line"]).mean()
        push = (d["margin"] == d["spread_line"]).mean()
        out["home_cover_pct"] = round(float(cover) * 100, 2)
        out["push_pct"] = round(float(push) * 100, 2)
        dogs = d[d["spread_line"] < 0]
        out["road_favorite_cover_pct"] = (
            round(float((dogs["margin"] > dogs["spread_line"]).mean()) * 100, 2)
            if len(dogs) else None)
    if "total_line" in df and {"home_score", "away_score"}.issubset(df.columns):
        d = df.dropna(subset=["total_line"])
        pts = d["home_score"] + d["away_score"]
        out["over_pct"] = round(float((pts > d["total_line"]).mean()) * 100, 2)
        out["total_push_pct"] = round(float((pts == d["total_line"]).mean()) * 100, 2)
        out["mean_actual_total"] = round(float(pts.mean()), 2)
        out["mean_market_total"] = round(float(d["total_line"].mean()), 2)
    return out


def half_point_value(df: pd.DataFrame, spread: float) -> dict:
    """How much of the distribution sits exactly on this number — i.e. what a half point
    across it actually buys."""
    freq = float((df["abs_margin"] == abs(spread)).mean())
    return {
        "number": abs(spread),
        "pct_of_games_landing_exactly_here": round(freq * 100, 2),
        "note": "a half point across this number converts that share of games "
                "from push/loss to win (roughly — direction depends on side)",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=int, default=2018)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    df = load_games(a.since)
    result = {
        "seasons": f"{a.since}-{int(df['season'].max())}",
        "games": int(len(df)),
        "key_numbers": key_numbers(df).to_dict("records"),
        "home_field_advantage": home_field_advantage(df),
        "margin_sd": margin_sd(df),
        "market_calibration": market_calibration(df),
        "half_point_values": [half_point_value(df, n) for n in (3, 4, 6, 7, 10, 14)],
    }

    if a.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"\nNFL {result['seasons']}  ·  {result['games']} games\n")
    print("KEY NUMBERS (margin of victory)")
    print(pd.DataFrame(result["key_numbers"]).to_string(index=False))
    print("\nHOME FIELD ADVANTAGE")
    hfa = result["home_field_advantage"]
    print(f"  mean home margin (actual): {hfa['overall_mean_home_margin']:+.2f}")
    print(f"  mean home spread (market): {hfa['market_mean_home_spread']:+.2f}")
    print("  by season:", hfa["by_season"])
    print("\nMARGIN DISPERSION")
    for k, v in result["margin_sd"].items():
        print(f"  {k:26s} {v}")
    print("  -> use residual_sd_vs_spread in any normal-approximation win-prob model")
    print("\nMARKET CALIBRATION")
    for k, v in result["market_calibration"].items():
        print(f"  {k:26s} {v}")
    print("\nSPREAD -> HOME WIN % (empirical)")
    tbl = spread_to_winprob_table(df)
    if not tbl.empty:
        print(tbl.to_string(index=False))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
