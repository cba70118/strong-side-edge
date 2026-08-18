#!/usr/bin/env bash
# verify.sh — confirm this package is healthy after transfer.
# Exit 0 = every script runs and every embedded self-test passes.
#
#   ./verify.sh              # offline checks only
#   ./verify.sh --network    # also fetch live nflverse data

set -uo pipefail
cd "$(dirname "$0")"

PASS=0; FAIL=0
ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL+1)); }
run()  { if eval "$2" >/dev/null 2>&1; then ok "$1"; else bad "$1"; fi }

echo
echo "nfl-slate-analyst — package verification"
echo "----------------------------------------"

echo "Dependencies"
run "python3 available"            "command -v python3"
run "numpy importable"             "python3 -c 'import numpy'"
run "scipy importable"             "python3 -c 'import scipy'"
run "pandas importable"            "python3 -c 'import pandas'"

echo "Files present"
for f in README.md MANIFEST.json ROADMAP.md requirements.txt \
         skill/SKILL.md skill/references/market-math.md \
         skill/references/football-metrics.md skill/references/props-modeling.md \
         skill/references/data-sources.md skill/references/brief-format.md \
         skill/references/modeler-playbook.md \
         skill/scripts/market.py skill/scripts/props.py \
         skill/scripts/key_numbers.py skill/scripts/build_brief.py \
         schema/week.schema.json templates/week_template.json \
         examples/sample_brief.html \
         docs/modelers-guide.md docs/architecture-spec.md; do
  if [ -f "$f" ]; then ok "$f"; else bad "$f MISSING"; fi
done

echo "Scripts execute and self-test"
run "market.py self-test"          "python3 skill/scripts/market.py"
run "props.py self-test"           "python3 skill/scripts/props.py"
run "build_brief.py --sample"      "python3 skill/scripts/build_brief.py --sample -o /tmp/_v_sample.html"
run "build_brief.py from template" "python3 skill/scripts/build_brief.py templates/week_template.json -o /tmp/_v_tmpl.html"

echo "Behavioural guarantees"
run "rejects play missing kill condition" "python3 - <<'PY'
import json,sys
sys.path.insert(0,'skill/scripts')
import build_brief as b
d=json.loads(json.dumps(b.SAMPLE))
d['games'][0]['markets'][0].pop('kill')
try:
    b.build(d); sys.exit(1)          # should NOT succeed
except SystemExit as ex:
    sys.exit(0 if 'kill condition' in str(ex) else 1)
PY"
run "rejects pass missing reason" "python3 - <<'PY'
import json,sys
sys.path.insert(0,'skill/scripts')
import build_brief as b
d=json.loads(json.dumps(b.SAMPLE))
d['games'][0]['markets'][1].pop('reason')
try:
    b.build(d); sys.exit(1)
except SystemExit:
    sys.exit(0)
PY"
run "template validates against JSON Schema" "python3 - <<'PY'
import json,sys
try:
    import jsonschema
except ImportError:
    sys.exit(0)                       # optional dep; skip rather than fail
s=json.load(open('schema/week.schema.json'))
d=json.load(open('templates/week_template.json'))
jsonschema.validate(d,s)
PY"
run "sample brief renders both themes" "grep -q 'prefers-color-scheme' /tmp/_v_sample.html && grep -q 'data-theme' /tmp/_v_sample.html"
run "sample brief carries SAMPLE banner" "grep -q 'SAMPLE DATA' /tmp/_v_sample.html"

if [ "${1:-}" = "--network" ]; then
  echo "Network (live data sources)"
  run "nflverse pbp asset reachable" \
      "curl -sIL -o /dev/null -w '%{http_code}' https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_2025.parquet | grep -q 200"
  run "nflverse games.csv reachable" \
      "curl -sIL -o /dev/null -w '%{http_code}' https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv | grep -q 200"
  run "key_numbers.py end-to-end"    "python3 skill/scripts/key_numbers.py --since 2023 --json"
else
  echo "Network checks skipped (re-run with --network)"
fi

rm -f /tmp/_v_sample.html /tmp/_v_tmpl.html
echo "----------------------------------------"
echo "  $PASS passed, $FAIL failed"
echo
[ "$FAIL" -eq 0 ] || exit 1
echo "Package is healthy."
