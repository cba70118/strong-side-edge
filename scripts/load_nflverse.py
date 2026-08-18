"""
load_nflverse.py - materialize nflverse releases into the raw schema.

Build order step 2. Ingestion is DuckDB reading parquet directly over HTTPS;
there is no dataframe library in this path. Season-partitioned assets are
probed for availability, then read in a single read_parquet(list) call with
union_by_name=true so cross-season schema drift unions instead of failing.

Every load writes a raw._ingest_log row (ok | failed | skipped_unchanged), and
the column contract is asserted after the batch. A load that succeeds but
violates the contract is a FAILED run - shape drift is not a warning.

Dependencies: duckdb + requests + stdlib.

Usage (see RUNBOOK.md):
    py -3 scripts/load_nflverse.py --seasons 2020-2025
    py -3 scripts/load_nflverse.py --seasons 2024-2025 --only pbp,injuries
    py -3 scripts/load_nflverse.py --seasons 2020-2025 --force
    py -3 scripts/load_nflverse.py --list
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import requests

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"
NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download/{tag}/{file}"

CURRENT_SEASON = 2026


@dataclass(frozen=True)
class Asset:
    key: str            # --only selector
    tag: str            # nflverse release tag
    file: str           # filename, may contain {season}
    table: str          # target table, e.g. 'raw.pbp'
    partitioned: bool    # True = one file per season
    first_season: int | None = None   # earliest season published
    note: str = ""


# Verified against live nflverse 2026-08-16. first_season prevents pointless
# 404 probes for assets that simply do not go back that far.
ASSETS: list[Asset] = [
    Asset("games",         "schedules",        "games.parquet",
          "raw.games",              False, note="all seasons in one file; carries closing lines 1999+"),
    Asset("players",       "players",          "players.parquet",
          "raw.players",            False, note="canonical gsis_id"),
    Asset("teams",         "teams",            "teams_colors_logos.parquet",
          "raw.teams",              False),
    Asset("ngs_pass",      "nextgen_stats",    "ngs_passing.parquet",
          "raw.ngs_passing",        False, note="all seasons per file"),
    Asset("ngs_rec",       "nextgen_stats",    "ngs_receiving.parquet",
          "raw.ngs_receiving",      False),
    Asset("ngs_rush",      "nextgen_stats",    "ngs_rushing.parquet",
          "raw.ngs_rushing",        False),
    Asset("qbr",           "espn_data",        "qbr_week_level.parquet",
          "raw.qbr_week",           False),

    Asset("pbp",           "pbp",              "play_by_play_{season}.parquet",
          "raw.pbp",                True, 1999, "372 cols, ~49k rows/season"),
    Asset("stats_player",  "stats_player",     "stats_player_week_{season}.parquet",
          "raw.stats_player_week",  True, 1999),
    Asset("stats_team",    "stats_team",       "stats_team_week_{season}.parquet",
          "raw.stats_team_week",    True, 1999),
    Asset("rosters",       "weekly_rosters",   "roster_weekly_{season}.parquet",
          "raw.roster_weekly",      True, 2002),
    Asset("snap_counts",   "snap_counts",      "snap_counts_{season}.parquet",
          "raw.snap_counts",        True, 2012),
    Asset("injuries",      "injuries",         "injuries_{season}.parquet",
          "raw.injuries",           True, 2009, "practice participation DNP/LP/FP"),
    Asset("depth_charts",  "depth_charts",     "depth_charts_{season}.parquet",
          "raw.depth_charts",       True, 2001),
    Asset("participation", "pbp_participation", "pbp_participation_{season}.parquet",
          "raw.pbp_participation",  True, 2016, "personnel + defenders in box; 2016-2025"),
    Asset("ftn",           "ftn_charting",     "ftn_charting_{season}.parquet",
          "raw.ftn_charting",       True, 2022, "current season lags a year"),
    Asset("pfr_pass",      "pfr_advstats",     "advstats_week_pass_{season}.parquet",
          "raw.pfr_pass",           True, 2018),
    Asset("pfr_rush",      "pfr_advstats",     "advstats_week_rush_{season}.parquet",
          "raw.pfr_rush",           True, 2018),
    Asset("pfr_rec",       "pfr_advstats",     "advstats_week_rec_{season}.parquet",
          "raw.pfr_rec",            True, 2018),
    Asset("pfr_def",       "pfr_advstats",     "advstats_week_def_{season}.parquet",
          "raw.pfr_def",            True, 2018),
]


def log(msg: str = "") -> None:
    print(msg, flush=True)


def url_for(a: Asset, season: int | None = None) -> str:
    fname = a.file.format(season=season) if season is not None else a.file
    return NFLVERSE.format(tag=a.tag, file=fname)


def probe(url: str) -> tuple[str, int] | None:
    """HEAD a URL; return (url, byte_size) if it exists."""
    try:
        r = requests.head(url, allow_redirects=True, timeout=30)
        if r.status_code == 200:
            size = r.headers.get("Content-Length", "0")
            return (url, int(size) if size.isdigit() else 0)
    except requests.RequestException:
        pass
    return None


def resolve_urls(a: Asset, seasons: list[int]) -> list[tuple[str, int]]:
    """Which files for this asset actually exist right now."""
    if not a.partitioned:
        got = probe(url_for(a))
        return [got] if got else []

    want = [s for s in seasons if a.first_season is None or s >= a.first_season]
    if not want:
        return []
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda s: probe(url_for(a, s)), want))
    return [r for r in results if r]


def last_signature(con: duckdb.DuckDBPyConnection, table: str) -> tuple[int, int] | None:
    """(byte_size, row_count) of the last successful load, if any."""
    row = con.execute("""
        SELECT byte_size, row_count
        FROM raw._ingest_log
        WHERE target_table = ? AND status = 'ok'
        ORDER BY load_started_at DESC LIMIT 1
    """, [table]).fetchone()
    return (row[0], row[1]) if row and row[0] is not None else None


def load_asset(con: duckdb.DuckDBPyConnection, a: Asset,
               seasons: list[int], force: bool) -> str:
    started = datetime.now(timezone.utc)
    t0 = time.time()

    found = resolve_urls(a, seasons)
    if not found:
        log(f"  {a.key:<14} no files available for requested seasons - skipped")
        con.execute("""
            INSERT INTO raw._ingest_log
                (asset_tag, asset_file, target_table, source_url, season,
                 row_count, byte_size, load_started_at, load_ms, status, error_text)
            VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?, ?, 'skipped_unchanged',
                    'no matching files published')
        """, [a.tag, a.file, a.table, url_for(a), started,
              int((time.time() - t0) * 1000)])
        return "skipped"

    urls = [u for u, _ in found]
    total_bytes = sum(sz for _, sz in found)

    prev = last_signature(con, a.table)
    table_exists = con.execute("""
        SELECT count(*) FROM duckdb_tables()
        WHERE schema_name || '.' || table_name = ?
    """, [a.table]).fetchone()[0] > 0

    if prev and prev[0] == total_bytes and table_exists and not force:
        log(f"  {a.key:<14} unchanged ({total_bytes/1e6:6.1f} MB, "
            f"{prev[1]:,} rows) - skipped")
        con.execute("""
            INSERT INTO raw._ingest_log
                (asset_tag, asset_file, target_table, source_url, season,
                 row_count, byte_size, load_started_at, load_ms, status, error_text)
            VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, 'skipped_unchanged', NULL)
        """, [a.tag, a.file, a.table, urls[0], prev[1], total_bytes,
              started, int((time.time() - t0) * 1000)])
        return "skipped"

    url_list = ", ".join(f"'{u}'" for u in urls)
    schema, tname = a.table.split(".", 1)
    try:
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        # union_by_name handles cross-season column drift instead of erroring.
        con.execute(f"""
            CREATE OR REPLACE TABLE {a.table} AS
            SELECT * FROM read_parquet([{url_list}], union_by_name=true)
        """)
        n = con.execute(f"SELECT count(*) FROM {a.table}").fetchone()[0]
        ncols = len(con.execute(f"SELECT * FROM {a.table} LIMIT 0").description)
        ms = int((time.time() - t0) * 1000)
        con.execute("""
            INSERT INTO raw._ingest_log
                (asset_tag, asset_file, target_table, source_url, season,
                 row_count, byte_size, load_started_at, load_ms, status, error_text)
            VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, 'ok', NULL)
        """, [a.tag, a.file, a.table, urls[0], n, total_bytes, started, ms])
        log(f"  {a.key:<14} {n:>9,} rows  {ncols:>3} cols  "
            f"{len(urls):>2} file(s)  {total_bytes/1e6:6.1f} MB  {ms/1000:5.1f}s")
        return "ok"
    except Exception as exc:  # noqa: BLE001
        ms = int((time.time() - t0) * 1000)
        con.execute("""
            INSERT INTO raw._ingest_log
                (asset_tag, asset_file, target_table, source_url, season,
                 row_count, byte_size, load_started_at, load_ms, status, error_text)
            VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?, 'failed', ?)
        """, [a.tag, a.file, a.table, urls[0], total_bytes, started, ms,
              f"{type(exc).__name__}: {exc}"[:500]])
        log(f"  {a.key:<14} FAILED  {type(exc).__name__}: {str(exc)[:120]}")
        return "failed"


def parse_seasons(spec: str) -> list[int]:
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.update(range(int(lo), int(hi) + 1))
        elif part:
            out.add(int(part))
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Load nflverse releases into DuckDB.")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--seasons", default="2020-2025",
                    help="e.g. '2020-2025' or '2023,2024,2025'")
    ap.add_argument("--only", default=None,
                    help="comma list of asset keys (see --list)")
    ap.add_argument("--force", action="store_true",
                    help="reload even if byte size is unchanged")
    ap.add_argument("--list", action="store_true", help="list asset keys and exit")
    args = ap.parse_args()

    if args.list:
        log(f"{'key':<15}{'table':<26}{'tag':<20}part  first  note")
        for a in ASSETS:
            log(f"{a.key:<15}{a.table:<26}{a.tag:<20}"
                f"{'Y' if a.partitioned else 'n':<6}{a.first_season or '-':<7}{a.note}")
        return 0

    if not args.db.exists():
        log(f"database not found: {args.db}")
        log("run:  py -3 scripts/init_db.py --reset --validate")
        return 1

    seasons = parse_seasons(args.seasons)
    selected = ASSETS
    if args.only:
        keys = {k.strip() for k in args.only.split(",")}
        unknown = keys - {a.key for a in ASSETS}
        if unknown:
            log(f"unknown asset key(s): {', '.join(sorted(unknown))}")
            return 1
        selected = [a for a in ASSETS if a.key in keys]

    con = duckdb.connect(str(args.db))
    con.execute("INSTALL httpfs; LOAD httpfs;")

    log(f"database: {args.db}")
    log(f"seasons:  {seasons[0]}-{seasons[-1]} ({len(seasons)})")
    log(f"assets:   {len(selected)}")
    log(f"duckdb:   {duckdb.__version__}   python: {sys.version.split()[0]}\n")

    t0 = time.time()
    tally = {"ok": 0, "skipped": 0, "failed": 0}
    for a in selected:
        tally[load_asset(con, a, seasons, args.force)] += 1

    log(f"\nloaded {tally['ok']} | skipped {tally['skipped']} | "
        f"failed {tally['failed']}  in {time.time() - t0:.1f}s")

    # Contract assertion. A load that succeeds with the wrong shape is a failure.
    viol = con.execute("""
        SELECT target_table, missing_column, note
        FROM audit.column_contract_violations ORDER BY 1, 2
    """).fetchall()
    if viol:
        log(f"\nCOLUMN CONTRACT VIOLATIONS ({len(viol)}):")
        for tbl, col, note in viol:
            log(f"  {tbl}.{col}  {note}")
    else:
        log("\ncolumn contract: clean")

    log("\naudit.run_all:")
    for name, n in con.execute(
            "SELECT check_name, n_issues FROM audit.run_all").fetchall():
        flag = "ISSUE" if n else "ok   "
        log(f"  {flag} {name:<28} {n}")

    still_missing = con.execute(
        "SELECT count(*) FROM audit.contract_tables_missing").fetchone()[0]
    if still_missing:
        log(f"\ncontracted tables not yet materialized: {still_missing}")

    con.close()
    return 1 if (tally["failed"] or viol) else 0


if __name__ == "__main__":
    raise SystemExit(main())
