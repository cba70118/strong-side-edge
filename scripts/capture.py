"""capture.py - the scheduled odds capture. Run by Windows Task Scheduler.

WHY THIS IS THE URGENT PIECE. Line movement and pregame player props cannot be
reconstructed after the fact. No feed carries historical PREGAME props at all,
so the prop model - built, coverage-tested, per-stat bands and all - can only
ever be validated on snapshots we capture ourselves, going forward. Every day
without a capture is a day of that validation window gone permanently. Nothing
else on the roadmap has that property.

Two feeds, split by role (settled earlier, do not re-litigate):
  SGO           the placeable board - DK, FanDuel, BetMGM, Caesars. Marginal
                cost zero because the subscription already exists.
  The Odds API  Pinnacle only, via --pinnacle-only. 3 credits a snapshot,
                which fits the free tier at roughly 52 a month.

Cadence is deliberately uneven. Daily through the preseason is enough to build
an opening-to-now history; the hours before kickoff are where the closing line
actually forms, and CLV is graded against the close, so that window gets the
density. `--phase close` is the one that matters most and the one that cannot
be run late.

Usage (normally invoked by the scheduler, not by hand):
    py -3 scripts/capture.py --phase adhoc
    py -3 scripts/capture.py --phase close --props
    py -3 scripts/capture.py --install-task     # register with Task Scheduler
    py -3 scripts/capture.py --status
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "nfl.duckdb"
LOGF = ROOT / "data" / "capture.log"
PY = sys.executable

# name -> (schedule spec for schtasks, phase, extra args)
#
# The close capture is an HOURLY WATCHER, not a weekly schedule. 2026 has 118
# distinct kickoff windows across 57 gamedays; hardcoding them would mean
# re-registering tasks all season and silently missing any flexed game. The
# watcher runs every hour, asks the warehouse whether a kickoff is imminent,
# and exits without spending a credit when it is not.
TASKS = {
    "NFL_capture_daily": (
        ["/SC", "DAILY", "/ST", "09:00"], "adhoc", []),
    "NFL_capture_evening": (
        ["/SC", "DAILY", "/ST", "18:00"], "adhoc", []),
    "NFL_capture_close": (
        ["/SC", "HOURLY"], "close", ["--if-kickoff-within", "150"]),
}


def kickoff_imminent(minutes: int) -> tuple[bool, str]:
    """Is any game kicking off within `minutes`? Closing lines form here.

    Returns (should_capture, reason). Reads gameday/gametime as local wall
    clock, which is what raw.games stores.
    """
    con = duckdb.connect(str(DB), read_only=True)
    try:
        row = con.execute("""
            SELECT game_id, gameday, gametime,
                   date_diff('minute', now()::TIMESTAMP,
                             (gameday::VARCHAR || ' ' || gametime)::TIMESTAMP)
            FROM raw.games
            WHERE gameday IS NOT NULL AND gametime IS NOT NULL
              AND home_score IS NULL
            ORDER BY 4 LIMIT 1
        """).fetchone()
    finally:
        con.close()
    if not row:
        return False, "no unplayed games with a kickoff time"
    gid, day, tm, mins = row
    if mins is None:
        return False, "kickoff time unparseable"
    if 0 <= mins <= minutes:
        return True, f"{gid} kicks off in {mins} min"
    return False, f"next kickoff {gid} is {mins} min away"


SECRET_RE = re.compile(r"(apiKey|api_key|X-Api-Key|key)=([A-Za-z0-9_\-]{8,})",
                       re.IGNORECASE)


def redact(m: str) -> str:
    """Strip credentials before anything reaches disk.

    A ConnectionError from requests prints the full request URL, and that URL
    carries ?apiKey=... - so the live key was landing in data/capture.log in
    plaintext on every failed pull. That file was one `git add` away from a
    public repository.
    """
    return SECRET_RE.sub(lambda g: f"{g.group(1)}=***REDACTED***", m)


def log(m: str) -> None:
    m = redact(m)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp}  {m}"
    print(line, flush=True)
    try:
        with LOGF.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def run(script: str, args: list[str]) -> int:
    cmd = [PY, str(ROOT / "scripts" / script)] + args
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        log(f"  {script} TIMEOUT after 900s")
        return 1
    tail = [ln for ln in (p.stdout or "").splitlines() if ln.strip()][-3:]
    for ln in tail:
        log(f"  {script}: {ln.strip()}")
    if p.returncode:
        err = (p.stderr or "").strip().splitlines()[-2:]
        for ln in err:
            log(f"  {script} ERR: {ln}")
    return p.returncode


def cmd_capture(a) -> int:
    if a.if_kickoff_within:
        go, why = kickoff_imminent(a.if_kickoff_within)
        if not go:
            log(f"close watcher: no capture ({why})")
            return 0
        log(f"close watcher: FIRING - {why}")
    log(f"capture phase={a.phase} props={a.props}")
    rc = 0
    # Pinnacle anchor first - it is the CLV reference and the cheapest call.
    rc |= run("snapshot_odds.py", ["--phase", a.phase, "--pinnacle-only"])
    # Placeable board.
    sgo = ["--phase", a.phase]
    rc |= run("snapshot_sgo.py", sgo)
    if a.props:
        # Props are per-event and expensive; capped so a scheduled run cannot
        # drain the month's credits unattended.
        rc |= run("snapshot_odds.py",
                  ["--phase", a.phase, "--props", "--max-events",
                   str(a.max_events)])
    log(f"capture done rc={rc}")
    return rc


def cmd_status(a) -> int:
    con = duckdb.connect(str(DB), read_only=True)
    try:
        n = con.execute(
            "SELECT count(DISTINCT snapshot_id) FROM odds.line").fetchone()[0]
        rows = con.execute(
            "SELECT cast(captured_at AS VARCHAR), count(*) FROM odds.line "
            "GROUP BY 1 ORDER BY 1 DESC LIMIT 6").fetchall()
        props = con.execute("""
            SELECT count(*) FROM odds.line_pregame l
            JOIN ref.market m USING (market_id) WHERE m.is_player
        """).fetchone()[0]
    finally:
        con.close()
    log(f"snapshots {n}  player-prop rows {props}")
    for ts, c in rows:
        log(f"  {ts[:19]}  {c:>6} lines")
    if n < 5:
        log("  capture history is thin. CLV cannot be graded and the prop")
        log("  model cannot be validated until this accumulates.")
    return 0


def cmd_install(a) -> int:
    """Register with Windows Task Scheduler.

    A cloud cron cannot do this job: the warehouse is a local DuckDB file, so
    the capture has to run on this machine.
    """
    ok = 0
    for name, (sched, phase, extra) in TASKS.items():
        tr = (f'"{PY}" "{ROOT / "scripts" / "capture.py"}" --phase {phase}'
              + (" " + " ".join(extra) if extra else ""))
        cmd = ["schtasks", "/Create", "/TN", name, "/TR", tr, "/F"] + sched
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode == 0:
            log(f"installed {name}  {' '.join(sched)}")
            ok += 1
        else:
            log(f"FAILED {name}: {(p.stderr or p.stdout).strip()[:160]}")
    log(f"{ok}/{len(TASKS)} tasks registered")
    log("close capture is an hourly watcher: it checks the warehouse for an")
    log("imminent kickoff and spends nothing when there is not one.")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="adhoc",
                    choices=["opener", "post_wed", "post_fri", "close", "adhoc"])
    ap.add_argument("--props", action="store_true")
    ap.add_argument("--max-events", type=int, default=4)
    ap.add_argument("--if-kickoff-within", type=int, default=0,
                    help="only capture if a kickoff is this many minutes away")
    ap.add_argument("--install-task", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.install_task:
        return cmd_install(a)
    if a.status:
        return cmd_status(a)
    return cmd_capture(a)


if __name__ == "__main__":
    raise SystemExit(main())
