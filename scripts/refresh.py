"""refresh.py - capture, rebuild, re-encrypt, publish. The whole loop.

THE GAP THIS CLOSES. capture.py has been running on a schedule since day one
and it updates the WAREHOUSE. The published page is a static artifact from
whenever someone last ran the builders by hand, so the site could sit days
behind the lines it claims to show. A board that is quietly stale is worse than
one labelled stale, because nothing on the page says so.

ORDER MATTERS and each step depends on the one before:

    capture      both feeds, unless --no-capture
    slate_data   prices the week from the newest snapshot
    dash_data    the live board reads the same payload
    season_log   APPEND the build, before results exist. Never edited later.
    reports      the merged page, which mounts the board and the team ledger
    publish      re-encrypt under the EXISTING passphrase
    git          commit and push only when docs/ actually changed

THE PASSPHRASE IS REUSED, never regenerated. publish_site.py mints a new one
when none is passed, which would silently lock the owner out of their own link
on every scheduled run. It is read from SITE_PASSWORD.txt, which is gitignored.

FAILURE IS NOT FATAL PER STEP. A network drop during capture should not stop
the rebuild from publishing what is already in the warehouse, so each step
reports its own status and the run continues where that is safe.

Usage:
    py -3 scripts/refresh.py                 # full loop
    py -3 scripts/refresh.py --no-capture    # rebuild and publish only
    py -3 scripts/refresh.py --no-push       # everything except git
    py -3 scripts/refresh.py --install-task  # register the schedule
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
LOGF = ROOT / "data" / "refresh.log"
SECRET = ROOT / "SITE_PASSWORD.txt"
PY = sys.executable

SECRET_RE = re.compile(r"(apiKey|api_key|X-Api-Key|key)=([A-Za-z0-9_\-]{8,})",
                       re.IGNORECASE)

TASK = "NFL_refresh_site"
# Twice a day, offset half an hour after the captures so the newest snapshot is
# already in the warehouse when the rebuild reads it.
SCHEDULE = ["/SC", "DAILY", "/ST", "09:30"]


def redact(m: str) -> str:
    return SECRET_RE.sub(lambda g: f"{g.group(1)}=***REDACTED***", m)


def log(m: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {redact(m)}"
    print(line, flush=True)
    try:
        with LOGF.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def run(cmd, label, cwd=ROOT, timeout=1800):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd),
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        log(f"  {label}: TIMEOUT")
        return 1, ""
    tail = [ln.strip() for ln in (p.stdout or "").splitlines() if ln.strip()]
    if tail:
        log(f"  {label}: {tail[-1]}")
    if p.returncode:
        for ln in (p.stderr or "").strip().splitlines()[-2:]:
            log(f"  {label} ERR: {ln}")
    return p.returncode, p.stdout or ""


def current_week(season: int) -> int:
    """Whatever week the schedule says is live. Never hardcoded, so this keeps
    working in November without an edit."""
    try:
        con = duckdb.connect(str(DB), read_only=True)
        try:
            row = con.execute(
                "SELECT week FROM mart.current_week WHERE season = ?",
                [season]).fetchone()
        finally:
            con.close()
        return int(row[0]) if row and row[0] is not None else 1
    except Exception as exc:
        log(f"  week lookup failed ({type(exc).__name__}); assuming 1")
        return 1


def passphrase() -> str | None:
    if not SECRET.exists():
        return None
    lines = [ln.strip() for ln in
             SECRET.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return lines[-1] if lines else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, default=0, help="0 = auto")
    ap.add_argument("--no-capture", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--install-task", action="store_true")
    a = ap.parse_args()

    if a.install_task:
        tr = f'"{PY}" "{ROOT / "scripts" / "refresh.py"}"'
        p = subprocess.run(["schtasks", "/Create", "/TN", TASK, "/TR", tr,
                            "/F"] + SCHEDULE, capture_output=True, text=True)
        log(f"install {TASK}: "
            f"{'ok' if p.returncode == 0 else (p.stderr or p.stdout)[:120]}")
        log("  runs 09:30 daily, half an hour after the 09:00 capture so the")
        log("  newest snapshot is already in the warehouse")
        return 0 if p.returncode == 0 else 1

    week = a.week or current_week(a.season)
    tag = f"week{week:02d}"
    slate, dash = f"{tag}_slate.json", f"{tag}_dash.json"
    log(f"refresh {a.season} week {week}")

    if not a.no_capture:
        run([PY, str(ROOT / "scripts" / "capture.py"), "--phase", "adhoc"],
            "capture")

    # Injury status and headlines. Cheap, unauthenticated, and it fails soft:
    # a network blip leaves the last capture in place rather than blanking the
    # News tab.
    run([PY, str(ROOT / "scripts" / "capture_news.py")], "news")

    # Pressure rate and the two metric screens. Cheap, and they must be rebuilt
    # in-season: the stability table is the thing that says whether a week-5
    # read is a team or a schedule, so it has to include the season underway.
    run([PY, str(ROOT / "scripts" / "build_pressure.py")], "pressure")
    run([PY, str(ROOT / "scripts" / "metric_stability.py")], "stability")
    run([PY, str(ROOT / "scripts" / "metric_edge_test.py")], "metric edge")

    rc, _ = run([PY, str(ROOT / "scripts" / "slate_data.py"),
                 "--season", str(a.season), "--week", str(week), "-o", slate],
                "slate_data")
    if rc:
        log("  slate_data failed; nothing downstream would be trustworthy")
        return 1
    run([PY, str(ROOT / "scripts" / "dash_data.py"), "--season", str(a.season),
         "--week", str(week), "-o", dash], "dash_data")
    run([PY, str(ROOT / "scripts" / "season_log.py"), "append", "-i", slate],
        "season_log")
    run([PY, str(ROOT / "scripts" / "dash_report.py"), "-i", dash,
         "-o", f"{tag}_dashboard.html"], "dash_report")
    rc, _ = run([PY, str(ROOT / "scripts" / "slate_report.py"), "-i", slate,
                 "--dash", dash, "-o", f"{tag}_brief.html"], "slate_report")
    if rc:
        log("  report build failed; leaving the published page as it was")
        return 1

    ph = passphrase()
    if not ph:
        log("  NO PASSPHRASE on file. Refusing to publish, because a fresh one "
            "would lock you out of your own link.")
        return 1
    # Show the capture time on the gate, so freshness is visible BEFORE the
    # reader unlocks rather than only in the masthead afterwards.
    cap = ""
    try:
        import json as _json
        cap = str((_json.loads(Path(slate).read_text(encoding="utf-8"))
                   .get("meta") or {}).get("captured") or "")[:16]
    except Exception:
        pass
    rc, _ = run([PY, str(ROOT / "scripts" / "publish_site.py"),
                 "-i", f"{tag}_brief.html", "-o", "docs/index.html",
                 "--passphrase", ph, "--captured", cap], "publish")
    if rc:
        return 1

    if a.no_push:
        log("  --no-push: docs/ rebuilt, not committed")
        return 0

    # Only commit when the encrypted payload actually changed. Every run makes
    # a new salt and nonce, so docs/index.html differs on every build even when
    # the underlying page is identical - which would otherwise produce a commit
    # a day that says nothing.
    rc, out = run(["git", "status", "--porcelain", "docs"], "git status")
    if not out.strip():
        log("  docs unchanged; nothing to push")
        return 0
    run(["git", "add", "docs"], "git add")
    msg = (f"Refresh {a.season} week {week} "
           f"({datetime.now():%Y-%m-%d %H:%M})")
    run(["git", "-c", "user.name=cba70118",
         "-c", "user.email=cba70118@users.noreply.github.com",
         "commit", "-q", "-m", msg], "git commit")
    rc, _ = run(["git", "push", "-q", "origin", "HEAD"], "git push")
    log("  published" if rc == 0 else "  push failed; commit is local")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
