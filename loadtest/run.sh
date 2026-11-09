#!/usr/bin/env bash
# Step load: the same closed-loop test at increasing concurrency, against a
# running askdocs, then one summary table.
#
#   loadtest/run.sh                    # 1 2 4 8 16 users, 60 s each -> loadtest/out/
#   STEPS="4 8" DURATION=30s OUT=/tmp/lt loadtest/run.sh
set -euo pipefail
HOST=${HOST:-http://127.0.0.1:8000}
STEPS=${STEPS:-1 2 4 8 16}
DURATION=${DURATION:-60s}
OUT=${OUT:-$(dirname "$0")/out}
LOCUST=${LOCUST:-locust}
mkdir -p "$OUT"
curl -sf "$HOST/healthz" >/dev/null || { echo "askdocs is not answering at $HOST" >&2; exit 1; }
for u in $STEPS; do
    echo "== $u users, $DURATION" >&2
    "$LOCUST" -f "$(dirname "$0")/locustfile.py" --host "$HOST" --headless --only-summary \
        --users "$u" --spawn-rate "$u" --run-time "$DURATION" --csv "$OUT/u$u" \
        --exit-code-on-error 0 >/dev/null 2>"$OUT/u$u.log" || true
    curl -s "$HOST/stats" > "$OUT/u$u.server-stats.json"
done
python3 "$(dirname "$0")/summarize.py" "$OUT"
