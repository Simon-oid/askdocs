#!/usr/bin/env bash
# A four-scene terminal demo against a running askdocs (docker compose up,
# or the quick start). Made to be recorded: paced, narrated, no setup on screen.
#
#   scripts/demo.sh                 # http://127.0.0.1:8000
#   HOST=http://host:8000 PAUSE=0 scripts/demo.sh    # no pauses (for tests)
set -euo pipefail
HOST=${HOST:-http://127.0.0.1:8000}
PAUSE=${PAUSE:-2}
HERE=$(cd "$(dirname "$0")" && pwd)
bold=$(tput bold 2>/dev/null || true); dim=$(tput dim 2>/dev/null || true); off=$(tput sgr0 2>/dev/null || true)

say() { printf '\n%s# %s%s\n' "$bold" "$1" "$off"; sleep "$PAUSE"; }

ask() {   # streams one question: sources, tokens as they arrive, the outcome
    local body
    body=$(python3 -c 'import json,sys; print(json.dumps({"question": sys.argv[1]}))' "$1")
    printf "%s\$ curl -N %s/ask -d '%s'%s\n" "$dim" "$HOST" "$body" "$off"
    curl -sN "$HOST/ask" -H 'content-type: application/json' -d "$body" |
        python3 -u "$HERE/demo_render.py" stream
    sleep "$PAUSE"
}

curl -sf "$HOST/healthz" >/dev/null || { echo "askdocs is not answering at $HOST" >&2; exit 1; }

say "1. A question the documents answer: sources first, then a cited answer, streamed"
ask "What BM25 parameters does cRAG use?"

say "2. A question that needs two documents"
ask "Which embedding model should be used for Italian text, and what cosine cutoff does crag-mcp apply for it?"

say "3. A question the documents cannot answer: refused before the model is called"
ask "What is the best pizza topping?"

say "4. What the service measured meanwhile: P50/P95 per stage, tokens per answer"
curl -s "$HOST/stats" | python3 "$HERE/demo_render.py" stats
