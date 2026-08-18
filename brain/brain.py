#!/usr/bin/env python3
"""
brain.py - analytical second brain, stdlib only.

Stores judgment, not data. Raw data stays on disk and out of context; this
holds the things a script cannot recompute: beliefs, hypotheses, graded
outcomes.

Copy this file into each project. Stores are project-local and are never
shared between projects.

Commands
  init                      create brain/ store and default config
  open                      register a hypothesis BEFORE pulling data
  resolve <id>              confirm / refute / mark inconclusive
  assert                    write a prior
  recall                    read priors for an entity, contradictions first
  challenge <id>            record a challenge, lower confidence
  retire <id>               deactivate a prior
  log                       record a decision (bet, call, build choice)
  grade <id>                grade at close, not at result
  review                    what needs attention now
  status                    one screen: store health and write-back check

Every command prints a capped, verdict-first payload. Nothing here dumps a
table. If you want the raw rows, open the .jsonl.
"""

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRIORS = ROOT / "priors.jsonl"
HYPOS = ROOT / "hypotheses.jsonl"
LEDGER = ROOT / "ledger.jsonl"
CONFIG = ROOT / "config.json"
PLAYBOOK = ROOT / "playbook.md"

DEFAULT_CONFIG = {
    "project": "unnamed",
    "row_cap": 20,
    "confidence_floor": 0.55,
    "stale_days": 45,
    "half_life_days": {
        "form": 21,
        "personnel": 56,
        "fit": 240,
        "market": 240,
        "structural": 1000,
    },
    "sources": ["graded", "data", "judgment", "narrative"],
}

TODAY = dt.date.today()


# ---------- storage ----------

def load_config():
    if CONFIG.exists():
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
        return cfg
    return dict(DEFAULT_CONFIG)


def read(path):
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def append(path, obj):
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def rewrite(path, rows):
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def next_id(rows, prefix):
    n = 0
    for r in rows:
        try:
            n = max(n, int(str(r.get("id", "")).split("-")[-1]))
        except ValueError:
            pass
    return f"{prefix}-{n + 1:04d}"


def days_since(iso):
    try:
        return (TODAY - dt.date.fromisoformat(iso)).days
    except (TypeError, ValueError):
        return 0


# ---------- decay ----------

def decayed(prior, cfg):
    """Confidence decays toward 0.5 (no information), never toward zero."""
    hl = cfg["half_life_days"].get(prior.get("class", "structural"), 240)
    age = days_since(prior.get("touched") or prior.get("created"))
    factor = math.pow(0.5, age / hl) if hl else 1.0
    return round(0.5 + (float(prior.get("confidence", 0.5)) - 0.5) * factor, 3)


# ---------- printing ----------

def line(*parts):
    print(" ".join(str(p) for p in parts if p not in (None, "")))


def prior_line(p, cfg):
    d = decayed(p, cfg)
    mark = {1: "+", -1: "-", 0: "="}.get(p.get("stance", 0), "=")
    flags = []
    if p.get("source") == "narrative":
        flags.append("NARRATIVE, cannot be sole support")
    if d < cfg["confidence_floor"]:
        flags.append("BELOW FLOOR, review")
    if p.get("challenges"):
        flags.append(f"challenged x{p['challenges']}")
    tail = ("  [" + "; ".join(flags) + "]") if flags else ""
    return (f"{p['id']} {mark} {d:.2f} {p.get('class','?'):<11} "
            f"{p.get('entity','-'):<12} {p['claim']}{tail}")


# ---------- commands ----------

def cmd_init(args, cfg):
    ROOT.mkdir(parents=True, exist_ok=True)
    if not CONFIG.exists():
        cfg = dict(DEFAULT_CONFIG)
        cfg["project"] = args.project or "unnamed"
        CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    for p in (PRIORS, HYPOS, LEDGER):
        p.touch()
    if not PLAYBOOK.exists():
        PLAYBOOK.write_text(
            "# Playbook\n\nRules earned from graded outcomes only. "
            "Keep under 40 lines. Delete anything that has not paid.\n",
            encoding="utf-8")
    line(f"initialized {ROOT}  project={cfg['project']}")


