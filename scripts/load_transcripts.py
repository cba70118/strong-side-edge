"""Load Move The Line podcast/YouTube transcripts into ref.transcript.

WHY THIS EXISTS. Every model in this repo reads numbers. None of them can read
a roster battle, a coordinator's stated intent, or the reason a win total is
where it is. The transcripts are the only source in the project that carries
sharp-bettor REASONING rather than sharp-bettor PRICES, and reasoning is what
survives when the price moves.

Treat this as evidence, not as truth. These are other people's opinions,
captured at a moment in August 2026. They are stored with their publish date
and a timestamp on every segment so any claim can be traced back to when it was
made and what had happened by then.

The raw segment layer is kept because paraphrase loses the hedge. "We like it"
and "I would bet that today at plus money" are different bets, and only the
verbatim text preserves which one was said.

Usage:
    py -3 scripts/load_transcripts.py --json <apify_dump.json>
    py -3 scripts/load_transcripts.py --json <dump.json> --dump-md handoff/transcripts
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "nfl.duckdb"
SQL_FILE = ROOT / "sql" / "07_transcript.sql"

# Which division/topic each episode covers. Derived from the title, not hand
# keyed, so a new dump of the same series classifies itself.
TOPIC_PATTERNS = [
    (r"\b(AFC|NFC)\s+(East|West|North|South)\b", "division_preview"),
    (r"\bcoaching\s+changes?\b",                 "coaching"),
    (r"\binjur",                                 "injury"),
    (r"\bwin\s+total",                           "win_totals"),
]


def log(msg: str) -> None:
    print(msg, flush=True)


def classify(title: str) -> tuple[str, str | None]:
    """Return (topic, division) for an episode title."""
    division = None
    m = re.search(r"\b(AFC|NFC)\s+(East|West|North|South)\b", title, re.I)
    if m:
        division = f"{m.group(1).upper()} {m.group(2).title()}"
    for pattern, topic in TOPIC_PATTERNS:
        if re.search(pattern, title, re.I):
            return topic, division
    return "other", division


def clean(text: str) -> str:
    """Strip the auto-caption speaker markers and music cues."""
    text = text.replace(">>", " ").replace("[music]", " ").replace("[Music]", " ")
    return re.sub(r"\s+", " ", text).strip()


def load(con: duckdb.DuckDBPyConnection, payload: list[dict]) -> tuple[int, int]:
    con.execute("DELETE FROM ref.transcript_segment")
    con.execute("DELETE FROM ref.transcript")

    vids, segs = [], []
    for item in payload:
        vid = item.get("video_id")
        if not vid:
            continue
        title = item.get("title") or ""
        topic, division = classify(title)
        body = clean(item.get("transcript_text") or "")
        vids.append((
            vid,
            item.get("channel_name"),
            title,
            item.get("url"),
            (item.get("published_at") or "")[:10] or None,
            item.get("duration_seconds"),
            topic,
            division,
            item.get("description"),
            len(body.split()),
            hashlib.sha256(body.encode("utf-8")).hexdigest()[:16],
        ))
        for i, s in enumerate(item.get("transcript") or []):
            t = clean(s.get("text") or "")
            if not t:
                continue
            segs.append((vid, i, s.get("start"), s.get("end"), t))

    con.executemany(
        "INSERT INTO ref.transcript VALUES (?,?,?,?,?,?,?,?,?,?,?)", vids)
    con.executemany(
        "INSERT INTO ref.transcript_segment VALUES (?,?,?,?,?)", segs)
    return len(vids), len(segs)


def dump_md(payload: list[dict], outdir: Path) -> None:
    """Emit one readable markdown file per episode.

    Segments are re-flowed into ~40-second paragraphs with a running timestamp,
    because a wall of 2-second caption fragments is unreadable and a single
    unbroken block loses the ability to cite a moment.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    for item in payload:
        title = item.get("title") or item.get("video_id")
        topic, division = classify(title)
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]

        lines = [
            f"# {title}",
            "",
            f"- source: {item.get('channel_name')} — {item.get('url')}",
            f"- published: {(item.get('published_at') or '')[:10]}",
            f"- topic: {topic}" + (f" / {division}" if division else ""),
            "",
            "---",
            "",
        ]
        buf, bucket_start = [], None
        for s in item.get("transcript") or []:
            t = clean(s.get("text") or "")
            if not t:
                continue
            if bucket_start is None:
                bucket_start = s.get("start") or 0
            buf.append(t)
            if (s.get("end") or 0) - bucket_start >= 40:
                mm, ss = divmod(int(bucket_start), 60)
                lines.append(f"**[{mm:02d}:{ss:02d}]** " + " ".join(buf))
                lines.append("")
                buf, bucket_start = [], None
        if buf:
            mm, ss = divmod(int(bucket_start or 0), 60)
            lines.append(f"**[{mm:02d}:{ss:02d}]** " + " ".join(buf))

        (outdir / f"{slug}.md").write_text("\n".join(lines), encoding="utf-8")
    log(f"  wrote {len(payload)} markdown files to {outdir}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True, help="Apify youtube-transcript dump")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--dump-md", default=None, help="also write readable .md files")
    args = ap.parse_args()

    payload = json.loads(Path(args.json).read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = [payload]

    con = duckdb.connect(args.db)
    con.execute(SQL_FILE.read_text(encoding="utf-8"))
    n_vid, n_seg = load(con, payload)
    log(f"  loaded {n_vid} transcripts / {n_seg:,} segments")

    for row in con.execute(
        "SELECT topic, count(*), sum(word_count) FROM ref.transcript "
        "GROUP BY 1 ORDER BY 2 DESC").fetchall():
        log(f"    {row[0]:<18} {row[1]:>2} eps  {row[2]:>7,} words")
    con.close()

    if args.dump_md:
        dump_md(payload, Path(args.dump_md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
