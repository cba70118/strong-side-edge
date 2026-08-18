"""
build_props.py - build and validate the prop projection layer.

Applies sql/12_prop_projection.sql, then validates the projections against
NAIVE BASELINES. A projection that cannot beat "use last week's number" is
not a model, it is decoration - so the comparison is the deliverable, not the
projection itself.

Baselines compared, all point-in-time:
    last        the player's most recent game
    prior6      unshrunk mean of the previous 6 games
    baseline    the positional average, ignoring the player entirely
    model       shrunk usage x projected volume x shrunk baseline efficiency

Usage:
    py -3 scripts/build_props.py build
    py -3 scripts/build_props.py validate
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"
SQL = ROOT / "sql" / "12_prop_projection.sql"

MIN_PRIOR = 3
MIN_SNAP = 0.25


def log(m: str = "") -> None:
    print(m, flush=True)


def build(con) -> int:
    log("applying sql/12_prop_projection.sql")
    con.execute(SQL.read_text(encoding="utf-8"))
    for t in ("mart.usage_baseline", "mart.team_volume_projection",
              "mart.player_usage_projection"):
        n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        log(f"  {t:<34}{n:>9,} rows")

    log("\npositional baselines:")
    log(f"  {'pos':<5}{'games':>8}{'snap%':>8}{'route%':>8}{'tgt sh':>8}"
        f"{'ay sh':>8}{'carry sh':>10}{'YPT':>7}{'catch':>7}{'YPC':>6}")
    for r in con.execute("""
        SELECT position, n_games, base_snap_pct, base_route_pct,
               base_target_share, base_ay_share, base_carry_share,
               base_ypt, base_catch_rate, base_ypc
        FROM mart.usage_baseline ORDER BY position
    """).fetchall():
        log(f"  {r[0]:<5}{r[1]:>8,}{r[2]:>8.3f}{r[3]:>8.3f}{r[4]:>8.3f}"
            f"{r[5]:>8.3f}{r[6]:>10.3f}{r[7]:>7.2f}{r[8]:>7.3f}{r[9]:>6.2f}")
    return 0


def validate(con) -> int:
    # Common evaluation sample, shared by every method.
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE ev AS
        WITH b AS (
            SELECT p.*,
                   lag(p.target_share) OVER wp AS last_tgt_share,
                   lag(p.rec_yards)    OVER wp AS last_rec_yards,
                   lag(p.carry_share)  OVER wp AS last_carry_share,
                   lag(p.rush_yards)   OVER wp AS last_rush_yards,
                   lag(p.targets)      OVER wp AS last_targets,
                   avg(p.target_share) OVER w6 AS raw6_tgt_share,
                   avg(p.rec_yards)    OVER w6 AS raw6_rec_yards,
                   avg(p.targets)      OVER w6 AS raw6_targets,
                   avg(p.rush_yards)   OVER w6 AS raw6_rush_yards
            FROM mart.player_usage_projection p
            WINDOW
              wp AS (PARTITION BY p.gsis_id ORDER BY p.season, p.week),
              w6 AS (PARTITION BY p.gsis_id ORDER BY p.season, p.week
                     ROWS BETWEEN 6 PRECEDING AND 1 PRECEDING)
        )
        SELECT b.*, u.base_target_share, u.base_ypt
        FROM b JOIN mart.usage_baseline u ON u.position = b.position
        WHERE b.n_prior >= {MIN_PRIOR}
          AND b.offense_pct >= {MIN_SNAP}
          AND b.proj_rec_yards IS NOT NULL
          AND b.last_rec_yards IS NOT NULL
          AND b.raw6_rec_yards IS NOT NULL
    """)
    n = con.execute("SELECT count(*) FROM ev").fetchone()[0]
    log("=" * 78)
    log("OUT-OF-SAMPLE VALIDATION vs NAIVE BASELINES")
    log("=" * 78)
    log(f"common sample: {n:,} player-games "
        f"(n_prior >= {MIN_PRIOR}, snap% >= {MIN_SNAP})\n")
    if n < 500:
        log("sample too small")
        return 1

    def compare(title, actual, methods, unit=""):
        log(f"  {title}")
        log(f"    {'method':<14}{'MAE':>9}{'RMSE':>9}{'corr':>8}{'bias':>9}")
        best = None
        for name, expr in methods:
            r = con.execute(f"""
                SELECT avg(abs({actual} - ({expr}))),
                       sqrt(avg(pow({actual} - ({expr}), 2))),
                       corr({actual}, {expr}),
                       avg(({expr}) - {actual})
                FROM ev WHERE {expr} IS NOT NULL
            """).fetchone()
            log(f"    {name:<14}{r[0]:>9.3f}{r[1]:>9.3f}"
                f"{(r[2] if r[2] is not None else 0):>8.3f}{r[3]:>+9.3f}")
            if best is None or r[0] < best[1]:
                best = (name, r[0])
        log(f"    -> best MAE: {best[0]}{unit}\n")
        return best[0]

    winners = []
    winners.append(compare(
        "TARGET SHARE", "target_share",
        [("last", "last_tgt_share"), ("prior6", "raw6_tgt_share"),
         ("baseline", "base_target_share"), ("model", "proj_target_share")]))

    winners.append(compare(
        "TARGETS", "targets",
        [("last", "last_targets"), ("prior6", "raw6_targets"),
         ("model", "proj_targets")]))

    winners.append(compare(
        "RECEIVING YARDS", "rec_yards",
        [("last", "last_rec_yards"), ("prior6", "raw6_rec_yards"),
         ("baseline", "base_ypt * proj_targets"),
         ("model", "proj_rec_yards")]))

    winners.append(compare(
        "RUSH YARDS", "rush_yards",
        [("last", "last_rush_yards"), ("prior6", "raw6_rush_yards"),
         ("model", "proj_rush_yards")]))

    log("=" * 78)
    log("DOES PLAYER-SPECIFIC EFFICIENCY HELP AT ALL?")
    log("=" * 78)
    log("Compare projected targets x SHRUNK player YPT against projected")
    log("targets x FLAT positional YPT. Usage stability said player YPT")
    log("carries correlation 0.136; this asks whether keeping any of it pays.\n")
    r = con.execute("""
        SELECT avg(abs(rec_yards - proj_rec_yards)),
               avg(abs(rec_yards - base_ypt * proj_targets)),
               corr(rec_yards, proj_rec_yards),
               corr(rec_yards, base_ypt * proj_targets)
        FROM ev WHERE proj_targets IS NOT NULL
    """).fetchone()
    log(f"  shrunk player YPT : MAE {r[0]:.3f}  corr {r[2]:.3f}")
    log(f"  flat position YPT : MAE {r[1]:.3f}  corr {r[3]:.3f}")
    gain = r[1] - r[0]
    log(f"  gain from keeping player efficiency: {gain:+.3f} yards MAE")
    if abs(gain) < 0.15:
        log("  -> negligible. Efficiency is essentially a positional constant;")
        log("     the model's value is entirely in the USAGE projection.")
    elif gain > 0:
        log("  -> shrunk player efficiency helps; keep it.")
    else:
        log("  -> player efficiency HURTS. Shrink harder or drop it.")

    log("\n" + "=" * 78)
    log("VERDICT")
    log("=" * 78)
    wins = sum(1 for w in winners if w == "model")
    log(f"  model wins {wins}/{len(winners)} on MAE")
    if wins < len(winners):
        log("  Where a naive baseline wins, the model is not adding value on")
        log("  that stat and should not be trusted to price it.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "validate"])
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    a = ap.parse_args()
    if not a.db.exists():
        log(f"database not found: {a.db}")
        return 1
    con = duckdb.connect(str(a.db))
    try:
        return build(con) if a.cmd == "build" else validate(con)
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