def cmd_open(args, cfg):
    rows = read(HYPOS)
    h = {
        "id": next_id(rows, "h"),
        "question": args.q,
        "hypothesis": args.hyp,
        "falsifier": args.falsifier,
        "entity": args.entity,
        "opened": TODAY.isoformat(),
        "status": "open",
    }
    append(HYPOS, h)
    line(f"{h['id']} open")
    line(f"  falsifier: {h['falsifier']}")
    line("  pull data now. resolve before the session ends.")


def cmd_resolve(args, cfg):
    rows = read(HYPOS)
    hit = None
    for r in rows:
        if r["id"] == args.id:
            r["status"] = "resolved"
            r["verdict"] = args.verdict
            r["note"] = args.note
            r["resolved"] = TODAY.isoformat()
            hit = r
    if not hit:
        line(f"no hypothesis {args.id}")
        return 1
    rewrite(HYPOS, rows)
    line(f"{hit['id']} {args.verdict}")
    if args.verdict == "confirmed":
        line("  assert a prior from this, or it is forgotten.")
    if args.verdict == "refuted":
        line("  challenge or retire the prior that predicted it.")


def cmd_assert(args, cfg):
    rows = read(PRIORS)
    p = {
        "id": next_id(rows, "p"),
        "claim": args.claim,
        "entity": args.entity or "-",
        "class": getattr(args, "class"),
        "stance": args.stance,
        "confidence": args.conf,
        "source": args.source,
        "evidence": args.evidence,
        "created": TODAY.isoformat(),
        "touched": TODAY.isoformat(),
        "challenges": 0,
        "recalls": 0,
        "status": "active",
    }
    append(PRIORS, p)
    line(prior_line(p, cfg))
    if args.source == "narrative":
        line("  narrative source: may raise a question, may not answer one.")


def cmd_recall(args, cfg):
    rows = [p for p in read(PRIORS) if p.get("status") == "active"]
    if args.entity:
        rows = [p for p in rows
                if p.get("entity", "").lower() == args.entity.lower()]
    if getattr(args, "class", None):
        rows = [p for p in rows if p.get("class") == getattr(args, "class")]

    if not rows:
        line("no active priors match. this is a blank slate, say so out loud.")
        return

    # contradictions first: same entity + class, opposing stance
    groups = {}
    for p in rows:
        groups.setdefault((p.get("entity"), p.get("class")), []).append(p)
    conflicted, rest = [], []
    for _, g in groups.items():
        stances = {p.get("stance", 0) for p in g}
        (conflicted if (1 in stances and -1 in stances) else rest).extend(g)

    rest.sort(key=lambda p: decayed(p, cfg), reverse=True)
    cap = args.limit or cfg["row_cap"]

    if conflicted:
        line("CONTRADICTIONS, resolve before using either side")
        for p in conflicted[:cap]:
            line(" ", prior_line(p, cfg))
        line("")
    for p in rest[:cap]:
        line(prior_line(p, cfg))
    if len(rest) > cap:
        line(f"... {len(rest) - cap} more, narrow the question")

    # bump recall counters
    seen = {p["id"] for p in (conflicted[:cap] + rest[:cap])}
    allrows = read(PRIORS)
    for p in allrows:
        if p["id"] in seen:
            p["recalls"] = p.get("recalls", 0) + 1
    rewrite(PRIORS, allrows)


def cmd_challenge(args, cfg):
    rows = read(PRIORS)
    hit = None
    for p in rows:
        if p["id"] == args.id:
            p["challenges"] = p.get("challenges", 0) + 1
            p["confidence"] = round(
                max(0.5, 0.5 + (float(p["confidence"]) - 0.5) * 0.6), 3)
            p["touched"] = TODAY.isoformat()
            p.setdefault("notes", []).append(
                {"date": TODAY.isoformat(), "note": args.note})
            hit = p
    if not hit:
        line(f"no prior {args.id}")
        return 1
    rewrite(PRIORS, rows)
    line(prior_line(hit, cfg))


def cmd_retire(args, cfg):
    rows = read(PRIORS)
    hit = None
    for p in rows:
        if p["id"] == args.id:
            p["status"] = "retired"
            p["retired"] = TODAY.isoformat()
            p["retire_reason"] = args.reason
            hit = p
    if not hit:
        line(f"no prior {args.id}")
        return 1
    rewrite(PRIORS, rows)
    line(f"{hit['id']} retired: {args.reason}")


def cmd_log(args, cfg):
    rows = read(LEDGER)
    if not args.thesis or not args.kill:
        line("refused: a decision without a thesis and a kill condition is not a decision.")
        return 1
    e = {
        "id": next_id(rows, "b"),
        "entity": args.entity,
        "market": args.market,
        "pick": args.pick,
        "price": args.price,
        "fair": args.fair,
        "stake": args.stake,
        "thesis": args.thesis,
        "kill": args.kill,
        "priors": args.priors.split(",") if args.priors else [],
        "placed": TODAY.isoformat(),
        "status": "open",
    }
    append(LEDGER, e)
    line(f"{e['id']} logged  {e['pick']} @ {e['price']}  stake {e['stake']}")
    line(f"  kill: {e['kill']}")


def cmd_grade(args, cfg):
    rows = read(LEDGER)
    hit = None
    for e in rows:
        if e["id"] == args.id:
            e["close"] = args.close
            e["result"] = args.result
            e["clv"] = args.clv
            e["status"] = "graded"
            e["graded"] = TODAY.isoformat()
            hit = e
    if not hit:
        line(f"no entry {args.id}")
        return 1
    rewrite(LEDGER, rows)
    beat = "unknown"
    if args.clv is not None:
        beat = "beat the close" if args.clv > 0 else "lost to the close"
    line(f"{hit['id']} graded  result={args.result}  CLV={args.clv}  {beat}")
    if hit.get("priors"):
        line(f"  update the priors that carried it: {', '.join(hit['priors'])}")
    else:
        line("  no priors attached. that decision taught the brain nothing.")


def cmd_review(args, cfg):
    priors = read(PRIORS)
    hypos = read(HYPOS)
    ledger = read(LEDGER)
    cap = cfg["row_cap"]

    stale = [p for p in priors if p.get("status") == "active"
             and decayed(p, cfg) < cfg["confidence_floor"]]
    unused = [p for p in priors if p.get("status") == "active"
              and p.get("recalls", 0) == 0
              and days_since(p.get("created")) > cfg["stale_days"]]
    open_h = [h for h in hypos if h.get("status") == "open"]
    ungraded = [e for e in ledger if e.get("status") == "open"]

    line(f"REVIEW  {cfg['project']}  {TODAY.isoformat()}")
    line("")
    line(f"open hypotheses ..... {len(open_h)}")
    for h in open_h[:cap]:
        line(f"  {h['id']}  {h['question']}  (opened {h['opened']})")
    line(f"ungraded decisions .. {len(ungraded)}")
    for e in ungraded[:cap]:
        line(f"  {e['id']}  {e['pick']} @ {e['price']}  kill: {e['kill']}")
    line(f"priors below floor .. {len(stale)}")
    for p in stale[:cap]:
        line(f"  {p['id']}  {decayed(p, cfg):.2f}  {p['claim']}")
    line(f"never recalled ...... {len(unused)}  (retire or use)")
    for p in unused[:cap]:
        line(f"  {p['id']}  {p['claim']}")


def cmd_status(args, cfg):
    priors = read(PRIORS)
    hypos = read(HYPOS)
    ledger = read(LEDGER)
    active = [p for p in priors if p.get("status") == "active"]
    graded = [e for e in ledger if e.get("status") == "graded"]
    clvs = [e["clv"] for e in graded if isinstance(e.get("clv"), (int, float))]
    by_source = {}
    for p in active:
        by_source[p.get("source", "?")] = by_source.get(p.get("source", "?"), 0) + 1

    line(f"{cfg['project']}  {TODAY.isoformat()}")
    line(f"priors active {len(active)}  by source " +
         ", ".join(f"{k} {v}" for k, v in sorted(by_source.items())))
    line(f"hypotheses open {sum(1 for h in hypos if h.get('status') == 'open')}"
         f"  resolved {sum(1 for h in hypos if h.get('status') == 'resolved')}")
    line(f"decisions open {sum(1 for e in ledger if e.get('status') == 'open')}"
         f"  graded {len(graded)}")
    if clvs:
        beat = sum(1 for c in clvs if c > 0)
        line(f"mean CLV {sum(clvs)/len(clvs):+.2f}  beat the close "
             f"{beat}/{len(clvs)} ({100*beat/len(clvs):.0f}%)")
    touched = [p for p in priors if p.get("touched") == TODAY.isoformat()]
    wrote = touched or [h for h in hypos if h.get("resolved") == TODAY.isoformat()] \
        or [e for e in ledger if e.get("graded") == TODAY.isoformat()]
    line("")
    line("write-back: " + ("satisfied" if wrote else
         "NOTHING WRITTEN TODAY. that was a lookup, not analysis."))


# ---------- cli ----------

def build_parser():
    p = argparse.ArgumentParser(description="analytical second brain")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init"); s.add_argument("--project"); s.set_defaults(fn=cmd_init)

    s = sub.add_parser("open")
    s.add_argument("--q", required=True, help="the question, one sentence")
    s.add_argument("--hyp", required=True, help="what you expect, before data")
    s.add_argument("--falsifier", required=True, help="what would make it wrong")
    s.add_argument("--entity")
    s.set_defaults(fn=cmd_open)

    s = sub.add_parser("resolve")
    s.add_argument("id")
    s.add_argument("--verdict", required=True,
                   choices=["confirmed", "refuted", "inconclusive"])
    s.add_argument("--note", default="")
    s.set_defaults(fn=cmd_resolve)

    s = sub.add_parser("assert")
    s.add_argument("--claim", required=True)
    s.add_argument("--entity")
    s.add_argument("--class", dest="class", required=True)
    s.add_argument("--conf", type=float, required=True)
    s.add_argument("--source", required=True,
                   choices=["graded", "data", "judgment", "narrative"])
    s.add_argument("--evidence", default="")
    s.add_argument("--stance", type=int, default=0, choices=[-1, 0, 1])
    s.set_defaults(fn=cmd_assert)

    s = sub.add_parser("recall")
    s.add_argument("--entity")
    s.add_argument("--class", dest="class")
    s.add_argument("--limit", type=int)
    s.set_defaults(fn=cmd_recall)

    s = sub.add_parser("challenge")
    s.add_argument("id"); s.add_argument("--note", default="")
    s.set_defaults(fn=cmd_challenge)

    s = sub.add_parser("retire")
    s.add_argument("id"); s.add_argument("--reason", default="")
    s.set_defaults(fn=cmd_retire)

    s = sub.add_parser("log")
    for f in ("entity", "market", "pick", "price", "fair", "stake",
              "thesis", "kill", "priors"):
        s.add_argument(f"--{f}", default="")
    s.set_defaults(fn=cmd_log)

    s = sub.add_parser("grade")
    s.add_argument("id")
    s.add_argument("--close", default="")
    s.add_argument("--result", default="", choices=["win", "loss", "push", ""])
    s.add_argument("--clv", type=float, default=None)
    s.set_defaults(fn=cmd_grade)

    s = sub.add_parser("review"); s.set_defaults(fn=cmd_review)
    s = sub.add_parser("status"); s.set_defaults(fn=cmd_status)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    cfg = load_config()
    return args.fn(args, cfg) or 0


if __name__ == "__main__":
    sys.exit(main())
